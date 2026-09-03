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
let pendingEventsCount = 2;

// DOM Elements
const currentPageTitle = document.getElementById('current-page-title');
const liveTimeEl = document.getElementById('live-time');
const liveDateEl = document.getElementById('live-date');
const topbarAttendanceStat = document.getElementById('topbar-attendance-stat');
const topbarAlertStat = document.getElementById('topbar-alert-stat');
const pendingEventsBadge = document.getElementById('pending-events-badge');

// Surveillance DOM Elements
const videoStreamImg = document.getElementById('video-stream');
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

// Phân hệ I và II không phải hai nhóm màn riêng: cùng một nghiệp vụ, chỉ khác
// loại lịch. Nên dùng chung màn và tách bằng bộ lọc training_type.
const SHARED_VIEW = {
    'attendance': 'attendance-summary',
    'safety': 'safety'
};

function switchNavTab(tabName) {
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => item.classList.remove('active'));
    const activeNav = document.getElementById(`nav-${tabName}`);
    if (activeNav) activeNav.classList.add('active');

    document.querySelectorAll('.page-view').forEach(view => view.classList.remove('active'));
    const targetView = document.getElementById(`view-${SHARED_VIEW[tabName] || tabName}`);
    if (targetView) targetView.classList.add('active');

    // Rời màn nào thì ngắt luồng hình của màn đó, không để chạy ngầm
    ['ad-stream', 'sf-stream'].forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.closest('.page-view').classList.contains('active')) detachStream(el);
    });
    const mainImg = document.getElementById('video-stream');
    if (mainImg && tabName !== 'monitoring') detachStream(mainImg);

    const titles = {
        'schedule-progress': 'Lịch & Tiến độ huấn luyện',
        'attendance': 'Giám sát quân số',
        'safety': 'An toàn bắn đạn thật',
        'session-detail': 'Chi tiết lịch huấn luyện',
        'attendance-detail': 'Chi tiết giám sát quân số',
        'monitoring': 'Giám sát trực tiếp',
        'zones': 'Vùng giám sát',
        'cameras': 'Thiết bị camera',
        'registration': 'Đăng ký khuôn mặt',
        'schedule': 'Cấu hình giám sát',
        'logs': 'Nhật ký điểm danh'
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

    if (safetyPollTimer) { clearInterval(safetyPollTimer); safetyPollTimer = null; }
    currentSafetyType = null;
    currentTabName = tabName;

    syncTrainingFilterButtons();

    switch (tabName) {
        case 'schedule-progress':
            loadTrainingSchedule();
            break;
        case 'attendance':
            loadAttendanceSummary();
            break;
        case 'safety':
            currentSafetyType = currentTrainingType;
            loadSafetyDashboard();
            // Màn hoạt động thời gian thực, giữ nguyên trang và tự làm mới
            safetyPollTimer = setInterval(loadSafetyDashboard, 15000);
            break;
        case 'cameras':
            loadCameras();
            break;
        case 'monitoring':
            loadCameras();
            startStream();
            break;
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
        if (!ctx) return;
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

async function lockBaselineManual() {
    const targetCount = currentCount > 0 ? currentCount : 45;
    try {
        const res = await fetch(`/api/set-baseline?count=${targetCount}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            baselineCount = data.baseline;
            if (topbarAttendanceStat) topbarAttendanceStat.textContent = `Quân số: ${currentCount || baselineCount}/${baselineCount}`;

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
        alert('✓ ' + (data.message || 'Đã mở phiên điểm danh'));
    } catch (e) {
        alert('Lỗi mở phiên điểm danh: ' + e.message);
    }
}
window.startAttendanceNow = startAttendanceNow;

function takeQuickSnapshot() {
    // Tải ảnh gốc từ máy chủ, không chụp lại từ thẻ ảnh đang hiển thị
    window.open(`/api/v1/cameras/${activeCameraId}/snapshot?overlay=1&download=1`, '_blank');
}
window.takeQuickSnapshot = takeQuickSnapshot;

async function quickStartStream() {
    if (overlayStatusText) overlayStatusText.textContent = 'Đang khởi chạy luồng giám sát CAM-01...';
    try {
        const res = await fetch('/api/start?mode=video', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            startStream();
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
            startStream();
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
                    startStream();
                }
            }
        } catch (err) {
            console.error(err);
            uploadStatus.textContent = `✗ ${err.message}`;
            uploadStatus.style.color = '#ef4444';
        }
    });
}

// ----------------- LUỒNG HÌNH (MJPEG) VÀ SỰ KIỆN (SSE) -----------------
// Hình và dữ liệu đi hai đường khác nhau: thẻ <img> nhận MJPEG, EventSource nhận
// sự kiện. Không còn nhồi khung hình base64 qua WebSocket rồi vẽ lên canvas.

let eventSource = null;
let statusTimer = null;
let lastEventId = null;
let activeCameraId = 'cam_01';

function streamUrl(cameraId, overlay) {
    return `/api/v1/cameras/${cameraId}/stream.mjpg?overlay=${overlay ? 1 : 0}&_=${Date.now()}`;
}

function attachStream(imgEl, cameraId, overlay) {
    if (!imgEl) return;
    imgEl.src = streamUrl(cameraId, overlay);
    imgEl.onerror = () => { imgEl.removeAttribute('src'); };
}

function detachStream(imgEl) {
    // Bỏ src để trình duyệt đóng kết nối; không làm thì luồng vẫn chạy ngầm
    if (imgEl) imgEl.removeAttribute('src');
}

function startStream() {
    const img = document.getElementById('video-stream');
    if (!img) return;
    if (loadingOverlay) loadingOverlay.style.display = 'none';
    isProcessing = true;
    attachStream(img, activeCameraId, isAiOverlayEnabled);
    img.onerror = () => {
        isProcessing = false;
        if (loadingOverlay) loadingOverlay.style.display = 'flex';
        if (overlayStatusText) overlayStatusText.textContent = 'Luồng giám sát chưa chạy';
        if (overlayStartBtn) overlayStartBtn.style.display = 'block';
    };
}
window.startStream = startStream;

function switchMonitorCamera(cameraId) {
    activeCameraId = cameraId || activeCameraId;
    startStream();
}
window.switchMonitorCamera = switchMonitorCamera;

// ---------- kênh sự kiện ----------

function connectEventStream() {
    if (eventSource) return;
    const since = lastEventId ? `?since_event_id=${encodeURIComponent(lastEventId)}` : '';
    eventSource = new EventSource(`/api/v1/events/stream${since}`);

    eventSource.onmessage = (msg) => {
        try {
            handleAiEvent(JSON.parse(msg.data));
        } catch (e) {
            console.error('Bản tin sự kiện không hợp lệ:', e);
        }
    };

    eventSource.onerror = () => {
        // EventSource tự nối lại; đóng hẳn để lần sau mở kèm since_event_id,
        // nhờ vậy không mất sự kiện phát sinh trong lúc đứt kết nối.
        eventSource.close();
        eventSource = null;
        setTimeout(connectEventStream, 3000);
    };
}

const EVENT_LABELS = {
    ABSENT: 'THIẾU QUÂN SỐ',
    LATE: 'ĐI CHẬM',
    EARLY_LEAVE: 'VỀ SỚM',
    INTRUSION: 'VI PHẠM AN TOÀN',
    SYSTEM: 'HỆ THỐNG'
};
const EVENT_CLASS = {
    ABSENT: 'event-absent',
    LATE: 'event-absent',
    EARLY_LEAVE: 'event-absent',
    INTRUSION: 'event-safety',
    SYSTEM: 'event-system'
};

function handleAiEvent(event) {
    lastEventId = event.id;

    if (event.type !== 'SYSTEM') incrementAlertCount();
    renderEventCard(eventsListContainer, event, true);

    if (event.type === 'INTRUSION') {
        onIntrusionEvent(event);
    } else if (event.severity !== 'info' && alertBanner && alertMessage && !isSirenMuted) {
        alertMessage.textContent = `⚠️ ${event.message}`;
        alertBanner.style.display = 'flex';
        setTimeout(() => { alertBanner.style.display = 'none'; }, 10000);
    }

    if (event.type === 'SYSTEM' && event.detail && event.detail.code === 'check_closed') {
        loadAttendanceLogs();
    }
}

function renderEventCard(container, event, prepend) {
    if (!container) return;
    const hint = container.querySelector('.empty-hint');
    if (hint) hint.remove();

    const card = document.createElement('div');
    card.className = `event-card ${EVENT_CLASS[event.type] || 'event-system'}`;
    card.id = `evt-${event.id}`;

    const time = new Date(event.occurred_at).toLocaleTimeString('vi-VN');
    const place = [event.camera_name, event.area_name].filter(Boolean).join(' · ');
    const clipBtn = event.clip_url
        ? `<button class="btn-event-clip" onclick="viewEventClip('${event.clip_id}')">▶️ Xem clip 10s</button>` : '';
    const ackBtn = event.acked
        ? `<button class="btn-event-processed" disabled>✓ ${event.acked_by || 'Đã xử lý'}</button>`
        : `<button class="btn-event-confirm" onclick="ackEvent('${event.id}')">Xác nhận xử lý</button>`;

    card.innerHTML = `
        <div class="event-card-header">
            <span class="event-category cat-${(event.type || '').toLowerCase()}">${EVENT_LABELS[event.type] || event.type}</span>
            <span class="event-time">${time}</span>
        </div>
        <p class="event-desc">${event.message}</p>
        ${event.snapshot_url ? `<img class="event-thumb" src="${event.snapshot_url}" onclick="openEvidence('${event.snapshot_url}','${event.message.replace(/'/g, '')}')" alt="Ảnh sự kiện">` : ''}
        <div class="event-location">${place}</div>
        <div class="event-card-actions">${clipBtn}${ackBtn}</div>
    `;

    if (prepend) container.prepend(card); else container.appendChild(card);
    while (container.children.length > 60) container.lastElementChild.remove();
}

async function ackEvent(eventId) {
    const who = document.querySelector('.user-name');
    try {
        const res = await fetch(`/api/v1/events/${eventId}/ack`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ acked_by: (who && who.textContent) || 'Chỉ huy trực ban' })
        });
        if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
        const updated = await res.json();

        document.querySelectorAll(`#evt-${eventId} .event-card-actions`).forEach(el => {
            el.innerHTML = `<button class="btn-event-processed" disabled>✓ ${updated.acked_by}</button>`;
        });
        pendingEventsCount = Math.max(0, pendingEventsCount - 1);
        if (pendingEventsBadge) pendingEventsBadge.textContent = `${pendingEventsCount} chờ xử lý`;
        if (currentSafetyType) loadSafetyDashboard();
    } catch (e) {
        alert('Không xác nhận được: ' + e.message);
    }
}
window.ackEvent = ackEvent;

// ---------- chỉ số trực tiếp ----------
// MJPEG chỉ mang hình, các con số lấy bằng cách hỏi máy chủ định kỳ.

async function pollLiveStatus() {
    try {
        const [statusRes, attRes] = await Promise.all([
            fetch('/api/status'), fetch('/api/attendance/status')
        ]);
        const status = await statusRes.json();
        const att = (await attRes.json()).attendance || {};

        isProcessing = !!status.is_processing;
        currentCount = status.current_count || 0;
        if (typeof status.baseline_count === 'number') baselineCount = status.baseline_count;

        if (topbarAttendanceStat) {
            topbarAttendanceStat.textContent = `Quân số: ${currentCount}/${baselineCount || 0}`;
        }

        const attBtn = document.getElementById('attendance-btn-text');
        if (attBtn) {
            if (att.active) {
                const remain = att.remaining_seconds || 0;
                const mm = String(Math.floor(remain / 60)).padStart(2, '0');
                const ss = String(remain % 60).padStart(2, '0');
                attBtn.textContent = `${att.phase_label || 'Điểm danh'} ${mm}:${ss} · ${att.present}/${att.required || 0}`;
            } else {
                attBtn.textContent = 'Điểm danh ngay';
            }
        }
    } catch (e) {
        /* máy chủ bận thì bỏ qua nhịp này */
    }
}

function startLivePolling() {
    if (statusTimer) return;
    pollLiveStatus();
    statusTimer = setInterval(pollLiveStatus, 2000);
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
    // overlay=0: lấy khung hình GỐC. Dùng ảnh đã vẽ lớp phủ thì sẽ vẽ vùng mới
    // đè lên chính hình các vùng cũ, càng chỉnh càng lệch.
    try {
        const res = await fetch(`/api/v1/cameras/${activeCameraId}/snapshot?overlay=0&_=${Date.now()}`);
        if (!res.ok) {
            alert('Chưa có luồng video đang chạy. Hãy bắt đầu giám sát hoặc tải video trước.');
            return;
        }
        const blob = await res.blob();
        const img = new Image();
        img.onload = () => {
            URL.revokeObjectURL(img.src);
            roiBackgroundImage = img;
            redrawRoiCanvas();
        };
        img.src = URL.createObjectURL(blob);
    } catch (e) {
        console.error('Không lấy được ảnh nền để vẽ vùng:', e);
    }
}
window.captureFrameForRoi = captureFrameForRoi;

function redrawRoiCanvas() {
    if (!roiCanvas) return;
    const ctx = roiCanvas.getContext('2d');
    if (!ctx) return;
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

function openScheduleModal(schedule) {
    const modal = document.getElementById('schedule-modal');
    if (!modal) return;
    const sch = schedule || {};
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };

    document.getElementById('schedule-modal-title').textContent =
        sch.id ? 'Cập nhật ca huấn luyện' : 'Thêm ca huấn luyện';
    set('sch-id', sch.id || '');
    set('sch-name-input', sch.name || '');
    // Ca mới mặc định theo loại đang lọc, đỡ phải chọn lại
    set('sch-training-type', sch.training_type || currentTrainingType || 'dao_tao');
    set('sch-shift-select', sch.shift || 'Ca sáng');
    set('sch-unit-select', sch.unit || 'Đại đội 1');
    set('sch-class-name', sch.class_name || '');
    set('sch-start-time', sch.start_time || '07:00');
    set('sch-end-time', sch.end_time || '11:30');
    set('sch-lesson-name', sch.lesson_name || '');
    set('sch-instructor', sch.instructor || '');
    set('sch-field', sch.field || '');
    set('sch-window-input', sch.check_window_mins || 5);
    set('sch-count-input', sch.required_count || 45);
    set('sch-late-tol', sch.late_tolerance_mins != null ? sch.late_tolerance_mins : 5);
    set('sch-early-tol', sch.early_leave_tolerance_mins != null ? sch.early_leave_tolerance_mins : 5);
    document.getElementById('sch-form-status').textContent = '';
    modal.style.display = 'flex';
}
window.openScheduleModal = openScheduleModal;

function closeScheduleModal() {
    const modal = document.getElementById('schedule-modal');
    if (modal) modal.style.display = 'none';
}
window.closeScheduleModal = closeScheduleModal;

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
    const val = (id) => (document.getElementById(id) || {}).value;
    const num = (id, fallback) => parseInt(val(id), 10) || fallback;
    const id = val('sch-id');
    const status = document.getElementById('sch-form-status');

    // Trường lõi AI đọc thì gửi đúng kiểu; lesson_name / instructor / field /
    // class_name là của giao diện, backend giữ nguyên và trả lại.
    const payload = {
        name: (val('sch-name-input') || '').trim(),
        training_type: val('sch-training-type'),
        shift: val('sch-shift-select'),
        unit: val('sch-unit-select'),
        class_name: (val('sch-class-name') || '').trim(),
        start_time: val('sch-start-time'),
        end_time: val('sch-end-time'),
        lesson_name: (val('sch-lesson-name') || '').trim(),
        instructor: (val('sch-instructor') || '').trim(),
        field: (val('sch-field') || '').trim(),
        check_window_mins: num('sch-window-input', 5),
        required_count: num('sch-count-input', 45),
        late_tolerance_mins: num('sch-late-tol', 5),
        early_leave_tolerance_mins: num('sch-early-tol', 5)
    };

    try {
        const res = await fetch(id ? `/api/v1/schedules/${id}` : '/api/v1/schedules', {
            method: id ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));

        closeScheduleModal();
        loadSchedules();
        loadTrainingSchedule();
    } catch (err) {
        if (status) {
            status.textContent = `✗ ${err.message}`;
            status.style.color = '#dc2626';
        }
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



// =====================================================================
// PHÂN HỆ I & II — LỊCH, TIẾN ĐỘ, GIÁM SÁT QUÂN SỐ
// Hai phân hệ dùng chung một bộ hàm, chỉ khác training_type. Doc yêu cầu
// hai nhóm màn riêng nhưng nghiệp vụ giống hệt nhau nên không nhân đôi code.
// =====================================================================

// Rỗng = xem cả hai loại huấn luyện. Phân hệ I và II chỉ khác nhau ở đây.
let currentTrainingType = '';
let currentTabName = 'attendance';
let currentSafetyType = null;
let attendanceDetailData = { items: [], session: null };
let safetyPollTimer = null;
let isSafetySirenMuted = false;

const TRAINING_LABEL = { dao_tao: 'Đào tạo', chien_dau: 'Chiến đấu', '': 'Toàn đơn vị' };
const TRAINING_TAG = {
    dao_tao: '<span class="tt-tag tt-dt">Đào tạo</span>',
    chien_dau: '<span class="tt-tag tt-cd">Chiến đấu</span>'
};

function trainingQuery(prefix) {
    return currentTrainingType ? `${prefix}training_type=${currentTrainingType}` : '';
}

function syncTrainingFilterButtons() {
    document.querySelectorAll('.training-filter .tt-btn').forEach(btn => {
        btn.classList.toggle('active', (btn.dataset.tt || '') === currentTrainingType);
    });
}

function setTrainingFilter(value) {
    currentTrainingType = value || '';
    syncTrainingFilterButtons();

    // Nạp lại đúng màn đang xem, không nạp cả ba
    if (currentTabName === 'schedule-progress') loadTrainingSchedule();
    else if (currentTabName === 'attendance') loadAttendanceSummary();
    else if (currentTabName === 'safety') {
        currentSafetyType = currentTrainingType;
        loadSafetyDashboard();
    }
}
window.setTrainingFilter = setTrainingFilter;
const STATE_CLASS = {
    upcoming: 'status-neutral', check_start: 'status-active',
    running: 'status-ok', check_end: 'status-active', finished: 'status-neutral'
};

function esc(text) {
    return String(text == null ? '' : text).replace(/[&<>"]/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
}

function fmtTime(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
}

async function getJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error((await res.text()).slice(0, 200));
    return res.json();
}

// ----------------- MÀN 1.1: LỊCH & TIẾN ĐỘ -----------------

async function loadTrainingSchedule() {
    const tbody = document.getElementById('dt-schedule-tbody');
    if (!tbody) return;

    const dateEl = document.getElementById('dt-schedule-date');
    const q = (document.getElementById('dt-schedule-search') || {}).value || '';
    const date = (dateEl && dateEl.value) ? `&date=${dateEl.value}` : '';

    try {
        const data = await getJson(`/api/v1/summary/training?_=1${trainingQuery('&')}${date}`);
        const rows = data.sessions.filter(s =>
            !q || `${s.name} ${s.unit}`.toLowerCase().includes(q.toLowerCase()));

        document.getElementById('dt-metric-running').textContent = data.stats.running_sessions;
        const pct = Math.round(data.stats.overall_progress_pct || 0);
        document.getElementById('dt-metric-progress').textContent = `${pct}%`;
        document.getElementById('dt-metric-progress-bar').style.width = `${pct}%`;
        document.getElementById('dt-metric-headcount').textContent =
            `${data.stats.present_total}/${data.stats.required_total}`;
        document.getElementById('dt-metric-violations').textContent = data.stats.violation_total;

        tbody.innerHTML = rows.length ? '' :
            `<tr><td colspan="8" class="empty-row">Không có lịch huấn luyện nào cho ngày này</td></tr>`;

        rows.forEach(s => {
            const prog = Math.round(s.progress_pct || 0);
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${TRAINING_TAG[s.training_type] || '<span class="tt-tag">—</span>'}
                    <br><span class="muted">${esc(s.shift || '')}</span></td>
                <td><strong>${esc(s.name)}</strong></td>
                <td>${esc(s.unit || '—')}</td>
                <td class="font-mono">${esc(s.planned || '')}</td>
                <td><span class="status-tag ${STATE_CLASS[s.state] || 'status-neutral'}">${esc(s.state_label)}</span></td>
                <td>
                    <div class="progress-track"><div class="progress-fill" style="width:${prog}%"></div></div>
                    <span class="progress-text">${prog}% · ${s.actual_minutes || 0}/${s.scheduled_minutes || 0} phút</span>
                </td>
                <td><strong>${s.present_start || 0}</strong> / ${s.required || 0}</td>
                <td><button class="btn-event-clip" onclick="openSessionDetail('${s.id}','${s.schedule_id}')">Xem chi tiết</button></td>`;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-row">Lỗi tải lịch: ${esc(e.message)}</td></tr>`;
    }
}
window.loadTrainingSchedule = loadTrainingSchedule;

// ----------------- MÀN 1.2: CHI TIẾT LỊCH -----------------

let sessionDetailId = null;
let sessionDetailFrom = 'schedule-progress';

async function openSessionDetail(sessionId, scheduleId) {
    sessionDetailId = sessionId || scheduleId;
    sessionDetailFrom = 'schedule-progress';
    switchNavTab('session-detail');

    try {
        const sch = await getJson(`/api/v1/schedules/${scheduleId}`);
        document.getElementById('sd-title').textContent = (sch.name || '').toUpperCase();
        document.getElementById('sd-subtitle').textContent =
            `${sch.shift || ''} · ${sch.start_time}–${sch.end_time} · ${sch.unit || 'Toàn đơn vị'}`;

        // Trường giáo viên / thao trường / bài học do hệ thống quản lý gửi kèm khi
        // tạo ca; service AI giữ nguyên và trả lại, ở đây chỉ hiển thị.
        const info = [
            ['Tên bài học', sch.lesson_name],
            ['Giáo viên phụ trách', sch.instructor],
            ['Thao trường', sch.field],
            ['Đội học / Lớp', sch.class_name],
            ['Khung giờ', `${sch.start_time} – ${sch.end_time}`],
            ['Cửa sổ điểm danh', `${sch.check_window_mins} phút mỗi mốc`],
            ['Dung sai đi chậm', `${sch.late_tolerance_mins} phút`],
            ['Sĩ số chuẩn', sch.required_count || '—'],
            ['Trạng thái', sch.state_label]
        ];
        document.getElementById('sd-info').innerHTML = info.map(([k, v]) =>
            `<div class="detail-item"><span class="detail-key">${k}</span>
             <span class="detail-val">${esc(v || '—')}</span></div>`).join('');

        const btn = document.getElementById('sd-btn-watch');
        btn.style.display = ['check_start', 'running', 'check_end'].includes(sch.state) ? '' : 'none';
    } catch (e) {
        document.getElementById('sd-subtitle').textContent = `Lỗi tải ca: ${e.message}`;
    }

    await loadSessionChecks(sessionDetailId);
}
window.openSessionDetail = openSessionDetail;

async function loadSessionChecks(sessionId) {
    const tbody = document.getElementById('sd-checks-tbody');
    if (!tbody) return;
    try {
        const checks = await getJson(`/api/v1/sessions/${encodeURIComponent(sessionId)}/checks`);
        tbody.innerHTML = checks.length ? '' :
            `<tr><td colspan="5" class="empty-row">Buổi chưa diễn ra — cả hai mốc đều bằng 0</td></tr>`;
        checks.forEach(c => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${esc(c.phase_label)}</strong></td>
                <td class="font-mono">${esc(c.time || '—')}</td>
                <td class="text-green"><strong>${c.present}</strong></td>
                <td class="${c.absent > 0 ? 'text-amber' : ''}">${c.absent}</td>
                <td>${c.evidence_url
                    ? `<img class="evidence-thumb" src="${c.evidence_url}" onclick="openEvidence('${c.evidence_url}','Điểm danh ${esc(c.phase_label)}')" alt="Ảnh bằng chứng">`
                    : '<span class="muted">Chưa có</span>'}</td>`;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-row">Chưa có biên bản điểm danh</td></tr>`;
    }
}

function backFromSessionDetail() { switchNavTab(sessionDetailFrom); }
window.backFromSessionDetail = backFromSessionDetail;

function watchSessionAttendance() { openAttendanceDetail(sessionDetailId, sessionDetailId); }
window.watchSessionAttendance = watchSessionAttendance;

// ----------------- MÀN 2.1 / 4.1: TỔNG HỢP QUÂN SỐ -----------------

async function loadAttendanceSummary() {
    const tbody = document.getElementById('as-tbody');
    if (!tbody) return;

    document.getElementById('as-title').textContent = currentTrainingType
        ? `TỔNG HỢP GIÁM SÁT QUÂN SỐ HUẤN LUYỆN ${TRAINING_LABEL[currentTrainingType].toUpperCase()}`
        : 'TỔNG HỢP GIÁM SÁT QUÂN SỐ HUẤN LUYỆN';
    document.getElementById('as-subtitle').textContent =
        'CÁC LỚP ĐANG DIỄN RA TRÊN THAO TRƯỜNG · CẬP NHẬT THEO THỜI GIAN THỰC';

    try {
        const data = await getJson(`/api/v1/summary/training?_=1${trainingQuery('&')}`);
        document.getElementById('as-metric-running').textContent = data.stats.running_sessions;
        document.getElementById('as-metric-present').textContent = data.stats.present_total;
        document.getElementById('as-metric-required').textContent = data.stats.required_total;
        document.getElementById('as-metric-violations').textContent = data.stats.violation_total;

        tbody.innerHTML = data.sessions.length ? '' :
            `<tr><td colspan="8" class="empty-row">
                Chưa có lớp nào${currentTrainingType ? ` thuộc huấn luyện ${TRAINING_LABEL[currentTrainingType].toLowerCase()}` : ''}
             </td></tr>`;

        data.sessions.forEach(s => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${esc(s.name)}</strong><br>
                    ${TRAINING_TAG[s.training_type] || ''}
                    <span class="muted">${esc(s.shift || '')}</span></td>
                <td>${esc(s.unit || '—')}</td>
                <td><span class="status-tag ${STATE_CLASS[s.state] || 'status-neutral'}">${esc(s.state_label)}</span></td>
                <td class="text-green"><strong>${s.present_start || 0}</strong></td>
                <td>${s.present_end || 0}</td>
                <td>${s.required || 0}</td>
                <td class="${s.violation_count > 0 ? 'text-amber' : ''}"><strong>${s.violation_count || 0}</strong></td>
                <td><button class="btn-event-clip" onclick="openAttendanceDetail('${s.id}','${s.schedule_id}')">Xem chi tiết điểm danh</button></td>`;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-row">Lỗi tải dữ liệu: ${esc(e.message)}</td></tr>`;
    }
}
window.loadAttendanceSummary = loadAttendanceSummary;

// ----------------- MÀN 2.2 / 4.2: CHI TIẾT + HÌNH ẢNH AI -----------------

const VIOLATION_TAG = {
    late: '<span class="viol-tag viol-late">Đi chậm</span>',
    early_leave: '<span class="viol-tag viol-early">Chưa hết giờ đã về</span>',
    absent: '<span class="viol-tag viol-absent">Không tham gia</span>'
};

async function openAttendanceDetail(sessionId, scheduleId) {
    attendanceDetailData.session = sessionId || scheduleId;
    switchNavTab('attendance-detail');

    document.getElementById('ad-title').textContent = currentTrainingType
        ? `CHI TIẾT GIÁM SÁT QUÂN SỐ · ${TRAINING_LABEL[currentTrainingType].toUpperCase()}`
        : 'CHI TIẾT GIÁM SÁT QUÂN SỐ';

    attachStream(document.getElementById('ad-stream'), activeCameraId, true);
    document.getElementById('ad-camera-caption').textContent =
        'Camera AI đang giám sát lớp học — khung xanh là quân nhân đã định danh';

    try {
        const data = await getJson(
            `/api/v1/sessions/${encodeURIComponent(attendanceDetailData.session)}/attendance`);
        attendanceDetailData.items = data.items || [];

        const sm = data.summary || {};
        document.getElementById('ad-metrics').innerHTML = `
            <div class="metric-card"><span class="metric-label">Sĩ số yêu cầu</span><span class="metric-val">${sm.required || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Đủ giờ</span><span class="metric-val text-green">${sm.present || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Đi chậm</span><span class="metric-val text-amber">${sm.late || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Về sớm</span><span class="metric-val text-amber">${sm.early_leave || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Không tham gia</span><span class="metric-val text-red">${sm.absent || 0}</span></div>`;
        document.getElementById('ad-subtitle').textContent =
            `Quân số danh sách ${sm.required || 0} · ${attendanceDetailData.items.length} bản ghi`;
    } catch (e) {
        attendanceDetailData.items = [];
        document.getElementById('ad-subtitle').textContent = `Chưa có dữ liệu điểm danh: ${e.message}`;
        document.getElementById('ad-metrics').innerHTML = '';
    }

    await loadAttendanceEvidence(attendanceDetailData.session);
    renderAttendanceDetail();
}
window.openAttendanceDetail = openAttendanceDetail;

async function loadAttendanceEvidence(sessionId) {
    const box = document.getElementById('ad-evidence');
    if (!box) return;
    try {
        const checks = await getJson(`/api/v1/sessions/${encodeURIComponent(sessionId)}/checks`);
        const withPhoto = checks.filter(c => c.evidence_url);
        box.innerHTML = withPhoto.length ? '' :
            '<p class="empty-hint">Chưa có ảnh điểm danh nào được chụp</p>';
        withPhoto.forEach(c => {
            box.insertAdjacentHTML('beforeend', `
                <figure class="evidence-figure">
                    <img src="${c.evidence_url}" alt="Ảnh điểm danh ${esc(c.phase_label)}"
                         onclick="openEvidence('${c.evidence_url}','Điểm danh ${esc(c.phase_label)} — ${c.present} có mặt')">
                    <figcaption>
                        <strong>${esc(c.phase_label)}</strong> · ${esc(c.time || '')} · ${c.present} có mặt
                        <a class="btn-download" href="${c.evidence_url}" download>⬇ Tải ảnh</a>
                    </figcaption>
                </figure>`);
        });
    } catch (e) {
        box.innerHTML = '<p class="empty-hint">Chưa có ảnh điểm danh nào được chụp</p>';
    }
}

function renderAttendanceDetail() {
    const tbody = document.getElementById('ad-tbody');
    if (!tbody) return;

    const filter = (document.getElementById('ad-filter') || {}).value || 'all';
    const q = ((document.getElementById('ad-search') || {}).value || '').toLowerCase();

    let items = attendanceDetailData.items;
    if (filter !== 'all') items = items.filter(i => (i.violations || []).includes(filter));
    if (q) items = items.filter(i => {
        const p = i.person || {};
        return `${p.rank || ''} ${p.name || ''} ${p.military_id || ''}`.toLowerCase().includes(q);
    });

    tbody.innerHTML = items.length ? '' :
        `<tr><td colspan="6" class="empty-row">Không có quân nhân nào khớp bộ lọc</td></tr>`;

    items.forEach(i => {
        const p = i.person || {};
        const tags = (i.violations || []).map(v => VIOLATION_TAG[v] || v).join(' ')
            || '<span class="viol-tag viol-ok">Đủ giờ</span>';
        const extra = [];
        if (i.late_minutes) extra.push(`chậm ${i.late_minutes}′`);
        if (i.early_leave_minutes) extra.push(`về sớm ${i.early_leave_minutes}′`);

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${esc(p.rank || '')} ${esc(p.name || '')}</strong></td>
            <td class="font-mono">${esc(p.military_id || '—')}</td>
            <td>${esc(p.unit || '—')}</td>
            <td class="font-mono">${fmtTime(i.first_seen)}</td>
            <td class="font-mono">${fmtTime(i.last_seen)}</td>
            <td>${tags}${extra.length ? `<br><span class="muted">${extra.join(' · ')}</span>` : ''}</td>`;
        tbody.appendChild(tr);
    });
}
window.renderAttendanceDetail = renderAttendanceDetail;

function backFromAttendanceDetail() {
    detachStream(document.getElementById('ad-stream'));
    switchNavTab('attendance');
}
window.backFromAttendanceDetail = backFromAttendanceDetail;


// =====================================================================
// MÀN 3.1 / 5.1 — DASHBOARD GIÁM SÁT AN TOÀN BẮN ĐẠN THẬT
// =====================================================================

let activeIntrusion = null;

async function loadSafetyDashboard() {
    document.getElementById('sf-title').textContent = currentSafetyType
        ? `GIÁM SÁT AN TOÀN BẮN ĐẠN THẬT · HUẤN LUYỆN ${TRAINING_LABEL[currentSafetyType].toUpperCase()}`
        : 'TRUNG TÂM GIÁM SÁT AN TOÀN BẮN ĐẠN THẬT';
    document.getElementById('sf-subtitle').textContent =
        'PHÁT HIỆN ĐỐI TƯỢNG ĐI VÀO VÙNG CẤM CỦA TRƯỜNG BẮN THEO THỜI GIAN THỰC';

    try {
        const data = await getJson('/api/v1/summary/safety');
        const stateEl = document.getElementById('sf-state');
        stateEl.className = `safety-state-pill state-${data.state}`;
        document.getElementById('sf-state-label').textContent = data.state_label;

        const badge = document.getElementById('sf-pending-badge');
        if (badge) badge.textContent = `${data.pending_count} chờ xử lý`;
        pendingEventsCount = data.pending_count;

        const cam = (data.cameras || [])[0];
        if (cam) {
            attachStream(document.getElementById('sf-stream'), cam.id, true);
            document.getElementById('sf-camera-caption').textContent =
                `${cam.name} · ${cam.area_name || ''} · ${cam.status === 'online' ? 'đang giám sát' : 'chưa chạy'}`;
        }

        const list = document.getElementById('sf-events-list');
        list.innerHTML = '';
        if (!data.events.length) {
            list.innerHTML = '<p class="empty-hint">Chưa ghi nhận vi phạm an toàn nào</p>';
        } else {
            data.events.forEach(ev => renderEventCard(list, ev, false));
        }

        renderViolationGallery(data.events);
        setActiveIntrusion(data.active_intrusion);
    } catch (e) {
        console.error('Lỗi tải dashboard an toàn:', e);
    }
}
window.loadSafetyDashboard = loadSafetyDashboard;

function renderViolationGallery(events) {
    const gallery = document.getElementById('sf-gallery');
    if (!gallery) return;
    const withPhoto = events.filter(e => e.snapshot_url);
    gallery.innerHTML = withPhoto.length ? '' :
        '<p class="empty-hint">Thư viện trống — chưa có ảnh vi phạm nào được ghi nhận</p>';

    withPhoto.forEach(ev => {
        const when = new Date(ev.occurred_at).toLocaleString('vi-VN');
        gallery.insertAdjacentHTML('beforeend', `
            <figure class="violation-card ${ev.acked ? 'acked' : ''}">
                <img src="${ev.snapshot_url}" alt="Ảnh vi phạm an toàn"
                     onclick="openEvidence('${ev.snapshot_url}','${esc(ev.message)}')">
                <figcaption>
                    <span class="viol-time">${when}</span>
                    <span class="viol-place">${esc(ev.camera_name || '')} · ${esc((ev.detail || {}).zone_name || '')}</span>
                    <span class="viol-msg">${esc(ev.message)}</span>
                    <span class="viol-actions">
                        <a class="btn-download" href="${ev.snapshot_url}" download>⬇ Tải ảnh</a>
                        ${ev.acked
                            ? `<span class="viol-acked">✓ ${esc(ev.acked_by || 'đã xử lý')}</span>`
                            : `<button class="btn-event-confirm" onclick="ackEvent('${ev.id}')">Xác nhận xử lý</button>`}
                    </span>
                </figcaption>
            </figure>`);
    });
}

function setActiveIntrusion(event) {
    activeIntrusion = event || null;
    const banner = document.getElementById('safety-alert-banner');
    if (!banner) return;

    if (!activeIntrusion) {
        banner.style.display = 'none';
        banner.classList.remove('blinking');
        return;
    }

    document.getElementById('safety-banner-title').textContent =
        (activeIntrusion.detail || {}).zone_name
            ? `PHÁT HIỆN ĐỐI TƯỢNG TRONG ${String((activeIntrusion.detail || {}).zone_name).toUpperCase()}`
            : 'PHÁT HIỆN ĐỐI TƯỢNG TRONG VÙNG CẤM';
    document.getElementById('safety-banner-desc').textContent =
        `${activeIntrusion.message} — ${new Date(activeIntrusion.occurred_at).toLocaleTimeString('vi-VN')}`;
    banner.style.display = 'flex';
    if (!isSafetySirenMuted) banner.classList.add('blinking');
}

function onIntrusionEvent(event) {
    // Đang mở màn an toàn thì dựng lại banner và thư viện ngay
    if (currentSafetyType) {
        setActiveIntrusion(event);
        loadSafetyDashboard();
    }
}

async function ackActiveIntrusion() {
    if (!activeIntrusion) return;
    await ackEvent(activeIntrusion.id);
    setActiveIntrusion(null);
}
window.ackActiveIntrusion = ackActiveIntrusion;

function toggleSafetySiren() {
    isSafetySirenMuted = !isSafetySirenMuted;
    const btn = document.getElementById('btn-safety-siren');
    const banner = document.getElementById('safety-alert-banner');
    if (btn) btn.textContent = isSafetySirenMuted ? '🔔 Bật cảnh báo âm thanh' : '🔕 Tắt cảnh báo âm thanh';
    if (banner) banner.classList.toggle('blinking', !isSafetySirenMuted && !!activeIntrusion);
}
window.toggleSafetySiren = toggleSafetySiren;


// =====================================================================
// MÀN 8.1 — QUẢN LÝ THIẾT BỊ CAMERA
// =====================================================================

const CAMERA_STATUS_TAG = {
    online: '<span class="status-tag status-ok">Trực tuyến</span>',
    offline: '<span class="status-tag status-neutral">Ngoại tuyến</span>',
    disabled: '<span class="status-tag status-neutral">Đã tắt</span>',
    error: '<span class="status-tag status-danger">Lỗi</span>'
};

async function loadCameras() {
    const tbody = document.getElementById('cameras-tbody');
    if (!tbody) return;
    try {
        const data = await getJson('/api/v1/cameras');
        tbody.innerHTML = data.items.length ? '' :
            '<tr><td colspan="7" class="empty-row">Chưa có thiết bị camera nào</td></tr>';

        data.items.forEach(c => {
            const running = c.status === 'online';
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td class="font-mono">${esc(c.code || c.id)}</td>
                <td><strong>${esc(c.name)}</strong></td>
                <td class="font-mono source-uri">${esc(c.source_type)} · ${esc(c.source_uri || '(chưa khai)')}</td>
                <td>${esc(c.area_name || '—')}</td>
                <td>${c.target_fps || 5}</td>
                <td>${CAMERA_STATUS_TAG[c.status] || c.status}</td>
                <td class="row-actions">
                    <button class="btn-event-clip" onclick="toggleCameraRun('${c.id}', ${running})">
                        ${running ? '⏹ Dừng' : '▶ Chạy'}</button>
                    <button class="btn-event-clip" onclick='openCameraModal(${JSON.stringify(c)})'>Sửa</button>
                    <button class="btn-row-danger" onclick="deleteCamera('${c.id}','${esc(c.name)}')">Xoá</button>
                </td>`;
            tbody.appendChild(tr);
        });

        // Ô chọn camera ở màn giám sát trực tiếp
        const select = document.getElementById('monitor-camera-select');
        if (select) {
            select.innerHTML = data.items
                .map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
            select.value = activeCameraId;
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-row">Lỗi tải danh sách: ${esc(e.message)}</td></tr>`;
    }
}
window.loadCameras = loadCameras;

function openCameraModal(camera) {
    const modal = document.getElementById('camera-modal');
    if (!modal) return;
    const cam = camera || {};
    document.getElementById('camera-modal-title').textContent =
        cam.id ? 'Cập nhật thiết bị camera' : 'Thêm thiết bị camera';
    document.getElementById('cam-id').value = cam.id || '';
    document.getElementById('cam-name').value = cam.name || '';
    document.getElementById('cam-code').value = cam.code || '';
    document.getElementById('cam-area').value = cam.area_name || '';
    document.getElementById('cam-source-type').value = cam.source_type || 'rtsp';
    document.getElementById('cam-source-uri').value = cam.source_uri || '';
    document.getElementById('cam-fps').value = cam.target_fps || 5;
    document.getElementById('cam-form-status').textContent = '';
    modal.style.display = 'flex';
}
window.openCameraModal = openCameraModal;

function closeCameraModal() {
    const modal = document.getElementById('camera-modal');
    if (modal) modal.style.display = 'none';
}
window.closeCameraModal = closeCameraModal;

async function submitCameraForm(event) {
    event.preventDefault();
    const id = document.getElementById('cam-id').value;
    const status = document.getElementById('cam-form-status');
    const body = {
        name: document.getElementById('cam-name').value.trim(),
        code: document.getElementById('cam-code').value.trim() || null,
        area_name: document.getElementById('cam-area').value.trim() || null,
        source_type: document.getElementById('cam-source-type').value,
        source_uri: document.getElementById('cam-source-uri').value.trim(),
        target_fps: parseInt(document.getElementById('cam-fps').value, 10) || 5
    };

    try {
        const res = await fetch(id ? `/api/v1/cameras/${id}` : '/api/v1/cameras', {
            method: id ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        closeCameraModal();
        loadCameras();
    } catch (e) {
        status.textContent = `✗ ${e.message}`;
        status.style.color = '#dc2626';
    }
}
window.submitCameraForm = submitCameraForm;

// Backend trả 422 kèm danh sách lỗi theo từng trường; dựng lại thành câu đọc được
function describeApiError(payload) {
    const detail = payload && payload.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map(d => {
            const field = (d.loc || []).filter(x => x !== 'body').join('.');
            return field ? `${field}: ${d.msg}` : d.msg;
        }).join('; ');
    }
    return 'Dữ liệu không hợp lệ';
}

async function toggleCameraRun(cameraId, isRunning) {
    try {
        const res = await fetch(`/api/v1/cameras/${cameraId}/${isRunning ? 'stop' : 'start'}`,
                                { method: 'POST' });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        activeCameraId = cameraId;
        setTimeout(loadCameras, 800);
    } catch (e) {
        alert(e.message);
    }
}
window.toggleCameraRun = toggleCameraRun;

async function deleteCamera(cameraId, name) {
    if (!confirm(`Xoá camera "${name}"? Các vùng giám sát của nó cũng bị xoá theo.`)) return;
    try {
        const res = await fetch(`/api/v1/cameras/${cameraId}`, { method: 'DELETE' });
        if (!res.ok && res.status !== 204) throw new Error(describeApiError(await res.json()));
        loadCameras();
    } catch (e) {
        alert(e.message);
    }
}
window.deleteCamera = deleteCamera;


// ----------------- Hàm còn thiếu của modal nguồn camera -----------------

function switchMode(mode) {
    currentInputMode = mode;
    const isVideo = mode === 'video';
    document.getElementById('mode-video-btn').classList.toggle('active', isVideo);
    document.getElementById('mode-rtsp-btn').classList.toggle('active', !isVideo);
    document.getElementById('video-source-panel').style.display = isVideo ? '' : 'none';
    document.getElementById('rtsp-source-panel').style.display = isVideo ? 'none' : '';
}
window.switchMode = switchMode;

function setRtspDemo(event) {
    event.preventDefault();
    document.getElementById('rtsp-url').value =
        'rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mp4';
}
window.setRtspDemo = setRtspDemo;


// =====================================================================
// VAI TRÒ NGƯỜI DÙNG
// CBQH: theo dõi huấn luyện và quân số (phân hệ I + II).
// QTHT: có thêm phân hệ III — cấu hình camera, vùng, thời khoá biểu.
// Đây là phân quyền phía giao diện cho bản POC; backend chưa có đăng nhập nên
// không được coi là ranh giới bảo mật.
// =====================================================================

const ROLES = {
    cbqh: {
        label: 'Cán bộ quản lý',
        user: 'Đại uý Nguyễn Văn Hùng',
        avatar: 'H',
        home: 'attendance'
    },
    qtht: {
        label: 'Quản trị hệ thống',
        user: 'Thiếu tá Lê Quang Trung',
        avatar: 'T',
        home: 'monitoring'
    }
};

let currentRole = 'cbqh';

function switchRole(role) {
    if (!ROLES[role]) return;
    currentRole = role;
    const cfg = ROLES[role];

    document.querySelectorAll('.role-btn').forEach(btn => {
        btn.classList.toggle('active', btn.id === `role-btn-${role}`);
    });

    // Mục chỉ dành cho QTHT thì ẩn khi đang ở vai trò CBQH
    document.querySelectorAll('.role-only').forEach(el => {
        el.style.display = el.dataset.role === role ? '' : 'none';
    });

    const nameEl = document.getElementById('user-name');
    const avatarEl = document.getElementById('user-avatar');
    if (nameEl) nameEl.textContent = `${cfg.user} · ${cfg.label}`;
    if (avatarEl) avatarEl.textContent = cfg.avatar;

    try { localStorage.setItem('horus_role', role); } catch (e) { /* chế độ riêng tư */ }

    // Đang đứng ở màn mà vai trò mới không được xem thì đưa về màn chính
    const activeNav = document.querySelector('.sidebar-nav .nav-item.active');
    const hidden = activeNav && activeNav.closest('.role-only')
        && activeNav.closest('.role-only').style.display === 'none';
    if (hidden || !activeNav) switchNavTab(cfg.home);
}
window.switchRole = switchRole;


// =====================================================================
// KHỞI TẠO
// Đặt cuối file để mọi biến trạng thái phía trên đã được khai báo xong.
// =====================================================================

document.addEventListener('DOMContentLoaded', async () => {
    loadRegisteredFaces();
    connectEventStream();
    startLivePolling();

    // Nạp sẵn các sự kiện gần đây để dòng sự kiện không trống khi mới mở trang
    try {
        const recent = await getJson('/api/v1/events?page_size=20');
        recent.items.slice().reverse().forEach(ev => {
            lastEventId = lastEventId || ev.id;
            renderEventCard(eventsListContainer, ev, true);
        });
        pendingEventsCount = recent.items.filter(e => !e.acked).length;
        if (pendingEventsBadge) pendingEventsBadge.textContent = `${pendingEventsCount} chờ xử lý`;
    } catch (e) {
        console.error('Không nạp được sự kiện gần đây:', e);
    }

    const today = new Date().toISOString().slice(0, 10);
    const dateInput = document.getElementById('dt-schedule-date');
    if (dateInput) dateInput.value = today;

    let saved = 'cbqh';
    try { saved = localStorage.getItem('horus_role') || 'cbqh'; } catch (e) { /* chế độ riêng tư */ }
    switchRole(ROLES[saved] ? saved : 'cbqh');
    switchNavTab(ROLES[currentRole].home);
});
