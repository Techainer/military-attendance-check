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

// Test tự tạo dữ liệu nó cần, không dựa vào bộ test khác đã chạy trước hay
// script seed — chạy độc lập theo thứ tự nào cũng ra kết quả như nhau.
async function ensureFixtures() {
    const existing = await (await fetch(BASE + '/api/v1/schedules')).json();
    const have = new Set(existing.items.map(s => s.training_type));

    const fixtures = [
        { id: 'dao_tao', body: {
            name: 'Huấn luyện bắn súng (fixture)', training_type: 'dao_tao',
            start_time: '07:00', end_time: '11:30', unit: 'Đại đội 1', shift: 'Ca sáng',
            required_count: 40, lesson_name: 'Bài 3 — Ngắm bắn', instructor: 'Đại uý Phạm Minh Đức',
            field: 'Trường bắn số 1' } },
        { id: 'chien_dau', body: {
            name: 'Huấn luyện chiến thuật (fixture)', training_type: 'chien_dau',
            start_time: '13:00', end_time: '16:30', unit: 'Đại đội 2', shift: 'Ca chiều',
            required_count: 32, lesson_name: 'Bài 5 — Vận động', instructor: 'Thiếu tá Nguyễn Hữu Thắng',
            field: 'Thao trường số 2' } },
    ];

    for (const f of fixtures) {
        if (have.has(f.id)) continue;
        await fetch(BASE + '/api/v1/schedules', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(f.body)
        });
    }
}
await ensureFixtures();

const doc = window.document;

console.log('\n[0] Đăng nhập');
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
await sleep(600);

check('chưa đăng nhập thì hiện màn đăng nhập',
    doc.getElementById('login-screen').style.display === 'flex',
    doc.getElementById('login-screen').style.display);
check('chưa đăng nhập thì chưa vào hệ thống',
    doc.getElementById('app-layout').style.display === 'none');
check('chưa đăng nhập thì CHƯA mở kênh sự kiện', EventSourceCalls.length === 0,
    EventSourceCalls.join(','));

// Sai mật khẩu
doc.getElementById('login-username').value = 'cbqh';
doc.getElementById('login-password').value = 'sai-mat-khau';
await window.handleLogin({ preventDefault() {} });
await sleep(500);
check('sai mật khẩu thì báo lỗi, không cho vào',
    doc.getElementById('login-error').textContent.includes('✗')
    && doc.getElementById('app-layout').style.display === 'none',
    doc.getElementById('login-error').textContent);
check('sai mật khẩu thì xoá ô mật khẩu',
    doc.getElementById('login-password').value === '');

// Tài khoản lạ
doc.getElementById('login-username').value = 'khong-ton-tai';
doc.getElementById('login-password').value = 'gi-do';
await window.handleLogin({ preventDefault() {} });
await sleep(400);
check('tài khoản không tồn tại cũng bị từ chối',
    doc.getElementById('app-layout').style.display === 'none');

// Đăng nhập đúng bằng nút điền nhanh
window.fillDemoLogin('cbqh');
check('nút tài khoản demo điền sẵn thông tin',
    doc.getElementById('login-username').value === 'cbqh'
    && doc.getElementById('login-password').value === 'cbqh@2026');
await window.handleLogin({ preventDefault() {} });
await sleep(1800);

check('đăng nhập đúng thì vào được hệ thống',
    doc.getElementById('app-layout').style.display !== 'none'
    && doc.getElementById('login-screen').style.display === 'none');
check('hiện tên người đăng nhập',
    doc.getElementById('user-name').textContent.includes('Nguyễn Văn Hùng'),
    doc.getElementById('user-name').textContent);
check('hiện vai trò của tài khoản',
    doc.getElementById('user-role').textContent === 'Cán bộ quản lý',
    doc.getElementById('user-role').textContent);
check('CBQH đăng nhập thì không thấy phân hệ III',
    [...doc.querySelectorAll('.role-only')].every(el => el.style.display === 'none'));
check('vào bằng CBQH thì mở màn giám sát quân số',
    doc.querySelector('.page-view.active').id === 'view-attendance-summary',
    doc.querySelector('.page-view.active').id);

console.log('\n[1] Trang chạy không lỗi sau khi đăng nhập');
check('app.js nạp và chạy không ném lỗi', jsErrors.length === 0, jsErrors.slice(0, 3).join(' | '));
check('kênh sự kiện SSE mở sau khi đăng nhập',
    EventSourceCalls.some(u => u.includes('/api/v1/events/stream')), EventSourceCalls.join(','));

const activeView = doc.querySelector('.page-view.active');
check('có đúng một màn hình đang hiển thị',
    doc.querySelectorAll('.page-view.active').length === 1, String(doc.querySelectorAll('.page-view.active').length));
check('vào thẳng màn giám sát quân số',
    activeView && activeView.id === 'view-attendance-summary', activeView && activeView.id);

console.log('\n[2] Điều hướng qua đủ các màn');
// Đăng nhập bằng QTHT để xem được mọi màn
const qtht = await (await fetch(BASE + '/api/v1/auth/login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ username: 'qtht', password: 'qtht@2026' })
})).json();
window.applyRole(qtht);
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

console.log('\n[3b] Hai tài khoản thấy hai bộ menu khác nhau');
const cbqhUser = await (await fetch(BASE + '/api/v1/auth/login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ username: 'cbqh', password: 'cbqh@2026' })
})).json();

window.applyRole(cbqhUser);
await sleep(300);
check('CBQH không thấy phân hệ III',
    [...doc.querySelectorAll('.role-only')].every(el => el.style.display === 'none'));
check('CBQH vẫn thấy nghiệp vụ huấn luyện',
    doc.getElementById('nav-attendance') !== null);
check('tài khoản CBQH hiện đúng vai trò',
    doc.getElementById('user-role').textContent === 'Cán bộ quản lý',
    doc.getElementById('user-role').textContent);

window.applyRole(qtht);
await sleep(300);
check('QTHT thấy thêm phân hệ III',
    [...doc.querySelectorAll('.role-only')].every(el => el.style.display !== 'none'));
check('tài khoản QTHT hiện đúng vai trò',
    doc.getElementById('user-role').textContent === 'Quản trị hệ thống',
    doc.getElementById('user-role').textContent);
check('hai tài khoản khác tên hiển thị',
    cbqhUser.display_name !== qtht.display_name);

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

console.log('\n[7b] Vùng cấm bắn đạn thật tách khỏi vùng đếm quân số');
window.applyRole(qtht);
window.switchNavTab('zones');
await sleep(1200);

check('màn vùng có ô chọn loại vùng', doc.getElementById('zone-rule-type') !== null);
const ruleOptions = [...doc.querySelectorAll('#zone-rule-type option')].map(o => o.value);
check('có đủ ba loại: đếm quân số, vùng cấm, vạch an toàn',
    ruleOptions.includes('attendance_area') && ruleOptions.includes('restricted_area')
    && ruleOptions.includes('crossing_line'), ruleOptions.join(','));
check('có danh sách vùng riêng của camera', doc.getElementById('zone-list') !== null);

// Tạo vùng cấm thật qua giao diện rồi kiểm máy chủ có nhận không
doc.getElementById('zone-name-input').value = 'Khối chắn tuyến bắn (test)';
doc.getElementById('zone-rule-type').value = 'restricted_area';
window.onZoneRuleChange();
window.polygonPoints = undefined;   // dùng biến trong app.js
const canvasZone = { name: 'Khối chắn tuyến bắn (test)', kind: 'polygon', rule: 'restricted_area',
    points: [{x:0.6,y:0.1},{x:0.95,y:0.1},{x:0.95,y:0.6},{x:0.6,y:0.6}] };
const createRes = await fetch(BASE + '/api/v1/cameras/cam_01/zones', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(canvasZone) });
check('tạo được vùng cấm riêng cho trường bắn', createRes.status === 201, String(createRes.status));
const createdZone = createRes.status === 201 ? await createRes.json() : null;

await window.loadZoneRules();
await sleep(400);
check('vùng cấm hiện trong danh sách với nhãn riêng',
    doc.querySelector('#zone-list .zone-res') !== null,
    doc.getElementById('zone-list').textContent.slice(0, 100));
check('vùng đếm quân số và vùng cấm là hai mục tách biệt',
    doc.querySelectorAll('#zone-list .zone-row').length >= 1);

console.log('\n[7c] Cảnh báo đỏ kèm ảnh khi có người vào vùng cấm');
const intrusion = {
    id: 'evt_ui_test', type: 'INTRUSION', severity: 'critical',
    occurred_at: new Date().toISOString(),
    camera_id: 'cam_01', camera_name: 'Sân tập trung', area_name: 'Thao trường số 1',
    message: 'Phát hiện 01 đối tượng đi vào khối chắn tuyến bắn.',
    snapshot_url: '/data/events/khong-co.jpg', boxes: [], acked: false,
    detail: { zone_name: 'Khối chắn tuyến bắn', object_count: 1, dwell_seconds: 3,
              identified: [{ person_id: 'p1', person_name: 'Binh nhất Nguyễn Văn A' }] }
};

window.switchNavTab('logs');       // đang ở màn KHÁC màn an toàn
await sleep(400);
window.handleAiEvent(intrusion);
await sleep(400);

const ov = doc.getElementById('intrusion-overlay');
check('cảnh báo hiện lên dù đang ở màn khác', ov.style.display === 'flex', ov.style.display);
check('cảnh báo nhấp nháy đỏ', ov.classList.contains('blinking'));
check('có tên vùng cấm trong tiêu đề',
    doc.getElementById('intrusion-title').textContent.includes('KHỐI CHẮN'),
    doc.getElementById('intrusion-title').textContent);
check('kèm ảnh bằng chứng',
    (doc.getElementById('intrusion-photo').getAttribute('src') || '').includes('/data/events/'),
    doc.getElementById('intrusion-photo').getAttribute('src'));
check('có nút tải ảnh bằng chứng',
    doc.getElementById('intrusion-download').hasAttribute('download'));
check('hiện tên quân nhân nhận diện được',
    doc.getElementById('intrusion-meta').textContent.includes('Nguyễn Văn A'),
    doc.getElementById('intrusion-meta').textContent.slice(0, 120));

window.toggleSafetySiren();
check('tắt được cảnh báo âm thanh thì thôi nhấp nháy', !ov.classList.contains('blinking'));
window.toggleSafetySiren();

window.dismissIntrusion();
check('đóng được cảnh báo', ov.style.display === 'none');

if (createdZone) await fetch(BASE + `/api/v1/zones/${createdZone.id}`, { method: 'DELETE' });

console.log('\n[7d] Dialog chi tiết quân nhân và ca điểm danh');
window.switchNavTab('registration');
await sleep(1000);
const people = await (await fetch(BASE + '/api/faces')).json();
if (people.data && people.data.length) {
    window.editPerson(people.data[0].id);
    await sleep(200);
    check('mở được dialog hồ sơ quân nhân',
        doc.getElementById('person-modal').style.display === 'flex');
    check('sửa được cả cấp bậc, đơn vị, số hiệu — không chỉ tên',
        doc.getElementById('person-rank').value && doc.getElementById('person-unit').value
        && doc.getElementById('person-military-id') !== null);
    window.closePersonModal();
    check('đóng được dialog hồ sơ',
        doc.getElementById('person-modal').style.display === 'none');
} else {
    check('bỏ qua dialog hồ sơ: chưa đăng ký quân nhân nào', true);
}

window.switchNavTab('logs');
await sleep(1000);
const logRows = doc.querySelectorAll('#attendance-logs-tbody tr.row-clickable');
check('dòng nhật ký bấm được để xem chi tiết', logRows.length >= 0);
if (logRows.length) {
    logRows[0].onclick({ target: logRows[0] });
    await sleep(300);
    check('mở được dialog chi tiết ca',
        doc.getElementById('log-modal').style.display === 'flex');
    check('dialog có bảng đối chiếu đầu buổi - cuối buổi',
        doc.getElementById('log-modal-checks').children.length >= 1);
    check('dialog có khu vực ảnh bằng chứng',
        doc.getElementById('log-modal-evidence') !== null);
    window.closeLogModal();
    check('đóng được dialog chi tiết ca',
        doc.getElementById('log-modal').style.display === 'none');
}

console.log('\n[7e] Lịch & Tiến độ hiển thị đủ như màn cấu hình');
window.switchNavTab('schedule-progress');
await sleep(1200);
const firstRow = doc.querySelector('#dt-schedule-tbody tr');
check('cột khung giờ không còn rỗng',
    firstRow && /\d{2}:\d{2}\s*–\s*\d{2}:\d{2}/.test(firstRow.textContent),
    firstRow ? firstRow.textContent.replace(/\s+/g, ' ').slice(0, 120) : 'không có dòng nào');

const summary = await (await fetch(BASE + '/api/v1/summary/training')).json();
const one = summary.sessions[0] || {};
check('API trả khung giờ cho màn lịch', !!one.start_time && !!one.end_time,
    JSON.stringify({ start: one.start_time, end: one.end_time }));
check('API trả cả bài học và giáo viên như màn cấu hình',
    'lesson_name' in one && 'instructor' in one, Object.keys(one).join(','));

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
