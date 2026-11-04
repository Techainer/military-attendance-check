"""Cầu nối giữa service AI và go2rtc.

Vì sao có file này: luồng xem trực tiếp trước đây đi qua Python (đọc RTSP bằng
OpenCV, mã hoá lại JPEG, bắn MJPEG) — tốn CPU và trễ theo tốc độ mã hoá.
go2rtc relay thẳng bit RTSP tới trình duyệt qua WebRTC/MSE, Python chỉ còn lo
AI. Nhưng go2rtc cần URI camera (có mật khẩu) nên:

1. App đẩy cấu hình nguồn xuống go2rtc qua REST API của go2rtc
   (``PUT /api/streams?name=...&src=...``) — mật khẩu không ra khỏi mạng nội bộ.
2. Trình duyệt CHỈ ra ngoài được qua tunnel tới cổng FastAPI (xem ghi chú ở
   đầu nhiệm vụ), nên FastAPI phải LÀM PROXY cho API HTTP + WebSocket của
   go2rtc dưới tiền tố ``/go2rtc/`` — ``router`` dưới đây.

Không dùng ``httpx`` (chưa có trong môi trường, xem báo cáo) — chỉ cần gọi
REST đơn giản nên dùng ``urllib`` có sẵn trong Python, khỏi phải thêm phụ
thuộc. WebSocket proxy dùng ``websockets`` (đã có sẵn, kéo theo bởi
``uvicorn[standard]``).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
import websockets
from websockets.exceptions import ConnectionClosed

from app import auth

# Tắt được bằng GO2RTC_ENABLED=0 để quay về MJPEG hoàn toàn (máy không chạy
# được go2rtc, hoặc đang gỡ lỗi).
GO2RTC_ENABLED = os.environ.get("GO2RTC_ENABLED", "1").strip().lower() not in ("0", "false", "")
GO2RTC_URL = os.environ.get("GO2RTC_URL", "http://127.0.0.1:1984").rstrip("/")

# Cấu hình STUN/TURN cho WebRTC, dạng JSON (RTCIceServer[]). Qua tunnel
# Cloudflare thì WebRTC là UDP thẳng, không đi qua được — cần TURN mới hoạt
# động; không set thì trình duyệt tự rơi về MSE (xem static/player.js).
_ICE_SERVERS_RAW = os.environ.get("GO2RTC_ICE_SERVERS", "")

# TURN dùng chung (coturn, chế độ use-auth-secret / "TURN REST API"): app không
# giữ mật khẩu TURN cố định nào, mà ký mật khẩu tạm từ TURN_SECRET:
#   username = "<hết_hạn_unix>:<tên>", credential = base64(HMAC-SHA1(secret, username))
# Mật khẩu chỉ cấp cho trình duyệt ĐÃ đăng nhập (xem v1_system_config) — lộ ra
# endpoint công khai là ai cũng mượn được TURN để relay lưu lượng của họ.
_TURN_URLS = [u.strip() for u in os.environ.get("TURN_URLS", "").split(",") if u.strip()]
_TURN_SECRET = os.environ.get("TURN_SECRET", "")
_TURN_TTL_S = int(os.environ.get("TURN_TTL_S", "43200"))

# Thời hạn gọi HTTP REST tới go2rtc (server nội bộ, nên ngắn là đủ)
_HTTP_TIMEOUT_S = 6.0


def turn_credentials(user: str = "horus", ttl_s: Optional[int] = None,
                     now: Optional[float] = None) -> Optional[dict]:
    """RTCIceServer có mật khẩu TURN tạm, hoặc None nếu chưa cấu hình TURN."""
    if not (_TURN_URLS and _TURN_SECRET):
        return None
    expiry = int((time.time() if now is None else now) + (ttl_s or _TURN_TTL_S))
    username = f"{expiry}:{user}"
    digest = hmac.new(_TURN_SECRET.encode(), username.encode(), hashlib.sha1).digest()
    return {"urls": list(_TURN_URLS), "username": username,
            "credential": base64.b64encode(digest).decode()}


def ice_servers(include_turn: bool = False) -> list:
    servers: list = []
    if _ICE_SERVERS_RAW:
        try:
            value = json.loads(_ICE_SERVERS_RAW)
            servers = value if isinstance(value, list) else []
        except (json.JSONDecodeError, TypeError):
            print(f"[go2rtc] GO2RTC_ICE_SERVERS không phải JSON hợp lệ, bỏ qua: {_ICE_SERVERS_RAW!r}")
    if include_turn:
        turn = turn_credentials()
        if turn:
            servers = servers + [turn]
    return servers


# ---------------------------------------------------------------- stream sync

def _stream_names(camera_id: str) -> Dict[str, str]:
    """Bốn tên luồng cho mỗi camera: chính/phụ × HEVC gốc/H.264 chuyển mã.

    Luôn tạo đủ bốn tên dù camera chưa khai luồng phụ — khi đó tên "_sub" chỉ
    là bí danh của luồng chính, để giao diện không cần biết camera có luồng
    phụ hay không, lúc nào cũng gọi đúng một quy ước tên.
    """
    return {
        "main": camera_id,
        "main_h264": f"{camera_id}_h264",
        "sub": f"{camera_id}_sub",
        "sub_h264": f"{camera_id}_sub_h264",
    }


def _put_stream(name: str, srcs: List[str]) -> Optional[str]:
    """``PUT /api/streams?name=...&src=...``. Trả None nếu OK, lý do lỗi nếu không."""
    if not srcs:
        return "không có nguồn"
    query = [("name", name)] + [("src", s) for s in srcs]
    url = f"{GO2RTC_URL}/api/streams?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            resp.read()
        return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
    except Exception as e:
        return str(e)


def _delete_stream(name: str) -> None:
    url = f"{GO2RTC_URL}/api/streams?{urllib.parse.urlencode({'src': name})}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            resp.read()
    except Exception as e:
        # Xoá là dọn dẹp best-effort: camera đã xoá khỏi hồ sơ rồi, go2rtc còn
        # giữ stream cũ vài phút cũng không hại gì, không cần chặn request.
        print(f"[go2rtc] Không xoá được stream '{name}': {e}")


def _go2rtc_src(uri: str) -> str:
    """go2rtc cần SCHEME để biết chọn producer nào (rtsp://, http://, ffmpeg:,
    exec:...). Camera nguồn RTSP đã có scheme, truyền thẳng. Camera nguồn TỆP
    lưu đường dẫn trần (để OpenCV phía AI đọc được, xem app/video_processor.py)
    — ở đây tự bọc ``ffmpeg:``, không đụng gì tới giá trị đã lưu.

    KHÔNG dùng ``#input=...`` để tự thêm ``-stream_loop`` (muốn phát lại vô hạn
    khi hết tệp): go2rtc chặn thẳng mọi src có khoảng trắng ("source with
    spaces may be insecure", chống chèn lệnh qua exec) — tệp demo/test ngắn
    thì dừng khi hết, biết trước và chấp nhận được (xem báo cáo, "known limitations").
    """
    if "://" in uri or uri.startswith(("ffmpeg:", "exec:")):
        return uri
    return f"ffmpeg:{uri}#video=copy"


# Tên luồng đã đăng ký lên go2rtc. Proxy WS chỉ cho xem đúng các tên này:
# go2rtc hiểu src là URL/nguồn động (vd "exec:..." chạy lệnh trên máy), nên
# không được để trình duyệt tự đặt src tuỳ ý.
_registered: set = set()
_STREAM_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def sync_camera(camera: dict) -> List[str]:
    """Đẩy cấu hình nguồn của MỘT camera lên go2rtc. Trả về danh sách lỗi (rỗng = OK).

    HÀM ĐỒNG BỘ (urllib chặn) — gọi qua ``asyncio.to_thread`` ở route, không
    thì một go2rtc chậm trả lời sẽ treo cả vòng lặp async của FastAPI.
    """
    if not GO2RTC_ENABLED:
        return []
    camera_id = camera.get("id")
    if not camera_id:
        return ["camera chưa có id"]
    main_src = (camera.get("source_uri") or "").strip()
    sub_src = (camera.get("substream_uri") or "").strip() or main_src
    names = _stream_names(camera_id)
    _registered.update(names.values())
    errors = []

    if main_src:
        for key, src in ((names["main"], main_src), (names["sub"], sub_src)):
            err = _put_stream(key, [_go2rtc_src(src)])
            if err:
                errors.append(f"{key}: {err}")
        # Luồng H.264 chuyển mã THAM CHIẾU luồng vừa tạo ở trên theo tên (go2rtc
        # hỗ trợ "ffmpeg:<tên stream đã khai>#..."), không cần URI gốc lần hai.
        for h264_key, base_key in ((names["main_h264"], names["main"]),
                                    (names["sub_h264"], names["sub"])):
            err = _put_stream(h264_key, [f"ffmpeg:{base_key}#video=h264#hardware"])
            if err:
                errors.append(f"{h264_key}: {err}")
    else:
        errors.append("camera chưa khai source_uri")

    return errors


def remove_camera(camera_id: str) -> None:
    if not GO2RTC_ENABLED:
        return
    for name in _stream_names(camera_id).values():
        _registered.discard(name)
        _delete_stream(name)


def sync_all(cameras: List[dict]) -> None:
    """Đồng bộ toàn bộ danh sách camera lên go2rtc — gọi lúc service khởi động,
    vì go2rtc tự khởi động lại là mất hết cấu hình stream đã PUT qua API (nó
    chỉ ghi lại YAML nếu có cấu hình cho phép, POC không dựa vào đó)."""
    if not GO2RTC_ENABLED:
        return
    for camera in cameras:
        errors = sync_camera(camera)
        if errors:
            print(f"[go2rtc] Đăng ký luồng camera '{camera.get('id')}' có lỗi: {errors}")


# ---------------------------------------------------------------- reverse proxy
# Trình duyệt chỉ ra ngoài được qua tunnel tới cổng FastAPI (xem đầu nhiệm vụ),
# không gọi thẳng được cổng go2rtc — nên mọi request của player.js phải đi qua
# đây dưới tiền tố /go2rtc/.

router = APIRouter()


def _ws_base() -> str:
    """http(s)://... -> ws(s)://... Tính lại mỗi lần gọi (không cache ở mức
    module) để test có thể trỏ GO2RTC_URL sang server giả mà không phải reload."""
    url = GO2RTC_URL
    return "ws" + url[len("http"):] if url.startswith("http") else url


@router.websocket("/api/ws")
async def proxy_ws(websocket: WebSocket) -> None:
    """Relay hai chiều WebSocket signalling/MSE của go2rtc (``/api/ws?src=...``).

    Một kết nối này mang CẢ đàm phán WebRTC (SDP qua JSON) và dữ liệu MSE nhị
    phân — xem video-rtc.js vendor — nên chỉ cần proxy đúng một đường WS, không
    cần route riêng cho WebRTC.

    Middleware kiểm phiên trong app/api.py chỉ bắt scope ``http``, không bắt
    ``websocket`` — tự kiểm cookie ở đây, đọc từ handshake qua
    ``websocket.cookies``. Thiếu cookie/hết hạn thì đóng trước khi accept,
    không để lộ stream camera cho người chưa đăng nhập.
    """
    if auth.AUTH_REQUIRED and auth.verify_session_token(
            websocket.cookies.get(auth.SESSION_COOKIE)) is None:
        await websocket.close(code=1008)
        return

    if not GO2RTC_ENABLED:
        await websocket.close(code=1013)
        return

    # Chỉ chuyển tiếp đúng một tham số src là tên luồng đã đăng ký; mọi giá
    # trị khác (URL, "exec:", "ffmpeg:"...) bị từ chối trước khi tới go2rtc
    src = websocket.query_params.get("src", "")
    if not _STREAM_NAME_RE.match(src) or src not in _registered:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    target = f"{_ws_base()}/api/ws?{urllib.parse.urlencode({'src': src})}"

    try:
        async with websockets.connect(target, max_size=None, open_timeout=_HTTP_TIMEOUT_S) as upstream:

            async def browser_to_go2rtc():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("text") is not None:
                        await upstream.send(msg["text"])
                    elif msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])

            async def go2rtc_to_browser():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = [asyncio.create_task(browser_to_go2rtc()), asyncio.create_task(go2rtc_to_browser())]
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
    except (ConnectionClosed, WebSocketDisconnect, OSError, asyncio.TimeoutError) as e:
        # go2rtc chưa chạy, hoặc trình duyệt đóng tab giữa chừng — bình thường,
        # không phải lỗi cần ồn ào trong log.
        print(f"[go2rtc proxy] luồng WS kết thúc: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


def _forward_http(method: str, url: str, body: bytes, content_type: Optional[str]):
    """Gọi HTTP đồng bộ tới go2rtc — chạy trong ``asyncio.to_thread``."""
    req = urllib.request.Request(url, data=body or None, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "application/json")
    except Exception as e:
        return 502, json.dumps({"error": str(e)}).encode(), "application/json"


@router.get("/api")
async def proxy_info(request: Request):
    """Chỉ cho đọc thông tin chung của go2rtc (phiên bản) để giao diện dò xem
    go2rtc còn sống không. API quản trị (/api/streams, /api/config, /api/restart…)
    KHÔNG được proxy: /api/streams lộ URI có mật khẩu, PUT nhận src "exec:" là
    chạy lệnh trên máy. Đăng ký luồng chỉ do server làm (sync_camera)."""
    if not GO2RTC_ENABLED:
        raise HTTPException(status_code=503, detail="go2rtc đang tắt (GO2RTC_ENABLED=0)")
    status, data, content_type = await asyncio.to_thread(
        _forward_http, "GET", f"{GO2RTC_URL}/api", b"", None)
    return Response(content=data, status_code=status, media_type=content_type)


@router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_blocked(path: str):
    raise HTTPException(status_code=403, detail="Không cho truy cập API quản trị go2rtc qua proxy")
