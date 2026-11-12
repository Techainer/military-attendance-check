"""Kho sự kiện dùng chung cho mọi loại cảnh báo.

Trước đây mỗi loại cảnh báo đi một đường riêng: thiếu quân số bắn qua WebSocket
dưới dạng ``alert``, sự kiện hệ thống dưới dạng ``system_event``, còn vi phạm an
toàn thì chưa có. Module này gom tất cả về một cấu trúc duy nhất theo
``docs/api/events.schema.json`` để dòng sự kiện, thư viện ảnh vi phạm và nút xác
nhận xử lý cùng đọc một nguồn.
"""

import asyncio
import os
from pathlib import Path
from typing import List, Optional

import subprocess

import cv2

from app import clock
from app.storage import read_json_list, write_json_list

# Bản POC chạy một luồng camera. Hợp đồng API vẫn luôn mang camera_id để khi lên
# nhiều camera không phải đổi hình dạng payload.
CAMERA_ID = "cam_01"
CAMERA_NAME = "Sân tập trung"
AREA_ID = "area_01"
AREA_NAME = "Thao trường số 1"

TYPE_ABSENT = "ABSENT"
TYPE_LATE = "LATE"
TYPE_EARLY_LEAVE = "EARLY_LEAVE"
TYPE_INTRUSION = "INTRUSION"
TYPE_SYSTEM = "SYSTEM"

# Giữ lại bấy nhiêu sự kiện gần nhất trong file JSON
MAX_STORED_EVENTS = 500

# Đoạn ghi phát lại lấy khung từ vòng render (STREAM_FPS trong video_processor,
# mặc định 12/giây) nên ở đây đọc chung biến môi trường để không bị lệch nhịp
# (ffmpeg cần đúng framerate đầu vào, sai thì clip chạy nhanh/chậm hơn thật).
CLIP_FPS = int(os.environ.get("STREAM_FPS", "12"))


def normalize_box(box, width: int, height: int, confidence=None, label=None) -> dict:
    """Đổi bounding box pixel sang toạ độ chuẩn hoá [0,1] như hợp đồng quy định."""
    x1, y1, x2, y2 = box
    out = {
        "x1": round(float(x1) / width, 4),
        "y1": round(float(y1) / height, 4),
        "x2": round(float(x2) / width, 4),
        "y2": round(float(y2) / height, 4),
    }
    if confidence is not None:
        out["confidence"] = round(float(confidence), 3)
    if label:
        out["label"] = label
    return out


class EventStore:
    """Ghi sự kiện xuống JSON và đẩy realtime cho các client đang mở kênh SSE."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.events_file = self.data_dir / "events.json"
        self.snapshot_dir = self.data_dir / "events"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._subscribers: List[asyncio.Queue] = []
        self._seq = 0

    # ---------- kênh realtime ----------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _publish(self, event: dict) -> None:
        """Đẩy sự kiện cho các client SSE.

        Phải được gọi từ chính vòng lặp sự kiện đang phục vụ các client (vòng xử
        lý video và các handler API đều thoả). Gọi từ một thread khác thì client
        sẽ không được đánh thức.
        """
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Client đọc chậm thì bỏ bản tin chứ không chặn vòng xử lý video
                pass

    # ---------- ghi nhận ----------

    def _save_snapshot(self, frame, event_id: str) -> Optional[str]:
        if frame is None:
            return None
        try:
            cv2.imwrite(str(self.snapshot_dir / f"{event_id}.jpg"), frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 85])
        except Exception as e:
            print(f"[Events] Lỗi lưu ảnh sự kiện: {e}")
            return None
        return f"/data/events/{event_id}.jpg"

    def save_clip(self, event_id: str, frames: List[bytes]) -> Optional[str]:
        """Dựng đoạn mp4 từ các khung JPEG (bytes) và lưu cạnh ảnh bằng chứng.

        Trước đây khung truyền vào là base64 của ảnh ĐỘ PHÂN GIẢI GỐC — nặng,
        và phải giải mã base64 trước khi ghép. Giờ bộ đệm clip giữ sẵn JPEG bytes
        ở độ phân giải luồng (bản vòng render đã mã hoá để phát, dùng lại luôn),
        nên ở đây chỉ còn việc ghép, khỏi base64-decode.

        Bộ đệm khung hình chỉ nằm trong RAM và bị dọn dần, nên khởi động lại máy
        chủ là mọi nút "Xem clip" trong nhật ký cũ đều chết. Ghi ra file ngay lúc
        sự kiện xảy ra thì đoạn ghi sống cùng ảnh bằng chứng.

        Mã hoá bằng ffmpeg chứ không bằng cv2.VideoWriter: bản opencv-python trên
        PyPI không kèm encoder x264 nên chỉ ghi được mp4v (MPEG-4 Part 2), mà thẻ
        <video> của trình duyệt từ chối codec đó — file hợp lệ nhưng không phát
        được. Khung hình sẵn là JPEG nên đẩy thẳng vào ffmpeg, khỏi giải mã.
        """
        if not frames:
            return None

        clip_file = self.snapshot_dir / f"{event_id}.mp4"
        if clip_file.exists() and clip_file.stat().st_size > 0:
            return f"/api/v1/events/{event_id}/clip"

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            # Nói thẳng đầu vào là MJPEG: để ffmpeg tự dò thì khung hình nhỏ
            # (ảnh nén nhẹ) không đủ dữ liệu, nó bỏ luôn luồng video
            "-f", "image2pipe", "-vcodec", "mjpeg",
            "-framerate", str(CLIP_FPS), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            # yuv420p đòi cạnh chẵn; khung lẻ pixel sẽ làm libx264 bỏ cuộc
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            # Đưa moov atom lên đầu file để trình duyệt phát được ngay, không
            # phải tải hết mới bắt đầu
            "-movflags", "+faststart",
            str(clip_file),
        ]
        try:
            payload = b"".join(frames)
            proc = subprocess.run(cmd, input=payload, capture_output=True, timeout=120)
            if proc.returncode != 0:
                print(f"[Events] ffmpeg lỗi khi dựng đoạn ghi: "
                      f"{proc.stderr.decode('utf-8', 'replace')[:300]}")
        except Exception as e:
            print(f"[Events] Lỗi dựng đoạn ghi: {e}")

        if not clip_file.exists() or clip_file.stat().st_size == 0:
            clip_file.unlink(missing_ok=True)
            return None
        return f"/api/v1/events/{event_id}/clip"

    def _build_event(self, event_type, message, severity, boxes, detail, acked, fields) -> dict:
        """Dựng cấu trúc sự kiện. KHÔNG đụng tới đĩa — xem ``_persist``."""
        now = clock.now()
        self._seq += 1
        event_id = f"evt_{int(now.timestamp() * 1000)}_{self._seq}"

        event = {
            "id": event_id,
            "type": event_type,
            "severity": severity,
            "occurred_at": clock.iso(now),
            "camera_id": fields.pop("camera_id", CAMERA_ID),
            "camera_name": fields.pop("camera_name", CAMERA_NAME),
            "area_id": fields.pop("area_id", AREA_ID),
            "area_name": fields.pop("area_name", AREA_NAME),
            "zone_id": fields.pop("zone_id", None),
            "session_id": fields.pop("session_id", None),
            "schedule_id": fields.pop("schedule_id", None),
            "person_id": fields.pop("person_id", None),
            "person_name": fields.pop("person_name", None),
            # Số hiệu (military_id) đi kèm người, để hiển thị không mập mờ khi
            # trùng tên (xem app/attendance.py person_label) — có thể thiếu ở
            # sự kiện cũ, FE/API đọc list đã enrich lại bằng person_id (xem
            # app/api.py v1_list_events).
            "person_military_id": fields.pop("person_military_id", None),
            "message": message,
            "snapshot_url": None,
            "clip_id": fields.pop("clip_id", None),
            "clip_url": None,
            "boxes": boxes or [],
            "detail": detail or {},
            "acked": acked,
            "acked_by": None,
            "acked_at": None,
            "ack_note": None,
        }
        if event["clip_id"]:
            event["clip_url"] = f"/api/v1/events/{event_id}/clip"
        return event

    def _persist(self, event: dict, frame=None) -> dict:
        """Ghi ảnh bằng chứng + thêm vào events.json. CÓ I/O đĩa: gọi từ vòng xử
        lý video phải bọc ``asyncio.to_thread`` (xem ``emit_async``), không thì
        chặn cả vòng lặp trong lúc chờ ghi ảnh/JPEG + ghi lại toàn bộ file JSON.
        """
        event["snapshot_url"] = self._save_snapshot(frame, event["id"])

        events = read_json_list(self.events_file)
        events.insert(0, event)
        write_json_list(self.events_file, events[:MAX_STORED_EVENTS])
        return event

    def emit(
        self,
        event_type: str,
        message: str,
        severity: str = "info",
        frame=None,
        boxes: Optional[List[dict]] = None,
        detail: Optional[dict] = None,
        acked: bool = False,
        **fields
    ) -> dict:
        """Tạo một sự kiện, lưu xuống file và đẩy cho client. Trả về sự kiện đã tạo.

        Đồng bộ — dùng ở mọi nơi KHÔNG phải vòng xử lý video (điểm danh, API ack,
        …). Vòng xử lý video dùng ``emit_async`` để không chặn vòng lặp khi ghi
        ảnh bằng chứng xuống đĩa.
        """
        event = self._build_event(event_type, message, severity, boxes, detail, acked, fields)
        self._persist(event, frame)
        self._publish(event)
        print(f"[Events] {event_type}: {message}")
        return event

    async def emit_async(
        self,
        event_type: str,
        message: str,
        severity: str = "info",
        frame=None,
        boxes: Optional[List[dict]] = None,
        detail: Optional[dict] = None,
        acked: bool = False,
        **fields
    ) -> dict:
        """Bản không chặn vòng lặp sự kiện, dùng trong vòng xử lý video.

        Ghi ảnh bằng chứng + file JSON (có I/O đĩa) ở một luồng khác; CHỈ
        ``_publish`` (đẩy cho client SSE) mới chạy trên vòng lặp chính — bắt
        buộc, nếu không client đang mở kênh sẽ không được đánh thức (xem docstring
        của ``_publish``).
        """
        event = self._build_event(event_type, message, severity, boxes, detail, acked, fields)
        await asyncio.to_thread(self._persist, event, frame)
        self._publish(event)
        print(f"[Events] {event_type}: {message}")
        return event

    # ---------- cập nhật sự kiện đang MỞ ----------
    # Vi phạm vùng cấm được gộp vào MỘT sự kiện còn mở trong lúc vùng còn người
    # (xem app/video_processor.py) — khác ``ack`` (người dùng xác nhận đã xử lý),
    # đây là hệ thống tự cập nhật thêm thông tin (tên vừa định danh được, số
    # người đổi) vào sự kiện đã bắn, KHÔNG tạo sự kiện mới.

    def _update_locked(self, event_id: str, fields: dict) -> Optional[dict]:
        """Đọc-sửa-ghi ``events.json``, KHÔNG publish (xem ``update``/``update_async``).
        ``detail`` được GỘP vào detail cũ thay vì đè hẳn, để không mất các
        trường không đổi (vd. ``zone_name``, ``dwell_seconds``)."""
        events = read_json_list(self.events_file)
        for event in events:
            if event.get("id") != event_id:
                continue
            for key, value in fields.items():
                if key == "detail" and isinstance(value, dict):
                    event["detail"] = {**(event.get("detail") or {}), **value}
                else:
                    event[key] = value
            write_json_list(self.events_file, events)
            return event
        return None

    def update(self, event_id: str, **fields) -> Optional[dict]:
        """Cập nhật một sự kiện đã có rồi đẩy lại cho client SSE. Trả về
        ``None`` nếu không tìm thấy.

        Đồng bộ — dùng ở mọi nơi KHÔNG phải vòng xử lý video (API, script vá
        dữ liệu...). Vòng xử lý video dùng ``update_async`` để không chặn vòng
        lặp khi ghi lại file JSON (xem docstring ``emit``/``emit_async``).

        Payload đẩy cho SSE là CHÍNH sự kiện đã cập nhật, kèm thêm
        ``"updated": True`` để phân biệt với một sự kiện MỚI (``emit`` không
        có cờ này) — FE dò cờ này để sửa lại dòng đã hiện thay vì thêm dòng mới.
        """
        event = self._update_locked(event_id, fields)
        if event is not None:
            self._publish({**event, "updated": True})
            print(f"[Events] cập nhật {event_id}: {fields.get('message', '')}")
        return event

    async def update_async(self, event_id: str, **fields) -> Optional[dict]:
        """Bản không chặn vòng lặp sự kiện của ``update``, dùng trong vòng xử
        lý video."""
        event = await asyncio.to_thread(self._update_locked, event_id, fields)
        if event is not None:
            self._publish({**event, "updated": True})
            print(f"[Events] cập nhật {event_id}: {fields.get('message', '')}")
        return event

    # ---------- truy vấn ----------

    def list_events(
        self,
        types: Optional[List[str]] = None,
        acked: Optional[bool] = None,
        session_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        occurred_from: Optional[str] = None,
        occurred_to: Optional[str] = None,
        zone_rule: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple:
        """Trả về ``(danh sách sự kiện, tổng số khớp)``. Danh sách mới nhất trước."""
        events = read_json_list(self.events_file)
        if types:
            events = [e for e in events if e.get("type") in types]
        if acked is not None:
            events = [e for e in events if bool(e.get("acked")) == acked]
        if session_id:
            events = [e for e in events if e.get("session_id") == session_id]
        if camera_id:
            events = [e for e in events if e.get("camera_id") == camera_id]
        # So sánh chuỗi ISO được vì cùng một múi giờ thì thứ tự chuỗi trùng thứ
        # tự thời gian; mốc lọc không kèm offset nên cắt phần offset khi so.
        if occurred_from:
            events = [e for e in events if str(e.get("occurred_at", ""))[:19] >= occurred_from[:19]]
        if occurred_to:
            events = [e for e in events if str(e.get("occurred_at", ""))[:19] <= occurred_to[:19]]
        # "Vùng cấm" (restricted_area) hay "Vạch an toàn" (crossing_line) — lưu
        # trong detail.zone_rule lúc bắn sự kiện INTRUSION (xem video_processor.py)
        if zone_rule:
            events = [e for e in events if (e.get("detail") or {}).get("zone_rule") == zone_rule]
        return events[offset:offset + limit], len(events)

    def since(self, event_id: str, types=None, camera_id: Optional[str] = None) -> List[dict]:
        """Các sự kiện phát sinh SAU ``event_id``, cũ trước mới sau.

        Dùng khi client SSE nối lại: phát bù phần đã bỏ lỡ rồi mới chuyển sang
        luồng trực tiếp. Không tìm thấy mốc thì coi như không bỏ lỡ gì, tránh
        dội lại toàn bộ lịch sử.
        """
        events = read_json_list(self.events_file)
        index = next((i for i, e in enumerate(events) if e.get("id") == event_id), None)
        if index is None:
            return []

        missed = list(reversed(events[:index]))
        if types:
            missed = [e for e in missed if e.get("type") in types]
        if camera_id:
            missed = [e for e in missed if e.get("camera_id") == camera_id]
        return missed

    def get(self, event_id: str) -> Optional[dict]:
        return next((e for e in read_json_list(self.events_file) if e.get("id") == event_id), None)

    def ack(self, event_id: str, acked_by: str, note: Optional[str] = None) -> Optional[dict]:
        """Đánh dấu đã xử lý. Trả về ``None`` nếu không tìm thấy sự kiện."""
        events = read_json_list(self.events_file)
        for event in events:
            if event.get("id") != event_id:
                continue
            event["acked"] = True
            event["acked_by"] = acked_by
            event["acked_at"] = clock.iso()
            event["ack_note"] = note
            write_json_list(self.events_file, events)
            return event
        return None

    def pending_count(self) -> int:
        return sum(1 for e in read_json_list(self.events_file) if not e.get("acked"))
