"""Tài khoản và phiên đăng nhập: kho JSON nhỏ, mật khẩu băm PBKDF2, phiên ký
bằng HMAC trong cookie HttpOnly.

Ranh giới bảo mật thật nằm ở đây + middleware kiểm phiên trong ``app/api.py``:
mọi endpoint khác (trừ danh sách công khai) đều cần cookie ``horus_session``
hợp lệ, ký bằng ``AUTH_SECRET``, còn hạn, và khớp số phiên bản (token_version)
hiện tại của tài khoản. Đổi mật khẩu tăng số phiên bản này lên một, nên mọi
phiên cũ (kể cả bị đánh cắp) lập tức vô hiệu.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

ROLE_CBQH = "cbqh"
ROLE_QTHT = "qtht"

USERS_FILE = Path(__file__).parent.parent / "data" / "users.json"
SECRET_FILE = Path(__file__).parent.parent / "data" / ".auth_secret"

PBKDF2_ROUNDS = 120_000

# Tên cookie phiên. Dùng chung giữa middleware (app/api.py), proxy go2rtc
# (app/go2rtc.py) và front-end (gửi kèm tự động vì cùng gốc, không cần JS đọc).
SESSION_COOKIE = "horus_session"

# AUTH_REQUIRED=0 tắt hẳn việc kiểm phiên — chỉ dành cho máy phát triển cá
# nhân, không được tắt khi public ra ngoài qua tunnel.
AUTH_REQUIRED = os.environ.get("AUTH_REQUIRED", "1").strip().lower() not in ("0", "false", "")

# Phiên sống bao lâu (giờ). Hết hạn thì cookie còn đó nhưng verify thất bại.
SESSION_HOURS = float(os.environ.get("AUTH_SESSION_HOURS", "12"))

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
            # Tăng lên mỗi lần đổi mật khẩu để mọi phiên ký bằng số cũ bị
            # verify_session_token() từ chối ngay, không cần danh sách đen.
            "token_version": 0,
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
    user["token_version"] = int(user.get("token_version", 0)) + 1
    _save(users)
    return True


def token_version_of(username: str) -> int:
    """Số phiên bản phiên hiện tại của tài khoản. Tài khoản lạ coi như 0."""
    name = (username or "").strip().lower()
    user = _load().get(name)
    return int(user.get("token_version", 0)) if user else 0


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


# ---------------------------------------------------------------- phiên đăng nhập

_secret_cache: Optional[bytes] = None


def _secret() -> bytes:
    """Khoá ký HMAC. Lấy từ ``AUTH_SECRET`` nếu có; không thì tự sinh và lưu
    lại vào ``data/.auth_secret`` (quyền 600) để qua lần chạy sau phiên cũ vẫn
    còn ký đúng — không thì mỗi lần khởi động lại server là mọi người bị đăng
    xuất."""
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache

    env = os.environ.get("AUTH_SECRET")
    if env:
        _secret_cache = env.encode("utf-8")
        return _secret_cache

    if SECRET_FILE.exists():
        _secret_cache = SECRET_FILE.read_bytes()
        return _secret_cache

    secret = secrets.token_bytes(32)
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_bytes(secret)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass  # Windows hoặc filesystem không hỗ trợ chmod — chấp nhận được cho POC
    _secret_cache = secret
    return _secret_cache


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_b64: str) -> str:
    return hmac.new(_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def create_session_token(username: str, role: str, token_version: int,
                         hours: Optional[float] = None) -> str:
    """Token phiên: payload JSON (tài khoản, vai trò, số phiên bản, hết hạn)
    + chữ ký HMAC, nối bằng dấu chấm. Không mã hoá — không có gì bí mật trong
    payload, chỉ cần không giả mạo được và không sửa được.
    """
    hours = SESSION_HOURS if hours is None else hours
    payload = {
        "u": username, "r": role, "v": int(token_version),
        "exp": int(time.time() + hours * 3600),
    }
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_session_token(token: Optional[str]) -> Optional[dict]:
    """Kiểm token: chữ ký đúng, chưa hết hạn, và số phiên bản khớp tài khoản
    HIỆN TẠI (đổi mật khẩu xong là số phiên bản đổi, token cũ rớt ngay).

    Hồ sơ trả về đọc lại từ kho tài khoản (không lấy từ payload) để tên hiển
    thị/avatar luôn mới nhất, không phải bản chụp lúc đăng nhập.
    """
    if not token or "." not in token:
        return None

    payload_b64, _, signature = token.rpartition(".")
    if not hmac.compare_digest(_sign(payload_b64), signature):
        return None

    try:
        payload = json.loads(_b64d(payload_b64))
    except (ValueError, UnicodeDecodeError):
        return None

    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None

    name = (payload.get("u") or "").strip().lower()
    user = _load().get(name)
    if user is None:
        return None
    if int(user.get("token_version", 0)) != int(payload.get("v", -1)):
        return None

    return _profile(name, user)


# ---------------------------------------------------------------- chặn dò mật khẩu

# Tuỳ biến được từ test: 10 lần sai trong 10 phút thì khoá, theo IP VÀ theo
# tài khoản (chặn cả kẻ rải nhiều IP nhắm một tài khoản, và một IP dò nhiều
# tài khoản).
RATE_LIMIT_WINDOW_S = 600
RATE_LIMIT_MAX_FAILURES = 10

_failed_attempts: dict = {}


def _prune(key: str, now: float) -> list:
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < RATE_LIMIT_WINDOW_S]
    _failed_attempts[key] = attempts
    return attempts


def is_rate_limited(key: str) -> bool:
    return len(_prune(key, time.time())) >= RATE_LIMIT_MAX_FAILURES


def record_failure(key: str) -> None:
    now = time.time()
    _prune(key, now)
    _failed_attempts.setdefault(key, []).append(now)


def record_success(key: str) -> None:
    _failed_attempts.pop(key, None)
