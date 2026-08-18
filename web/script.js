let streams = [];
let nodeMetrics = null;
const sourceMetricsCache = new Map();
const streamPlayers = new Map();

const PLAYER_RETRY_MIN_MS = 1000;
const PLAYER_RETRY_MAX_MS = 15000;
const PLAYER_STALL_TIMEOUT_MS = 15000;

const MONITOR_LAYOUT_KEY = 'cricket-monitor-columns';

function setMonitorColumns(value) {
    const columns = ['2', '3', '4'].includes(String(value))
        ? String(value)
        : '4';
    const grid = document.getElementById('streamsGrid');
    if (!grid) return;

    grid.classList.remove('columns-2', 'columns-3', 'columns-4');
    grid.classList.add(`columns-${columns}`);

    document.querySelectorAll('.layout-switcher button').forEach(button => {
        button.classList.toggle(
            'active',
            button.dataset.columns === columns
        );
    });

    localStorage.setItem(MONITOR_LAYOUT_KEY, columns);
}

function setupMonitorLayout() {
    document.querySelectorAll('.layout-switcher button').forEach(button => {
        button.addEventListener('click', () => {
            setMonitorColumns(button.dataset.columns);
        });
    });
    setMonitorColumns(localStorage.getItem(MONITOR_LAYOUT_KEY) || '4');
}

function getPlaceMetrics(prefix) {
    const source = sourceMetricsCache.get(String(prefix)) || null;
    const hls = nodeMetrics?.hls?.places?.[String(prefix)] || null;
    return { source, hls };
}

function mergeSourceMetrics(metrics) {
    const applications = metrics?.rtmp?.applications || {};

    Object.entries(applications).forEach(([applicationName, application]) => {
        const match = /^place([1-9]|1[0-6])$/.exec(applicationName);
        if (!match) return;

        const candidates = application?.stream_metrics || [];
        const source = candidates.find(item => item.publishers > 0)
            || candidates.find(item => (
                item?.video?.resolution
                || item?.video?.codec
                || Number.isFinite(item?.input_bitrate_bps)
            ));

        if (source) {
            sourceMetricsCache.set(match[1], source);
        }
    });

    const places = metrics?.hls?.places || {};
    Object.entries(places).forEach(([placeId, hls]) => {
        // no_signal already means that no fresh HLS segment has existed for
        // at least two minutes. A single incomplete RTMP snapshot must not
        // erase the last trustworthy source metadata.
        if (hls?.state === 'no_signal') {
            sourceMetricsCache.delete(String(placeId));
        }
    });
}

function updateStreamStatus(stream) {
    const status = document.getElementById(stream.statusId);
    if (!status) return;

    const { hls } = getPlaceMetrics(stream.prefix);
    const state = hls?.state || 'no_signal';

    if (state === 'active') {
        status.className = 'status online';
        status.textContent = 'Live';
    } else if (state === 'stale') {
        status.className = 'status stale';
        status.textContent = 'Stale';
    } else {
        status.className = 'status offline';
        status.textContent = 'Offline';
    }

    streamPlayers.get(String(stream.prefix))?.setServerState(state);
}

function setMetric(prefix, name, value, className = 'tech-value') {
    const element = document.getElementById(`${name}${prefix}`);
    if (!element) return;
    element.textContent = value ?? '-';
    element.className = className;
}

function formatBitrate(value) {
    return Number.isFinite(value) ? `${(value / 1000000).toFixed(2)} Mbps` : '-';
}

function formatUptime(value) {
    if (!Number.isFinite(value)) return '-';
    const total = Math.max(0, Math.floor(value));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const clock = [hours, minutes, seconds]
        .map(part => String(part).padStart(2, '0'))
        .join(':');
    return days ? `${days}d ${clock}` : clock;
}

function formatVideoCodec(video) {
    if (!video?.codec) return '-';
    const codec = video.codec.toUpperCase() === 'H264' ? 'H.264' : video.codec;
    return [codec, video.profile, video.level ? `L${video.level}` : null]
        .filter(Boolean)
        .join(' · ');
}

function formatAudioCodec(audio) {
    if (!audio?.codec) return '-';
    const rate = Number.isFinite(audio.sample_rate_hz)
        ? `${(audio.sample_rate_hz / 1000).toFixed(1).replace('.0', '')} kHz`
        : null;
    const channels = Number.isFinite(audio.channels) ? `${audio.channels} ch` : null;
    return [audio.codec, audio.profile, rate, channels].filter(Boolean).join(' · ');
}

function getColorClass(value, thresholds, inverted = false) {
    if (inverted) {
        if (value <= thresholds.good) return 'tech-value good';
        if (value <= thresholds.warning) return 'tech-value warning';
        return 'tech-value error';
    } else {
        if (value >= thresholds.good) return 'tech-value good';
        if (value >= thresholds.warning) return 'tech-value warning';
        return 'tech-value error';
    }
}

function getBufferColorClass(value) {
    if (value >= 5 && value <= 15) return 'tech-value good';
    if (value >= 2 && value < 5) return 'tech-value warning';
    if (value > 15 && value <= 20) return 'tech-value warning';
    return 'tech-value error';
}

function getLatencyColorClass(value) {
    if (value >= 5 && value <= 15) return 'tech-value good';
    if (value >= 2 && value < 5) return 'tech-value warning';
    if (value > 15 && value <= 20) return 'tech-value warning';
    return 'tech-value error';
}

function updateTechInfo(stream, hls, video) {
    const p = stream.prefix;
    const { source, hls: serverHls } = getPlaceMetrics(p);
    const sourceVideo = source?.video;
    const sourceAudio = source?.audio;

    setMetric(p, 'resolution', sourceVideo?.resolution?.replace('x', '×'));
    setMetric(
        p,
        'fps',
        Number.isFinite(sourceVideo?.source_fps)
            ? `${sourceVideo.source_fps.toFixed(1)} fps`
            : '-'
    );
    setMetric(p, 'bitrate', formatBitrate(source?.input_bitrate_bps));
    setMetric(p, 'codec', formatVideoCodec(sourceVideo));
    setMetric(p, 'audio', formatAudioCodec(sourceAudio));
    setMetric(p, 'uptime', formatUptime(source?.uptime_seconds));
    const rtmpDropped = source?.publisher_dropped;
    setMetric(
        p,
        'dropped',
        Number.isFinite(rtmpDropped) ? String(rtmpDropped) : '-',
        Number.isFinite(rtmpDropped)
            ? getColorClass(rtmpDropped, {good: 0, warning: 10}, true)
            : 'tech-value'
    );
    const mediaAge = serverHls?.latest_segment_age_seconds;
    setMetric(
        p,
        'lastupdate',
        Number.isFinite(mediaAge) ? `${mediaAge.toFixed(1)}s ago` : '-',
        Number.isFinite(mediaAge)
            ? getColorClass(mediaAge, {good: 10, warning: 30}, true)
            : 'tech-value'
    );
    
    // Keyframe
    const kfEl = document.getElementById(`keyframe${p}`);
    if (kfEl) {
        if (hls && hls.levels && hls.levels.length > 0) {
            const level = hls.levels[hls.currentLevel];
            if (level.details && level.details.fragments && level.details.fragments.length > 0) {
                const fragment = level.details.fragments[0];
                const segmentDuration = fragment.duration;
                kfEl.innerHTML = `${segmentDuration.toFixed(1)}s <span class="tech-hint">(HLS segment)</span>`;
                kfEl.className = getColorClass(segmentDuration, {good: 4, warning: 6});
            } else {
                kfEl.innerHTML = `4.0s <span class="tech-hint">(HLS segment)</span>`;
                kfEl.className = 'tech-value good';
            }
        } else {
            kfEl.textContent = '-';
            kfEl.className = 'tech-value';
        }
    }
    
    // Buffer (5-15s зелёный, 2-5s или 15-20s жёлтый, <2s или >20s красный)
    const bufEl = document.getElementById(`buffer${p}`);
    if (bufEl) {
        if (video.buffered.length > 0) {
            const bufferEnd = video.buffered.end(video.buffered.length - 1);
            const bufferDuration = bufferEnd - video.currentTime;
            bufEl.textContent = `${bufferDuration.toFixed(1)}s`;
            bufEl.className = getBufferColorClass(bufferDuration);
        } else {
            bufEl.textContent = '-';
            bufEl.className = 'tech-value';
        }
    }
    
    // Browser playback drops are distinct from RTMP publisher drops.
    const browserDropEl = document.getElementById(`browserdropped${p}`);
    if (browserDropEl) {
        if (typeof video.getVideoPlaybackQuality === 'function') {
            const quality = video.getVideoPlaybackQuality();
            const dropped = quality.droppedVideoFrames || 0;
            const corrupted = quality.corruptedVideoFrames || 0;
            browserDropEl.textContent = corrupted ? `${dropped} / ${corrupted} corrupt` : String(dropped);
            browserDropEl.className = getColorClass(dropped, {good: 0, warning: 10}, true);
        } else {
            browserDropEl.textContent = 'N/A';
            browserDropEl.className = 'tech-value';
        }
    }
    
    // Delay (5-15s зелёный, 2-5s или 15-20s жёлтый, <2s или >20s красный)
    const latEl = document.getElementById(`latency${p}`);
    if (latEl) {
        if (hls && hls.latency) {
            const latency = hls.latency;
            latEl.textContent = `${latency.toFixed(1)}s`;
            latEl.className = getLatencyColorClass(latency);
        } else {
            latEl.textContent = '-';
            latEl.className = 'tech-value';
        }
    }
    
    updateStreamStatus(stream);
}

function createStreamPlayer(stream) {
    const video = document.getElementById(stream.id);
    if (!video) return null;

    let hls = null;
    let retryTimer = null;
    let watchdogTimer = null;
    let retryDelay = PLAYER_RETRY_MIN_MS;
    let serverState = 'no_signal';
    let lastPlaybackTime = null;
    let lastProgressAt = Date.now();
    let destroyed = false;

    function clearRetry() {
        if (retryTimer !== null) {
            clearTimeout(retryTimer);
            retryTimer = null;
        }
    }

    function scheduleRecovery(reason) {
        if (destroyed || retryTimer !== null) return;

        const delay = retryDelay;
        retryDelay = Math.min(retryDelay * 2, PLAYER_RETRY_MAX_MS);
        console.warn(
            `Player ${stream.prefix}: recovery in ${delay} ms (${reason})`
        );

        retryTimer = setTimeout(() => {
            retryTimer = null;
            rebuild();
        }, delay);
    }

    function markPlaybackProgress() {
        retryDelay = PLAYER_RETRY_MIN_MS;
        lastPlaybackTime = video.currentTime;
        lastProgressAt = Date.now();
    }

    function attachHlsJs() {
        hls = new Hls({
            liveSyncDurationCount: 3,
            liveMaxLatencyDurationCount: 10,
            enableWorker: true
        });

        hls.on(Hls.Events.MANIFEST_PARSED, () => {
            markPlaybackProgress();
            video.play().catch(() => {});
        });

        hls.on(Hls.Events.FRAG_LOADED, markPlaybackProgress);

        hls.on(Hls.Events.ERROR, (event, data) => {
            if (!data.fatal) return;

            if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                try {
                    hls.recoverMediaError();
                    return;
                } catch (error) {
                    console.warn(
                        `Player ${stream.prefix}: media recovery failed`,
                        error
                    );
                }
            }

            scheduleRecovery(data.details || data.type || 'fatal HLS error');
        });

        hls.loadSource(stream.url);
        hls.attachMedia(video);
    }

    function attachNativeHls() {
        video.src = stream.url;
        video.load();
        video.play().catch(() => {});
    }

    function rebuild() {
        if (destroyed) return;
        clearRetry();

        if (hls) {
            hls.destroy();
            hls = null;
        }

        video.pause();
        video.removeAttribute('src');
        video.load();
        lastPlaybackTime = null;
        lastProgressAt = Date.now();

        if (typeof Hls !== 'undefined' && Hls.isSupported()) {
            attachHlsJs();
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            attachNativeHls();
        }
    }

    video.addEventListener('playing', markPlaybackProgress);
    video.addEventListener('timeupdate', () => {
        if (video.currentTime !== lastPlaybackTime) {
            markPlaybackProgress();
        }
    });
    video.addEventListener('error', () => {
        scheduleRecovery('video element error');
    });
    video.addEventListener('stalled', () => {
        scheduleRecovery('video stalled');
    });

    watchdogTimer = setInterval(() => {
        updateTechInfo(stream, hls, video);

        if (serverState !== 'active') return;

        const playbackStalled = (
            video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
            || Date.now() - lastProgressAt > PLAYER_STALL_TIMEOUT_MS
        );
        if (playbackStalled) {
            scheduleRecovery('active stream is not advancing');
        } else if (video.paused) {
            video.play().catch(() => {});
        }
    }, 2000);

    rebuild();

    return {
        setServerState(nextState) {
            const becameActive = (
                nextState === 'active'
                && serverState !== 'active'
            );
            serverState = nextState;
            if (becameActive) {
                clearRetry();
                retryDelay = PLAYER_RETRY_MIN_MS;
                scheduleRecovery('stream became active');
            }
        },
        destroy() {
            destroyed = true;
            clearRetry();
            if (watchdogTimer !== null) clearInterval(watchdogTimer);
            if (hls) hls.destroy();
        }
    };
}

function initStream(stream) {
    const existing = streamPlayers.get(String(stream.prefix));
    existing?.destroy();

    const player = createStreamPlayer(stream);
    if (player) {
        streamPlayers.set(String(stream.prefix), player);
    }

    const video = document.getElementById(stream.id);
    if (video) updateTechInfo(stream, null, video);
}

async function refreshNodeMetrics() {
    try {
        const response = await fetch('/api/node/metrics', {cache: 'no-store'});
        if (!response.ok) throw new Error(`Metrics HTTP ${response.status}`);
        const metrics = await response.json();
        mergeSourceMetrics(metrics);
        nodeMetrics = metrics;
        streams.forEach(stream => updateStreamStatus(stream));
    } catch (error) {
        console.warn('Server metrics are temporarily unavailable:', error);
    }
}

async function loadStreams() {
    try {
        const [fieldsResponse] = await Promise.all([
            fetch('/api/fields'),
            refreshNodeMetrics(),
        ]);
        if (!fieldsResponse.ok) throw new Error(`Fields HTTP ${fieldsResponse.status}`);
        const fieldsData = await fieldsResponse.json();
        
        streams = Object.entries(fieldsData)
            .filter(([id]) => {
                const fieldNumber = Number(id);
                return Number.isInteger(fieldNumber)
                    && fieldNumber >= 1
                    && fieldNumber <= 16;
            })
            .map(([id, field]) => ({
                id: `stream${id}`,
                url: field.hls_url,
                statusId: `status${id}`,
                prefix: id,
                name: field.name,
                emoji: field.emoji || '🏟️',
                rtmpUrl: field.rtmp_url
            }));
        
        renderStreams();
        streams.forEach(stream => initStream(stream));
        
    } catch (error) {
        document.getElementById('streamsGrid').innerHTML = 
            '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #f44336;">Failed to load streams. Please try again later.</div>';
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function renderStreams() {
    const grid = document.getElementById('streamsGrid');

    if (streams.length === 0) {
        grid.innerHTML = '<div class="empty-state">No streams enabled. Configure streams at <a href="/config/">Configuration</a>.</div>';
        return;
    }
    
    grid.innerHTML = streams.map(stream => {
        const safeEmoji = escapeHtml(stream.emoji);
        const safeName = escapeHtml(stream.name);
        const safeRtmpBase = escapeHtml(
            stream.rtmpUrl.replace(/\/[^\/]+$/, '')
        );
        const safeStreamKey = escapeHtml(
            stream.rtmpUrl.split('/').pop()
        );

        return `
        <div class="stream-card">
            <div class="stream-card-header">
                <div class="stream-title">
                    <span class="stream-emoji">${safeEmoji}</span>
                    <span class="stream-name">${safeName}</span>
                </div>
                <div class="status offline" id="${stream.statusId}">Offline</div>
            </div>
            <div class="video-container"><video id="${stream.id}" controls autoplay muted></video></div>

            <div class="stream-details">
                <div class="detail-panel">
                    <div class="panel-title">Publish settings</div>
                    <div class="stream-url">
                        <div class="stream-url-row">
                            <span class="stream-url-label">RTMP URL</span>
                            <code class="stream-url-value">${safeRtmpBase}</code>
                        </div>
                        <div class="stream-url-row">
                            <span class="stream-url-label">Stream key</span>
                            <code class="stream-url-value">${safeStreamKey}</code>
                        </div>
                    </div>
                </div>
                <div class="detail-panel">
                    <div class="panel-title">Technical information</div>
                    <div class="tech-info" id="tech${stream.prefix}">
                        <div class="tech-row"><span class="tech-label">Resolution</span><span class="tech-value" id="resolution${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Source FPS</span><span class="tech-value" id="fps${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Input bitrate</span><span class="tech-value" id="bitrate${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Video codec</span><span class="tech-value" id="codec${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Audio codec</span><span class="tech-value" id="audio${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Uptime</span><span class="tech-value" id="uptime${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">RTMP dropped</span><span class="tech-value" id="dropped${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Last media</span><span class="tech-value" id="lastupdate${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">HLS segment</span><span class="tech-value" id="keyframe${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">HLS latency</span><span class="tech-value" id="latency${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Player buffer</span><span class="tech-value" id="buffer${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Browser dropped</span><span class="tech-value" id="browserdropped${stream.prefix}">-</span></div>
                    </div>
                </div>
            </div>
        </div>
        `;
    }).join('');
}

setupMonitorLayout();
loadStreams();
setInterval(refreshNodeMetrics, 5000);
