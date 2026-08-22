// HORUS AI WEB - MILITARY EDITION FRONTEND CONTROLLER

let ws = null;
let currentCount = 0;
let baselineCount = 45;
let isProcessing = false;
let currentInputMode = 'video';
let currentMediaMode = 'webcam';
let webcamStream = null;
let capturedSnapshotBase64 = null;
let registeredPersonnel = [];
let isAiOverlayEnabled = true;
let isSirenMuted = false;
let currentStreamType = 'optical';
let pendingEventsCount = 2;

// DOM Elements
const currentPageTitle = document.getElementById('current-page-title');
const liveTimeEl = document.getElementById('live-time');
const liveDateEl = document.getElementById('live-date');
const topbarAttendanceStat = document.getElementById('topbar-attendance-stat');
const topbarAlertStat = document.getElementById('topbar-alert-stat');
const pendingEventsBadge = document.getElementById('pending-events-badge');

// Surveillance DOM Elements
const videoCanvas = document.getElementById('video-canvas');
const loadingOverlay = document.getElementById('loading-overlay');
const overlayStatusText = document.getElementById('overlay-status-text');
const overlayStartBtn = document.getElementById('overlay-start-btn');
const btnLockBaseline = document.getElementById('btn-lock-baseline');
const aiOverlayBtnText = document.getElementById('ai-overlay-btn-text');
const eventsListContainer = document.getElementById('events-list-container');
const sourceModal = document.getElementById('source-modal');
const alertBanner = document.getElementById('alert-banner');
const alertMessage = document.getElementById('alert-message');

// Face Reg Elements
const faceRegForm = document.getElementById('face-reg-form');
const regNameInput = document.getElementById('reg-name');
const regMilitaryIdInput = document.getElementById('reg-military-id');
const regRankSelect = document.getElementById('reg-rank');
const regUnitSelect = document.getElementById('reg-unit');
const regStatusMsg = document.getElementById('reg-status-msg');
const btnSaveFace = document.getElementById('btn-save-face');

const webcamVideo = document.getElementById('webcam-video');
const captureCanvas = document.getElementById('capture-canvas');
const guideStatusText = document.getElementById('guide-status-text');
const photoUploadInput = document.getElementById('photo-upload-input');
const photoPreviewImg = document.getElementById('photo-preview-img');
const uploadPlaceholderContent = document.getElementById('upload-placeholder-content');

const personnelTbody = document.getElementById('personnel-tbody');
const registeredCountBadge = document.getElementById('registered-count-badge');
const searchPersonnelInput = document.getElementById('search-personnel');
const filterUnitSelect = document.getElementById('filter-unit');
const filterRankSelect = document.getElementById('filter-rank');
const tableEmptyMsg = document.getElementById('table-empty-msg');


// ----------------- LIVE CLOCK (bám theo giờ máy chủ) -----------------
// Máy chủ có thể chạy múi giờ khác máy của người dùng. Đồng hồ trên thanh tiêu đề
// phải trùng với dấu thời gian in trên khung hình camera nên lấy chênh lệch so với
// giờ máy chủ rồi hiển thị theo giờ đó.
let serverClockOffsetMs = 0;

function serverNow() {
    return new Date(Date.now() + serverClockOffsetMs);
}
window.serverNow = serverNow;

function syncServerClock(isoString) {
    if (!isoString) return;
    const parsed = new Date(isoString).getTime();
    if (Number.isNaN(parsed)) return;
    serverClockOffsetMs = parsed - Date.now();

    const warnEl = document.getElementById('clock-drift-warning');
    if (warnEl) {
        const driftSec = Math.round(Math.abs(serverClockOffsetMs) / 1000);
        if (driftSec >= 60) {
            const driftMin = Math.round(driftSec / 60);
            warnEl.textContent = `⚠ Máy trạm lệch ${driftMin} phút so với giờ hệ thống`;
            warnEl.style.display = 'block';
        } else {
            warnEl.style.display = 'none';
        }
    }
    updateLiveClock();
}

async function fetchServerClock() {
    try {
        const res = await fetch('/api/time');
        const data = await res.json();
        syncServerClock(data.server_time);
    } catch (e) {
        console.error('Không lấy được giờ máy chủ:', e);
    }
}

function updateLiveClock() {
    const now = serverNow();
    const pad = (n) => String(n).padStart(2, '0');
    if (liveTimeEl) {
        liveTimeEl.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    }
    if (liveDateEl) {
        liveDateEl.textContent = `${pad(now.getDate())}/${pad(now.getMonth() + 1)}/${now.getFullYear()}`;
    }
}
setInterval(updateLiveClock, 1000);
updateLiveClock();
fetchServerClock();
// Đồng bộ lại định kỳ phòng khi máy trạm bị trôi giờ
setInterval(fetchServerClock, 5 * 60 * 1000);


// ----------------- NAVIGATION TABS -----------------
let scheduleRefreshTimer = null;

function switchNavTab(tabName) {
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => item.classList.remove('active'));
    const activeNav = document.getElementById(`nav-${tabName}`);
    if (activeNav) activeNav.classList.add('active');

    document.querySelectorAll('.page-view').forEach(view => view.classList.remove('active'));
    const targetView = document.getElementById(`view-${tabName}`);
    if (targetView) targetView.classList.add('active');

    const titles = {
        'monitoring': 'Giám sát trực tiếp',
        'zones': 'Quản lý Vùng & Luật (F-06)',
        'registration': 'Đăng ký Khuôn mặt',
        'schedule': 'Cấu hình Thời khóa biểu',
        'logs': 'Nhật ký & Điểm danh'
    };
    if (currentPageTitle) currentPageTitle.textContent = titles[tabName] || 'Hệ thống Horus AI';

    if (tabName === 'registration') {
        if (currentMediaMode === 'webcam') startWebcam();
        loadRegisteredFaces();
    } else {
        stopWebcam();
    }

    if (tabName === 'zones') {
        setTimeout(() => {
            initRoiCanvas();
            loadZoneRules();
            captureFrameForRoi();
        }, 50);
    }

    // Trạng thái ca đổi theo giờ thực nên phải làm mới định kỳ khi đang xem bảng
    if (scheduleRefreshTimer) {
        clearInterval(scheduleRefreshTimer);
        scheduleRefreshTimer = null;
    }
    if (tabName === 'schedule') {
        loadSchedules();
        scheduleRefreshTimer = setInterval(loadSchedules, 30000);
    }

    if (tabName === 'logs') {
        loadAttendanceLogs();
    }
}
window.switchNavTab = switchNavTab;


// ----------------- ALERT COUNTER LOGIC -----------------
let totalAlertsCount = 0;

function incrementAlertCount() {
    totalAlertsCount++;
    if (topbarAlertStat) topbarAlertStat.textContent = `Cảnh báo: ${totalAlertsCount}`;
    const notiBadge = document.getElementById('header-noti-badge');
    if (notiBadge) notiBadge.textContent = totalAlertsCount;
}


// ----------------- 10S CLIP REPLAY PLAYER -----------------
let clipFrames = [];
let clipCurrentIdx = 0;
let isClipPlaying = false;
let clipPlayTimer = null;

const clipModal = document.getElementById('clip-modal');
const clipPlayerCanvas = document.getElementById('clip-player-canvas');
const clipTimelineSlider = document.getElementById('clip-timeline-slider');
const clipTimerLabel = document.getElementById('clip-timer-label');
const btnClipPlayToggle = document.getElementById('btn-clip-play-toggle');

async function viewEventClip(clipId) {
    if (!clipModal) return;
    clipModal.style.display = 'flex';
    
    try {
        const res = await fetch(`/api/events/clip?clip_id=${encodeURIComponent(clipId || '')}`);
        const data = await res.json();
        
        if (data.status === 'success' && data.frames && data.frames.length > 0) {
            clipFrames = data.frames;
        } else {
            // Fallback: try snapshot
            const snapRes = await fetch('/api/snapshot');
            const snapData = await snapRes.json();
            if (snapData.status === 'success' && snapData.frame) {
                clipFrames = [snapData.frame];
            } else {
                clipFrames = [];
            }
        }
    } catch (e) {
        console.error('Error loading clip replay:', e);
        clipFrames = [];
    }

    clipCurrentIdx = 0;
    if (clipTimelineSlider) {
        clipTimelineSlider.max = Math.max(1, clipFrames.length - 1);
        clipTimelineSlider.value = 0;
    }
    isClipPlaying = true;
    if (btnClipPlayToggle) btnClipPlayToggle.textContent = '⏸ Tạm dừng';

    if (clipFrames.length > 0) {
        renderClipFrame(0);
    }
    startClipPlaybackLoop();
}
window.viewEventClip = viewEventClip;

function startClipPlaybackLoop() {
    if (clipPlayTimer) clearInterval(clipPlayTimer);

    clipPlayTimer = setInterval(() => {
        if (!isClipPlaying || clipFrames.length === 0) return;

        renderClipFrame(clipCurrentIdx);
        clipCurrentIdx = (clipCurrentIdx + 1) % clipFrames.length;
        if (clipTimelineSlider) clipTimelineSlider.value = clipCurrentIdx;
    }, 200); // 5 FPS
}

function renderClipFrame(idx) {
    if (!clipPlayerCanvas || idx >= clipFrames.length) return;

    const frameB64 = clipFrames[idx];
    const img = new Image();
    img.onload = () => {
        clipPlayerCanvas.width = img.width;
        clipPlayerCanvas.height = img.height;
        const ctx = clipPlayerCanvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
    };
    img.src = 'data:image/jpeg;base64,' + frameB64;

    const currentSec = (idx / 5).toFixed(1);
    const totalSec = (clipFrames.length / 5).toFixed(1);
    if (clipTimerLabel) clipTimerLabel.textContent = `00:${String(Math.floor(currentSec)).padStart(2, '0')} / 00:${String(Math.floor(totalSec)).padStart(2, '0')}`;
}

function toggleClipPlayback() {
    isClipPlaying = !isClipPlaying;
    if (btnClipPlayToggle) {
        btnClipPlayToggle.textContent = isClipPlaying ? '⏸ Tạm dừng' : '▶ Tiếp tục';
    }
}
window.toggleClipPlayback = toggleClipPlayback;

function seekClipFrame(val) {
    clipCurrentIdx = parseInt(val);
    renderClipFrame(clipCurrentIdx);
}
window.seekClipFrame = seekClipFrame;

function closeClipModal() {
    if (clipPlayTimer) clearInterval(clipPlayTimer);
    isClipPlaying = false;
    if (clipModal) clipModal.style.display = 'none';
}
window.closeClipModal = closeClipModal;


// ----------------- SURVEILLANCE & STREAM CONTROLS -----------------
function toggleSourceModal() {
    if (!sourceModal) return;
    sourceModal.style.display = sourceModal.style.display === 'none' ? 'flex' : 'none';
}
window.toggleSourceModal = toggleSourceModal;

function toggleMuteSiren(isMuted) {
    isSirenMuted = isMuted;
    console.log("Mute siren state:", isSirenMuted);
}
window.toggleMuteSiren = toggleMuteSiren;

function toggleAiOverlay() {
    isAiOverlayEnabled = !isAiOverlayEnabled;
    if (aiOverlayBtnText) {
        aiOverlayBtnText.textContent = isAiOverlayEnabled ? 'Tắt lớp phủ AI' : 'Bật lớp phủ AI';
    }
}
window.toggleAiOverlay = toggleAiOverlay;

function switchStreamType(type) {
    currentStreamType = type;
    const btnOptical = document.getElementById('btn-optical-stream');
    const btnThermal = document.getElementById('btn-thermal-stream');

    if (type === 'optical') {
        btnOptical.classList.add('active');
        btnThermal.classList.remove('active');
    } else {
        btnOptical.classList.remove('active');
        btnThermal.classList.add('active');
    }
}
window.switchStreamType = switchStreamType;

async function lockBaselineManual() {
    const targetCount = currentCount > 0 ? currentCount : 45;
    try {
        const res = await fetch(`/api/set-baseline?count=${targetCount}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            baselineCount = data.baseline;
            if (topbarAttendanceStat) topbarAttendanceStat.textContent = `Quân số: ${currentCount || baselineCount}/${baselineCount}`;

            // Add SYSTEM Event Card to Event Feed
            addEventFeedCard({
                category: 'SYSTEM',
                time: new Date().toLocaleTimeString(),
                desc: `Baseline điểm danh đã được khoá thủ công: ${data.baseline} quân nhân.`,
                location: 'CAM-01 SÂN TẬP TRUNG',
                isProcessed: true
            });
            alert(`✓ Đã chốt sĩ số chuẩn: ${data.baseline} quân nhân`);
        }
    } catch (e) {
        alert('Lỗi khi chốt sĩ số: ' + e.message);
    }
}
window.lockBaselineManual = lockBaselineManual;

async function startAttendanceNow() {
    try {
        const res = await fetch('/api/attendance/start', { method: 'POST' });
        const data = await res.json();
        if (data.status !== 'success') {
            alert(data.message || 'Không mở được phiên điểm danh');
            return;
        }
        addEventFeedCard({
            category: 'SYSTEM',
            time: new Date().toLocaleTimeString(),
            desc: data.message,
            location: 'CAM-01 SÂN TẬP TRUNG',
            isProcessed: true
        });
    } catch (e) {
        alert('Lỗi mở phiên điểm danh: ' + e.message);
    }
}
window.startAttendanceNow = startAttendanceNow;

function takeQuickSnapshot() {
    if (!videoCanvas) return;
    const link = document.createElement('a');
    link.download = `snapshot_CAM-01_${Date.now()}.jpg`;
    link.href = videoCanvas.toDataURL('image/jpeg');
    link.click();
}
window.takeQuickSnapshot = takeQuickSnapshot;

function confirmEventResolution(eventId) {
    const card = document.getElementById(eventId);
    if (!card) return;

    const actionContainer = card.querySelector('.event-card-actions');
    if (actionContainer) {
        actionContainer.innerHTML = `
            <button class="btn-event-clip" onclick="viewEventClip('clip')">▶️ Xem clip 10s</button>
            <button class="btn-event-processed" disabled>✓ Đã xử lý</button>
        `;
    }

    pendingEventsCount = Math.max(0, pendingEventsCount - 1);
    if (pendingEventsBadge) {
        pendingEventsBadge.textContent = `${pendingEventsCount} chờ xử lý`;
    }
}
window.confirmEventResolution = confirmEventResolution;

function addEventFeedCard(eventData) {
    if (!eventsListContainer) return;

    const cardId = `event-${Date.now()}`;
    const card = document.createElement('div');
    const catLower = (eventData.category || 'SYSTEM').toLowerCase();
    card.className = `event-card event-${catLower}`;
    card.id = cardId;

    const catClass = `cat-${catLower}`;
    const actionBtn = eventData.isProcessed
        ? `<button class="btn-event-processed" disabled>✓ Đã xử lý</button>`
        : `<button class="btn-event-confirm" onclick="confirmEventResolution('${cardId}')">Xác nhận xử lý</button>`;

    card.innerHTML = `
        <div class="event-card-header">
            <span class="event-category ${catClass}">${eventData.category}</span>
            <span class="event-time">${eventData.time || new Date().toLocaleTimeString()}</span>
        </div>
        <p class="event-desc">${eventData.desc || eventData.message}</p>
        <div class="event-location">${eventData.location || 'CAM-01 SÂN TẬP TRUNG'}</div>
        <div class="event-card-actions">
            <button class="btn-event-clip" onclick="viewEventClip('${cardId}')">▶️ Xem clip 10s</button>
            ${actionBtn}
        </div>
    `;

    eventsListContainer.insertBefore(card, eventsListContainer.firstChild);

    if (!eventData.isProcessed) {
        pendingEventsCount++;
        if (pendingEventsBadge) pendingEventsBadge.textContent = `${pendingEventsCount} chờ xử lý`;
    }
}


// ----------------- VIDEO STREAM & WEBSOCKET -----------------
function switchMode(mode) {
    currentInputMode = mode;
    const modeVideoBtn = document.getElementById('mode-video-btn');
    const modeRtspBtn = document.getElementById('mode-rtsp-btn');
    const videoSourcePanel = document.getElementById('video-source-panel');
    const rtspSourcePanel = document.getElementById('rtsp-source-panel');

    if (mode === 'video') {
        videoSourcePanel.style.display = 'block';
        rtspSourcePanel.style.display = 'none';
        modeVideoBtn.classList.add('active');
        modeRtspBtn.classList.remove('active');
    } else {
        videoSourcePanel.style.display = 'none';
        rtspSourcePanel.style.display = 'block';
        modeVideoBtn.classList.remove('active');
        modeRtspBtn.classList.add('active');
    }
}
window.switchMode = switchMode;

function setRtspDemo(event) {
    event.preventDefault();
    const rtspUrlInput = document.getElementById('rtsp-url');
    if (rtspUrlInput) rtspUrlInput.value = 'rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mov';
}
window.setRtspDemo = setRtspDemo;

async function quickStartStream() {
    if (overlayStatusText) overlayStatusText.textContent = 'Đang khởi chạy luồng giám sát CAM-01...';
    try {
        const res = await fetch('/api/start?mode=video', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            connectWebSocket();
        } else {
            // If no video uploaded, show source modal
            toggleSourceModal();
        }
    } catch (e) {
        toggleSourceModal();
    }
}
window.quickStartStream = quickStartStream;

async function startRtspStream() {
    const rtspUrlInput = document.getElementById('rtsp-url');
    const rtspUrl = rtspUrlInput ? rtspUrlInput.value.trim() : '';
    if (!rtspUrl) {
        alert('Vui lòng nhập địa chỉ RTSP Stream');
        return;
    }

    try {
        const res = await fetch(`/api/start?mode=rtsp&rtsp_url=${encodeURIComponent(rtspUrl)}`, { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            toggleSourceModal();
            connectWebSocket();
        } else {
            alert('Lỗi: ' + data.message);
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
    }
}
window.startRtspStream = startRtspStream;

const uploadForm = document.getElementById('upload-form');
if (uploadForm) {
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const videoFileInput = document.getElementById('video-file');
        const uploadStatus = document.getElementById('upload-status');
        const file = videoFileInput.files[0];
        if (!file) {
            uploadStatus.textContent = 'Vui lòng chọn tệp video';
            uploadStatus.style.color = '#ef4444';
            return;
        }

        const CHUNK_SIZE = 1 * 1024 * 1024;
        const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
        const uploadId = Date.now().toString() + '-' + Math.random().toString(36).substr(2, 9);

        uploadStatus.textContent = 'Đang tải tệp video lên...';
        uploadStatus.style.color = '#0284c7';

        try {
            for (let i = 0; i < totalChunks; i++) {
                const start = i * CHUNK_SIZE;
                const end = Math.min(start + CHUNK_SIZE, file.size);
                const chunk = file.slice(start, end);

                const formData = new FormData();
                formData.append('file', chunk);
                formData.append('chunk_index', i);
                formData.append('total_chunks', totalChunks);
                formData.append('upload_id', uploadId);
                formData.append('filename', file.name);

                const percent = Math.round(((i) / totalChunks) * 100);
                uploadStatus.textContent = `Tải lên ${i + 1}/${totalChunks} (${percent}%)`;

                const response = await fetch('/api/upload_chunk', { method: 'POST', body: formData });
                if (!response.ok) throw new Error(`Lỗi tải lên chunk ${i}`);

                const data = await response.json();
                if (i === totalChunks - 1 && data.status === 'success') {
                    uploadStatus.textContent = `✓ Đã tải xong video`;
                    uploadStatus.style.color = '#0a8f4c';
                    toggleSourceModal();
                    connectWebSocket();
                }
            }
        } catch (err) {
            console.error(err);
            uploadStatus.textContent = `✗ ${err.message}`;
            uploadStatus.style.color = '#ef4444';
        }
    });
}

function connectWebSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) return;

    isProcessing = true;
    if (loadingOverlay) loadingOverlay.style.display = 'flex';
    if (overlayStatusText) overlayStatusText.textContent = 'Đang kết nối luồng giám sát CAM-01...';
    if (overlayStartBtn) overlayStartBtn.style.display = 'none';

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        if (loadingOverlay) loadingOverlay.style.display = 'none';
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        if (loadingOverlay) loadingOverlay.style.display = 'flex';
        if (overlayStatusText) overlayStatusText.textContent = 'Mất kết nối tới CAM-01';
        if (overlayStartBtn) overlayStartBtn.style.display = 'block';
        isProcessing = false;
    };

    ws.onclose = () => {
        if (loadingOverlay) loadingOverlay.style.display = 'flex';
        if (overlayStatusText) overlayStatusText.textContent = 'Luồng giám sát CAM-01 đã tạm dừng';
        if (overlayStartBtn) overlayStartBtn.style.display = 'block';
        isProcessing = false;
    };
}

function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'frame_update':
            updateFrame(data);
            break;
        case 'alert':
            handleStreamAlert(data);
            break;
        case 'attendance_complete':
            handleAttendanceComplete(data.log);
            break;
        case 'system_event':
            addEventFeedCard({
                category: 'SYSTEM',
                time: new Date(data.timestamp).toLocaleTimeString(),
                desc: data.message,
                location: 'CAM-01 SÂN TẬP TRUNG',
                isProcessed: true
            });
            break;
        case 'processing_complete':
            if (loadingOverlay) loadingOverlay.style.display = 'flex';
            if (overlayStatusText) overlayStatusText.textContent = 'Đã hoàn tất ca giám sát';
            if (overlayStartBtn) overlayStartBtn.style.display = 'block';
            if (ws) { ws.close(); ws = null; }
            break;
    }
}

function updateFrame(data) {
    if (!videoCanvas) return;

    const img = new Image();
    img.onload = () => {
        videoCanvas.width = img.width;
        videoCanvas.height = img.height;
        const ctx = videoCanvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
    };
    img.src = 'data:image/jpeg;base64,' + data.frame;

    if (data.server_time) syncServerClock(data.server_time);

    currentCount = data.count;
    if (typeof data.baseline === 'number') baselineCount = data.baseline;

    if (topbarAttendanceStat) {
        const unknown = data.unidentified_count || 0;
        const suffix = unknown > 0 ? ` (${unknown} chưa định danh)` : '';
        topbarAttendanceStat.textContent = `Quân số: ${currentCount}/${baselineCount}${suffix}`;
    }

    const attBtn = document.getElementById('attendance-btn-text');
    if (attBtn) {
        const att = data.attendance;
        if (att && att.active) {
            const remain = att.remaining_seconds || 0;
            const mm = String(Math.floor(remain / 60)).padStart(2, '0');
            const ss = String(remain % 60).padStart(2, '0');
            const phaseLabel = att.phase_label || 'Điểm danh';
            const total = att.required || att.roster_size || 0;
            attBtn.textContent = `${phaseLabel} ${mm}:${ss} · ${att.present}/${total}`;
        } else {
            attBtn.textContent = 'Điểm danh ngay';
        }
    }
}

function handleAttendanceComplete(log) {
    if (!log) return;

    const desc = log.absent > 0
        ? `Chốt điểm danh ${log.unit}: có mặt ${log.present}/${log.required}, vắng ${log.absent} — ${log.absent_personnel.join(', ')}`
        : `Chốt điểm danh ${log.unit}: đủ quân số ${log.present}/${log.required}`;

    addEventFeedCard({
        category: log.absent > 0 ? 'ABSENT' : 'SYSTEM',
        time: `${log.date} ${log.time}`,
        desc: desc,
        location: 'CAM-01 SÂN TẬP TRUNG',
        isProcessed: log.absent === 0
    });

    loadAttendanceLogs();
}

function handleStreamAlert(data) {
    incrementAlertCount();

    if (!isSirenMuted && alertBanner && alertMessage) {
        alertMessage.textContent = `⚠️ ${data.message} lúc ${new Date(data.timestamp).toLocaleTimeString()}`;
        alertBanner.style.display = 'flex';
        setTimeout(() => { alertBanner.style.display = 'none'; }, 10000);
    }

    addEventFeedCard({
        category: data.category || 'ABSENT',
        time: new Date(data.timestamp).toLocaleTimeString(),
        desc: data.message,
        location: 'CAM-01 SÂN TẬP TRUNG',
        isProcessed: false
    });
}


// ----------------- WEBCAM & FACE REGISTRATION -----------------
function switchMediaMode(mode) {
    currentMediaMode = mode;
    const tabWebcam = document.getElementById('tab-webcam');
    const tabUpload = document.getElementById('tab-upload');
    const viewportWebcam = document.getElementById('viewport-webcam');
    const viewportUpload = document.getElementById('viewport-upload');
    const webcamActions = document.getElementById('webcam-actions');

    if (mode === 'webcam') {
        tabWebcam.classList.add('active');
        tabUpload.classList.remove('active');
        viewportWebcam.style.display = 'flex';
        viewportUpload.style.display = 'none';
        webcamActions.style.display = 'block';
        startWebcam();
    } else {
        tabWebcam.classList.remove('active');
        tabUpload.classList.add('active');
        viewportWebcam.style.display = 'none';
        viewportUpload.style.display = 'flex';
        webcamActions.style.display = 'none';
        stopWebcam();
    }
}
window.switchMediaMode = switchMediaMode;

async function startWebcam() {
    if (webcamStream) return;
    try {
        if (guideStatusText) guideStatusText.textContent = 'ĐANG KẾT NỐI WEBCAM...';
        webcamStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
            audio: false
        });
        if (webcamVideo) {
            webcamVideo.srcObject = webcamStream;
            if (guideStatusText) guideStatusText.textContent = 'CĂN CHỈNH KHUÔN MẶT VÀO VÒNG TRÒN';
        }
    } catch (err) {
        console.warn('Webcam error:', err);
        if (guideStatusText) guideStatusText.textContent = 'KHÔNG THỂ MỞ WEBCAM (HÃY TẢI ẢNH)';
    }
}

function stopWebcam() {
    if (webcamStream) {
        webcamStream.getTracks().forEach(track => track.stop());
        webcamStream = null;
    }
}

function captureWebcamSnapshot() {
    if (!webcamVideo || !webcamStream) {
        alert('Vui lòng bật webcam trước khi chụp.');
        return;
    }

    captureCanvas.width = webcamVideo.videoWidth || 640;
    captureCanvas.height = webcamVideo.videoHeight || 480;
    const ctx = captureCanvas.getContext('2d');
    ctx.drawImage(webcamVideo, 0, 0, captureCanvas.width, captureCanvas.height);

    capturedSnapshotBase64 = captureCanvas.toDataURL('image/jpeg', 0.9);
    if (guideStatusText) {
        guideStatusText.textContent = '✓ ĐÃ CHỤP KHUÔN MẶT!';
        guideStatusText.style.color = '#10b981';
    }
    if (regStatusMsg) {
        regStatusMsg.textContent = '✓ Ảnh chụp webcam đã sẵn sàng để đăng ký';
        regStatusMsg.style.color = '#0a8f4c';
    }
}
window.captureWebcamSnapshot = captureWebcamSnapshot;

function previewUploadedPhoto(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
        photoPreviewImg.src = e.target.result;
        photoPreviewImg.style.display = 'block';
        uploadPlaceholderContent.style.display = 'none';
        capturedSnapshotBase64 = e.target.result;
        if (regStatusMsg) {
            regStatusMsg.textContent = `✓ Đã chọn ảnh: ${file.name}`;
            regStatusMsg.style.color = '#0a8f4c';
        }
    };
    reader.readAsDataURL(file);
}
window.previewUploadedPhoto = previewUploadedPhoto;

async function handleFaceRegister(e) {
    e.preventDefault();

    const name = regNameInput.value.trim();
    const militaryId = regMilitaryIdInput.value.trim();
    const rank = regRankSelect.value;
    const unit = regUnitSelect.value;

    if (!name || !militaryId || !rank || !unit) {
        alert('Vui lòng điền đầy đủ các trường thông tin');
        return;
    }

    const formData = new FormData();
    formData.append('name', name);
    formData.append('military_id', militaryId);
    formData.append('rank', rank);
    formData.append('unit', unit);
    formData.append('status', 'Active');

    if (currentMediaMode === 'webcam') {
        if (!capturedSnapshotBase64) captureWebcamSnapshot();
        if (!capturedSnapshotBase64) {
            alert('Vui lòng chụp ảnh khuôn mặt từ webcam trước khi lưu.');
            return;
        }
        formData.append('image_base64', capturedSnapshotBase64);
    } else {
        const file = photoUploadInput.files[0];
        if (!file && !capturedSnapshotBase64) {
            alert('Vui lòng tải lên ảnh chân dung rõ nét');
            return;
        }
        if (file) {
            formData.append('image', file);
        } else {
            formData.append('image_base64', capturedSnapshotBase64);
        }
    }

    btnSaveFace.disabled = true;
    regStatusMsg.textContent = 'Đang trích xuất Face ID & lưu trữ sinh trắc học...';
    regStatusMsg.style.color = '#0284c7';

    try {
        const res = await fetch('/api/faces/register', {
            method: 'POST',
            body: formData
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || data.message || 'Lỗi khi đăng ký khuôn mặt');
        }

        regStatusMsg.textContent = `✓ ${data.message}`;
        regStatusMsg.style.color = '#0a8f4c';

        regNameInput.value = '';
        regMilitaryIdInput.value = '';
        regRankSelect.value = '';
        regUnitSelect.value = '';
        capturedSnapshotBase64 = null;
        if (photoPreviewImg) photoPreviewImg.style.display = 'none';
        if (uploadPlaceholderContent) uploadPlaceholderContent.style.display = 'flex';
        if (guideStatusText) guideStatusText.textContent = 'CĂN CHỈNH KHUÔN MẶT VÀO VÒNG TRÒN';

        await loadRegisteredFaces();
    } catch (err) {
        console.error(err);
        regStatusMsg.textContent = `✗ ${err.message}`;
        regStatusMsg.style.color = '#dc2626';
    } finally {
        btnSaveFace.disabled = false;
    }
}
window.handleFaceRegister = handleFaceRegister;


// ----------------- REGISTERED PERSONNEL TABLE -----------------
async function loadRegisteredFaces() {
    try {
        const res = await fetch('/api/faces');
        const result = await res.json();
        if (result.status === 'success') {
            registeredPersonnel = result.data || [];
            renderPersonnelTable(registeredPersonnel);
        }
    } catch (e) {
        console.error('Error fetching registered faces:', e);
    }
}

function renderPersonnelTable(list) {
    if (registeredCountBadge) registeredCountBadge.textContent = list.length;

    if (!personnelTbody) return;
    personnelTbody.innerHTML = '';

    if (list.length === 0) {
        tableEmptyMsg.style.display = 'block';
        return;
    }
    tableEmptyMsg.style.display = 'none';

    list.forEach(p => {
        const row = document.createElement('tr');
        const initial = (p.name || 'Q').trim().charAt(0).toUpperCase();
        const avatarHtml = p.avatar_path
            ? `<div class="avatar-badge-col"><img src="${p.avatar_path}" alt="${p.name}"></div>`
            : `<div class="avatar-badge-col">${initial}</div>`;

        row.innerHTML = `
            <td>${avatarHtml}</td>
            <td><strong>${p.name}</strong></td>
            <td><span class="military-id-tag">${p.military_id}</span></td>
            <td>${p.rank || '-'}</td>
            <td>${p.unit || '-'}</td>
            <td>${p.created_at || '01/08/2026'}</td>
            <td><span class="status-tag status-active">${p.status || 'Active'}</span></td>
            <td>
                <div class="action-btn-group">
                    <button class="icon-btn" title="Chỉnh sửa" onclick="editPerson('${p.id}')">✏️</button>
                    <button class="icon-btn icon-btn-delete" title="Xóa" onclick="deletePerson('${p.id}')">🗑️</button>
                </div>
            </td>
        `;
        personnelTbody.appendChild(row);
    });
}

function filterPersonnelTable() {
    const q = (searchPersonnelInput.value || '').toLowerCase().trim();
    const unit = filterUnitSelect.value;
    const rank = filterRankSelect.value;

    const filtered = registeredPersonnel.filter(p => {
        const matchQ = !q || (p.name && p.name.toLowerCase().includes(q)) || (p.military_id && p.military_id.toLowerCase().includes(q));
        const matchUnit = unit === 'all' || p.unit === unit;
        const matchRank = rank === 'all' || p.rank === rank;
        return matchQ && matchUnit && matchRank;
    });

    renderPersonnelTable(filtered);
}
window.filterPersonnelTable = filterPersonnelTable;

async function deletePerson(personId) {
    if (!confirm('Bạn có chắc chắn muốn xóa quân nhân này khỏi cơ sở dữ liệu Face ID?')) return;
    try {
        const res = await fetch(`/api/faces/${personId}`, { method: 'DELETE' });
        if (res.ok) {
            await loadRegisteredFaces();
        } else {
            alert('Lỗi khi xóa quân nhân');
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
    }
}
window.deletePerson = deletePerson;

function editPerson(personId) {
    const p = registeredPersonnel.find(x => x.id === personId);
    if (!p) return;
    const newName = prompt('Cập nhật Họ và tên:', p.name);
    if (newName && newName.trim()) {
        const formData = new FormData();
        formData.append('name', newName.trim());
        fetch(`/api/faces/${personId}`, { method: 'PUT', body: formData })
            .then(() => loadRegisteredFaces())
            .catch(e => console.error(e));
    }
}
window.editPerson = editPerson;

function closeAlert() {
    if (alertBanner) alertBanner.style.display = 'none';
}
window.closeAlert = closeAlert;

function triggerMockAlarm() {
    handleStreamAlert({
        timestamp: new Date().toISOString(),
        message: "Phát hiện đối tượng xâm nhập vượt vạch an toàn khu vực SÂN TẬP TRUNG!",
        category: "SAFETY",
        count: currentCount,
        baseline: baselineCount,
        image_path: ""
    });
}
window.triggerMockAlarm = triggerMockAlarm;


// ----------------- ROI & ZONE RULES (F-06) -----------------
let roiDrawingMode = 'polygon'; // 'polygon' | 'tripwire'
let polygonPoints = [
    { x: 0.08, y: 0.75 },
    { x: 0.35, y: 0.50 },
    { x: 0.70, y: 0.56 },
    { x: 0.92, y: 0.85 },
    { x: 0.12, y: 0.90 }
];
let tripwirePoints = [
    { x: 0.10, y: 0.45 },
    { x: 0.90, y: 0.40 }
];

const roiCanvas = document.getElementById('roi-canvas');
const roiModeLabel = document.getElementById('roi-mode-label');
const zoneCoordsJson = document.getElementById('zone-coords-json');
const zoneNameInput = document.getElementById('zone-name-input');
const zoneRuleTypeSelect = document.getElementById('zone-rule-type');
const detectHumanCb = document.getElementById('detect-human-cb');
const detectObjectCb = document.getElementById('detect-object-cb');
const zoneSaveStatus = document.getElementById('zone-save-status');

function initRoiCanvas() {
    if (!roiCanvas) return;
    const wrapper = document.getElementById('roi-canvas-wrapper');
    if (wrapper) {
        roiCanvas.width = wrapper.clientWidth || 720;
        roiCanvas.height = wrapper.clientHeight || 405;
    }

    roiCanvas.removeEventListener('click', onRoiCanvasClick);
    roiCanvas.addEventListener('click', onRoiCanvasClick);

    redrawRoiCanvas();
    updateCoordsJsonDisplay();
}

function setDrawingMode(mode) {
    roiDrawingMode = mode;
    const toolPoly = document.getElementById('tool-polygon');
    const toolTrip = document.getElementById('tool-tripwire');

    if (mode === 'polygon') {
        toolPoly.classList.add('active');
        toolTrip.classList.remove('active');
        if (roiModeLabel) roiModeLabel.textContent = 'CHẾ ĐỘ: POLYGON';
    } else {
        toolPoly.classList.remove('active');
        toolTrip.classList.add('active');
        if (roiModeLabel) roiModeLabel.textContent = 'CHẾ ĐỘ: TRIPWIRE';
    }
    redrawRoiCanvas();
    updateCoordsJsonDisplay();
}
window.setDrawingMode = setDrawingMode;

function resetRoiCanvas() {
    if (roiDrawingMode === 'polygon') {
        polygonPoints = [];
    } else {
        tripwirePoints = [];
    }
    redrawRoiCanvas();
    updateCoordsJsonDisplay();
}
window.resetRoiCanvas = resetRoiCanvas;

function onRoiCanvasClick(e) {
    if (!roiCanvas) return;
    const rect = roiCanvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;

    const normX = Math.round(x * 100) / 100;
    const normY = Math.round(y * 100) / 100;

    if (roiDrawingMode === 'polygon') {
        polygonPoints.push({ x: normX, y: normY });
    } else {
        if (tripwirePoints.length >= 2) {
            tripwirePoints = [{ x: normX, y: normY }];
        } else {
            tripwirePoints.push({ x: normX, y: normY });
        }
    }

    redrawRoiCanvas();
    updateCoordsJsonDisplay();
}

let roiBackgroundImage = null;

async function captureFrameForRoi() {
    try {
        const res = await fetch('/api/snapshot');
        const data = await res.json();
        if (data.status === 'success' && data.frame) {
            const img = new Image();
            img.onload = () => {
                roiBackgroundImage = img;
                redrawRoiCanvas();
            };
            img.src = 'data:image/jpeg;base64,' + data.frame;
        } else {
            alert('Chưa có luồng video đang chạy. Hãy bắt đầu giám sát hoặc tải video trước.');
        }
    } catch (e) {
        console.error('Error capturing ROI background:', e);
    }
}
window.captureFrameForRoi = captureFrameForRoi;

function redrawRoiCanvas() {
    if (!roiCanvas) return;
    const ctx = roiCanvas.getContext('2d');
    const w = roiCanvas.width;
    const h = roiCanvas.height;

    ctx.clearRect(0, 0, w, h);

    // 1. Draw Background Image if available
    if (roiBackgroundImage) {
        ctx.drawImage(roiBackgroundImage, 0, 0, w, h);
        // Dim slightly with dark overlay so tactical lines stand out
        ctx.fillStyle = 'rgba(9, 13, 22, 0.45)';
        ctx.fillRect(0, 0, w, h);
    }

    // 2. Draw Grid Lines
    ctx.strokeStyle = 'rgba(16, 185, 129, 0.12)';
    ctx.lineWidth = 1;
    for (let x = 0; x < w; x += 30) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
    }
    for (let y = 0; y < h; y += 30) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
    }

    // 2. Draw Tripwire (Orange dashed line)
    if (tripwirePoints.length >= 1) {
        ctx.save();
        ctx.strokeStyle = '#f59e0b';
        ctx.fillStyle = '#f59e0b';
        ctx.lineWidth = 3;
        ctx.setLineDash([8, 6]);

        if (tripwirePoints.length >= 2) {
            ctx.beginPath();
            ctx.moveTo(tripwirePoints[0].x * w, tripwirePoints[0].y * h);
            ctx.lineTo(tripwirePoints[1].x * w, tripwirePoints[1].y * h);
            ctx.stroke();
        }

        // End points
        tripwirePoints.forEach(pt => {
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.arc(pt.x * w, pt.y * h, 5, 0, Math.PI * 2);
            ctx.fill();
        });
        ctx.restore();
    }

    // 3. Draw Polygon (Red glow + transparent fill)
    if (polygonPoints.length > 0) {
        ctx.save();
        ctx.strokeStyle = '#ef4444';
        ctx.fillStyle = 'rgba(239, 68, 68, 0.22)';
        ctx.lineWidth = 2.5;

        // Shadow glow
        ctx.shadowColor = '#ef4444';
        ctx.shadowBlur = 10;

        ctx.beginPath();
        ctx.moveTo(polygonPoints[0].x * w, polygonPoints[0].y * h);
        for (let i = 1; i < polygonPoints.length; i++) {
            ctx.lineTo(polygonPoints[i].x * w, polygonPoints[i].y * h);
        }
        if (polygonPoints.length >= 3) {
            ctx.closePath();
            ctx.fill();
        }
        ctx.stroke();

        // Vertices points
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#f87171';
        polygonPoints.forEach(pt => {
            ctx.beginPath();
            ctx.arc(pt.x * w, pt.y * h, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        });
        ctx.restore();
    }
}

function updateCoordsJsonDisplay() {
    if (!zoneCoordsJson) return;
    const targetPoints = roiDrawingMode === 'polygon' ? polygonPoints : tripwirePoints;
    const formatted = JSON.stringify(targetPoints, null, 2);
    zoneCoordsJson.value = formatted;
}

async function loadZoneRules() {
    try {
        const res = await fetch('/api/zones');
        if (res.ok) {
            const data = await res.json();
            if (zoneNameInput && data.zone_name) zoneNameInput.value = data.zone_name;
            if (zoneRuleTypeSelect && data.rule_type) zoneRuleTypeSelect.value = data.rule_type;
            if (detectHumanCb) detectHumanCb.checked = data.detect_human !== false;
            if (detectObjectCb) detectObjectCb.checked = data.detect_object !== false;

            if (data.polygon_points && data.polygon_points.length > 0) {
                polygonPoints = data.polygon_points;
            }
            if (data.tripwire_points && data.tripwire_points.length > 0) {
                tripwirePoints = data.tripwire_points;
            }
            redrawRoiCanvas();
            updateCoordsJsonDisplay();
        }
    } catch (e) {
        console.error('Error loading zone rules:', e);
    }
}

async function saveZoneRules(e) {
    e.preventDefault();

    const config = {
        zone_name: zoneNameInput.value.trim() || 'Khu vực tập trung',
        rule_type: zoneRuleTypeSelect.value,
        detect_human: detectHumanCb.checked,
        detect_object: detectObjectCb.checked,
        polygon_points: polygonPoints,
        tripwire_points: tripwirePoints,
        updated_at: new Date().toISOString()
    };

    if (zoneSaveStatus) {
        zoneSaveStatus.textContent = 'Đang lưu cấu hình zone...';
        zoneSaveStatus.style.color = '#0284c7';
    }

    try {
        const res = await fetch('/api/zones', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        const data = await res.json();

        if (res.ok) {
            if (zoneSaveStatus) {
                zoneSaveStatus.textContent = `✓ ${data.message}`;
                zoneSaveStatus.style.color = '#0a8f4c';
            }
            // Add system event
            addEventFeedCard({
                category: 'SYSTEM',
                time: new Date().toLocaleTimeString(),
                desc: `Đã cập nhật Vùng & Luật F-06: "${config.zone_name}" (${config.rule_type}).`,
                location: 'CAM-01 SÂN TẬP TRUNG',
                isProcessed: true
            });
        } else {
            throw new Error(data.detail || 'Lỗi khi lưu cấu hình');
        }
    } catch (err) {
        if (zoneSaveStatus) {
            zoneSaveStatus.textContent = `✗ ${err.message}`;
            zoneSaveStatus.style.color = '#dc2626';
        }
    }
}
window.saveZoneRules = saveZoneRules;


// ----------------- SCHEDULE MANAGEMENT -----------------
const schedulesTbody = document.getElementById('schedules-tbody');
const scheduleModal = document.getElementById('schedule-modal');

function toggleScheduleModal() {
    if (!scheduleModal) return;
    scheduleModal.style.display = scheduleModal.style.display === 'none' ? 'flex' : 'none';
}
window.toggleScheduleModal = toggleScheduleModal;

async function loadSchedules() {
    if (!schedulesTbody) return;
    try {
        const res = await fetch('/api/schedules');
        const result = await res.json();
        if (result.status === 'success' && result.data) {
            renderSchedulesTable(result.data);
        }
    } catch (e) {
        console.error('Error loading schedules:', e);
    }
}
window.loadSchedules = loadSchedules;

// Ca chỉ "đang hoạt động" trong khung giờ của nó, hết giờ phải chuyển sang đã kết thúc
const SCHEDULE_STATE_CLASS = {
    upcoming: 'status-neutral',
    check_start: 'status-active',
    running: 'status-ok',
    check_end: 'status-active',
    finished: 'status-neutral'
};

function addMinutesToClock(hhmm, mins) {
    const parts = String(hhmm || '').split(':');
    if (parts.length < 2) return '--:--';
    const total = (parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10) + mins + 1440) % 1440;
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function checkedBadge(done, label) {
    return done
        ? `<span class="check-badge check-done">✓ ${label}</span>`
        : `<span class="check-badge check-pending">○ ${label}</span>`;
}

function renderSchedulesTable(schedules) {
    if (!schedulesTbody) return;
    schedulesTbody.innerHTML = '';

    if (schedules.length === 0) {
        schedulesTbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: #94a3b8; padding: 24px;">Chưa có ca thời khóa biểu nào được thiết lập</td></tr>`;
        return;
    }

    schedules.forEach(sch => {
        const win = sch.check_window_mins || 5;
        const done = sch.checked_today || {};
        const stateClass = SCHEDULE_STATE_CLASS[sch.state] || 'status-neutral';
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><span class="status-tag status-active">${sch.shift}</span></td>
            <td><strong>${sch.name}</strong></td>
            <td>${sch.unit}</td>
            <td class="font-mono">${sch.start_time} - ${sch.end_time}</td>
            <td class="font-mono" style="color: #059669; font-weight: 700;">${sch.start_time} → ${addMinutesToClock(sch.start_time, win)}</td>
            <td class="font-mono" style="color: #0369a1; font-weight: 700;">${addMinutesToClock(sch.end_time, -win)} → ${sch.end_time}</td>
            <td><strong>${sch.required_count || 45}</strong> quân nhân</td>
            <td><span class="status-tag ${stateClass}">${sch.state_label || sch.status || 'Active'}</span></td>
            <td class="check-badges">
                ${checkedBadge(done.start, 'Đầu giờ')}
                ${checkedBadge(done.end, 'Cuối giờ')}
            </td>
            <td>
                <button class="icon-btn icon-btn-delete" title="Xóa ca" onclick="deleteSchedule('${sch.id}')">🗑️</button>
            </td>
        `;
        schedulesTbody.appendChild(row);
    });
}

async function handleCreateSchedule(e) {
    e.preventDefault();
    const name = document.getElementById('sch-name-input').value.trim();
    const shift = document.getElementById('sch-shift-select').value;
    const unit = document.getElementById('sch-unit-select').value;
    const startTime = document.getElementById('sch-start-time').value;
    const endTime = document.getElementById('sch-end-time').value;
    const windowMins = parseInt(document.getElementById('sch-window-input').value) || 5;
    const requiredCount = parseInt(document.getElementById('sch-count-input').value) || 45;

    const payload = {
        name,
        shift,
        unit,
        start_time: startTime,
        end_time: endTime,
        check_window_mins: windowMins,
        required_count: requiredCount
    };

    try {
        const res = await fetch('/api/schedules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            toggleScheduleModal();
            loadSchedules();
            alert('✓ Đã tạo ca thời khóa biểu mới!');
        }
    } catch (err) {
        alert('Lỗi tạo thời khóa biểu: ' + err.message);
    }
}
window.handleCreateSchedule = handleCreateSchedule;

async function deleteSchedule(schId) {
    if (!confirm('Bạn có chắc muốn xóa ca thời khóa biểu này?')) return;
    try {
        const res = await fetch(`/api/schedules/${schId}`, { method: 'DELETE' });
        if (res.ok) {
            loadSchedules();
        }
    } catch (e) {
        alert('Lỗi xóa ca: ' + e.message);
    }
}
window.deleteSchedule = deleteSchedule;


// ----------------- ATTENDANCE LOGS MANAGEMENT -----------------
let attendanceLogsData = [];
const attendanceLogsTbody = document.getElementById('attendance-logs-tbody');

async function loadAttendanceLogs() {
    if (!attendanceLogsTbody) return;
    const unitFilter = document.getElementById('log-filter-unit');
    const unit = unitFilter ? unitFilter.value : 'all';

    try {
        const res = await fetch(`/api/attendance-logs?unit=${encodeURIComponent(unit)}`);
        const result = await res.json();
        if (result.status === 'success' && result.data) {
            attendanceLogsData = result.data;
            renderAttendanceLogsTable(attendanceLogsData);
            updateLogMetrics(attendanceLogsData);
        }
    } catch (e) {
        console.error('Error loading attendance logs:', e);
    }
}
window.loadAttendanceLogs = loadAttendanceLogs;

// Bản ghi cũ chỉ có một mốc đầu giờ ở cấp ngoài cùng, bản ghi mới gom cả hai mốc
// vào log.checks. Hàm này quy về cùng một dạng để bảng hiển thị được cả hai.
function getCheck(log, phase) {
    if (log.checks && log.checks[phase]) return log.checks[phase];
    if (phase === 'start' && !log.checks) {
        return {
            time: log.time,
            present: log.present,
            absent: log.absent,
            absent_personnel: log.absent_personnel || [],
            evidence: log.evidence || null
        };
    }
    if (log.checks && phase === 'start' && log.checks.manual) return log.checks.manual;
    return null;
}

function renderCheckCell(check, required) {
    if (!check) return '<span style="color: #94a3b8;">Chưa chốt</span>';
    const color = check.absent > 0 ? '#dc2626' : '#059669';
    return `<strong style="color: ${color};">${check.present}/${required}</strong>`
        + `<div class="cell-subtext font-mono">${check.time || ''}</div>`;
}

function renderEvidenceCell(check, log, phaseLabel) {
    if (!check || !check.evidence) {
        return '<span style="color: #94a3b8;">—</span>';
    }
    const caption = `${log.date} · ${log.unit} · ${phaseLabel} · Có mặt ${check.present}/${log.required}`;
    return `<img src="${check.evidence}" class="evidence-thumb" loading="lazy"`
        + ` alt="Bằng chứng ${phaseLabel}"`
        + ` onclick="openEvidenceModal('${check.evidence}', '${phaseLabel}', '${caption.replace(/'/g, "\\'")}')">`;
}

function renderAbsentList(log) {
    const startCheck = getCheck(log, 'start');
    const endCheck = getCheck(log, 'end');
    const parts = [];
    if (startCheck && (startCheck.absent_personnel || []).length > 0) {
        parts.push(`<div><span class="phase-tag">Đầu giờ</span> ${startCheck.absent_personnel.join(', ')}</div>`);
    }
    if (endCheck && (endCheck.absent_personnel || []).length > 0) {
        parts.push(`<div><span class="phase-tag">Cuối giờ</span> ${endCheck.absent_personnel.join(', ')}</div>`);
    }
    if (parts.length === 0) return '<span style="color: #64748b;">-</span>';
    return `<span style="color: #d97706; font-weight: 500;">${parts.join('')}</span>`;
}

function renderAttendanceLogsTable(logs) {
    if (!attendanceLogsTbody) return;
    attendanceLogsTbody.innerHTML = '';

    if (logs.length === 0) {
        attendanceLogsTbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: #94a3b8; padding: 24px;">Không có bản ghi điểm danh nào phù hợp</td></tr>`;
        return;
    }

    logs.forEach(log => {
        const row = document.createElement('tr');
        const statusClass = log.status_type === 'success' ? 'status-ok' : 'status-warning';
        const startCheck = getCheck(log, 'start');
        const endCheck = getCheck(log, 'end');

        row.innerHTML = `
            <td class="font-mono"><strong>${log.date}</strong> ${log.time || ''}</td>
            <td>${log.shift}<div class="cell-subtext">${log.schedule_name || ''}</div></td>
            <td><strong>${log.unit}</strong></td>
            <td>${log.required}</td>
            <td>${renderCheckCell(startCheck, log.required)}</td>
            <td>${renderEvidenceCell(startCheck, log, 'Đầu giờ')}</td>
            <td>${renderCheckCell(endCheck, log.required)}</td>
            <td>${renderEvidenceCell(endCheck, log, 'Cuối giờ')}</td>
            <td style="max-width: 260px;">${renderAbsentList(log)}</td>
            <td><span class="status-tag ${statusClass}">${log.status}</span></td>
            <td>${log.commander || 'Đại úy Nguyễn Văn Hùng'}</td>
        `;
        attendanceLogsTbody.appendChild(row);
    });
}

// ----------------- EVIDENCE LIGHTBOX -----------------
function openEvidenceModal(src, phaseLabel, caption) {
    const modal = document.getElementById('evidence-modal');
    const img = document.getElementById('evidence-modal-img');
    const title = document.getElementById('evidence-modal-title');
    const captionEl = document.getElementById('evidence-modal-caption');
    if (!modal || !img) return;

    img.src = src;
    if (title) title.textContent = `Bằng chứng điểm danh ${phaseLabel.toLowerCase()}`;
    if (captionEl) captionEl.textContent = caption || '';
    modal.style.display = 'flex';
}
window.openEvidenceModal = openEvidenceModal;

function closeEvidenceModal() {
    const modal = document.getElementById('evidence-modal');
    if (modal) modal.style.display = 'none';
}
window.closeEvidenceModal = closeEvidenceModal;

function filterAttendanceLogsByStatus() {
    const statusSelect = document.getElementById('log-filter-status');
    const selected = statusSelect ? statusSelect.value : 'all';

    if (selected === 'all') {
        renderAttendanceLogsTable(attendanceLogsData);
    } else {
        const filtered = attendanceLogsData.filter(l => l.status_type === selected);
        renderAttendanceLogsTable(filtered);
    }
}
window.filterAttendanceLogsByStatus = filterAttendanceLogsByStatus;

function updateLogMetrics(logs) {
    const totalEl = document.getElementById('metric-total-logs');
    const passRateEl = document.getElementById('metric-pass-rate');
    const absentLogsEl = document.getElementById('metric-absent-logs');

    if (!totalEl) return;
    totalEl.textContent = logs.length;

    const passCount = logs.filter(l => l.absent === 0).length;
    const rate = logs.length > 0 ? Math.round((passCount / logs.length) * 100) : 100;
    if (passRateEl) passRateEl.textContent = `${rate}%`;

    const absentCount = logs.filter(l => l.absent > 0).length;
    if (absentLogsEl) absentLogsEl.textContent = absentCount;
}

function exportAttendanceLogsCsv() {
    if (attendanceLogsData.length === 0) {
        alert('Không có dữ liệu điểm danh để xuất báo cáo');
        return;
    }

    let csvContent = "data:text/csv;charset=utf-8,\uFEFF";
    csvContent += "Ngày,Ca điểm danh,Đơn vị,Sĩ số yêu cầu,"
        + "Giờ đầu giờ,Hiện diện đầu giờ,Vắng đầu giờ,Bằng chứng đầu giờ,"
        + "Giờ cuối giờ,Hiện diện cuối giờ,Vắng cuối giờ,Bằng chứng cuối giờ,"
        + "Quân nhân vắng,Trạng thái,Chỉ huy duyệt\n";

    const origin = window.location.origin;
    const cell = (check, field) => (check && check[field] !== undefined && check[field] !== null ? check[field] : '');
    const evidenceUrl = (check) => (check && check.evidence ? origin + check.evidence : '');

    attendanceLogsData.forEach(l => {
        const st = getCheck(l, 'start');
        const en = getCheck(l, 'end');
        const absentStr = (l.absent_personnel || []).join('; ');
        csvContent += `"${l.date}","${l.shift}","${l.unit}",${l.required},`
            + `"${cell(st, 'time')}","${cell(st, 'present')}","${cell(st, 'absent')}","${evidenceUrl(st)}",`
            + `"${cell(en, 'time')}","${cell(en, 'present')}","${cell(en, 'absent')}","${evidenceUrl(en)}",`
            + `"${absentStr}","${l.status}","${l.commander || ''}"\n`;
    });

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `bao_cao_diem_danh_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
window.exportAttendanceLogsCsv = exportAttendanceLogsCsv;


// ----------------- INITIALIZATION -----------------
document.addEventListener('DOMContentLoaded', () => {
    loadRegisteredFaces();

    fetch('/api/status')
        .then(r => r.json())
        .then(data => {
            if (data.is_processing) {
                connectWebSocket();
            }
        })
        .catch(e => console.error(e));
});
