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
import re
import threading
import time
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
# Đo độ lệch mỗi bấy nhiêu giây (chỉ đọc, rẻ) — bù lệch luôn theo số đo mới nhất.
CAMERA_CLOCK_READ_S = float(os.environ.get("CAMERA_CLOCK_READ_S", "60"))
# Biến CŨ: mặc định TẮT ghi, vì thực tế ở đơn vị có thiết bị khác (đầu ghi/phần
# mềm quản lý) đẩy giờ sai xuống camera vài phút một lần, app ghi lại (kiểu
# "manual" bên dưới) chỉ làm OSD nhảy qua lại. Vẫn đọc được qua biến này cho
# cấu hình CŨ chưa khai ``CAMERA_CLOCK_MODE`` (xem ngay dưới).
CAMERA_CLOCK_SYNC_ENABLED = os.environ.get("CAMERA_CLOCK_SYNC", "0").strip().lower() not in ("0", "false", "")

# Máy chủ NTP app CHỈNH cho camera lúc ``ensure_ntp`` ghi lại (mode "ntp").
# Mặc định dùng IP (time.google.com = 216.239.35.0): camera ở đơn vị không khai
# DNS (0.0.0.0) nên NTP theo tên miền báo "no dns", theo IP thì chạy được.
CAMERA_NTP_SERVER = (os.environ.get("CAMERA_NTP_SERVER") or "216.239.35.0").strip() or "216.239.35.0"
NTP_PORT = 123
# 1 phút: đầu ghi NVR ở đơn vị (giờ chậm ~7 phút) đẩy giờ sai xuống camera
# khoảng 12 phút một lần; NTP 1 phút kéo camera về đúng gần như ngay.
NTP_SYNCHRONIZE_INTERVAL_MIN = int(os.environ.get("CAMERA_NTP_INTERVAL_MIN", "1"))


def _default_clock_mode() -> str:
    """``CAMERA_CLOCK_MODE`` (ntp | manual | off) quyết định cách vòng nền GHI
    giờ camera (xem ``sync_camera_blocking``):

    - ``ntp``: trỏ camera vào máy chủ NTP (``ensure_ntp``) — camera tự giữ giờ
      đúng liên tục, không phải chỉnh tay mỗi lần lệch.
    - ``manual``: hành vi CŨ, ghi thẳng giờ máy chủ xuống camera (``sync``).
    - ``off``: chỉ đọc (``read``), không ghi gì — dùng khi có thiết bị khác
      đang tự quản giờ camera (xem comment ``CAMERA_CLOCK_SYNC_ENABLED``).

    Biến này THẮNG biến cũ ``CAMERA_CLOCK_SYNC`` khi được khai (dù để trống).
    Không khai cả hai -> ``ntp``. Chỉ khai ``CAMERA_CLOCK_SYNC`` (cấu hình cũ)
    -> giữ hành vi cũ: "1" = ``manual``, "0" = ``off``.
    """
    raw = os.environ.get("CAMERA_CLOCK_MODE")
    if raw is not None:
        mode = raw.strip().lower()
        return mode if mode in ("ntp", "manual", "off") else "ntp"
    if os.environ.get("CAMERA_CLOCK_SYNC") is None:
        return "ntp"
    return "manual" if CAMERA_CLOCK_SYNC_ENABLED else "off"


CAMERA_CLOCK_MODE = _default_clock_mode()

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
    # Không tin cờ ``sync_ok``: camera có thể bị thiết bị khác đặt lại giờ ngay
    # sau khi app chỉnh — chỉ số đo MỚI NHẤT (đọc mỗi CAMERA_CLOCK_READ_S) mới đúng.
    if not info:
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


def _parse_ntp_xml(xml_bytes: bytes) -> dict:
    """``<NTPServerList><NTPServer><hostName>time.google.com</hostName>
    <portNo>123</portNo><synchronizeInterval>60</synchronizeInterval>
    </NTPServer></NTPServerList>`` -> các trường đã tách (lấy máy chủ ĐẦU tiên,
    dòng camera dân dụng chỉ có một)."""
    root = ET.fromstring(xml_bytes)
    node = root.find("isapi:NTPServer", _NS)
    if node is None:
        node = root.find("NTPServer")
    if node is None:
        node = root

    def _find(tag):
        el = node.find(f"isapi:{tag}", _NS)
        if el is None:
            el = node.find(tag)
        return el.text if el is not None else None

    return {
        "host_name": _find("hostName") or _find("ipAddress"),
        "port_no": _find("portNo"),
        "synchronize_interval": _find("synchronizeInterval"),
    }


def _ntp_servers_payload(host_name: str) -> bytes:
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host_name):
        address = ('<addressingFormatType>ipaddress</addressingFormatType>'
                   f'<ipAddress>{host_name}</ipAddress>')
    else:
        address = ('<addressingFormatType>hostname</addressingFormatType>'
                   f'<hostName>{host_name}</hostName>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NTPServerList xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        '<NTPServer>'
        '<id>1</id>'
        + address
        + f'<portNo>{NTP_PORT}</portNo>'
        f'<synchronizeInterval>{NTP_SYNCHRONIZE_INTERVAL_MIN}</synchronizeInterval>'
        '</NTPServer>'
        '</NTPServerList>'
    ).encode("utf-8")


def ensure_ntp(camera: dict, data_dir: str = "data") -> dict:
    """Trỏ camera dùng NTP (``CAMERA_NTP_SERVER``) để tự giữ giờ đúng liên tục,
    thay vì phải chỉnh tay mỗi lần lệch (xem ``sync``, mode "manual").

    Chỉ ĐỌC rồi GHI khi cấu hình THỰC SỰ khác mong muốn (``timeMode`` chưa là
    NTP, hoặc máy chủ/múi giờ khác) — ghi lại dù không cần chỉ làm OSD nhảy qua
    lại vô ích, giống tinh thần ``sync`` cũ. Backup CẢ HAI XML (``time`` và
    ``ntpServers``) trước khi ghi, một bản mỗi ngày.
    """
    creds = _creds_from_source(camera)
    if creds is None:
        raise ValueError("Camera chưa có source_uri rtsp kèm user:mật khẩu để ghi ISAPI")
    host, user, password = creds
    port = int(camera.get("isapi_port") or ISAPI_PORT)
    opener = _opener(host, port, user, password)
    time_url = _isapi_url(host, port, "/ISAPI/System/time")
    ntp_url = _isapi_url(host, port, "/ISAPI/System/time/ntpServers")

    with opener.open(urllib.request.Request(time_url), timeout=HTTP_TIMEOUT_S) as resp:
        time_xml = resp.read()
    with opener.open(urllib.request.Request(ntp_url), timeout=HTTP_TIMEOUT_S) as resp:
        ntp_xml = resp.read()

    time_info = _parse_time_xml(time_xml)
    ntp_info = _parse_ntp_xml(ntp_xml)
    needs_write = (
        (time_info.get("time_mode") or "").upper() != "NTP"
        or (ntp_info.get("host_name") or "") != CAMERA_NTP_SERVER
        or (time_info.get("time_zone") or "") != "CST-7:00:00"
    )
    if not needs_write:
        return read(camera)

    backup_dir = Path(data_dir) / "camera_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    day = clock.now().strftime("%Y%m%d")
    # Cùng tên file backup giờ với ``sync()`` -> dù vòng nền đổi mode qua lại,
    # chỉ giữ đúng MỘT bản gốc mỗi ngày cho ``<Time>``.
    time_backup = backup_dir / f"{camera.get('id', 'cam')}_time_{day}.xml"
    ntp_backup = backup_dir / f"{camera.get('id', 'cam')}_ntpServers_{day}.xml"
    if not time_backup.exists():
        time_backup.write_bytes(time_xml)
    if not ntp_backup.exists():
        ntp_backup.write_bytes(ntp_xml)

    ntp_put = urllib.request.Request(
        ntp_url, data=_ntp_servers_payload(CAMERA_NTP_SERVER), method="PUT",
        headers={"Content-Type": "application/xml"})
    with opener.open(ntp_put, timeout=HTTP_TIMEOUT_S) as resp:
        resp.read()

    time_payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Time xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        '<timeMode>NTP</timeMode>'
        '<timeZone>CST-7:00:00</timeZone>'
        '</Time>'
    ).encode("utf-8")
    time_put = urllib.request.Request(time_url, data=time_payload, method="PUT",
                                      headers={"Content-Type": "application/xml"})
    with opener.open(time_put, timeout=HTTP_TIMEOUT_S) as resp:
        resp.read()

    return read(camera)


# ---------------------------------------------------------------- vòng nền

def _is_candidate(camera: dict) -> bool:
    """Chỉ những camera Hikvision thật (rtsp kèm user:mật khẩu) mới gọi được
    ISAPI; camera nguồn tệp/demo bỏ qua, không log lỗi mỗi vòng cho đỡ ồn."""
    return _creds_from_source(camera) is not None


def sync_camera_blocking(camera: dict, data_dir: str = "data") -> dict:
    """Đọc (và ghi, theo ``CAMERA_CLOCK_MODE``) giờ một camera, trả về số liệu
    kèm ``sync_ok``/``synced_at``. HÀM ĐỒNG BỘ — gọi qua ``asyncio.to_thread``.

    - ``ntp``: ``ensure_ntp`` (trỏ camera vào máy chủ NTP).
    - ``manual``: ``sync`` (ghi thẳng giờ máy chủ, hành vi cũ).
    - ``off``: chỉ ``read``.
    """
    if CAMERA_CLOCK_MODE == "ntp":
        result = ensure_ntp(camera, data_dir=data_dir)
    elif CAMERA_CLOCK_MODE == "manual":
        result = sync(camera, data_dir=data_dir)
    else:
        result = read(camera)
    offset_s = result.get("offset_s")
    result["sync_ok"] = bool(
        CAMERA_CLOCK_MODE != "off" and offset_s is not None and abs(offset_s) <= DRIFT_THRESHOLD_S
    )
    result["synced_at"] = clock.iso()
    return result


async def start_background(get_cameras, data_dir: str = "data") -> None:
    """Đo (và chỉnh, theo ``CAMERA_CLOCK_MODE``) giờ mọi camera lúc service
    khởi động, lặp lại mỗi ``CAMERA_CLOCK_SYNC_S`` giây. Camera đọc/ghi lỗi
    (mất mạng, sai mật khẩu...) thì GIỮ offset đo được gần nhất trong
    ``_state`` — chỉ huy cần biết độ lệch cuối cùng biết được, thà cũ còn hơn
    mất trắng.
    """
    print(f"[CameraClock] Khởi động mode={CAMERA_CLOCK_MODE}, đo mỗi {CAMERA_CLOCK_READ_S:.0f}s"
          + (f", chỉnh mỗi {CAMERA_CLOCK_SYNC_S:.0f}s" if CAMERA_CLOCK_MODE != "off"
             else " (chỉ đọc, không ghi lại camera)"))
    last_sync: Dict[str, float] = {}
    while True:
        for camera in get_cameras():
            camera_id = camera.get("id")
            if not camera_id or not _is_candidate(camera):
                continue
            try:
                now_mono = time.monotonic()
                if CAMERA_CLOCK_MODE != "off" and now_mono - last_sync.get(camera_id, -1e9) >= CAMERA_CLOCK_SYNC_S:
                    last_sync[camera_id] = now_mono
                    result = await asyncio.to_thread(sync_camera_blocking, camera, data_dir)
                else:
                    result = await asyncio.to_thread(read, camera)
                    result["sync_ok"] = False
                    result["synced_at"] = (offset_for(camera_id) or {}).get("synced_at")
                result["measured_at"] = clock.iso()
            except Exception as e:
                print(f"[CameraClock] Lỗi đo/chỉnh giờ camera {camera_id}: {e}")
                continue
            record_offset(camera_id, result)
        await asyncio.sleep(CAMERA_CLOCK_READ_S)
