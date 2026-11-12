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
const topbarAlertStat = document.getElementById('topbar-alert-stat');
const pendingEventsBadge = document.getElementById('pending-events-badge');

// Surveillance DOM Elements
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
    const sdStream = document.getElementById('sd-stream');
    if (sdStream && !sdStream.closest('.page-view').classList.contains('active')) detachStream(sdStream);
    if (tabName !== 'monitoring') detachAllCameraStreams();
    if (tabName !== 'safety-detail') detachSafetyStreams();

    const titles = {
        'schedule-progress': 'Lịch & Tiến độ huấn luyện',
        'safety': 'An toàn bắn đạn thật',
        'safety-detail': 'Chi tiết camera an toàn',
        'session-detail': 'Chi tiết lịch huấn luyện',
        'monitoring': 'Giám sát trực tiếp',
        'zones': 'Vùng giám sát',
        'cameras': 'Thiết bị camera',
        'registration': 'Đăng ký khuôn mặt',
        'schedule': 'Cấu hình giám sát',
        'logs': 'Nhật ký điểm danh',
        'violations': 'Lịch sử vi phạm'
    };
    if (currentPageTitle) currentPageTitle.textContent = titles[tabName] || 'Hệ thống Horus AI';

    if (tabName === 'registration') {
        if (currentMediaMode === 'webcam') startWebcam();
        loadRegisteredFaces().then(loadUnknownFaces);
    } else {
        stopWebcam();
    }

    if (tabName === 'zones') {
        setTimeout(async () => {
            initRoiCanvas();
            await fillZoneCameraSelect();
            await loadZoneRules();
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

    if (tabName === 'violations') {
        fillViolationCameraSelect();
        loadViolationHistory(1);
    }

    if (safetyPollTimer) { clearInterval(safetyPollTimer); safetyPollTimer = null; }
    if (cameraWallPollTimer) { clearInterval(cameraWallPollTimer); cameraWallPollTimer = null; }
    currentSafetyType = null;
    currentTabName = tabName;

    syncTrainingFilterButtons();

    switch (tabName) {
        case 'schedule-progress':
            loadTrainingSchedule();
            break;
        case 'safety':
            currentSafetyType = currentTrainingType;
            loadSafetyDashboard();
            // Màn hoạt động thời gian thực, giữ nguyên trang và tự làm mới
            safetyPollTimer = setInterval(loadSafetyDashboard, 15000);
            break;
        case 'safety-detail':
            loadSafetyDetail();
            safetyPollTimer = setInterval(loadSafetyDetail, 15000);
            break;
        case 'cameras':
            loadCameras();
            break;
        case 'monitoring':
            loadCameraWall();
            // Quét nhẹ trạng thái bật/tắt mỗi 15s — KHÔNG gắn lại luồng camera
            // không đổi gì, xem pollCameraWallStatus()
            cameraWallPollTimer = setInterval(pollCameraWallStatus, 15000);
            break;
    }
}
window.switchNavTab = switchNavTab;


// ----------------- ĐIỀU HƯỚNG THEO location.hash -----------------
// Mục menu là thẻ <a href="#...">, nên bấm chuột đã tự đổi hash; listener này
// phục vụ các đường vào khác: tải lại trang, nút back/forward, dán link kèm #.
function applyHashRoute() {
    const tab = (location.hash || '').replace(/^#/, '');
    if (!tab || tab === currentTabName) return;
    const navEl = document.getElementById(`nav-${tab}`);
    // offsetParent === null khi mục menu (hoặc tổ tiên) đang display:none —
    // dùng lại đúng cơ chế ẩn/hiện theo vai trò đã có, khỏi liệt kê lại danh sách tab
    if (!navEl || navEl.offsetParent === null) return;
    switchNavTab(tab);
}
window.addEventListener('hashchange', applyHashRoute);
window.addEventListener('load', () => {
    const appLayout = document.getElementById('app-layout');
    if (appLayout && appLayout.style.display !== 'none') applyHashRoute();
});


// ----------------- SIDEBAR: THU GỌN (≤1280) & NGĂN KÉO (≤768) -----------------
const SIDEBAR_COLLAPSE_KEY = 'horus_sidebar_collapsed';
const appSidebar = document.getElementById('app-sidebar');
const sidebarToggleBtn = document.getElementById('sidebar-toggle-btn');
const menuToggleBtn = document.getElementById('menu-toggle');
const sidebarBackdrop = document.getElementById('sidebar-backdrop');

function applySidebarCollapsed(collapsed) {
    if (!appSidebar) return;
    appSidebar.classList.toggle('collapsed', collapsed);
    if (sidebarToggleBtn) {
        sidebarToggleBtn.setAttribute('aria-expanded', String(!collapsed));
        sidebarToggleBtn.title = collapsed ? 'Mở rộng menu' : 'Thu gọn menu';
        sidebarToggleBtn.setAttribute('aria-label', sidebarToggleBtn.title);
    }
}

(function initSidebarCollapse() {
    let saved = false;
    try { saved = localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === '1'; } catch (e) { /* chế độ riêng tư */ }
    applySidebarCollapsed(saved);
})();

if (sidebarToggleBtn) {
    sidebarToggleBtn.addEventListener('click', () => {
        const collapsed = !appSidebar.classList.contains('collapsed');
        applySidebarCollapsed(collapsed);
        try { localStorage.setItem(SIDEBAR_COLLAPSE_KEY, collapsed ? '1' : '0'); } catch (e) { /* chế độ riêng tư */ }
    });
}

function openSidebarDrawer() {
    if (!appSidebar) return;
    appSidebar.classList.add('drawer-open');
    if (sidebarBackdrop) sidebarBackdrop.classList.add('show');
    if (menuToggleBtn) menuToggleBtn.setAttribute('aria-expanded', 'true');
}
function closeSidebarDrawer() {
    if (!appSidebar) return;
    appSidebar.classList.remove('drawer-open');
    if (sidebarBackdrop) sidebarBackdrop.classList.remove('show');
    if (menuToggleBtn) menuToggleBtn.setAttribute('aria-expanded', 'false');
}
window.closeSidebarDrawer = closeSidebarDrawer;

if (menuToggleBtn) {
    menuToggleBtn.addEventListener('click', () => {
        if (appSidebar && appSidebar.classList.contains('drawer-open')) closeSidebarDrawer();
        else openSidebarDrawer();
    });
}
if (sidebarBackdrop) sidebarBackdrop.addEventListener('click', closeSidebarDrawer);
// Bấm vào một mục menu trên điện thoại thì đóng luôn ngăn kéo lại
if (appSidebar) {
    appSidebar.querySelectorAll('.nav-item').forEach((el) => {
        el.addEventListener('click', closeSidebarDrawer);
    });
}
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && appSidebar && appSidebar.classList.contains('drawer-open')) closeSidebarDrawer();
});


// ----------------- TOAST: thay cho alert() chặn luồng -----------------
let toastContainer = null;

function ensureToastContainer() {
    if (toastContainer) return toastContainer;
    toastContainer = document.createElement('div');
    toastContainer.className = 'toast-container';
    toastContainer.setAttribute('role', 'status');
    toastContainer.setAttribute('aria-live', 'polite');
    document.body.appendChild(toastContainer);
    return toastContainer;
}

// type: 'success' | 'error' | 'info'
function showToast(message, type = 'info', durationMs = 4500) {
    const wrap = ensureToastContainer();
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const iconMap = { success: '✓', error: '✕', info: 'ℹ' };
    toast.innerHTML = `<span class="toast-icon" aria-hidden="true">${iconMap[type] || iconMap.info}</span>
        <span class="toast-msg">${esc(message)}</span>
        <button type="button" class="toast-close" aria-label="Đóng thông báo">×</button>`;
    toast.querySelector('.toast-close').onclick = () => toast.remove();
    wrap.appendChild(toast);
    setTimeout(() => toast.remove(), durationMs);
}
window.showToast = showToast;


// ----------------- ALERT COUNTER LOGIC -----------------
let totalAlertsCount = 0;

function incrementAlertCount() {
    totalAlertsCount++;
    if (topbarAlertStat) topbarAlertStat.textContent = `Cảnh báo: ${totalAlertsCount}`;
    const notiBadge = document.getElementById('header-noti-badge');
    if (notiBadge) notiBadge.textContent = totalAlertsCount;
}


// ----------------- BẢNG THÔNG BÁO 🔔 -----------------
// Dùng lại đúng các thẻ sự kiện đã dựng ở dòng sự kiện trực tiếp
// (events-list-container), khỏi gọi API riêng hay giữ thêm một bản state nữa.
const notificationBtn = document.getElementById('notification-btn');
const notificationPanel = document.getElementById('notification-panel');

function renderNotificationPanel() {
    const list = document.getElementById('notification-panel-list');
    if (!list) return;
    const cards = eventsListContainer ? [...eventsListContainer.querySelectorAll('.event-card')] : [];
    if (!cards.length) {
        list.innerHTML = '<p class="empty-hint">Không có sự kiện nào chờ xử lý.</p>';
        return;
    }
    list.innerHTML = cards.slice(0, 8).map((card) => {
        const desc = card.querySelector('.event-desc');
        const time = card.querySelector('.event-time');
        const place = card.querySelector('.event-location');
        const descText = desc ? desc.textContent : '';
        const metaText = [time ? time.textContent : '', place ? place.textContent : ''].filter(Boolean).join(' · ');
        return `<button type="button" class="notification-panel-item" role="menuitem" onclick="openSafetyFromNotification()">
            ${esc(descText)}
            <span class="noti-item-time">${esc(metaText)}</span>
        </button>`;
    }).join('');
}

function toggleNotificationPanel() {
    if (!notificationPanel) return;
    const opening = notificationPanel.style.display === 'none';
    if (opening) renderNotificationPanel();
    notificationPanel.style.display = opening ? 'block' : 'none';
    if (notificationBtn) notificationBtn.setAttribute('aria-expanded', String(opening));
}

function closeNotificationPanel() {
    if (!notificationPanel) return;
    notificationPanel.style.display = 'none';
    if (notificationBtn) notificationBtn.setAttribute('aria-expanded', 'false');
}

if (notificationBtn) {
    notificationBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleNotificationPanel(); });
}
// Bấm ra ngoài hoặc Esc thì đóng bảng thông báo
document.addEventListener('click', (e) => {
    if (notificationPanel && notificationPanel.style.display !== 'none'
        && !notificationPanel.contains(e.target) && e.target !== notificationBtn) {
        closeNotificationPanel();
    }
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && notificationPanel && notificationPanel.style.display !== 'none') closeNotificationPanel();
});

function openSafetyFromNotification() {
    closeNotificationPanel();
    switchNavTab('safety');
}
window.openSafetyFromNotification = openSafetyFromNotification;


// ----------------- TRÌNH PHÁT ĐOẠN GHI 10 GIÂY -----------------
// Đoạn ghi được máy chủ dựng sẵn thành mp4 lúc sự kiện xảy ra, nên chỉ cần trỏ
// thẻ <video> vào đó. Trước đây phía này tự tải về vài chục ảnh base64 rồi vẽ
// từng khung lên canvas, mà bộ đệm ảnh chỉ nằm trong RAM máy chủ: khởi động lại
// là mất sạch và người xem chỉ thấy khung đen không một dòng báo.

const clipModal = document.getElementById('clip-modal');
const clipPlayerVideo = document.getElementById('clip-player-video');
const clipEmptyHint = document.getElementById('clip-empty-hint');
const clipDownloadLink = document.getElementById('btn-clip-download');

function showClipMessage(text) {
    if (clipEmptyHint) {
        clipEmptyHint.textContent = text || '';
        clipEmptyHint.style.display = text ? 'flex' : 'none';
    }
    if (clipDownloadLink) clipDownloadLink.style.display = text ? 'none' : 'inline-flex';
}

// Tăng mỗi lần mở clip mới hoặc đóng modal — vòng chờ 202 cũ kiểm lại token
// này trước khi đụng vào DOM, để không ghi đè lên clip khác đang mở sau nó.
let _clipLoadToken = 0;

function viewEventClip(eventId) {
    if (!clipModal || !clipPlayerVideo) return;
    const token = ++_clipLoadToken;
    clipModal.style.display = 'flex';
    showClipMessage('Đang tải đoạn ghi…');

    const url = `/api/v1/events/${encodeURIComponent(eventId)}/clip`;
    if (clipDownloadLink) clipDownloadLink.href = `${url}?download=1`;

    clipPlayerVideo.onloadeddata = () => showClipMessage('');
    clipPlayerVideo.onerror = async () => {
        if (token !== _clipLoadToken) return;
        // Thẻ <video> chỉ báo "hỏng", không nói vì sao. Hỏi lại máy chủ để nói
        // đúng lý do thay vì để người xem nhìn khung đen như trước.
        let reason = 'Không tải được đoạn ghi.';
        try {
            const res = await fetch(url);
            if (res.status === 404) reason = 'Sự kiện này không còn đoạn ghi kèm.';
            else if (!res.ok) reason = `Không tải được đoạn ghi (mã ${res.status}).`;
        } catch (e) {
            reason = `Không tải được đoạn ghi: ${e.message}`;
        }
        showClipMessage(reason);
    };

    // Clip được ghi ra ngay lúc sự kiện xảy ra nên vài giây đầu máy chủ có thể
    // trả 202 {"status":"pending"} — kiểm trước bằng fetch() rồi mới gán vào
    // <video>, không thì <video> nhận thẳng JSON 202 làm nội dung và báo lỗi.
    // Thử lại mỗi 2s, tối đa 15s.
    const deadline = Date.now() + 15000;
    const tryLoad = async () => {
        if (token !== _clipLoadToken) return; // đã mở clip khác hoặc đã đóng modal
        try {
            const res = await fetch(url);
            if (token !== _clipLoadToken) return;
            if (res.status === 202) {
                if (Date.now() < deadline) {
                    showClipMessage('Clip đang xử lý, thử lại sau vài giây');
                    setTimeout(tryLoad, 2000);
                } else {
                    showClipMessage('Clip chưa sẵn sàng, vui lòng thử lại sau.');
                }
                return;
            }
            if (res.status === 404) { showClipMessage('Sự kiện này không còn đoạn ghi kèm.'); return; }
            if (!res.ok) { showClipMessage(`Không tải được đoạn ghi (mã ${res.status}).`); return; }
            clipPlayerVideo.src = url;
            clipPlayerVideo.load();
        } catch (e) {
            showClipMessage(`Không tải được đoạn ghi: ${e.message}`);
        }
    };
    tryLoad();
}
window.viewEventClip = viewEventClip;

function closeClipModal() {
    _clipLoadToken++; // huỷ mọi vòng chờ 202 còn đang chạy cho clip vừa đóng
    // Bỏ src để trình duyệt ngừng tải, không thì clip vẫn chạy ngầm sau khi đóng
    if (clipPlayerVideo) {
        clipPlayerVideo.pause();
        clipPlayerVideo.removeAttribute('src');
        clipPlayerVideo.load();
    }
    if (clipModal) clipModal.style.display = 'none';
}
window.closeClipModal = closeClipModal;


// ----------------- SURVEILLANCE & STREAM CONTROLS -----------------
function toggleSourceModal() {
    // Nạp danh sách camera mỗi lần mở, để chọn được nguồn gán cho camera nào
    if (sourceModal && sourceModal.style.display === 'none') fillSourceCameraSelect();
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
    // Gắn lại luồng của mọi camera đang chạy theo chế độ mới
    (cameraWallData || []).filter(c => c.status === 'online').forEach(cam => {
        attachStream(document.getElementById(`cam-img-${cam.id}`), cam.id, isAiOverlayEnabled, 'sub');
    });
}
window.toggleAiOverlay = toggleAiOverlay;

async function lockBaselineManual() {
    const targetCount = currentCount > 0 ? currentCount : 45;
    try {
        const res = await fetch(`/api/set-baseline?count=${targetCount}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            baselineCount = data.baseline;
            showToast(`Đã chốt sĩ số chuẩn: ${data.baseline} quân nhân`, 'success');
        }
    } catch (e) {
        showToast('Lỗi khi chốt sĩ số: ' + e.message, 'error');
    }
}
window.lockBaselineManual = lockBaselineManual;

async function startAttendanceNow() {
    try {
        const res = await fetch('/api/attendance/start', { method: 'POST' });
        const data = await res.json();
        if (data.status !== 'success') {
            showToast(data.message || 'Không mở được phiên điểm danh', 'error');
            return;
        }
        showToast(data.message || 'Đã mở phiên điểm danh', 'success');
    } catch (e) {
        showToast('Lỗi mở phiên điểm danh: ' + e.message, 'error');
    }
}
window.startAttendanceNow = startAttendanceNow;


// ---------- gán nguồn tín hiệu cho một camera ----------
// Nguồn thuộc về từng camera (trường source_uri), không phải một biến dùng
// chung. Trước đây tải video lên là gán cứng vào camera mặc định, nên có hai
// camera thì không biết nguồn vừa thêm rơi vào đâu.

function sourceTargetCamera() {
    const select = document.getElementById('source-camera-select');
    return select ? select.value : activeCameraId;
}

function onSourceCameraChange() {
    const cam = (cameraWallData || []).find(c => c.id === sourceTargetCamera());
    const hint = document.getElementById('source-camera-hint');
    if (!hint) return;

    if (!cam) {
        hint.textContent = '';
        return;
    }
    hint.textContent = cam.source_uri
        ? `Nguồn hiện tại: ${cam.source_type} · ${cam.source_uri}`
        : 'Camera này chưa khai nguồn.';
    if (cam.status === 'online') {
        hint.textContent += ' — camera đang chạy, gán nguồn mới sẽ dừng rồi chạy lại.';
    }
}
window.onSourceCameraChange = onSourceCameraChange;

async function fillSourceCameraSelect() {
    const select = document.getElementById('source-camera-select');
    if (!select) return;
    try {
        cameraWallData = (await getJson('/api/v1/cameras')).items;
    } catch (e) {
        return;
    }
    const keep = select.value;
    select.innerHTML = cameraWallData
        .map(c => `<option value="${c.id}">${esc(c.name)}${c.status === 'online' ? ' (đang chạy)' : ''}</option>`)
        .join('');
    select.value = cameraWallData.some(c => c.id === keep) ? keep : (cameraWallData[0] || {}).id || '';
    onSourceCameraChange();
}

async function assignSourceAndStart(cameraId, sourceType, sourceUri, statusEl) {
    const autostart = (document.getElementById('source-autostart-cb') || {}).checked;

    // Camera đang chạy thì phải dừng trước, nếu không nguồn mới chỉ nằm trong
    // hồ sơ mà luồng vẫn phát nguồn cũ.
    const cam = (cameraWallData || []).find(c => c.id === cameraId);
    if (cam && cam.status === 'online') {
        await fetch(`/api/v1/cameras/${cameraId}/stop`, { method: 'POST' });
        await new Promise(r => setTimeout(r, 900));
    }

    const res = await fetch(`/api/v1/cameras/${cameraId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_type: sourceType, source_uri: sourceUri })
    });
    if (!res.ok) throw new Error(describeApiError(await res.json()));

    if (autostart) {
        const started = await fetch(`/api/v1/cameras/${cameraId}/start`, { method: 'POST' });
        if (!started.ok) throw new Error(describeApiError(await started.json()));
    }

    if (statusEl) {
        statusEl.textContent = autostart
            ? '✓ Đã gán nguồn và khởi chạy camera'
            : '✓ Đã gán nguồn. Bấm Chạy trên ô camera khi cần.';
        statusEl.style.color = '#0a8f4c';
    }
    await loadCameraWall();
    await fillSourceCameraSelect();
}

// Tải video lên rồi gán cho ĐÚNG camera đang chọn. Chia nhỏ file để tránh
// giới hạn kích thước một request.
const uploadForm = document.getElementById('upload-form');
if (uploadForm) {
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fileInput = document.getElementById('video-file');
        const status = document.getElementById('upload-status');
        const file = fileInput && fileInput.files[0];

        if (!file) {
            status.textContent = '✗ Vui lòng chọn tệp video';
            status.style.color = '#dc2626';
            return;
        }

        // Camera đích phải chốt TRƯỚC khi tải: quá trình tải mất thời gian, đọc
        // lại ô chọn ở cuối thì người dùng có thể đã đổi sang camera khác.
        const targetCamera = sourceTargetCamera();
        const CHUNK_SIZE = 1024 * 1024;
        const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
        const uploadId = `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;

        status.style.color = '#0284c7';
        try {
            let videoPath = null;
            for (let i = 0; i < totalChunks; i++) {
                const form = new FormData();
                form.append('file', file.slice(i * CHUNK_SIZE, Math.min((i + 1) * CHUNK_SIZE, file.size)));
                form.append('chunk_index', i);
                form.append('total_chunks', totalChunks);
                form.append('upload_id', uploadId);
                form.append('filename', file.name);

                status.textContent = `Đang tải ${i + 1}/${totalChunks} (${Math.round(i / totalChunks * 100)}%)`;
                const res = await fetch('/api/upload_chunk', { method: 'POST', body: form });
                if (!res.ok) throw new Error(`Lỗi tải phần ${i + 1}/${totalChunks}`);

                const data = await res.json();
                if (data.status === 'error') throw new Error(data.message || 'Lỗi tải lên');
                if (data.video_path) videoPath = data.video_path;
            }

            if (!videoPath) throw new Error('Máy chủ không trả về đường dẫn tệp đã tải');

            status.textContent = 'Đã tải xong, đang gán cho camera...';
            await assignSourceAndStart(targetCamera, 'file', videoPath, status);
            setTimeout(toggleSourceModal, 900);
        } catch (err) {
            status.textContent = `✗ ${err.message}`;
            status.style.color = '#dc2626';
        }
    });
}

async function startRtspStream() {
    const input = document.getElementById('rtsp-url');
    const status = document.getElementById('rtsp-status');
    const url = input ? input.value.trim() : '';
    if (!url) {
        status.textContent = '✗ Vui lòng nhập địa chỉ RTSP';
        status.style.color = '#dc2626';
        return;
    }

    try {
        await assignSourceAndStart(sourceTargetCamera(), 'rtsp', url, status);
        setTimeout(toggleSourceModal, 900);
    } catch (e) {
        status.textContent = `✗ ${e.message}`;
        status.style.color = '#dc2626';
    }
}
window.startRtspStream = startRtspStream;

// ----------------- LUỒNG HÌNH VÀ SỰ KIỆN (SSE) -----------------
// Luồng hình giờ ưu tiên go2rtc (WebRTC/MSE, relay thẳng RTSP, lớp phủ AI vẽ
// trên <canvas> từ /detections) — xem static/player.js. Hàm attachStream/
// detachStream GIỮ NGUYÊN TÊN+CHỮ KÝ để mọi nơi gọi cũ (lưới camera, chi tiết
// ca huấn luyện, chi tiết an toàn, modal xem thử) khỏi phải sửa; bên trong chỉ
// còn là lớp mỏng gọi sang window.AiPlayer, rơi về MJPEG cũ nếu player.js vì lý
// do gì không nạp được (ví dụ trình duyệt rất cũ chặn <script type="module">).

let eventSource = null;
let statusTimer = null;
let lastEventId = null;
let activeCameraId = 'cam_01';

function streamUrl(cameraId, overlay) {
    return `/api/v1/cameras/${cameraId}/stream.mjpg?overlay=${overlay ? 1 : 0}&_=${Date.now()}`;
}

function attachStream(imgEl, cameraId, overlay, variant = 'main') {
    if (!imgEl) return;
    if (window.AiPlayer) {
        window.AiPlayer.attachPlayer(imgEl, cameraId, overlay, variant);
        return;
    }
    imgEl.src = streamUrl(cameraId, overlay);
    imgEl.onerror = () => { imgEl.removeAttribute('src'); };
}

function detachStream(imgEl) {
    if (window.AiPlayer) {
        window.AiPlayer.detachPlayer(imgEl);
        return;
    }
    // Bỏ src để trình duyệt đóng kết nối; không làm thì luồng vẫn chạy ngầm
    if (imgEl) imgEl.removeAttribute('src');
}

// ---------- lưới camera ----------
// Mỗi camera một ô, các camera chạy song song và độc lập nhau: bật hay tắt ô
// này không ảnh hưởng ô kia.

let cameraWallData = [];
// 2x2 cố định/trang (yêu cầu tester): camera thứ 5 trở đi xem ở trang khác,
// không co giãn lưới theo tổng số camera như trước.
const CAMWALL_PAGE_SIZE = 4;
let camWallPage = 0;
let cameraWallPollTimer = null;

function cameraWallTotalPages() {
    return Math.max(1, Math.ceil(cameraWallData.length / CAMWALL_PAGE_SIZE));
}

function cameraWallPageCams() {
    const start = camWallPage * CAMWALL_PAGE_SIZE;
    return cameraWallData.slice(start, start + CAMWALL_PAGE_SIZE);
}

function renderCameraWallPager() {
    const pager = document.getElementById('camera-wall-pager');
    if (!pager) return;
    const totalPages = cameraWallTotalPages();
    pager.style.display = cameraWallData.length > CAMWALL_PAGE_SIZE ? 'flex' : 'none';
    const info = document.getElementById('camwall-page-info');
    if (info) info.textContent = `Trang ${camWallPage + 1}/${totalPages}`;
    const prev = document.getElementById('camwall-prev-btn');
    const next = document.getElementById('camwall-next-btn');
    if (prev) prev.disabled = camWallPage <= 0;
    if (next) next.disabled = camWallPage >= totalPages - 1;
}

function goCameraWallPage(delta) {
    const next = Math.min(cameraWallTotalPages() - 1, Math.max(0, camWallPage + delta));
    if (next === camWallPage) return;
    camWallPage = next;
    loadCameraWall();
}
window.goCameraWallPage = goCameraWallPage;

// Dựng/cập nhật một ô camera. forceSync=false (dùng ở nhịp quét 15s) thì CHỈ
// chạm DOM/luồng khi trạng thái bật-tắt thật sự đổi — tránh giật hình/kết nối
// lại vô ích mỗi 15s cho những camera không đổi gì. forceSync=true (nạp lần
// đầu, đổi trang, bấm ▶/⏹) luôn đồng bộ lại toàn bộ.
function syncCameraTile(wall, cam, forceSync) {
    let tile = document.getElementById(`cam-tile-${cam.id}`);
    const isNew = !tile;
    if (isNew) {
        tile = document.createElement('div');
        tile.className = 'camera-tile';
        tile.id = `cam-tile-${cam.id}`;
        tile.innerHTML = `
            <div class="camera-tile-head">
                <span class="camera-tile-name"></span>
                <span class="camera-tile-status"></span>
            </div>
            <div class="camera-tile-video">
                <img id="cam-img-${cam.id}" alt="Luồng ${esc(cam.name)}">
                <div class="camera-tile-idle">Chưa chạy</div>
            </div>
            <div class="camera-tile-foot">
                <span class="camera-tile-area"></span>
                <button class="btn-event-clip"></button>
            </div>`;
        wall.appendChild(tile);
        makeZoomable(tile.querySelector('.camera-tile-video'));
    }

    const running = cam.status === 'online';
    const btn = tile.querySelector('.camera-tile-foot button');
    const statusChanged = isNew || forceSync || tile.dataset.running !== String(running);
    if (!statusChanged) return tile;

    tile.dataset.running = String(running);
    tile.classList.toggle('is-running', running);
    tile.querySelector('.camera-tile-name').textContent = cam.name;
    tile.querySelector('.camera-tile-status').innerHTML =
        CAMERA_STATUS_TAG[cam.status] || cam.status;
    tile.querySelector('.camera-tile-area').textContent = cam.area_name || '';

    // Nút có thể đang hiện "Đang dừng…/Đang chạy…" (toggleCameraRun) — tự sửa
    // lại nhãn/mở khoá ngay khi trạng thái máy chủ đã khớp với điều đang chờ.
    btn.disabled = false;
    btn.textContent = running ? '⏹ Dừng' : '▶ Chạy';
    btn.onclick = () => toggleCameraRun(cam.id, running, cam.name, btn);

    const img = document.getElementById(`cam-img-${cam.id}`);
    if (running) {
        // Chỉ gắn lại khi chưa có luồng, tránh giật hình mỗi lần làm mới
        if (!img.getAttribute('src')) attachStream(img, cam.id, isAiOverlayEnabled, 'sub');
    } else {
        detachStream(img);
    }
    return tile;
}

async function loadCameraWall() {
    const wall = document.getElementById('camera-wall');
    if (!wall) return;

    try {
        const data = await getJson('/api/v1/cameras');
        cameraWallData = data.items;
        knownCameraTotal = cameraWallData.length;
    } catch (e) {
        wall.innerHTML = `<p class="empty-hint">Không tải được danh sách camera: ${esc(e.message)}</p>`;
        return;
    }

    const running = cameraWallData.filter(c => c.status === 'online');
    const summary = document.getElementById('monitor-summary');
    if (summary) {
        summary.textContent = running.length
            ? `Đang chạy ${running.length}/${cameraWallData.length} camera`
            : `Chưa camera nào chạy · ${cameraWallData.length} thiết bị`;
    }

    if (!cameraWallData.length) {
        wall.innerHTML = '<p class="empty-hint">Chưa có thiết bị camera nào. Thêm ở màn Thiết bị camera.</p>';
        renderCameraWallPager();
        return;
    }

    // Trang hiện tại có thể lệch (ví dụ camera vừa bị xoá) — kẹp lại cho hợp lệ
    if (camWallPage >= cameraWallTotalPages()) camWallPage = cameraWallTotalPages() - 1;

    const pageCams = cameraWallPageCams();
    const pageIds = new Set(pageCams.map(c => c.id));

    // Chỉ camera của TRANG HIỆN TẠI mới có luồng. Ô nào đang hiện mà rời trang
    // (chuyển trang hoặc camera bị xoá) phải ngắt luồng rồi gỡ — bỏ quên
    // detachStream() là còn kết nối go2rtc/MJPEG chạy ngầm dù ô đã biến mất.
    [...wall.querySelectorAll('.camera-tile')].forEach(tile => {
        const id = tile.id.replace('cam-tile-', '');
        if (!pageIds.has(id)) {
            detachStream(document.getElementById(`cam-img-${id}`));
            tile.remove();
        }
    });

    pageCams.forEach(cam => syncCameraTile(wall, cam, true));
    renderCameraWallPager();
}
window.loadCameraWall = loadCameraWall;

// Quét trạng thái bật/tắt mỗi 15s khi còn ở màn giám sát (gọi từ switchNavTab).
// KHÔNG dựng lại cả lưới — chỉ ô nào đổi trạng thái mới chạm DOM/luồng, xem
// syncCameraTile(). Việc này tránh giật hình các camera không đổi gì giữa
// hai lần quét liên tiếp.
async function pollCameraWallStatus() {
    const wall = document.getElementById('camera-wall');
    if (!wall) return;

    let data;
    try {
        data = await getJson('/api/v1/cameras');
    } catch (e) {
        return; // máy chủ bận thì bỏ qua nhịp này, giữ nguyên lưới đang có
    }
    cameraWallData = data.items;
    knownCameraTotal = cameraWallData.length;

    const running = cameraWallData.filter(c => c.status === 'online');
    const summary = document.getElementById('monitor-summary');
    if (summary) {
        summary.textContent = running.length
            ? `Đang chạy ${running.length}/${cameraWallData.length} camera`
            : `Chưa camera nào chạy · ${cameraWallData.length} thiết bị`;
    }

    // Số camera đổi khiến số trang đổi (thêm/xoá thiết bị) — dựng lại đầy đủ
    // một lần cho đúng, nhịp sau mới trở lại kiểu quét nhẹ.
    if (camWallPage >= cameraWallTotalPages()) { loadCameraWall(); return; }

    cameraWallPageCams().forEach(cam => syncCameraTile(wall, cam, false));
    renderCameraWallPager();
}

// Vuốt ngang trên lưới camera để đổi trang (ngưỡng 50px theo yêu cầu). Khi đã
// zoom (scale>1), makeZoomable() tự preventDefault() lúc pointerdown nên cử
// chỉ kéo-ảnh không bị hiểu nhầm thành vuốt đổi trang.
(function setupCameraWallSwipe() {
    const wall = document.getElementById('camera-wall');
    if (!wall) return;
    let startX = null, startY = null;
    wall.addEventListener('pointerdown', (e) => { startX = e.clientX; startY = e.clientY; });
    wall.addEventListener('pointerup', (e) => {
        if (startX === null) return;
        const dx = e.clientX - startX, dy = e.clientY - startY;
        startX = null;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) goCameraWallPage(dx < 0 ? 1 : -1);
    });
    wall.addEventListener('pointercancel', () => { startX = null; });
})();

// Phím mũi tên đổi trang lưới camera (tuỳ chọn, theo yêu cầu) — chỉ khi đang ở
// màn giám sát và không gõ trong ô nhập liệu nào.
document.addEventListener('keydown', (e) => {
    if (currentTabName !== 'monitoring') return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target && e.target.tagName) || '')) return;
    if (e.key === 'ArrowLeft') goCameraWallPage(-1);
    else if (e.key === 'ArrowRight') goCameraWallPage(1);
});

function detachAllCameraStreams() {
    document.querySelectorAll('.camera-tile img').forEach(detachStream);
}

// ---------- kênh sự kiện ----------

function connectEventStream() {
    if (eventSource) return;
    const since = lastEventId ? `?since_event_id=${encodeURIComponent(lastEventId)}` : '';
    eventSource = new EventSource(`/api/v1/events/stream${since}`);

    eventSource.onmessage = (msg) => {
        try {
            const payload = JSON.parse(msg.data);
            // Cập nhật một sự kiện đã phát trước đó (gộp thêm người/đổi số liệu…)
            // có hai dạng tuỳ nơi bắn: chính sự kiện kèm cờ ``updated``, hoặc một
            // bản tin bọc riêng ``{type:'event_update', event:{...}}`` — xử lý cả
            // hai, KHÔNG tạo thẻ mới / không kêu chuông lần nữa.
            if (payload && payload.type === 'event_update' && payload.event) {
                handleEventUpdate(payload.event);
            } else if (payload && payload.updated) {
                handleEventUpdate(payload);
            } else {
                handleAiEvent(payload);
            }
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

// Định dạng tên quân nhân kèm số hiệu, dùng CHUNG cho mọi nơi hiển thị danh
// sách người (thẻ sự kiện, lịch sử vi phạm, danh sách vắng điểm danh...).
// Chấp nhận một chuỗi tên có sẵn (dữ liệu cũ hoặc server đã gộp sẵn), hoặc
// một object — tên trường có thể là {name, rank, military_id} (điểm danh)
// hoặc {person_name, person_rank, person_military_id} (sự kiện) — chấp nhận
// cả hai vì backend đặt tên trường khác nhau tuỳ API.
function formatPersonLabel(p) {
    if (p == null) return '';
    if (typeof p === 'string') return p;
    const rank = p.rank || p.person_rank || '';
    const name = p.name || p.person_name || '';
    const militaryId = p.military_id || p.person_military_id || '';
    const label = [rank, name].filter(Boolean).join(' ').trim() || militaryId;
    if (!militaryId || label.includes(`(${militaryId})`)) return label;
    return `${label} (${militaryId})`;
}
window.formatPersonLabel = formatPersonLabel;

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

// Cập nhật một sự kiện ĐÃ phát trước đó (ví dụ gộp thêm người vào cùng một
// cảnh báo vùng cấm) — chỉ sửa chữ trong thẻ/dòng đang có, không tạo thẻ mới,
// không tăng số đếm cảnh báo / không kêu chuông hay bật toast lần nữa.
function handleEventUpdate(event) {
    if (!event || !event.id) return;

    const detail = event.detail || {};
    const names = (detail.identified || []).map(p => formatPersonLabel(p)).filter(Boolean);
    if (!names.length && event.person_name) names.push(formatPersonLabel(event));
    const personText = names.length ? names.join(', ') : 'Không xác định';

    // Dòng trực tiếp ở màn giám sát
    const liveCard = document.getElementById(`evt-${event.id}`);
    if (liveCard) {
        const desc = liveCard.querySelector('.event-desc');
        if (desc) desc.textContent = event.message;
    }

    // Trang "Lịch sử vi phạm", nếu dòng đó đang hiển thị trên trang hiện tại
    const histRow = document.getElementById(`vh-${event.id}`);
    if (histRow) {
        const zoneCell = histRow.children[2];
        const personCell = histRow.children[3];
        if (personCell) personCell.textContent = personText;
        if (zoneCell) {
            const zoneKindLabel = ZONE_RULE_LABELS[detail.zone_rule] || '';
            zoneCell.textContent = [detail.zone_name, zoneKindLabel].filter(Boolean).join(' · ') || event.area_name || '—';
        }
    }
}

function renderEventCard(container, event, prepend) {
    if (!container) return;

    // Dòng sự kiện trực tiếp chỉ để việc còn phải làm. Xử lý xong thì biến mất
    // khỏi đây; muốn tra lại thì có nhật ký vi phạm ở màn chi tiết camera.
    if (container.id === 'events-list-container' && event.acked) return;

    const hint = container.querySelector('.empty-hint');
    if (hint) hint.remove();

    const card = document.createElement('div');
    card.className = `event-card ${EVENT_CLASS[event.type] || 'event-system'}`;
    card.id = `evt-${event.id}`;

    const time = new Date(event.occurred_at).toLocaleTimeString('vi-VN');
    const place = [event.camera_name, event.area_name].filter(Boolean).join(' · ');
    const clipBtn = event.clip_url
        ? `<button class="btn-event-clip" onclick="viewEventClip('${event.id}')">▶️ Xem clip 10s</button>` : '';
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
window.renderEventCard = renderEventCard;

// ---------- trang riêng "Lịch sử vi phạm" ----------
// Dòng trực tiếp (events-list-container, ở màn giám sát) chỉ hiện sự kiện
// CHƯA xử lý và mất đi ngay khi xác nhận — trang này tra lại toàn bộ qua
// /api/v1/events, lọc đúng loại vi phạm an toàn (INTRUSION), có phân trang
// server-side + lọc theo khoảng ngày / trạng thái / camera / loại vùng.
const VIOLATION_EVENT_TYPES = ['INTRUSION'];
const VH_PAGE_SIZE = 20;
let vhPage = 1;
let vhTotalPages = 1;
let vhCameras = [];

function violationHistoryVisible() {
    const view = document.getElementById('view-violations');
    return !!view && view.classList.contains('active');
}

async function fillViolationCameraSelect() {
    const select = document.getElementById('vh-filter-camera');
    if (!select) return;
    try {
        vhCameras = (await getJson('/api/v1/cameras')).items || [];
    } catch (e) {
        vhCameras = [];
    }
    const current = select.value;
    select.innerHTML = '<option value="">Tất cả camera</option>'
        + vhCameras.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
    select.value = vhCameras.some(c => c.id === current) ? current : '';
}
window.fillViolationCameraSelect = fillViolationCameraSelect;

// Mặc định từ ngày = hôm nay - 7, đến ngày = hôm nay — chỉ áp khi ô ngày
// đang trống (không ghi đè lựa chọn người dùng mỗi lần vào lại trang).
function defaultViolationHistoryDates() {
    const fromEl = document.getElementById('vh-date-from');
    const toEl = document.getElementById('vh-date-to');
    const fmt = (d) => d.toISOString().slice(0, 10);
    const today = serverNow();
    if (toEl && !toEl.value) toEl.value = fmt(today);
    if (fromEl && !fromEl.value) {
        const from = new Date(today);
        from.setDate(from.getDate() - 7);
        fromEl.value = fmt(from);
    }
}

function resetViolationHistoryFilters() {
    const ids = ['vh-filter-status', 'vh-filter-camera', 'vh-filter-kind'];
    ids.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    const fromEl = document.getElementById('vh-date-from');
    const toEl = document.getElementById('vh-date-to');
    if (fromEl) fromEl.value = '';
    if (toEl) toEl.value = '';
    defaultViolationHistoryDates();
    loadViolationHistory(1);
}
window.resetViolationHistoryFilters = resetViolationHistoryFilters;

async function loadViolationHistory(page) {
    const tbody = document.getElementById('violations-tbody');
    if (!tbody) return;
    vhPage = page || vhPage || 1;
    defaultViolationHistoryDates();

    const val = (id) => ((document.getElementById(id) || {}).value || '').trim();
    const params = new URLSearchParams();
    params.set('type', VIOLATION_EVENT_TYPES.join(','));
    if (val('vh-filter-status')) params.set('acked', val('vh-filter-status'));
    if (val('vh-filter-camera')) params.set('camera_id', val('vh-filter-camera'));
    if (val('vh-filter-kind')) params.set('zone_rule', val('vh-filter-kind'));
    if (val('vh-date-from')) params.set('date_from', val('vh-date-from'));
    if (val('vh-date-to')) params.set('date_to', val('vh-date-to'));
    params.set('page', String(vhPage));
    params.set('page_size', String(VH_PAGE_SIZE));

    tbody.innerHTML = '<tr><td colspan="10" class="empty-row">Đang tải lịch sử vi phạm…</td></tr>';
    try {
        const res = await fetch(`/api/v1/events?${params.toString()}`);
        if (!res.ok) throw new Error(res.statusText);
        const data = await res.json();
        vhTotalPages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || VH_PAGE_SIZE)));
        if (vhPage > vhTotalPages) { vhPage = vhTotalPages; return loadViolationHistory(vhPage); }
        renderViolationHistory(data.items || []);
        renderViolationHistoryPager();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="10" class="empty-row">Không tải được lịch sử vi phạm: ${esc(e.message)}</td></tr>`;
    }
}
window.loadViolationHistory = loadViolationHistory;

function renderViolationHistoryPager() {
    const pager = document.getElementById('vh-pager');
    if (!pager) return;
    pager.style.display = vhTotalPages > 1 ? 'flex' : 'none';
    const info = document.getElementById('vh-page-info');
    if (info) info.textContent = `Trang ${vhPage}/${vhTotalPages}`;
    const prev = document.getElementById('vh-prev-btn');
    const next = document.getElementById('vh-next-btn');
    if (prev) prev.disabled = vhPage <= 1;
    if (next) next.disabled = vhPage >= vhTotalPages;
}

function goViolationHistoryPage(delta) {
    const next = Math.min(vhTotalPages, Math.max(1, vhPage + delta));
    if (next === vhPage) return;
    loadViolationHistory(next);
}
window.goViolationHistoryPage = goViolationHistoryPage;

const ZONE_RULE_LABELS = { restricted_area: 'Vùng cấm', crossing_line: 'Vạch an toàn' };

function renderViolationHistory(items) {
    const tbody = document.getElementById('violations-tbody');
    if (!tbody) return;
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty-row">Không có vi phạm nào phù hợp.</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    items.forEach(event => {
        const detail = event.detail || {};
        const names = (detail.identified || []).map(p => formatPersonLabel(p)).filter(Boolean);
        if (!names.length && event.person_name) names.push(formatPersonLabel(event));
        const personText = names.length ? names.join(', ') : 'Không xác định';
        const zoneKindLabel = ZONE_RULE_LABELS[detail.zone_rule] || '';
        const zoneText = [detail.zone_name, zoneKindLabel].filter(Boolean).join(' · ') || event.area_name || '—';

        const imgCell = event.snapshot_url
            ? `<img class="evidence-thumb" loading="lazy" src="${event.snapshot_url}"
                    onclick="openEvidence('${event.snapshot_url}','${esc(event.message).replace(/'/g, "\\'")}')" alt="Ảnh vi phạm">`
            : '<span style="color:#94a3b8;">—</span>';
        const clipCell = event.clip_url
            ? `<button class="btn-event-clip" onclick="viewEventClip('${event.id}')">▶️ Xem clip</button>`
            : '<span style="color:#94a3b8;">—</span>';
        const statusBadge = event.acked
            ? '<span class="event-history-badge acked">Đã xử lý</span>'
            : '<span class="event-history-badge unacked">Chưa xử lý</span>';
        const actionCell = event.acked ? '<span style="color:#94a3b8;">—</span>'
            : `<button class="btn-event-confirm" onclick="ackEvent('${event.id}')">Xác nhận xử lý</button>`;

        const row = document.createElement('tr');
        row.id = `vh-${event.id}`;
        row.innerHTML = `
            <td class="font-mono">${esc(new Date(event.occurred_at).toLocaleString('vi-VN'))}</td>
            <td>${esc(event.camera_name || '—')}</td>
            <td>${esc(zoneText)}</td>
            <td>${esc(personText)}</td>
            <td>${imgCell}</td>
            <td>${clipCell}</td>
            <td>${statusBadge}</td>
            <td>${esc(event.acked_by || '—')}</td>
            <td class="font-mono">${event.acked_at ? esc(new Date(event.acked_at).toLocaleString('vi-VN')) : '—'}</td>
            <td class="row-actions">${actionCell}</td>
        `;
        tbody.appendChild(row);
    });
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

        // Ở dòng sự kiện trực tiếp thì gỡ hẳn; ở danh sách khác thì đổi nút
        document.querySelectorAll(`#evt-${eventId}`).forEach(el => {
            if (el.closest('#events-list-container')) {
                el.remove();
            } else {
                const actions = el.querySelector('.event-card-actions');
                if (actions) actions.innerHTML =
                    `<button class="btn-event-processed" disabled>✓ ${updated.acked_by}</button>`;
            }
        });

        const feed = document.getElementById('events-list-container');
        if (feed && !feed.querySelector('.event-card')) {
            feed.innerHTML = '<p class="empty-hint">Không còn sự kiện nào chờ xử lý.</p>';
        }
        pendingEventsCount = Math.max(0, pendingEventsCount - 1);
        if (pendingEventsBadge) pendingEventsBadge.textContent = `${pendingEventsCount} chờ xử lý`;
        if (currentTabName === 'safety') loadSafetyDashboard();
        else if (currentTabName === 'safety-detail') loadSafetyDetail();
        // Trang "Lịch sử vi phạm" dùng badge trạng thái riêng (không phải thẻ
        // #evt-), và có thể đang lọc "Chưa xử lý" nên món vừa xử lý phải biến
        // mất khỏi danh sách — nạp lại trang hiện tại là chắc chắn nhất.
        if (violationHistoryVisible()) loadViolationHistory(vhPage);
    } catch (e) {
        showToast('Không xác nhận được: ' + e.message, 'error');
    }
}
window.ackEvent = ackEvent;

// ---------- chỉ số trực tiếp ----------
// MJPEG chỉ mang hình, các con số lấy bằng cách hỏi máy chủ định kỳ.

// Tổng số thiết bị camera. Chỉ đổi khi thêm / xoá camera, nên không việc gì
// phải hỏi lại máy chủ mỗi nhịp 2 giây của pollLiveStatus.
let knownCameraTotal = 0;

async function refreshCameraTotal() {
    try { knownCameraTotal = (await getJson('/api/v1/cameras')).total; } catch (e) { /* bỏ qua nhịp này */ }
}

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

        const camPill = document.getElementById('topbar-camera-stat');
        if (camPill) {
            const online = (status.cameras_running || []).length;
            camPill.textContent = `Camera: ${online}/${knownCameraTotal} trực tuyến`;
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
        showToast('Vui lòng bật webcam trước khi chụp.', 'info');
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
        showToast('Vui lòng điền đầy đủ các trường thông tin', 'info');
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
            showToast('Vui lòng chụp ảnh khuôn mặt từ webcam trước khi lưu.', 'info');
            return;
        }
        formData.append('image_base64', capturedSnapshotBase64);
    } else {
        const file = photoUploadInput.files[0];
        if (!file && !capturedSnapshotBase64) {
            showToast('Vui lòng tải lên ảnh chân dung rõ nét', 'info');
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


// ----------------- MẪU KHUÔN MẶT BỔ SUNG -----------------
let faceSampleTarget = null;

function pickFaceSample(personId) {
    faceSampleTarget = personId;
    const input = document.getElementById('face-sample-file');
    if (input) { input.value = ''; input.click(); }
}
window.pickFaceSample = pickFaceSample;

async function uploadFaceSample(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file || !faceSampleTarget) return;
    const form = new FormData();
    form.append('image', file);
    try {
        const res = await fetch(`/api/faces/${encodeURIComponent(faceSampleTarget)}/samples`, { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Không thêm được mẫu');
        showToast(data.message, 'success');
        loadRegisteredFaces();
    } catch (e) {
        showToast(e.message, 'error');
    }
}
window.uploadFaceSample = uploadFaceSample;

async function loadUnknownFaces() {
    const grid = document.getElementById('unknown-faces-grid');
    if (!grid) return;
    let items = [];
    try {
        const res = await fetch('/api/faces/unknown');
        if (res.ok) items = (await res.json()).data || [];
    } catch (e) {
        console.error('Error fetching unknown faces:', e);
    }
    document.getElementById('unknown-faces-count').textContent = items.length;
    document.getElementById('unknown-faces-empty').style.display = items.length ? 'none' : 'block';
    const options = registeredPersonnel
        .map(p => `<option value="${esc(p.id)}">${esc(`${p.rank || ''} ${p.name || ''}`.trim())}</option>`)
        .join('');
    grid.innerHTML = items.map(it => {
        const t = new Date(it.last_seen);
        const when = isNaN(t) ? '' : t.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
        const imgs = Array.from({ length: it.samples }, (_, i) =>
            `<img src="/api/faces/unknown/${it.id}/image?i=${i}" data-i="${i}" alt="Khuôn mặt chưa xác định" title="Bấm để bỏ/chọn ảnh này" loading="lazy" onclick="this.classList.toggle('excluded')">`).join('');
        return `
            <div class="unknown-face" data-id="${it.id}">
                <div class="unknown-face-imgs">${imgs}</div>
                <div class="unknown-face-meta">${esc(it.camera_id)} · ${when} · mặt ${it.face_width}px${it.tracks > 1 ? ` · ${it.tracks} lượt xuất hiện` : ''}</div>
                <div class="unknown-face-actions">
                    <select aria-label="Chọn quân nhân"><option value="">— Là ai? —</option>${options}</select>
                    <button type="button" class="btn-primary-green btn-sm" onclick="assignUnknownFace('${it.id}', this)">Gán</button>
                </div>
            </div>`;
    }).join('');
}
window.loadUnknownFaces = loadUnknownFaces;

async function assignUnknownFace(uid, btn) {
    const card = btn.closest('.unknown-face');
    const personId = card.querySelector('select').value;
    if (!personId) { showToast('Chọn quân nhân trước khi gán', 'warning'); return; }
    const chosen = [...card.querySelectorAll('.unknown-face-imgs img:not(.excluded)')].map(img => img.dataset.i);
    if (!chosen.length) { showToast('Đã bỏ hết ảnh — chọn lại ít nhất một ảnh', 'warning'); return; }
    const form = new FormData();
    form.append('unknown_id', uid);
    form.append('indices', chosen.join(','));
    btn.disabled = true;
    try {
        const res = await fetch(`/api/faces/${encodeURIComponent(personId)}/samples`, { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Không gán được');
        showToast(data.message, 'success');
        card.remove();
        loadRegisteredFaces();
        const left = document.querySelectorAll('#unknown-faces-grid .unknown-face').length;
        document.getElementById('unknown-faces-count').textContent = left;
        if (!left) document.getElementById('unknown-faces-empty').style.display = 'block';
    } catch (e) {
        showToast(e.message, 'error');
        btn.disabled = false;
    }
}
window.assignUnknownFace = assignUnknownFace;

// ----------------- C: LẤY MẪU KHUÔN MẶT TỪ CAMERA (hồ sơ quân nhân) -----------------
// POST /api/faces/capture chặn ~seconds giây rồi trả về một item CÙNG hình
// dạng với phần tử của GET /api/faces/unknown (id, samples, ...) — tái dùng
// luôn cách hiển thị/loại ảnh của "Mặt chưa xác định từ camera" ở trên, rồi
// "Lưu mẫu" gọi lại đúng POST /api/faces/{person_id}/samples đã có.
let captureResultItem = null; // item vừa chụp, còn chờ bấm "Lưu mẫu"
let captureCountdownTimer = null;
// request /api/faces/capture chặn ~20s không huỷ được giữa đường — đổi hồ sơ
// (hoặc gọi lại) trong lúc chờ thì tăng số này để bản trả về SAU không còn
// khớp, tránh gán nhầm ảnh chụp cho hồ sơ cũ vào hồ sơ đang mở.
let captureRequestSeq = 0;

function resetPersonCapturePanel() {
    captureRequestSeq += 1;
    if (captureCountdownTimer) { clearInterval(captureCountdownTimer); captureCountdownTimer = null; }
    captureResultItem = null;
    const panel = document.getElementById('person-capture-panel');
    if (panel) panel.style.display = 'none';
    const status = document.getElementById('capture-status');
    if (status) status.textContent = '';
    const imgsBox = document.getElementById('capture-result-imgs');
    if (imgsBox) imgsBox.innerHTML = '';
    const saveBtn = document.getElementById('capture-save-btn');
    if (saveBtn) saveBtn.style.display = 'none';
}

async function fillCaptureCameraSelect() {
    const select = document.getElementById('capture-camera-select');
    if (!select) return;
    try {
        select.innerHTML = ((await getJson('/api/v1/cameras')).items || [])
            .map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
    } catch (e) {
        select.innerHTML = '<option value="">Không tải được danh sách camera</option>';
    }
}

function togglePersonCapture() {
    const panel = document.getElementById('person-capture-panel');
    if (!panel) return;
    const show = panel.style.display === 'none';
    panel.style.display = show ? 'block' : 'none';
    if (show) fillCaptureCameraSelect();
}
window.togglePersonCapture = togglePersonCapture;

async function startPersonCapture() {
    const cameraId = (document.getElementById('capture-camera-select') || {}).value;
    const seconds = parseInt((document.getElementById('capture-seconds') || {}).value, 10) || 20;
    const status = document.getElementById('capture-status');
    const startBtn = document.getElementById('capture-start-btn');
    const saveBtn = document.getElementById('capture-save-btn');
    const imgsBox = document.getElementById('capture-result-imgs');
    if (!cameraId) { showToast('Chọn camera trước khi chụp', 'warning'); return; }

    const seq = ++captureRequestSeq;
    captureResultItem = null;
    if (saveBtn) saveBtn.style.display = 'none';
    if (imgsBox) imgsBox.innerHTML = '';
    if (startBtn) startBtn.disabled = true;

    // Đếm ngược trong lúc chờ server chặn ~seconds giây để chụp — chỉ để báo
    // tiến độ, không huỷ được request đang chạy (giống lúc chụp điểm danh).
    let remaining = seconds;
    const tick = () => {
        if (status) {
            status.textContent = remaining > 0
                ? `Đang chụp… còn ${remaining}s — quân nhân đứng/đi chậm trong khung hình, nhìn về phía camera.`
                : 'Đang xử lý ảnh vừa chụp…';
        }
        remaining -= 1;
    };
    tick();
    const timerId = setInterval(tick, 1000);
    captureCountdownTimer = timerId;

    const form = new FormData();
    form.append('camera_id', cameraId);
    form.append('seconds', String(seconds));
    try {
        const res = await fetch('/api/faces/capture', { method: 'POST', body: form });
        const data = await res.json();
        if (seq !== captureRequestSeq) return; // hồ sơ đã đổi/đã reset trong lúc chờ — bỏ kết quả trễ này
        if (!res.ok) throw new Error(data.detail || data.message || 'Không chụp được mẫu từ camera');

        const item = data.data || {};
        captureResultItem = item;
        if (status) status.textContent = `✓ Đã chụp ${item.samples || 0} ảnh — bỏ ảnh sai rồi bấm "Lưu mẫu".`;
        if (imgsBox) {
            imgsBox.innerHTML = Array.from({ length: item.samples || 0 }, (_, i) =>
                `<img src="/api/faces/unknown/${item.id}/image?i=${i}" data-i="${i}"
                      alt="Ảnh mẫu vừa chụp" title="Bấm để bỏ/chọn ảnh này" loading="lazy"
                      onclick="this.classList.toggle('excluded')">`).join('');
        }
        if (saveBtn) saveBtn.style.display = (item.samples || 0) ? 'inline-flex' : 'none';
    } catch (e) {
        if (seq === captureRequestSeq && status) status.textContent = `✗ ${e.message}`;
    } finally {
        clearInterval(timerId);
        // Chỉ bỏ biến đếm giờ CHUNG nếu nó vẫn đang là của lần chụp này — một
        // lần chụp mới hơn (đổi hồ sơ/bấm lại) có thể đã gán biến đếm khác.
        if (captureCountdownTimer === timerId) captureCountdownTimer = null;
        if (seq === captureRequestSeq && startBtn) startBtn.disabled = false;
    }
}
window.startPersonCapture = startPersonCapture;

async function savePersonCapture() {
    const personId = (document.getElementById('person-id') || {}).value;
    const imgsBox = document.getElementById('capture-result-imgs');
    const saveBtn = document.getElementById('capture-save-btn');
    const status = document.getElementById('capture-status');
    if (!personId || !captureResultItem) return;

    const chosen = [...(imgsBox ? imgsBox.querySelectorAll('img:not(.excluded)') : [])].map(img => img.dataset.i);
    if (!chosen.length) { showToast('Đã bỏ hết ảnh — chọn lại ít nhất một ảnh', 'warning'); return; }

    const form = new FormData();
    form.append('unknown_id', captureResultItem.id);
    form.append('indices', chosen.join(','));
    if (saveBtn) saveBtn.disabled = true;
    try {
        const res = await fetch(`/api/faces/${encodeURIComponent(personId)}/samples`, { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Không lưu được mẫu');
        showToast(data.message, 'success');
        if (status) status.textContent = '';
        if (imgsBox) imgsBox.innerHTML = '';
        if (saveBtn) saveBtn.style.display = 'none';
        captureResultItem = null;
        await loadRegisteredFaces(); // cập nhật lại số mẫu (+N mẫu) ở bảng danh sách
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        if (saveBtn) saveBtn.disabled = false;
    }
}
window.savePersonCapture = savePersonCapture;

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

        row.className = 'row-clickable';
        row.title = 'Bấm để xem và sửa hồ sơ';
        row.onclick = (ev) => { if (!ev.target.closest('.action-btn-group')) editPerson(p.id); };
        row.innerHTML = `
            <td>${avatarHtml}</td>
            <td><strong>${p.name}</strong>${p.sample_count ? `<br><small class="sample-count" title="Mẫu khuôn mặt bổ sung (từ camera / ảnh thêm / tự học)">+${p.sample_count} mẫu</small>` : ''}</td>
            <td><span class="military-id-tag">${p.military_id}</span></td>
            <td>${p.rank || '-'}</td>
            <td>${p.unit || '-'}</td>
            <td>${p.created_at || '01/08/2026'}</td>
            <td><span class="status-tag status-active">${p.status || 'Active'}</span></td>
            <td>
                <div class="action-btn-group">
                    <button class="icon-btn" title="Chỉnh sửa" aria-label="Chỉnh sửa" onclick="editPerson('${p.id}')">✏️</button>
                    <button class="icon-btn" title="Thêm ảnh mẫu khuôn mặt (ảnh khác góc / ảnh từ camera)" aria-label="Thêm ảnh mẫu" onclick="pickFaceSample('${p.id}')">🖼️</button>
                    <button class="icon-btn icon-btn-delete" title="Xoá" aria-label="Xoá quân nhân" onclick="deletePerson('${p.id}')">🗑️</button>
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
    if (!confirm('Bạn có chắc chắn muốn xoá quân nhân này khỏi cơ sở dữ liệu Face ID?')) return;
    try {
        const res = await fetch(`/api/faces/${personId}`, { method: 'DELETE' });
        if (res.ok) {
            await loadRegisteredFaces();
        } else {
            showToast('Lỗi khi xoá quân nhân', 'error');
        }
    } catch (e) {
        showToast('Lỗi: ' + e.message, 'error');
    }
}
window.deletePerson = deletePerson;

function editPerson(personId) {
    const p = registeredPersonnel.find(x => x.id === personId);
    if (!p) return;

    const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
    document.getElementById('person-modal-title').textContent =
        `${p.rank || ''} ${p.name || ''}`.trim() || 'Hồ sơ quân nhân';
    set('person-id', p.id);
    set('person-name', p.name || '');
    set('person-military-id', p.military_id || '');
    set('person-rank', p.rank || 'Binh nhất');
    set('person-unit', p.unit || 'Đại đội 1');
    set('person-status', p.status || 'Active');

    const avatar = document.getElementById('person-avatar-lg');
    avatar.innerHTML = p.avatar_path
        ? `<img src="${p.avatar_path}" alt="${esc(p.name || '')}">`
        : (p.name || 'Q').trim().charAt(0).toUpperCase();
    document.getElementById('person-registered-at').textContent =
        `Đăng ký: ${p.created_at || p.registered_at || '—'}`;
    document.getElementById('person-form-status').textContent = '';
    resetPersonCapturePanel(); // đổi hồ sơ thì bỏ dở kết quả chụp của hồ sơ trước

    document.getElementById('person-modal').style.display = 'flex';
}
window.editPerson = editPerson;

function closePersonModal() {
    const modal = document.getElementById('person-modal');
    if (modal) modal.style.display = 'none';
}
window.closePersonModal = closePersonModal;

async function submitPersonForm(event) {
    event.preventDefault();
    const id = document.getElementById('person-id').value;
    const status = document.getElementById('person-form-status');
    const val = (elId) => (document.getElementById(elId) || {}).value;

    // /api/faces nhận multipart; chỉ gửi thông tin hồ sơ, ảnh và đặc trưng
    // khuôn mặt giữ nguyên.
    const form = new FormData();
    form.append('name', (val('person-name') || '').trim());
    form.append('military_id', (val('person-military-id') || '').trim());
    form.append('rank', val('person-rank'));
    form.append('unit', val('person-unit'));
    form.append('status', val('person-status'));

    try {
        const res = await fetch(`/api/faces/${id}`, { method: 'PUT', body: form });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        closePersonModal();
        loadRegisteredFaces();
    } catch (e) {
        status.textContent = `✗ ${e.message}`;
        status.style.color = '#dc2626';
    }
}
window.submitPersonForm = submitPersonForm;

async function deletePersonFromModal() {
    const id = document.getElementById('person-id').value;
    const name = document.getElementById('person-name').value;
    if (!confirm(`Xoá quân nhân "${name}" khỏi cơ sở dữ liệu sinh trắc học?`)) return;
    try {
        const res = await fetch(`/api/faces/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        closePersonModal();
        loadRegisteredFaces();
    } catch (e) {
        showToast(e.message, 'error');
    }
}
window.deletePersonFromModal = deletePersonFromModal;

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
        const res = await fetch(`/api/v1/cameras/${zoneTargetCamera()}/snapshot?overlay=0&_=${Date.now()}`);
        if (!res.ok) {
            const cam = (cameraWallData || []).find(c => c.id === zoneTargetCamera());
            showToast(`Camera "${(cam && cam.name) || zoneTargetCamera()}" chưa chạy nên chưa có khung hình. `
                  + 'Bấm Chạy trên ô camera đó rồi lấy lại khung hình.', 'info');
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

// Mỗi camera có thể có nhiều vùng: một vùng đếm quân số, các vùng cấm của
// trường bắn, và vạch an toàn. Loại vùng quyết định AI xử lý thế nào.

let zonesOnCamera = [];
// Camera đang được vẽ vùng. Tách riêng khỏi activeCameraId để chọn camera ở
// màn vùng không làm đổi camera đang xem ở màn khác.
let zoneCameraId = null;

function zoneTargetCamera() {
    return zoneCameraId || activeCameraId;
}

async function fillZoneCameraSelect() {
    const select = document.getElementById('zone-camera-select');
    if (!select) return;
    try {
        cameraWallData = (await getJson('/api/v1/cameras')).items;
    } catch (e) {
        return;
    }
    if (!zoneCameraId && cameraWallData.length) zoneCameraId = cameraWallData[0].id;

    select.innerHTML = cameraWallData
        .map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
    select.value = zoneTargetCamera();

    const cam = cameraWallData.find(c => c.id === zoneTargetCamera());
    const label = document.getElementById('roi-canvas-label');
    if (label && cam) {
        label.textContent = `KHUNG HÌNH TĨNH · ${cam.name.toUpperCase()}`
            + (cam.status === 'online' ? '' : ' · CHƯA CHẠY');
    }
}

async function switchZoneCamera(cameraId) {
    zoneCameraId = cameraId;
    resetZoneForm();
    await fillZoneCameraSelect();
    await loadZoneRules();
    // Ảnh nền phải là khung hình của chính camera đang vẽ
    captureFrameForRoi();
}
window.switchZoneCamera = switchZoneCamera;

const ZONE_RULE_META = {
    attendance_area: {
        label: 'Đếm quân số', cls: 'zone-att', kind: 'polygon',
        hint: 'Chỉ những người đứng trong vùng này mới được tính vào sĩ số. Mỗi camera chỉ được một vùng loại này.'
    },
    restricted_area: {
        label: 'Vùng cấm', cls: 'zone-res', kind: 'polygon',
        hint: 'Dùng cho khối chắn trường bắn. Có người vào là sinh cảnh báo đỏ kèm ảnh chụp khoanh đối tượng.'
    },
    crossing_line: {
        label: 'Vạch an toàn', cls: 'zone-line', kind: 'tripwire',
        hint: 'Cảnh báo khi có người cắt qua vạch. Vẽ đúng 2 điểm.'
    }
};

function onZoneRuleChange() {
    const rule = document.getElementById('zone-rule-type').value;
    const meta = ZONE_RULE_META[rule];
    document.getElementById('zone-rule-hint').textContent = meta.hint;
    // Vạch thì chuyển sang chế độ vẽ 2 điểm, vùng kín thì vẽ đa giác
    setDrawingMode(meta.kind);
}
window.onZoneRuleChange = onZoneRuleChange;

async function loadZoneRules() {
    try {
        zonesOnCamera = await getJson(`/api/v1/cameras/${zoneTargetCamera()}/zones`);
    } catch (e) {
        zonesOnCamera = [];
    }
    renderZoneList();

    // Vẽ sẵn vùng đếm quân số để thấy bối cảnh khi thêm vùng khác
    const att = zonesOnCamera.find(z => z.rule === 'attendance_area');
    if (att && !document.getElementById('zone-edit-id').value) {
        polygonPoints = att.points.map(p => ({ ...p }));
        redrawRoiCanvas();
        updateCoordsJsonDisplay();
    }
}
window.loadZoneRules = loadZoneRules;

function renderZoneList() {
    const box = document.getElementById('zone-list');
    if (!box) return;

    if (!zonesOnCamera.length) {
        box.innerHTML = '<p class="empty-hint">Chưa có vùng nào. Vẽ trên khung hình rồi bấm Lưu vùng.</p>';
        return;
    }

    box.innerHTML = zonesOnCamera.map(z => {
        const meta = ZONE_RULE_META[z.rule] || { label: z.rule, cls: '' };
        return `
        <div class="zone-row ${z.enabled ? '' : 'zone-off'}">
            <div class="zone-row-main">
                <span class="zone-tag ${meta.cls}">${meta.label}</span>
                <strong>${esc(z.name)}</strong>
                <span class="muted">${z.points.length} điểm${z.enabled ? '' : ' · đang tắt'}</span>
            </div>
            <div class="zone-row-actions">
                <button class="icon-btn" title="Sửa vùng" aria-label="Sửa vùng" onclick="editZone('${z.id}')">✏️</button>
                <button class="icon-btn" title="${z.enabled ? 'Tắt vùng' : 'Bật vùng'}" aria-label="${z.enabled ? 'Tắt vùng' : 'Bật vùng'}"
                        onclick="toggleZone('${z.id}', ${!z.enabled})">${z.enabled ? '🔕' : '🔔'}</button>
                <button class="icon-btn icon-btn-delete" title="Xoá vùng" aria-label="Xoá vùng"
                        onclick="deleteZone('${z.id}','${esc(z.name)}')">🗑️</button>
            </div>
        </div>`;
    }).join('');
}

function editZone(zoneId) {
    const zone = zonesOnCamera.find(z => z.id === zoneId);
    if (!zone) return;

    document.getElementById('zone-edit-id').value = zone.id;
    document.getElementById('zone-name-input').value = zone.name;
    document.getElementById('zone-rule-type').value = zone.rule;
    document.getElementById('detect-human-cb').checked = zone.detect_human !== false;
    document.getElementById('detect-object-cb').checked = zone.detect_object === true;
    document.getElementById('zone-enabled-cb').checked = zone.enabled !== false;
    document.getElementById('zone-rule-hint').textContent = (ZONE_RULE_META[zone.rule] || {}).hint || '';

    const pts = zone.points.map(p => ({ ...p }));
    if (zone.kind === 'tripwire') {
        tripwirePoints = pts;
        polygonPoints = [];
        setDrawingMode('tripwire');
    } else {
        polygonPoints = pts;
        tripwirePoints = [];
        setDrawingMode('polygon');
    }
    redrawRoiCanvas();
    updateCoordsJsonDisplay();
    document.getElementById('btn-save-zone').textContent = '💾 Cập nhật vùng';
}
window.editZone = editZone;

function resetZoneForm() {
    document.getElementById('zone-edit-id').value = '';
    document.getElementById('zone-name-input').value = '';
    document.getElementById('zone-save-status').textContent = '';
    document.getElementById('btn-save-zone').textContent = '💾 Lưu vùng';
    polygonPoints = [];
    tripwirePoints = [];
    redrawRoiCanvas();
    updateCoordsJsonDisplay();
    onZoneRuleChange();
}
window.resetZoneForm = resetZoneForm;

async function toggleZone(zoneId, enabled) {
    try {
        const res = await fetch(`/api/v1/zones/${zoneId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        loadZoneRules();
    } catch (e) {
        showToast(e.message, 'error');
    }
}
window.toggleZone = toggleZone;

async function deleteZone(zoneId, name) {
    if (!confirm(`Xoá vùng "${name}"?`)) return;
    try {
        const res = await fetch(`/api/v1/zones/${zoneId}`, { method: 'DELETE' });
        if (!res.ok && res.status !== 204) throw new Error(describeApiError(await res.json()));
        resetZoneForm();
        loadZoneRules();
    } catch (e) {
        showToast(e.message, 'error');
    }
}
window.deleteZone = deleteZone;

async function saveZoneRules(event) {
    event.preventDefault();
    const status = document.getElementById('zone-save-status');
    const id = document.getElementById('zone-edit-id').value;
    const rule = document.getElementById('zone-rule-type').value;
    const kind = ZONE_RULE_META[rule].kind;
    const points = kind === 'tripwire' ? tripwirePoints : polygonPoints;

    const payload = {
        name: document.getElementById('zone-name-input').value.trim(),
        kind,
        rule,
        points,
        detect_human: document.getElementById('detect-human-cb').checked,
        detect_object: document.getElementById('detect-object-cb').checked,
        enabled: document.getElementById('zone-enabled-cb').checked
    };

    try {
        const res = await fetch(
            id ? `/api/v1/zones/${id}` : `/api/v1/cameras/${zoneTargetCamera()}/zones`,
            {
                method: id ? 'PATCH' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        if (!res.ok) throw new Error(describeApiError(await res.json()));

        status.textContent = id ? '✓ Đã cập nhật vùng' : '✓ Đã thêm vùng, AI áp dụng ngay';
        status.style.color = '#0a8f4c';
        resetZoneForm();
        loadZoneRules();
    } catch (err) {
        status.textContent = `✗ ${err.message}`;
        status.style.color = '#dc2626';
    }
}
window.saveZoneRules = saveZoneRules;


// ----------------- SCHEDULE MANAGEMENT -----------------
const schedulesTbody = document.getElementById('schedules-tbody');
const scheduleModal = document.getElementById('schedule-modal');

async function fillScheduleCameraSelect(selected) {
    const select = document.getElementById('sch-camera-select');
    if (!select) return;
    try {
        scheduleCameras = (await getJson('/api/v1/cameras')).items || [];
    } catch (e) {
        scheduleCameras = [];
    }
    select.innerHTML = '<option value="">— Chưa gán camera —</option>' +
        scheduleCameras.map(c =>
            `<option value="${esc(c.id)}">${esc(c.name)}${c.area_name ? ' · ' + esc(c.area_name) : ''}</option>`
        ).join('');
    select.value = scheduleCameras.some(c => c.id === selected) ? selected : '';
}

function openScheduleModal(schedule) {
    const modal = document.getElementById('schedule-modal');
    if (!modal) return;
    const sch = schedule || {};
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };

    // Danh sách camera nạp bất đồng bộ; modal mở ngay, select điền sau
    fillScheduleCameraSelect(sch.camera_id || '');

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
    set('sch-date-from', sch.date_from || '');
    set('sch-date-to', sch.date_to || '');
    const chonThu = new Set((sch.weekdays || []).map(Number));
    document.querySelectorAll('#sch-weekdays input').forEach(cb => {
        cb.checked = chonThu.has(Number(cb.value));
    });
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
        // Nạp camera trước để bảng hiện được tên thay vì mã
        scheduleCameras = (await getJson('/api/v1/cameras')).items || [];
    } catch (e) {
        scheduleCameras = [];
    }
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

// Tên camera để hiện trong bảng ca; danh sách đã nạp sẵn khi mở modal, chưa có
// thì hiện mã cho đỡ trống.
let scheduleCameras = [];

function scheduleCameraName(cameraId) {
    if (!cameraId) return '—';
    const cam = scheduleCameras.find(c => c.id === cameraId);
    return cam ? cam.name : cameraId;
}

// Danh sách ca đang hiển thị, để nút sửa lấy lại đúng bản ghi
let scheduleRows = [];

const WEEKDAY_LABELS = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];

// Phạm vi áp dụng của ca. Không khai gì thì ca lặp mọi ngày như thời khoá biểu cũ.
function scheduleDateScope(sch) {
    const ngay = (iso) => {
        const [y, m, d] = String(iso).split('-');
        return d ? `${d}/${m}/${y}` : iso;
    };
    const phan = [];
    if (sch.date_from && sch.date_to) phan.push(`${ngay(sch.date_from)} – ${ngay(sch.date_to)}`);
    else if (sch.date_from) phan.push(`Từ ${ngay(sch.date_from)}`);
    else if (sch.date_to) phan.push(`Đến ${ngay(sch.date_to)}`);

    const thu = (sch.weekdays || []).map(Number).sort((a, b) => a - b).map(d => WEEKDAY_LABELS[d]);
    if (thu.length) phan.push(thu.join(', '));
    if (!phan.length) return '<span class="muted">Hằng ngày</span>';
    return phan.map(t => `<div>${esc(t)}</div>`).join('');
}

function renderSchedulesTable(schedules) {
    if (!schedulesTbody) return;
    scheduleRows = schedules;
    schedulesTbody.innerHTML = '';

    if (schedules.length === 0) {
        schedulesTbody.innerHTML = `<tr><td colspan="7" class="empty-row">Chưa có ca thời khoá biểu nào được thiết lập</td></tr>`;
        return;
    }

    schedules.forEach(sch => {
        const win = sch.check_window_mins || 5;
        const done = sch.checked_today || {};
        const stateClass = SCHEDULE_STATE_CLASS[sch.state] || 'status-neutral';
        const row = document.createElement('tr');
        // Gom 12 cột xuống 7: mỗi ô mang một nhóm thông tin, phần phụ xuống dòng
        // nhỏ bên dưới. Trước đây bảng quá rộng nên cột bị bóp và chữ vỡ dòng.
        row.innerHTML = `
            <td>
                <strong>${esc(sch.name)}</strong>
                <div class="cell-subtext">${esc(sch.shift || '')}</div>
            </td>
            <td>
                ${esc(sch.unit || '—')}
                <div class="cell-subtext">${sch.required_count || 45} quân nhân</div>
            </td>
            <td>
                <span class="font-mono">${esc(sch.start_time)} – ${esc(sch.end_time)}</span>
                <div class="cell-subtext font-mono">Điểm danh ${esc(sch.start_time)}–${addMinutesToClock(sch.start_time, win)}
                    · ${addMinutesToClock(sch.end_time, -win)}–${esc(sch.end_time)}</div>
            </td>
            <td>${scheduleDateScope(sch)}</td>
            <td>${esc(scheduleCameraName(sch.camera_id))}</td>
            <td>
                <span class="status-tag ${stateClass}">${esc(sch.state_label || sch.status || 'Active')}</span>
                <div class="check-badges">
                    ${checkedBadge(done.start, 'Đầu giờ')}
                    ${checkedBadge(done.end, 'Cuối giờ')}
                </div>
            </td>
            <td>
                <button class="icon-btn icon-btn-edit" title="Sửa ca" aria-label="Sửa ca" onclick="editSchedule('${sch.id}')">✏️</button>
                <button class="icon-btn icon-btn-delete" title="Xoá ca" aria-label="Xoá ca" onclick="deleteSchedule('${sch.id}')">🗑️</button>
            </td>
        `;
        schedulesTbody.appendChild(row);
    });
}

// Không có nút này thì ca đã tạo không bao giờ gán được camera giám sát khác,
// nên cột CAMERA của bảng mãi mãi giữ nguyên camera mặc định.
function editSchedule(scheduleId) {
    const sch = scheduleRows.find(s => s.id === scheduleId);
    if (!sch) return;
    openScheduleModal(sch);
}
window.editSchedule = editSchedule;

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
        camera_id: val('sch-camera-select') || null,
        date_from: val('sch-date-from') || null,
        date_to: val('sch-date-to') || null,
        weekdays: [...document.querySelectorAll('#sch-weekdays input:checked')].map(cb => Number(cb.value)),
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
    if (!confirm('Bạn có chắc muốn xoá ca thời khoá biểu này?')) return;
    try {
        const res = await fetch(`/api/schedules/${schId}`, { method: 'DELETE' });
        if (res.ok) {
            loadSchedules();
        }
    } catch (e) {
        showToast('Lỗi xoá ca: ' + e.message, 'error');
    }
}
window.deleteSchedule = deleteSchedule;


// ----------------- PHÂN TRANG BẢNG (dùng chung nhiều bảng dài) -----------------
// Nhật ký điểm danh và nhật ký vi phạm an toàn đều là bảng dài, cuộn riêng
// (xem .table-scroll-tall trong style.css) — cùng cắt trang kiểu này cho đỡ
// lặp lại code, 20 dòng/trang theo yêu cầu.
function paginate(rows, page, pageSize) {
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    const safePage = Math.min(Math.max(1, page || 1), totalPages);
    const start = (safePage - 1) * pageSize;
    return { items: rows.slice(start, start + pageSize), page: safePage, totalPages };
}

// ``onChange(nextPage)`` — gọi lại khi bấm ‹/›; bản thân renderPager() không
// tự vẽ lại bảng, bên gọi tự quyết định vẽ lại từ đâu (API hay mảng đã lọc sẵn).
function renderPager(containerEl, page, totalPages, onChange) {
    if (!containerEl) return;
    if (totalPages <= 1) { containerEl.innerHTML = ''; return; }
    containerEl.innerHTML = `
        <button type="button" class="pager-nav-btn" ${page <= 1 ? 'disabled' : ''}>‹</button>
        <span class="pager-info">Trang ${page}/${totalPages}</span>
        <button type="button" class="pager-nav-btn" ${page >= totalPages ? 'disabled' : ''}>›</button>
    `;
    const prevBtn = containerEl.children[0], nextBtn = containerEl.children[2];
    prevBtn.onclick = () => onChange(page - 1);
    nextBtn.onclick = () => onChange(page + 1);
}

// ----------------- ATTENDANCE LOGS MANAGEMENT -----------------
let attendanceLogsData = [];
const attendanceLogsTbody = document.getElementById('attendance-logs-tbody');
let attLogsPage = 1;
let attLogsFiltered = [];
const ATT_LOGS_PAGE_SIZE = 20;

async function loadAttendanceLogs() {
    if (!attendanceLogsTbody) return;
    const val = (id) => ((document.getElementById(id) || {}).value || '').trim();

    const params = new URLSearchParams();
    params.set('unit', val('log-filter-unit') || 'all');
    if (val('log-filter-shift')) params.set('shift', val('log-filter-shift'));
    if (val('log-date-from')) params.set('date_from', val('log-date-from'));
    if (val('log-date-to')) params.set('date_to', val('log-date-to'));
    if (val('log-search')) params.set('q', val('log-search'));

    try {
        const res = await fetch(`/api/attendance-logs?${params.toString()}`);
        const result = await res.json();
        if (result.status === 'success' && result.data) {
            attendanceLogsData = result.data;
            // Qua bộ lọc trạng thái để lần nạp lại không xoá lựa chọn đang có
            filterAttendanceLogsByStatus();
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
            evidence: log.evidence || null,
            evidence_captured_at: log.evidence_captured_at || null
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
    // Giờ hệ thống chốt ảnh (khác giờ in sẵn trên khung hình, lấy từ camera —
    // xem ghi chú cạnh bảng), hiện thêm dưới ảnh thu nhỏ để chỉ huy đối chiếu.
    const capturedAt = check.evidence_captured_at
        ? `<div class="cell-subtext font-mono">${esc(new Date(check.evidence_captured_at).toLocaleTimeString('vi-VN'))}</div>` : '';
    return `<img src="${check.evidence}" class="evidence-thumb" loading="lazy"`
        + ` alt="Bằng chứng ${phaseLabel}"`
        + ` onclick="openEvidenceModal('${check.evidence}', '${phaseLabel}', '${caption.replace(/'/g, "\\'")}')">`
        + capturedAt;
}

// Tên quân nhân xếp hàng dọc, mỗi người một gạch đầu dòng: đọc nhanh hơn một
// chuỗi dài nối bằng dấu phẩy, nhất là khi vắng nhiều người
// Mỗi phần tử có thể là chuỗi tên đã gộp sẵn (dữ liệu cũ) hoặc object quân
// nhân {name, rank, military_id} — formatPersonLabel() xử lý cả hai dạng.
function absentNameList(names) {
    return `<ul class="absent-name-list">${(names || []).map(n => `<li>${esc(formatPersonLabel(n))}</li>`).join('')}</ul>`;
}

function renderAbsentList(log) {
    const parts = [['start', 'Đầu giờ'], ['end', 'Cuối giờ']]
        .map(([phase, label]) => [label, (getCheck(log, phase) || {}).absent_personnel || []])
        .filter(([, names]) => names.length)
        .map(([label, names]) => `<div><span class="phase-tag">${label}</span>${absentNameList(names)}</div>`);
    if (parts.length === 0) return '<span style="color: #64748b;">-</span>';
    return parts.join('');
}

function renderAttendanceLogsTable(logs) {
    if (!attendanceLogsTbody) return;
    attLogsFiltered = logs; // pager đổi trang thì vẽ lại từ đây, không lọc lại từ đầu
    attendanceLogsTbody.innerHTML = '';
    const pagerEl = document.getElementById('attendance-logs-pager');

    if (logs.length === 0) {
        attendanceLogsTbody.innerHTML = `<tr><td colspan="12" class="empty-row">Không có bản ghi điểm danh nào phù hợp</td></tr>`;
        if (pagerEl) pagerEl.innerHTML = '';
        return;
    }

    const { items, page, totalPages } = paginate(logs, attLogsPage, ATT_LOGS_PAGE_SIZE);
    attLogsPage = page;

    items.forEach(log => {
        const row = document.createElement('tr');
        const statusClass = log.status_type === 'success' ? 'status-ok' : 'status-warning';
        const startCheck = getCheck(log, 'start');
        const endCheck = getCheck(log, 'end');

        row.innerHTML = `
            <td class="font-mono"><strong>${esc(log.date)}</strong> ${esc(log.time || '')}</td>
            <td>${esc(log.shift)}</td>
            <td><strong>${esc(log.lesson_name || log.schedule_name || '—')}</strong></td>
            <td><strong>${esc(log.unit)}</strong></td>
            <td>${log.required}</td>
            <td>${renderCheckCell(startCheck, log.required)}</td>
            <td>${renderEvidenceCell(startCheck, log, 'Đầu giờ')}</td>
            <td>${renderCheckCell(endCheck, log.required)}</td>
            <td>${renderEvidenceCell(endCheck, log, 'Cuối giờ')}</td>
            <td style="max-width: 260px;">${renderAbsentList(log)}</td>
            <td><span class="status-tag ${statusClass}">${esc(log.status)}</span></td>
            <td><button class="btn-event-clip" onclick="openLogModal('${log.id}')">Xem chi tiết</button></td>
        `;
        attendanceLogsTbody.appendChild(row);
    });

    renderPager(pagerEl, page, totalPages, (p) => {
        attLogsPage = p;
        renderAttendanceLogsTable(attLogsFiltered);
    });
}


// ----------------- CHI TIẾT MỘT CA TRONG NHẬT KÝ -----------------

function openLogModal(logId) {
    const log = attendanceLogsData.find(l => l.id === logId);
    if (!log) return;

    document.getElementById('log-modal-title').textContent =
        log.schedule_name || log.shift || 'Chi tiết ca điểm danh';
    document.getElementById('log-modal-subtitle').textContent =
        `${log.date || ''} · ${log.unit || ''} · sĩ số chuẩn ${log.required || 0}`;

    const sm = log.attendance_summary || {};
    const info = [
        ['Ca', log.shift],
        ['Tên bài học', log.lesson_name || log.schedule_name],
        ['Loại huấn luyện', TRAINING_LABEL[log.training_type] || ''],
        ['Giáo viên phụ trách', log.instructor],
        ['Thao trường', log.field],
        ['Đội học / Lớp', log.class_name],
        ['Đơn vị', log.unit],
        ['Ngày', log.date],
        ['Khung giờ', log.start_time && log.end_time ? `${log.start_time} – ${log.end_time}` : null],
        ['Sĩ số yêu cầu', log.required],
        ['Trạng thái', log.status],
        ['Thời gian diễn ra thực tế', log.actual_minutes != null
            ? `${log.actual_minutes}/${log.scheduled_minutes || '?'} phút` : null],
        ['Đủ giờ', sm.present],
        ['Đi chậm', sm.late],
        ['Về sớm', sm.early_leave],
        ['Không tham gia', sm.absent],
        ['Chỉ huy duyệt', log.commander]
    ];
    document.getElementById('log-modal-info').innerHTML = info
        .filter(([, v]) => v !== null && v !== undefined && v !== '')
        .map(([k, v]) => `<div class="detail-item"><span class="detail-key">${k}</span>
                          <span class="detail-val">${esc(v)}</span></div>`).join('');

    const checks = log.checks || {};
    const rows = ['start', 'end', 'manual'].filter(ph => checks[ph]);
    document.getElementById('log-modal-checks').innerHTML = rows.length
        ? rows.map(ph => {
            const c = checks[ph];
            return `<tr>
                <td><strong>${esc(c.phase_label)}</strong></td>
                <td class="font-mono">${esc(c.time || '—')}</td>
                <td class="text-green"><strong>${c.present}</strong></td>
                <td class="${c.absent > 0 ? 'text-amber' : ''}">${c.absent}</td>
                <td>${(c.absent_personnel || []).length
                    ? absentNameList(c.absent_personnel) : '<span class="muted">Không vắng ai</span>'}</td>
                <td>${c.scans != null ? c.scans : '—'}</td>
            </tr>`;
        }).join('')
        : '<tr><td colspan="6" class="empty-row">Biên bản chưa có mốc điểm danh nào</td></tr>';

    const photos = rows.filter(ph => checks[ph].evidence);
    document.getElementById('log-modal-evidence').innerHTML = photos.length
        ? photos.map(ph => {
            const c = checks[ph];
            return `<figure class="evidence-figure">
                <img src="${c.evidence}" alt="Ảnh điểm danh ${esc(c.phase_label)}"
                     onclick="openEvidence('${c.evidence}','${esc(c.phase_label)} — ${c.present} có mặt')">
                <figcaption><strong>${esc(c.phase_label)}</strong> · ${esc(c.time || '')}
                    <a class="btn-download" href="${c.evidence}" download>⬇ Tải ảnh</a></figcaption>
            </figure>`;
        }).join('')
        : '<p class="empty-hint">Chưa có ảnh bằng chứng nào được chụp</p>';

    // Bảng vi phạm chỉ có khi máy chủ quan sát được cả buổi
    const violators = (log.attendance || []).filter(i => (i.violations || []).length);
    document.getElementById('log-modal-violations').innerHTML = violators.length
        ? `<div class="table-responsive"><table class="personnel-table">
             <thead><tr><th>QUÂN NHÂN</th><th>CHỨC VỤ</th><th>ĐƠN VỊ</th><th>TÊN VI PHẠM</th></tr></thead>
             <tbody>${violators.map(i => {
                 const p = i.person || {};
                 const extra = [];
                 if (i.late_minutes) extra.push(`chậm ${i.late_minutes}′`);
                 if (i.early_leave_minutes) extra.push(`về sớm ${i.early_leave_minutes}′`);
                 return `<tr>
                    <td><strong>${esc(p.name || '')}</strong>
                        <div class="cell-subtext font-mono">${esc(p.military_id || '')}</div></td>
                    <td>${esc(p.rank || '—')}</td>
                    <td>${esc(p.unit || '—')}</td>
                    <td>${(i.violations || []).map(v => VIOLATION_TAG[v] || v).join(' ')}
                        ${extra.length ? `<div class="cell-subtext">${extra.join(' · ')}</div>` : ''}</td>
                 </tr>`;
             }).join('')}</tbody></table></div>`
        : (log.absent_personnel || []).length
            ? `<p class="muted">Danh sách vắng:</p>${absentNameList(log.absent_personnel)}`
            : '<p class="empty-hint">Không có vi phạm giờ giấc trong ca này</p>';

    document.getElementById('log-modal').style.display = 'flex';
}
window.openLogModal = openLogModal;

function closeLogModal() {
    const modal = document.getElementById('log-modal');
    if (modal) modal.style.display = 'none';
}
window.closeLogModal = closeLogModal;

// ----------------- EVIDENCE LIGHTBOX -----------------
// Giữ tên cũ vì bảng nhật ký đang gọi; thực chất dùng chung hộp phóng to
function openEvidenceModal(src, phaseLabel, caption) {
    openEvidence(src, caption || `Bằng chứng điểm danh ${String(phaseLabel).toLowerCase()}`);
}
window.openEvidenceModal = openEvidenceModal;


// =====================================================================
// XEM ẢNH PHÓNG TO VÀ ZOOM CAMERA — DÙNG CHUNG TOÀN HỆ THỐNG
// Trước đây nhiều chỗ gọi openEvidence() nhưng hàm chưa bao giờ được định
// nghĩa, nên bấm vào ảnh bằng chứng chỉ ném ReferenceError và không mở gì cả.
// Phóng to dùng Fullscreen API và transform: scale() sẵn có, không thêm thư viện.
// =====================================================================

function toggleFullscreen(el) {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (el && el.requestFullscreen) el.requestFullscreen();
}
window.toggleFullscreen = toggleFullscreen;

// Nút ⛶/🗗 trên MỌI khung zoomable phải đổi chữ/tiêu đề đúng theo khung nào
// đang toàn màn hình — một listener fullscreenchange dùng chung, gắn một lần.
let _fullscreenBtnSyncAdded = false;
function ensureFullscreenButtonSync() {
    if (_fullscreenBtnSyncAdded) return;
    _fullscreenBtnSyncAdded = true;
    document.addEventListener('fullscreenchange', () => {
        document.querySelectorAll('.zoom-fullscreen-btn').forEach((btn) => {
            const container = btn.closest('.zoomable');
            const isFs = !!container && document.fullscreenElement === container;
            btn.textContent = isFs ? '🗗' : '⛶';
            btn.title = isFs ? 'Thu nhỏ' : 'Phóng to';
            btn.setAttribute('aria-label', btn.title);
        });
    });
}

// Khung zoomable người dùng vừa chạm tới gần nhất (lăn chuột/kéo) — để phím
// '0' biết nên reset zoom của khung nào, vì không phải lúc nào cũng đang toàn
// màn hình (nhiều ô camera cùng zoomable được trên một trang).
let _lastInteractedZoomable = null;
let _zoomResetKeyAdded = false;
function ensureZoomResetKey() {
    if (_zoomResetKeyAdded) return;
    _zoomResetKeyAdded = true;
    document.addEventListener('keydown', (e) => {
        if (e.key !== '0') return;
        const tag = (e.target && e.target.tagName) || '';
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
        const target = (document.fullscreenElement && document.fullscreenElement.classList.contains('zoomable'))
            ? document.fullscreenElement : _lastInteractedZoomable;
        if (target && target._zoom && target._zoom.scale > 1) target._zoom.reset();
    });
}

// Gắn khả năng phóng to / kéo di cho một khung có chứa <img>: lăn chuột để
// zoom, kéo để di khi đã phóng, bấm đúp để toàn màn hình, nút ⛶ để toàn màn
// hình, nút "1:1" (hiện khi đã zoom) hoặc phím '0' để về vừa khung.
function makeZoomable(container) {
    if (!container || container.dataset.zoomable === '1') return;
    container.dataset.zoomable = '1';
    container.classList.add('zoomable');
    ensureFullscreenButtonSync();
    ensureZoomResetKey();

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'zoom-fullscreen-btn';
    btn.title = 'Phóng to';
    btn.setAttribute('aria-label', 'Phóng to');
    btn.textContent = '⛶';
    btn.onclick = (e) => { e.stopPropagation(); toggleFullscreen(container); };
    container.appendChild(btn);

    // Nút "1:1": chỉ hiện khi đã zoom (CSS theo .is-zoomed) — thay cho bấm đúp
    // để về vừa khung như trước, vì bấm đúp giờ dùng để vào/ra toàn màn hình.
    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'zoom-reset-btn';
    resetBtn.title = 'Về đúng kích cỡ (phím 0)';
    resetBtn.setAttribute('aria-label', resetBtn.title);
    resetBtn.textContent = '1:1';
    resetBtn.onclick = (e) => { e.stopPropagation(); reset(); };
    container.appendChild(resetBtn);

    let scale = 1, x = 0, y = 0, drag = null;
    // player.js (go2rtc) chèn <video-stream> và ẨN <img> MJPEG thay vì gỡ nó —
    // nên không chỉ querySelector('img'): phải lấy đúng phần tử ĐANG HIỂN THỊ
    // để phóng/kéo đúng cái người dùng nhìn thấy.
    const img = () => {
        for (const el of container.querySelectorAll('video-stream, img')) {
            if (el.offsetWidth > 0 && el.style.display !== 'none') return el;
        }
        return container.querySelector('img');
    };

    // Giữ ảnh trong khung: phóng to hơn khung thì không cho kéo lộ khoảng trống,
    // nhỏ hơn khung thì căn giữa. Không có cái này thì kéo quá tay là mất ảnh.
    const clamp = () => {
        const el = img();
        if (!el) return;
        const cw = container.clientWidth, ch = container.clientHeight;
        const w = el.offsetWidth * scale, h = el.offsetHeight * scale;
        const ox = el.offsetLeft, oy = el.offsetTop;
        x = w >= cw ? Math.min(-ox, Math.max(cw - ox - w, x)) : (cw - w) / 2 - ox;
        y = h >= ch ? Math.min(-oy, Math.max(ch - oy - h, y)) : (ch - h) / 2 - oy;
    };

    const apply = () => {
        const el = img();
        if (el) {
            el.draggable = false;
            // Gốc biến đổi ở góc trên-trái để tự tính điểm neo theo con trỏ
            el.style.transformOrigin = '0 0';
            el.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
        }
        container.classList.toggle('is-zoomed', scale > 1);
        const label = container.querySelector('.zoom-level')
            || document.getElementById(container.dataset.zoomLevelId || '');
        if (label) label.textContent = `${Math.round(scale * 100)}%`;
    };

    // Phóng quanh điểm (mx, my) tính trong khung: điểm ảnh đang nằm dưới con trỏ
    // vẫn nằm dưới con trỏ sau khi phóng, thay vì luôn phóng vào giữa khung.
    const zoomAt = (delta, mx, my) => {
        const next = Math.min(6, Math.max(1, Math.round((scale + delta) * 100) / 100));
        if (next === scale) return;
        const el = img();
        const ox = el ? el.offsetLeft : 0, oy = el ? el.offsetTop : 0;
        x = mx - ox - (mx - ox - x) * next / scale;
        y = my - oy - (my - oy - y) * next / scale;
        scale = next;
        if (scale === 1) { x = 0; y = 0; }
        clamp();
        apply();
    };

    const zoomBy = (delta) => zoomAt(delta, container.clientWidth / 2, container.clientHeight / 2);

    const reset = () => { scale = 1; x = 0; y = 0; apply(); };

    container.addEventListener('wheel', (e) => {
        e.preventDefault();
        _lastInteractedZoomable = container;
        const r = container.getBoundingClientRect();
        zoomAt(e.deltaY < 0 ? 0.25 : -0.25, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    // Trình duyệt tự bắt đầu kéo-thả khi nhấn giữ trên ảnh, cắt ngang thao tác kéo
    container.addEventListener('dragstart', (e) => e.preventDefault());

    container.addEventListener('pointerdown', (e) => {
        _lastInteractedZoomable = container;
        if (scale === 1 || (e.target.closest && e.target.closest('button'))) return;
        e.preventDefault();
        drag = { sx: e.clientX - x, sy: e.clientY - y };
        container.classList.add('is-panning');
        if (container.setPointerCapture && e.pointerId !== undefined) container.setPointerCapture(e.pointerId);
    });
    container.addEventListener('pointermove', (e) => {
        if (!drag) return;
        x = e.clientX - drag.sx;
        y = e.clientY - drag.sy;
        clamp();
        apply();
    });
    const endDrag = (e) => {
        if (!drag) return;
        drag = null;
        container.classList.remove('is-panning');
        if (container.releasePointerCapture && e.pointerId !== undefined
            && container.hasPointerCapture && container.hasPointerCapture(e.pointerId)) {
            container.releasePointerCapture(e.pointerId);
        }
    };
    container.addEventListener('pointerup', endDrag);
    container.addEventListener('pointercancel', endDrag);
    // Bấm đúp giờ vào/ra toàn màn hình (đồng bộ với nút ⛶), khớp với hành vi
    // của <video> go2rtc nếu lỡ còn lộ ra — khỏi có hai cách bấm đúp khác nhau
    // tuỳ đang xem MJPEG hay go2rtc. Về vừa khung giờ dùng nút "1:1" hoặc phím '0'.
    container.addEventListener('dblclick', (e) => {
        if (e.target.closest && e.target.closest('button')) return;
        toggleFullscreen(container);
    });

    container._zoom = { zoomBy, reset, get scale() { return scale; } };
}
window.makeZoomable = makeZoomable;

function zoomStage() {
    const stage = document.getElementById('zoom-stage');
    if (stage && !stage._zoom) {
        stage.dataset.zoomLevelId = 'zoom-level';
        makeZoomable(stage);
    }
    return stage;
}

function zoomStageBy(delta) { const s = zoomStage(); if (s) s._zoom.zoomBy(delta); }
window.zoomStageBy = zoomStageBy;

function zoomStageReset() { const s = zoomStage(); if (s) s._zoom.reset(); }
window.zoomStageReset = zoomStageReset;

// Hàm mà toàn bộ ảnh bằng chứng trong hệ thống gọi tới
function openEvidence(src, caption) {
    const modal = document.getElementById('zoom-modal');
    const img = document.getElementById('zoom-img');
    if (!modal || !img || !src) return;

    img.src = src;
    const cap = document.getElementById('zoom-caption');
    if (cap) cap.textContent = caption || '';
    const dl = document.getElementById('zoom-download');
    if (dl) dl.href = src;

    modal.style.display = 'flex';
    zoomStageReset();
}
window.openEvidence = openEvidence;

function closeZoomModal(event) {
    // Bấm vào chính ảnh hoặc thanh công cụ thì không đóng
    if (event && event.target && event.target.id !== 'zoom-modal') return;
    const modal = document.getElementById('zoom-modal');
    if (modal) modal.style.display = 'none';
    if (document.fullscreenElement) document.exitFullscreen();
}
window.closeZoomModal = closeZoomModal;

document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('zoom-modal');
    if (modal && modal.style.display !== 'none') closeZoomModal();
});

function filterAttendanceLogsByStatus() {
    attLogsPage = 1; // đổi bộ lọc (trạng thái, hoặc gọi lại từ loadAttendanceLogs) thì về trang 1
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
        showToast('Không có dữ liệu điểm danh để xuất báo cáo', 'info');
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
        const absentStr = (l.absent_personnel || []).map(n => formatPersonLabel(n)).join('; ');
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


// =====================================================================
// MENU TÀI KHOẢN Ở GÓC TRÁI DƯỚI CÙNG
// =====================================================================

function toggleAccountMenu() {
    const menu = document.getElementById('account-menu');
    if (!menu) return;
    menu.style.display = menu.style.display === 'flex' ? 'none' : 'flex';
}
window.toggleAccountMenu = toggleAccountMenu;

document.addEventListener('click', (e) => {
    const footer = e.target.closest && e.target.closest('.sidebar-footer');
    if (footer) return;
    const menu = document.getElementById('account-menu');
    if (menu) menu.style.display = 'none';
});

function openAccountModal() {
    const modal = document.getElementById('account-modal');
    if (!modal) return;
    const menu = document.getElementById('account-menu');
    if (menu) menu.style.display = 'none';

    document.getElementById('acc-display-name').value =
        (currentUser && currentUser.display_name) || '';
    ['acc-old-password', 'acc-new-password'].forEach(id => {
        document.getElementById(id).value = '';
    });
    ['acc-profile-status', 'acc-password-status'].forEach(id => {
        document.getElementById(id).textContent = '';
    });
    modal.style.display = 'flex';
}
window.openAccountModal = openAccountModal;

function closeAccountModal() {
    const modal = document.getElementById('account-modal');
    if (modal) modal.style.display = 'none';
}
window.closeAccountModal = closeAccountModal;

function setAccountStatus(id, text, ok) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.style.color = ok ? 'var(--primary-green)' : 'var(--danger-red)';
}

async function submitProfileForm(event) {
    event.preventDefault();
    if (!currentUser) return;
    const displayName = document.getElementById('acc-display-name').value.trim();
    try {
        const res = await fetch('/api/v1/auth/profile', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUser.username, display_name: displayName })
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        const user = await res.json();
        try { localStorage.setItem('horus_user', JSON.stringify(user)); } catch (e) { /* chế độ riêng tư */ }
        applyRole(user);
        setAccountStatus('acc-profile-status', '✓ Đã lưu tên hiển thị', true);
    } catch (e) {
        setAccountStatus('acc-profile-status', `✗ ${e.message}`, false);
    }
}
window.submitProfileForm = submitProfileForm;

async function submitPasswordForm(event) {
    event.preventDefault();
    if (!currentUser) return;
    try {
        const res = await fetch('/api/v1/auth/password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser.username,
                old_password: document.getElementById('acc-old-password').value,
                new_password: document.getElementById('acc-new-password').value
            })
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        ['acc-old-password', 'acc-new-password'].forEach(id => {
            document.getElementById(id).value = '';
        });
        setAccountStatus('acc-password-status', '✓ Đã đổi mật khẩu', true);
    } catch (e) {
        setAccountStatus('acc-password-status', `✗ ${e.message}`, false);
    }
}
window.submitPasswordForm = submitPasswordForm;


// ----------------- INITIALIZATION -----------------



// =====================================================================
// PHÂN HỆ I & II — LỊCH, TIẾN ĐỘ, GIÁM SÁT QUÂN SỐ
// Hai phân hệ dùng chung một bộ hàm, chỉ khác training_type. Doc yêu cầu
// hai nhóm màn riêng nhưng nghiệp vụ giống hệt nhau nên không nhân đôi code.
// =====================================================================

// Rỗng = xem cả hai loại huấn luyện. Phân hệ I và II chỉ khác nhau ở đây.
let currentTrainingType = '';
let currentTabName = 'schedule-progress';
let currentSafetyType = null;
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

// ----------------- Phiên đăng nhập & fetch tập trung -----------------
// Server kiểm phiên qua cookie HttpOnly (gửi kèm tự động, cùng gốc) — ở đây
// chỉ cần bắt 401 (phiên hết/mất) để quay lại màn đăng nhập, không phải tự
// thêm header gì. Bọc quanh fetch gốc một lần, mọi lời gọi fetch() thường
// (bare, không qua getJson) trong toàn trang đều tự được bắt luôn, không phải
// sửa từng chỗ gọi.
let _sessionExpired = false;
const _rawFetch = window.fetch.bind(window);
window.fetch = async function (input, init) {
    const res = await _rawFetch(input, init);
    // currentUser chỉ có giá trị SAU khi đã enterApp() — tránh phản ứng với
    // 401 "bình thường" của /auth/login (sai mật khẩu) hay /auth/me lúc nạp
    // trang (chưa đăng nhập, không phải phiên bị mất giữa chừng).
    if (res.status === 401 && currentUser && !_sessionExpired) {
        _sessionExpired = true;
        currentUser = null;
        try { localStorage.removeItem('horus_user'); } catch (e) { /* chế độ riêng tư */ }
        alert('Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.');
        // Nạp lại trang cho sạch: đóng kênh sự kiện, luồng hình, hẹn giờ...
        // giống hệt lý do handleLogout() cũng reload.
        window.location.reload();
    }
    return res;
};

async function getJson(url) {
    const res = await fetch(url);
    if (!res.ok) {
        let message = (await res.text()).slice(0, 200);
        try { message = describeApiError(JSON.parse(message)); } catch (e) { /* không phải JSON */ }
        throw new Error(message);
    }
    return res.json();
}

// ----------------- MÀN 1.1: LỊCH & TIẾN ĐỘ -----------------

// Dòng lịch đang hiển thị, theo id. Màn chi tiết cần trạng thái của ĐÚNG NGÀY
// được bấm; /schedules/{id} chỉ trả trạng thái của hôm nay.
let trainingRowsById = {};

async function loadTrainingSchedule() {
    const tbody = document.getElementById('dt-schedule-tbody');
    if (!tbody) return;

    const val = (id) => ((document.getElementById(id) || {}).value || '').trim();
    const params = new URLSearchParams();
    if (currentTrainingType) params.set('training_type', currentTrainingType);
    if (val('dt-date-from')) params.set('date_from', val('dt-date-from'));
    if (val('dt-date-to')) params.set('date_to', val('dt-date-to'));
    if (val('dt-filter-shift')) params.set('shift', val('dt-filter-shift'));
    if (val('dt-filter-state')) params.set('state', val('dt-filter-state'));
    if (val('dt-schedule-search')) params.set('q', val('dt-schedule-search'));

    try {
        const data = await getJson(`/api/v1/summary/training?${params.toString()}`);
        const st = data.stats;

        document.getElementById('dt-metric-running').textContent = st.running_sessions;
        document.getElementById('dt-metric-cameras').textContent =
            `${st.cameras_online}/${st.cameras_total}`;
        document.getElementById('dt-metric-headcount').textContent =
            `${st.present_total}/${st.required_total}`;
        document.getElementById('dt-metric-violations').textContent = st.violation_total;

        tbody.innerHTML = data.sessions.length ? '' :
            `<tr><td colspan="10" class="empty-row">Không có lịch huấn luyện nào khớp bộ lọc</td></tr>`;

        trainingRowsById = {};
        data.sessions.forEach(s => {
            trainingRowsById[s.id] = s;
            // Tiến độ ở đây là tiến độ theo đồng hồ: lớp đã học bao lâu trong
            // khung giờ của nó, còn bao lâu nữa thì tan. Không phải số phút
            // camera quan sát được.
            const prog = Math.round(s.time_progress_pct || 0);
            const remain = s.remaining_minutes || 0;
            const conLai = s.state === 'finished' ? 'Đã kết thúc'
                : s.state === 'upcoming' ? 'Chưa bắt đầu'
                : `còn ${remain} phút`;

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td class="font-mono">${esc(s.day || '')}</td>
                <td>${esc(s.shift || '—')}</td>
                <td>${TRAINING_TAG[s.training_type] || '<span class="tt-tag">—</span>'}</td>
                <td><strong>${esc(s.name)}</strong>
                    ${s.lesson_name ? `<div class="cell-subtext">${esc(s.lesson_name)}</div>` : ''}</td>
                <td>${esc(s.unit || '—')}
                    ${s.instructor ? `<div class="cell-subtext">${esc(s.instructor)}</div>` : ''}</td>
                <td class="font-mono">${esc(s.start_time || '--:--')} – ${esc(s.end_time || '--:--')}</td>
                <td><span class="status-tag ${STATE_CLASS[s.state] || 'status-neutral'}">${esc(s.state_label)}</span>
                    ${s.state === 'finished' && !s.has_record
                        ? '<div class="cell-subtext no-record">Không có biên bản</div>' : ''}</td>
                <td>
                    <div class="progress-track"><div class="progress-fill" style="width:${prog}%"></div></div>
                    <span class="progress-text">${prog}% · ${conLai}</span>
                </td>
                <td><strong>${s.required || 0}</strong></td>
                <td><button class="btn-event-clip" onclick="openSessionDetail('${s.id}','${s.schedule_id}')">Xem chi tiết</button></td>`;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="10" class="empty-row">Lỗi tải lịch: ${esc(e.message)}</td></tr>`;
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

    // Hai mốc điểm danh cần cho cả ô "Sĩ số đầu/cuối buổi" lẫn bảng đối chiếu,
    // nên nạp một lần rồi dùng lại chứ không gọi hai lượt.
    let checks = [];
    try {
        checks = await getJson(`/api/v1/sessions/${encodeURIComponent(sessionDetailId)}/checks`);
    } catch (e) {
        checks = [];
    }
    const row = trainingRowsById[sessionId] || {};
    const phaseOf = (ph) => checks.find(c => c.phase === ph);
    let state = row.state;
    const headcount = (ph) => {
        const c = phaseOf(ph);
        if (c) return `${c.present} có mặt · ${c.absent} vắng`;
        return state === 'finished' ? 'Không ghi nhận' : 'Chưa chốt';
    };

    try {
        const sch = await getJson(`/api/v1/schedules/${scheduleId}`);
        state = row.state || sch.state;
        const stateLabel = row.state_label || sch.state_label;
        document.getElementById('sd-title').textContent = (sch.name || '').toUpperCase();
        document.getElementById('sd-subtitle').textContent =
            [row.day, sch.shift, `${sch.start_time}–${sch.end_time}`, sch.unit || 'Toàn đơn vị']
                .filter(Boolean).join(' · ');

        // Trường giáo viên / thao trường / bài học do hệ thống quản lý gửi kèm khi
        // tạo ca; service AI giữ nguyên và trả lại, ở đây chỉ hiển thị.
        const info = [
            ['Tên bài học', sch.lesson_name],
            ['Loại huấn luyện', TRAINING_LABEL[sch.training_type] || '—'],
            ['Giáo viên phụ trách', sch.instructor],
            ['Thao trường', sch.field],
            ['Đội học / Lớp', sch.class_name],
            ['Khung giờ', `${sch.start_time} – ${sch.end_time}`],
            ['Sĩ số đầu buổi', headcount('start')],
            ['Sĩ số cuối buổi', headcount('end')],
            ['Sĩ số chuẩn', sch.required_count || '—'],
            ['Trạng thái', stateLabel]
        ];
        document.getElementById('sd-info').innerHTML = info.map(([k, v]) =>
            `<div class="detail-item"><span class="detail-key">${k}</span>
             <span class="detail-val">${esc(v || '—')}</span></div>`).join('');

        attachSessionCamera({ ...sch, state });
        renderSessionChecks(checks, { ...sch, state });
    } catch (e) {
        document.getElementById('sd-subtitle').textContent = `Lỗi tải ca: ${e.message}`;
        renderSessionChecks(checks, { state });
    }

    await loadSessionAttendance(sessionDetailId);
}
window.openSessionDetail = openSessionDetail;

// Camera của chính ca này, thay cho nút "Giám sát quân số" ngày trước: người
// trực mở chi tiết ca là thấy luôn lớp đang học, không phải bấm thêm một lần.
function attachSessionCamera(sch) {
    const box = document.getElementById('sd-camera-box');
    const img = document.getElementById('sd-stream');
    const idle = document.getElementById('sd-camera-idle');
    if (!box || !img) return;

    makeZoomable(box);
    const live = ['check_start', 'running', 'check_end'].includes(sch.state);
    if (live && sch.camera_id) {
        attachStream(img, sch.camera_id, true);
        if (idle) idle.style.display = 'none';
    } else {
        detachStream(img);
        if (idle) {
            idle.style.display = '';
            idle.textContent = live ? 'Ca chưa gán camera giám sát' : 'Ca không diễn ra, camera không chạy';
        }
    }

    // scheduleCameras chỉ được nạp ở màn cấu hình thời khoá biểu; vào thẳng màn
    // này thì nó còn rỗng và tên camera sẽ hiện ra mã. Nạp trước rồi mới ghi chú.
    const caption = document.getElementById('sd-camera-caption');
    caption.textContent = 'Đang lấy thông tin camera…';
    fillScheduleCameraSelect(sch.camera_id || '').then(() => {
        caption.textContent =
            `${scheduleCameraName(sch.camera_id)} — khung xanh là quân nhân đã định danh`;
    });
}

// Bảng đối chiếu trống vì nhiều lý do khác nhau; nói "chưa diễn ra" cho một ca
// đã kết thúc khiến người xem tưởng hệ thống lúc có lúc không.
function emptyChecksMessage(sch) {
    const win = sch.check_window_mins || 5;
    if (sch.state === 'finished') {
        const windows = sch.start_time && sch.end_time
            ? ` đầu giờ (${sch.start_time}–${addMinutesToClock(sch.start_time, win)})`
              + ` và cuối giờ (${addMinutesToClock(sch.end_time, -win)}–${sch.end_time})`
            : '';
        return `Ca đã kết thúc nhưng không có biên bản điểm danh — camera không chạy trong cửa sổ điểm danh${windows}.`;
    }
    if (sch.state === 'upcoming') return 'Ca chưa diễn ra.';
    return 'Ca đang diễn ra, chưa tới mốc chốt điểm danh nào.';
}

function renderSessionChecks(checks, sch) {
    const tbody = document.getElementById('sd-checks-tbody');
    if (!tbody) return;

    tbody.innerHTML = checks.length ? '' :
        `<tr><td colspan="6" class="empty-row">${esc(emptyChecksMessage(sch || {}))}</td></tr>`;

    checks.forEach(c => {
        const names = c.absent_personnel || [];
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${esc(c.phase_label)}</strong></td>
            <td class="font-mono">${esc(c.time || '—')}</td>
            <td class="text-green"><strong>${c.present}</strong></td>
            <td class="${c.absent > 0 ? 'text-amber' : ''}">${c.absent}</td>
            <td>${names.length
                ? absentNameList(names)
                : '<span class="muted">Không vắng ai</span>'}</td>
            <td>${c.evidence_url
                ? `<img class="evidence-thumb" src="${c.evidence_url}"
                        onclick="openEvidence('${c.evidence_url}','Điểm danh ${esc(c.phase_label)} — ${c.present} có mặt')"
                        alt="Ảnh bằng chứng">`
                : '<span class="muted">Chưa có</span>'}</td>`;
        tbody.appendChild(tr);
    });
}

let sessionAttendanceData = { items: [], summary: {} };

async function loadSessionAttendance(sessionId) {
    const metrics = document.getElementById('sd-metrics');
    try {
        const data = await getJson(`/api/v1/sessions/${encodeURIComponent(sessionId)}/attendance`);
        sessionAttendanceData = { items: data.items || [], summary: data.summary || {} };
    } catch (e) {
        sessionAttendanceData = { items: [], summary: {} };
    }

    const sm = sessionAttendanceData.summary;
    if (metrics) {
        metrics.innerHTML = `
            <div class="metric-card"><span class="metric-label">Sĩ số yêu cầu</span><span class="metric-val">${sm.required || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Đủ giờ</span><span class="metric-val text-green">${sm.present || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Đi chậm</span><span class="metric-val text-amber">${sm.late || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Về sớm</span><span class="metric-val text-amber">${sm.early_leave || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Không tham gia</span><span class="metric-val text-red">${sm.absent || 0}</span></div>`;
    }
    renderSessionAttendance();
}
window.loadSessionAttendance = loadSessionAttendance;

function renderSessionAttendance() {
    const tbody = document.getElementById('sd-attendance-tbody');
    if (!tbody) return;

    const filter = (document.getElementById('sd-filter') || {}).value || 'all';
    const q = ((document.getElementById('sd-search') || {}).value || '').toLowerCase();

    let items = sessionAttendanceData.items;
    if (filter !== 'all') items = items.filter(i => (i.violations || []).includes(filter));
    if (q) items = items.filter(i => {
        const p = i.person || {};
        return `${p.rank || ''} ${p.name || ''} ${p.military_id || ''}`.toLowerCase().includes(q);
    });

    tbody.innerHTML = items.length ? '' :
        `<tr><td colspan="7" class="empty-row">Chưa có dữ liệu điểm danh cho ca này</td></tr>`;

    items.forEach(i => {
        const p = i.person || {};
        const tags = (i.violations || []).map(v => VIOLATION_TAG[v] || v).join(' ')
            || '<span class="viol-tag viol-ok">Đủ giờ</span>';
        const extra = [];
        if (i.late_minutes) extra.push(`chậm ${i.late_minutes}′`);
        if (i.early_leave_minutes) extra.push(`về sớm ${i.early_leave_minutes}′`);

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${esc(p.name || '')}</strong></td>
            <td class="font-mono">${esc(p.military_id || '—')}</td>
            <td>${esc(p.rank || '—')}</td>
            <td>${esc(p.unit || '—')}</td>
            <td class="font-mono">${fmtTime(i.first_seen)}</td>
            <td class="font-mono">${fmtTime(i.last_seen)}</td>
            <td>${tags}${extra.length ? `<br><span class="muted">${extra.join(' · ')}</span>` : ''}</td>`;
        tbody.appendChild(tr);
    });
}
window.renderSessionAttendance = renderSessionAttendance;

// Nhãn vi phạm giờ giấc, dùng ở màn chi tiết ca và hộp chi tiết nhật ký
const VIOLATION_TAG = {
    late: '<span class="viol-tag viol-late">Đi chậm</span>',
    early_leave: '<span class="viol-tag viol-early">Chưa hết giờ đã về</span>',
    absent: '<span class="viol-tag viol-absent">Không tham gia</span>'
};


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

    const errEl = document.getElementById('sf-error');
    try {
        const data = await getJson('/api/v1/summary/safety');
        if (errEl) errEl.style.display = 'none';
        const stateEl = document.getElementById('sf-state');
        stateEl.className = `safety-state-pill state-${data.state}`;
        document.getElementById('sf-state-label').textContent = data.state_label;

        pendingEventsCount = data.pending_count;

        renderSafetyCameraTable(data.cameras || []);
        setActiveIntrusion(data.active_intrusion);
    } catch (e) {
        console.error('Lỗi tải dashboard an toàn:', e);
        if (errEl) {
            errEl.textContent = `⚠ Không tải được dữ liệu an toàn: ${e.message}`;
            errEl.style.display = 'block';
        }
    }
}
window.loadSafetyDashboard = loadSafetyDashboard;

// Trường bắn có thể có nhiều camera nên màn này phải thấy hết. Dựng luồng cho
// tất cả cùng lúc thì lag, nên ở đây chỉ là bảng danh sách; ấn vào một dòng mới
// mở luồng của đúng camera đó.
let safetyCameras = [];

function renderSafetyCameraTable(cameras) {
    const tbody = document.getElementById('sf-camera-tbody');
    if (!tbody) return;
    if (cameras) safetyCameras = cameras;

    const q = ((document.getElementById('sf-search') || {}).value || '').toLowerCase();
    const stateFilter = (document.getElementById('sf-filter-state') || {}).value || '';

    let rows = currentTrainingType
        ? safetyCameras.filter(c => c.training_type === currentTrainingType)
        : safetyCameras.slice();
    if (stateFilter) rows = rows.filter(c => c.safety_state === stateFilter);
    if (q) rows = rows.filter(c =>
        `${c.name || ''} ${c.area_name || ''} ${c.lesson_name || ''}`.toLowerCase().includes(q));

    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Không có camera nào khớp bộ lọc</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    rows.forEach((cam, index) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${index + 1}</td>
            <td><strong>${esc(cam.name)}</strong>${cam.area_name ? `<br><span class="muted">${esc(cam.area_name)}</span>` : ''}</td>
            <td>${esc(cam.lesson_name || '—')}</td>
            <td>${TRAINING_TAG[cam.training_type] || '—'}</td>
            <td>${cam.safety_state === 'danger'
                    ? `<span class="status-tag status-danger">Cảnh báo${cam.alarm_count > 1 ? ` (${cam.alarm_count})` : ''}</span>`
                    : '<span class="status-tag status-ok">Bình thường</span>'}</td>
            <td><button class="btn-event-clip" onclick="openSafetyDetail('${cam.id}')">Xem chi tiết</button></td>`;
        tbody.appendChild(tr);
    });
}
window.renderSafetyCameraTable = renderSafetyCameraTable;

// ---------- màn chi tiết một camera ----------

let safetyDetailCameraId = null;

let sfdLogPage = 1;
const SFD_LOG_PAGE_SIZE = 20;

function openSafetyDetail(cameraId) {
    safetyDetailCameraId = cameraId;
    sfdLogPage = 1; // đổi camera đang xem thì về trang 1 của nhật ký vi phạm
    switchNavTab('safety-detail');
}
window.openSafetyDetail = openSafetyDetail;

async function loadSafetyDetail() {
    if (!safetyDetailCameraId) return;
    const errEl = document.getElementById('sfd-error');
    try {
        const data = await getJson('/api/v1/summary/safety');
        if (errEl) errEl.style.display = 'none';
        const cam = (data.cameras || []).find(c => c.id === safetyDetailCameraId);
        if (!cam) {
            document.getElementById('sfd-subtitle').textContent = 'Camera không còn trong hệ thống';
            return;
        }

        document.getElementById('sfd-title').textContent =
            `GIÁM SÁT AN TOÀN · ${cam.name.toUpperCase()}`;
        document.getElementById('sfd-subtitle').textContent =
            [cam.area_name, cam.lesson_name, TRAINING_LABEL[cam.training_type]]
                .filter(Boolean).join(' · ') || 'Chưa gán ca huấn luyện';
        document.getElementById('sfd-camera-caption').textContent = cam.area_name || cam.name;

        const stateEl = document.getElementById('sfd-state');
        stateEl.className = `safety-state-pill state-${cam.safety_state}`;
        document.getElementById('sfd-state-label').textContent = cam.safety_state_label;

        // Chỉ gắn khi ô chưa có luồng: gán lại src là mở lại kết nối MJPEG
        const box = document.getElementById('sfd-camera-box');
        const img = document.getElementById('sfd-stream');
        const idle = document.getElementById('sfd-idle');
        makeZoomable(box);
        if (cam.status === 'online') {
            if (!img.getAttribute('src')) attachStream(img, cam.id, true);
            idle.style.display = 'none';
        } else {
            detachStream(img);
            idle.style.display = '';
        }

        const mine = (data.events || []).filter(e => e.camera_id === cam.id);
        // Xử lý xong thì rời danh sách trên, xuống nhật ký bên dưới
        const pending = mine.filter(e => !e.acked);
        const handled = mine.filter(e => e.acked);
        document.getElementById('sfd-pending-badge').textContent = `${pending.length} chờ xử lý`;

        const list = document.getElementById('sfd-events-list');
        list.innerHTML = '';
        if (!pending.length) {
            list.innerHTML = '<p class="empty-hint">Không còn vi phạm nào chờ xử lý trên camera này</p>';
        } else {
            pending.forEach(ev => renderEventCard(list, ev, false));
        }

        renderSafetyLog(handled);
        setActiveIntrusion(data.active_intrusion);
    } catch (e) {
        console.error('Lỗi tải chi tiết camera an toàn:', e);
        if (errEl) {
            errEl.textContent = `⚠ Không tải được chi tiết camera: ${e.message}`;
            errEl.style.display = 'block';
        }
    }
}
window.loadSafetyDetail = loadSafetyDetail;

function detachSafetyStreams() {
    detachStream(document.getElementById('sfd-stream'));
}

// Vi phạm đã xác nhận xử lý: rời danh sách trực tiếp và rơi xuống nhật ký, kèm
// lỗi, thời điểm chính xác, ảnh và đoạn ghi 10 giây làm bằng chứng.
function renderSafetyLog(events) {
    const tbody = document.getElementById('sfd-log-tbody');
    if (!tbody) return;
    const pagerEl = document.getElementById('sfd-log-pager');

    if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Chưa có vi phạm nào được xử lý trên camera này</td></tr>';
        if (pagerEl) pagerEl.innerHTML = '';
        return;
    }

    const { items, page, totalPages } = paginate(events, sfdLogPage, SFD_LOG_PAGE_SIZE);
    sfdLogPage = page;

    tbody.innerHTML = '';
    items.forEach(ev => {
        const when = new Date(ev.occurred_at).toLocaleString('vi-VN');
        const zone = (ev.detail || {}).zone_name || ev.area_name || '—';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="font-mono">${esc(when)}</td>
            <td><strong>${esc(ev.message)}</strong></td>
            <td>${esc(zone)}</td>
            <td>${ev.snapshot_url
                ? `<img class="evidence-thumb" src="${ev.snapshot_url}"
                        onclick="openEvidence('${ev.snapshot_url}','${esc(ev.message)} — ${esc(when)}')"
                        alt="Ảnh vi phạm">`
                : '<span class="muted">—</span>'}</td>
            <td>${ev.clip_url
                ? `<button class="btn-event-clip" onclick="viewEventClip('${ev.id}')">▶️ Xem clip 10s</button>`
                : '<span class="muted">Không có</span>'}</td>
            <td>${esc(ev.acked_by || '—')}
                ${ev.acked_at ? `<div class="cell-subtext font-mono">${esc(new Date(ev.acked_at).toLocaleString('vi-VN'))}</div>` : ''}</td>`;
        tbody.appendChild(tr);
    });

    renderPager(pagerEl, page, totalPages, (p) => {
        sfdLogPage = p;
        renderSafetyLog(events);
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
    // Vi phạm an toàn phải thấy ngay dù đang ở màn nào, nên báo bằng lớp phủ
    // toàn màn hình kèm ảnh AI chụp, không chỉ cập nhật dashboard.
    showIntrusionAlert(event);

    if (currentTabName === 'safety') {
        setActiveIntrusion(event);
        loadSafetyDashboard();
    } else if (currentTabName === 'safety-detail') {
        setActiveIntrusion(event);
        loadSafetyDetail();
    }
}

let pendingIntrusion = null;

function showGlobalAlert(message) {
    const bar = document.getElementById('global-alert');
    if (!bar) return;
    document.getElementById('global-alert-text').textContent = message;
    bar.style.display = 'flex';
    bar.classList.toggle('blinking', !isSafetySirenMuted);
}
window.showGlobalAlert = showGlobalAlert;

function hideGlobalAlert() {
    const bar = document.getElementById('global-alert');
    if (!bar) return;
    bar.style.display = 'none';
    bar.classList.remove('blinking');
    // Chỉ ẩn khỏi màn hình, sự kiện vẫn nằm trong danh sách chờ xử lý
    pendingIntrusion = null;
}
window.hideGlobalAlert = hideGlobalAlert;

// Vi phạm an toàn phải thấy ngay dù đang ở màn nào, nhưng che kín màn hình thì
// người trực không làm được gì khác. Nên chỉ một dải đỏ bám đỉnh, bấm vào là
// sang thẳng trang An toàn và thấy camera nào đang báo động.
function showIntrusionAlert(event) {
    pendingIntrusion = event;
    const zone = (event.detail || {}).zone_name;
    showGlobalAlert(zone
        ? `Có người đi vào ${zone}`
        : (event.message || 'Có người đi vào vùng cấm'));
}

function openSafetyFromAlert() {
    const bar = document.getElementById('global-alert');
    if (bar) bar.classList.remove('blinking');
    switchNavTab('safety');
}
window.openSafetyFromAlert = openSafetyFromAlert;

async function ackActiveIntrusion() {
    if (!activeIntrusion) return;
    await ackEvent(activeIntrusion.id);
    setActiveIntrusion(null);
}
window.ackActiveIntrusion = ackActiveIntrusion;

function toggleSafetySiren() {
    isSafetySirenMuted = !isSafetySirenMuted;
    const label = isSafetySirenMuted ? '🔔 Bật cảnh báo âm thanh' : '🔕 Tắt cảnh báo âm thanh';
    ['btn-safety-siren'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.textContent = label;
    });

    const banner = document.getElementById('safety-alert-banner');
    if (banner) banner.classList.toggle('blinking', !isSafetySirenMuted && !!activeIntrusion);
    const bar = document.getElementById('global-alert');
    if (bar) bar.classList.toggle('blinking', !isSafetySirenMuted && !!pendingIntrusion);
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
            '<tr><td colspan="8" class="empty-row">Chưa có thiết bị camera nào</td></tr>';

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
                <td class="cam-clock-cell" id="cam-clock-${c.id}">Đang kiểm tra…</td>
                <td class="row-actions">
                    <button class="btn-event-clip" onclick="toggleCameraRun('${c.id}', ${running}, '${esc(c.name)}', this)">
                        ${running ? '⏹ Dừng' : '▶ Chạy'}</button>
                    <button class="btn-event-clip" onclick='openCameraStreamModal(${JSON.stringify(c)})'>📺 Xem luồng</button>
                    <button class="btn-event-clip" onclick='openCameraModal(${JSON.stringify(c)})'>Sửa</button>
                    <button class="btn-row-danger" onclick="deleteCamera('${c.id}','${esc(c.name)}')">Xoá</button>
                </td>`;
            tbody.appendChild(tr);
            refreshCameraClock(c.id);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-row">Lỗi tải danh sách: ${esc(e.message)}</td></tr>`;
    }
}
window.loadCameras = loadCameras;

// ---------- "Giờ camera" trong bảng quản lý thiết bị ----------
// Backend đang được một agent khác dựng song song (/api/v1/cameras/{id}/clock
// và .../clock/sync) nên phải chịu được 404/500 — hiện thông báo gọn, không
// vỡ cả bảng.
async function refreshCameraClock(cameraId) {
    const cell = document.getElementById(`cam-clock-${cameraId}`);
    if (!cell) return;
    try {
        const res = await fetch(`/api/v1/cameras/${cameraId}/clock`);
        if (!res.ok) throw new Error(String(res.status));
        const data = await res.json();
        const offset = Math.round(data.offset_s || 0);
        const sign = offset >= 0 ? '+' : '';
        cell.innerHTML = `<span class="cell-subtext">lệch ${sign}${offset}s</span>
            <button class="btn-event-clip" onclick="syncCameraClock('${cameraId}')">⏱ Đồng bộ giờ</button>`;
    } catch (e) {
        cell.innerHTML = `<span class="cell-subtext">Không đọc được giờ camera</span>
            <button class="btn-event-clip" onclick="syncCameraClock('${cameraId}')">⏱ Đồng bộ giờ</button>`;
    }
}
window.refreshCameraClock = refreshCameraClock;

async function syncCameraClock(cameraId) {
    const cell = document.getElementById(`cam-clock-${cameraId}`);
    if (cell) cell.innerHTML = '<span class="cell-subtext">Đang đồng bộ…</span>';
    try {
        const res = await fetch(`/api/v1/cameras/${cameraId}/clock/sync`, { method: 'POST' });
        if (!res.ok) throw new Error(String(res.status));
        showToast('Đã đồng bộ giờ camera', 'success');
    } catch (e) {
        showToast('Không đồng bộ được giờ camera', 'error');
    } finally {
        refreshCameraClock(cameraId);
    }
}
window.syncCameraClock = syncCameraClock;

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
    document.getElementById('cam-substream-uri').value = cam.substream_uri || '';
    document.getElementById('cam-fps').value = cam.target_fps || 5;
    document.getElementById('cam-form-status').textContent = '';
    modal.style.display = 'flex';
}
window.openCameraModal = openCameraModal;

// Xem thử luồng ngay trong phần quản lý thiết bị. Chỉ mở luồng khi modal đang
// mở, đóng là ngắt để không có kết nối MJPEG chạy ngầm.
function openCameraStreamModal(camera) {
    const modal = document.getElementById('camera-stream-modal');
    if (!modal) return;

    document.getElementById('cam-stream-title').textContent = `📺 ${camera.name}`;
    document.getElementById('cam-stream-subtitle').textContent =
        [camera.code || camera.id, camera.area_name].filter(Boolean).join(' · ');

    const img = document.getElementById('cam-stream-img');
    const hint = document.getElementById('cam-stream-hint');
    if (camera.status === 'online') {
        attachStream(img, camera.id, true);
        img.style.display = '';
        hint.style.display = 'none';
    } else {
        detachStream(img);
        img.style.display = 'none';
        hint.textContent = 'Camera chưa chạy — bấm ▶ Chạy ở dòng tương ứng rồi xem lại.';
        hint.style.display = '';
    }
    modal.style.display = 'flex';
}
window.openCameraStreamModal = openCameraStreamModal;

function closeCameraStreamModal() {
    const modal = document.getElementById('camera-stream-modal');
    if (modal) modal.style.display = 'none';
    detachStream(document.getElementById('cam-stream-img'));
}
window.closeCameraStreamModal = closeCameraStreamModal;

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
        substream_uri: document.getElementById('cam-substream-uri').value.trim() || null,
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

// ``btnEl``: nút vừa bấm (ô lưới giám sát hoặc dòng trong bảng Thiết bị camera
// — cùng một hàm, dùng chung) — để khoá/đổi chữ ngay, không chờ nạp lại xong
// mới thấy phản hồi. ``cameraName`` chỉ dùng cho hộp thoại xác nhận khi dừng.
async function toggleCameraRun(cameraId, isRunning, cameraName, btnEl) {
    if (isRunning && !confirm(`Dừng camera ${cameraName || cameraId}?`)) return;

    const prevText = btnEl ? btnEl.textContent : null;
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.textContent = isRunning ? 'Đang dừng…' : 'Đang chạy…';
    }

    try {
        const res = await fetch(`/api/v1/cameras/${cameraId}/${isRunning ? 'stop' : 'start'}`,
                                { method: 'POST' });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        activeCameraId = cameraId;
        // Cả hai: lưới giám sát (nếu đang mở) và bảng quản lý thiết bị (nếu
        // đang mở) — hàm nào không tìm thấy phần tử của mình thì tự bỏ qua.
        setTimeout(() => { loadCameraWall(); loadCameras(); }, 800);
    } catch (e) {
        showToast(e.message, 'error');
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = prevText;
        }
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
        showToast(e.message, 'error');
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
    cbqh: { home: 'schedule-progress' },
    qtht: { home: 'monitoring' }
};

let currentRole = null;
let currentUser = null;

function applyRole(user) {
    currentUser = user;
    currentRole = user.role;

    // Mục chỉ dành cho QTHT thì ẩn với CBQH
    document.querySelectorAll('.role-only').forEach(el => {
        el.style.display = el.dataset.role === currentRole ? '' : 'none';
    });

    const nameEl = document.getElementById('user-name');
    const roleEl = document.getElementById('user-role');
    const avatarEl = document.getElementById('user-avatar');
    if (nameEl) nameEl.textContent = user.display_name;
    if (roleEl) roleEl.textContent = user.role_label;
    if (avatarEl) avatarEl.textContent = user.avatar;
}
window.applyRole = applyRole;

function fillDemoLogin(username) {
    document.getElementById('login-username').value = username;
    document.getElementById('login-password').value = `${username}@2026`;
    document.getElementById('login-error').textContent = '';
}
window.fillDemoLogin = fillDemoLogin;

async function handleLogin(event) {
    event.preventDefault();
    const btn = document.getElementById('login-submit');
    const err = document.getElementById('login-error');
    err.textContent = '';
    btn.disabled = true;

    try {
        const res = await fetch('/api/v1/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: document.getElementById('login-username').value,
                password: document.getElementById('login-password').value
            })
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));

        // Đăng nhập đúng là server đã set cookie phiên (HttpOnly, cùng gốc) —
        // không cần tự giữ gì ở client, /auth/me lúc nạp lại trang sẽ hỏi lại
        // server, không tin localStorage nữa (client tự sửa được, không chứng
        // minh được gì về quyền thật).
        const user = await res.json();
        enterApp(user);
    } catch (e) {
        err.textContent = `✗ ${e.message}`;
        err.style.color = '#dc2626';
        document.getElementById('login-password').value = '';
    } finally {
        btn.disabled = false;
    }
}
window.handleLogin = handleLogin;

function enterApp(user) {
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('app-layout').style.display = '';
    applyRole(user);
    startAppSession();
    switchNavTab(ROLES[user.role].home);
}

async function handleLogout() {
    try { await fetch('/api/v1/auth/logout', { method: 'POST' }); } catch (e) { /* mất mạng: cookie hết hạn tự nhiên theo AUTH_SESSION_HOURS */ }
    try { localStorage.removeItem('horus_user'); } catch (e) { /* chế độ riêng tư */ }
    // Nạp lại trang cho sạch: đóng kênh sự kiện, luồng hình và các hẹn giờ
    window.location.reload();
}
window.handleLogout = handleLogout;


// =====================================================================
// KHỞI TẠO
// Đặt cuối file để mọi biến trạng thái phía trên đã được khai báo xong.
// =====================================================================

let appSessionStarted = false;

async function startAppSession() {
    if (appSessionStarted) return;
    appSessionStarted = true;

    loadRegisteredFaces();
    refreshCameraTotal();
    connectEventStream();
    startLivePolling();

    // Mặc định xem lịch hôm nay; người dùng mở rộng bằng hai ô khoảng ngày
    const today = new Date().toISOString().slice(0, 10);
    ['dt-date-from', 'dt-date-to'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = today;
    });

    // Nạp sẵn các sự kiện gần đây để dòng sự kiện không trống khi mới vào
    try {
        const recent = await getJson('/api/v1/events?page_size=20&acked=false');
        recent.items.slice().reverse().forEach(ev => {
            lastEventId = lastEventId || ev.id;
            renderEventCard(eventsListContainer, ev, true);
        });
        pendingEventsCount = recent.items.filter(e => !e.acked).length;
        if (pendingEventsBadge) pendingEventsBadge.textContent = `${pendingEventsCount} chờ xử lý`;
    } catch (e) {
        console.error('Không nạp được sự kiện gần đây:', e);
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    // Khối "tài khoản demo" ở màn đăng nhập: chỉ hiện khi server bật
    // SHOW_DEMO_ACCOUNTS — route /system/config công khai, gọi được trước khi
    // có phiên. Không chặn màn đăng nhập hiện ra nếu lời gọi này lỗi.
    try {
        const config = await getJson('/api/v1/system/config');
        const demoHint = document.querySelector('.login-hint');
        if (demoHint) demoHint.style.display = config.show_demo_accounts ? '' : 'none';
    } catch (e) { /* không lấy được cấu hình thì cứ để mặc định của HTML */ }

    // Phiên đăng nhập sống trong cookie HttpOnly (server quản) — hỏi /auth/me
    // để biết có đang đăng nhập hay không, KHÔNG tin localStorage: giá trị đó
    // phía client tự sửa được, không chứng minh được gì về quyền thật.
    try {
        const res = await fetch('/api/v1/auth/me');
        if (res.ok) {
            const user = await res.json();
            if (ROLES[user.role]) {
                enterApp(user);
                return;
            }
        }
    } catch (e) { /* lỗi mạng: coi như chưa đăng nhập, hiện màn đăng nhập */ }

    try { localStorage.removeItem('horus_user'); } catch (e) { /* chế độ riêng tư */ }
    document.getElementById('login-screen').style.display = 'flex';
    document.getElementById('app-layout').style.display = 'none';
});
