"""Tài khoản cho bản POC: kho JSON nhỏ, mật khẩu băm PBKDF2.

**Đây không phải ranh giới bảo mật.** Không có phiên, không có token ký, và các
endpoint khác không kiểm quyền — ai gọi thẳng API vẫn làm được mọi thứ. Phần
này chỉ để giao diện biết đang là vai trò nào và để người dùng đổi được mật
khẩu của chính mình, đổi xong thì lưu lại được.

Muốn dùng thật thì phải thay bằng xác thực có phiên và kiểm quyền ở từng
endpoint; chỗ đó cố ý để trống chứ không phải quên.
"""

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Optional

ROLE_CBQH = "cbqh"
ROLE_QTHT = "qtht"

USERS_FILE = Path(__file__).parent.parent / "data" / "users.json"

PBKDF2_ROUNDS = 120_000

# Tài khoản dựng sẵn lần đầu chạy. Mật khẩu lấy từ biến môi trường nếu có, để
# lúc demo trước khách không phải dùng đúng chuỗi ghi trong mã nguồn công khai.
SEED_USERS = {
    "cbqh": {
        "password": os.environ.get("CBQH_PASSWORD", "cbqh@2026"),
        "role": ROLE_CBQH,
        "display_name": "Đại uý Nguyễn Văn Hùng",
        "role_label": "Cán bộ quản lý",
        "avatar": "H",
    },
    "qtht": {
        "password": os.environ.get("QTHT_PASSWORD", "qtht@2026"),
        "role": ROLE_QTHT,
        "display_name": "Thiếu tá Lê Quang Trung",
        "role_label": "Quản trị hệ thống",
        "avatar": "T",
    },
}

_users = None


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS
    ).hex()


def _seed() -> dict:
    users = {}
    for username, seed in SEED_USERS.items():
        salt = secrets.token_hex(16)
        users[username] = {
            "role": seed["role"],
            "display_name": seed["display_name"],
            "role_label": seed["role_label"],
            "avatar": seed["avatar"],
            "salt": salt,
            "password_hash": _hash(seed["password"], salt),
        }
    return users


def _save(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def reload_users() -> dict:
    """Nạp lại kho tài khoản từ đĩa; chưa có thì dựng sẵn hai tài khoản mặc định."""
    global _users
    if USERS_FILE.exists():
        try:
            _users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _users = _seed()
            _save(_users)
    else:
        _users = _seed()
        _save(_users)
    return _users


def _load() -> dict:
    return _users if _users is not None else reload_users()


def _profile(username: str, user: dict) -> dict:
    return {
        "username": username,
        "role": user["role"],
        "display_name": user["display_name"],
        "role_label": user["role_label"],
        "avatar": user["avatar"],
    }


def authenticate(username: str, password: str) -> Optional[dict]:
    """Trả hồ sơ người dùng nếu đúng tài khoản, ``None`` nếu sai.

    So sánh bằng ``compare_digest`` để thời gian so sánh không phụ thuộc độ
    giống nhau của chuỗi.
    """
    name = (username or "").strip().lower()
    user = _load().get(name)
    if user is None:
        return None
    if not hmac.compare_digest(user["password_hash"], _hash(password or "", user["salt"])):
        return None
    return _profile(name, user)


def change_password(username: str, old_password: str, new_password: str) -> bool:
    """Đổi mật khẩu. Trả ``False`` nếu sai tài khoản hoặc sai mật khẩu cũ."""
    name = (username or "").strip().lower()
    users = _load()
    user = users.get(name)
    if user is None:
        return False
    if not hmac.compare_digest(user["password_hash"], _hash(old_password or "", user["salt"])):
        return False

    user["salt"] = secrets.token_hex(16)
    user["password_hash"] = _hash(new_password, user["salt"])
    _save(users)
    return True


def update_profile(username: str, display_name: str) -> Optional[dict]:
    """Đổi tên hiển thị. Trả ``None`` nếu không có tài khoản đó."""
    name = (username or "").strip().lower()
    users = _load()
    user = users.get(name)
    if user is None:
        return None

    user["display_name"] = display_name.strip()
    user["avatar"] = (display_name.strip()[-1:] or user["avatar"]).upper()
    _save(users)
    return _profile(name, user)
