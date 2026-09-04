"""Kho sự kiện dùng chung cho mọi loại cảnh báo.

Trước đây mỗi loại cảnh báo đi một đường riêng: thiếu quân số bắn qua WebSocket
dưới dạng ``alert``, sự kiện hệ thống dưới dạng ``system_event``, còn vi phạm an
toàn thì chưa có. Module này gom tất cả về một cấu trúc duy nhất theo
``docs/api/events.schema.json`` để dòng sự kiện, thư viện ảnh vi phạm và nút xác
nhận xử lý cùng đọc một nguồn.
"""

import asyncio
from pathlib import Path
from typing import List, Optional

import base64

import cv2
import numpy as np

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

# Vòng xử lý chạy 5 khung/giây nên đoạn ghi phát lại cũng ở nhịp đó
CLIP_FPS = 5


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

    def save_clip(self, event_id: str, frames: List[str]) -> Optional[str]:
        """Dựng đoạn mp4 từ các khung base64 và lưu cạnh ảnh bằng chứng.

        Bộ đệm khung hình chỉ nằm trong RAM và bị dọn dần, nên khởi động lại máy
        chủ là mọi nút "Xem clip" trong nhật ký cũ đều chết. Ghi ra file ngay lúc
        sự kiện xảy ra thì đoạn ghi sống cùng ảnh bằng chứng.
        """
        if not frames:
            return None

        clip_file = self.snapshot_dir / f"{event_id}.mp4"
        if clip_file.exists() and clip_file.stat().st_size > 0:
            return f"/api/v1/events/{event_id}/clip"

        try:
            decoded = []
            for frame_b64 in frames:
                img = cv2.imdecode(np.frombuffer(base64.b64decode(frame_b64), np.uint8),
                                   cv2.IMREAD_COLOR)
                if img is not None:
                    decoded.append(img)
            if not decoded:
                return None

            h, w = decoded[0].shape[:2]
            writer = cv2.VideoWriter(str(clip_file), cv2.VideoWriter_fourcc(*"mp4v"),
                                     CLIP_FPS, (w, h))
            for img in decoded:
                # Khung lệch kích thước bị VideoWriter bỏ qua âm thầm
                if img.shape[:2] == (h, w):
                    writer.write(img)
            writer.release()
        except Exception as e:
            print(f"[Events] Lỗi dựng đoạn ghi: {e}")
            clip_file.unlink(missing_ok=True)
            return None

        if not clip_file.exists() or clip_file.stat().st_size == 0:
            clip_file.unlink(missing_ok=True)
            return None
        return f"/api/v1/events/{event_id}/clip"

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
        """Tạo một sự kiện, lưu xuống file và đẩy cho client. Trả về sự kiện đã tạo."""
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
            "message": message,
            "snapshot_url": self._save_snapshot(frame, event_id),
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

        events = read_json_list(self.events_file)
        events.insert(0, event)
        write_json_list(self.events_file, events[:MAX_STORED_EVENTS])

        self._publish(event)
        print(f"[Events] {event_type}: {message}")
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
