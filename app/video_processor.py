"""Video processing pipeline for attendance monitoring and face recognition with Horus HUD.

Thiết kế: mỗi camera chạy HAI task song song thay vì một vòng lặp duy nhất,
vì vòng cũ publish khung hình SAU KHI AI xong hết (YOLO + SCRFD + vẽ + mã hoá),
nên AI chậm là luồng xem đứng hình theo.

- AI task  (``_ai_loop``)   : lấy khung MỚI NHẤT, chạy suy luận + toàn bộ
  nghiệp vụ (vùng, đếm quân số, điểm danh, an toàn, sự kiện), rồi CHỈ lưu một
  ``latest_result`` (số liệu, không vẽ) — chạy ở tốc độ AI cho phép (CPU chậm
  thì vài fps).
- Render task (``_render_loop``): lấy khung MỚI NHẤT (không đợi AI), vẽ
  ``latest_result`` (có thể tính từ khung cũ hơn) lên bằng ``draw_overlay``,
  mã hoá MỘT bản JPEG lớp phủ + MỘT bản sạch cho luồng xem, publish ở nhịp
  ``STREAM_FPS`` cố định — không phụ thuộc AI nhanh hay chậm.

Ảnh độ phân giải gốc (ảnh chụp, bằng chứng điểm danh) không còn mã hoá mỗi
khung hình; chỉ mã hoá LƯỜI lúc có người thực sự xin (xem ``_encode_on_demand``).
"""

import cv2
import re
import asyncio
import os
import threading
import time
import numpy as np
from pathlib import Path
from collections import deque
from typing import Optional, List, Dict, Tuple

from app import clock
from app.detector import PersonDetector
from app.overlay import draw_label
from app.monitor import AttendanceMonitor
from app.face_engine import FaceEngine
from app.attendance import AttendanceManager
from app.events import CAMERA_ID, EventStore, TYPE_ABSENT, TYPE_INTRUSION, normalize_box
from app.safety import RULE_ATTENDANCE, RULE_CROSSING, RULE_RESTRICTED, IntrusionDetector, ZoneStore


# Nhịp publish khung hình cho luồng xem, ĐỘC LẬP với tốc độ AI (camera có thể
# cấu hình target_fps khác, chậm hơn nhiều nếu CPU yếu).
STREAM_FPS = int(os.environ.get("STREAM_FPS", "12"))

# Mỗi camera có kho riêng: khung hình mới nhất và bộ đệm clip phát lại. Trước
# đây tất cả dùng chung một biến toàn cục nên chỉ chạy được một camera; hai
# camera cùng chạy thì camera sau ghi đè khung hình của camera trước.
_camera_state: Dict[str, dict] = {}

# Giữ khoảng bấy nhiêu giây khung hình cho đoạn phát lại, theo đúng nhịp publish
# thực tế (trước đây cố định 60 khung ở 5fps ~ 12s; giờ fps publish có thể khác).
CLIP_BUFFER_SECONDS = 12

# Mỗi đoạn clip giữ vài chục khung JPEG độ phân giải luồng (vài trăm KB), nên chỉ
# giữ lại bấy nhiêu đoạn gần nhất; luồng chạy cả ngày mà không dọn thì bộ nhớ
# phình theo số sự kiện.
MAX_STORED_CLIPS = 20
latest_event_clips: Dict[str, List[bytes]] = {}


def _state(camera_id: str) -> dict:
    return _camera_state.setdefault(camera_id, {
        "overlay": None,
        "clean": None,
        "stream_overlay": None,
        "stream_clean": None,
        # Tăng mỗi khung hình mới; luồng MJPEG dựa vào đây để biết đã có hình
        # mới chưa thay vì gửi lại đúng một khung nhiều lần.
        "revision": 0,
        "clips": deque(maxlen=max(60, round(CLIP_BUFFER_SECONDS * STREAM_FPS))),
        # Khung gốc (chưa vẽ) mới nhất + kết quả AI mới nhất: dùng để mã hoá
        # ẢNH ĐỘ PHÂN GIẢI GỐC theo kiểu lười, chỉ khi ảnh chụp/bằng chứng cần.
        "raw": None,
        "raw_ts": None,
        "result": None,
        # Luồng MJPEG chờ trên biến này thay vì hỏi liên tục mỗi X ms.
        "event": asyncio.Event(),
    })


# Luồng xem trực tiếp đi qua tunnel ~150 KB/s, trong khi một khung full HD chất
# lượng 85 nặng ~300 KB: 5 fps cần ~1.5 MB/s mỗi camera nên người xem chỉ nhận
# được chưa tới 1 fps. Bản thu nhỏ chỉ dùng cho luồng; ảnh chụp, bằng chứng điểm
# danh và ảnh vi phạm vẫn lấy bản gốc. Đặt STREAM_MAX_WIDTH=0 để tắt thu nhỏ.
STREAM_MAX_WIDTH = int(os.environ.get("STREAM_MAX_WIDTH", "960"))
STREAM_JPEG_QUALITY = int(os.environ.get("STREAM_JPEG_QUALITY", "65"))


def encode_stream_jpeg(frame) -> bytes:
    """JPEG nhẹ cho luồng xem trực tiếp. Khung vốn đã nhỏ thì không phóng to."""
    h, w = frame.shape[:2]
    if STREAM_MAX_WIDTH > 0 and w > STREAM_MAX_WIDTH:
        frame = cv2.resize(frame, (STREAM_MAX_WIDTH, round(h * STREAM_MAX_WIDTH / w)),
                           interpolation=cv2.INTER_AREA)
    return cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])[1].tobytes()


def publish_frame(camera_id: str, overlay_jpeg: Optional[bytes], clean_jpeg: Optional[bytes],
                  stream_overlay: Optional[bytes] = None,
                  stream_clean: Optional[bytes] = None,
                  raw=None, result: Optional[dict] = None,
                  ts: Optional[float] = None) -> None:
    """Công bố khung hình mới của một camera.

    ``overlay_jpeg``/``clean_jpeg`` là bản JPEG ĐỘ PHÂN GIẢI GỐC dựng sẵn — chỉ
    nên truyền khi đã có sẵn (ví dụ test gọi trực tiếp); vòng render thật không
    còn mã hoá bản này mỗi khung hình nữa, xem ``raw``/``result`` bên dưới.
    ``raw``/``result`` là khung gốc (numpy) + kết quả AI mới nhất, dùng để mã
    hoá ảnh độ phân giải gốc một cách LƯỜI khi có người xin (ảnh chụp, bằng
    chứng) — xem ``_encode_on_demand``.
    """
    st = _state(camera_id)
    st["overlay"] = overlay_jpeg
    st["clean"] = clean_jpeg
    st["stream_overlay"] = stream_overlay
    st["stream_clean"] = stream_clean
    if raw is not None:
        st["raw"] = raw
        st["raw_ts"] = ts if ts is not None else time.monotonic()
    if result is not None:
        st["result"] = result
    st["revision"] += 1
    if not st["event"].is_set():
        st["event"].set()


def clear_frames(camera_id: str) -> None:
    """Luồng dừng thì bỏ khung hình cũ, tránh phát lại hình đã chết."""
    st = _state(camera_id)
    st["overlay"] = None
    st["clean"] = None
    st["stream_overlay"] = None
    st["stream_clean"] = None
    st["raw"] = None
    st["raw_ts"] = None
    st["result"] = None
    # Đánh thức client MJPEG đang chờ để họ thấy ngay khung đã mất, khỏi phải
    # đợi hết hạn keepalive mới biết camera đã dừng.
    if not st["event"].is_set():
        st["event"].set()


def frame_event(camera_id: str) -> asyncio.Event:
    """Biến báo thức cho luồng MJPEG: chờ khung mới thay vì vòng lặp hỏi liên tục."""
    return _state(camera_id)["event"]


def _encode_on_demand(camera_id: str, overlay: bool) -> Optional[bytes]:
    """Mã hoá ảnh ĐỘ PHÂN GIẢI GỐC ngay lúc cần, từ khung gốc mới nhất.

    Vòng render giờ chỉ đẩy bản nhỏ cho luồng xem (``encode_stream_jpeg``), bỏ
    hẳn việc mã hoá bản đầy đủ-res mỗi khung hình — đó từng là một trong các lý
    do luồng xem bị nghẽn. Ảnh chụp/bằng chứng không xảy ra mỗi khung nên mã
    hoá lười ở đây, không tốn gì khi không ai xin.
    """
    st = _state(camera_id)
    raw = st.get("raw")
    if raw is None:
        return None
    frame = raw
    result = st.get("result")
    if overlay and result is not None:
        frame = draw_overlay(raw.copy(), result)
    return cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def get_frame(camera_id: str, overlay: bool, stream: bool = False) -> Optional[bytes]:
    """Khung hình mới nhất của camera. Không có bản gốc thì trả bản có lớp phủ.

    ``stream=True`` lấy bản thu nhỏ dành cho luồng xem trực tiếp, chưa có thì
    rơi về bản gốc (mã hoá lười nếu cần).
    """
    st = _state(camera_id)
    if stream:
        small = (st.get("stream_overlay") if overlay else st.get("stream_clean")) or st.get("stream_overlay")
        if small:
            return small
    cached = (st["overlay"] if overlay else st["clean"]) or st["overlay"]
    if cached:
        return cached
    return _encode_on_demand(camera_id, overlay)


def get_revision(camera_id: str) -> int:
    return _state(camera_id)["revision"]


def push_clip_frame(camera_id: str, frame, ts: Optional[float] = None) -> None:
    """Đẩy một khung vào bộ đệm phát lại của camera, kèm thời điểm chụp."""
    _state(camera_id)["clips"].append((frame, ts if ts is not None else time.monotonic()))


def keep_clip(camera_id: str, clip_id: str) -> str:
    """Chốt lại đoạn đệm hiện tại của camera và dọn bớt các đoạn cũ.

    Bộ đệm theo camera giữ (khung, thời điểm) để biết đúng nhịp khung hình; đoạn
    đã chốt chỉ cần nội dung khung, bỏ thời điểm cho nhẹ.
    """
    latest_event_clips[clip_id] = [frame for frame, _ts in _state(camera_id)["clips"]]
    for stale in list(latest_event_clips)[:-MAX_STORED_CLIPS]:
        del latest_event_clips[stale]
    return clip_id


# ---------- bộ đếm tần suất trượt, dùng cho chỉ số vận hành ----------

class RateCounter:
    """Đếm số lần xảy ra trong một khoảng trượt để suy ra tần suất tức thời."""

    def __init__(self, window_s: float = 5.0):
        self.window_s = window_s
        self._events: deque = deque()

    def tick(self, now_mono: Optional[float] = None) -> None:
        now_mono = now_mono if now_mono is not None else time.monotonic()
        self._events.append(now_mono)
        cutoff = now_mono - self.window_s
        while self._events and self._events[0] < cutoff:
            self._events.popleft()

    def rate(self) -> float:
        if len(self._events) < 2:
            return 0.0
        span = self._events[-1] - self._events[0]
        return (len(self._events) - 1) / span if span > 0 else 0.0


# ---------- pub/sub kết quả AI thô, cho overlay WebRTC/canvas sau này ----------
# Nhẹ hơn hẳn so với bắn khung hình JPEG qua WebSocket: chỉ số liệu + box chuẩn
# hoá [0,1], client tự vẽ lên <canvas> đặt trên nền WebRTC/MJPEG.

_result_subscribers: Dict[str, List[asyncio.Queue]] = {}


def subscribe_results(camera_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=5)
    _result_subscribers.setdefault(camera_id, []).append(queue)
    return queue


def unsubscribe_results(camera_id: str, queue: asyncio.Queue) -> None:
    subs = _result_subscribers.get(camera_id)
    if subs and queue in subs:
        subs.remove(queue)


def _publish_result(camera_id: str, payload: dict) -> None:
    """Đẩy cho các client đang đăng ký kết quả AI. Client đọc chậm thì bỏ bản
    cũ nhất trong hàng đợi của NÓ chứ không chặn vòng xử lý video."""
    for queue in list(_result_subscribers.get(camera_id, [])):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass


# Bao lâu không còn thấy một track thì bỏ ràng buộc danh tính của nó
IDENTITY_TTL_SECONDS = 20.0
# Điểm tương đồng cộng dồn tối thiểu (khoảng hai lượt khớp) trước khi gán cứng
# danh tính vào một track
IDENTITY_BIND_SCORE = 0.7
# Số vùng người được quét lại khuôn mặt ở độ phân giải cao mỗi khung hình
MAX_FACE_RESCANS = 4
# Chỉ quét lại những người có bề ngang box nhỏ hơn ngần này (mặt bị thu nhỏ khi
# quét cả khung hình nên hay bị bỏ sót)
FACE_RESCAN_MAX_WIDTH = 300


def _bbox_center(bbox) -> Tuple[float, float]:
    """Tâm của một bounding box, dạng float cho cv2.pointPolygonTest."""
    x1, y1, x2, y2 = bbox
    return ((float(x1) + float(x2)) / 2.0, (float(y1) + float(y2)) / 2.0)


# Mở luồng mạng phải chỉ đích danh backend FFMPEG. Để OpenCV tự chọn thì khi
# FFMPEG hỏng nó rơi tiếp xuống backend đọc dãy ảnh (CAP_IMAGES) và ném ra thông
# báo lạc đề "expected '0?[1-9][du]' pattern", che mất nguyên nhân thật
# (thường là không có đường tới camera, sai mật khẩu hoặc sai đường dẫn luồng).
STREAM_OPEN_TIMEOUT_S = float(os.environ.get("STREAM_OPEN_TIMEOUT", "8"))
# TCP đáng tin hơn UDP khi đi qua VPN hoặc mạng nhiều tầng; đổi được nếu camera
# chỉ hỗ trợ UDP.
STREAM_RTSP_TRANSPORT = os.environ.get("STREAM_RTSP_TRANSPORT", "tcp")

# Đặt MỘT LẦN lúc nạp module. Đặt lại mỗi lần mở luồng (như trước) là một biến
# môi trường toàn-tiến-trình bị ghi đè liên tục — hai camera mở gần nhau có thể
# đua nhau ghi, khiến cấu hình rtsp_transport của luồng này ăn sang luồng kia.
if STREAM_RTSP_TRANSPORT:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{STREAM_RTSP_TRANSPORT}"

# Không thấy khung mới trong ngần này giây thì coi luồng là "mất tín hiệu" và
# bắt đầu thử nối lại.
STREAM_STALE_S = float(os.environ.get("STREAM_STALE_S", "5"))
# Giãn cách giữa các lần thử nối lại, tăng dần để không dội liên tục vào một
# camera đang hỏng thật (sai mật khẩu, đứt cáp...).
RECONNECT_BACKOFF_MIN_S = 1.0
RECONNECT_BACKOFF_MAX_S = 30.0


def mask_credentials(uri) -> str:
    """Giấu mật khẩu trong địa chỉ luồng trước khi in ra log hay trả cho người dùng."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", str(uri or ""))


def open_stream_capture(uri: str):
    """VideoCapture cho nguồn mạng, ép dùng FFMPEG và truyền tải RTSP theo cấu hình."""
    return cv2.VideoCapture(uri, cv2.CAP_FFMPEG)


def probe_stream(uri: str) -> Optional[str]:
    """Thử mở luồng. Trả ``None`` nếu được, ngược lại trả lý do đọc được cho người trực.

    Mở trong luồng phụ để một camera treo không giữ cả yêu cầu HTTP: OpenCV không
    có tham số hết giờ dùng chung cho mọi phiên bản FFMPEG.
    """
    ket_qua = {}

    def _thu():
        cap = None
        try:
            cap = open_stream_capture(uri)
            ket_qua["mo_duoc"] = bool(cap.isOpened())
        except Exception as e:                      # nguồn lạ khiến FFMPEG ném lỗi
            ket_qua["loi"] = str(e).splitlines()[0][:200]
        finally:
            if cap is not None:
                cap.release()

    worker = threading.Thread(target=_thu, daemon=True)
    worker.start()
    worker.join(STREAM_OPEN_TIMEOUT_S)

    dia_chi = mask_credentials(uri)
    if worker.is_alive():
        return (f"Quá {STREAM_OPEN_TIMEOUT_S:.0f} giây vẫn chưa mở được luồng {dia_chi}. "
                f"Kiểm tra máy chủ có đường tới camera không.")
    if ket_qua.get("loi"):
        return f"Lỗi mở luồng {dia_chi}: {ket_qua['loi']}"
    if not ket_qua.get("mo_duoc"):
        return (f"Không mở được luồng {dia_chi}. Thường do máy chủ không có đường tới "
                f"camera (khác dải mạng / chưa mở VPN), sai tài khoản hoặc sai đường dẫn luồng.")
    return None


class FrameSource:
    """Giao diện chung cho nguồn khung hình thô: RTSP hoặc tệp video.

    AI task và render task chỉ cần ``read()``; không cần biết nguồn là RTSP hay
    tệp, nhờ vậy cả hai task dùng chung một đường xử lý cho mọi loại nguồn.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._cap_ts = 0.0
        self.started = False
        # Hết nguồn hẳn (tệp phát xong) — khác với "stale": không thử nối lại.
        self.ended = False
        self.error: Optional[str] = None
        self.reconnects = 0
        self.stale = False
        self.capture_rate = RateCounter()

    def _publish(self, frame) -> None:
        now_mono = time.monotonic()
        with self._lock:
            self._frame = frame
            self._seq += 1
            self._cap_ts = now_mono
        self.capture_rate.tick(now_mono)

    def read(self):
        """``(ok, frame, seq, ts)``. Không copy: người gọi tự copy khi cần vẽ
        lên khung, tránh tốn một lượt cấp phát + chép ảnh mỗi lần đọc."""
        with self._lock:
            if self._frame is None:
                return False, None, self._seq, self._cap_ts
            return True, self._frame, self._seq, self._cap_ts

    def stop(self) -> None:
        raise NotImplementedError


class RTSPStreamReader(FrameSource):
    """Đọc liên tục từ luồng RTSP/mạng trong một thread riêng, tự nối lại khi mất tín hiệu."""

    def __init__(self, rtsp_url: str):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.cap = open_stream_capture(rtsp_url)
        # Giữ hàng đợi giải mã ở mức tối thiểu để khung hình luôn là mới nhất,
        # nếu không hình trên màn hình sẽ trễ dần so với thời gian thực.
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self.thread = None
        self._stop_flag = False

    def start(self):
        if self.started:
            return self
        if not self.cap.isOpened():
            self.error = (f"Không mở được luồng {mask_credentials(self.rtsp_url)}. "
                          f"Kiểm tra đường mạng tới camera, tài khoản và đường dẫn luồng.")
            print(f"[Camera] {self.error}")
            return self
        self.started = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.daemon = True
        self.thread.start()
        return self

    def _reopen(self) -> None:
        try:
            self.cap.release()
        except Exception:
            pass
        self.cap = open_stream_capture(self.rtsp_url)
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self.reconnects += 1

    def _run(self) -> None:
        last_good = time.monotonic()
        backoff = RECONNECT_BACKOFF_MIN_S
        while not self._stop_flag:
            if not self.cap.isOpened():
                time.sleep(0.1)
                continue
            ret, frame = self.cap.read()
            now_mono = time.monotonic()
            if not ret or frame is None:
                if now_mono - last_good > STREAM_STALE_S:
                    if not self.stale:
                        self.stale = True
                        print(f"[Camera] Mất tín hiệu, thử nối lại: {mask_credentials(self.rtsp_url)}")
                    time.sleep(backoff)
                    self._reopen()
                    backoff = min(RECONNECT_BACKOFF_MAX_S, backoff * 2)
                else:
                    time.sleep(0.01)
                continue
            last_good = now_mono
            backoff = RECONNECT_BACKOFF_MIN_S
            if self.stale:
                self.stale = False
                print(f"[Camera] Đã nối lại luồng: {mask_credentials(self.rtsp_url)}")
            self._publish(frame)
            time.sleep(0.005)

    def stop(self) -> None:
        self._stop_flag = True
        self.started = False
        if self.thread:
            self.thread.join(timeout=1.0)
        try:
            if self.cap.isOpened():
                self.cap.release()
        except Exception:
            pass


class FileStreamReader(FrameSource):
    """Đọc tệp video, phát khung đúng theo nhịp thời gian thực của tệp.

    Trước đây đọc nhanh hết mức CPU cho phép rồi chỉ GIỮ LẠI 1/N khung (frame
    skip) để giả lập fps mong muốn — tệp phát nhanh hơn thời gian thật, AI đuổi
    theo một nguồn ảo. Phát đúng nhịp gốc thì AI/luồng xem thấy đúng tốc độ như
    một camera thật đang quay.
    """

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.cap = cv2.VideoCapture(path)
        fps = self.cap.get(cv2.CAP_PROP_FPS) if self.cap.isOpened() else 0
        self.video_fps = fps if fps and fps > 0 else 30.0
        self.thread = None
        self._stop_flag = False

    def start(self):
        if self.started:
            return self
        if not self.cap.isOpened():
            self.error = "Không thể mở tệp video"
            return self
        self.started = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.daemon = True
        self.thread.start()
        return self

    def _run(self) -> None:
        interval = 1.0 / self.video_fps
        next_due = time.monotonic()
        while not self._stop_flag:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                self.ended = True
                break
            self._publish(frame)
            next_due += interval
            sleep_s = next_due - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                # Máy chậm hơn tốc độ tệp: không dồn khung, bắt kịp ngay từ đây
                next_due = time.monotonic()
        self.started = False

    def stop(self) -> None:
        self._stop_flag = True
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap.isOpened():
            self.cap.release()


def _attendance_polygon_points(zones_cfg, w_img: int, h_img: int):
    """Polygon đếm quân số: dùng để LỌC người trong vùng và để VẼ, cùng một
    nguồn để hai việc không bao giờ lệch nhau.

    Có vùng ``attendance_area`` đã cấu hình thì lấy từ đó; chưa cấu hình gì thì
    dùng vùng ngũ giác mặc định như hành vi cũ.
    """
    for zone in zones_cfg:
        points = zone.get("points", [])
        if zone.get("rule") == RULE_ATTENDANCE and len(points) >= 3:
            pts = np.array([[int(p["x"] * w_img), int(p["y"] * h_img)] for p in points], np.int32)
            return pts, False
    raw_pts = [
        [int(w_img * 0.08), int(h_img * 0.75)],
        [int(w_img * 0.35), int(h_img * 0.60)],
        [int(w_img * 0.70), int(h_img * 0.62)],
        [int(w_img * 0.92), int(h_img * 0.85)],
        [int(w_img * 0.12), int(h_img * 0.90)],
    ]
    return np.array(raw_pts, np.int32), True


def draw_overlay(frame, result: Optional[dict]):
    """Vẽ toàn bộ lớp phủ HUD lên ``frame`` theo dữ liệu đã tính sẵn trong
    ``result``. Hàm THUẦN (không đụng gì ngoài hai tham số): vòng render gọi
    hàm này để vẽ kết quả AI (có thể hơi cũ) lên một khung hình MỚI HƠN khung mà
    AI đã dùng để tính ra kết quả đó.
    """
    if result is None:
        return frame

    h_img, w_img = frame.shape[:2]
    zones_cfg = result.get("zones") or []

    np_pts = None
    for zone in zones_cfg:
        points = zone.get("points", [])
        rule = zone.get("rule")
        if rule == RULE_CROSSING and len(points) >= 2:
            p1 = (int(points[0]["x"] * w_img), int(points[0]["y"] * h_img))
            p2 = (int(points[1]["x"] * w_img), int(points[1]["y"] * h_img))
            cv2.line(frame, p1, p2, (0, 140, 255), 2, cv2.LINE_AA)
            continue
        if len(points) < 3:
            continue

        pts = np.array([[int(p["x"] * w_img), int(p["y"] * h_img)] for p in points], np.int32)
        fill, border = ((20, 160, 60), (30, 220, 90)) if rule == RULE_ATTENDANCE \
            else ((0, 0, 200), (0, 60, 255))
        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts], fill)
        cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
        cv2.polylines(frame, [pts], isClosed=True, color=border, thickness=2)
        if rule == RULE_ATTENDANCE:
            np_pts = pts
        elif rule == RULE_RESTRICTED:
            draw_label(frame, f"VÙNG CẤM · {zone.get('name', '')}",
                       (int(pts[:, 0].mean()), int(pts[:, 1].min())), font_size=14,
                       bg_color=(0, 40, 160), anchor="bottom")

    if np_pts is None:
        np_pts, _ = _attendance_polygon_points(zones_cfg, w_img, h_img)
        cv2.polylines(frame, [np_pts], isClosed=True, color=(30, 220, 90), thickness=2)

    for track in result.get("tracks", []):
        x1, y1, x2, y2 = track["box"]
        conf = track["confidence"]
        is_in_zone = track["in_zone"]
        is_occluded = track["occluded"]
        identity = track.get("identity")

        if track["intrusion"]:
            # VI PHẠM AN TOÀN: khoanh đỏ đậm, ưu tiên hơn mọi trạng thái khác
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
            draw_label(frame, "VI PHẠM AN TOÀN", (x1, y1), font_size=15,
                       bg_color=(0, 0, 200), anchor="bottom")
        elif is_in_zone:
            if identity is not None:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 120), 2)
                rank_str = identity.get('rank', '')
                name_str = identity.get('name', '')
                mid_str = identity.get('military_id', '')
                id_label = f"[{rank_str}] {name_str} ({mid_str})" if rank_str else f"{name_str} ({mid_str})"
                draw_label(frame, id_label, (x1, y1), font_size=15, bg_color=(0, 180, 70), anchor="bottom")
            elif is_occluded:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 190, 230), 1)
                draw_label(frame, "Bị che khuất", (x1, y1), font_size=14,
                           bg_color=(0, 120, 150), anchor="bottom")
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 220, 80), 2)
                draw_label(frame, f"Trong vùng {int(conf * 100)}%", (x1, y1), font_size=14,
                           bg_color=(10, 140, 50), anchor="bottom")
        else:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (120, 130, 140), 1)
            draw_label(frame, "Ngoài vùng", (x1, y1), font_size=13,
                       text_color=(200, 205, 215), bg_color=(60, 65, 75), anchor="bottom")

    for fbox in result.get("matched_face_boxes", []):
        fx1, fy1, fx2, fy2 = fbox
        cv2.rectangle(frame, (fx1, fy1), (fx2, fy2), (0, 255, 120), 2)

    hud = result.get("hud") or {}
    cv2.circle(frame, (25, 28), 6, (0, 0, 240), -1)
    draw_label(frame, hud.get("camera_name", ""), (38, 18), font_size=16,
               text_color=(230, 230, 230), bg_color=(18, 22, 28))
    draw_label(frame, hud.get("time_str", ""), (20, 46), font_size=16,
               text_color=(200, 215, 230), bg_color=(18, 22, 28))
    draw_label(frame, hud.get("stat_line", ""), (w_img, 14), font_size=17,
               text_color=(0, 255, 120), bg_color=(15, 30, 20))
    if hud.get("roll_line"):
        draw_label(frame, hud["roll_line"], (16, 78), font_size=17,
                   text_color=(0, 200, 255), bg_color=(10, 45, 90))
    draw_label(frame, hud.get("source_label", ""), (18, h_img - 12), font_size=14,
               text_color=(160, 175, 190), bg_color=(18, 22, 28), anchor="bottom")

    return frame


def _render_pair(frame, result: Optional[dict]):
    """Vẽ + mã hoá một cặp JPEG (lớp phủ, sạch) cho luồng xem. Chạy trong
    ``asyncio.to_thread`` nên phải là hàm đồng bộ, không đụng asyncio."""
    overlay_frame = draw_overlay(frame.copy(), result) if result is not None else frame
    overlay_jpeg = encode_stream_jpeg(overlay_frame)
    clean_jpeg = encode_stream_jpeg(frame)
    return overlay_jpeg, clean_jpeg


class VideoProcessor:
    """Processes video frames for person detection, face recognition, and attendance monitoring."""

    def __init__(
        self,
        fps: int = 5,
        face_engine: Optional[FaceEngine] = None,
        attendance: Optional[AttendanceManager] = None,
        events: Optional[EventStore] = None,
        data_dir: str = "data",
        camera_id: str = CAMERA_ID,
        camera_name: str = "",
    ):
        self.camera_id = camera_id
        self.camera_name = camera_name or camera_id
        self.detector = PersonDetector(model_name=os.environ.get("YOLO_MODEL", "yolo11s.pt"))
        # Model nhận diện khuôn mặt dùng chung được: nó không giữ trạng thái của
        # riêng luồng nào. Bộ phát hiện người thì mỗi camera một bản vì có tracker.
        self.face_engine = face_engine or FaceEngine()
        self.attendance = attendance
        self.events = events
        # Chỉ lấy vùng của chính camera này; trước đây lấy hết nên camera này
        # réo còi theo vùng cấm vẽ cho camera khác.
        self.zones = ZoneStore(data_dir, camera_id=camera_id)
        self.intrusion = IntrusionDetector(self.zones)
        self.fps = fps
        self._stop_requested = False
        # track_id -> {"scores": {person_id: điểm cộng dồn}, "person": dict, "last_seen": ts}
        self.track_identity: Dict[int, dict] = {}

        # Trạng thái chia sẻ giữa AI task và render task
        self.reader: Optional[FrameSource] = None
        self.latest_result: Optional[dict] = None
        # Tham chiếu các task nền (dựng clip) để không bị garbage-collect giữa chừng
        self._bg_tasks: set = set()
        self._ai_rate = RateCounter()
        self._publish_rate = RateCounter()
        self._infer_ms: deque = deque(maxlen=50)

    def stop(self) -> None:
        """Request the processing pipeline to stop."""
        self._stop_requested = True

    # ---------- chỉ số vận hành ----------

    def get_metrics(self) -> dict:
        """Chỉ số vận hành của camera này, cho ``GET /api/v1/system/health``."""
        reader = self.reader
        samples = sorted(self._infer_ms)

        def _pct(p: float) -> float:
            if not samples:
                return 0.0
            idx = min(len(samples) - 1, int(p * len(samples)))
            return round(samples[idx], 1)

        raw_ts = _state(self.camera_id).get("raw_ts")
        frame_age_ms = round(max(0.0, (time.monotonic() - raw_ts) * 1000), 1) if raw_ts else None

        return {
            "capture_fps": round(reader.capture_rate.rate(), 2) if reader else 0.0,
            "ai_fps": round(self._ai_rate.rate(), 2),
            "infer_ms_p50": _pct(0.5),
            "infer_ms_p95": _pct(0.95),
            "publish_fps": round(self._publish_rate.rate(), 2),
            "frame_age_ms": frame_age_ms,
            "stale": bool(reader.stale) if reader else False,
            "reconnects": reader.reconnects if reader else 0,
        }

    # ---------- nhận diện ----------

    def _assign_faces(self, faces, person_boxes, in_zone_indices, claimed, assignments) -> list:
        """Gắn mỗi khuôn mặt nhận ra được vào một người đang đứng trong vùng.

        Gán vào box người TRONG VÙNG nhỏ nhất có chứa tâm khuôn mặt; mỗi box chỉ
        nhận một người. Không dùng polygon để lọc mặt: polygon là vùng mặt đất nên
        tâm khuôn mặt luôn nằm phía trên nó. Mặt không gắn được vào ai trong vùng
        thì không tính điểm danh.
        """
        matched_boxes = []
        for rf in faces:
            if not (rf.get("is_recognized") and rf.get("person")):
                continue

            fcx, fcy = _bbox_center(rf["bbox"])
            best_idx = None
            best_area = None
            for p_idx in in_zone_indices:
                if p_idx in claimed:
                    continue
                px1, py1, px2, py2 = person_boxes[p_idx]
                if not (px1 - 20 <= fcx <= px2 + 20 and py1 - 20 <= fcy <= py2 + 40):
                    continue
                area = max(1, (px2 - px1) * (py2 - py1))
                if best_area is None or area < best_area:
                    best_area = area
                    best_idx = p_idx

            if best_idx is None:
                continue

            claimed.add(best_idx)
            assignments[best_idx] = {"person": rf["person"], "similarity": rf.get("similarity", 0.0)}
            matched_boxes.append(rf["bbox"])

        return matched_boxes

    async def _archive_clip(self, event: dict, clip_id: Optional[str]) -> None:
        """Ghi đoạn phát lại của sự kiện ra file mp4.

        Bộ đệm khung hình nằm trong RAM nên khởi động lại là mất. Dựng mp4 tốn
        CPU (giải mã vài chục ảnh JPEG) nên đẩy sang luồng khác, không chặn vòng
        xử lý khung hình.
        """
        if self.events is None or not clip_id:
            return
        frames = latest_event_clips.get(clip_id)
        if not frames:
            return
        await asyncio.to_thread(self.events.save_clip, event["id"], list(frames))

    def _archive_clip_bg(self, event: dict, clip_id: Optional[str]) -> None:
        """Bản bắn-rồi-quên của ``_archive_clip``: vòng AI không chờ dựng mp4
        xong mới xử lý khung tiếp theo. Giữ tham chiếu task để không bị GC giữa
        chừng, và log nếu dựng clip lỗi (không để lỗi âm thầm biến mất)."""
        task = asyncio.create_task(self._archive_clip_safe(event, clip_id))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _archive_clip_safe(self, event: dict, clip_id: Optional[str]) -> None:
        try:
            await self._archive_clip(event, clip_id)
        except Exception as e:
            print(f"[Camera] Lỗi dựng đoạn ghi nền cho sự kiện {event.get('id')}: {e}")

    def _rescan_regions(self, person_boxes, in_zone_indices, claimed, frame_shape) -> list:
        """Chọn vùng ảnh quanh những người trong vùng chưa định danh để quét lại."""
        h_img, w_img = frame_shape[:2]
        candidates = []
        for p_idx in in_zone_indices:
            if p_idx in claimed:
                continue
            x1, y1, x2, y2 = person_boxes[p_idx]
            width = x2 - x1
            if width <= 0 or width > FACE_RESCAN_MAX_WIDTH:
                continue
            # Chỉ lấy phần thân trên, nơi có khuôn mặt, để tỉ lệ phóng to cao hơn
            pad = int(width * 0.25)
            candidates.append((width, (
                max(0, x1 - pad),
                max(0, y1 - pad),
                min(w_img, x2 + pad),
                min(h_img, y1 + int((y2 - y1) * 0.6))
            )))

        candidates.sort(key=lambda c: c[0], reverse=True)
        return [region for _w, region in candidates[:MAX_FACE_RESCANS]]

    def _remember_identity(self, track_id, person, similarity, now_mono) -> None:
        """Cộng dồn bằng chứng nhận diện cho một track."""
        if track_id is None or track_id < 0:
            return
        entry = self.track_identity.setdefault(
            track_id, {"scores": {}, "people": {}, "person": None, "last_seen": now_mono}
        )
        entry["last_seen"] = now_mono

        pid = person.get("id")
        entry["people"][pid] = person
        entry["scores"][pid] = entry["scores"].get(pid, 0.0) + max(0.1, float(similarity))

        best_pid = max(entry["scores"], key=entry["scores"].get)
        if entry["scores"][best_pid] >= IDENTITY_BIND_SCORE:
            entry["person"] = entry["people"][best_pid]

    def _recall_identity(self, track_id, now_mono) -> Optional[dict]:
        """Danh tính đã khoá cho track, nếu vẫn còn hiệu lực.

        Mỗi lần dùng lại thì gia hạn: track còn sống thì giữ danh tính, chỉ khi
        người đó rời khỏi khung hình đủ lâu ràng buộc mới hết hiệu lực.
        """
        if track_id is None or track_id < 0:
            return None
        entry = self.track_identity.get(track_id)
        if entry is None or entry.get("person") is None:
            return None
        if now_mono - entry["last_seen"] > IDENTITY_TTL_SECONDS:
            return None
        entry["last_seen"] = now_mono
        return entry["person"]

    def _prune_identities(self, now_mono) -> None:
        for tid in [t for t, e in self.track_identity.items() if now_mono - e["last_seen"] > IDENTITY_TTL_SECONDS]:
            del self.track_identity[tid]

    # ---------- pub/sub kết quả AI thô ----------

    def _publish_ai_result(self, result: dict, now) -> None:
        """Đẩy kết quả AI (box chuẩn hoá, không phải JPEG) cho client đã đăng ký
        qua ``subscribe_results`` — dành cho overlay vẽ trên WebRTC/canvas sau
        này. Không ai đăng ký thì bỏ qua ngay, khỏi tốn công dựng payload.
        """
        if not _result_subscribers.get(self.camera_id):
            return
        w, h = result["width"], result["height"]
        tracks = []
        for t in result["tracks"]:
            x1, y1, x2, y2 = t["box"]
            identity = t.get("identity")
            if t["intrusion"]:
                color, label = "#ff2d2d", "VI PHẠM AN TOÀN"
            elif t["in_zone"] and identity:
                color = "#00ff78"
                label = f"{identity.get('rank', '')} {identity.get('name', '')}".strip()
            elif t["in_zone"] and t["occluded"]:
                color, label = "#00bee6", "Bị che khuất"
            elif t["in_zone"]:
                color, label = "#14dc50", "Trong vùng"
            else:
                color, label = "#788692", "Ngoài vùng"
            tracks.append({
                "id": t.get("track_id"),
                "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                "label": label,
                "color": color,
                "identity": ({
                    "id": identity.get("id"), "name": identity.get("name"),
                    "rank": identity.get("rank"), "military_id": identity.get("military_id"),
                } if identity else None),
                "in_zone": t["in_zone"],
                "violation": t["intrusion"],
            })
        payload = {
            "camera_id": self.camera_id,
            "frame_seq": result["frame_seq"],
            "ts": int(now.timestamp() * 1000),
            "width": w,
            "height": h,
            "tracks": tracks,
            "zones_state": result.get("zones_state", {}),
            "hud": result.get("hud", {}),
        }
        _publish_result(self.camera_id, payload)

    # ---------- vòng render: vẽ + mã hoá + publish, độc lập tốc độ AI ----------

    async def _render_loop(self, reader: FrameSource) -> None:
        interval = 1.0 / max(1, STREAM_FPS)
        next_due = time.monotonic()
        while True:
            try:
                ok, frame, _seq, cap_ts = reader.read()
                if ok and frame is not None:
                    result = self.latest_result
                    try:
                        overlay_jpeg, clean_jpeg = await asyncio.to_thread(_render_pair, frame, result)
                    except Exception as e:
                        print(f"[Camera] Lỗi dựng khung luồng xem: {e}")
                        overlay_jpeg = clean_jpeg = None
                    if overlay_jpeg is not None:
                        publish_frame(self.camera_id, None, None,
                                      stream_overlay=overlay_jpeg, stream_clean=clean_jpeg,
                                      raw=frame, result=result, ts=cap_ts)
                        push_clip_frame(self.camera_id, overlay_jpeg, cap_ts)
                        self._publish_rate.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[Camera] Lỗi vòng render: {e}")

            next_due += interval
            sleep_s = next_due - time.monotonic()
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
            else:
                # Trễ nhịp (máy chậm): bắt kịp ngay từ bây giờ, không dồn cục
                next_due = time.monotonic()

    # ---------- vòng AI: suy luận + toàn bộ nghiệp vụ ----------

    async def _ai_loop(self, reader: FrameSource, on_update, monitor: AttendanceMonitor,
                       video_path: str, is_rtsp: bool) -> int:
        frame_count = 0
        last_seq = -1
        min_interval = 1.0 / max(1, self.fps)
        last_processed_mono = 0.0
        was_stale = False

        try:
            while not self._stop_requested:
                ok, frame, seq, cap_ts = reader.read()

                # Luồng mạng tự nối lại rồi (xem RTSPStreamReader) nên không còn
                # dừng hẳn xử lý như trước; chỉ báo một lần lúc mất và một lần
                # lúc phục hồi.
                if is_rtsp and reader.stale != was_stale:
                    was_stale = reader.stale
                    if was_stale:
                        await on_update({'type': 'error', 'message': 'Mất tín hiệu camera RTSP'})
                    else:
                        await on_update({'type': 'system_event', 'category': 'SYSTEM',
                                          'message': 'Đã khôi phục tín hiệu camera RTSP',
                                          'timestamp': clock.now().isoformat()})

                if not ok or frame is None:
                    if not is_rtsp and reader.ended:
                        break
                    await asyncio.sleep(0.02)
                    continue

                if seq == last_seq:
                    if not is_rtsp and reader.ended:
                        break
                    await asyncio.sleep(0.01)
                    continue

                # Giữ đúng tốc độ xử lý mong muốn (camera.target_fps) dù nguồn
                # phát nhanh hơn: lấy khung MỚI NHẤT nhưng không xử lý dày hơn
                # mức cấu hình, tránh làm sai nhịp quan sát điểm danh.
                now_check = time.monotonic()
                if now_check - last_processed_mono < min_interval:
                    await asyncio.sleep(0.005)
                    continue

                last_seq = seq
                last_processed_mono = now_check
                t0 = time.monotonic()

                # 1. Hai model chạy song song trên cùng khung hình, mỗi model một thread
                #    nên vòng lặp sự kiện không bị chặn trong lúc inference.
                detection, recognized_faces = await asyncio.gather(
                    asyncio.to_thread(self.detector.detect_persons, frame),
                    asyncio.to_thread(self.face_engine.recognize_faces_in_frame, frame),
                )
                person_boxes = detection['boxes']
                confidences = detection['confidences']
                track_ids = detection['track_ids']
                occluded_flags = detection['occluded']

                h_img, w_img = frame.shape[:2]

                # 2. Vùng đếm quân số: lấy từ cấu hình, không có thì dùng mặc định
                zones_cfg = self.zones.zones()
                np_pts, _used_fallback = _attendance_polygon_points(zones_cfg, w_img, h_img)

                in_zone_indices = set()
                for p_idx, box in enumerate(person_boxes):
                    x1, y1, x2, y2 = box
                    # Test feet (ground point) or center point against the polygon
                    feet_pt = ((x1 + x2) / 2.0, float(y2 - 2))
                    center_pt = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
                    in_feet = cv2.pointPolygonTest(np_pts, feet_pt, False) >= 0
                    in_center = cv2.pointPolygonTest(np_pts, center_pt, False) >= 0
                    if in_feet or in_center:
                        in_zone_indices.add(p_idx)

                # Sĩ số trong vùng = chỉ những người NẰM TRONG vùng đã cấu hình
                person_count = len(in_zone_indices)

                # 3. Giám sát an toàn: ai vào vùng cấm hoặc vượt vạch an toàn
                now = clock.now()
                intrusions = self.intrusion.check(person_boxes, track_ids, w_img, h_img, now,
                                                  occluded=occluded_flags)
                intrusion_indices = {idx for v in intrusions for idx in v["indices"]}

                # 4. Gắn khuôn mặt đã quét được vào người trong vùng
                now_mono = time.monotonic()
                claimed_boxes = set()
                assignments: Dict[int, dict] = {}
                matched_face_boxes = self._assign_faces(
                    recognized_faces, person_boxes, in_zone_indices, claimed_boxes, assignments
                )

                # 4b. Người trong vùng còn lại: cắt riêng vùng ảnh rồi quét lại.
                regions = self._rescan_regions(person_boxes, in_zone_indices, claimed_boxes, frame.shape)
                if regions:
                    rescanned = await asyncio.to_thread(
                        self.face_engine.recognize_in_regions, frame, regions
                    )
                    matched_face_boxes += self._assign_faces(
                        rescanned, person_boxes, in_zone_indices, claimed_boxes, assignments
                    )

                # 4c. Khoá danh tính vào track: một quân nhân đã nhận ra được thì vẫn
                #     được tính khi quay mặt đi hoặc bị che trong các khung hình sau.
                live_matches = len(assignments)
                for p_idx, info in assignments.items():
                    self._remember_identity(track_ids[p_idx], info["person"], info["similarity"], now_mono)

                person_to_face_match = {p_idx: info["person"] for p_idx, info in assignments.items()}
                claimed_person_ids = {info["person"]["id"] for info in assignments.values()}

                for p_idx in in_zone_indices:
                    if p_idx in person_to_face_match:
                        continue
                    remembered = self._recall_identity(track_ids[p_idx], now_mono)
                    if remembered is None or remembered.get("id") in claimed_person_ids:
                        continue
                    person_to_face_match[p_idx] = remembered
                    claimed_person_ids.add(remembered.get("id"))

                self._prune_identities(now_mono)

                present_personnel = []
                seen_ids = set()
                for p_idx in sorted(person_to_face_match):
                    p_info = person_to_face_match[p_idx]
                    if p_info["id"] in seen_ids:
                        continue
                    seen_ids.add(p_info["id"])
                    present_personnel.append(p_info)

                # Phiên điểm danh: mở theo thời khoá biểu (đầu giờ / cuối giờ)
                if self.attendance is not None and self.attendance.session is None:
                    self.attendance.maybe_open_scheduled(now)

                att_status = self.attendance.status(now) if self.attendance else {"active": False}

                baseline_val = monitor.baseline_count
                if baseline_val is None and att_status.get("active"):
                    baseline_val = att_status.get("required", att_status.get("roster_size", 0))
                if baseline_val is None:
                    baseline_val = 0

                hud_stat = (f"Chuẩn {baseline_val}  ·  Trong vùng {person_count}"
                            f"  ·  Định danh {len(present_personnel)}")

                roll_line = None
                if att_status.get("active"):
                    remain = int(att_status.get("remaining_seconds", 0))
                    roll_line = (f"ĐIỂM DANH {att_status.get('phase_label', '').upper()}"
                                 f"  ·  còn {remain // 60:02d}:{remain % 60:02d}"
                                 f"  ·  có mặt {att_status.get('present', 0)}/{att_status.get('required', 0)}")

                source_label = ("Nguồn: RTSP · quang học" if is_rtsp
                                else f"Nguồn: {Path(video_path).name} · quang học")

                # 5. Gói kết quả cho vòng render vẽ lên khung MỚI HƠN, và dùng
                #    ngay ở đây để ra khung bằng chứng cho sự kiện / điểm danh.
                tracks = []
                for p_idx, (box, conf) in enumerate(zip(person_boxes, confidences)):
                    tracks.append({
                        "box": tuple(int(v) for v in box),
                        "confidence": float(conf),
                        "in_zone": p_idx in in_zone_indices,
                        "occluded": bool(occluded_flags[p_idx]),
                        "intrusion": p_idx in intrusion_indices,
                        "identity": person_to_face_match.get(p_idx),
                        "track_id": track_ids[p_idx] if p_idx < len(track_ids) else None,
                    })

                result = {
                    "frame_seq": seq,
                    "ts": cap_ts,
                    "width": w_img,
                    "height": h_img,
                    "zones": zones_cfg,
                    "tracks": tracks,
                    "matched_face_boxes": [tuple(int(v) for v in b) for b in matched_face_boxes],
                    "zones_state": {
                        "person_count": person_count,
                        "baseline": baseline_val,
                        "present": len(present_personnel),
                    },
                    "hud": {
                        "camera_name": self.camera_name.upper(),
                        "time_str": now.strftime("%d/%m/%Y %H:%M:%S"),
                        "stat_line": hud_stat,
                        "roll_line": roll_line,
                        "source_label": source_label,
                    },
                }
                self.latest_result = result
                self._publish_ai_result(result, now)

                # Khung bằng chứng: vẽ MỘT LẦN ở đây (không phải 4 lần như trước)
                # cho sự kiện vi phạm + bằng chứng điểm danh.
                display_frame = draw_overlay(frame.copy(), result)

                # Vi phạm an toàn được chốt sau khi đã có khung bằng chứng, để
                # ảnh nhìn thấy đúng đối tượng bị phát hiện.
                for violation in intrusions:
                    if self.events is None:
                        break
                    zone = violation["zone"]
                    identified = []
                    boxes = []
                    for idx in violation["indices"]:
                        person = person_to_face_match.get(idx)
                        if person is None:
                            boxes.append(normalize_box(person_boxes[idx], w_img, h_img,
                                                       confidences[idx], "Không xác định"))
                            continue
                        name = f"{person.get('rank', '')} {person.get('name', '')}".strip()
                        identified.append({"person_id": person.get("id"), "person_name": name})
                        boxes.append(normalize_box(person_boxes[idx], w_img, h_img,
                                                   confidences[idx], name))
                    # Giữ lại đoạn đệm dẫn tới lúc vi phạm để chỉ huy xem lại
                    clip_id = keep_clip(self.camera_id,
                                        f"clip_{int(time.time())}_{zone.get('id', 'zone')}")

                    # Đẩy cảnh báo đi trước rồi mới ghi ảnh/clip: cảnh báo đỏ phải
                    # hiện ngay, đừng đợi ghi đĩa xong. emit_async ghi ảnh + JSON ở
                    # luồng khác, chỉ phần đẩy SSE chạy ngay trên vòng lặp này.
                    event = await self.events.emit_async(
                        TYPE_INTRUSION,
                        f"Phát hiện {len(boxes):02d} đối tượng "
                        f"{'vượt' if zone.get('rule') == RULE_CROSSING else 'đi vào'} "
                        f"{zone.get('name', 'vùng cấm')}.",
                        severity="critical",
                        frame=display_frame,
                        boxes=boxes,
                        camera_id=self.camera_id,
                        camera_name=self.camera_name,
                        zone_id=zone.get("id"),
                        clip_id=clip_id,
                        detail={
                            "zone_name": zone.get("name", ""),
                            "zone_rule": zone.get("rule"),
                            "object_count": len(boxes),
                            "object_class": "person",
                            "identified": identified,
                            "dwell_seconds": violation["dwell_seconds"],
                        },
                    )
                    await on_update({"type": "event", "event": event})
                    self._archive_clip_bg(event, clip_id)

                # 6. Ghi nhận vào phiên điểm danh và chốt biên bản khi hết cửa sổ.
                #    Khung hình dùng làm bằng chứng là khung đã vẽ đầy đủ HUD.
                if self.attendance is not None:
                    # Ghi dấu vết hiện diện trên MỌI khung hình, không chỉ trong hai
                    # cửa sổ điểm danh — có vậy mới suy được ai đi chậm, ai về sớm.
                    quality = live_matches * 100 + len(present_personnel)
                    self.attendance.observe(
                        [p["id"] for p in present_personnel],
                        now,
                        frame=display_frame,
                        quality=quality,
                        frame_seq=seq,
                    )
                    closed_log = self.attendance.close_if_due(now)
                    if closed_log:
                        await on_update({'type': 'attendance_complete', 'log': closed_log})

                frame_count += 1
                infer_ms = (time.monotonic() - t0) * 1000
                self._infer_ms.append(infer_ms)
                self._ai_rate.tick()

                # Gửi số liệu qua WebSocket cho client cũ (nếu còn ai mở). Không
                # còn kèm ảnh base64 — đó là một trong các lý do chặn vòng lặp.
                await on_update({
                    'type': 'frame_update',
                    'camera_id': self.camera_id,
                    'count': person_count,
                    'baseline': baseline_val,
                    'recognized_count': len(present_personnel),
                    'unidentified_count': max(0, person_count - len(present_personnel)),
                    'present_personnel': present_personnel,
                    'attendance': att_status,
                    'boxes': person_boxes,
                    'frame_number': frame_count,
                    'server_time': now.isoformat(),
                    'infer_ms': round(infer_ms, 1),
                    'is_processing': True
                })

                # Check for baseline drop alerts. update_count tự lưu ảnh + JSON
                # xuống đĩa khi có cảnh báo; đẩy hẳn việc gọi nó sang luồng khác
                # để việc ghi đĩa không chặn vòng xử lý khung kế tiếp.
                alert_result = await asyncio.to_thread(monitor.update_count, person_count, display_frame)
                if alert_result['alert_triggered']:
                    event_clip_id = keep_clip(self.camera_id, f"clip_{int(time.time())}")
                    alert_data = alert_result['alert_data']

                    if self.events is not None:
                        baseline = alert_data.get('baseline') or 0
                        event = await self.events.emit_async(
                            TYPE_ABSENT,
                            f"Thiếu {max(0, baseline - person_count):02d} quân nhân so với "
                            f"sĩ số chuẩn ({person_count}/{baseline}).",
                            severity="warning",
                            frame=display_frame,
                            camera_id=self.camera_id,
                            camera_name=self.camera_name,
                            clip_id=event_clip_id,
                            detail={
                                "current_count": person_count,
                                "required_count": baseline,
                                "missing_count": max(0, baseline - person_count),
                                "duration_seconds": monitor.ALERT_THRESHOLD_SECONDS,
                            },
                        )
                        await on_update({"type": "event", "event": event})
                        self._archive_clip_bg(event, event_clip_id)

                    await on_update({
                        'type': 'alert',
                        'category': 'ABSENT',
                        'clip_id': event_clip_id,
                        **alert_data
                    })
                elif alert_result.get('recovered'):
                    await on_update({
                        'type': 'system_event',
                        'category': 'SYSTEM',
                        'message': f'Quân số đã đủ trở lại ({person_count} quân nhân)',
                        'timestamp': now.isoformat()
                    })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Error processing video: {e}")
            await on_update({
                'type': 'error',
                'message': f'Lỗi luồng xử lý video: {str(e)}'
            })

        return frame_count

    # ---------- vòng xử lý ----------

    async def process_video(
        self,
        video_path: str,
        on_update,
        monitor: AttendanceMonitor
    ) -> None:
        """Chạy một camera cho tới khi hết nguồn hoặc bị yêu cầu dừng.

        Tách thành hai task song song (AI + render) — xem docstring đầu file.
        """
        is_rtsp = "://" in video_path

        self.detector.reset()
        self.track_identity.clear()
        self.latest_result = None
        self._bg_tasks = set()

        reader: FrameSource = RTSPStreamReader(video_path) if is_rtsp else FileStreamReader(video_path)
        if is_rtsp:
            print(f"[Camera] Kết nối luồng mạng: {mask_credentials(video_path)}")
        else:
            print(f"Opening video file: {video_path}")
        reader.start()
        self.reader = reader

        if not reader.started:
            await on_update({
                'type': 'error',
                'message': reader.error or (
                    'Không thể kết nối tới luồng camera RTSP' if is_rtsp else 'Không thể mở tệp video')
            })
            self.reader = None
            return

        frame_count = 0
        ai_task = asyncio.create_task(self._ai_loop(reader, on_update, monitor, video_path, is_rtsp))
        render_task = asyncio.create_task(self._render_loop(reader))
        tasks = {ai_task, render_task}

        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t is not ai_task and t.exception():
                    print(f"[Camera] Vòng render dừng ngoài ý muốn: {t.exception()}")
            if ai_task in done and not ai_task.cancelled():
                frame_count = ai_task.result()
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            reader.stop()
            self.reader = None
            clear_frames(self.camera_id)

            if self.attendance is not None and self.attendance.session is not None:
                if self._stop_requested:
                    # Người dùng bấm dừng giữa phiên -> huỷ, không chốt biên bản vắng giả
                    self.attendance.cancel_session()
                else:
                    closed_log = self.attendance.close_if_due(clock.now(), force=True)
                    if closed_log:
                        await on_update({'type': 'attendance_complete', 'log': closed_log})

            await on_update({
                'type': 'processing_complete',
                'total_frames': frame_count,
                'is_processing': False
            })
