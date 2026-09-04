"""Kiểm chứng nhóm endpoint cấu hình: camera, thời khoá biểu, hệ thống.

Đây là ĐẦU VÀO của service AI. Nguyên tắc: trường lõi AI đọc thì kiểm chặt,
trường giao diện cần mà AI không dùng thì đi xuyên qua nguyên vẹn.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from fastapi.testclient import TestClient

import app.api as api
from app.events import CAMERA_ID
from app.storage import write_json_list

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


client = TestClient(api.app)


def reset():
    write_json_list(api.cameras_file, [])
    write_json_list(api.schedules_file, [])
    api.zone_store.save([])


# ================================================================ camera
print("\n[1] Camera")

reset()
r = client.get("/api/v1/cameras")
check("lần đầu chạy tự tạo camera mặc định",
      r.status_code == 200 and r.json()["total"] == 1, r.text[:200])
default_cam = r.json()["items"][0]
check("camera mặc định đúng id đang dùng trong sự kiện", default_cam["id"] == CAMERA_ID,
      default_cam.get("id", ""))
check("có sẵn stream_url và snapshot_url dựng sẵn",
      default_cam["stream_url"].endswith("overlay=1")
      and default_cam["snapshot_url"].endswith("overlay=0"), str(default_cam))
check("chưa chạy thì trạng thái offline", default_cam["status"] == "offline",
      default_cam["status"])

r = client.post("/api/v1/cameras", json={
    "name": "Trường bắn hướng Đông", "source_type": "rtsp",
    "source_uri": "rtsp://10.0.0.21:554/stream1", "code": "CAM-07",
    "area_name": "Trường bắn số 1",
    "ghi_chu": "trường lạ do giao diện gửi kèm",
})
check("thêm camera -> 201", r.status_code == 201, r.text[:250])
cam = r.json() if r.status_code == 201 else {}
check("trường lạ đi xuyên qua nguyên vẹn",
      cam.get("ghi_chu") == "trường lạ do giao diện gửi kèm", str(cam))

for label, body, code in [
    ("thiếu tên", {"name": "", "source_type": "rtsp", "source_uri": "rtsp://x/y"}, 422),
    ("source_type lạ", {"name": "x", "source_type": "magic", "source_uri": "a"}, 422),
    ("rtsp không phải URL", {"name": "x", "source_type": "rtsp", "source_uri": "10.0.0.21"}, 422),
    ("fps ngoài khoảng", {"name": "x", "source_type": "file", "source_uri": "a",
                          "target_fps": 999}, 422),
    ("thiếu nguồn", {"name": "x", "source_type": "file"}, 422),
]:
    r = client.post("/api/v1/cameras", json=body)
    check(f"từ chối camera: {label} -> {code}", r.status_code == code, f"nhận {r.status_code}")

r = client.patch(f"/api/v1/cameras/{cam['id']}", json={"name": "Trường bắn hướng Tây"})
check("sửa tên camera -> 200", r.status_code == 200, r.text[:200])
check("tên đã đổi, nguồn giữ nguyên",
      r.json()["name"] == "Trường bắn hướng Tây"
      and r.json()["source_uri"] == "rtsp://10.0.0.21:554/stream1", str(r.json()))
check("trường lạ vẫn còn sau khi sửa", r.json().get("ghi_chu") is not None, str(r.json()))

r = client.patch(f"/api/v1/cameras/{cam['id']}", json={"source_type": "rtsp",
                                                       "source_uri": "khong-phai-url"})
check("sửa thành nguồn rtsp sai -> 422", r.status_code == 422, str(r.status_code))

r = client.patch("/api/v1/cameras/khong_ton_tai", json={"name": "x"})
check("sửa camera không tồn tại -> 404", r.status_code == 404, str(r.status_code))

r = client.get(f"/api/v1/cameras?area_name=Trường bắn số 1")
check("lọc theo tên khu vực", r.json()["total"] == 1, str(r.json()["total"]))

# Vùng của camera phải bị dọn theo khi xoá camera
client.post(f"/api/v1/cameras/{cam['id']}/zones", json={
    "name": "Khối chắn", "kind": "polygon", "rule": "restricted_area",
    "points": [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.1}, {"x": 0.4, "y": 0.4}]})
check("camera mới có vùng riêng", len(client.get(f"/api/v1/cameras/{cam['id']}/zones").json()) == 1)

r = client.delete(f"/api/v1/cameras/{cam['id']}")
check("xoá camera -> 204", r.status_code == 204, str(r.status_code))
check("vùng của camera bị dọn theo, không để lại rác",
      len(client.get(f"/api/v1/cameras/{cam['id']}/zones").json()) == 0)
check("xoá camera không tồn tại -> 404",
      client.delete(f"/api/v1/cameras/{cam['id']}").status_code == 404)

r = client.post(f"/api/v1/cameras/{CAMERA_ID}/start")
check("bật camera chưa khai nguồn -> 422", r.status_code == 422, r.text[:200])

# ================================================================ thời khoá biểu
print("\n[2] Thời khoá biểu")

r = client.post("/api/v1/schedules", json={
    "name": "Huấn luyện bắn súng tiểu liên AK",
    "start_time": "07:00", "end_time": "11:30",
    "unit": "Đại đội 1", "shift": "Ca sáng",
    "training_type": "dao_tao",
    "check_window_mins": 5, "late_tolerance_mins": 10,
    "required_count": 45,
    "lesson_name": "Bài 3 — Ngắm bắn mục tiêu cố định",
    "instructor": "Thượng uý Trần Văn B",
    "field": "Trường bắn số 1",
    "class_name": "Đội học A1",
})
check("tạo ca -> 201", r.status_code == 201, r.text[:250])
sch = r.json() if r.status_code == 201 else {}
check("giữ nguyên trường giao diện cần mà AI không dùng",
      sch.get("lesson_name") and sch.get("instructor") and sch.get("field")
      and sch.get("class_name"), str(sch))
check("giữ đúng dung sai đi chậm đã khai", sch.get("late_tolerance_mins") == 10, str(sch))

for label, body, code in [
    ("giờ sai định dạng", {"name": "x", "start_time": "7h", "end_time": "11:30"}, 422),
    ("giờ ngoài phạm vi", {"name": "x", "start_time": "25:00", "end_time": "11:30"}, 422),
    ("bắt đầu trùng kết thúc", {"name": "x", "start_time": "07:00", "end_time": "07:00"}, 422),
    ("ca quá ngắn cho hai cửa sổ", {"name": "x", "start_time": "07:00", "end_time": "07:05",
                                    "check_window_mins": 5}, 422),
    ("training_type lạ", {"name": "x", "start_time": "07:00", "end_time": "11:30",
                          "training_type": "gi_do"}, 422),
    ("cửa sổ điểm danh 0 phút", {"name": "x", "start_time": "07:00", "end_time": "11:30",
                                 "check_window_mins": 0}, 422),
]:
    r = client.post("/api/v1/schedules", json=body)
    check(f"từ chối ca: {label} -> {code}", r.status_code == code, f"nhận {r.status_code}")

r = client.post("/api/v1/schedules", json={"name": "Gác đêm", "start_time": "22:00",
                                           "end_time": "06:00", "training_type": "chien_dau"})
check("ca qua đêm được chấp nhận", r.status_code == 201, r.text[:200])
night = r.json() if r.status_code == 201 else {}

r = client.get("/api/v1/schedules")
check("liệt kê ca kèm trạng thái vận hành",
      r.status_code == 200 and all("state" in s for s in r.json()["items"]), r.text[:200])

r = client.get("/api/v1/schedules?training_type=chien_dau")
check("lọc theo phân hệ chiến đấu",
      r.json()["total"] == 1 and r.json()["items"][0]["name"] == "Gác đêm", r.text[:200])

r = client.patch(f"/api/v1/schedules/{sch['id']}", json={"required_count": 50})
check("sửa sĩ số chuẩn -> 200", r.status_code == 200 and r.json()["required_count"] == 50,
      r.text[:200])
check("sửa xong vẫn giữ trường của giao diện", r.json().get("instructor") is not None,
      str(r.json()))

r = client.patch(f"/api/v1/schedules/{sch['id']}", json={"end_time": "07:03"})
check("sửa thành ca quá ngắn -> 422", r.status_code == 422, str(r.status_code))

r = client.patch("/api/v1/schedules/khong_co", json={"name": "x"})
check("sửa ca không tồn tại -> 404", r.status_code == 404, str(r.status_code))

r = client.get(f"/api/v1/schedules/{night['id']}")
check("lấy chi tiết ca -> 200", r.status_code == 200 and "state_label" in r.json(), r.text[:200])

check("xoá ca -> 204", client.delete(f"/api/v1/schedules/{night['id']}").status_code == 204)
check("xoá ca không tồn tại -> 404",
      client.delete(f"/api/v1/schedules/{night['id']}").status_code == 404)

# ================================================================ tổng hợp
print("\n[3] Tổng hợp lọc theo phân hệ")

r = client.get("/api/v1/summary/training")
check("tổng hợp không lọc -> 200", r.status_code == 200, r.text[:200])
check("buổi có mang training_type cho giao diện",
      all("training_type" in s for s in r.json()["sessions"]), r.text[:200])

r = client.get("/api/v1/summary/training?training_type=dao_tao")
check("lọc phân hệ đào tạo",
      all(s["training_type"] == "dao_tao" for s in r.json()["sessions"]), r.text[:300])

r = client.get("/api/v1/summary/training?training_type=chien_dau")
check("phân hệ chiến đấu không còn ca nào", r.json()["stats"]["running_sessions"] == 0,
      r.text[:200])

r = client.get("/api/v1/summary/safety")
check("dashboard an toàn lấy camera từ danh sách thật",
      r.status_code == 200 and len(r.json()["cameras"]) >= 1, r.text[:200])

# ================================================================ hệ thống
print("\n[4] Hệ thống")

r = client.get("/api/v1/system/time")
check("giờ máy chủ -> 200", r.status_code == 200, r.text[:200])
check("giờ kèm offset múi giờ", r.json()["server_time"].endswith("+07:00"),
      r.json().get("server_time", ""))
check("khai báo múi giờ", r.json()["timezone"] == "Asia/Ho_Chi_Minh", str(r.json()))

r = client.get("/api/v1/system/health")
check("tình trạng hệ thống -> 200", r.status_code == 200, r.text[:200])
check("báo đủ chỉ số vận hành",
      {"status", "cameras_running", "cameras_total", "models_loaded",
       "registered_personnel", "pending_events"} <= set(r.json()), str(set(r.json())))

# ================================================================ hợp đồng
print("\n[4b] Đăng nhập hai tài khoản")

for user, pw, role, label in [("cbqh", "cbqh@2026", "cbqh", "Cán bộ quản lý"),
                              ("qtht", "qtht@2026", "qtht", "Quản trị hệ thống")]:
    r = client.post("/api/v1/auth/login", json={"username": user, "password": pw})
    check(f"đăng nhập {user} -> 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        body = r.json()
        check(f"{user} trả đúng vai trò", body["role"] == role, str(body))
        check(f"{user} có tên và nhãn vai trò hiển thị được",
              body["display_name"] and body["role_label"] == label, str(body))
        check(f"{user} KHÔNG trả mật khẩu về cho giao diện",
              "password" not in body, str(body))

for label, body, code in [
    ("sai mật khẩu", {"username": "cbqh", "password": "sai"}, 401),
    ("tài khoản lạ", {"username": "khongco", "password": "cbqh@2026"}, 401),
    ("mật khẩu rỗng", {"username": "cbqh", "password": ""}, 422),
    ("thiếu tài khoản", {"password": "cbqh@2026"}, 422),
]:
    r = client.post("/api/v1/auth/login", json=body)
    check(f"từ chối: {label} -> {code}", r.status_code == code, f"nhận {r.status_code}")

r = client.post("/api/v1/auth/login", json={"username": "  CBQH  ", "password": "cbqh@2026"})
check("tên tài khoản không phân biệt hoa thường và khoảng trắng thừa",
      r.status_code == 200, str(r.status_code))

# Vai trò chỉ để giao diện hiện menu; API không kiểm quyền, phải nói rõ chứ
# không để người dùng tưởng đã có bảo mật.
r = client.get("/api/v1/cameras")
check("API vẫn gọi được khi chưa đăng nhập (POC không có phiên)",
      r.status_code == 200, str(r.status_code))

print("\n[4c] Giao diện không bị trình duyệt giữ bản cũ")

r = client.get("/")
check("trang chính trả HTML", r.headers["content-type"].startswith("text/html"),
      r.headers.get("content-type", ""))
check("trang chính cấm đệm, buộc hỏi lại máy chủ",
      "no-cache" in r.headers.get("cache-control", ""),
      r.headers.get("cache-control", "(không có)"))

r_js = client.get("/static/app.js")
check("app.js cũng cấm đệm",
      "no-cache" in r_js.headers.get("cache-control", ""),
      r_js.headers.get("cache-control", "(không có)"))

import re as _re
version = _re.search(r"app\.js\?v=([a-z0-9]+)", r.text)
check("CSS và JS có dấu phiên bản trong đường dẫn", version is not None,
      r.text[:200] if version is None else "")

# Sửa file thì dấu phiên bản phải đổi, nếu không trình duyệt vẫn dùng bản cũ
(api.static_path / "app.js").touch()
version2 = _re.search(r"app\.js\?v=([a-z0-9]+)", client.get("/").text)
check("sửa app.js thì dấu phiên bản đổi theo",
      version and version2 and version.group(1) != version2.group(1),
      f"{version and version.group(1)} -> {version2 and version2.group(1)}")

check("trang chính có màn đăng nhập", "login-screen" in r.text)
check("đã bỏ hẳn tab chọn vai trò", "role-switcher" not in r.text and "role-btn" not in r.text)
check("chưa đăng nhập thì khung ứng dụng ẩn sẵn từ máy chủ",
      'id="app-layout" style="display: none;"' in r.text)

print("\n[4d] Đa camera chạy song song")

reset()
import app.video_processor as vp
from app.attendance import AttendanceManager
from app.safety import IntrusionDetector, ZoneStore

# Khung hình tách theo camera, không dùng chung một biến
vp.publish_frame("cam_A", b"anh-cua-A", b"goc-cua-A")
vp.publish_frame("cam_B", b"anh-cua-B", b"goc-cua-B")
check("mỗi camera giữ khung hình riêng",
      vp.get_frame("cam_A", True) == b"anh-cua-A"
      and vp.get_frame("cam_B", True) == b"anh-cua-B")
check("số hiệu khung hình đếm riêng từng camera",
      vp.get_revision("cam_A") == 1 and vp.get_revision("cam_B") == 1)
vp.publish_frame("cam_A", b"anh-A-2", b"goc-A-2")
check("camera này có hình mới không làm đổi camera kia",
      vp.get_revision("cam_A") == 2 and vp.get_revision("cam_B") == 1
      and vp.get_frame("cam_B", True) == b"anh-cua-B")
vp.clear_frames("cam_A")
check("dừng camera này không xoá hình camera kia",
      vp.get_frame("cam_A", True) is None and vp.get_frame("cam_B", True) == b"anh-cua-B")

# Bộ đệm clip cũng tách theo camera
vp.push_clip_frame("cam_A", "khung-A")
vp.push_clip_frame("cam_B", "khung-B")
vp.keep_clip("cam_A", "clip_A")
check("đoạn clip chỉ chứa khung của đúng camera",
      vp.latest_event_clips["clip_A"] == ["khung-A"],
      str(vp.latest_event_clips.get("clip_A")))

# Vùng giám sát không lẫn giữa các camera
api.zone_store.save([
    {"id": "zA", "camera_id": "cam_A", "name": "Vùng đếm A", "kind": "polygon",
     "rule": "attendance_area", "enabled": True,
     "points": [{"x": 0, "y": 0}, {"x": 0.4, "y": 0}, {"x": 0.4, "y": 1}]},
    {"id": "zB", "camera_id": "cam_B", "name": "Vùng cấm B", "kind": "polygon",
     "rule": "restricted_area", "enabled": True,
     "points": [{"x": 0.6, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0.6, "y": 1}]},
])
zones_a = [z["name"] for z in ZoneStore(str(api.data_path), camera_id="cam_A").zones()]
zones_b = [z["name"] for z in ZoneStore(str(api.data_path), camera_id="cam_B").zones()]
check("camera A chỉ thấy vùng của A", zones_a == ["Vùng đếm A"], str(zones_a))
check("camera B chỉ thấy vùng của B", zones_b == ["Vùng cấm B"], str(zones_b))
check("màn quản trị vẫn xem được vùng của mọi camera",
      len(ZoneStore(str(api.data_path)).zones()) == 2)

# Người đứng trong vùng cấm của B không được làm A báo động
inside_b = [700, 100, 800, 400]
det_a = IntrusionDetector(ZoneStore(str(api.data_path), camera_id="cam_A"))
from datetime import datetime as _dt
check("camera A KHÔNG báo vi phạm theo vùng cấm của camera B",
      det_a.check([inside_b], [1], 1000, 1000, _dt(2026, 9, 3, 10, 0)) == [])
det_b = IntrusionDetector(ZoneStore(str(api.data_path), camera_id="cam_B"))
check("camera B vẫn báo đúng vùng cấm của mình",
      len(det_b.check([inside_b], [1], 1000, 1000, _dt(2026, 9, 3, 10, 0))) == 1)

# Thời khoá biểu chia theo camera, hai camera không cùng điểm danh một lớp
write_json_list(api.schedules_file, [
    {"id": "s_a", "name": "Ca của A", "start_time": "07:00", "end_time": "11:30",
     "camera_id": "cam_A"},
    {"id": "s_b", "name": "Ca của B", "start_time": "07:00", "end_time": "11:30",
     "camera_id": "cam_B"},
    {"id": "s_old", "name": "Ca chưa gán camera", "start_time": "07:00", "end_time": "11:30"},
])
mgr_a = AttendanceManager(str(api.data_path), api.face_engine, api.events, camera_id="cam_A")
mgr_default = AttendanceManager(str(api.data_path), api.face_engine, api.events,
                                camera_id=CAMERA_ID)
ids_a = {s["id"] for s in mgr_a._load_schedules()}
ids_default = {s["id"] for s in mgr_default._load_schedules()}
check("camera A chỉ nhận ca của mình", ids_a == {"s_a"}, str(ids_a))
check("ca chưa gán camera thuộc về camera mặc định",
      "s_old" in ids_default and "s_a" not in ids_default, str(ids_default))
check("bản đọc chung vẫn thấy hết ca cho màn tổng hợp",
      len(api.schedules_view._load_schedules()) == 3)

# Trạng thái camera bám theo camera nào đang chạy, không phải một cờ chung
check("chưa chạy thì không camera nào online", not api.any_camera_running())
r = client.get("/api/v1/cameras")
check("mọi camera đều offline khi chưa chạy",
      all(c["status"] != "online" for c in r.json()["items"]))

reset()

print("\n[5] Hợp đồng khớp code")

spec = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "docs/api/openapi.yaml"))
declared = {}
for path, ops in spec["paths"].items():
    declared[path] = {m.upper() for m in ops if m in ("get", "post", "patch", "delete", "put")}

impl = {}
for route in api.app.routes:
    path = getattr(route, "path", "")
    if path.startswith("/api/v1"):
        impl.setdefault(path.replace("/api/v1", "", 1), set()).update(
            {m for m in getattr(route, "methods", set()) if m not in ("HEAD", "OPTIONS")})

chua_lam = {p: sorted(m - impl.get(p, set())) for p, m in declared.items()
            if m - impl.get(p, set())}
check("mọi đường dẫn trong hợp đồng đều đã hiện thực", not chua_lam, str(chua_lam))

thua = {p: sorted(m) for p, m in impl.items() if p not in declared}
check("không có endpoint v1 nào nằm ngoài hợp đồng", not thua, str(thua))

# Khớp đường dẫn thôi chưa đủ: hợp đồng hứa bộ lọc mà code lặng lẽ bỏ qua thì
# giao diện dựng ô chọn ngày xong thấy nó không có tác dụng.
import inspect

params_def = spec["components"].get("parameters", {})


def _resolve(p):
    return params_def[p["$ref"].rsplit("/", 1)[1]] if "$ref" in p else p


declared_q = {}
for path, ops in spec["paths"].items():
    shared = [_resolve(p) for p in ops.get("parameters", [])]
    for method, op in ops.items():
        if method not in ("get", "post", "patch", "delete", "put"):
            continue
        allp = shared + [_resolve(p) for p in op.get("parameters", [])]
        declared_q[(path, method.upper())] = {p["name"] for p in allp if p.get("in") == "query"}

impl_q = {}
for route in api.app.routes:
    path = getattr(route, "path", "")
    if not path.startswith("/api/v1") or getattr(route, "endpoint", None) is None:
        continue
    names = set(inspect.signature(route.endpoint).parameters)
    names -= {"background_tasks", "payload", "body", "request"}
    names -= {seg.strip("{}") for seg in path.split("/") if seg.startswith("{")}
    for method in getattr(route, "methods", set()):
        if method not in ("HEAD", "OPTIONS"):
            impl_q[(path.replace("/api/v1", "", 1), method)] = names

hua_suong = {k: sorted(v - impl_q.get(k, set())) for k, v in declared_q.items()
             if v - impl_q.get(k, set())}
check("không có bộ lọc nào hợp đồng hứa mà code bỏ qua", not hua_suong, str(hua_suong))

am_tham = {k: sorted(impl_q[k] - v) for k, v in declared_q.items()
           if impl_q.get(k, set()) - v}
check("không có bộ lọc nào code nhận mà hợp đồng giấu", not am_tham, str(am_tham))

print("\n[6] Các bộ lọc hợp đồng hứa phải chạy thật")

api.events.emit("INTRUSION", "Sự kiện để kiểm bộ lọc.", severity="critical",
                zone_id="z1", detail={"zone_name": "z", "object_count": 1})

r = client.get("/api/v1/events?occurred_from=2099-01-01T00:00:00")
check("lọc từ mốc tương lai -> rỗng", r.json()["total"] == 0, str(r.json()["total"]))

r = client.get("/api/v1/events?occurred_to=2000-01-01T00:00:00")
check("lọc tới mốc quá khứ -> rỗng", r.json()["total"] == 0, str(r.json()["total"]))

r = client.get("/api/v1/events?occurred_from=2000-01-01T00:00:00")
check("lọc từ mốc quá khứ -> có kết quả", r.json()["total"] >= 1, str(r.json()["total"]))

r = client.get("/api/v1/summary/safety?date=2000-01-01")
check("dashboard an toàn lọc theo ngày cũ -> không có vi phạm",
      r.json()["pending_count"] == 0 and r.json()["date"] == "2000-01-01", r.text[:200])

r = client.get("/api/v1/summary/training?date=2000-01-01")
check("tổng hợp lọc theo ngày cũ", r.json()["date"] == "2000-01-01", r.text[:200])

first, _ = api.events.list_events(limit=1)
marker = first[0]["id"]
after = api.events.emit("SYSTEM", "Sự kiện sau mốc.", detail={"code": "camera_online"})
missed = api.events.since(marker)
check("phát bù đúng sự kiện phát sinh sau mốc",
      [e["id"] for e in missed] == [after["id"]], str([e["id"] for e in missed]))
check("mốc lạ thì không dội lại toàn bộ lịch sử", api.events.since("evt_khong_co") == [])

r = client.get("/api/v1/schedules?enabled=false")
check("lọc ca đang tắt", r.status_code == 200 and r.json()["total"] == 0, r.text[:200])

for goner in ["/areas", "/classes", "/sessions", "/sessions/{session_id}/approve"]:
    check(f"đã bỏ {goner} khỏi hợp đồng (hệ thống quản lý sở hữu)", goner not in declared)

reset()

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
