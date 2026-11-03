"""Kiểm chứng cầu nối go2rtc: đăng ký luồng, proxy HTTP/WebSocket, cấu hình cho UI.

Không cần go2rtc thật: dựng một máy chủ giả (HTTP thường + WebSocket) để xem
app/go2rtc.py gọi/chuyển tiếp đúng request, không cần quan tâm go2rtc xử lý gì.
"""

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

import websockets
from fastapi.testclient import TestClient

import app.api as api
from app import go2rtc

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


client = TestClient(api.app)

old_url, old_enabled, old_ice = go2rtc.GO2RTC_URL, go2rtc.GO2RTC_ENABLED, go2rtc._ICE_SERVERS_RAW


def reset_go2rtc_globals():
    go2rtc.GO2RTC_URL, go2rtc.GO2RTC_ENABLED, go2rtc._ICE_SERVERS_RAW = old_url, old_enabled, old_ice


# ================================================================ máy chủ go2rtc giả (HTTP)

received = []


class MockGo2rtcHandler(BaseHTTPRequestHandler):
    def _handle(self):
        received.append((self.command, self.path))
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_PUT = do_DELETE = do_POST = _handle

    def log_message(self, *a):  # im lặng, khỏi rác log test
        pass


mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockGo2rtcHandler)
mock_port = mock_server.server_address[1]
threading.Thread(target=mock_server.serve_forever, daemon=True).start()

go2rtc.GO2RTC_URL = f"http://127.0.0.1:{mock_port}"
go2rtc.GO2RTC_ENABLED = True


print("\n[1] Đăng ký luồng của một camera lên go2rtc")
received.clear()
errors = go2rtc.sync_camera({"id": "cam_test", "source_uri": "rtsp://u:p@host/stream1"})
check("không có lỗi khi go2rtc trả OK", errors == [], str(errors))
puts = [p for m, p in received if m == "PUT"]
check("đăng ký đủ 4 tên luồng (chính, chính-h264, phụ, phụ-h264)", len(puts) == 4, str(puts))
check("luồng chính dùng đúng URI gốc",
      any("name=cam_test&" in p and "src=rtsp" in p for p in puts), str(puts))
check("chưa khai luồng phụ riêng thì '_sub' là bí danh của luồng chính",
      any("name=cam_test_sub&" in p and "src=rtsp" in p for p in puts), str(puts))
check("luồng H.264 tham chiếu TÊN luồng đã đăng ký, không gửi lại URI gốc (giấu mật khẩu)",
      any("name=cam_test_h264&" in p and "ffmpeg" in p and "rtsp" not in p for p in puts), str(puts))


print("\n[2] Có luồng phụ riêng thì không dùng luồng chính làm bí danh")
received.clear()
go2rtc.sync_camera({"id": "cam_two", "source_uri": "rtsp://host/main",
                    "substream_uri": "rtsp://host/sub"})
puts = [p for m, p in received if m == "PUT"]
check("luồng phụ dùng đúng substream_uri",
      any("name=cam_two_sub&" in p and "src=rtsp%3A%2F%2Fhost%2Fsub" in p for p in puts), str(puts))


print("\n[3] Xoá camera thì xoá đủ bốn tên luồng khỏi go2rtc")
received.clear()
go2rtc.remove_camera("cam_test")
dels = [p for m, p in received if m == "DELETE"]
check("xoá đủ 4 tên luồng", len(dels) == 4, str(dels))
deleted_names = {parse_qs(urlparse(p).query).get("src", [None])[0] for p in dels}
check("tên luồng xoá đúng quy ước",
      deleted_names == {"cam_test", "cam_test_h264", "cam_test_sub", "cam_test_sub_h264"},
      str(dels))


print("\n[4] Camera chưa khai source_uri thì báo lỗi, không gọi go2rtc")
received.clear()
errors = go2rtc.sync_camera({"id": "cam_empty", "source_uri": ""})
check("báo lỗi rõ ràng", errors == ["camera chưa khai source_uri"], str(errors))
check("không gọi go2rtc khi thiếu nguồn", received == [], str(received))


print("\n[5] GO2RTC_ENABLED=0 thì không gọi gì tới go2rtc (và không lỗi)")
go2rtc.GO2RTC_ENABLED = False
received.clear()
errors = go2rtc.sync_camera({"id": "cam_test", "source_uri": "rtsp://host/s"})
check("sync_camera không gọi gì khi tắt", errors == [] and received == [], str((errors, received)))
go2rtc.remove_camera("cam_test")
check("remove_camera không gọi gì khi tắt", received == [], str(received))
go2rtc.GO2RTC_ENABLED = True


print("\n[6] Tạo/xoá camera qua API v1 tự đăng ký/gỡ luồng go2rtc")
import app.storage as storage  # noqa: E402

storage.write_json_list(api.cameras_file, [])
received.clear()
res = client.post("/api/v1/cameras", json={
    "name": "Cam go2rtc", "source_type": "rtsp", "source_uri": "rtsp://host/live",
})
check("tạo camera -> 201", res.status_code == 201, res.text)
cam_id = res.json()["id"]
check("tạo camera cũng đăng ký luồng lên go2rtc",
      any(m == "PUT" and f"name={cam_id}&" in p for m, p in received), str(received))

received.clear()
res = client.patch(f"/api/v1/cameras/{cam_id}", json={"substream_uri": "rtsp://host/sub"})
check("cập nhật camera -> 200", res.status_code == 200, res.text)
check("cập nhật camera cũng đồng bộ lại go2rtc",
      any(m == "PUT" for m, p in received), str(received))

received.clear()
res = client.delete(f"/api/v1/cameras/{cam_id}")
check("xoá camera -> 204", res.status_code == 204, res.text)
check("xoá camera cũng gỡ luồng khỏi go2rtc",
      any(m == "DELETE" and f"src={cam_id}" in p for m, p in received), str(received))


print("\n[7] Proxy HTTP /go2rtc/api/* chuyển tiếp đúng")
received.clear()
res = client.get("/go2rtc/api/streams")
check("GET qua proxy trả 200", res.status_code == 200, str(res.status_code))
check("proxy gọi đúng go2rtc", received == [("GET", "/api/streams")], str(received))
check("nội dung đi xuyên qua nguyên vẹn", res.json() == {"ok": True}, res.text)

received.clear()
res = client.put("/go2rtc/api/streams", params={"name": "x", "src": "rtsp://h/s"})
check("PUT qua proxy trả 200", res.status_code == 200)
check("PUT giữ nguyên query string", len(received) == 1 and received[0][0] == "PUT", str(received))


print("\n[8] go2rtc tắt thì proxy HTTP từ chối rõ ràng (503), không treo")
go2rtc.GO2RTC_ENABLED = False
res = client.get("/go2rtc/api/streams")
check("trả 503 khi tắt", res.status_code == 503, str(res.status_code))
go2rtc.GO2RTC_ENABLED = True


print("\n[9] /api/v1/system/config phản ánh đúng cờ bật/tắt + ICE servers")
go2rtc._ICE_SERVERS_RAW = '[{"urls": ["turn:example.com:3478"]}]'
res = client.get("/api/v1/system/config")
check("200 OK", res.status_code == 200)
body = res.json()
check("go2rtc_enabled = true", body.get("go2rtc_enabled") is True, str(body))
check("có tiền tố proxy /go2rtc", body.get("go2rtc_proxy_base") == "/go2rtc", str(body))
check("ice_servers đọc đúng từ JSON trong env",
      body.get("ice_servers") == [{"urls": ["turn:example.com:3478"]}], str(body))
go2rtc._ICE_SERVERS_RAW = ""

go2rtc.GO2RTC_ENABLED = False
check("go2rtc_enabled = false khi tắt", client.get("/api/v1/system/config").json().get("go2rtc_enabled") is False)
go2rtc.GO2RTC_ENABLED = True


print("\n[10] Proxy WebSocket relay hai chiều tới go2rtc (/api/ws)")


async def _mock_ws_handler(websocket):
    async for message in websocket:
        if isinstance(message, bytes):
            await websocket.send(bytes(reversed(message)))
        else:
            await websocket.send(f"echo:{message}")


def _run_mock_ws_server(ready_event, holder):
    async def main():
        async with websockets.serve(_mock_ws_handler, "127.0.0.1", 0) as server:
            holder["port"] = server.sockets[0].getsockname()[1]
            ready_event.set()
            await asyncio.Future()  # giữ server sống tới khi tiến trình test kết thúc (thread daemon)

    asyncio.run(main())


ws_ready = threading.Event()
ws_holder = {}
threading.Thread(target=_run_mock_ws_server, args=(ws_ready, ws_holder), daemon=True).start()
if not ws_ready.wait(timeout=5):
    check("máy chủ WS giả khởi động được", False, "timeout")
else:
    go2rtc.GO2RTC_URL = f"http://127.0.0.1:{ws_holder['port']}"
    try:
        with client.websocket_connect("/go2rtc/api/ws?src=cam_test") as ws:
            ws.send_text("hello")
            check("WS text đi và về đúng qua proxy", ws.receive_text() == "echo:hello")

            ws.send_bytes(b"\x01\x02\x03")
            check("WS binary đi và về đúng qua proxy", ws.receive_bytes() == b"\x03\x02\x01")
    except Exception as e:
        check("kết nối WS qua proxy thành công", False, str(e))


print("\n[11] go2rtc tắt thì proxy WS đóng kết nối ngay, không treo mãi")
go2rtc.GO2RTC_ENABLED = False
closed_properly = False
try:
    with client.websocket_connect("/go2rtc/api/ws?src=cam_test") as ws:
        try:
            ws.receive_text()
        except Exception:
            closed_properly = True
except Exception:
    closed_properly = True
check("đóng kết nối WS khi go2rtc tắt", closed_properly)


reset_go2rtc_globals()
mock_server.shutdown()

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
