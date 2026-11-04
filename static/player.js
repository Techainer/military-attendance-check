/**
 * Trình phát luồng xem: ưu tiên go2rtc (WebRTC, rồi MSE) relay thẳng bit RTSP
 * tới trình duyệt, chỉ rơi về <img> MJPEG (do Python mã hoá) khi go2rtc tắt
 * hoặc không gọi được — vì go2rtc chỉ relay, không tốn CPU vẽ+encode mỗi khung
 * như Python, nên luồng mượt và ít trễ hơn nhiều.
 *
 * Lớp phủ AI (khung người, định danh, vùng, HUD) KHÔNG còn vẽ sẵn trong khung
 * hình nữa khi dùng go2rtc: go2rtc không biết gì về AI, chỉ relay video gốc.
 * Lớp phủ được vẽ ngay trên <canvas> từ luồng JSON số liệu (SSE
 * /detections), theo đúng màu/nhãn của draw_overlay() trong
 * app/video_processor.py để nhìn giống bản MJPEG cũ.
 *
 * Module này được app.js gọi qua window.AiPlayer (app.js là script thường,
 * không phải module, nên không import ES module trực tiếp được).
 */
import '/static/vendor/go2rtc/video-stream.js';

// ---------------------------------------------------------------- cấu hình

const CONFIG_URL = '/api/v1/system/config';
let _configPromise = null;
let _configAt = 0;
// Cấu hình có mật khẩu TURN tạm (hết hạn, và chỉ có sau khi đăng nhập) — không
// giữ mãi bản lấy lúc còn ở màn đăng nhập
const CONFIG_MAX_AGE_MS = 5 * 60 * 1000;

function systemConfig() {
    if (!_configPromise || Date.now() - _configAt > CONFIG_MAX_AGE_MS) {
        _configAt = Date.now();
        _configPromise = fetch(CONFIG_URL)
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null)
            .then((cfg) => cfg || { go2rtc_enabled: false, go2rtc_proxy_base: '/go2rtc', ice_servers: [] });
    }
    return _configPromise;
}

async function probeGo2rtc(proxyBase, timeoutMs = 3000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        const res = await fetch(`${proxyBase}/api`, { signal: ctrl.signal });
        return res.ok;
    } catch (e) {
        return false;
    } finally {
        clearTimeout(timer);
    }
}

// Camera là H.265/HEVC; nhiều trình duyệt không chơi được HEVC qua MSE/WebRTC.
// go2rtc có sẵn bản chuyển mã H.264 (tên luồng hậu tố "_h264") — chọn bản nào
// theo đúng gợi ý trong nhiệm vụ: thử MediaSource rồi thử khả năng WebRTC.
function supportsHevc() {
    try {
        if (window.MediaSource && MediaSource.isTypeSupported('video/mp4; codecs="hvc1.1.6.L120.90"')) {
            return true;
        }
    } catch (e) { /* một số trình duyệt ném lỗi thay vì trả false */ }
    try {
        const caps = window.RTCRtpReceiver && RTCRtpReceiver.getCapabilities
            && RTCRtpReceiver.getCapabilities('video');
        if (caps && caps.codecs && caps.codecs.some((c) => /H265|HEVC/i.test(c.mimeType))) return true;
    } catch (e) { /* trình duyệt cũ không có RTCRtpReceiver.getCapabilities */ }
    return false;
}

// variant 'sub' = ô lưới (nhẹ hơn), 'main' = màn chi tiết. go2rtc luôn có đủ
// bốn tên luồng cho mỗi camera (xem app/go2rtc.py::_stream_names) — "_sub"
// là bí danh của luồng chính khi camera chưa khai luồng phụ riêng.
function streamName(cameraId, variant) {
    const base = variant === 'sub' ? `${cameraId}_sub` : cameraId;
    return supportsHevc() ? base : `${base}_h264`;
}

// ---------------------------------------------------------------- màu/nhãn
// PHẢI khớp draw_overlay() trong app/video_processor.py — server đã tính sẵn
// color/label chuẩn cho từng track trong _publish_ai_result, nên ở đây chỉ
// việc dùng lại, không tính lại luật nghiệp vụ ở phía trình duyệt.

const HUD_COLORS = {
    stat: '#00ff78',
    roll: '#00c8ff',
    text: '#e6e6e6',
};

const STATUS_LABEL = { live: 'LIVE', stale: 'TRỄ', lost: 'MẤT TÍN HIỆU', connecting: 'ĐANG KẾT NỐI' };

// ---------------------------------------------------------------- state

const _state = new Map(); // imgEl -> record

function esc(s) { return String(s == null ? '' : s); }

function teardown(state) {
    if (!state) return;
    if (state.es) { try { state.es.close(); } catch (e) {} }
    if (state.rafId) cancelAnimationFrame(state.rafId);
    if (state.rvfcHandle && state.video && state.video.video && state.video.video.cancelVideoFrameCallback) {
        try { state.video.video.cancelVideoFrameCallback(state.rvfcHandle); } catch (e) {}
    }
    if (state.watchdog) clearTimeout(state.watchdog);
    if (state.statusTimer) clearInterval(state.statusTimer);
    if (state.video && state.video.parentNode) state.video.parentNode.removeChild(state.video);
    if (state.canvas && state.canvas.parentNode) state.canvas.parentNode.removeChild(state.canvas);
    if (state.badge && state.badge.parentNode) state.badge.parentNode.removeChild(state.badge);
}

export function detachPlayer(imgEl) {
    if (!imgEl) return;
    const state = _state.get(imgEl);
    if (state) {
        teardown(state);
        _state.delete(imgEl);
    }
    imgEl.style.display = '';
    imgEl.removeAttribute('src');
}

// ---------------------------------------------------------------- MJPEG (dự phòng)

function mjpegUrl(cameraId, overlay) {
    return `/api/v1/cameras/${cameraId}/stream.mjpg?overlay=${overlay ? 1 : 0}&_=${Date.now()}`;
}

function attachMjpeg(imgEl, cameraId, overlayEnabled) {
    imgEl.style.display = '';
    imgEl.src = mjpegUrl(cameraId, overlayEnabled);
    imgEl.onerror = () => { imgEl.removeAttribute('src'); };
    _state.set(imgEl, { mode: 'mjpeg', cameraId, variant: 'main', overlayEnabled });
}

// ---------------------------------------------------------------- go2rtc

function buildBadge() {
    const badge = document.createElement('span');
    badge.className = 'player-status-badge player-status-connecting';
    badge.textContent = STATUS_LABEL.connecting;
    return badge;
}

function setBadge(state, kind) {
    if (!state.badge) return;
    state.badge.className = `player-status-badge player-status-${kind}`;
    state.badge.textContent = STATUS_LABEL[kind] || kind;
}

// Vẽ một kết quả AI (xem shape trong app/video_processor.py::_publish_ai_result)
// lên canvas, toạ độ chuẩn hoá 0..1 nên không cần biết kích thước video gốc.
function drawDetections(ctx, w, h, result, zonesMeta) {
    ctx.clearRect(0, 0, w, h);

    (zonesMeta || []).filter((z) => z.enabled !== false).forEach((zone) => {
        const pts = zone.points || [];
        if (pts.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(pts[0].x * w, pts[0].y * h);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x * w, pts[i].y * h);
        if (zone.rule !== 'crossing_line') ctx.closePath();
        ctx.strokeStyle = zone.rule === 'restricted_area' ? '#ff3c3c' : '#22dc5a';
        ctx.lineWidth = 2;
        ctx.stroke();
    });

    if (!result) return;

    // Khung camera nhỏ (lưới nhiều camera) thì chữ nhỏ lại; nhãn dài cắt theo
    // bề ngang box; nhãn đè lên nhãn đã vẽ thì thử đặt trong box, vẫn đè thì bỏ
    // (box và màu vẫn còn, chỉ mất chữ) — tránh mớ chữ chồng nhau không đọc nổi
    const fontPx = w < 520 ? 11 : 13;
    const labelH = fontPx + 5;
    const placed = [];
    const overlaps = (r) => placed.some((p) =>
        r.x < p.x + p.w && p.x < r.x + r.w && r.y < p.y + p.h && p.y < r.y + r.h);
    const fitText = (text, maxW) => {
        if (ctx.measureText(text).width <= maxW) return text;
        let t = text;
        while (t.length > 1 && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
        return t + '…';
    };

    (result.tracks || []).forEach((t) => {
        const [x1, y1, x2, y2] = t.box;
        const bx = x1 * w, by = y1 * h, bw = (x2 - x1) * w, bh = (y2 - y1) * h;
        ctx.strokeStyle = t.color || '#788692';
        ctx.lineWidth = t.violation ? 3 : 2;
        ctx.strokeRect(bx, by, bw, bh);

        if (t.label) {
            ctx.font = `${fontPx}px "Segoe UI", sans-serif`;
            ctx.textBaseline = 'alphabetic';
            const padding = 3;
            const text = fitText(t.label, Math.max(bw, 70) - padding * 2);
            const textW = ctx.measureText(text).width;
            const above = { x: bx, y: by - labelH, w: textW + padding * 2, h: labelH };
            const inside = { x: bx, y: by, w: above.w, h: labelH };
            const r = !overlaps(above) && above.y >= 0 ? above : (!overlaps(inside) ? inside : null);
            if (!r) return;
            placed.push(r);
            ctx.fillStyle = t.color || '#788692';
            ctx.globalAlpha = 0.85;
            ctx.fillRect(r.x, r.y, r.w, r.h);
            ctx.globalAlpha = 1;
            ctx.fillStyle = '#0b1220';
            ctx.fillText(text, r.x + padding, r.y + labelH - 5);
        }
    });

    const hud = result.hud || {};
    ctx.font = '13px "Segoe UI", sans-serif';
    ctx.textBaseline = 'top';
    // Nền tối sau chữ: camera tự in OSD (giờ, tên) ở góc trên, chữ HUD đè lên
    // nền sáng/OSD sẽ không đọc được
    const label = (text, x, y, color, align = 'left') => {
        const tw = ctx.measureText(text).width;
        const left = align === 'right' ? x - tw : x;
        ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
        ctx.fillRect(left - 4, y - 2, tw + 8, 18);
        ctx.fillStyle = color;
        ctx.fillText(text, left, y);
        return tw;
    };
    const nameW = hud.camera_name ? label(hud.camera_name, 10, 8, HUD_COLORS.text) : 0;
    if (hud.time_str) label(hud.time_str, 10, 28, HUD_COLORS.text);
    if (hud.roll_line) label(hud.roll_line, 10, 48, HUD_COLORS.roll);
    if (hud.stat_line) {
        // Khung nhỏ không đủ chỗ cho cả tên camera và dòng thống kê trên một
        // hàng thì đưa dòng thống kê xuống đáy để không chồng chữ
        const statW = ctx.measureText(hud.stat_line).width;
        if (nameW + statW + 40 <= w) label(hud.stat_line, w - 10, 8, HUD_COLORS.stat, 'right');
        else label(hud.stat_line, 10, h - 24, HUD_COLORS.stat);
    }
}

// Ghép hình với kết quả AI: AI chạy chậm hơn video nên phải chọn bản ``ts``
// (mốc chụp, epoch ms) gần nhất với "giờ đang hiển thị" ước lượng, không
// phải cứ lấy bản mới nhất — làm đơn giản theo đúng yêu cầu: trễ không đổi
// ước lượng bằng (buffered.end - currentTime) của <video>, rồi chọn kết quả
// gần ``now - trễ`` nhất trong một bộ đệm ngắn.
function pickResult(state) {
    const buf = state.resultBuffer;
    if (!buf.length) return null;
    const videoEl = state.video && state.video.video;
    let latencyMs = 150; // ước lượng mặc định cho WebRTC (gần như trực tiếp)
    try {
        if (videoEl && videoEl.buffered && videoEl.buffered.length) {
            const end = videoEl.buffered.end(videoEl.buffered.length - 1);
            latencyMs = Math.max(0, (end - videoEl.currentTime) * 1000);
        }
    } catch (e) { /* MSE chưa sẵn sàng */ }

    const targetTs = Date.now() - latencyMs;
    let best = buf[buf.length - 1];
    let bestDiff = Math.abs(best.ts - targetTs);
    for (let i = buf.length - 2; i >= 0; i--) {
        const diff = Math.abs(buf[i].ts - targetTs);
        if (diff < bestDiff) { best = buf[i]; bestDiff = diff; }
    }
    // Lệch quá 2s thì dữ liệu cũ, không gắn nhầm vào khung đang chiếu
    return bestDiff <= 2000 ? best : buf[buf.length - 1];
}

function startRenderLoop(state) {
    const render = () => {
        if (!_state.has(state.imgEl)) return; // đã detach
        const videoEl = state.video && state.video.video;
        if (state.overlayEnabled && videoEl && state.canvas.isConnected) {
            const w = state.canvas.clientWidth || 1, h = state.canvas.clientHeight || 1;
            if (state.canvas.width !== w) state.canvas.width = w;
            if (state.canvas.height !== h) state.canvas.height = h;
            drawDetections(state.ctx, w, h, pickResult(state), state.zonesMeta);
            state.canvas.style.display = '';
        } else if (state.canvas) {
            state.canvas.style.display = 'none';
        }
        if (videoEl && videoEl.requestVideoFrameCallback) {
            state.rvfcHandle = videoEl.requestVideoFrameCallback(() => { state.lastProgressAt = Date.now(); render(); });
        } else {
            if (videoEl && !videoEl.paused) state.lastProgressAt = Date.now();
            state.rafId = requestAnimationFrame(render);
        }
    };
    render();
}

function startDetections(state) {
    if (state.es) return;
    const es = new EventSource(`/api/v1/cameras/${state.cameraId}/detections`);
    es.onmessage = (msg) => {
        try {
            const payload = JSON.parse(msg.data);
            state.resultBuffer.push(payload);
            if (state.resultBuffer.length > 12) state.resultBuffer.shift();
        } catch (e) { /* bản tin hỏng, bỏ qua */ }
    };
    es.onerror = () => { /* EventSource tự nối lại */ };
    state.es = es;
}

function startStatusWatch(state) {
    state.statusTimer = setInterval(() => {
        const sinceMs = Date.now() - (state.lastProgressAt || 0);
        if (sinceMs > 8000) setBadge(state, 'lost');
        else if (sinceMs > 2500) setBadge(state, 'stale');
        else setBadge(state, 'live');
    }, 1000);

    // go2rtc không gọi được / camera không có nguồn hợp lệ: chưa từng thấy một
    // khung nào sau một khoảng hợp lý thì rơi về MJPEG, thay vì treo mãi.
    state.watchdog = setTimeout(() => {
        if (!state.lastProgressAt) {
            console.warn(`[player] go2rtc không lên hình cho ${state.cameraId}, rơi về MJPEG`);
            const { imgEl, cameraId, overlayEnabled } = state;
            detachPlayer(imgEl);
            attachMjpeg(imgEl, cameraId, overlayEnabled);
        }
    }, 10000);
}

async function fetchZonesMeta(cameraId) {
    try {
        const res = await fetch(`/api/v1/cameras/${cameraId}/zones`);
        if (!res.ok) return [];
        return await res.json();
    } catch (e) {
        return [];
    }
}

async function startGo2rtc(imgEl, cameraId, overlayEnabled, variant, cfg) {
    const container = imgEl.parentElement;
    if (!container) return attachMjpeg(imgEl, cameraId, overlayEnabled);

    imgEl.style.display = 'none';

    const video = document.createElement('video-stream');
    video.className = 'ai-player-stream';
    video.mode = 'webrtc,mse';
    video.background = true;
    if (cfg.ice_servers && cfg.ice_servers.length) {
        video.pcConfig = {
            bundlePolicy: 'max-bundle',
            iceServers: [{ urls: ['stun:stun.cloudflare.com:3478'] }, ...cfg.ice_servers],
            sdpSemantics: 'unified-plan',
        };
    }

    const canvas = document.createElement('canvas');
    canvas.className = 'ai-overlay-canvas';
    const badge = buildBadge();

    container.insertBefore(video, container.firstChild);
    container.insertBefore(canvas, video.nextSibling);
    container.insertBefore(badge, canvas.nextSibling);

    const state = {
        mode: 'go2rtc', imgEl, cameraId, variant, overlayEnabled,
        video, canvas, badge, ctx: canvas.getContext('2d'),
        resultBuffer: [], zonesMeta: [], lastProgressAt: 0,
    };
    _state.set(imgEl, state);

    // Zone polygon dùng để vẽ, lấy một lần — vùng hiếm khi đổi khi đang xem.
    const zonesMeta = await fetchZonesMeta(cameraId);
    // Có thể đã bị attachPlayer/detachPlayer gọi lại (đổi camera) trong lúc
    // chờ fetch ở trên — bỏ nếu state này không còn là bản hiện hành.
    if (_state.get(imgEl) !== state) return;
    state.zonesMeta = zonesMeta;

    const name = streamName(cameraId, variant);
    video.src = `${cfg.go2rtc_proxy_base}/api/ws?src=${encodeURIComponent(name)}`;

    startDetections(state);
    startRenderLoop(state);
    startStatusWatch(state);
}

// ---------------------------------------------------------------- API công khai

export function setOverlayEnabled(imgEl, enabled) {
    const state = _state.get(imgEl);
    if (!state) return;
    state.overlayEnabled = enabled;
    if (state.mode === 'mjpeg') {
        attachMjpeg(imgEl, state.cameraId, enabled);
    }
}

/**
 * Gắn trình phát vào ``imgEl`` (giữ nguyên để tương thích: các màn hiện có chỉ
 * có sẵn một <img>, player chèn <video-stream>+<canvas> làm anh em trước nó).
 * ``variant``: 'sub' cho ô lưới (ưu tiên luồng phụ), 'main' cho màn chi tiết.
 */
export function attachPlayer(imgEl, cameraId, overlayEnabled, variant = 'main') {
    if (!imgEl) return;
    const existing = _state.get(imgEl);
    // Cùng camera+variant: chỉ đổi cờ lớp phủ, không gắn lại từ đầu (tránh
    // mở lại kết nối WebRTC/MSE mỗi lần bấm nút "Bật/Tắt lớp phủ AI" hay mỗi
    // lần loadCameraWall() chạy lại — cả hai gọi attachStream() vô điều kiện).
    if (existing && existing.cameraId === cameraId && existing.variant === variant) {
        setOverlayEnabled(imgEl, overlayEnabled);
        return;
    }

    detachPlayer(imgEl);
    // Đánh dấu "đang gắn" NGAY (đồng bộ) trước khi chờ fetch config/probe —
    // nếu loadCameraWall() gọi lại lần nữa trong lúc đang chờ, nhánh ở trên
    // sẽ nhận ra cameraId/variant khớp và bỏ qua, không mở hai kết nối chồng.
    _state.set(imgEl, { mode: 'pending', imgEl, cameraId, variant, overlayEnabled });

    const stillPending = () => {
        const cur = _state.get(imgEl);
        return !!cur && cur.mode === 'pending' && cur.cameraId === cameraId && cur.variant === variant;
    };

    systemConfig().then(async (cfg) => {
        if (!stillPending()) return; // có lần gọi mới hơn đã thay chỗ

        if (!cfg.go2rtc_enabled) return attachMjpeg(imgEl, cameraId, overlayEnabled);

        const reachable = await probeGo2rtc(cfg.go2rtc_proxy_base);
        if (!stillPending()) return;
        if (!reachable) return attachMjpeg(imgEl, cameraId, overlayEnabled);

        startGo2rtc(imgEl, cameraId, overlayEnabled, variant, cfg);
    });
}

window.AiPlayer = { attachPlayer, detachPlayer, setOverlayEnabled };
