"""Kiểm phiên đăng nhập và phân quyền: route công khai, chặn người chưa đăng
nhập, chặn cbqh gọi API chỉ dành cho qtht, đổi mật khẩu vô hiệu phiên cũ, token
hết hạn/giả mạo, chặn dò mật khẩu.

Bài test_auth_api.py kiểm nghiệp vụ đăng nhập/đổi mật khẩu/sửa hồ sơ; bài này
kiểm RANH GIỚI: ai gọi được gì.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.api as api
import app.auth as auth
from app.storage import read_json_list, write_json_list
from auth_helper import login_as

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- dọn trước/sau

DATA_FILES = ["cameras.json", "schedules.json", "zone_rules.json", "attendance_logs.json"]
_backup = {name: (api.data_path / name).read_text(encoding="utf-8")
           if (api.data_path / name).exists() else None for name in DATA_FILES}


def restore_data():
    for name, content in _backup.items():
        path = api.data_path / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content, encoding="utf-8")


def reset_users():
    if auth.USERS_FILE.exists():
        auth.USERS_FILE.unlink()
    auth.reload_users()


reset_users()
auth._failed_attempts.clear()


# ================================================================ [1] Route công khai
print("\n[1] Route công khai vẫn vào được khi chưa đăng nhập")

anon = TestClient(api.app)

r = anon.get("/")
check("GET / -> 200", r.status_code == 200, str(r.status_code))

r = anon.get("/static/app.js")
check("GET /static/app.js -> 200", r.status_code == 200, str(r.status_code))

r = anon.get("/api/v1/system/health")
check("GET /api/v1/system/health -> 200", r.status_code == 200, str(r.status_code))

r = anon.get("/api/time")
check("GET /api/time -> 200", r.status_code == 200, str(r.status_code))

r = anon.get("/api/v1/system/time")
check("GET /api/v1/system/time -> 200", r.status_code == 200, str(r.status_code))

r = anon.get("/api/v1/system/config")
check("GET /api/v1/system/config -> 200 (màn đăng nhập cần hỏi trước khi có phiên)",
      r.status_code == 200, str(r.status_code))
check("system/config có cờ show_demo_accounts", "show_demo_accounts" in r.json(), str(r.json()))

r = anon.post("/api/v1/auth/login", json={"username": "cbqh", "password": "sai-cho-muc-1"})
check("POST /api/v1/auth/login (sai mật khẩu) vẫn chạy tới nơi -> 401, không phải 401 của middleware",
      r.status_code == 401, str(r.status_code))


# ================================================================ [2] Chặn người chưa đăng nhập
print("\n[2] Chưa đăng nhập thì bị chặn ở mọi nhóm route")

anon2 = TestClient(api.app)  # client mới, không dính cookie login sai ở trên

PROTECTED_GET = [
    "/api/v1/cameras",
    "/api/status",
    "/api/faces",
    "/api/v1/events",
    "/api/v1/events/stream",
    "/api/v1/cameras/cam_01/stream.mjpg",
    "/api/v1/cameras/cam_01/detections",
    "/data/events/khong-ton-tai.jpg",
    "/data/face_avatars/khong-ton-tai.jpg",
    "/data/attendance_evidence/khong-ton-tai.jpg",
    "/go2rtc/api/streams",
]
for path in PROTECTED_GET:
    r = anon2.get(path)
    check(f"GET {path} -> 401", r.status_code == 401, str(r.status_code))

r = anon2.post("/api/v1/cameras", json={"name": "x", "source_type": "file", "source_uri": "/tmp/x.mp4"})
check("POST /api/v1/cameras (chưa đăng nhập) -> 401", r.status_code == 401, str(r.status_code))

try:
    with anon2.websocket_connect("/ws") as ws:
        check("WS /ws chưa đăng nhập bị đóng ngay", False, "kết nối lại được")
except WebSocketDisconnect as e:
    check("WS /ws chưa đăng nhập bị đóng ngay (code 1008)", e.code == 1008, str(e.code))

try:
    with anon2.websocket_connect("/go2rtc/api/ws?src=cam_01") as ws:
        check("WS /go2rtc/api/ws chưa đăng nhập bị đóng ngay", False, "kết nối lại được")
except WebSocketDisconnect as e:
    check("WS /go2rtc/api/ws chưa đăng nhập bị đóng ngay (code 1008)", e.code == 1008, str(e.code))


# ================================================================ [3] cbqh bị chặn ở route QTHT-only
print("\n[3] cbqh đăng nhập được, nhưng không gọi được API chỉ dành cho QTHT")

cbqh_client = TestClient(api.app)
profile = login_as(cbqh_client, "cbqh")
check("cbqh đăng nhập -> vai trò cbqh", profile["role"] == "cbqh", str(profile))

r = cbqh_client.get("/api/v1/auth/me")
check("cbqh gọi /auth/me -> 200, đúng vai trò",
      r.status_code == 200 and r.json()["role"] == "cbqh", str(r.status_code))

QTHT_ONLY_CALLS = [
    ("POST", "/api/v1/cameras",
     {"name": "x", "source_type": "file", "source_uri": "/tmp/x.mp4"}),
    ("POST", "/api/v1/schedules",
     {"name": "x", "start_time": "07:00", "end_time": "11:30"}),
    ("POST", "/api/schedules", {"name": "x", "start_time": "07:00", "end_time": "11:30"}),
    ("POST", "/api/zones", {"polygon_points": []}),
    ("POST", "/api/attendance/start", None),
    ("POST", "/api/attendance/cancel", None),
    ("POST", "/api/start", None),
    ("POST", "/api/stop", None),
    ("POST", "/api/set-baseline?count=10", None),
    ("POST", "/api/v1/cameras/cam_01/start", None),
    ("POST", "/api/v1/cameras/cam_01/stop", None),
    ("PATCH", "/api/v1/cameras/cam_01", {"name": "y"}),
    ("DELETE", "/api/v1/cameras/cam_01", None),
    ("DELETE", "/api/faces/per_khong_co", None),
]
for method, path, body in QTHT_ONLY_CALLS:
    r = cbqh_client.request(method, path, json=body)
    check(f"{method} {path} (cbqh) -> 403", r.status_code == 403, str(r.status_code))

# Ack sự kiện: CẢ HAI vai trò được phép, không phải QTHT-only
evt = api.events.emit("SYSTEM", "Kiểm tra quyền ack.", detail={"code": "test_auth_enforcement"})
r = cbqh_client.post(f"/api/v1/events/{evt['id']}/ack", json={"acked_by": "cbqh test"})
check("cbqh ack sự kiện -> 200 (ack không phải QTHT-only)", r.status_code == 200, str(r.status_code))


# ================================================================ [4] qtht gọi được
print("\n[4] qtht gọi được các API chỉ dành cho QTHT")

qtht_client = TestClient(api.app)
profile = login_as(qtht_client, "qtht")
check("qtht đăng nhập -> vai trò qtht", profile["role"] == "qtht", str(profile))

r = qtht_client.post("/api/v1/cameras", json={
    "name": "Camera kiểm quyền", "source_type": "file", "source_uri": "/tmp/khong_co_thuc.mp4"})
check("qtht tạo camera -> 201", r.status_code == 201, r.text[:200])
new_cam_id = r.json().get("id") if r.status_code == 201 else None

if new_cam_id:
    r = qtht_client.delete(f"/api/v1/cameras/{new_cam_id}")
    check("qtht xoá camera vừa tạo -> 204", r.status_code == 204, str(r.status_code))


# ================================================================ [5] Đăng xuất
print("\n[5] Đăng xuất xoá phiên")

logout_client = TestClient(api.app)
login_as(logout_client, "cbqh")
r = logout_client.get("/api/v1/auth/me")
check("trước logout: /auth/me -> 200", r.status_code == 200, str(r.status_code))

r = logout_client.post("/api/v1/auth/logout")
check("logout -> 200", r.status_code == 200, str(r.status_code))

r = logout_client.get("/api/v1/auth/me")
check("sau logout: /auth/me -> 401", r.status_code == 401, str(r.status_code))

r = logout_client.get("/api/v1/cameras")
check("sau logout: route bảo vệ -> 401", r.status_code == 401, str(r.status_code))


# ================================================================ [6] Đổi mật khẩu vô hiệu phiên cũ
print("\n[6] Đổi mật khẩu làm phiên cũ hết hiệu lực, phiên vừa cấp lại vẫn dùng được")

pw_client = TestClient(api.app)
login_as(pw_client, "cbqh")
old_cookie = pw_client.cookies.get(auth.SESSION_COOKIE)
check("lấy được cookie phiên cũ", bool(old_cookie))

r = pw_client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "cbqh@2026", "new_password": "doi-mat-khau-moi-1"})
check("đổi mật khẩu -> 200", r.status_code == 200, r.text[:200])

stale_client = TestClient(api.app)
stale_client.cookies.set(auth.SESSION_COOKIE, old_cookie)
r = stale_client.get("/api/v1/auth/me")
check("cookie cũ (trước khi đổi mật khẩu) bị từ chối -> 401", r.status_code == 401, str(r.status_code))

r = pw_client.get("/api/v1/auth/me")
check("cookie mới (server tự cấp lại sau khi đổi mật khẩu) vẫn dùng được -> 200",
      r.status_code == 200, str(r.status_code))

# Đổi lại mật khẩu cũ để không ảnh hưởng bài kiểm khác chạy sau trong cùng tiến
# trình (không có vì mỗi bài là tiến trình riêng, nhưng cứ trả về cho sạch).
r = pw_client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "doi-mat-khau-moi-1", "new_password": "cbqh@2026-x"})
check("trả lại mật khẩu (khác bản gốc do ràng buộc độ dài) -> 200", r.status_code == 200, r.text[:200])


# ================================================================ [7] Token hết hạn
print("\n[7] Token hết hạn bị từ chối")

expired = auth.create_session_token("cbqh", "cbqh", auth.token_version_of("cbqh"), hours=-1)
expired_client = TestClient(api.app)
expired_client.cookies.set(auth.SESSION_COOKIE, expired)
r = expired_client.get("/api/v1/auth/me")
check("token hết hạn -> 401", r.status_code == 401, str(r.status_code))


# ================================================================ [8] Token giả mạo
print("\n[8] Token giả mạo (sửa chữ ký hoặc payload) bị từ chối")

valid = auth.create_session_token("qtht", "qtht", auth.token_version_of("qtht"))
payload_b64, _, sig = valid.rpartition(".")

tampered_sig = payload_b64 + "." + ("0" if sig[0] != "0" else "1") + sig[1:]
t1 = TestClient(api.app)
t1.cookies.set(auth.SESSION_COOKIE, tampered_sig)
check("chữ ký bị sửa -> 401", t1.get("/api/v1/auth/me").status_code == 401)

tampered_payload = payload_b64 + "x." + sig
t2 = TestClient(api.app)
t2.cookies.set(auth.SESSION_COOKIE, tampered_payload)
check("payload bị sửa -> 401", t2.get("/api/v1/auth/me").status_code == 401)

garbage = TestClient(api.app)
garbage.cookies.set(auth.SESSION_COOKIE, "khong-phai-token-hop-le")
check("cookie rác không đúng khuôn dạng -> 401", garbage.get("/api/v1/auth/me").status_code == 401)


# ================================================================ [9] Chặn dò mật khẩu
print("\n[9] Chặn dò mật khẩu: quá 10 lần sai trong 10 phút -> 429")

auth._failed_attempts.clear()
rl_client = TestClient(api.app)
target_user = "khong-ton-tai-rate-limit"

last_status = None
for i in range(auth.RATE_LIMIT_MAX_FAILURES):
    r = rl_client.post("/api/v1/auth/login", json={"username": target_user, "password": "sai"})
    last_status = r.status_code
check(f"{auth.RATE_LIMIT_MAX_FAILURES} lần sai đầu vẫn là 401, chưa khoá",
      last_status == 401, str(last_status))

r = rl_client.post("/api/v1/auth/login", json={"username": target_user, "password": "sai"})
check("lần sai tiếp theo (vượt ngưỡng) -> 429", r.status_code == 429, str(r.status_code))

# Đúng mật khẩu cũng bị chặn luôn vì đã khoá theo tài khoản
r = rl_client.post("/api/v1/auth/login", json={"username": "qtht", "password": "qtht@2026"})
check("một IP dò xong thì IP đó cũng bị khoá kể cả đăng nhập đúng tài khoản khác -> 429",
      r.status_code == 429, str(r.status_code))

auth._failed_attempts.clear()


# ================================================================ [10] AUTH_REQUIRED=0 tắt kiểm tra (dev)
print("\n[10] AUTH_REQUIRED=0 tắt hẳn kiểm phiên (chỉ cho máy phát triển cá nhân)")

old_auth_required = auth.AUTH_REQUIRED
auth.AUTH_REQUIRED = False
try:
    dev_client = TestClient(api.app)
    r = dev_client.get("/api/v1/cameras")
    check("AUTH_REQUIRED=0: route bảo vệ gọi được không cần đăng nhập",
          r.status_code == 200, str(r.status_code))
    r = dev_client.post("/api/v1/cameras", json={
        "name": "z", "source_type": "file", "source_uri": "/tmp/z.mp4"})
    check("AUTH_REQUIRED=0: route QTHT-only cũng gọi được không cần đăng nhập",
          r.status_code == 201, str(r.status_code))
finally:
    auth.AUTH_REQUIRED = old_auth_required


restore_data()
reset_users()
auth._failed_attempts.clear()

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
