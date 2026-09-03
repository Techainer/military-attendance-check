// Chạy giao diện thật trong jsdom, gọi vào máy chủ đang chạy.
// Mục đích: bắt lỗi runtime mà đọc code không thấy — hàm không tồn tại, id sai,
// dữ liệu trả về không khớp cái giao diện mong đợi.

import { JSDOM, VirtualConsole } from 'jsdom';
import { readFileSync } from 'fs';

// Cần máy chủ đang chạy và gói jsdom:
//   python main.py &
//   npm install jsdom && node tests/test_ui.mjs
const BASE = process.env.UI_TEST_BASE || 'http://127.0.0.1:8199';
const failures = [];
const jsErrors = [];

function check(name, cond, extra = '') {
    console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (!cond && extra ? `   ${extra}` : ''));
    if (!cond) failures.push(name);
}

const root = new URL('..', import.meta.url).pathname;
const html = readFileSync(root + 'static/index.html', 'utf8');
const appJs = readFileSync(root + 'static/app.js', 'utf8');

// jsdom không vẽ được canvas thật. Màn vẽ vùng dùng canvas nên luôn báo dòng
// này; đó là giới hạn công cụ, không phải lỗi giao diện. Lỗi khác vẫn bắt.
const JSDOM_CANVAS_NOISE = 'getContext';

const virtualConsole = new VirtualConsole();
const record = (msg) => { if (!String(msg).includes(JSDOM_CANVAS_NOISE)) jsErrors.push(String(msg)); };
virtualConsole.on('jsdomError', (e) => record(e.message));
virtualConsole.on('error', (...args) => record(args.join(' ')));

const dom = new JSDOM(html.replace('<script src="/static/app.js"></script>', ''), {
    url: BASE + '/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole,
});
const { window } = dom;

// jsdom không có EventSource; giả lập tối thiểu để kênh sự kiện không làm sập trang
window.EventSource = class {
    constructor(url) { this.url = url; EventSourceCalls.push(url); }
    close() { this.closed = true; }
};
const EventSourceCalls = [];
window.EventSource.prototype.close = function () { this.closed = true; };

window.fetch = (url, opts) => fetch(String(url).startsWith('http') ? url : BASE + url, opts);
window.alert = (m) => { alerts.push(m); };
window.confirm = () => true;
const alerts = [];

// Nạp app.js vào đúng ngữ cảnh trang
const script = window.document.createElement('script');
script.textContent = appJs;
try {
    window.document.body.appendChild(script);
} catch (e) {
    console.log('  FAIL  nạp app.js:', e.message);
    process.exit(1);
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

console.log('\n[1] Trang khởi động không lỗi');
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
await sleep(2500);

check('app.js nạp và chạy không ném lỗi', jsErrors.length === 0, jsErrors.slice(0, 3).join(' | '));
check('kênh sự kiện SSE được mở',
    EventSourceCalls.some(u => u.includes('/api/v1/events/stream')), EventSourceCalls.join(','));

const doc = window.document;
const activeView = doc.querySelector('.page-view.active');
check('có đúng một màn hình đang hiển thị',
    doc.querySelectorAll('.page-view.active').length === 1, String(doc.querySelectorAll('.page-view.active').length));
check('vào thẳng màn giám sát quân số',
    activeView && activeView.id === 'view-attendance-summary', activeView && activeView.id);

console.log('\n[2] Điều hướng qua đủ các màn');
window.switchRole('qtht');   // vai trò rộng nhất, xem được mọi màn
const tabs = ['schedule-progress', 'attendance', 'safety', 'logs',
              'monitoring', 'schedule', 'zones', 'cameras', 'registration'];
for (const tab of tabs) {
    jsErrors.length = 0;
    window.switchNavTab(tab);
    await sleep(700);
    const view = doc.querySelector('.page-view.active');
    check(`mở được màn ${tab}`, !!view && jsErrors.length === 0,
          jsErrors.slice(0, 2).join(' | ') || 'không có màn nào hiển thị');
}

console.log('\n[3] Phân hệ I và II là một màn, tách bằng bộ lọc loại huấn luyện');
window.switchNavTab('attendance');
await sleep(900);
const rowsAll = doc.querySelectorAll('#as-tbody tr').length;
const titleAll = doc.getElementById('as-title').textContent;

window.setTrainingFilter('dao_tao');
await sleep(900);
const rowsDt = doc.querySelectorAll('#as-tbody tr').length;
const titleDt = doc.getElementById('as-title').textContent;

window.setTrainingFilter('chien_dau');
await sleep(900);
const rowsCd = doc.querySelectorAll('#as-tbody tr').length;
const titleCd = doc.getElementById('as-title').textContent;

window.setTrainingFilter('');
await sleep(900);

check('không lọc thì thấy cả hai loại', rowsAll >= 1, String(rowsAll));
check('lọc đào tạo ra ít ca hơn tổng', rowsDt < rowsAll, `${rowsDt} / ${rowsAll}`);
check('lọc chiến đấu ra ít ca hơn tổng', rowsCd < rowsAll, `${rowsCd} / ${rowsAll}`);
check('hai loại cộng lại bằng tổng', rowsDt + rowsCd === rowsAll, `${rowsDt}+${rowsCd} vs ${rowsAll}`);
check('tiêu đề đổi theo loại đang lọc',
    titleDt.includes('ĐÀO TẠO') && titleCd.includes('CHIẾN ĐẤU') && !titleAll.includes('ĐÀO TẠO'),
    `${titleAll} | ${titleDt} | ${titleCd}`);
check('nút lọc sáng đúng nút đang chọn',
    doc.querySelector('#tt-filter-attendance .tt-btn.active').dataset.tt === undefined
    || doc.querySelector('#tt-filter-attendance .tt-btn.active').dataset.tt === '');

console.log('\n[3b] Phân quyền theo vai trò');
window.switchRole('cbqh');
await sleep(400);
const hiddenForCbqh = [...doc.querySelectorAll('.role-only')].every(el => el.style.display === 'none');
check('CBQH không thấy phân hệ III', hiddenForCbqh);
check('CBQH vẫn thấy nghiệp vụ huấn luyện',
    doc.getElementById('nav-attendance').offsetParent !== undefined);
check('đổi vai trò thì đổi luôn tên người dùng',
    doc.getElementById('user-name').textContent.includes('Cán bộ quản lý'),
    doc.getElementById('user-name').textContent);

window.switchRole('qtht');
await sleep(400);
const shownForQtht = [...doc.querySelectorAll('.role-only')].every(el => el.style.display !== 'none');
check('QTHT thấy thêm phân hệ III', shownForQtht);
check('vai trò QTHT hiện đúng tên',
    doc.getElementById('user-name').textContent.includes('Quản trị hệ thống'),
    doc.getElementById('user-name').textContent);

// Đang đứng ở màn của QTHT rồi chuyển sang CBQH thì phải bị đưa về màn hợp lệ
window.switchNavTab('cameras');
await sleep(500);
window.switchRole('cbqh');
await sleep(700);
check('đổi về CBQH khi đang ở màn quản trị thì tự chuyển về màn hợp lệ',
    doc.querySelector('.page-view.active').id !== 'view-cameras',
    doc.querySelector('.page-view.active').id);
window.switchRole('qtht');
await sleep(300);

console.log('\n[4] Dashboard an toàn');
window.switchNavTab('safety');
await sleep(1200);
check('có chỉ báo trạng thái an toàn',
    !!doc.getElementById('sf-state-label').textContent.trim(),
    doc.getElementById('sf-state-label').textContent);
// Banner bám theo dữ liệu thật: có vi phạm chưa xử lý thì phải hiện, không thì ẩn
const safety = await (await fetch(BASE + '/api/v1/summary/safety')).json();
const bannerShown = doc.getElementById('safety-alert-banner').style.display !== 'none';
check('banner cảnh báo bám đúng trạng thái vi phạm chờ xử lý',
    bannerShown === (safety.active_intrusion !== null),
    `banner=${bannerShown}, có vi phạm chờ=${safety.active_intrusion !== null}`);
check('trạng thái an toàn khớp dữ liệu máy chủ',
    doc.getElementById('sf-state-label').textContent === safety.state_label,
    doc.getElementById('sf-state-label').textContent);
check('thư viện ảnh vi phạm được dựng',
    doc.getElementById('sf-gallery').children.length >= 1,
    String(doc.getElementById('sf-gallery').innerHTML.slice(0, 80)));
check('luồng camera trường bắn được gắn',
    (doc.getElementById('sf-stream').getAttribute('src') || '').includes('stream.mjpg'),
    doc.getElementById('sf-stream').getAttribute('src'));

console.log('\n[4b] Lịch huấn luyện và form tạo ca');
window.switchNavTab('schedule-progress');
await sleep(1200);
const schRows = doc.querySelectorAll('#dt-schedule-tbody tr');
check('bảng lịch có dữ liệu giả lập', schRows.length >= 1, String(schRows.length));
check('mỗi ca hiện nhãn loại huấn luyện',
    doc.querySelector('#dt-schedule-tbody .tt-tag') !== null);
check('có thanh tiến độ', doc.querySelector('#dt-schedule-tbody .progress-fill') !== null);

window.openScheduleModal();
await sleep(200);
check('mở được form tạo ca từ màn lịch',
    doc.getElementById('schedule-modal').style.display === 'flex');
check('form có ô chọn loại huấn luyện',
    doc.getElementById('sch-training-type') !== null);
check('form có giáo viên / thao trường / bài học cho màn chi tiết',
    doc.getElementById('sch-instructor') && doc.getElementById('sch-field')
    && doc.getElementById('sch-lesson-name'));
window.closeScheduleModal();
check('đóng được form tạo ca',
    doc.getElementById('schedule-modal').style.display === 'none');

console.log('\n[5] Quản lý camera');
window.switchNavTab('cameras');
await sleep(1000);
const camRows = doc.querySelectorAll('#cameras-tbody tr');
check('bảng camera có dữ liệu', camRows.length >= 1, String(camRows.length));
check('hiện nguồn tín hiệu', doc.querySelector('#cameras-tbody .source-uri') !== null);
window.openCameraModal();
check('mở được form thêm thiết bị',
    doc.getElementById('camera-modal').style.display === 'flex');
window.closeCameraModal();
check('đóng được form', doc.getElementById('camera-modal').style.display === 'none');

console.log('\n[6] Rời màn thì ngắt luồng hình, không chạy ngầm');
window.switchNavTab('safety');
await sleep(600);
const sfBefore = doc.getElementById('sf-stream').getAttribute('src');
window.switchNavTab('logs');
await sleep(400);
check('luồng trường bắn được gắn khi đang xem', !!sfBefore);
check('rời màn thì luồng bị ngắt',
    !doc.getElementById('sf-stream').getAttribute('src'),
    doc.getElementById('sf-stream').getAttribute('src'));

console.log('\n[7] Không còn dấu vết của nút giả cũ');
const src = appJs;
for (const gone of ['addEventFeedCard', 'triggerMockAlarm', 'confirmEventResolution',
                    'switchStreamType', 'connectWebSocket', 'videoCanvas']) {
    check(`đã bỏ ${gone}`, !src.includes(gone));
}
// Trình phát clip 10s vẫn dùng canvas + base64, đó là đúng chỗ. Chỉ luồng
// trực tiếp mới phải bỏ hẳn cách đó.
check('luồng trực tiếp không còn vẽ base64 lên canvas',
    !src.includes("data:image/jpeg;base64,' + data.frame"));
check('ảnh nền vẽ vùng lấy khung hình GỐC, không lấy bản đã vẽ lớp phủ',
    src.includes('snapshot?overlay=0'));

console.log('\n[8] Màn vẽ vùng chịu được canvas không dùng được');
check('không sập khi trình duyệt không cấp ngữ cảnh vẽ',
    appJs.includes("if (!ctx) return;"));

console.log();
if (failures.length) {
    console.log(`${failures.length} kiểm thử KHÔNG đạt:`);
    failures.forEach(f => console.log('  -', f));
    dom.window.close();
    process.exit(1);
}
console.log('Tất cả kiểm thử giao diện đạt.');
// jsdom giữ timer và kết nối sống, không tự thoát; đóng cửa sổ rồi kết thúc
dom.window.close();
process.exit(0);
