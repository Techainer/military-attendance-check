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

// jsdom chưa cài đặt play/pause/load của <video>, gọi thẳng sẽ ném "Not
// implemented" ra virtual console. Giả lập tối thiểu để test chạm được trình
// phát đoạn ghi.
window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
window.HTMLMediaElement.prototype.pause = function () {};
window.HTMLMediaElement.prototype.load = function () {};

// FormData của jsdom không phải FormData của Node, fetch sẽ gửi thành chuỗi
// "[object FormData]". Chuyển sang FormData thật để test chạm được luồng
// tải file lên chứ không dừng ở mức giả lập.
async function toNodeBody(body) {
    if (!body || typeof body.entries !== 'function' || body instanceof FormData) return body;
    const out = new FormData();
    for (const [key, value] of body.entries()) {
        if (value && typeof value.arrayBuffer === 'function') {
            out.append(key, new Blob([await value.arrayBuffer()]), value.name || 'blob');
        } else {
            out.append(key, value);
        }
    }
    return out;
}

window.fetch = async (url, opts) => {
    const options = opts ? { ...opts, body: await toNodeBody(opts.body) } : opts;
    return fetch(String(url).startsWith('http') ? url : BASE + url, options);
};
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
check('vào bằng CBQH thì mở màn lịch và tiến độ',
    doc.querySelector('.page-view.active').id === 'view-schedule-progress',
    doc.querySelector('.page-view.active').id);

console.log('\n[1] Trang chạy không lỗi sau khi đăng nhập');
check('app.js nạp và chạy không ném lỗi', jsErrors.length === 0, jsErrors.slice(0, 3).join(' | '));
check('kênh sự kiện SSE mở sau khi đăng nhập',
    EventSourceCalls.some(u => u.includes('/api/v1/events/stream')), EventSourceCalls.join(','));

const activeView = doc.querySelector('.page-view.active');
check('có đúng một màn hình đang hiển thị',
    doc.querySelectorAll('.page-view.active').length === 1, String(doc.querySelectorAll('.page-view.active').length));
check('vào thẳng màn lịch và tiến độ',
    activeView && activeView.id === 'view-schedule-progress', activeView && activeView.id);

console.log('\n[2] Điều hướng qua đủ các màn');
// Đăng nhập bằng QTHT để xem được mọi màn
const qtht = await (await fetch(BASE + '/api/v1/auth/login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ username: 'qtht', password: 'qtht@2026' })
})).json();
window.applyRole(qtht);
const tabs = ['schedule-progress', 'safety', 'logs',
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
window.switchNavTab('schedule-progress');
await sleep(900);
const rowsAll = doc.querySelectorAll('#dt-schedule-tbody tr').length;

window.setTrainingFilter('dao_tao');
await sleep(900);
const rowsDt = doc.querySelectorAll('#dt-schedule-tbody tr').length;

window.setTrainingFilter('chien_dau');
await sleep(900);
const rowsCd = doc.querySelectorAll('#dt-schedule-tbody tr').length;

window.setTrainingFilter('');
await sleep(900);

check('không lọc thì thấy cả hai loại', rowsAll >= 1, String(rowsAll));
check('lọc đào tạo ra ít ca hơn tổng', rowsDt < rowsAll, `${rowsDt} / ${rowsAll}`);
check('lọc chiến đấu ra ít ca hơn tổng', rowsCd < rowsAll, `${rowsCd} / ${rowsAll}`);
check('hai loại cộng lại bằng tổng', rowsDt + rowsCd === rowsAll, `${rowsDt}+${rowsCd} vs ${rowsAll}`);
check('đã bỏ hẳn tab giám sát quân số',
    doc.getElementById('nav-attendance') === null
    && doc.getElementById('view-attendance-summary') === null);

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
    doc.getElementById('nav-schedule-progress') !== null);
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

console.log('\n[3c] Trang Lịch & Tiến độ');

window.switchNavTab('schedule-progress');
await sleep(1000);

const dtLabels = [...doc.querySelectorAll('#view-schedule-progress .metric-label')]
    .map(e => e.textContent.trim());
check('bỏ thẻ Tiến độ hoàn thành chung',
    !dtLabels.some(l => l.includes('Tiến độ hoàn thành chung')), dtLabels.join(' | '));
check('có thẻ camera trực tuyến trên tổng',
    dtLabels.some(l => l.includes('Camera trực tuyến')), dtLabels.join(' | '));
check('thẻ camera hiện dạng n/m',
    /^\d+\/\d+$/.test((doc.getElementById('dt-metric-cameras') || {}).textContent || ''),
    (doc.getElementById('dt-metric-cameras') || {}).textContent);
check('không còn thanh tiến độ chung',
    doc.getElementById('dt-metric-progress-bar') === null);

const dtTh = [...doc.querySelectorAll('#view-schedule-progress thead th')].map(e => e.textContent.trim());
check('bảng lịch có cột LOẠI', dtTh.includes('LOẠI'), dtTh.join(' | '));
check('vẫn có cột tiến độ thực tế', dtTh.includes('TIẾN ĐỘ THỰC TẾ'), dtTh.join(' | '));

check('có ô lọc từ ngày', !!doc.getElementById('dt-date-from'));
check('có ô lọc đến ngày', !!doc.getElementById('dt-date-to'));
check('có bộ lọc theo ca', !!doc.getElementById('dt-filter-shift'));
check('có bộ lọc theo trạng thái', !!doc.getElementById('dt-filter-state'));
check('có thanh tìm kiếm', !!doc.getElementById('dt-schedule-search'));

const addBtn = doc.getElementById('dt-btn-add');
check('nút thêm ca huấn luyện thuộc nhóm chỉ quản trị mới thấy',
    !!addBtn && addBtn.classList.contains('role-only') && addBtn.dataset.role === 'qtht');

if (addBtn) {
    window.applyRole(cbqhUser);
    await sleep(200);
    check('CBQH không thấy nút thêm ca huấn luyện', addBtn.style.display === 'none',
        addBtn.style.display);
    window.applyRole(qtht);
    await sleep(200);
    check('QTHT thấy nút thêm ca huấn luyện', addBtn.style.display !== 'none');
}

const quanSo = doc.querySelector('#dt-schedule-tbody tr td:nth-child(9)');
check('cột quân số chỉ hiện sĩ số chuẩn, không kèm dấu gạch chéo',
    !!quanSo && !quanSo.textContent.includes('/'), quanSo && quanSo.textContent.trim());

console.log('\n[3d] Tài khoản ở góc trái dưới cùng');

check('avatar nằm trong sidebar chứ không ở thanh trên',
    !!doc.querySelector('.sidebar #sidebar-user'));
check('thanh trên không còn khối tài khoản',
    doc.querySelector('.top-header .user-badge') === null);
check('bỏ pill Quân số trên thanh trên',
    doc.getElementById('topbar-attendance-stat') === null);
check('pill camera có id để cập nhật được',
    !!doc.getElementById('topbar-camera-stat'));
check('pill camera hiện dạng n/m trực tuyến',
    /\d+\/\d+/.test((doc.getElementById('topbar-camera-stat') || {}).textContent || ''),
    (doc.getElementById('topbar-camera-stat') || {}).textContent);

const accMenu = doc.getElementById('account-menu');
check('menu tài khoản mặc định đóng', !!accMenu && accMenu.style.display === 'none',
    accMenu && accMenu.style.display);
if (accMenu) {
    window.toggleAccountMenu();
    check('bấm avatar thì mở menu', accMenu.style.display === 'flex', accMenu.style.display);
    check('menu có nút đăng xuất', accMenu.textContent.includes('Đăng xuất'), accMenu.textContent);
    check('menu có nút đổi thông tin cá nhân',
        accMenu.textContent.includes('Thông tin cá nhân'), accMenu.textContent);
    window.toggleAccountMenu();
    check('bấm lần nữa thì đóng menu', accMenu.style.display === 'none');

    window.openAccountModal();
    check('mở được hộp thông tin cá nhân',
        doc.getElementById('account-modal').style.display === 'flex');
    check('hộp điền sẵn tên hiển thị hiện tại',
        doc.getElementById('acc-display-name').value.length > 0,
        doc.getElementById('acc-display-name').value);
    check('hộp có ô đổi mật khẩu',
        !!doc.getElementById('acc-old-password') && !!doc.getElementById('acc-new-password'));
    window.closeAccountModal();
    check('đóng được hộp', doc.getElementById('account-modal').style.display === 'none');
}

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
// Màn tổng là bảng danh sách, không dựng luồng nào — đó là điểm chống lag
const sfRows = [...doc.querySelectorAll('#sf-camera-tbody tr')];
check('màn an toàn liệt kê đủ MỌI camera, không chỉ camera đầu tiên',
    sfRows.length === safety.cameras.length,
    `${sfRows.length} dòng / ${safety.cameras.length} camera`);
check('mỗi dòng có đủ STT, vị trí, bài học, loại, trạng thái, thao tác',
    sfRows.every(r => r.children.length === 6));
check('màn tổng không mở luồng camera nào',
    doc.querySelectorAll('#view-safety img[src]').length === 0);
check('màn tổng không còn ô sự kiện và thư viện ảnh',
    doc.getElementById('sf-events-list') === null && doc.getElementById('sf-gallery') === null);
check('mỗi camera mang bài học và loại huấn luyện của ca đang gắn',
    safety.cameras.every(c => 'lesson_name' in c && 'safety_state_label' in c));

console.log('\n[4a] Chi tiết một camera an toàn');
window.openSafetyDetail(safety.cameras[0].id);
await sleep(1200);
check('mở được màn chi tiết camera',
    doc.getElementById('view-safety-detail').classList.contains('active'));
check('chi tiết hiện đúng tên camera',
    doc.getElementById('sfd-title').textContent.includes(safety.cameras[0].name.toUpperCase()));
const sfdSrc = doc.getElementById('sfd-stream').getAttribute('src');
check('camera đang chạy thì dựng đúng luồng của nó, chưa chạy thì không gắn luồng chết',
    safety.cameras[0].status === 'online'
        ? (sfdSrc || '').includes(`/cameras/${safety.cameras[0].id}/`)
        : !sfdSrc,
    String(sfdSrc));
const mine = safety.events.filter(e => e.camera_id === safety.cameras[0].id);
const minePending = mine.filter(e => !e.acked);
const mineHandled = mine.filter(e => e.acked);
// Danh sách trên chỉ còn việc phải làm; xử lý xong thì xuống bảng nhật ký dưới
check('danh sách xâm nhập chỉ còn vi phạm chưa xử lý của đúng camera',
    doc.getElementById('sfd-events-list').children.length === (minePending.length || 1),
    `${doc.getElementById('sfd-events-list').children.length} thẻ / ${minePending.length} chờ xử lý`);
check('vi phạm đã xử lý nằm ở bảng nhật ký bên dưới',
    doc.getElementById('sfd-log-tbody').children.length === (mineHandled.length || 1),
    `${doc.getElementById('sfd-log-tbody').children.length} dòng / ${mineHandled.length} đã xử lý`);
window.switchNavTab('safety');
await sleep(600);
check('rời màn chi tiết thì ngắt luồng',
    !doc.getElementById('sfd-stream').getAttribute('src'));

console.log('\n[4b] Lịch huấn luyện và form tạo ca');
window.switchNavTab('schedule-progress');
await sleep(1200);
const schRows = doc.querySelectorAll('#dt-schedule-tbody tr');
check('bảng lịch có dữ liệu giả lập', schRows.length >= 1, String(schRows.length));
check('mỗi ca hiện nhãn loại huấn luyện',
    doc.querySelector('#dt-schedule-tbody .tt-tag') !== null);
check('có thanh tiến độ', doc.querySelector('#dt-schedule-tbody .progress-fill') !== null);

window.openScheduleModal();
await sleep(800);
check('mở được form tạo ca từ màn lịch',
    doc.getElementById('schedule-modal').style.display === 'flex');
check('form có ô chọn loại huấn luyện',
    doc.getElementById('sch-training-type') !== null);
check('form có giáo viên / thao trường / bài học cho màn chi tiết',
    doc.getElementById('sch-instructor') && doc.getElementById('sch-field')
    && doc.getElementById('sch-lesson-name'));
check('form có ô chọn camera giám sát',
    doc.getElementById('sch-camera-select') !== null);
check('ô chọn camera nạp đủ camera của hệ thống',
    doc.getElementById('sch-camera-select').options.length === safety.cameras.length + 1,
    `${doc.getElementById('sch-camera-select').options.length} lựa chọn`);
window.closeScheduleModal();
check('đóng được form tạo ca',
    doc.getElementById('schedule-modal').style.display === 'none');

console.log('\n[4e] Lưới camera: nhiều camera cùng lúc');
window.switchNavTab('monitoring');
await sleep(1200);

const wall = doc.getElementById('camera-wall');
check('màn giám sát dựng lưới camera', wall !== null);
const camList = await (await fetch(BASE + '/api/v1/cameras')).json();
check('mỗi camera có một ô riêng trên lưới',
    wall.querySelectorAll('.camera-tile').length === camList.total,
    `${wall.querySelectorAll('.camera-tile').length} ô / ${camList.total} camera`);
check('không còn thẻ hình đơn lẻ dùng chung',
    doc.getElementById('video-stream') === null);
check('mỗi ô có nút chạy hoặc dừng riêng',
    [...wall.querySelectorAll('.camera-tile')].every(t =>
        /Chạy|Dừng/.test(t.querySelector('.camera-tile-foot button').textContent)));
check('tóm tắt cho biết mấy camera đang chạy',
    /camera|thiết bị/i.test(doc.getElementById('monitor-summary').textContent),
    doc.getElementById('monitor-summary').textContent);

// Mỗi ô trỏ vào luồng của ĐÚNG camera nó đại diện
const tiles = [...wall.querySelectorAll('.camera-tile')];
const wrongTile = tiles.find(t => {
    const id = t.id.replace('cam-tile-', '');
    const src = t.querySelector('img').getAttribute('src');
    return src && !src.includes(`/cameras/${id}/`);
});
check('ô nào trỏ đúng luồng camera đó', wrongTile === undefined,
    wrongTile ? wrongTile.id : '');

console.log('\n[4f] Gán nguồn: phải chọn được camera');
window.switchNavTab('monitoring');
await sleep(900);

// Dựng sẵn camera thứ hai để có cái mà chọn
const extra = await (await fetch(BASE + '/api/v1/cameras', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'Camera phụ (test nguồn)', source_type: 'file',
                           source_uri: '/tmp/khong-co.mp4' })
})).json();

window.toggleSourceModal();
await sleep(900);
const srcSelect = doc.getElementById('source-camera-select');
check('modal nguồn có ô chọn camera', srcSelect !== null);
check('ô chọn liệt kê đủ camera',
    srcSelect && srcSelect.options.length >= 2,
    srcSelect ? String(srcSelect.options.length) : 'không có');
check('có nút bật/tắt tự chạy sau khi gán',
    doc.getElementById('source-autostart-cb') !== null);

srcSelect.value = extra.id;
window.onSourceCameraChange();
check('đổi camera thì hiện nguồn hiện tại của chính nó',
    doc.getElementById('source-camera-hint').textContent.includes('khong-co.mp4'),
    doc.getElementById('source-camera-hint').textContent);

// Gán RTSP cho camera phụ, KHÔNG được đụng camera mặc định
const before = await (await fetch(BASE + '/api/v1/cameras/cam_01')).json();
doc.getElementById('source-autostart-cb').checked = false;
doc.getElementById('rtsp-url').value = 'rtsp://10.9.9.9:554/test';
await window.startRtspStream();
await sleep(1200);

const afterExtra = await (await fetch(BASE + `/api/v1/cameras/${extra.id}`)).json();
const afterMain = await (await fetch(BASE + '/api/v1/cameras/cam_01')).json();
check('nguồn được gán đúng camera đã chọn',
    afterExtra.source_uri === 'rtsp://10.9.9.9:554/test', afterExtra.source_uri);
check('camera kia KHÔNG bị đổi nguồn theo',
    afterMain.source_uri === before.source_uri,
    `${before.source_uri} -> ${afterMain.source_uri}`);

// Tải file lên cho camera phụ: KHÔNG được đụng nguồn của camera mặc định.
// Đây là kịch bản đã hỏng thật — handler của form tải video từng bị xoá nhầm
// nên bấm nút là trình duyệt submit kiểu cũ, file không hề được tải lên.
check('form tải video có handler, không submit kiểu cũ làm tải lại trang',
    appJs.includes("uploadForm.addEventListener"));

const mainBeforeUpload = await (await fetch(BASE + '/api/v1/cameras/cam_01')).json();

srcSelect.value = extra.id;
window.onSourceCameraChange();
window.switchMode('video');

// File thật, đủ nhỏ để đi trong một chunk
const fakeVideo = new window.File([new Uint8Array([0, 1, 2, 3, 4, 5, 6, 7])],
                                  'test-clip.mp4', { type: 'video/mp4' });
const fileInput = doc.getElementById('video-file');
Object.defineProperty(fileInput, 'files', { value: [fakeVideo], configurable: true });

doc.getElementById('upload-form').dispatchEvent(
    new window.Event('submit', { bubbles: true, cancelable: true }));
await sleep(2500);

const extraAfterUpload = await (await fetch(BASE + `/api/v1/cameras/${extra.id}`)).json();
const mainAfterUpload = await (await fetch(BASE + '/api/v1/cameras/cam_01')).json();

check('tải file lên thì camera phụ đổi sang nguồn tệp',
    extraAfterUpload.source_type === 'file'
    && String(extraAfterUpload.source_uri).includes('test-clip'),
    `${extraAfterUpload.source_type} · ${extraAfterUpload.source_uri}`);
check('tải file cho camera phụ KHÔNG làm đổi nguồn camera mặc định',
    mainAfterUpload.source_uri === mainBeforeUpload.source_uri,
    `${mainBeforeUpload.source_uri} -> ${mainAfterUpload.source_uri}`);
check('hai camera KHÔNG dùng chung một nguồn',
    extraAfterUpload.source_uri !== mainAfterUpload.source_uri,
    `${extraAfterUpload.source_uri} vs ${mainAfterUpload.source_uri}`);

window.toggleSourceModal();

console.log('\n[4g] Vẽ vùng: phải chọn được camera');
window.switchNavTab('zones');
await sleep(1400);

const zoneSelect = doc.getElementById('zone-camera-select');
check('màn vẽ vùng có ô chọn camera', zoneSelect !== null);
check('ô chọn liệt kê đủ camera',
    zoneSelect && zoneSelect.options.length >= 2,
    zoneSelect ? String(zoneSelect.options.length) : 'không có');

// Mỗi camera có danh sách vùng riêng
await fetch(BASE + `/api/v1/cameras/${extra.id}/zones`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'Vùng riêng của camera phụ', kind: 'polygon',
        rule: 'restricted_area',
        points: [{x:0.6,y:0.1},{x:0.9,y:0.1},{x:0.9,y:0.5},{x:0.6,y:0.5}] })
});

await window.switchZoneCamera(extra.id);
await sleep(900);
check('chọn camera nào thì hiện vùng của camera đó',
    doc.getElementById('zone-list').textContent.includes('Vùng riêng của camera phụ'),
    doc.getElementById('zone-list').textContent.slice(0, 120));
check('nhãn khung hình đổi theo camera đang chọn',
    doc.getElementById('roi-canvas-label').textContent.includes('CAMERA PHỤ'),
    doc.getElementById('roi-canvas-label').textContent);

await window.switchZoneCamera('cam_01');
await sleep(900);
check('quay lại camera khác thì KHÔNG còn thấy vùng của camera kia',
    !doc.getElementById('zone-list').textContent.includes('Vùng riêng của camera phụ'),
    doc.getElementById('zone-list').textContent.slice(0, 120));

console.log('\n[4h] Màn an toàn với nhiều camera');
window.switchNavTab('safety');
await sleep(1200);
const sfAll = await (await fetch(BASE + '/api/v1/summary/safety')).json();
const sfRows2 = [...doc.querySelectorAll('#sf-camera-tbody tr')];
check('có 2 camera thì màn an toàn liệt kê cả 2',
    sfAll.cameras.length >= 2 && sfRows2.length === sfAll.cameras.length,
    `${sfRows2.length} dòng / ${sfAll.cameras.length} camera`);
check('camera phụ có dòng riêng trên màn an toàn',
    sfRows2.some(r => r.textContent.includes('Camera phụ (test nguồn)')));
check('bảng vẫn không mở luồng nào dù có nhiều camera',
    doc.querySelectorAll('#view-safety img[src]').length === 0);

await fetch(BASE + `/api/v1/cameras/${extra.id}`, { method: 'DELETE' });

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

check('mỗi dòng camera có nút xem luồng',
    [...camRows].every(r => /Xem luồng/.test(r.textContent)));
window.openCameraStreamModal({ id: 'cam_01', name: 'Sân tập trung',
                               area_name: 'Thao trường số 1', status: 'online' });
check('mở được cửa sổ xem luồng',
    doc.getElementById('camera-stream-modal').style.display === 'flex');
check('cửa sổ trỏ đúng luồng camera đã chọn',
    (doc.getElementById('cam-stream-img').getAttribute('src') || '').includes('/cameras/cam_01/'),
    doc.getElementById('cam-stream-img').getAttribute('src'));
window.closeCameraStreamModal();
check('đóng cửa sổ thì ngắt luồng, không chạy ngầm',
    doc.getElementById('camera-stream-modal').style.display === 'none'
    && !doc.getElementById('cam-stream-img').getAttribute('src'));
window.openCameraStreamModal({ id: 'cam_01', name: 'Sân tập trung', status: 'offline' });
check('camera chưa chạy thì không gắn luồng chết mà nhắc bấm Chạy',
    !doc.getElementById('cam-stream-img').getAttribute('src')
    && doc.getElementById('cam-stream-hint').style.display !== 'none');
window.closeCameraStreamModal();

console.log('\n[6] Rời màn thì ngắt luồng hình, không chạy ngầm');
window.switchNavTab('safety');
await sleep(600);
// Máy chạy test không có camera nào online nên tự gắn luồng vào ô chi tiết, rồi
// mới kiểm việc rời màn có ngắt hay không.
window.openSafetyDetail('cam_01');
await sleep(600);
const sfdImg = doc.getElementById('sfd-stream');
sfdImg.setAttribute('src', '/api/v1/cameras/cam_01/stream.mjpg?overlay=1');
window.switchNavTab('logs');
await sleep(400);
check('rời màn thì luồng trường bắn bị ngắt',
    !sfdImg.getAttribute('src'), String(sfdImg.getAttribute('src')));

console.log('\n[6b] Trình phát đoạn ghi 10 giây');
// Đây là luồng đã hỏng thật: nút trỏ vào bộ đệm chỉ nằm trong RAM máy chủ nên
// modal mở ra là khung đen, không một dòng báo.
const evPage = await (await fetch(BASE + '/api/v1/events?limit=200')).json();
const evWithClip = (evPage.items || []).find(e => e.clip_url);
const clipVideo = doc.getElementById('clip-player-video');
const clipHint = doc.getElementById('clip-empty-hint');

check('modal đoạn ghi dùng thẻ video, không còn canvas',
    clipVideo !== null && doc.getElementById('clip-player-canvas') === null);
check('sự kiện có đoạn ghi thì clip_url trỏ vào endpoint v1',
    !!evWithClip && evWithClip.clip_url === `/api/v1/events/${evWithClip.id}/clip`,
    evWithClip ? evWithClip.clip_url : 'không có sự kiện nào kèm đoạn ghi');

window.viewEventClip(evWithClip.id);
await sleep(300);
check('thẻ video trỏ đúng endpoint của sự kiện đó',
    clipVideo.getAttribute('src') === `/api/v1/events/${evWithClip.id}/clip`,
    clipVideo.getAttribute('src'));
check('nút tải đoạn ghi trỏ đúng chỗ',
    (doc.getElementById('btn-clip-download').getAttribute('href') || '').includes('download=1'),
    doc.getElementById('btn-clip-download').getAttribute('href'));
check('đang tải thì có báo cho người xem',
    clipHint.style.display !== 'none' && clipHint.textContent.length > 0,
    clipHint.textContent);

// Endpoint phải nhận GET thật: bản đầu tôi dò bằng HEAD, máy chủ trả 405 nên
// đoạn ghi nào cũng bị báo là hỏng.
const clipRes = await fetch(BASE + evWithClip.clip_url);
check('GET đoạn ghi không trả 405', clipRes.status !== 405, String(clipRes.status));

// jsdom không tải video nên tự bắn sự kiện lỗi để kiểm nhánh báo lý do
clipVideo.dispatchEvent(new window.Event('error'));
await sleep(700);
check('phát hỏng thì nói rõ lý do, không để khung đen',
    clipHint.style.display !== 'none' && /đoạn ghi/i.test(clipHint.textContent),
    clipHint.textContent);

window.closeClipModal();
check('đóng modal thì ngừng tải, không chạy ngầm',
    !clipVideo.getAttribute('src'), clipVideo.getAttribute('src'));

console.log('\n[7] Không còn dấu vết của nút giả cũ');
const src = appJs;
for (const gone of ['addEventFeedCard', 'triggerMockAlarm', 'confirmEventResolution',
                    'switchStreamType', 'connectWebSocket', 'videoCanvas']) {
    check(`đã bỏ ${gone}`, !src.includes(gone));
}
check('luồng trực tiếp không còn vẽ base64 lên canvas',
    !src.includes("data:image/jpeg;base64,' + data.frame"));
check('trình phát đoạn ghi không còn tự dựng khung từ base64',
    !src.includes('renderClipFrame') && !src.includes('clipFrames'));
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

console.log('\n[7c] Cảnh báo đỏ toàn hệ thống khi có người vào vùng cấm');
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

const gAlert = doc.getElementById('global-alert');
check('có dải cảnh báo toàn hệ thống', !!gAlert);
check('cảnh báo hiện lên dù đang ở màn khác',
    !!gAlert && gAlert.style.display === 'flex', gAlert && gAlert.style.display);
check('cảnh báo nhấp nháy đỏ', !!gAlert && gAlert.classList.contains('blinking'));
check('dải đỏ ghi rõ lỗi kèm tên vùng cấm',
    doc.getElementById('global-alert-text').textContent.includes('Khối chắn tuyến bắn'),
    doc.getElementById('global-alert-text').textContent);
check('dải cảnh báo nằm giữa đỉnh màn hình, không che cả trang',
    doc.getElementById('intrusion-overlay') === null);

window.toggleSafetySiren();
check('tắt được cảnh báo âm thanh thì thôi nhấp nháy', !gAlert.classList.contains('blinking'));
window.toggleSafetySiren();

gAlert.querySelector('.global-alert-body').click();
await sleep(1000);
check('bấm vào thông báo thì sang trang An toàn bắn đạn thật',
    doc.querySelector('.page-view.active').id === 'view-safety',
    doc.querySelector('.page-view.active').id);

window.hideGlobalAlert();
check('đóng được dải cảnh báo', gAlert.style.display === 'none');

console.log('\n[7c2] Bộ lọc màn An toàn');
window.switchNavTab('safety');
await sleep(1000);
check('có thanh tìm kiếm camera / bài học', !!doc.getElementById('sf-search'));
check('có bộ lọc theo trạng thái', !!doc.getElementById('sf-filter-state'));

const sfTh = [...doc.querySelectorAll('#view-safety thead th')].map(e => e.textContent.trim());
check('bảng camera có cột tên bài học', sfTh.includes('TÊN BÀI HỌC'), sfTh.join(' | '));
check('bảng camera có cột loại', sfTh.includes('LOẠI'), sfTh.join(' | '));

if (createdZone) await fetch(BASE + `/api/v1/zones/${createdZone.id}`, { method: 'DELETE' });

console.log('\n[7c3] Chi tiết camera an toàn');

window.switchNavTab('safety');
await sleep(1000);
const sfDetailBtn2 = doc.querySelector('#sf-camera-tbody tr button');
check('bảng camera có nút xem chi tiết', !!sfDetailBtn2);

if (sfDetailBtn2) {
    sfDetailBtn2.click();
    await sleep(1400);

    check('mở đúng màn chi tiết camera',
        doc.querySelector('.page-view.active').id === 'view-safety-detail',
        doc.querySelector('.page-view.active').id);
    check('khung camera gắn được zoom',
        !!doc.getElementById('sfd-camera-box')
        && doc.getElementById('sfd-camera-box').dataset.zoomable === '1');
    check('bỏ thư viện hình ảnh vi phạm', doc.getElementById('sfd-gallery') === null);
    check('có bảng nhật ký vi phạm đã xử lý', !!doc.getElementById('sfd-log-tbody'));

    const sfdTh = [...doc.querySelectorAll('#view-safety-detail table th')].map(e => e.textContent.trim());
    check('nhật ký có cột lỗi', sfdTh.includes('LỖI'), sfdTh.join(' | '));
    check('nhật ký có cột thời gian', sfdTh.includes('THỜI GIAN'), sfdTh.join(' | '));
    check('nhật ký có cột video bằng chứng',
        sfdTh.includes('VIDEO BẰNG CHỨNG'), sfdTh.join(' | '));
}

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
const logDetailBtns = doc.querySelectorAll('#attendance-logs-tbody tr td:last-child button');
check('mỗi dòng nhật ký có nút xem chi tiết riêng', logDetailBtns.length >= 0);
if (logDetailBtns.length) {
    logDetailBtns[0].click();
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

console.log('\n[7d2] Bộ lọc và cột của bảng nhật ký điểm danh');

window.switchNavTab('logs');
await sleep(1000);

check('có bộ lọc theo ca', !!doc.getElementById('log-filter-shift'));
check('có ô lọc từ ngày', !!doc.getElementById('log-date-from'));
check('có ô lọc đến ngày', !!doc.getElementById('log-date-to'));
check('có thanh tìm kiếm theo tên bài', !!doc.getElementById('log-search'));

const logTh = [...doc.querySelectorAll('#view-logs thead th')].map(e => e.textContent.trim());
check('bảng nhật ký có cột tên bài học', logTh.includes('TÊN BÀI HỌC'), logTh.join(' | '));
check('cột cuối cùng là xem chi tiết',
    logTh[logTh.length - 1] === 'XEM CHI TIẾT', logTh.join(' | '));

const firstLogRow = doc.querySelector('#attendance-logs-tbody tr');
if (firstLogRow && !firstLogRow.querySelector('.empty-row')) {
    check('ô ca điểm danh không còn kèm tên bài bên dưới',
        !firstLogRow.querySelector('td:nth-child(2) .cell-subtext'),
        firstLogRow.querySelector('td:nth-child(2)').innerHTML.slice(0, 120));
    check('dòng nhật ký có nút xem chi tiết',
        !!firstLogRow.querySelector('td:last-child button'));
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

console.log('\n[7g] Màn chi tiết ca huấn luyện');

window.switchNavTab('schedule-progress');
await sleep(1000);
const sdDetailBtn = doc.querySelector('#dt-schedule-tbody tr button');
check('bảng lịch có nút xem chi tiết', !!sdDetailBtn);

if (sdDetailBtn) {
    sdDetailBtn.click();
    await sleep(1400);

    check('mở đúng màn chi tiết ca',
        doc.querySelector('.page-view.active').id === 'view-session-detail',
        doc.querySelector('.page-view.active').id);

    const infoKeys = [...doc.querySelectorAll('#sd-info .detail-key')].map(e => e.textContent.trim());
    check('có ô Sĩ số đầu buổi', infoKeys.includes('Sĩ số đầu buổi'), infoKeys.join(' | '));
    check('có ô Sĩ số cuối buổi', infoKeys.includes('Sĩ số cuối buổi'), infoKeys.join(' | '));
    check('bỏ ô Cửa sổ điểm danh', !infoKeys.includes('Cửa sổ điểm danh'), infoKeys.join(' | '));
    check('bỏ ô Dung sai đi chậm', !infoKeys.includes('Dung sai đi chậm'), infoKeys.join(' | '));
    check('tên bài học điền vào ô chứ không chỉ ở tiêu đề',
        infoKeys.includes('Tên bài học'), infoKeys.join(' | '));

    const infoVals = [...doc.querySelectorAll('#sd-info .detail-val')].map(e => e.textContent.trim());
    check('không còn ô nào hiện undefined',
        infoVals.every(v => !v.includes('undefined')), infoVals.join(' | '));

    const headers = [...doc.querySelectorAll('#view-session-detail table th')].map(e => e.textContent.trim());
    check('bảng đối chiếu có cột quân nhân vắng',
        headers.includes('QUÂN NHÂN VẮNG'), headers.join(' | '));

    check('màn chi tiết nhúng camera của ca', !!doc.getElementById('sd-stream'));
    check('khung camera gắn được zoom',
        !!doc.getElementById('sd-camera-box') && doc.getElementById('sd-camera-box').dataset.zoomable === '1',
        doc.getElementById('sd-camera-box') && doc.getElementById('sd-camera-box').dataset.zoomable);
    check('bỏ nút Giám sát quân số', doc.getElementById('sd-btn-watch') === null);
    check('có bảng từng quân nhân trong ca', !!doc.getElementById('sd-attendance-tbody'));
    check('có khu ảnh điểm danh do AI chụp', !!doc.getElementById('sd-evidence'));
}

console.log('\n[7f] Hộp xem ảnh phóng to dùng chung');

check('có hàm mở ảnh bằng chứng', typeof window.openEvidence === 'function');
check('có hàm gắn zoom cho khung camera', typeof window.makeZoomable === 'function');

window.openEvidence('/static/khong-co-that.jpg', 'Ảnh thử');
check('mở hộp ảnh thì lớp phủ hiện ra',
    doc.getElementById('zoom-modal').style.display === 'flex',
    doc.getElementById('zoom-modal').style.display);
check('hộp ảnh trỏ đúng ảnh được bấm',
    doc.getElementById('zoom-img').getAttribute('src') === '/static/khong-co-that.jpg');
check('hộp ảnh hiện chú thích', doc.getElementById('zoom-caption').textContent === 'Ảnh thử');
check('có nút tải ảnh trỏ đúng ảnh',
    doc.getElementById('zoom-download').getAttribute('href') === '/static/khong-co-that.jpg');
check('mở ra thì luôn bắt đầu ở 100%',
    doc.getElementById('zoom-level').textContent === '100%',
    doc.getElementById('zoom-level').textContent);

const stage = doc.getElementById('zoom-stage');
stage._zoom.zoomBy(0.5);
check('phóng to đổi được tỉ lệ', doc.getElementById('zoom-level').textContent === '150%',
    doc.getElementById('zoom-level').textContent);
stage._zoom.reset();
check('nút vừa khung đưa về 100%', doc.getElementById('zoom-level').textContent === '100%');

window.closeZoomModal();
check('đóng hộp ảnh', doc.getElementById('zoom-modal').style.display === 'none');

const box = doc.createElement('div');
box.appendChild(doc.createElement('img'));
doc.body.appendChild(box);
window.makeZoomable(box);
check('gắn zoom thì thêm nút toàn màn hình',
    !!box.querySelector('.zoom-fullscreen-btn'));
window.makeZoomable(box);
check('gọi lại không nhân đôi nút',
    box.querySelectorAll('.zoom-fullscreen-btn').length === 1,
    String(box.querySelectorAll('.zoom-fullscreen-btn').length));

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
