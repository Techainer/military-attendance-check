"""Kiểm đăng nhập, đổi mật khẩu và sửa thông tin cá nhân.

Bản POC không có phiên và không kiểm quyền ở endpoint khác; test ở đây chỉ
khẳng định mật khẩu đổi được và lưu lại được, không khẳng định gì về bảo mật.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import app.auth as auth
import app.api as api

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


client = TestClient(api.app)


def reset():
    if auth.USERS_FILE.exists():
        auth.USERS_FILE.unlink()
    auth.reload_users()


print("\n[1] Đăng nhập bằng tài khoản mặc định")
reset()
r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "cbqh@2026"})
check("đăng nhập đúng -> 200", r.status_code == 200, r.text[:200])
check("trả về vai trò cán bộ quản lý", r.json().get("role") == "cbqh", r.text[:200])
check("lần đầu chạy tự tạo kho tài khoản", auth.USERS_FILE.exists())

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "sai"})
check("sai mật khẩu -> 401", r.status_code == 401, str(r.status_code))

r = client.post("/api/v1/auth/login", json={"username": "khong-co", "password": "gi-do"})
check("tài khoản lạ -> 401", r.status_code == 401, str(r.status_code))


print("\n[2] Đổi mật khẩu")
r = client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "sai-rồi", "new_password": "matkhau-moi-1"})
check("sai mật khẩu cũ thì không cho đổi", r.status_code == 400, str(r.status_code))

r = client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "cbqh@2026", "new_password": "ngan"})
check("mật khẩu mới quá ngắn bị từ chối", r.status_code == 422, str(r.status_code))

r = client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "cbqh@2026", "new_password": "matkhau-moi-1"})
check("đổi mật khẩu đúng -> 200", r.status_code == 200, r.text[:200])

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "cbqh@2026"})
check("mật khẩu cũ hết hiệu lực", r.status_code == 401, str(r.status_code))

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "matkhau-moi-1"})
check("mật khẩu mới đăng nhập được", r.status_code == 200, r.text[:200])

auth.reload_users()
r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "matkhau-moi-1"})
check("mật khẩu mới sống sót qua lần nạp lại", r.status_code == 200, r.text[:200])

check("mật khẩu không được lưu dạng chữ thường",
      "matkhau-moi-1" not in auth.USERS_FILE.read_text(encoding="utf-8"))


print("\n[3] Sửa thông tin cá nhân")
r = client.patch("/api/v1/auth/profile", json={
    "username": "cbqh", "display_name": "Thiếu tá Nguyễn Văn Hùng"})
check("đổi tên hiển thị -> 200", r.status_code == 200, r.text[:200])
check("trả về tên mới",
      r.json().get("display_name") == "Thiếu tá Nguyễn Văn Hùng", r.text[:200])

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "matkhau-moi-1"})
check("đăng nhập lần sau thấy tên mới",
      r.json().get("display_name") == "Thiếu tá Nguyễn Văn Hùng", r.text[:200])

r = client.patch("/api/v1/auth/profile", json={
    "username": "khong-co", "display_name": "Ai đó"})
check("sửa hồ sơ tài khoản lạ -> 404", r.status_code == 404, str(r.status_code))

reset()

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
