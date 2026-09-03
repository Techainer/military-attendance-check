"""Đăng nhập cho bản POC: hai tài khoản khai cứng, không cơ sở dữ liệu.

**Đây không phải ranh giới bảo mật.** Không có phiên, không có token ký, và các
endpoint khác không kiểm quyền — ai gọi thẳng API vẫn làm được mọi thứ. Phần này
chỉ để giao diện biết đang là vai trò nào mà hiện đúng menu.

Muốn dùng thật thì phải thay bằng xác thực có phiên và kiểm quyền ở từng
endpoint; chỗ đó cố ý để trống chứ không phải quên.
"""

import hmac
import os
from typing import Optional

ROLE_CBQH = "cbqh"
ROLE_QTHT = "qtht"

# Mật khẩu lấy từ biến môi trường nếu có, để lúc demo trước khách không phải
# dùng đúng chuỗi ghi trong mã nguồn công khai.
USERS = {
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


def authenticate(username: str, password: str) -> Optional[dict]:
    """Trả về hồ sơ người dùng nếu đúng tài khoản, ``None`` nếu sai.

    So sánh mật khẩu bằng ``compare_digest`` để thời gian so sánh không phụ thuộc
    độ giống nhau của chuỗi.
    """
    user = USERS.get((username or "").strip().lower())
    if user is None:
        return None
    if not hmac.compare_digest(user["password"], password or ""):
        return None

    return {
        "username": (username or "").strip().lower(),
        "role": user["role"],
        "display_name": user["display_name"],
        "role_label": user["role_label"],
        "avatar": user["avatar"],
    }
