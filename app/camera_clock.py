"""Đồng bộ giờ camera Hikvision qua ISAPI.

Vì sao có file này: camera giám sát chạy đồng hồ riêng, có thể lệch giờ máy
chủ vài giây tới vài phút, và đôi khi khai múi giờ (``timeZone``) khác với múi
giờ THẬT của đơn vị (quan sát trên camera thật: ``timeZone`` ghi ``CST-8:00:00``
trong khi giờ hiển thị trên OSD vẫn là giờ Việt Nam +07 — ai đó đã chỉnh tay
``localTime`` mà không sửa ``timeZone``). Ảnh bằng chứng điểm danh phải khớp
đồng hồ chỉ huy nhìn thấy TRÊN CAMERA (OSD), không phải giờ máy chủ, nên cần
đọc/chỉnh lại giờ camera qua ISAPI (``GET``/``PUT /ISAPI/System/time``).

Chỉ dùng ``urllib`` (xem app/go2rtc.py) — ``HTTPDigestAuthHandler`` có sẵn
trong thư viện chuẩn và Hikvision ISAPI xác thực digest, khỏi phải tự ký.
"""

import asyncio
import os
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from app import clock

ISAPI_PORT = 80
HTTP_TIMEOUT_S = 5.0

# Lệch quá ngưỡng này (giây) mà camera KHÔNG tự chỉnh được (ghi bị tắt hoặc
# ghi rồi vẫn lệch) thì app phải tự bù khi chụp ảnh bằng chứng (xem
# app/attendance.py should_compensate).
DRIFT_THRESHOLD_S = 2.0

# Chạy lại mỗi bấy nhiêu giây (mặc định 1 giờ — camera trôi giờ rất chậm,
# không cần dò liên tục).
CAMERA_CLOCK_SYNC_S = float(os.environ.get("CAMERA_CLOCK_SYNC_S", "3600"))
# =0 thì CHỈ đọc, không ghi xuống camera (môi trường demo/không muốn đụng
# cấu hình camera thật).
CAMERA_CLOCK_SYNC_ENABLED = os.environ.get("CAMERA_CLOCK_SYNC", "1").strip().lower() not in ("0", "false", "")

_NS = {"isapi": "http://www.hikvision.com/ver20/XMLSchema"}

# camera_id -> {"camera_time", "server_time", "offset_s", "time_mode",
#               "timezone", "sync_ok", "synced_at"}
_state: Dict[str, dict] = {}
_lock = threading.Lock()


def offset_for(camera_id: str) -> Optional[dict]:
    """Số liệu giờ camera gần nhất đo được (xem ``start_background``), hoặc
    ``None`` nếu camera chưa từng đo được (chưa chạy nền, hoặc không phải
    camera Hikvision có user:pass trong ``source_uri``)."""
    with _lock:
        value = _state.get(camera_id)
        return dict(value) if value is not None else None


def record_offset(camera_id: str, result: dict) -> None:
    """Ghi lại số liệu đo được MỚI NHẤT cho một camera. Expose công khai để
    route ``POST .../clock/sync`` cập nhật ngay, không phải chờ vòng nền."""
    with _lock:
        _state[camera_id] = dict(result)


def should_compensate(camera_id: Optional[str]) -> Optional[float]:
    """Offset (giây, camera − máy chủ) cần BÙ khi chụp ảnh bằng chứng, hoặc
    ``None`` nếu không cần: chưa đo được, lệch nhỏ, hoặc camera đã tự đồng bộ
    ổn (``sync_ok``). Dùng ở app/attendance.py."""
    if not camera_id:
        return None
    info = offset_for(camera_id)
    if not info or info.get("sync_ok"):
        return None
    offset_s = info.get("offset_s")
    if offset_s is None or abs(offset_s) <= DRIFT_THRESHOLD_S:
        return None
    return offset_s


# ---------------------------------------------------------------- ISAPI I/O

def _creds_from_source(camera: dict) -> Optional[Tuple[str, str, str]]:
    """Lấy ``(host, user, password)`` từ ``source_uri`` dạng rtsp của camera.
    ``None`` nếu không trích được (camera nguồn tệp, hoặc URI thiếu user/mật
    khẩu) — những camera đó không phải Hikvision ISAPI, bỏ qua."""
    uri = (camera.get("source_uri") or "").strip()
    if "://" not in uri:
        return None
    parsed = urlparse(uri)
    if not parsed.hostname or not parsed.username:
        return None
    return parsed.hostname, unquote(parsed.username), unquote(parsed.password or "")


def _isapi_url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def _opener(host: str, port: int, user: str, password: str):
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, f"http://{host}:{port}/", user, password)
    handler = urllib.request.HTTPDigestAuthHandler(mgr)
    return urllib.request.build_opener(handler)


def _parse_time_xml(xml_bytes: bytes) -> dict:
    """``<Time><timeMode>manual</timeMode><localTime>2026-11-11T11:32:14+08:00
    </localTime><timeZone>CST-8:00:00</timeZone></Time>`` -> các trường đã tách.
    """
    root = ET.fromstring(xml_bytes)

    def _find(tag):
        el = root.find(f"isapi:{tag}", _NS)
        if el is None:
            el = root.find(tag)
        return el.text if el is not None else None

    return {
        "time_mode": _find("timeMode"),
        "local_time": _find("localTime"),
        "time_zone": _find("timeZone"),
    }


def _camera_wall_clock(local_time: str) -> datetime:
    """``2026-11-11T11:32:14+08:00`` -> datetime NAIVE lấy đúng giờ TREO TƯỜNG
    (bỏ phần offset ``+08:00``): camera có thể khai ``timeZone`` sai múi so với
    giờ thật đang hiển thị trên OSD, nhưng số giờ:phút:giây hiển thị luôn đúng
    với thứ camera THỰC SỰ đang dùng để đóng dấu hình ảnh."""
    return datetime.fromisoformat(local_time).replace(tzinfo=None)


def read(camera: dict) -> dict:
    """Đọc giờ camera qua ISAPI (KHÔNG ghi gì). Ném lỗi nếu không đọc được —
    hàm ĐỒNG BỘ (urllib chặn), gọi qua ``asyncio.to_thread`` ở route/vòng nền."""
    creds = _creds_from_source(camera)
    if creds is None:
        raise ValueError("Camera chưa có source_uri rtsp kèm user:mật khẩu để đọc ISAPI")
    host, user, password = creds
    port = int(camera.get("isapi_port") or ISAPI_PORT)
    opener = _opener(host, port, user, password)

    req = urllib.request.Request(_isapi_url(host, port, "/ISAPI/System/time"))
    with opener.open(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = resp.read()
    parsed = _parse_time_xml(body)

    server_now = clock.now()
    camera_wall = _camera_wall_clock(parsed["local_time"]) if parsed["local_time"] else None
    offset_s = (camera_wall - server_now).total_seconds() if camera_wall is not None else None

    return {
        "camera_time": parsed["local_time"],
        "server_time": clock.iso(server_now),
        "offset_s": round(offset_s, 1) if offset_s is not None else None,
        "time_mode": parsed["time_mode"],
        "timezone": parsed["time_zone"],
    }


def sync(camera: dict, data_dir: str = "data") -> dict:
    """Chỉnh giờ camera theo giờ máy chủ, luôn dùng múi +07:00 (giờ thật của
    đơn vị — xem docstring đầu file, bất kể camera đang khai ``timeZone`` gì).

    Lưu lại XML gốc TRƯỚC khi ghi (``data/camera_backup/``) để có đường lùi
    nếu camera cấu hình sai đi; đọc lại NGAY sau khi ghi để trả về offset mới
    (và để lộ ra ngay nếu lệnh ghi không ăn, thay vì âm thầm coi là đã xong).
    """
    creds = _creds_from_source(camera)
    if creds is None:
        raise ValueError("Camera chưa có source_uri rtsp kèm user:mật khẩu để ghi ISAPI")
    host, user, password = creds
    port = int(camera.get("isapi_port") or ISAPI_PORT)
    opener = _opener(host, port, user, password)
    url = _isapi_url(host, port, "/ISAPI/System/time")

    get_req = urllib.request.Request(url)
    with opener.open(get_req, timeout=HTTP_TIMEOUT_S) as resp:
        original_xml = resp.read()

    backup_dir = Path(data_dir) / "camera_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Mỗi ngày giữ một bản là đủ để lùi lại — vòng nền chạy mỗi giờ, đừng xả rác.
    backup = backup_dir / f"{camera.get('id', 'cam')}_time_{clock.now():%Y%m%d}.xml"
    if not backup.exists():
        backup.write_bytes(original_xml)

    now_local = clock.now().strftime("%Y-%m-%dT%H:%M:%S") + "+07:00"
    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Time xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        '<timeMode>manual</timeMode>'
        f'<localTime>{now_local}</localTime>'
        '<timeZone>CST-7:00:00</timeZone>'
        '</Time>'
    ).encode("utf-8")
    put_req = urllib.request.Request(url, data=payload, method="PUT",
                                     headers={"Content-Type": "application/xml"})
    with opener.open(put_req, timeout=HTTP_TIMEOUT_S) as resp:
        resp.read()

    return read(camera)


# ---------------------------------------------------------------- vòng nền

def _is_candidate(camera: dict) -> bool:
    """Chỉ những camera Hikvision thật (rtsp kèm user:mật khẩu) mới gọi được
    ISAPI; camera nguồn tệp/demo bỏ qua, không log lỗi mỗi vòng cho đỡ ồn."""
    return _creds_from_source(camera) is not None


def sync_camera_blocking(camera: dict, data_dir: str = "data") -> dict:
    """Đọc (và ghi, nếu ``CAMERA_CLOCK_SYNC_ENABLED``) giờ một camera, trả về
    số liệu kèm ``sync_ok``/``synced_at``. HÀM ĐỒNG BỘ — gọi qua
    ``asyncio.to_thread``."""
    result = sync(camera, data_dir=data_dir) if CAMERA_CLOCK_SYNC_ENABLED else read(camera)
    offset_s = result.get("offset_s")
    result["sync_ok"] = bool(
        CAMERA_CLOCK_SYNC_ENABLED and offset_s is not None and abs(offset_s) <= DRIFT_THRESHOLD_S
    )
    result["synced_at"] = clock.iso()
    return result


async def start_background(get_cameras, data_dir: str = "data") -> None:
    """Đo (và chỉnh) giờ mọi camera lúc service khởi động, lặp lại mỗi
    ``CAMERA_CLOCK_SYNC_S`` giây. Camera đọc/ghi lỗi (mất mạng, sai mật
    khẩu...) thì GIỮ offset đo được gần nhất trong ``_state`` — chỉ huy cần
    biết độ lệch cuối cùng biết được, thà cũ còn hơn mất trắng.
    """
    print(f"[CameraClock] Khởi động, chạy mỗi {CAMERA_CLOCK_SYNC_S:.0f}s"
          + ("" if CAMERA_CLOCK_SYNC_ENABLED else " (chỉ đọc, không ghi lại camera)"))
    while True:
        for camera in get_cameras():
            camera_id = camera.get("id")
            if not camera_id or not _is_candidate(camera):
                continue
            try:
                result = await asyncio.to_thread(sync_camera_blocking, camera, data_dir)
            except Exception as e:
                print(f"[CameraClock] Lỗi đo/chỉnh giờ camera {camera_id}: {e}")
                continue
            record_offset(camera_id, result)
        await asyncio.sleep(CAMERA_CLOCK_SYNC_S)
