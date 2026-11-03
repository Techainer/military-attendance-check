"""Đăng nhập dùng chung cho các bài kiểm HTTP.

Từ khi có phiên đăng nhập, mọi route (trừ danh sách công khai) đòi cookie
``horus_session`` hợp lệ. ``TestClient`` giữ cookie qua các lần gọi (dựa trên
httpx.Client), nên chỉ cần đăng nhập một lần đầu bài là đủ cho cả bài.

KHÔNG đặt tên file này khớp ``test_*.py`` — nếu không trình chạy test theo
glob sẽ cố chạy nó như một bài kiểm độc lập.
"""

import os


def login_as(client, username="qtht", password=None):
    """Đăng nhập qua API thật (không đi tắt), trả về hồ sơ người dùng.

    Mật khẩu mặc định khớp ``SEED_USERS`` trong app/auth.py: lấy từ biến môi
    trường ``<USERNAME>_PASSWORD`` nếu có, không thì theo quy ước demo
    ``<username>@2026``.
    """
    if password is None:
        password = os.environ.get(f"{username.upper()}_PASSWORD", f"{username}@2026")
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"Đăng nhập thử nghiệm thất bại ({username}): {r.status_code} {r.text}")
    return r.json()
