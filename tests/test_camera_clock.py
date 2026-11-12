"""Kiểm chứng đồng bộ giờ camera Hikvision qua ISAPI (app/camera_clock.py).

Không cần camera thật: dựng một máy chủ HTTP giả hiểu xác thực Digest (RFC
2617, như Hikvision ISAPI thật) và trả lại ``<Time>`` XML, để kiểm
``read``/``sync`` gọi đúng request + tính offset đúng, không cần quan tâm
firmware camera xử lý gì bên trong.

Chạy: uv run python tests/test_camera_clock.py
"""

import hashlib
import sys
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import camera_clock, clock

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# ================================================================ máy chủ ISAPI giả

REALM = "Hikvision_ISAPI"
NONCE = "testnonce-horus"
USER, PASSWORD = "admin", "Pass123!"

_camera_state = {"local_time": "", "time_zone": "CST-8:00:00", "time_mode": "manual"}
_ntp_state = {"host_name": "", "port_no": "123", "synchronize_interval": "60"}
_requests = []


def _xml_for_state() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Time xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        f'<timeMode>{_camera_state["time_mode"]}</timeMode>'
        f'<localTime>{_camera_state["local_time"]}</localTime>'
        f'<timeZone>{_camera_state["time_zone"]}</timeZone>'
        '</Time>'
    ).encode("utf-8")


def _xml_for_ntp() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NTPServerList xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        '<NTPServer>'
        '<id>1</id>'
        '<addressingFormatType>hostname</addressingFormatType>'
        f'<hostName>{_ntp_state["host_name"]}</hostName>'
        f'<portNo>{_ntp_state["port_no"]}</portNo>'
        f'<synchronizeInterval>{_ntp_state["synchronize_interval"]}</synchronizeInterval>'
        '</NTPServer>'
        '</NTPServerList>'
    ).encode("utf-8")


def _digest_ok(auth_header: str, method: str, path: str) -> bool:
    if not auth_header or not auth_header.startswith("Digest "):
        return False
    parts = {}
    for item in auth_header[len("Digest "):].split(","):
        if "=" not in item:
            continue
        k, v = item.strip().split("=", 1)
        parts[k] = v.strip('"')
    ha1 = hashlib.md5(f"{USER}:{REALM}:{PASSWORD}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{path}".encode()).hexdigest()
    if parts.get("qop") == "auth":
        expected = hashlib.md5(
            f"{ha1}:{parts.get('nonce')}:{parts.get('nc')}:{parts.get('cnonce')}:auth:{ha2}".encode()
        ).hexdigest()
    else:
        expected = hashlib.md5(f"{ha1}:{parts.get('nonce')}:{ha2}".encode()).hexdigest()
    return parts.get("username") == USER and expected == parts.get("response")


class ISAPIHandler(BaseHTTPRequestHandler):
    def _challenge(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Digest realm="{REALM}", nonce="{NONCE}", qop="auth"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authed(self, method: str) -> bool:
        return _digest_ok(self.headers.get("Authorization", ""), method, self.path)

    def do_GET(self):
        _requests.append(("GET", self.path))
        if self.path == "/ISAPI/System/time":
            if not self._authed("GET"):
                return self._challenge()
            body = _xml_for_state()
        elif self.path == "/ISAPI/System/time/ntpServers":
            if not self._authed("GET"):
                return self._challenge()
            body = _xml_for_ntp()
        else:
            return self._challenge()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        _requests.append(("PUT", self.path))
        if self.path not in ("/ISAPI/System/time", "/ISAPI/System/time/ntpServers") or not self._authed("PUT"):
            return self._challenge()
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        # Bóc vài trường đơn giản, khỏi kéo thêm thư viện XML ở phía "camera giả"
        import re
        if self.path == "/ISAPI/System/time":
            for tag, key in (("timeMode", "time_mode"), ("localTime", "local_time"), ("timeZone", "time_zone")):
                m = re.search(f"<{tag}>(.*?)</{tag}>", payload)
                if m:
                    _camera_state[key] = m.group(1)
        else:
            for tag, key in (("hostName", "host_name"), ("ipAddress", "host_name"), ("portNo", "port_no"),
                            ("synchronizeInterval", "synchronize_interval")):
                m = re.search(f"<{tag}>(.*?)</{tag}>", payload)
                if m:
                    _ntp_state[key] = m.group(1)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):  # im lặng, khỏi rác log test
        pass


mock_server = ThreadingHTTPServer(("127.0.0.1", 0), ISAPIHandler)
mock_port = mock_server.server_address[1]
threading.Thread(target=mock_server.serve_forever, daemon=True).start()

CAMERA = {
    "id": "cam_isapi_test",
    "source_uri": f"rtsp://{USER}:{PASSWORD}@127.0.0.1:554/stream1",
    "isapi_port": mock_port,
}

# Camera "lệch giờ": localTime hiện tại = giờ máy chủ + 125s, khai timeZone lạ
# (CST-8:00:00, như camera thật tester báo) để kiểm việc BỎ offset khai báo,
# chỉ lấy đúng giờ treo tường đã ghi.
server_now = clock.now()
_camera_state["local_time"] = (server_now + timedelta(seconds=125)).strftime("%Y-%m-%dT%H:%M:%S") + "+08:00"


# ================================================================ [1] đọc + tính offset

print("\n[1] read(): đọc giờ camera qua ISAPI (digest auth) và tính offset")
result = camera_clock.read(CAMERA)
check("đọc được localTime thô", result["camera_time"].startswith(server_now.strftime("%Y-%m-%d")))
check("offset ~ +125s (bỏ qua phần timeZone khai sai, chỉ lấy giờ treo tường)",
      120 <= result["offset_s"] <= 130, str(result["offset_s"]))
check("time_mode đọc được từ camera giả", result["time_mode"] == "manual", str(result))
check("timezone đọc được từ camera giả", result["timezone"] == "CST-8:00:00", str(result))
check("digest auth: có đúng 1 lần bị 401 rồi 1 lần GET thành công",
      sum(1 for m, p in _requests if m == "GET") == 2, str(_requests))


# ================================================================ [2] sync() chỉnh giờ

print("\n[2] sync(): ghi giờ máy chủ xuống camera (múi +07:00), backup XML gốc")
import tempfile
tmp_dir = Path(tempfile.mkdtemp())
_requests.clear()
synced = camera_clock.sync(CAMERA, data_dir=str(tmp_dir))

check("sau khi sync offset gần 0 (đã chỉnh theo giờ máy chủ)",
      abs(synced["offset_s"]) <= 2, str(synced["offset_s"]))
check("timeZone được ghi lại thành +07:00 (giờ thật của đơn vị)",
      _camera_state["time_zone"] == "CST-7:00:00", _camera_state["time_zone"])
check("timeMode được chỉnh thành manual", _camera_state["time_mode"] == "manual")

backup_files = list((tmp_dir / "camera_backup").glob("cam_isapi_test_time_*.xml"))
check("có lưu backup XML gốc trước khi ghi", len(backup_files) == 1, str(backup_files))
check("backup chứa đúng localTime CŨ (trước khi sync)",
      b"+08:00" in backup_files[0].read_bytes())


# ================================================================ [2b] ensure_ntp() chỉnh sang NTP

print("\n[2b] ensure_ntp(): trỏ camera vào máy chủ NTP mong muốn, backup cả time + ntpServers")
tmp_dir_ntp = Path(tempfile.mkdtemp())
_requests.clear()
# Camera đang ở chế độ manual, timeZone sai -> cần ghi lại cả hai XML
_camera_state["time_mode"] = "manual"
_camera_state["time_zone"] = "CST-8:00:00"
_ntp_state["host_name"] = ""

ntp_result = camera_clock.ensure_ntp(CAMERA, data_dir=str(tmp_dir_ntp))
check("GET cả /System/time và /System/time/ntpServers trước khi quyết định ghi",
      {"/ISAPI/System/time", "/ISAPI/System/time/ntpServers"} <= {p for _, p in _requests}, str(_requests))
check("đã PUT /System/time/ntpServers với hostName = CAMERA_NTP_SERVER",
      _ntp_state["host_name"] == camera_clock.CAMERA_NTP_SERVER, str(_ntp_state))
check("portNo/synchronizeInterval đúng mặc định (123 / 60)",
      _ntp_state["port_no"] == "123" and _ntp_state["synchronize_interval"] == "60", str(_ntp_state))
check("đã PUT /System/time với timeMode=NTP, timeZone=CST-7:00:00",
      _camera_state["time_mode"] == "NTP" and _camera_state["time_zone"] == "CST-7:00:00", str(_camera_state))
check("time_mode trả về cho caller là NTP (expose qua read())", ntp_result["time_mode"] == "NTP", str(ntp_result))

time_backups = list((tmp_dir_ntp / "camera_backup").glob("cam_isapi_test_time_*.xml"))
ntp_backups = list((tmp_dir_ntp / "camera_backup").glob("cam_isapi_test_ntpServers_*.xml"))
check("có backup XML gốc của /System/time trước khi ghi", len(time_backups) == 1, str(time_backups))
check("có backup XML gốc của /System/time/ntpServers trước khi ghi", len(ntp_backups) == 1, str(ntp_backups))

print("\n[2c] ensure_ntp(): đã đúng cấu hình rồi -> không ghi lại (tránh OSD nhảy qua lại vô ích)")
_requests.clear()
again = camera_clock.ensure_ntp(CAMERA, data_dir=str(tmp_dir_ntp))
check("không PUT gì cả khi cấu hình đã đúng NTP mong muốn",
      not any(m == "PUT" for m, _ in _requests), str(_requests))
check("vẫn trả về số liệu đọc được (time_mode=NTP)", again["time_mode"] == "NTP", str(again))


# ================================================================ [3] sync_camera_blocking + should_compensate

print("\n[3] sync_camera_blocking(): gắn cờ sync_ok, should_compensate() theo ngưỡng lệch")
camera_clock.CAMERA_CLOCK_MODE = "manual"   # mặc định "off" (không đổi camera thật), test bật lên
blocking_result = camera_clock.sync_camera_blocking(CAMERA, data_dir=str(tmp_dir))
check("sync_ok=True khi lệch đã về trong ngưỡng", blocking_result["sync_ok"] is True, str(blocking_result))

camera_clock.record_offset(CAMERA["id"], blocking_result)
check("should_compensate() == None khi lệch đo được nằm trong ngưỡng",
      camera_clock.should_compensate(CAMERA["id"]) is None)
camera_clock.record_offset(CAMERA["id"], {"offset_s": -419.0, "sync_ok": True, "synced_at": clock.iso()})
check("camera bị thiết bị khác đặt lại giờ -> vẫn bù theo số đo mới nhất dù cờ sync_ok cũ",
      camera_clock.should_compensate(CAMERA["id"]) == -419.0)

# Giả lập: camera lệch nặng mà KHÔNG tự chỉnh được (ghi bị tắt / ghi rồi vẫn lệch)
camera_clock.record_offset(CAMERA["id"], {"offset_s": 42.0, "sync_ok": False, "synced_at": clock.iso()})
drift = camera_clock.should_compensate(CAMERA["id"])
check("should_compensate() trả offset khi lệch > ngưỡng và chưa tự chỉnh được",
      drift == 42.0, str(drift))

camera_clock.record_offset(CAMERA["id"], {"offset_s": 1.0, "sync_ok": False, "synced_at": clock.iso()})
check("should_compensate() == None khi lệch nhỏ (<= ngưỡng), dù chưa 'sync_ok'",
      camera_clock.should_compensate(CAMERA["id"]) is None)

check("should_compensate() == None cho camera chưa từng đo (vd. camera_id=None)",
      camera_clock.should_compensate(None) is None and camera_clock.should_compensate("cam_unknown") is None)


# ================================================================ [4] camera không phải Hikvision / không đọc được -> fallback

print("\n[4] Camera không có user:mật khẩu trong source_uri -> không gọi ISAPI")
file_camera = {"id": "cam_file", "source_uri": "", "source_type": "file"}
raised = False
try:
    camera_clock.read(file_camera)
except ValueError:
    raised = True
check("read() báo lỗi rõ ràng (ValueError) khi camera không có creds rtsp", raised)

print("\n[5] Camera lỗi (mất mạng/sai cổng) -> giữ offset đo được gần nhất, không xoá")
camera_clock.record_offset("cam_down", {"offset_s": 7.0, "sync_ok": True, "synced_at": clock.iso()})
bad_camera = {"id": "cam_down", "source_uri": f"rtsp://{USER}:{PASSWORD}@127.0.0.1:1/stream1"}
read_failed = False
try:
    camera_clock.sync_camera_blocking(bad_camera, data_dir=str(tmp_dir))
except Exception:
    read_failed = True
check("camera không nối được thì ném lỗi (để vòng nền tự bắt + log, không âm thầm)", read_failed)
check("offset CŨ của camera đó vẫn còn nguyên (vòng nền KHÔNG xoá khi đo lỗi)",
      camera_clock.offset_for("cam_down")["offset_s"] == 7.0)


if failures:
    print(f"\n{len(failures)} kiểm thử LỖI: {failures}")
    sys.exit(1)
else:
    print("\nTất cả kiểm thử đạt.")
