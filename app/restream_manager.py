#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify, redirect, url_for
import os
import json
import re
import time
import psutil
import secrets
from functools import wraps

try:
    from .settings import SETTINGS
    from .monitoring import health_snapshot, metrics_snapshot
    from .config_store import ConfigStore, ConfigValidationError
    from .supervisor_client import SupervisorClient, SupervisorUnavailable
except ImportError:
    from settings import SETTINGS
    from monitoring import health_snapshot, metrics_snapshot
    from config_store import ConfigStore, ConfigValidationError
    from supervisor_client import SupervisorClient, SupervisorUnavailable

app = Flask(
    __name__,
    template_folder=str(SETTINGS.template_dir),
)

# ===== КОНФИГУРАЦИЯ =====
CONFIG_FILE = SETTINGS.config_file
CONFIG_STORE = ConfigStore(CONFIG_FILE)
SUPERVISOR_CLIENT = SupervisorClient(SETTINGS.supervisor_socket)

RTMP_URL_PATTERN = re.compile(
    r"rtmps?://\S+",
    re.IGNORECASE,
)


@app.route('/api/node/health')
def api_node_health():
    """Safe component readiness without secrets."""
    return jsonify(health_snapshot(SETTINGS))


@app.route('/api/node/metrics')
def api_node_metrics():
    """Safe node metrics without URLs, keys, client addresses or logs."""
    try:
        return jsonify(metrics_snapshot(SETTINGS))
    except (OSError, ValueError, json.JSONDecodeError):
        return jsonify({
            'status': 'unavailable',
            'message': 'Node metrics are temporarily unavailable',
        }), 503


@app.errorhandler(ConfigValidationError)
def handle_invalid_stored_config(error):
    """Do not expose config contents when the stored file is invalid."""
    return jsonify({
        'success': False,
        'message': 'Node configuration is invalid',
    }), 503


def redact_rtmp_urls(value):
    """Удаляет RTMP URL и ключи из диагностики."""
    return RTMP_URL_PATTERN.sub(
        "[RTMP URL REDACTED]",
        str(value),
    )



def load_config():
    """Load and validate the complete node configuration."""
    return CONFIG_STORE.load()

def save_config(config):
    """Validate and atomically replace the node configuration."""
    return CONFIG_STORE.save(config)


def serialized_config_write(function):
    """Prevent concurrent admin requests from losing configuration updates."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with CONFIG_STORE.locked():
            return function(*args, **kwargs)
    return wrapped

def get_process_status(pid_file, include_resources=True):
    """Check an FFmpeg PID file, optionally collecting slower resource data."""
    if not os.path.exists(pid_file):
        return {'status': 'stopped', 'pid': None, 'uptime': 0}
    
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())
        
        process = psutil.Process(pid)
        if (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
            and "ffmpeg" in process.name().lower()
        ):
            result = {
                'status': 'running',
                'pid': pid,
                'uptime': int(time.time() - process.create_time()),
            }
            if include_resources:
                memory_info = process.memory_info()
                result.update({
                    'cpu': process.cpu_percent(interval=0.1),
                    'memory': round(
                        memory_info.rss / 1024 / 1024,
                        2,
                    ),
                })
            return result
    except (OSError, psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        # Runtime files belong to the dedicated supervisor. The manager is
        # deliberately read-only here and must tolerate stale PID files.
        pass
    
    return {'status': 'stopped', 'pid': None, 'uptime': 0}

def read_log_tail(log_file, line_count=100, max_bytes=131072):
    """Read only a bounded tail instead of loading an unbounded FFmpeg log."""
    path = os.fspath(log_file)
    with open(path, 'rb') as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(max(0, size - max_bytes))
        payload = source.read(max_bytes)

    text = payload.decode('utf-8', errors='replace')
    lines = text.splitlines()
    # A bounded read may start in the middle of a line.
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines[-line_count:]


def get_delay_info(log_file):
    """Анализирует логи на предмет задержки"""
    if not os.path.exists(log_file):
        return {'delay': 0, 'drops': 0, 'errors': []}
    
    try:
        lines = read_log_tail(log_file, line_count=100)
        
        drops = 0
        errors = []
        
        for line in lines:
            if 'drop' in line.lower():
                drops += 1
            if 'error' in line.lower() or 'failed' in line.lower():
                errors.append(
                    redact_rtmp_urls(
                        line.strip()
                    )
                )
        
        return {
            'drops': drops,
            'errors': errors[-5:]
        }
    except Exception:
        return {'drops': 0, 'errors': []}

def get_fields():
    """Получает список полей для рестрима из конфига"""
    config = load_config()
    fields = {}
    for field_id, field_data in config.get('fields', {}).items():
        urls = field_data.get('restream_urls', [])
        
        # Получаем stream key (по умолчанию stream{id})
        stream_key = field_data.get('stream_key', f'stream{field_id}')
        
        fields[int(field_id)] = {
            'name': field_data.get('name', f'Field {field_id}'),
            'source': f'{SETTINGS.local_rtmp_origin}/place{field_id}/{stream_key}',
            'urls': urls,
            'pid_files': [str(SETTINGS.pid_file(field_id, i)) for i in range(len(urls))],
            'log_files': [str(SETTINGS.log_file(field_id, i)) for i in range(len(urls))]
        }
    return fields

def supervisor_action(action, field_id, url_index=None):
    """Send a process-management request without exposing configured URLs."""
    try:
        result = SUPERVISOR_CLIENT.request(action, field_id, url_index)
        return result['success'], result.get('message', 'Supervisor request processed')
    except (SupervisorUnavailable, ValueError) as error:
        return False, str(error)


def start_restream(field_id, url_index=None):
    return supervisor_action('start', field_id, url_index)


def stop_restream(field_id, url_index=None):
    return supervisor_action('stop', field_id, url_index)


# ===== СТРАНИЦЫ =====

@app.route('/')
def index():
    """Главная страница с панелью управления"""
    fields = get_fields()
    fields_status = {}
    
    for field_id, field in fields.items():
        # Статусы для каждого URL
        url_statuses = []
        for idx, url in enumerate(field['urls']):
            pid_file = SETTINGS.pid_file(field_id, idx)
            log_file = SETTINGS.log_file(field_id, idx)
            status = get_process_status(pid_file)
            delay_info = get_delay_info(log_file)
            url_statuses.append({
                'url': url,
                'index': idx,
                'status': status,
                'delay_info': delay_info
            })
        
        # Общий статус поля
        running_count = sum(1 for s in url_statuses if s['status']['status'] == 'running')
        
        fields_status[field_id] = {
            'name': field['name'],
            'urls': url_statuses,
            'running_count': running_count,
            'total_count': len(url_statuses)
        }
    
    return render_template('index.html', fields=fields_status)


@app.route('/api/status')
def api_restream_status():
    """Return a secret-free status snapshot for dynamic admin updates."""
    fields_status = {}

    for field_id, field in get_fields().items():
        destinations = []
        running_count = 0

        for url_index, _url in enumerate(field['urls']):
            process = get_process_status(
                SETTINGS.pid_file(field_id, url_index),
                include_resources=False,
            )
            if process['status'] == 'running':
                running_count += 1

            destinations.append({
                'index': url_index,
                'status': process['status'],
                'uptime': process.get('uptime', 0),
                'cpu': process.get('cpu'),
                'memory': process.get('memory'),
            })

        fields_status[str(field_id)] = {
            'running_count': running_count,
            'total_count': len(destinations),
            'destinations': destinations,
        }

    return jsonify({
        'success': True,
        'fields': fields_status,
    })


# ===== RESTREAM API =====

@app.route('/api/start/<int:field_id>', methods=['POST'])
@serialized_config_write
def api_start_all(field_id):
    """API: запустить рестрим для ВСЕХ URL поля"""
    success, message = start_restream(field_id, url_index=None)
    return jsonify({'success': success, 'message': message})


@app.route('/api/start/<int:field_id>/<int:url_index>', methods=['POST'])
@serialized_config_write
def api_start_specific(field_id, url_index):
    """API: запустить рестрим для конкретного URL"""
    success, message = start_restream(field_id, url_index)
    return jsonify({'success': success, 'message': message})


@app.route('/api/stop/<int:field_id>', methods=['POST'])
@serialized_config_write
def api_stop_all(field_id):
    """API: остановить рестрим для ВСЕХ URL поля"""
    success, message = stop_restream(field_id, url_index=None)
    return jsonify({'success': success, 'message': message})


@app.route('/api/stop/<int:field_id>/<int:url_index>', methods=['POST'])
@serialized_config_write
def api_stop_specific(field_id, url_index):
    """API: остановить рестрим для конкретного URL"""
    success, message = stop_restream(field_id, url_index)
    return jsonify({'success': success, 'message': message})


@app.route('/api/restart/<int:field_id>', methods=['POST'])
@serialized_config_write
def api_restart_all(field_id):
    """API: перезапустить рестрим для ВСЕХ URL поля"""
    success, message = supervisor_action('restart', field_id, url_index=None)
    return jsonify({'success': success, 'message': message})


@app.route('/api/restart/<int:field_id>/<int:url_index>', methods=['POST'])
@serialized_config_write
def api_restart_specific(field_id, url_index):
    """API: перезапустить рестрим для конкретного URL"""
    success, message = supervisor_action('restart', field_id, url_index)
    return jsonify({'success': success, 'message': message})


@app.route('/api/logs/<int:field_id>/<int:url_index>')
def api_logs_specific(field_id, url_index):
    """API: получить логи для конкретного URL"""
    log_file = SETTINGS.log_file(field_id, url_index)
    if not os.path.exists(log_file):
        return jsonify({'logs': []})
    
    try:
        lines = [
            redact_rtmp_urls(line)
            for line in read_log_tail(
                log_file,
                line_count=50,
            )
        ]
        return jsonify({'logs': lines})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== RESTREAM URLS API =====

@app.route('/api/restream-urls/<int:field_id>', methods=['GET'])
@serialized_config_write
def api_get_restream_urls(field_id):
    """API: получить список URL для рестрима"""
    config = load_config()
    
    if str(field_id) not in config.get('fields', {}):
        return jsonify({'success': False, 'message': 'Field not found'}), 404
    
    field = config['fields'][str(field_id)]
    
    urls = field.get('restream_urls', [])
    
    return jsonify({'success': True, 'urls': urls})


@app.route('/api/restream-urls/<int:field_id>', methods=['POST'])
@serialized_config_write
def api_add_restream_url(field_id):
    """API: добавить новый URL для рестрима"""
    try:
        data = request.get_json()
        new_url = data.get('url', '').strip()
        
        if not new_url:
            return jsonify({'success': False, 'message': 'URL is empty'}), 400
        
        config = load_config()
        
        if str(field_id) not in config.get('fields', {}):
            return jsonify({'success': False, 'message': 'Field not found'}), 404
        
        field = config['fields'][str(field_id)]
        
        field.setdefault('restream_urls', [])
        field['restream_urls'].append(new_url)
        save_config(config)
        
        return jsonify({
            'success': True,
            'message': 'URL added',
            'index': len(field['restream_urls']) - 1
        })
    except ConfigValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/restream-urls/<int:field_id>/<int:url_index>', methods=['PUT'])
@serialized_config_write
def api_update_restream_url(field_id, url_index):
    """API: обновить URL для рестрима"""
    try:
        data = request.get_json()
        new_url = data.get('url', '').strip()
        
        config = load_config()
        
        if str(field_id) not in config.get('fields', {}):
            return jsonify({'success': False, 'message': 'Field not found'}), 404
        
        field = config['fields'][str(field_id)]
        
        field.setdefault('restream_urls', [])
        if url_index >= len(field['restream_urls']):
            return jsonify({'success': False, 'message': 'Invalid URL index'}), 400
        
        success, message = stop_restream(field_id, url_index)
        if not success:
            return jsonify({'success': False, 'message': message}), 503
        
        field['restream_urls'][url_index] = new_url
        save_config(config)
        
        return jsonify({'success': True, 'message': 'URL updated'})
    except ConfigValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/restream-urls/<int:field_id>/<int:url_index>', methods=['DELETE'])
@serialized_config_write
def api_delete_restream_url(field_id, url_index):
    """API: удалить URL для рестрима"""
    try:
        config = load_config()
        
        if str(field_id) not in config.get('fields', {}):
            return jsonify({'success': False, 'message': 'Field not found'}), 404
        
        field = config['fields'][str(field_id)]
        
        field.setdefault('restream_urls', [])
        if url_index >= len(field['restream_urls']):
            return jsonify({'success': False, 'message': 'Invalid URL index'}), 400
        
        success, message = supervisor_action(
            'delete_destination', field_id, url_index
        )
        if not success:
            return jsonify({'success': False, 'message': message}), 503
        
        # Удаляем URL
        field['restream_urls'].pop(url_index)
        
        save_config(config)
        
        return jsonify({'success': True, 'message': 'URL deleted'})
    except ConfigValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ===== КОНФИГУРАЦИЯ ПОЛЕЙ =====

@app.route('/config/')
def config_page():
    """Страница конфигурации полей"""
    config = load_config()
    return render_template('config.html', fields=config.get('fields', {}))


@app.route('/api/config/fields')
def api_config_fields():
    """API: безопасный список включённых площадок для страницы мониторинга."""
    config = load_config()

    # Публичное представление строится только по белому списку.
    # Сюда нельзя добавлять key, restream_url, restream_urls
    # и другие конфигурационные или секретные значения.
    enabled_fields = {}

    for field_id, field in config.get('fields', {}).items():
        if not field.get('enabled', False):
            continue

        stream_key = field.get('stream_key') or f'stream{field_id}'

        enabled_fields[field_id] = {
            'name': field.get('name') or f'Площадка {field_id}',
            'emoji': field.get('emoji') or '🏟️',
            'rtmp_url': (
                f'rtmp://{SETTINGS.public_host}/'
                f'place{field_id}/{stream_key}'
            ),
            'hls_url': f'/hls/place{field_id}/{stream_key}.m3u8',
        }

    return jsonify(enabled_fields)


@app.route('/api/config/fields/status')
def api_config_fields_status():
    """API: проверить активность всех 16 площадок"""
    import glob
    
    config = load_config()
    status = {}
    now = time.time()
    
    for i in range(1, 17):
        # Получаем stream key для этой площадки
        field_data = config.get('fields', {}).get(str(i), {})
        stream_key = field_data.get('stream_key', f'stream{i}')
        
        place_dir = SETTINGS.hls_root / f"place{i}"
        m3u8_file = f"{place_dir}/{stream_key}.m3u8"
        
        if not os.path.exists(m3u8_file):
            status[str(i)] = 'no_signal'
            continue
        
        ts_files = glob.glob(f"{place_dir}/{stream_key}-*.ts")
        if not ts_files:
            status[str(i)] = 'no_signal'
            continue
        
        latest_ts = max(ts_files, key=os.path.getmtime)
        age = now - os.path.getmtime(latest_ts)
        
        if age < 30:
            status[str(i)] = 'active'
        elif age < 120:
            status[str(i)] = 'stale'
        else:
            status[str(i)] = 'no_signal'
    
    return jsonify(status)


@app.route('/api/config/fields/all')
def api_config_fields_all():
    """API: получить все поля (для страницы конфигурации)"""
    config = load_config()
    
    # Добавляем stream_key и формируем URL динамически
    all_fields = {}
    for k, v in config.get('fields', {}).items():
        field_copy = v.copy()
        stream_key = v.get('stream_key', f'stream{k}')
        field_copy['rtmp_url'] = f"rtmp://{SETTINGS.public_host}/place{k}/{stream_key}"
        field_copy['hls_url'] = f"/hls/place{k}/{stream_key}.m3u8"
        field_copy['stream_key'] = stream_key
        all_fields[k] = field_copy
    
    return jsonify(all_fields)


@app.route('/api/config/fields', methods=['POST'])
@serialized_config_write
def api_config_create_field():
    """API: создать новое поле (использует слоты 1-16)"""
    try:
        data = request.get_json()
        config = load_config()
        
        existing_ids = [int(k) for k in config.get('fields', {}).keys()]
        free_slot = None
        for i in range(1, 17):
            if i not in existing_ids:
                free_slot = i
                break
        
        if free_slot is None:
            return jsonify({'success': False, 'message': 'All 16 slots are used!'}), 400
        
        new_id = str(free_slot)
        random_key = secrets.token_urlsafe(8)
        stream_key = data.get('stream_key', f'stream{new_id}').strip()
        
        if not stream_key:
            stream_key = f'stream{new_id}'
        
        config['fields'][new_id] = {
            'name': data.get('name', f'Field {new_id}'),
            'emoji': data.get('emoji', '🏟️'),
            'stream_key': stream_key,
            'enabled': data.get('enabled', True),
            'key': random_key
        }
        
        save_config(config)
        return jsonify({'success': True, 'id': new_id, 'message': f'Field created in slot {new_id}'})
    except ConfigValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/config/fields/<field_id>', methods=['PUT'])
@serialized_config_write
def api_config_update_field(field_id):
    """API: обновить поле"""
    try:
        data = request.get_json()
        config = load_config()
        
        if field_id not in config.get('fields', {}):
            return jsonify({'success': False, 'message': 'Field not found'}), 404
        
        field = config['fields'][field_id]
        
        if 'name' in data:
            field['name'] = data['name']
        if 'emoji' in data:
            field['emoji'] = data['emoji']
        if 'enabled' in data:
            field['enabled'] = data['enabled']
        if 'key' in data:
            field['key'] = data['key']
        
        # Обновляем stream_key если передан
        if 'stream_key' in data:
            new_stream_key = data['stream_key'].strip()
            if new_stream_key:
                old_stream_key = field.get('stream_key', f'stream{field_id}')
                
                # Если stream_key изменился, обновляем URL
                if new_stream_key != old_stream_key:
                    field['stream_key'] = new_stream_key
        
        save_config(config)
        return jsonify({'success': True, 'message': 'Field updated'})
    except ConfigValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/config/fields/<field_id>', methods=['DELETE'])
@serialized_config_write
def api_config_delete_field(field_id):
    """API: удалить поле"""
    try:
        config = load_config()
        
        if field_id not in config.get('fields', {}):
            return jsonify({'success': False, 'message': 'Field not found'}), 404
        
        success, message = stop_restream(int(field_id), url_index=None)
        if not success:
            return jsonify({'success': False, 'message': message}), 503
        del config['fields'][field_id]
        save_config(config)
        return jsonify({'success': True, 'message': 'Field deleted'})
    except ConfigValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
