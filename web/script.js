const streamStats = {};
let streams = [];

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

function checkStreamStatus(videoElement, statusElement) {
    if (videoElement.readyState >= 3 && !videoElement.paused && !videoElement.ended) {
        statusElement.className = 'status online';
        statusElement.textContent = '🟢 Live';
    } else {
        statusElement.className = 'status offline';
        statusElement.textContent = '⚫ Offline';
    }
}

function setupFpsCounter(video, streamId) {
    if (!streamStats[streamId]) {
        streamStats[streamId] = {
            frameCount: 0,
            lastFpsUpdate: performance.now(),
            currentFps: 0,
            totalBytes: 0,
            lastBitrateUpdate: performance.now(),
            currentBitrate: 0,
            lastTotalFrames: 0,
            lastDataUpdate: Date.now()
        };
    }
    
    const stats = streamStats[streamId];
    
    if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
        function countFrame(now, metadata) {
            stats.frameCount++;
            const elapsed = (now - stats.lastFpsUpdate) / 1000;
            if (elapsed >= 2) {
                stats.currentFps = stats.frameCount / elapsed;
                stats.frameCount = 0;
                stats.lastFpsUpdate = now;
                stats.lastDataUpdate = Date.now();
            }
            video.requestVideoFrameCallback(countFrame);
        }
        video.requestVideoFrameCallback(countFrame);
    } else {
        setInterval(() => {
            if (typeof video.getVideoPlaybackQuality === 'function') {
                const quality = video.getVideoPlaybackQuality();
                const now = performance.now();
                const elapsed = (now - stats.lastFpsUpdate) / 1000;
                if (elapsed >= 2) {
                    const frameDelta = quality.totalVideoFrames - (stats.lastTotalFrames || 0);
                    stats.currentFps = frameDelta / elapsed;
                    stats.lastTotalFrames = quality.totalVideoFrames;
                    stats.lastFpsUpdate = now;
                    stats.lastDataUpdate = Date.now();
                }
            }
        }, 2000);
    }
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
    const now = new Date();
    const stats = streamStats[stream.id];
    
    const width = video.videoWidth || 0;
    const height = video.videoHeight || 0;
    const isHD = width > 1280;
    
    // Resolution
    const resEl = document.getElementById(`resolution${p}`);
    if (resEl) {
        if (width > 0) {
            resEl.textContent = `${width}×${height}`;
            if (height >= 720) {
                resEl.className = 'tech-value good';
            } else if (height >= 480) {
                resEl.className = 'tech-value warning';
            } else {
                resEl.className = 'tech-value error';
            }
        } else {
            resEl.textContent = '-';
            resEl.className = 'tech-value';
        }
    }
    
    // FPS (>20 зелёный, 15-20 жёлтый, <15 красный)
    const fpsEl = document.getElementById(`fps${p}`);
    if (fpsEl) {
        if (stats && stats.currentFps > 0) {
            const fps = stats.currentFps.toFixed(1);
            fpsEl.textContent = `${fps} fps`;
            fpsEl.className = getColorClass(parseFloat(fps), {good: 20, warning: 15});
        } else {
            fpsEl.textContent = 'measuring...';
            fpsEl.className = 'tech-value';
        }
    }
    
    // Bitrate (зависит от разрешения)
    const brEl = document.getElementById(`bitrate${p}`);
    if (brEl) {
        let bitrateValue = 0;
        
        if (stats && stats.currentBitrate > 0) {
            bitrateValue = stats.currentBitrate / 1000000;
            const mbps = bitrateValue.toFixed(2);
            brEl.textContent = `~${mbps} Mbps (measured)`;
        } else if (hls && hls.levels && hls.levels[hls.currentLevel] && hls.levels[hls.currentLevel].bitrate > 0) {
            const level = hls.levels[hls.currentLevel];
            bitrateValue = level.bitrate / 1000000;
            brEl.textContent = `${bitrateValue.toFixed(2)} Mbps`;
        } else {
            brEl.textContent = '-';
            brEl.className = 'tech-value';
        }
        
        if (bitrateValue > 0) {
            if (isHD) {
                brEl.className = getColorClass(bitrateValue, {good: 3.5, warning: 2.5});
            } else {
                brEl.className = getColorClass(bitrateValue, {good: 2.0, warning: 1.5});
            }
        }
    }
    
    // Codec
    const codecEl = document.getElementById(`codec${p}`);
    if (codecEl) {
        if (hls && hls.levels && hls.levels[hls.currentLevel]) {
            const level = hls.levels[hls.currentLevel];
            if (level.videoCodec) {
                let codecName = level.videoCodec;
                if (codecName.startsWith('avc1')) codecName = 'H.264';
                else if (codecName.startsWith('hvc1') || codecName.startsWith('hev1')) codecName = 'H.265';
                else if (codecName.startsWith('av01')) codecName = 'AV1';
                codecEl.textContent = codecName;
                codecEl.className = 'tech-value good';
            } else {
                codecEl.textContent = 'H.264 (assumed)';
                codecEl.className = 'tech-value good';
            }
        } else {
            codecEl.textContent = 'H.264 (assumed)';
            codecEl.className = 'tech-value good';
        }
    }
    
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
    
    // Dropped frames
    const dropEl = document.getElementById(`dropped${p}`);
    if (dropEl) {
        if (typeof video.getVideoPlaybackQuality === 'function') {
            const quality = video.getVideoPlaybackQuality();
            const dropped = quality.droppedVideoFrames || 0;
            const corrupted = quality.corruptedVideoFrames || 0;
            dropEl.textContent = `${dropped} dropped, ${corrupted} corrupted`;
            dropEl.className = getColorClass(dropped, {good: 1, warning: 11});
        } else {
            dropEl.textContent = 'N/A';
            dropEl.className = 'tech-value';
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
    
    // Last update (показываем сколько секунд назад обновлялись данные)
    const updEl = document.getElementById(`lastupdate${p}`);
    if (updEl) {
        if (stats && stats.lastDataUpdate) {
            const secondsAgo = Math.floor((Date.now() - stats.lastDataUpdate) / 1000);
            updEl.textContent = `${secondsAgo}s ago`;
            if (secondsAgo <= 5) {
                updEl.className = 'tech-value good';
            } else if (secondsAgo <= 10) {
                updEl.className = 'tech-value warning';
            } else {
                updEl.className = 'tech-value error';
            }
        } else {
            updEl.textContent = '-';
            updEl.className = 'tech-value';
        }
    }
}

function initStream(stream) {
    const video = document.getElementById(stream.id);
    const status = document.getElementById(stream.statusId);
    
    if (!video) return;
    
    if (typeof Hls !== 'undefined' && Hls.isSupported()) {
        const hls = new Hls({
            liveSyncDurationCount: 3,
            liveMaxLatencyDurationCount: 10,
            enableWorker: true
        });
        
        hls.loadSource(stream.url);
        hls.attachMedia(video);
        
        hls.on(Hls.Events.MANIFEST_PARSED, function() {
            video.play().catch(e => {});
        });
        
        hls.on(Hls.Events.FRAG_LOADED, function(event, data) {
            if (streamStats[stream.id]) {
                const stats = streamStats[stream.id];
                let fragmentSize = 0;
                
                if (data.frag && data.frag.stats && data.frag.stats.total) {
                    fragmentSize = data.frag.stats.total;
                } else if (data.frag && data.frag.data) {
                    fragmentSize = data.frag.data.byteLength;
                } else if (data.payload) {
                    fragmentSize = data.payload.byteLength;
                }
                
                if (fragmentSize > 0) {
                    stats.totalBytes += fragmentSize;
                    const now = performance.now();
                    const elapsed = (now - stats.lastBitrateUpdate) / 1000;
                    if (elapsed >= 3) {
                        stats.currentBitrate = (stats.totalBytes * 8) / elapsed;
                        stats.totalBytes = 0;
                        stats.lastBitrateUpdate = now;
                    }
                }
            }
        });
        
        hls.on(Hls.Events.ERROR, function(event, data) {
            if (data.fatal) {
                status.className = 'status offline';
                status.textContent = '⚫ Offline';
            }
        });
        
        setupFpsCounter(video, stream.id);
        
        setInterval(() => {
            if (video.readyState >= 2) {
                updateTechInfo(stream, hls, video);
            }
        }, 2000);
        
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = stream.url;
        video.addEventListener('loadedmetadata', function() {
            video.play().catch(e => {});
        });
        
        setupFpsCounter(video, stream.id);
        
        setInterval(() => {
            if (video.readyState >= 2) {
                updateTechInfo(stream, null, video);
            }
        }, 2000);
    }
    
    setInterval(() => checkStreamStatus(video, status), 5000);
    video.addEventListener('playing', () => checkStreamStatus(video, status));
    video.addEventListener('pause', () => checkStreamStatus(video, status));
    video.addEventListener('ended', () => checkStreamStatus(video, status));
    
}

async function loadStreams() {
    try {
        const response = await fetch('/api/fields');
        const fieldsData = await response.json();
        
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
                        <div class="tech-row"><span class="tech-label">FPS</span><span class="tech-value" id="fps${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Bitrate</span><span class="tech-value" id="bitrate${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Codec</span><span class="tech-value" id="codec${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">HLS segment</span><span class="tech-value" id="keyframe${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Player buffer</span><span class="tech-value" id="buffer${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Dropped frames</span><span class="tech-value" id="dropped${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">HLS latency</span><span class="tech-value" id="latency${stream.prefix}">-</span></div>
                        <div class="tech-row"><span class="tech-label">Last update</span><span class="tech-value" id="lastupdate${stream.prefix}">-</span></div>
                    </div>
                </div>
            </div>
        </div>
        `;
    }).join('');
}

setupMonitorLayout();
loadStreams();
