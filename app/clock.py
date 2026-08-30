"""Đồng hồ dùng chung cho toàn hệ thống.

Máy chủ có thể chạy timezone UTC trong khi thời khoá biểu, camera và người dùng
đều theo giờ Việt Nam. Mọi mốc thời gian trong ứng dụng phải lấy qua module này
để không bị lệch múi giờ.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_NAME = os.environ.get("APP_TZ", "Asia/Ho_Chi_Minh")

try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:
    print(f"[clock] Không nhận diện được múi giờ '{TZ_NAME}', dùng Asia/Ho_Chi_Minh")
    TZ_NAME = "Asia/Ho_Chi_Minh"
    TZ = ZoneInfo(TZ_NAME)


def now() -> datetime:
    """Giờ địa phương hiện tại, dạng naive để tương thích với phần code cũ."""
    return datetime.now(TZ).replace(tzinfo=None)


def stamp() -> str:
    """Chuỗi dấu thời gian in lên khung hình."""
    return now().strftime("%d/%m/%Y %H:%M:%S")


def iso(dt=None) -> str:
    """ISO-8601 kèm offset múi giờ, dạng hợp đồng API quy định.

    ``now()`` trả về dạng naive cho tương thích với phần code cũ, nên
    ``isoformat()`` trực tiếp sẽ mất phần ``+07:00`` và bên nhận dễ hiểu nhầm
    là giờ UTC.
    """
    dt = now() if dt is None else dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.isoformat()
