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
from app.detector import PersonDetector, _iou
from app.overlay import draw_label, measure_label
from app.monitor import AttendanceMonitor
from app.face_engine import FaceEngine
from app.attendance import AttendanceManager, person_label
from app.events import CAMERA_ID, EventStore, TYPE_ABSENT, TYPE_INTRUSION, normalize_box
from app.safety import RULE_ATTENDANCE, RULE_CROSSING, RULE_RESTRICTED, IntrusionDetector, ZoneStore

# scipy không phải dependency bắt buộc của repo gốc (tới dù vậy nó đi kèm sẵn
# qua ultralytics/opencv/insightface trong uv.lock); vẫn phòng hờ máy nào thiếu
# thì rơi về gán tham lam theo điểm, không chết tiến trình.
try:
    from scipy.optimize import linear_sum_assignment
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# Nhịp publish khung hình cho luồng xem, ĐỘC LẬP với tốc độ AI (camera có thể
# cấu hình target_fps khác, chậm hơn nhiều nếu CPU yếu).
from app import hw

_HW = hw.settings()
STREAM_FPS = _HW.stream_fps

# Mỗi camera có kho riêng: khung hình mới nhất và bộ đệm clip phát lại. Trước
# đây tất cả dùng chung một biến toàn cục nên chỉ chạy được một camera; hai
# camera cùng chạy thì camera sau ghi đè khung hình của camera trước.
_camera_state: Dict[str, dict] = {}

# Giữ khoảng bấy nhiêu giây khung hình cho đoạn phát lại, theo đúng nhịp publish
# thực tế (trước đây cố định 60 khung ở 5fps ~ 12s; giờ fps publish có thể khác).
# Phải đủ PRE + POST + biên an toàn (xem CLIP_PRE_S/CLIP_POST_S dưới) để lúc chốt
# clip không bị thiếu khung ở một trong hai đầu.
CLIP_BUFFER_SECONDS = 12

# Clip sự kiện lấy CLIP_PRE_S giây TRƯỚC + CLIP_POST_S giây SAU thời điểm vi
# phạm (t0) — trước đây chỉ chốt đệm trước (snapshot ngay lúc phát hiện) nên
# không bao giờ thấy được diễn biến SAU lúc báo động.
CLIP_PRE_S = float(os.environ.get("CLIP_PRE_S", "5"))
CLIP_POST_S = float(os.environ.get("CLIP_POST_S", "5"))

# Vi phạm ở MỘT (camera, vùng cấm) gộp vào một "biên bản" sự kiện còn MỞ suốt
# thời gian vùng còn người, chỉ đóng sau khi vùng trống liên tục đủ bấy nhiêu
# giây — trước đây mỗi lần tracker đổi ID (mất dấu rồi bắt lại) lại bắn một sự
# kiện MỚI dồn dập, và tên định danh ra SAU khi sự kiện đã chốt thì event cũ
# mãi mãi ghi "Không xác định" (xem khối xử lý vi phạm trong ``_ai_loop``).
INTRUSION_INCIDENT_GRACE_S = float(os.environ.get("INTRUSION_INCIDENT_GRACE_S", "15"))

# Hầu hết người vi phạm KHÔNG được định danh trong lúc đang ở trong vùng (quay
# mặt đi, đứng xa, góc xấu) mà chỉ lúc ĐÃ RỜI vùng, đi ngang camera ở góc đẹp
# hơn — trước đây chỉ ghi tên nếu định danh được NGAY LÚC còn ở trong vùng nên
# ~41/142 biên bản (tester báo) mãi ghi "Không xác định". Giờ biên bản nhớ các
# track đã từng vào vùng; sau khi biên bản ĐÃ ĐÓNG (hết GRACE), vẫn tiếp tục
# "theo dõi đặt tên" cho các track đó thêm bấy nhiêu giây nữa, miễn track còn
# sống (xem ``_sync_incident_names``, ``_close_stale_incidents``).
INTRUSION_NAMING_TAIL_S = float(os.environ.get("INTRUSION_NAMING_TAIL_S", "120"))

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


def keep_clip(camera_id: str, clip_id: str, t0_mono: Optional[float] = None) -> str:
    """Chốt lại đoạn đệm của camera thành ``clip_id`` và dọn bớt các đoạn cũ.

    ``t0_mono`` (tuỳ chọn): mốc monotonic của sự kiện — có thì CHỈ lấy khung có
    ``ts`` trong khoảng ``[t0-CLIP_PRE_S, t0+CLIP_POST_S]`` (clip CÓ CẢ trước và
    sau thời điểm vi phạm). Không truyền thì lấy TOÀN BỘ đệm hiện có, như hành
    vi cũ — dùng khi clip không gắn với một mốc cụ thể (ví dụ test gọi trực
    tiếp). Gọi hàm này SỚM (ngay lúc phát hiện) thì chỉ có nửa TRƯỚC vì nửa SAU
    chưa kịp ghi; vòng AI giờ gọi qua ``VideoProcessor._schedule_event_clip``,
    đợi đủ ``CLIP_POST_S`` giây rồi mới gọi vào đây.
    """
    clips = _state(camera_id)["clips"]
    if t0_mono is None:
        frames = [frame for frame, _ts in clips]
    else:
        lo, hi = t0_mono - CLIP_PRE_S, t0_mono + CLIP_POST_S
        frames = [frame for frame, ts in clips if lo <= ts <= hi]
    latest_event_clips[clip_id] = frames
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


# ---------- gating theo động, dùng để hạ nhịp AI lúc cảnh tĩnh ----------

class MotionGate:
    """Phát hiện có đang động trong một vùng ảnh hay không, rẻ hơn hẳn chạy
    YOLO chỉ để biết "có gì đổi không": downscale ảnh xám về bề ngang cố định
    rồi so khác biệt với trung bình trượt (EMA) của nền.
    """

    DOWNSCALE_WIDTH = 160
    # EMA thấp -> nền thích nghi chậm với nhiễu tức thời (không bị một khung có
    # động kéo nền lệch ngay), nhưng vẫn theo kịp thay đổi ánh sáng chậm (mây
    # che nắng) để không báo "động" giả kéo dài.
    EMA_ALPHA = 0.05

    def __init__(self, threshold: float = 12.0):
        self.threshold = threshold
        self._avg: Optional[np.ndarray] = None

    def update(self, region: Optional[np.ndarray]) -> bool:
        """Đưa vùng ảnh MỚI NHẤT vào, trả ``True`` nếu phát hiện có động."""
        if region is None or region.size == 0:
            return True  # Vùng rỗng (ROI tính sai) -> an toàn là coi như có động

        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
        h, w = gray.shape[:2]
        scale = self.DOWNSCALE_WIDTH / max(1, w)
        small = cv2.resize(gray, (self.DOWNSCALE_WIDTH, max(1, round(h * scale))),
                           interpolation=cv2.INTER_AREA).astype(np.float32)

        if self._avg is None or self._avg.shape != small.shape:
            self._avg = small.copy()
            return True  # Chưa có nền để so (khung đầu/ROI đổi cỡ) -> coi như có động

        motion = float(np.mean(cv2.absdiff(small, self._avg))) > self.threshold
        cv2.accumulateWeighted(small, self._avg, self.EMA_ALPHA)
        return motion

    def reset(self) -> None:
        """Bỏ nền cũ — gọi khi bắt đầu luồng mới hoặc ROI đổi hẳn vùng."""
        self._avg = None


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
# Một người KHÁC phải khớp cùng track đủ ngần này lượt, với điểm trung bình cao
# hơn người đang khoá, mới được THU HỒI và gán lại — chống sửa nhầm chỉ vì một
# lượt nhận nhầm ngẫu nhiên, nhưng vẫn sửa được lỗi khoá sai lúc mới vào khung.
IDENTITY_REVOKE_HITS = int(os.environ.get("IDENTITY_REVOKE_HITS", "3"))
# ---- Gộp embedding theo track (FACE_POOL_*) ----
# Mặt CCTV nhỏ/mờ cho embedding từng khung rất nhiễu: một khung lẻ hiếm khi
# vượt ngưỡng dù đúng người. Cộng dồn (có trọng số chất lượng) embedding của
# CÙNG một track qua nhiều khung rồi so bản "gộp" — nhiễu ngẫu nhiên triệt tiêu
# nhau, phần đặc trưng của người đó cộng dồn lại. Bản gộp ổn định hơn nên dùng
# ngưỡng thấp hơn một chút, nhưng đòi khoảng cách tới người nhì lớn hơn.
FACE_POOL_MIN_SAMPLES = int(os.environ.get("FACE_POOL_MIN_SAMPLES", "4"))
FACE_POOL_THRESHOLD = float(os.environ.get("FACE_POOL_THRESHOLD", "0.36"))
FACE_POOL_MARGIN = float(os.environ.get("FACE_POOL_MARGIN", "0.08"))
# Mặt nhỏ hơn ngần này (px) bỏ qua khi gộp — dưới mức đó embedding gần như ngẫu nhiên
FACE_POOL_MIN_WIDTH = int(os.environ.get("FACE_POOL_MIN_WIDTH", "12"))
# ---- Tự học mẫu từ camera (FACE_AUTO_ENROLL) ----
# Track ĐÃ khoá danh tính, khung hiện tại lại khớp rất chắc (điểm cao + cách xa
# người nhì) -> lưu embedding khung đó làm mẫu "auto" cho quân nhân, tối đa 1
# mẫu / người / FACE_AUTO_ENROLL_INTERVAL_S. Mẫu auto bị giới hạn số lượng
# riêng (face_engine.FACE_MAX_AUTO_SAMPLES) và xoá được trong trang Face ID.
FACE_AUTO_ENROLL = os.environ.get("FACE_AUTO_ENROLL", "1").strip().lower() not in ("0", "false", "")
FACE_AUTO_ENROLL_SIM = float(os.environ.get("FACE_AUTO_ENROLL_SIM", "0.55"))
FACE_AUTO_ENROLL_MARGIN = float(os.environ.get("FACE_AUTO_ENROLL_MARGIN", "0.15"))
FACE_AUTO_ENROLL_MIN_WIDTH = int(os.environ.get("FACE_AUTO_ENROLL_MIN_WIDTH", "24"))
FACE_AUTO_ENROLL_INTERVAL_S = float(os.environ.get("FACE_AUTO_ENROLL_INTERVAL_S", "300"))
# Số pixel tối thiểu để lưu mặt chưa xác định cho cán bộ gán tay
UNKNOWN_FACE_MIN_WIDTH = int(os.environ.get("UNKNOWN_FACE_MIN_WIDTH", "16"))
# Số vùng người được quét lại khuôn mặt ở độ phân giải cao mỗi khung hình;
# CPU yếu thì ít lần hơn, lấy từ app.hw theo phần cứng
MAX_FACE_RESCANS = _HW.max_face_rescans
# Mỗi track chưa định danh được quét lại mặt tối đa 1 lần / khoảng này (giây)
FACE_RESCAN_COOLDOWN_S = float(os.environ.get("FACE_RESCAN_COOLDOWN_S", "1.0"))
# Chỉ quét lại những người có bề ngang box nhỏ hơn ngần này (mặt bị thu nhỏ khi
# quét cả khung hình nên hay bị bỏ sót)
FACE_RESCAN_MAX_WIDTH = 300

# ---- Thu khuôn mặt chủ động qua API (POST /api/faces/capture) ----
# Cán bộ chủ động đứng trước camera bấm chụp — đòi mặt rõ hơn hẳn ngưỡng gộp
# nền (FACE_POOL_MIN_WIDTH=12) để mẫu dùng đăng ký luôn chắc ngay từ đầu.
FACE_CAPTURE_MIN_WIDTH = int(os.environ.get("FACE_CAPTURE_MIN_WIDTH", "24"))
# Số mẫu tối đa trả về cho một lượt chụp (xem _pick_diverse_samples)
FACE_CAPTURE_MAX_SAMPLES = 10


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() not in ("0", "false", "")


# ---------------------------------------------------------- ROI cho YOLO
# Khung camera góc rộng mà vùng quan tâm chỉ chiếm một phần: chạy YOLO trên cả
# khung ở imgsz cố định làm mỗi người chỉ còn vài chục pixel, dễ bị bỏ sót (đo
# thực tế: YOLO11s@640 cả khung chỉ bắt được ~65% người so với @1280 — xem mục
# benchmark). Cắt về đúng vùng quan tâm (nới 10%) rồi mới resize về imgsz thì độ
# phân giải hiệu dụng trên mỗi người cao hơn hẳn, không cần tăng imgsz (tốn CPU
# hơn nhiều, tăng tuyến tính theo imgsz^2).
AI_ROI = _env_bool("AI_ROI", True)
AI_ROI_PAD_FRAC = float(os.environ.get("AI_ROI_PAD_FRAC", "0.10"))
# ROI chiếm quá tỉ lệ này diện tích khung thì cắt chẳng còn lợi gì (gần hết
# khung), dùng cả khung cho đơn giản.
AI_ROI_MAX_AREA_FRAC = float(os.environ.get("AI_ROI_MAX_AREA_FRAC", "0.70"))
# Ghép 2 ô có chồng lấn + NMS khi ROI dài hơn hẳn imgsz trên CPU — CHƯA triển
# khai trong bản này (ưu tiên thời gian cho ROI/motion-gating/identity, như kế
# hoạch cho phép bỏ qua nếu không kịp). Cờ vẫn đọc được, giữ chỗ cho sau; bật
# lên hiện tại không có tác dụng gì khác AI_ROI thường, chỉ in cảnh báo một lần.
AI_TILING = _env_bool("AI_TILING", False)
if AI_TILING:
    print("[Camera] AI_TILING=1 nhưng tính năng ghép ô (tiling) chưa triển khai trong bản "
          "này; chạy như ROI nguyên khối (AI_TILING không có tác dụng).")

# ---------------------------------------------------------- motion gating
# Cảnh tĩnh (sân bãi vắng người, không ai di chuyển) chiếm phần lớn thời gian
# trong ngày — chạy YOLO+Face full nhịp lúc đó chỉ phí CPU. Hạ xuống AI_IDLE_FPS
# khi không có động VÀ không có track sống; trở lại ai_fps đầy đủ ngay khi có
# động, có track, đang điểm danh, hoặc có người trong vùng cấm.
AI_MOTION_GATING = _env_bool("AI_MOTION_GATING", True)
AI_IDLE_FPS = int(os.environ.get("AI_IDLE_FPS", "1"))


def _box_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _bbox_center(bbox) -> Tuple[float, float]:
    """Tâm của một bounding box, dạng float cho cv2.pointPolygonTest."""
    x1, y1, x2, y2 = bbox
    return ((float(x1) + float(x2)) / 2.0, (float(y1) + float(y2)) / 2.0)


def _pick_diverse_samples(samples: list, k: int) -> list:
    """Chọn tối đa ``k`` mẫu ``(quality, emb, jpeg, width)`` CÀNG KHÁC NHAU CÀNG
    TỐT, dùng cho ``VideoProcessor.capture_faces`` (xem POST /api/faces/capture).

    Giữ mẫu chất lượng cao nhất trước, rồi tham lam chọn tiếp mẫu có khoảng
    cách embedding (1 - cos) XA NHẤT so với các mẫu đã chọn — tránh 10 ảnh gần
    như giống nhau (đứng yên nhìn thẳng một hướng) mà bỏ lỡ góc khác, vốn giúp
    đăng ký/nhận diện chắc hơn hẳn chỉ một góc.
    """
    if not samples:
        return []
    ordered = sorted(samples, key=lambda s: -s[0])
    if len(ordered) <= k:
        return ordered
    chosen = [ordered[0]]
    remaining = ordered[1:]
    while len(chosen) < k and remaining:
        best_i, best_dist = 0, -1.0
        for i, cand in enumerate(remaining):
            dist = min(1.0 - float(cand[1] @ c[1]) for c in chosen)
            if dist > best_dist:
                best_i, best_dist = i, dist
        chosen.append(remaining.pop(best_i))
    return chosen


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
        self._stop_event = threading.Event()

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
        # Đã bị dừng trong lúc chờ backoff thì không mở kết nối mới: không ai
        # còn giữ reader để release, camera giá rẻ lại giới hạn số phiên RTSP
        if self._stop_flag:
            return
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
                    # Chờ được ngắt ngay khi stop(), không ngủ hết backoff (tới 30 s)
                    if self._stop_event.wait(backoff):
                        break
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
        # Luồng đọc tự release capture của nó khi thoát, kể cả khi stop() đã
        # hết thời gian chờ join — tránh rò kết nối RTSP
        try:
            self.cap.release()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_flag = True
        self._stop_event.set()
        self.started = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.thread is None or not self.thread.is_alive():
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


def _zones_signature(zones_cfg) -> tuple:
    """Khoá rẻ để biết cấu hình vùng có đổi không, khỏi tính lại ROI mỗi khung
    hình — ROI phải ĐỨNG YÊN qua các khung liên tiếp để tracker (ByteTrack) ổn
    định, chỉ tính lại khi thật sự có người chỉnh vùng."""
    return tuple(
        (z.get("id"), tuple((p.get("x"), p.get("y")) for p in z.get("points", [])))
        for z in zones_cfg
    )


def _zones_bounding_box(zones_cfg, w_img: int, h_img: int) -> Optional[Tuple[float, float, float, float]]:
    """Hộp bao (pixel) mọi điểm của mọi vùng đã cấu hình. ``None`` nếu chưa có vùng nào."""
    xs, ys = [], []
    for zone in zones_cfg:
        for p in zone.get("points", []):
            xs.append(p["x"] * w_img)
            ys.append(p["y"] * h_img)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def compute_roi(
    zones_cfg, w_img: int, h_img: int,
    pad_frac: float = AI_ROI_PAD_FRAC, max_area_frac: float = AI_ROI_MAX_AREA_FRAC,
) -> Optional[Tuple[int, int, int, int]]:
    """ROI để chạy YOLO: hộp bao mọi vùng (điểm danh + an toàn) đã cấu hình của
    camera, nới thêm ``pad_frac`` mỗi phía (người ở ngay mép vùng vẫn cần thấy
    đủ thân để tracker không bị cắt box).

    Trả ``None`` (nghĩa là: dùng cả khung) khi không có vùng nào, hoặc ROI sau
    khi nới đã chiếm hơn ``max_area_frac`` diện tích khung — cắt chẳng còn lợi
    gì so với công quản lý thêm một bước toạ độ.
    """
    box = _zones_bounding_box(zones_cfg, w_img, h_img)
    if box is None:
        return None
    bx1, by1, bx2, by2 = box
    pad_x = (bx2 - bx1) * pad_frac
    pad_y = (by2 - by1) * pad_frac
    x1 = max(0, int(bx1 - pad_x))
    y1 = max(0, int(by1 - pad_y))
    x2 = min(w_img, int(bx2 + pad_x) + 1)
    y2 = min(h_img, int(by2 + pad_y) + 1)
    if x2 <= x1 or y2 <= y1:
        return None
    if (x2 - x1) * (y2 - y1) > max_area_frac * w_img * h_img:
        return None
    return x1, y1, x2, y2


def _dedupe_zone_count(in_zone_indices, track_ids, occluded_flags, person_boxes) -> int:
    """Sĩ số trong vùng, đếm theo TRACK DUY NHẤT: track vẫn còn đó nhưng xuất
    hiện 2 box (vd. box "ma" giữ qua che khuất của chính track đó, hoặc tracker
    gán lại id mới cho người đang di chuyển qua chỗ che) không được đếm đôi. Box
    chưa có track id thật (id âm, tracker chưa gán kịp) thì không có gì để gộp
    -> đếm riêng từng cái như hành vi cũ.
    """
    seen_tids = set()
    count = 0
    for p_idx in in_zone_indices:
        tid = track_ids[p_idx] if p_idx < len(track_ids) else None
        if tid is None or tid < 0:
            count += 1
            continue
        if occluded_flags[p_idx] and any(
            j != p_idx and j in in_zone_indices and not occluded_flags[j]
            and _iou(person_boxes[p_idx], person_boxes[j]) > 0
            for j in in_zone_indices
        ):
            continue  # box "ma" đè lên box đang thấy thật -> không đếm đôi
        if tid in seen_tids:
            continue
        seen_tids.add(tid)
        count += 1
    return count


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


def _place_zone_label(frame, text: str, pts, obstacles, font_size: int = 14, stripe_color=None) -> None:
    """Đặt nhãn khoanh vùng ở GÓC TRÊN-TRÁI của đa giác; nếu đè lên người/nhãn
    người (``obstacles``: danh sách box ``(x1, y1, x2, y2)``) thì thử vị trí
    khác (ra NGOÀI phía trên đa giác, rồi LÙI VÀO TRONG) trước khi đành chịu vẽ
    đè (tester phản hồi nhãn "VÙNG CẤM · ..." chồng lên nhãn người khi ai đó
    đứng gần đỉnh vùng).
    """
    box_w, box_h = measure_label(text, font_size)
    # Đỉnh trên-trái: y nhỏ nhất, cùng y thì x nhỏ nhất
    vx, vy = min(((int(p[0]), int(p[1])) for p in pts), key=lambda p: (p[1], p[0]))

    def overlaps(x: int, y: int, anchor_bottom: bool) -> bool:
        top = y - box_h if anchor_bottom else y
        bottom = y if anchor_bottom else y + box_h
        for ox1, oy1, ox2, oy2 in obstacles:
            # Nới khung người lên ~34px để tính luôn phần nhãn người thường vẽ
            # PHÍA TRÊN box (anchor="bottom" ở vòng vẽ track dưới đây)
            if x < ox2 and x + box_w > ox1 and top < oy2 and bottom > oy1 - 34:
                return True
        return False

    candidates = [
        (vx, vy, True),             # ra ngoài, ngay trên đỉnh trên-trái
        (vx + 10, vy + 10, False),  # lùi vào trong đa giác một chút
        (int(pts[:, 0].mean()), int(pts[:, 1].min()), True),  # giữa cạnh trên (chỗ cũ)
    ]
    chosen = next(((x, y, bottom) for x, y, bottom in candidates if not overlaps(x, y, bottom)),
                  candidates[0])
    x, y, anchor_bottom = chosen
    draw_label(frame, text, (x, y), font_size=font_size, stripe_color=stripe_color,
               anchor="bottom" if anchor_bottom else "top")


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
    # Khung người đã có sẵn trong result (không phụ thuộc thứ tự vẽ) -> dùng để
    # tránh chồng nhãn vùng cấm lên nhãn người (xem _place_zone_label).
    person_boxes_for_labels = [t["box"] for t in result.get("tracks", [])]

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
            _place_zone_label(frame, f"VÙNG CẤM · {zone.get('name', '')}", pts,
                              person_boxes_for_labels, font_size=14, stripe_color=(0, 40, 160))

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
            vio_label = "VI PHẠM AN TOÀN"
            if identity is not None:
                who = person_label(identity)
                vio_label = f"VI PHẠM: {who}" if who and who != "?" else vio_label
            draw_label(frame, vio_label, (x1, y1), font_size=15,
                       stripe_color=(0, 0, 200), anchor="bottom")
        elif is_in_zone:
            if identity is not None:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 120), 2)
                rank_str = identity.get('rank', '')
                name_str = identity.get('name', '')
                mid_str = identity.get('military_id', '')
                id_label = f"[{rank_str}] {name_str} ({mid_str})" if rank_str else f"{name_str} ({mid_str})"
                draw_label(frame, id_label, (x1, y1), font_size=15, stripe_color=(0, 180, 70), anchor="bottom")
            elif is_occluded:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 190, 230), 1)
                draw_label(frame, "Bị che khuất", (x1, y1), font_size=14,
                           stripe_color=(0, 120, 150), anchor="bottom")
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 220, 80), 2)
                draw_label(frame, f"Trong vùng {int(conf * 100)}%", (x1, y1), font_size=14,
                           stripe_color=(10, 140, 50), anchor="bottom")
        else:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (120, 130, 140), 1)
            draw_label(frame, "Ngoài vùng", (x1, y1), font_size=13,
                       stripe_color=(60, 65, 75), anchor="bottom")

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
        # track_id -> mốc monotonic lần cuối quét lại mặt (xem _rescan_regions)
        self._last_rescan: Dict[int, float] = {}
        # track_id -> {"sum": vec, "wsum", "n", "top": [(q, emb, jpeg, width)], "last_seen"}
        # (gộp embedding theo track, xem FACE_POOL_*)
        self._track_faces: Dict[int, dict] = {}
        # person_id -> mốc monotonic lần cuối tự học mẫu (FACE_AUTO_ENROLL)
        self._last_auto_enroll: Dict[str, float] = {}
        # zone_id -> biên bản vi phạm vùng cấm còn MỞ: {"event_id", "zone",
        # "identified_names" (set), "has_unidentified", "max_count", "person_id",
        # "person_name", "track_ids" (set các track đã từng vào vùng),
        # "last_inside_mono"} — xem INTRUSION_INCIDENT_GRACE_S và khối xử lý vi
        # phạm trong ``_ai_loop``.
        self._open_incidents: Dict[str, dict] = {}
        # event_id -> biên bản ĐÃ ĐÓNG còn "theo dõi đặt tên" thêm
        # INTRUSION_NAMING_TAIL_S giây: {"track_ids", "identified_names",
        # "has_unidentified", "max_count", "person_id", "person_name", "zone",
        # "deadline_mono"} — xem ``_sync_incident_names``/``_close_stale_incidents``.
        self._naming_watch: Dict[str, dict] = {}
        # Phiên thu khuôn mặt chủ động đang mở (POST /api/faces/capture): mỗi
        # phiên {"per_track": {track_id: [(quality, emb, jpeg, width), ...]}} —
        # xem ``capture_faces``/``_feed_capture_sessions``.
        self._capture_sessions: List[dict] = []

        # Trạng thái chia sẻ giữa AI task và render task
        self.reader: Optional[FrameSource] = None
        self.latest_result: Optional[dict] = None
        # Tham chiếu các task nền (dựng clip) để không bị garbage-collect giữa chừng
        self._bg_tasks: set = set()
        self._ai_rate = RateCounter()
        self._publish_rate = RateCounter()
        self._infer_ms: deque = deque(maxlen=50)

        # ROI: nhớ lại ROI đã tính theo cấu hình vùng + cỡ khung hiện tại, khỏi
        # tính lại mỗi khung hình (và khỏi làm ROI nhảy linh tinh làm rối tracker).
        self._roi_cache_key = None
        self._roi_box: Optional[Tuple[int, int, int, int]] = None
        # Motion gating: nhịp AI hiện tại ("idle"/"active") + có động hay không,
        # lộ ra qua get_metrics() cho /api/v1/system/health.
        self._motion_gate = MotionGate()
        self._last_motion = True
        self._ai_mode = "active"

    def stop(self) -> None:
        """Request the processing pipeline to stop."""
        self._stop_requested = True

    def _get_roi(self, zones_cfg, w_img: int, h_img: int) -> Optional[Tuple[int, int, int, int]]:
        """ROI cố định của camera này; chỉ tính lại khi vùng hoặc cỡ khung đổi
        (xem ``compute_roi``) — tracker cần ROI đứng yên qua các khung liên tiếp."""
        key = (_zones_signature(zones_cfg), w_img, h_img)
        if key != self._roi_cache_key:
            self._roi_cache_key = key
            self._roi_box = compute_roi(zones_cfg, w_img, h_img)
        return self._roi_box

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
            # Motion gating (AI_MOTION_GATING): có đang động trong ROI không, và
            # nhịp AI hiện tại đang chạy đầy ("active") hay rảnh ("idle").
            "motion": bool(self._last_motion),
            "ai_mode": self._ai_mode,
        }

    # ---------- nhận diện ----------

    @staticmethod
    def _assign_faces(faces, person_boxes, in_zone_indices, claimed, assignments) -> list:
        """Gắn mỗi khuôn mặt nhận ra được vào một người đang đứng trong vùng.

        Gán vào box người TRONG VÙNG nhỏ nhất có chứa tâm khuôn mặt; mỗi box chỉ
        nhận một người. Không dùng polygon để lọc mặt: polygon là vùng mặt đất nên
        tâm khuôn mặt luôn nằm phía trên nó. Mặt không gắn được vào ai trong vùng
        thì không tính điểm danh.

        Gán TOÀN CỤC (``linear_sum_assignment``) thay vì tham lam theo thứ tự mặt:
        thứ tự mặt trả về từ InsightFace không có ý nghĩa gì, gán tham lam theo
        thứ tự đó có thể cướp mất box đúng của một mặt xử lý sau. Thiếu scipy thì
        rơi về tham lam nhưng xếp theo độ tương đồng giảm dần (ít nhất ưu tiên
        mặt chắc chắn nhất được ghép trước), không chết tính năng vì thiếu dependency.
        """
        candidates = [rf for rf in faces if rf.get("is_recognized") and rf.get("person")]
        people_idx = [p_idx for p_idx in in_zone_indices if p_idx not in claimed]
        if not candidates or not people_idx:
            return []

        # cost[f, p] = diện tích box p (ưu tiên box nhỏ nhất bọc mặt, như logic
        # cũ); NO_MATCH = tâm mặt không nằm trong (biên độ) box đó -> cấm ghép.
        NO_MATCH = 1e9
        cost = np.full((len(candidates), len(people_idx)), NO_MATCH, dtype=np.float64)
        for fi, rf in enumerate(candidates):
            fcx, fcy = _bbox_center(rf["bbox"])
            for pi, p_idx in enumerate(people_idx):
                px1, py1, px2, py2 = person_boxes[p_idx]
                if px1 - 20 <= fcx <= px2 + 20 and py1 - 20 <= fcy <= py2 + 40:
                    cost[fi, pi] = max(1.0, (px2 - px1) * (py2 - py1))

        if _HAS_SCIPY:
            rows, cols = linear_sum_assignment(cost)
            pairs = [(r, c) for r, c in zip(rows, cols) if cost[r, c] < NO_MATCH]
        else:
            order = sorted(range(len(candidates)), key=lambda i: -candidates[i].get("similarity", 0.0))
            used_cols = set()
            pairs = []
            for fi in order:
                best_pi, best_cost = None, NO_MATCH
                for pi in range(len(people_idx)):
                    if pi not in used_cols and cost[fi, pi] < best_cost:
                        best_cost, best_pi = cost[fi, pi], pi
                if best_pi is not None:
                    used_cols.add(best_pi)
                    pairs.append((fi, best_pi))

        matched_boxes = []
        for fi, pi in pairs:
            rf = candidates[fi]
            p_idx = people_idx[pi]
            claimed.add(p_idx)
            assignments[p_idx] = {"person": rf["person"], "similarity": rf.get("similarity", 0.0), "face": rf}
            matched_boxes.append(rf["bbox"])

        return matched_boxes

    def _schedule_event_clip(self, clip_id: str, t0_mono: float, event: Optional[dict]) -> None:
        """Lên lịch chốt clip 10s (``CLIP_PRE_S`` trước + ``CLIP_POST_S`` sau) cho
        một sự kiện, không chặn vòng AI: phải CHỜ ĐỦ ``CLIP_POST_S`` giây mới có
        khung ở nửa SAU thời điểm vi phạm, nên việc chốt (``keep_clip``) + lưu
        ra file (``_archive_clip_bg``, chỉ khi có ``event``) chạy ở một task nền.
        """
        task = asyncio.create_task(self._finalize_event_clip_safe(clip_id, t0_mono, event))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _finalize_event_clip_safe(self, clip_id: str, t0_mono: float,
                                        event: Optional[dict]) -> None:
        try:
            await asyncio.sleep(CLIP_POST_S)
            keep_clip(self.camera_id, clip_id, t0_mono)
            if event is not None:
                self._archive_clip_bg(event, clip_id)
        except Exception as e:
            print(f"[Camera] Lỗi chốt clip sự kiện {clip_id}: {e}")

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

    # ---------- biên bản vi phạm vùng cấm (gộp theo camera+vùng) ----------
    # Tách khỏi vòng lặp chính của ``_ai_loop`` để test được state machine (mở
    # biên bản mới / cập nhật khi có tên-mới-hoặc-đông-thêm / bỏ qua khi không
    # có gì mới / đóng sau khi vùng trống đủ lâu) mà không phải chạy cả vòng AI
    # (detector + face engine) — xem tests/test_incidents.py.

    @staticmethod
    def _render_incident_message(inc: dict) -> str:
        """Nội dung hiển thị của một biên bản (đang mở hoặc đang theo dõi đặt
        tên sau khi đóng): số người cao nhất từng thấy + tên đã biết, kèm
        "Không xác định" nếu lần gần nhất vẫn còn người chưa định danh."""
        zone = inc["zone"]
        display_names = sorted(inc["identified_names"]) + (["Không xác định"] if inc.get("has_unidentified") else [])
        return (f"{inc['max_count']} người vào vùng cấm {zone.get('name', 'vùng cấm')}: "
               + ", ".join(display_names))

    @staticmethod
    def _track_ids_of(indices, track_ids) -> set:
        if not track_ids:
            return set()
        out = set()
        for idx in indices:
            if idx < len(track_ids) and track_ids[idx] is not None and track_ids[idx] >= 0:
                out.add(track_ids[idx])
        return out

    async def _handle_restricted_violation(self, zone: dict, inside_now: list, identified: list,
                                           boxes: list, display_frame, dwell_seconds,
                                           now_mono: float, on_update, track_ids=None) -> None:
        """Gộp một vùng cấm ĐANG CÓ NGƯỜI vào biên bản còn mở của (camera, vùng)
        — track mới xác nhận hay tên vừa định danh thêm được đều cập nhật biên
        bản đang mở, KHÔNG tạo sự kiện mới (xem INTRUSION_INCIDENT_GRACE_S và
        docstring đầu file ``__init__``).

        Biên bản nhớ lại TẤT CẢ track đã từng thấy trong vùng (``track_ids``) —
        kể cả sau khi người đó đã rời vùng, track này vẫn được định danh tiếp
        (xem ``_sync_incident_names``), vì tên thường chỉ ra được LÚC RỜI vùng.
        """
        if self.events is None or not inside_now:
            return
        zone_id = zone.get("id", zone.get("name", "zone"))
        first_identified = identified[0] if identified else None
        identified_names = {p["person_name"] for p in identified}
        has_unidentified = len(identified) < len(boxes)
        entering_track_ids = self._track_ids_of(inside_now, track_ids)

        incident = self._open_incidents.get(zone_id)
        if incident is None:
            incident = {
                "zone": zone,
                "identified_names": set(identified_names),
                "has_unidentified": has_unidentified,
                "max_count": len(boxes),
                "person_id": (first_identified or {}).get("person_id"),
                "person_name": (first_identified or {}).get("person_name"),
                "person_military_id": (first_identified or {}).get("person_military_id"),
                # Track đã KHOÁ danh tính TRƯỚC KHI vào vùng (recall_identity đã
                # chạy cho mọi track trong intrusion_indices, xem _ai_loop) nên
                # ``identified`` ở đây đã phản ánh đúng ngay từ lúc mở biên bản.
                "track_ids": entering_track_ids,
                "identified_people": list(identified),
                "last_inside_mono": now_mono,
            }
            message = self._render_incident_message(incident)
            clip_id = f"clip_{int(time.time())}_{zone.get('id', 'zone')}"
            t0_mono = time.monotonic()
            # Đẩy cảnh báo đi trước rồi mới ghi ảnh/clip: cảnh báo đỏ phải hiện
            # ngay, đừng đợi ghi đĩa xong. emit_async ghi ảnh + JSON ở luồng
            # khác, chỉ phần đẩy SSE chạy ngay trên vòng lặp này.
            event = await self.events.emit_async(
                TYPE_INTRUSION, message, severity="critical", frame=display_frame,
                boxes=boxes, camera_id=self.camera_id, camera_name=self.camera_name,
                zone_id=zone.get("id"), clip_id=clip_id,
                person_id=incident["person_id"], person_name=incident["person_name"],
                person_military_id=incident["person_military_id"],
                detail={
                    "zone_name": zone.get("name", ""),
                    "zone_rule": zone.get("rule"),
                    "object_count": len(boxes),
                    "object_class": "person",
                    "identified": identified,
                    "dwell_seconds": dwell_seconds,
                },
            )
            incident["event_id"] = event["id"]
            self._open_incidents[zone_id] = incident
            await on_update({"type": "event", "event": event})
            self._schedule_event_clip(clip_id, t0_mono, event)
            return

        # Biên bản đã mở: chỉ ghi/đẩy lại khi có gì MỚI (tên vừa định danh thêm
        # được, hoặc số người tăng) — không ghi JSON xuống đĩa mỗi khung chỉ vì
        # vẫn còn đúng những người cũ.
        incident["last_inside_mono"] = now_mono
        incident["track_ids"] |= entering_track_ids
        incident["has_unidentified"] = has_unidentified
        grown = bool(identified_names - incident["identified_names"]) or len(boxes) > incident["max_count"]
        if not grown:
            return

        people = incident.setdefault("identified_people", [])
        for p in identified:
            if p.get("person_name") not in incident["identified_names"]:
                people.append(p)
        incident["identified_names"] |= identified_names
        incident["max_count"] = max(incident["max_count"], len(boxes))
        message = self._render_incident_message(incident)
        update_fields = dict(
            message=message, boxes=boxes,
            detail={"identified": list(people), "object_count": incident["max_count"]},
        )
        if incident.get("person_id") is None and first_identified is not None:
            incident["person_id"] = first_identified.get("person_id")
            incident["person_name"] = first_identified.get("person_name")
            incident["person_military_id"] = first_identified.get("person_military_id")
            update_fields["person_id"] = incident["person_id"]
            update_fields["person_name"] = incident["person_name"]
            update_fields["person_military_id"] = incident["person_military_id"]
        event = await self.events.update_async(incident["event_id"], **update_fields)
        if event is not None:
            await on_update({"type": "event_update", "event": event})

    def _close_stale_incidents(self, now_mono: float) -> None:
        """Đóng biên bản vùng cấm đã trống đủ ``INTRUSION_INCIDENT_GRACE_S``
        giây: vùng trống thì nó không còn xuất hiện trong ``intrusions`` của
        khung đó nữa (xem IntrusionDetector.check), nên phải dò bằng mốc "lần
        cuối còn người" đã ghi lúc còn mở, không suy ra được từ khung hiện tại.

        Đóng biên bản KHÔNG có nghĩa là hết việc: các track đã từng vào vùng
        chuyển sang "theo dõi đặt tên" thêm ``INTRUSION_NAMING_TAIL_S`` giây
        nữa (xem ``_sync_incident_names``) — phần lớn tên chỉ định danh được
        SAU khi người đó đã rời vùng, không phải lúc còn đứng trong đó.
        """
        for zid in [z for z, inc in self._open_incidents.items()
                   if now_mono - inc["last_inside_mono"] >= INTRUSION_INCIDENT_GRACE_S]:
            inc = self._open_incidents.pop(zid)
            if inc["track_ids"]:
                self._naming_watch[inc["event_id"]] = {**inc, "deadline_mono": now_mono + INTRUSION_NAMING_TAIL_S}

    async def _sync_incident_names(self, now_mono: float, on_update) -> None:
        """Mỗi khung: với track đã từng vào vùng cấm (biên bản còn MỞ hoặc đang
        trong thời gian theo dõi đặt tên SAU KHI ĐÓNG), nếu track đó VỪA có
        danh tính (``_recall_identity`` — ở bất cứ đâu trong khung, không chỉ
        lúc còn trong vùng, xem widen ``priority_indices`` trong ``_ai_loop``)
        mà biên bản CHƯA có tên đó -> cập nhật, KHÔNG tạo sự kiện mới.
        """
        if self.events is None:
            return
        for inc in self._open_incidents.values():
            await self._apply_incident_naming(inc, now_mono, on_update)

        expired = [eid for eid, w in self._naming_watch.items() if now_mono >= w["deadline_mono"]]
        for eid in expired:
            del self._naming_watch[eid]
        for watch in self._naming_watch.values():
            await self._apply_incident_naming(watch, now_mono, on_update)

    async def _apply_incident_naming(self, inc: dict, now_mono: float, on_update) -> None:
        if not inc.get("track_ids"):
            return
        changed = False
        for tid in inc["track_ids"]:
            person = self._recall_identity(tid, now_mono)
            if person is None:
                continue
            name = person_label(person)
            if name in inc["identified_names"]:
                continue
            inc["identified_names"].add(name)
            inc.setdefault("identified_people", []).append({
                "person_id": person.get("id"), "person_name": name,
                "person_military_id": person.get("military_id"),
            })
            changed = True
            if inc.get("person_id") is None:
                inc["person_id"] = person.get("id")
                inc["person_name"] = name
                inc["person_military_id"] = person.get("military_id")
        if not changed:
            return
        update_fields = dict(message=self._render_incident_message(inc),
                             detail={"identified": list(inc.get("identified_people") or [])})
        if inc.get("person_id") is not None:
            update_fields["person_id"] = inc["person_id"]
            update_fields["person_name"] = inc["person_name"]
            update_fields["person_military_id"] = inc.get("person_military_id")
        event = await self.events.update_async(inc["event_id"], **update_fields)
        if event is not None:
            await on_update({"type": "event_update", "event": event})

    def _rescan_regions(self, person_boxes, in_zone_indices, claimed, frame_shape,
                        track_ids=None, now_mono: Optional[float] = None,
                        restricted_indices=None, extra_budget: int = 0) -> list:
        """Chọn vùng ảnh quanh những người trong vùng chưa định danh để quét lại.

        Quét lại là phần đắt nhất của một khung (~30 ms/vùng, chạy tuần tự). Bỏ
        qua track đã khoá danh tính, và mỗi track chỉ quét lại tối đa một lần mỗi
        ``FACE_RESCAN_COOLDOWN_S`` — người lạ (chưa đăng ký) không bao giờ khớp
        nên trước đây bị quét lại ở MỌI khung, ăn hết thời gian AI.

        ``restricted_indices``: track đang ở TRONG VÙNG CẤM (``intrusion_indices``
        ở ``_ai_loop``) được xếp lên ĐẦU hàng chờ — biên bản vi phạm cần tên gấp
        hơn sĩ số đếm thường. ``extra_budget``: cộng thêm vào ``MAX_FACE_RESCANS``
        khi đang có biên bản vi phạm còn mở (xem ``self._open_incidents``), vì
        lúc đó vài vùng quét thêm mỗi khung đổi lại được tên sớm hơn, đáng giá.
        """
        h_img, w_img = frame_shape[:2]
        now_mono = time.monotonic() if now_mono is None else now_mono
        restricted_indices = restricted_indices or set()
        candidates = []
        for p_idx in in_zone_indices:
            if p_idx in claimed:
                continue
            tid = track_ids[p_idx] if track_ids is not None and p_idx < len(track_ids) else None
            if tid is not None and tid >= 0:
                entry = self.track_identity.get(tid)
                if entry is not None and entry.get("person") is not None:
                    continue
                last = self._last_rescan.get(tid)
                if last is not None and now_mono - last < FACE_RESCAN_COOLDOWN_S:
                    continue
            x1, y1, x2, y2 = person_boxes[p_idx]
            width = x2 - x1
            if width <= 0 or width > FACE_RESCAN_MAX_WIDTH:
                continue
            # Chỉ lấy phần thân trên, nơi có khuôn mặt, để tỉ lệ phóng to cao hơn
            pad = int(width * 0.25)
            # Ưu tiên: (1) đang vi phạm vùng cấm, (2) track lâu chưa được quét
            # nhất, (3) box to (rõ mặt hơn)
            waited = now_mono - self._last_rescan.get(tid, 0.0) if tid is not None else 1e9
            candidates.append((p_idx in restricted_indices, waited, width, tid, (
                max(0, x1 - pad),
                max(0, y1 - pad),
                min(w_img, x2 + pad),
                min(h_img, y1 + int((y2 - y1) * 0.6))
            )))

        candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
        chosen = candidates[:MAX_FACE_RESCANS + max(0, extra_budget)]
        for _restricted, _waited, _w, tid, _region in chosen:
            if tid is not None and tid >= 0:
                self._last_rescan[tid] = now_mono
        if len(self._last_rescan) > 1000:
            for old in sorted(self._last_rescan, key=self._last_rescan.get)[:500]:
                del self._last_rescan[old]
        return [region for _restricted, _waited, _w, _tid, region in chosen]

    def _remember_identity(self, track_id, person, similarity, now_mono, force_bind: bool = False) -> None:
        """Cộng dồn bằng chứng nhận diện cho một track; khoá cứng khi đủ điểm.

        Sau khi đã khoá, một người KHÁC vẫn được phép THAY THẾ nếu khớp đủ
        ``IDENTITY_REVOKE_HITS`` lượt với điểm trung bình cao hơn người đang
        khoá — sửa được lỗi khoá nhầm lúc mới vào khung (hai quân nhân giống
        nhau) mà không bị đổi lung tung chỉ vì một lượt nhận nhầm ngẫu nhiên.
        """
        if track_id is None or track_id < 0:
            return
        entry = self.track_identity.setdefault(
            track_id, {"scores": {}, "hits": {}, "people": {}, "person": None, "last_seen": now_mono}
        )
        entry["last_seen"] = now_mono

        pid = person.get("id")
        entry["people"][pid] = person
        entry["scores"][pid] = entry["scores"].get(pid, 0.0) + max(0.1, float(similarity))
        entry["hits"][pid] = entry["hits"].get(pid, 0) + 1

        if entry["person"] is None and force_bind:
            # Bằng chứng đã gộp từ nhiều khung (FACE_POOL_*) — đủ để khoá ngay
            entry["person"] = person
            return
        if entry["person"] is None:
            best_pid = max(entry["scores"], key=entry["scores"].get)
            if entry["scores"][best_pid] >= IDENTITY_BIND_SCORE:
                entry["person"] = entry["people"][best_pid]
            return

        bound_pid = entry["person"].get("id")
        if pid == bound_pid or entry["hits"][pid] < IDENTITY_REVOKE_HITS:
            return

        bound_mean = entry["scores"].get(bound_pid, 0.0) / max(1, entry["hits"].get(bound_pid, 1))
        challenger_mean = entry["scores"][pid] / entry["hits"][pid]
        if challenger_mean > bound_mean:
            print(f"[Camera] Thu hồi danh tính track {track_id}: "
                  f"{entry['person'].get('name')} (TB {bound_mean:.2f}) -> "
                  f"{person.get('name')} (TB {challenger_mean:.2f}, {entry['hits'][pid]} lượt khớp)")
            entry["person"] = person

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
        # Xoá HẲN entry (không chỉ "person") khi track vắng mặt quá TTL -> lượt
        # tới nếu track id này quay lại (hiếm, do tracker cấp id mới) sẽ coi như
        # chưa từng nhận diện, không kế thừa danh tính + điểm số cũ.
        for tid in [t for t, e in self.track_identity.items() if now_mono - e["last_seen"] > IDENTITY_TTL_SECONDS]:
            del self.track_identity[tid]
        for tid in [t for t, e in self._track_faces.items() if now_mono - e["last_seen"] > IDENTITY_TTL_SECONDS]:
            del self._track_faces[tid]

    # ---------- gộp embedding theo track / mặt chưa xác định / tự học ----------

    @staticmethod
    def _faces_to_people(faces, person_boxes, indices) -> Dict[int, dict]:
        """Gắn MỌI khuôn mặt (kể cả chưa nhận ra) vào box người nhỏ nhất chứa
        tâm mặt. Box chứa từ 2 mặt trở lên thì bỏ qua — không chắc mặt nào của
        người đó, gộp nhầm mặt người bên cạnh vào track còn tệ hơn bỏ một khung."""
        # Cùng một mặt có thể bị bắt 2 lần (quét cả khung + quét lại vùng người):
        # gộp các mặt chồng lấn nhiều, giữ bản to nhất (quét lại = rõ hơn).
        uniq: list = []
        for rf in sorted((f for f in faces if f.get("_emb") is not None),
                         key=lambda f: -(f["bbox"][2] - f["bbox"][0])):
            if all(_box_iou(rf["bbox"], u["bbox"]) < 0.3 for u in uniq):
                uniq.append(rf)
        per_box: Dict[int, list] = {}
        for rf in uniq:
            fcx, fcy = _bbox_center(rf["bbox"])
            best, best_area = None, None
            for p_idx in indices:
                px1, py1, px2, py2 = person_boxes[p_idx]
                if px1 - 20 <= fcx <= px2 + 20 and py1 - 20 <= fcy <= py2 + 40:
                    area = max(1, (px2 - px1) * (py2 - py1))
                    if best_area is None or area < best_area:
                        best, best_area = p_idx, area
            if best is not None:
                per_box.setdefault(best, []).append(rf)
        return {p_idx: rows[0] for p_idx, rows in per_box.items() if len(rows) == 1}

    @staticmethod
    def _face_jpeg(frame, bbox) -> Optional[bytes]:
        """Ảnh mặt (nới rộng, phóng to tối thiểu 112px) để cán bộ nhìn và gán tay."""
        h_img, w_img = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]
        pad = int(max(x2 - x1, y2 - y1) * 0.4)
        crop = frame[max(0, y1 - pad):min(h_img, y2 + pad), max(0, x1 - pad):min(w_img, x2 + pad)]
        if crop.size == 0:
            return None
        if crop.shape[0] < 112:
            scale = 112.0 / crop.shape[0]
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buf.tobytes() if ok else None

    def _pool_track_face(self, tid, face, frame, now_mono) -> Optional[dict]:
        """Cộng embedding khung này vào bản gộp của track (trọng số = chất lượng).

        Mặt nghiêng quá (``frontal`` False — ước lượng yaw từ 5 điểm mốc, xem
        ``face_engine.estimate_face_yaw``) có embedding kém tin cậy hơn hẳn ảnh
        đăng ký (thường chụp thẳng): BỎ hẳn khung này khỏi bản gộp/mẫu đề xuất
        (trọng số 0) thay vì để nó kéo lệch trung tâm track hoặc bị chọn làm
        ảnh đại diện "mặt chưa xác định". Face dict không có khoá ``frontal``
        (vd. test cũ, hoặc model không bật kps) thì coi như thẳng — giữ hành
        vi cũ.
        """
        width = int(face.get("width") or (face["bbox"][2] - face["bbox"][0]))
        if tid is None or tid < 0 or width < FACE_POOL_MIN_WIDTH or not face.get("frontal", True):
            return None
        emb = face["_emb"]
        quality = float(face.get("det_score", 0.5)) * min(1.0, width / 48.0)
        entry = self._track_faces.get(tid)
        if entry is None or entry["sum"].shape != emb.shape:
            entry = {"sum": np.zeros_like(emb, dtype=np.float32), "wsum": 0.0, "n": 0, "top": [],
                     "last_seen": now_mono}
            self._track_faces[tid] = entry
        entry["sum"] += quality * emb
        entry["wsum"] += quality
        entry["n"] += 1
        entry["last_seen"] = now_mono
        top = entry["top"]
        if len(top) < 3 or quality > top[-1][0]:
            # Ảnh đã canh thẳng (112x112, từ face_engine) cho kết quả nhất quán hơn
            # hẳn ảnh cắt thô để hiển thị + lưu làm mẫu; không có (model không bật
            # kps) thì rơi về cách cũ.
            crop = face.get("_crop")
            jpeg = None
            if crop is not None:
                ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                jpeg = buf.tobytes() if ok else None
            if jpeg is None:
                jpeg = self._face_jpeg(frame, face["bbox"])
            if jpeg is not None:
                top.append((quality, emb, jpeg, width))
                top.sort(key=lambda t: -t[0])
                del top[3:]
        return entry

    def _update_face_pools(self, faces, person_boxes, track_ids, frame, now_mono) -> int:
        """Gộp embedding theo track; track chưa định danh mà bản gộp khớp đủ chắc
        thì khoá danh tính; còn lại đưa vào danh sách "mặt chưa xác định".
        Trả số track vừa được định danh nhờ bản gộp."""
        attached = self._faces_to_people(faces, person_boxes, range(len(person_boxes)))
        bound = 0
        for p_idx, face in attached.items():
            tid = track_ids[p_idx] if p_idx < len(track_ids) else None
            if self._capture_sessions:
                self._feed_capture_sessions(tid, face, frame, now_mono)
            entry = self._pool_track_face(tid, face, frame, now_mono)
            if entry is None:
                continue
            ident = self.track_identity.get(tid)
            if ident is not None and ident.get("person") is not None:
                self.face_engine.unknown_log.drop_track(self.camera_id, tid)
                continue
            if entry["n"] >= FACE_POOL_MIN_SAMPLES:
                pooled = entry["sum"] / (np.linalg.norm(entry["sum"]) or 1.0)
                person, sim, _margin = self.face_engine.match_embedding(
                    pooled, FACE_POOL_THRESHOLD, FACE_POOL_MARGIN)
                if person is not None:
                    self._remember_identity(tid, person, sim, now_mono, force_bind=True)
                    self.face_engine.unknown_log.drop_track(self.camera_id, tid)
                    bound += 1
                    continue
            if entry["top"] and entry["top"][0][3] >= UNKNOWN_FACE_MIN_WIDTH:
                self.face_engine.unknown_log.upsert(self.camera_id, tid, entry["top"])
        return bound

    # ---------- thu khuôn mặt chủ động (POST /api/faces/capture) ----------
    # Khác mọi nơi khác (giới hạn theo priority_indices để đỡ tốn CPU), ở đây
    # cán bộ đã CHỦ ĐỘNG bấm chụp nên không lọc theo vùng: gom mặt đủ rõ của
    # MỌI track thấy được trong window, không chặn vòng AI.

    def _feed_capture_sessions(self, tid, face, frame, now_mono) -> None:
        """Góp một khuôn mặt đủ rõ (``FACE_CAPTURE_MIN_WIDTH`` px, không
        nghiêng) vào các phiên thu chủ động đang mở (nếu có). Tách khỏi
        ``_pool_track_face`` vì phiên thu không quan tâm track đã khoá danh
        tính hay chưa — chỉ cần mặt rõ."""
        if not self._capture_sessions or tid is None or tid < 0:
            return
        width = int(face.get("width") or (face["bbox"][2] - face["bbox"][0]))
        if width < FACE_CAPTURE_MIN_WIDTH or not face.get("frontal", True):
            return
        emb = face.get("_emb")
        if emb is None:
            return
        crop = face.get("_crop")
        jpeg = None
        if crop is not None:
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            jpeg = buf.tobytes() if ok else None
        if jpeg is None:
            jpeg = self._face_jpeg(frame, face["bbox"])
        if jpeg is None:
            return
        quality = float(face.get("det_score", 0.5)) * min(1.0, width / 48.0)
        sample = (quality, emb, jpeg, width)
        for session in self._capture_sessions:
            session["per_track"].setdefault(tid, []).append(sample)

    async def capture_faces(self, seconds: float) -> Optional[dict]:
        """Thu khuôn mặt chủ động theo yêu cầu cán bộ (xem POST /api/faces/capture):
        mở một phiên gom mặt trong ``seconds`` giây (``await asyncio.sleep``,
        không chặn vòng AI — ``_feed_capture_sessions`` vẫn chạy trên
        ``_ai_loop`` suốt thời gian này), rồi chọn track có NHIỀU MẶT DÙNG
        ĐƯỢC NHẤT và tối đa ``FACE_CAPTURE_MAX_SAMPLES`` mẫu càng khác nhau
        càng tốt (xem ``_pick_diverse_samples``).

        Trả ``None`` nếu không track nào có mặt đủ rõ trong suốt phiên (API
        trả 400 cho trường hợp này, xem app/api.py).
        """
        session = {"per_track": {}}
        self._capture_sessions.append(session)
        try:
            await asyncio.sleep(max(0.0, seconds))
        finally:
            self._capture_sessions.remove(session)

        per_track = session["per_track"]
        if not per_track:
            return None
        best_tid = max(per_track, key=lambda t: len(per_track[t]))
        chosen = _pick_diverse_samples(per_track[best_tid], FACE_CAPTURE_MAX_SAMPLES)
        if not chosen:
            return None
        return {"track_id": best_tid, "samples": chosen}

    def _auto_enroll_candidates(self, assignments, track_ids, now_mono) -> list:
        """(person_id, embedding) đủ điều kiện tự học mẫu (xem FACE_AUTO_ENROLL).

        Mặt nghiêng quá (``frontal`` False) không được tự học: embedding của góc
        nghiêng kéo trung tâm mẫu của người đó lệch khỏi ảnh đăng ký (chụp thẳng),
        tự học sai hướng còn hại hơn không học. Thiếu khoá ``frontal`` thì coi là
        thẳng (giữ hành vi cũ, vd. test dựng face dict tay không có khoá này).
        """
        if not FACE_AUTO_ENROLL:
            return []
        out = []
        for p_idx, info in assignments.items():
            face = info.get("face") or {}
            pid = (info.get("person") or {}).get("id")
            tid = track_ids[p_idx] if p_idx < len(track_ids) else None
            ident = self.track_identity.get(tid) if tid is not None else None
            if (pid is None or face.get("_emb") is None or ident is None
                    or (ident.get("person") or {}).get("id") != pid):
                continue
            if not face.get("frontal", True):
                continue
            if (face.get("similarity", 0.0) < FACE_AUTO_ENROLL_SIM
                    or face.get("margin", 0.0) < FACE_AUTO_ENROLL_MARGIN
                    or int(face.get("width") or 0) < FACE_AUTO_ENROLL_MIN_WIDTH):
                continue
            if now_mono - self._last_auto_enroll.get(pid, -1e9) < FACE_AUTO_ENROLL_INTERVAL_S:
                continue
            self._last_auto_enroll[pid] = now_mono
            # Kèm ảnh mặt đã căn chỉnh (nếu có) để sau đổi model còn tính lại được
            crop = face.get("_crop")
            out.append((pid, (face["_emb"], crop, face.get("width")) if crop is not None else face["_emb"]))
        return out

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
                if identity:
                    who = person_label(identity)
                    label = f"VI PHẠM: {who}" if who and who != "?" else label
            elif t["in_zone"] and identity:
                color = "#00ff78"
                label = person_label(identity)
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
        # Khung đã dùng để cập nhật motion gate lần gần nhất — tách khỏi last_seq
        # vì motion phải cập nhật ở MỌI khung MỚI nhận được (để phản ứng kịp lúc
        # vừa có động), không chỉ khung được AI xử lý đầy đủ (bị throttle).
        last_motion_seq = -1
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

                h_img, w_img = frame.shape[:2]
                # Vùng đếm quân số/an toàn: lấy từ cấu hình, không có thì dùng mặc
                # định. Tính SỚM (trước cả throttle) vì ROI và motion gate đều cần.
                zones_cfg = self.zones.zones()
                roi_box = self._get_roi(zones_cfg, w_img, h_img) if AI_ROI else None

                # Motion gate: rẻ (ảnh xám 160px) nên chạy ở MỌI khung mới, kể cả
                # khung sắp bị bỏ qua vì chưa tới nhịp AI — phát hiện có động phải
                # nhanh hơn chính cái nhịp mà nó dùng để quyết định hạ/nâng.
                if AI_MOTION_GATING and seq != last_motion_seq:
                    last_motion_seq = seq
                    region = frame[roi_box[1]:roi_box[3], roi_box[0]:roi_box[2]] if roi_box else frame
                    self._last_motion = self._motion_gate.update(region)

                if seq == last_seq:
                    if not is_rtsp and reader.ended:
                        break
                    await asyncio.sleep(0.01)
                    continue

                now = clock.now()

                # Nhịp AI: đủ nhịp bình thường (ai_fps) nếu có động, có track đang
                # sống, đang trong cửa sổ điểm danh, hoặc có người trong vùng cấm;
                # ngược lại hạ về AI_IDLE_FPS lúc cảnh tĩnh hoàn toàn để đỡ CPU —
                # không bao giờ hạ nhịp khi đang cần "nhìn" (điểm danh/an toàn).
                if AI_MOTION_GATING:
                    has_tracks = bool(self.latest_result and self.latest_result.get("tracks"))
                    attendance_active = bool(self.attendance and self.attendance.status(now).get("active"))
                    restricted_occupied = self.intrusion.restricted_occupied()
                    idle = (not self._last_motion) and not has_tracks \
                        and not attendance_active and not restricted_occupied
                    self._ai_mode = "idle" if idle else "active"
                    min_interval = 1.0 / max(1, AI_IDLE_FPS) if idle else 1.0 / max(1, _HW.ai_fps)
                else:
                    self._ai_mode = "active"
                    min_interval = 1.0 / max(1, _HW.ai_fps)

                # Giữ đúng tốc độ xử lý mong muốn dù nguồn phát nhanh hơn: lấy
                # khung MỚI NHẤT nhưng không xử lý dày hơn nhịp đang chọn ở trên.
                now_check = time.monotonic()
                if now_check - last_processed_mono < min_interval:
                    await asyncio.sleep(0.005)
                    continue

                last_seq = seq
                last_processed_mono = now_check
                t0 = time.monotonic()

                # 1. Hai model chạy song song trên cùng khung hình, mỗi model một thread
                #    nên vòng lặp sự kiện không bị chặn trong lúc inference. YOLO chỉ
                #    thấy ROI (nếu có) để giữ độ phân giải hiệu dụng cao hơn ở imgsz cố
                #    định; mặt vẫn quét CẢ khung vì người ngoài ROI vẫn định danh được.
                if roi_box:
                    rx1, ry1, rx2, ry2 = roi_box
                    # .copy(): slice theo cột không liên tục trong bộ nhớ, một số bước
                    # xử lý ảnh của ultralytics cần mảng liên tục để chạy đúng/nhanh.
                    detect_frame = frame[ry1:ry2, rx1:rx2].copy()
                    detect_offset = (rx1, ry1)
                else:
                    detect_frame = frame
                    detect_offset = (0, 0)

                detection, recognized_faces = await asyncio.gather(
                    asyncio.to_thread(self.detector.detect_persons, detect_frame, detect_offset),
                    asyncio.to_thread(self.face_engine.recognize_faces_in_frame, frame),
                )
                person_boxes = detection['boxes']
                confidences = detection['confidences']
                track_ids = detection['track_ids']
                occluded_flags = detection['occluded']

                # 2. Polygon đếm quân số (đã có zones_cfg từ bước tính ROI ở trên)
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

                # Sĩ số trong vùng = những người NẰM TRONG vùng đã cấu hình, đếm
                # theo TRACK DUY NHẤT (xem ``_dedupe_zone_count``).
                person_count = _dedupe_zone_count(in_zone_indices, track_ids, occluded_flags, person_boxes)

                # 3. Giám sát an toàn: ai vào vùng cấm hoặc vượt vạch an toàn
                # (``now`` đã lấy sớm hơn ở bước quyết định nhịp AI, dùng lại để
                # toàn bộ khung này mang ĐÚNG MỘT mốc thời gian nhất quán)
                intrusions = self.intrusion.check(person_boxes, track_ids, w_img, h_img, now,
                                                  occluded=occluded_flags,
                                                  confidences=confidences)
                # intrusion_indices dùng để VẼ (khoanh đỏ liên tục) và cho SSE: phải
                # là TẤT CẢ người đang ở trong vùng cấm ("inside"), không chỉ người
                # vừa xác nhận ("indices" — chỉ có ở khung VỪA chuyển trạng thái).
                intrusion_indices = {idx for v in intrusions for idx in v.get("inside") or []}
                intrusion_indices |= {idx for v in intrusions for idx in v["indices"]}

                # Track đã từng vào vùng cấm của một biên bản còn mở HOẶC đang
                # trong thời gian "theo dõi đặt tên" sau khi đóng (xem
                # INTRUSION_NAMING_TAIL_S): vẫn cần được định danh dù đã RỜI
                # vùng — phần lớn tên chỉ ra được lúc này (quay mặt lại/đi ngang
                # camera ở góc đẹp hơn), không phải lúc còn đứng trong vùng.
                naming_watch_track_ids = set()
                for _inc in self._open_incidents.values():
                    naming_watch_track_ids |= _inc.get("track_ids") or set()
                for _watch in self._naming_watch.values():
                    naming_watch_track_ids |= _watch.get("track_ids") or set()
                naming_watch_indices = {
                    p_idx for p_idx, tid in enumerate(track_ids)
                    if tid is not None and tid >= 0 and tid in naming_watch_track_ids
                } if naming_watch_track_ids else set()

                # 4. Gắn khuôn mặt đã quét được vào người trong vùng điểm danh HOẶC
                # đang vi phạm vùng cấm (violator cần định danh cho sự kiện, dù có
                # thể đứng ngoài vùng điểm danh) — không dùng để tính sĩ số/có mặt,
                # chỉ để tìm tên; present_personnel dưới đây lọc lại đúng trong vùng.
                priority_indices = in_zone_indices | intrusion_indices | naming_watch_indices
                now_mono = time.monotonic()
                claimed_boxes = set()
                assignments: Dict[int, dict] = {}
                matched_face_boxes = self._assign_faces(
                    recognized_faces, person_boxes, priority_indices, claimed_boxes, assignments
                )

                # 4b. Người còn lại (vùng điểm danh + vùng cấm): cắt riêng vùng ảnh
                # rồi quét lại.
                rescanned = []
                regions = self._rescan_regions(person_boxes, priority_indices, claimed_boxes, frame.shape,
                                               track_ids, time.monotonic(),
                                               restricted_indices=intrusion_indices | naming_watch_indices,
                                               extra_budget=2 if (self._open_incidents or self._naming_watch) else 0)
                if regions:
                    rescanned = await asyncio.to_thread(
                        self.face_engine.recognize_in_regions, frame, regions
                    )
                    matched_face_boxes += self._assign_faces(
                        rescanned, person_boxes, priority_indices, claimed_boxes, assignments
                    )

                # 4c. Khoá danh tính vào track: một quân nhân đã nhận ra được thì vẫn
                #     được tính khi quay mặt đi hoặc bị che trong các khung hình sau.
                live_matches = len(assignments)
                for p_idx, info in assignments.items():
                    self._remember_identity(track_ids[p_idx], info["person"], info["similarity"], now_mono)

                # 4d. Gộp embedding theo track (mặt nhỏ/mờ: từng khung không đủ
                #     điểm nhưng gộp nhiều khung thì đủ) + mặt chưa xác định + tự học.
                try:
                    self._update_face_pools(list(recognized_faces) + list(rescanned), person_boxes,
                                            track_ids, frame, now_mono)
                    for pid, emb in self._auto_enroll_candidates(assignments, track_ids, now_mono):
                        added = await asyncio.to_thread(self.face_engine.add_samples, pid, [emb], "auto")
                        if added:
                            print(f"[Camera] {self.camera_id}: tự học thêm 1 mẫu khuôn mặt cho {pid}")
                except Exception as e:
                    print(f"[Camera] {self.camera_id}: lỗi gộp/tự học khuôn mặt: {e}")

                person_to_face_match = {p_idx: info["person"] for p_idx, info in assignments.items()}
                claimed_person_ids = {info["person"]["id"] for info in assignments.values()}

                for p_idx in priority_indices:
                    if p_idx in person_to_face_match:
                        continue
                    remembered = self._recall_identity(track_ids[p_idx], now_mono)
                    if remembered is None or remembered.get("id") in claimed_person_ids:
                        continue
                    person_to_face_match[p_idx] = remembered
                    claimed_person_ids.add(remembered.get("id"))

                self._prune_identities(now_mono)

                # present_personnel (sĩ số đã định danh) chỉ tính người THẬT SỰ
                # trong vùng điểm danh — person_to_face_match có thể còn chứa cả
                # người đang vi phạm vùng cấm (ngoài vùng điểm danh), không được
                # tính vào đây.
                present_personnel = []
                seen_ids = set()
                for p_idx in sorted(person_to_face_match):
                    if p_idx not in in_zone_indices:
                        continue
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

                def _collect_people(indices):
                    """Box + tên cho từng người tại các vị trí ``indices`` của
                    khung này. ``identified`` chỉ gồm người ĐÃ định danh được."""
                    identified_people, out_boxes = [], []
                    for idx in indices:
                        person = person_to_face_match.get(idx)
                        label = "Không xác định"
                        if person is not None:
                            label = person_label(person)
                            identified_people.append({
                                "person_id": person.get("id"), "person_name": label,
                                "person_military_id": person.get("military_id"),
                            })
                        out_boxes.append(normalize_box(person_boxes[idx], w_img, h_img,
                                                       confidences[idx], label))
                    return identified_people, out_boxes

                for violation in intrusions:
                    if self.events is None:
                        break
                    zone = violation["zone"]
                    is_crossing = zone.get("rule") == RULE_CROSSING

                    if is_crossing:
                        # Vạch an toàn: không có khái niệm "đang ở trong", chỉ có
                        # khoảnh khắc cắt qua -> giữ đúng hành vi cũ, một sự kiện
                        # mỗi lần "indices" (vừa cắt qua) không rỗng.
                        if not violation["indices"]:
                            continue
                        identified, boxes = _collect_people(violation["indices"])
                        first_identified = identified[0] if identified else None
                        message = (f"Phát hiện {len(boxes):02d} đối tượng vượt "
                                   f"{zone.get('name', 'vùng cấm')}.")
                        clip_id = f"clip_{int(time.time())}_{zone.get('id', 'zone')}"
                        t0_mono = time.monotonic()
                        event = await self.events.emit_async(
                            TYPE_INTRUSION, message, severity="critical", frame=display_frame,
                            boxes=boxes, camera_id=self.camera_id, camera_name=self.camera_name,
                            zone_id=zone.get("id"), clip_id=clip_id,
                            person_id=(first_identified or {}).get("person_id"),
                            person_name=(first_identified or {}).get("person_name"),
                            person_military_id=(first_identified or {}).get("person_military_id"),
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
                        self._schedule_event_clip(clip_id, t0_mono, event)
                        continue

                    # Vùng cấm: gộp mọi lần xác nhận của (camera, vùng) này vào
                    # MỘT biên bản còn mở (xem _handle_restricted_violation).
                    inside_now = violation.get("inside") or []
                    identified, boxes = _collect_people(inside_now)
                    await self._handle_restricted_violation(
                        zone, inside_now, identified, boxes, display_frame,
                        violation["dwell_seconds"], now_mono, on_update, track_ids=track_ids)

                # Đóng biên bản vùng cấm đã trống đủ lâu: vùng trống thì nó
                # không còn xuất hiện trong ``intrusions`` của khung này nữa
                # (xem IntrusionDetector.check), nên phải dò bằng mốc "lần cuối
                # còn người" đã ghi lúc còn mở, không suy ra được từ vòng trên.
                self._close_stale_incidents(now_mono)

                # Tên muộn: track đã từng vào vùng cấm (biên bản còn mở hoặc
                # đang trong thời gian theo dõi sau khi đóng) mà VỪA định danh
                # được ở BẤT CỨ ĐÂU trong khung (không chỉ lúc còn trong vùng)
                # -> cập nhật lại tên cho (các) biên bản liên quan.
                await self._sync_incident_names(now_mono, on_update)

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
                    event_clip_id = f"clip_{int(time.time())}"
                    t0_mono = time.monotonic()
                    alert_data = alert_result['alert_data']

                    event = None
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

                    self._schedule_event_clip(event_clip_id, t0_mono, event)

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
        self._track_faces.clear()
        self._open_incidents.clear()
        # ROI/nền động gắn với cỡ khung + vùng của luồng CŨ; luồng mới có thể
        # đổi camera nguồn (cỡ khung khác) nên bỏ hết, tính lại từ đầu.
        self._roi_cache_key = None
        self._roi_box = None
        self._motion_gate.reset()
        self._last_motion = True
        self._ai_mode = "active"
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
