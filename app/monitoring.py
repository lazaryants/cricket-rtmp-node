"""Safe health and metrics snapshots without configuration secrets."""

import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import psutil

try:
    from .version import __version__
except ImportError:
    from version import __version__

try:
    from .config_store import load_and_validate_config
except ImportError:
    from config_store import load_and_validate_config


def timestamp_utc():
    return datetime.now(timezone.utc).isoformat()


def read_config(config_file):
    return load_and_validate_config(config_file)


def hls_snapshot(settings, config, now=None):
    now = time.time() if now is None else now
    places = {}
    counts = {"active": 0, "stale": 0, "no_signal": 0}

    for place_id in range(1, 17):
        field = config.get("fields", {}).get(str(place_id), {})
        stream_key = field.get("stream_key") or f"stream{place_id}"
        directory = settings.hls_root / f"place{place_id}"
        segments = list(directory.glob(f"{stream_key}-*.ts"))

        if not segments:
            state = "no_signal"
            age = None
        else:
            latest = max(segments, key=lambda path: path.stat().st_mtime)
            age = max(0.0, now - latest.stat().st_mtime)
            if age < 30:
                state = "active"
            elif age < 120:
                state = "stale"
            else:
                state = "no_signal"

        counts[state] += 1
        places[str(place_id)] = {
            "state": state,
            "latest_segment_age_seconds": round(age, 1) if age is not None else None,
        }

    return {"counts": counts, "places": places}


def restream_snapshot(settings, config):
    configured = 0
    running = 0

    for field_id, field in config.get("fields", {}).items():
        destinations = field.get("restream_urls", [])
        if not isinstance(destinations, list):
            destinations = []
        configured += len(destinations)

        for url_index in range(len(destinations)):
            pid_file = settings.pid_file(field_id, url_index)
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                process = psutil.Process(pid)
                if process.is_running() and "ffmpeg" in process.name().lower():
                    running += 1
            except (
                FileNotFoundError,
                ValueError,
                psutil.NoSuchProcess,
                psutil.AccessDenied,
            ):
                continue

    return {
        "configured_destinations": configured,
        "running_processes": running,
    }


def _number(element, path, cast=int):
    """Read an optional numeric XML value without failing the snapshot."""
    value = element.findtext(path)
    if value in (None, ""):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _text(element, path):
    value = element.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _stream_metrics(stream):
    clients = stream.findall("client")
    publishers = [
        client
        for client in clients
        if client.find("publishing") is not None
    ]
    players = len(clients) - len(publishers)
    publisher_dropped = sum(
        _number(client, "dropped") or 0
        for client in publishers
    )

    time_ms = _number(stream, "time")
    width = _number(stream, "meta/video/width")
    height = _number(stream, "meta/video/height")

    return {
        "uptime_seconds": (
            round(time_ms / 1000, 1)
            if time_ms is not None
            else None
        ),
        "input_bitrate_bps": _number(stream, "bw_in"),
        "video_bitrate_bps": _number(stream, "bw_video"),
        "audio_bitrate_bps": _number(stream, "bw_audio"),
        "bytes_received": _number(stream, "bytes_in"),
        "publishers": len(publishers),
        "players": players,
        "publisher_dropped": publisher_dropped,
        "video": {
            "codec": _text(stream, "meta/video/codec"),
            "profile": _text(stream, "meta/video/profile"),
            "level": _text(stream, "meta/video/level"),
            "width": width,
            "height": height,
            "resolution": (
                f"{width}x{height}"
                if width is not None and height is not None
                else None
            ),
            "source_fps": _number(
                stream,
                "meta/video/frame_rate",
                float,
            ),
        },
        "audio": {
            "codec": _text(stream, "meta/audio/codec"),
            "profile": _text(stream, "meta/audio/profile"),
            "sample_rate_hz": _number(
                stream,
                "meta/audio/sample_rate",
            ),
            "channels": _number(stream, "meta/audio/channels"),
        },
    }


def parse_rtmp_stat(root):
    """Build a safe RTMP snapshot without stream names or client IPs."""

    applications = {}
    active_streams = 0
    clients = 0
    for application in root.findall(".//application"):
        app_name = application.findtext("name", default="unknown")
        streams = application.findall("./live/stream")
        stream_count = len(streams)
        client_count = sum(
            int(stream.findtext("nclients", default="0") or 0)
            for stream in streams
        )
        if stream_count or client_count:
            applications[app_name] = {
                "streams": stream_count,
                "clients": client_count,
                "stream_metrics": [
                    _stream_metrics(stream)
                    for stream in streams
                ],
            }
        active_streams += stream_count
        clients += client_count

    return {
        "reachable": True,
        "active_streams": active_streams,
        "clients": clients,
        "applications": applications,
    }


def rtmp_snapshot(stat_url, timeout=2):
    with urllib.request.urlopen(stat_url, timeout=timeout) as response:
        root = ET.fromstring(response.read())
    return parse_rtmp_stat(root)


def system_snapshot(settings):
    memory = psutil.virtual_memory()
    disk_path = settings.hls_root if settings.hls_root.exists() else settings.project_root
    disk = psutil.disk_usage(disk_path)
    load_average = os.getloadavg()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "load_average": {
            "1m": round(load_average[0], 2),
            "5m": round(load_average[1], 2),
            "15m": round(load_average[2], 2),
        },
        "memory": {
            "total_bytes": memory.total,
            "available_bytes": memory.available,
            "used_percent": memory.percent,
        },
        "hls_disk": {
            "total_bytes": disk.total,
            "free_bytes": disk.free,
            "used_percent": disk.percent,
        },
    }


def health_snapshot(settings):
    checks = {}
    try:
        config = read_config(settings.config_file)
        checks["config"] = {
            "ok": True,
            "fields": len(config.get("fields", {})),
            "schema_version": config["schema_version"],
        }
    except (OSError, ValueError, json.JSONDecodeError):
        checks["config"] = {"ok": False}

    checks["ffmpeg"] = {
        "ok": settings.ffmpeg_bin.is_file() and os.access(settings.ffmpeg_bin, os.X_OK),
    }
    checks["hls_root"] = {
        "ok": settings.hls_root.is_dir(),
    }

    try:
        rtmp = rtmp_snapshot(settings.rtmp_stat_url)
        checks["rtmp_stat"] = {"ok": rtmp["reachable"]}
    except (OSError, ValueError, ET.ParseError):
        checks["rtmp_stat"] = {"ok": False}

    return {
        "status": "ok" if all(check["ok"] for check in checks.values()) else "degraded",
        "timestamp": timestamp_utc(),
        "version": __version__,
        "checks": checks,
    }


def metrics_snapshot(settings):
    config = read_config(settings.config_file)
    fields = config.get("fields", {})
    safe_config = {
        "schema_version": config["schema_version"],
        "configured_places": len(fields),
        "enabled_places": sum(field.get("enabled") is True for field in fields.values()),
        "publish_auth_enabled_places": sum(
            field.get("publish_auth_enabled") is True for field in fields.values()
        ),
    }

    try:
        rtmp = rtmp_snapshot(settings.rtmp_stat_url)
    except (OSError, ValueError, ET.ParseError):
        rtmp = {
            "reachable": False,
            "active_streams": 0,
            "clients": 0,
            "applications": {},
        }

    return {
        "timestamp": timestamp_utc(),
        "version": __version__,
        "config": safe_config,
        "hls": hls_snapshot(settings, config),
        "rtmp": rtmp,
        "restream": restream_snapshot(settings, config),
        "system": system_snapshot(settings),
    }
