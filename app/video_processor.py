"""Video processing pipeline for attendance monitoring and face recognition with Horus HUD and 10s clip buffer."""

import cv2
import base64
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


# Global rolling buffer for 10-second clip replay (stores last 60 frames ~ 12 seconds at 5fps)
global_clip_buffer = deque(maxlen=60)
latest_event_clips: Dict[str, List[str]] = {}
# Mỗi đoạn giữ ~60 khung base64 (vài MB), nên chỉ giữ lại bấy nhiêu đoạn gần
# nhất; luồng chạy cả ngày mà không dọn thì bộ nhớ phình theo số sự kiện.
MAX_STORED_CLIPS = 20


# Khung hình JPEG mới nhất, phục vụ luồng MJPEG và ảnh chụp nhanh. Giữ cả bản
# có lớp phủ AI lẫn bản gốc để tham số ``overlay`` là thật, không phải chỉ đổi
# nhãn nút bấm trên giao diện.
latest_jpeg: Dict[str, Optional[bytes]] = {"overlay": None, "clean": None}
# Tăng mỗi khung hình mới; luồng MJPEG dựa vào đây để biết đã có hình mới chưa
# thay vì gửi lại đúng một khung nhiều lần.
frame_revision = 0


def publish_frame(overlay_jpeg: bytes, clean_jpeg: Optional[bytes]) -> None:
    global frame_revision
    latest_jpeg["overlay"] = overlay_jpeg
    latest_jpeg["clean"] = clean_jpeg
    frame_revision += 1


def clear_frames() -> None:
    """Luồng dừng thì bỏ khung hình cũ, tránh phát lại hình đã chết."""
    latest_jpeg["overlay"] = None
    latest_jpeg["clean"] = None


def keep_clip(clip_id: str, frames: List[str]) -> str:
    """Lưu một đoạn phát lại và bỏ bớt các đoạn cũ nhất."""
    latest_event_clips[clip_id] = frames
    for stale in list(latest_event_clips)[:-MAX_STORED_CLIPS]:
        del latest_event_clips[stale]
    return clip_id


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


class RTSPStreamReader:
    """Continuously reads frames from an RTSP / network stream in a background thread."""

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url
        self.cap = cv2.VideoCapture(rtsp_url)
        # Giữ hàng đợi giải mã ở mức tối thiểu để khung hình luôn là mới nhất,
        # nếu không hình trên màn hình sẽ trễ dần so với thời gian thực.
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self.ret = False
        self.frame = None
        self.started = False
        self.read_lock = threading.Lock()
        self.thread = None

    def start(self):
        if self.started:
            return self
        if not self.cap.isOpened():
            print(f"Error: Unable to open RTSP stream source: {self.rtsp_url}")
            return self
        self.started = True
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()
        return self

    def update(self):
        while self.started:
            if not self.cap.isOpened():
                time.sleep(0.1)
                continue
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            with self.read_lock:
                self.ret = ret
                self.frame = frame
            time.sleep(0.005)

    def read(self):
        with self.read_lock:
            if self.frame is None:
                return False, None
            return self.ret, self.frame.copy()

    def stop(self):
        self.started = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap.isOpened():
            self.cap.release()


class VideoProcessor:
    """Processes video frames for person detection, face recognition, and attendance monitoring."""

    def __init__(
        self,
        fps: int = 5,
        face_engine: Optional[FaceEngine] = None,
        attendance: Optional[AttendanceManager] = None,
        events: Optional[EventStore] = None,
        data_dir: str = "data"
    ):
        self.detector = PersonDetector(model_name=os.environ.get("YOLO_MODEL", "yolo11s.pt"))
        self.face_engine = face_engine or FaceEngine()
        self.attendance = attendance
        self.events = events
        self.zones = ZoneStore(data_dir)
        self.intrusion = IntrusionDetector(self.zones)
        self.fps = fps
        self._stop_requested = False
        # track_id -> {"scores": {person_id: điểm cộng dồn}, "person": dict, "last_seen": ts}
        self.track_identity: Dict[int, dict] = {}

    def stop(self) -> None:
        """Request the processing pipeline to stop."""
        self._stop_requested = True

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

    # ---------- vòng xử lý ----------

    async def process_video(
        self,
        video_path: str,
        on_update,
        monitor: AttendanceMonitor
    ) -> None:
        """
        Process video file or RTSP stream and stream results via callback.
        """
        global global_clip_buffer, latest_event_clips
        is_rtsp = "://" in video_path
        reader = None
        cap = None

        self.detector.reset()
        self.track_identity.clear()

        if is_rtsp:
            print(f"Connecting to RTSP/Network stream: {video_path}")
            reader = RTSPStreamReader(video_path)
            reader.start()
            if not reader.started:
                await on_update({
                    'type': 'error',
                    'message': 'Không thể kết nối tới luồng camera RTSP'
                })
                return
        else:
            print(f"Opening video file: {video_path}")
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                await on_update({
                    'type': 'error',
                    'message': 'Không thể mở tệp video'
                })
                return

        if not is_rtsp:
            video_fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if video_fps == 0:
                video_fps = 30
            frame_skip = max(1, int(video_fps / self.fps))
        else:
            video_fps = 30
            frame_skip = 1

        frame_count = 0
        empty_frames_count = 0

        try:
            while not self._stop_requested:
                if is_rtsp:
                    if not reader.thread or not reader.thread.is_alive():
                        break

                    ret, frame = reader.read()
                    if not ret or frame is None:
                        empty_frames_count += 1
                        if empty_frames_count > 100:
                            await on_update({
                                'type': 'error',
                                'message': 'Mất tín hiệu camera RTSP'
                            })
                            break
                        await asyncio.sleep(0.05)
                        continue
                    else:
                        empty_frames_count = 0
                else:
                    if not cap.isOpened():
                        break
                    ret, frame = cap.read()
                    if not ret:
                        break

                frame_count += 1

                if not is_rtsp and frame_count % frame_skip != 0:
                    continue

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

                # 2. Clean Frame + Annotated HUD Frame
                h_img, w_img = frame.shape[:2]
                display_frame = frame.copy()

                # Vẽ các vùng đã cấu hình. Vùng đếm quân số màu xanh, vùng cấm màu
                # đỏ, vạch an toàn màu cam.
                np_pts = None
                for zone in self.zones.zones():
                    points = zone.get("points", [])
                    rule = zone.get("rule")
                    if rule == RULE_CROSSING and len(points) >= 2:
                        p1 = (int(points[0]["x"] * w_img), int(points[0]["y"] * h_img))
                        p2 = (int(points[1]["x"] * w_img), int(points[1]["y"] * h_img))
                        cv2.line(display_frame, p1, p2, (0, 140, 255), 2, cv2.LINE_AA)
                        continue
                    if len(points) < 3:
                        continue

                    pts = np.array([[int(p["x"] * w_img), int(p["y"] * h_img)] for p in points], np.int32)
                    fill, border = ((20, 160, 60), (30, 220, 90)) if rule == RULE_ATTENDANCE \
                        else ((0, 0, 200), (0, 60, 255))
                    overlay = display_frame.copy()
                    cv2.fillPoly(overlay, [pts], fill)
                    cv2.addWeighted(overlay, 0.15, display_frame, 0.85, 0, display_frame)
                    cv2.polylines(display_frame, [pts], isClosed=True, color=border, thickness=2)
                    if rule == RULE_ATTENDANCE:
                        np_pts = pts
                    elif rule == RULE_RESTRICTED:
                        draw_label(display_frame, f"VÙNG CẤM · {zone.get('name', '')}",
                                   (int(pts[:, 0].mean()), int(pts[:, 1].min())), font_size=14,
                                   bg_color=(0, 40, 160), anchor="bottom")

                if np_pts is None:
                    # Default Fallback Zone
                    raw_pts = [
                        [int(w_img * 0.08), int(h_img * 0.75)],
                        [int(w_img * 0.35), int(h_img * 0.60)],
                        [int(w_img * 0.70), int(h_img * 0.62)],
                        [int(w_img * 0.92), int(h_img * 0.85)],
                        [int(w_img * 0.12), int(h_img * 0.90)]
                    ]
                    np_pts = np.array(raw_pts, np.int32)
                    cv2.polylines(display_frame, [np_pts], isClosed=True, color=(30, 220, 90), thickness=2)

                # 3. Filter persons inside ROI zone
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

                # 3b. Giám sát an toàn: ai vào vùng cấm hoặc vượt vạch an toàn
                now = clock.now()
                intrusions = self.intrusion.check(person_boxes, track_ids, w_img, h_img, now)
                intrusion_indices = {idx for v in intrusions for idx in v["indices"]}

                # 4. Gắn khuôn mặt đã quét được vào người trong vùng
                now_mono = time.monotonic()
                claimed_boxes = set()
                assignments: Dict[int, dict] = {}
                matched_face_boxes = self._assign_faces(
                    recognized_faces, person_boxes, in_zone_indices, claimed_boxes, assignments
                )

                # 4b. Người trong vùng còn lại: cắt riêng vùng ảnh rồi quét lại. Khuôn mặt
                #     ở xa chỉ vài chục pixel trên khung hình đầy đủ nên lượt quét chung
                #     hay bỏ sót, cắt ra quét lại thì được phóng to lên det_size.
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

                # Draw Person Bounding Boxes with In-Zone / Out-of-Zone distinction
                for p_idx, (box, conf) in enumerate(zip(person_boxes, confidences)):
                    x1, y1, x2, y2 = box
                    is_in_zone = p_idx in in_zone_indices
                    is_occluded = occluded_flags[p_idx]

                    if p_idx in intrusion_indices:
                        # VI PHẠM AN TOÀN: khoanh đỏ đậm, ưu tiên hơn mọi trạng thái khác
                        cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
                        draw_label(display_frame, "VI PHẠM AN TOÀN", (x1, y1), font_size=15,
                                      bg_color=(0, 0, 200), anchor="bottom")
                    elif is_in_zone:
                        if p_idx in person_to_face_match:
                            # RECOGNIZED SOLDIER IN ZONE: Bright glowing green box & identification banner
                            p_info = person_to_face_match[p_idx]
                            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 120), 2)

                            rank_str = p_info.get('rank', '')
                            name_str = p_info.get('name', '')
                            mid_str = p_info.get('military_id', '')
                            id_label = f"[{rank_str}] {name_str} ({mid_str})" if rank_str else f"{name_str} ({mid_str})"
                            draw_label(display_frame, id_label, (x1, y1), font_size=15, bg_color=(0, 180, 70), anchor="bottom")
                        elif is_occluded:
                            # BỊ VẬT CHE: vẫn tính vào sĩ số, vẽ nét đứt màu vàng để phân biệt
                            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 190, 230), 1)
                            draw_label(display_frame, "Bị che khuất", (x1, y1), font_size=14,
                                          bg_color=(0, 120, 150), anchor="bottom")
                        else:
                            # UNIDENTIFIED SOLDIER IN ZONE: Active green detection box
                            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (20, 220, 80), 2)
                            draw_label(display_frame, f"Trong vùng {int(conf * 100)}%", (x1, y1), font_size=14,
                                          bg_color=(10, 140, 50), anchor="bottom")
                    else:
                        # SOLDIER OUTSIDE ZONE (FAR AWAY): Subtle grey/dimmed box, not counted
                        cv2.rectangle(display_frame, (x1, y1), (x2, y2), (120, 130, 140), 1)
                        draw_label(display_frame, "Ngoài vùng", (x1, y1), font_size=13,
                                      text_color=(200, 205, 215), bg_color=(60, 65, 75), anchor="bottom")

                # Draw any standalone recognized face tags
                for fbox in matched_face_boxes:
                    fx1, fy1, fx2, fy2 = fbox
                    cv2.rectangle(display_frame, (fx1, fy1), (fx2, fy2), (0, 255, 120), 2)

                # Vi phạm an toàn được chốt sau khi đã vẽ xong khung đỏ, để ảnh
                # bằng chứng nhìn thấy đúng đối tượng bị phát hiện.
                for violation in intrusions:
                    if self.events is None:
                        break
                    zone = violation["zone"]
                    # Người vi phạm mà đã nhận diện được thì ghi tên vào biên bản
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
                    clip_id = keep_clip(f"clip_{int(time.time())}_{zone.get('id', 'zone')}",
                                        list(global_clip_buffer))

                    event = self.events.emit(
                        TYPE_INTRUSION,
                        f"Phát hiện {len(boxes):02d} đối tượng "
                        f"{'vượt' if zone.get('rule') == RULE_CROSSING else 'đi vào'} "
                        f"{zone.get('name', 'vùng cấm')}.",
                        severity="critical",
                        frame=display_frame,
                        boxes=boxes,
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

                # Phiên điểm danh: mở theo thời khoá biểu (đầu giờ / cuối giờ)
                if self.attendance is not None and self.attendance.session is None:
                    self.attendance.maybe_open_scheduled(now)

                # 5. Top & Bottom HUD Overlays
                cv2.circle(display_frame, (25, 28), 6, (0, 0, 240), -1)
                draw_label(display_frame, "CAM-01 · SÂN TẬP TRUNG", (38, 18), font_size=16,
                              text_color=(230, 230, 230), bg_color=(18, 22, 28))

                # Dấu thời gian của máy chủ (giờ Việt Nam) để đối chiếu với đồng hồ camera
                draw_label(display_frame, now.strftime("%d/%m/%Y %H:%M:%S"), (20, 46), font_size=16,
                              text_color=(200, 215, 230), bg_color=(18, 22, 28))

                att_status = self.attendance.status(now) if self.attendance else {"active": False}

                baseline_val = monitor.baseline_count
                if baseline_val is None and att_status.get("active"):
                    baseline_val = att_status.get("required", att_status.get("roster_size", 0))
                if baseline_val is None:
                    baseline_val = 0

                hud_stat = (f"Chuẩn {baseline_val}  ·  Trong vùng {person_count}"
                            f"  ·  Định danh {len(present_personnel)}")
                # x vượt mép phải: nhãn được kéo về canh sát lề phải
                draw_label(display_frame, hud_stat, (w_img, 14), font_size=17,
                              text_color=(0, 255, 120), bg_color=(15, 30, 20))

                if att_status.get("active"):
                    remain = int(att_status.get("remaining_seconds", 0))
                    roll_label = (f"ĐIỂM DANH {att_status.get('phase_label', '').upper()}"
                                  f"  ·  còn {remain // 60:02d}:{remain % 60:02d}"
                                  f"  ·  có mặt {att_status.get('present', 0)}/{att_status.get('required', 0)}")
                    draw_label(display_frame, roll_label, (16, 78), font_size=17,
                                  text_color=(0, 200, 255), bg_color=(10, 45, 90))

                source_label = ("Nguồn: RTSP · quang học" if is_rtsp
                                else f"Nguồn: {Path(video_path).name} · quang học")
                draw_label(display_frame, source_label, (18, h_img - 12), font_size=14,
                              text_color=(160, 175, 190), bg_color=(18, 22, 28), anchor="bottom")

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
                        quality=quality
                    )
                    closed_log = self.attendance.close_if_due(now)
                    if closed_log:
                        await on_update({'type': 'attendance_complete', 'log': closed_log})

                # Encode to base64 JPEG
                _, buffer = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')

                # Khung hình cho luồng MJPEG: bản có lớp phủ và bản gốc
                _, clean_buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                publish_frame(buffer.tobytes(), clean_buffer.tobytes())

                # Store into global 10-second rolling clip buffer
                global_clip_buffer.append(frame_b64)

                # Send frame update over WebSocket
                await on_update({
                    'type': 'frame_update',
                    'camera_id': CAMERA_ID,
                    'frame': frame_b64,
                    'count': person_count,
                    'baseline': baseline_val,
                    'recognized_count': len(present_personnel),
                    'unidentified_count': max(0, person_count - len(present_personnel)),
                    'present_personnel': present_personnel,
                    'attendance': att_status,
                    'boxes': person_boxes,
                    'frame_number': frame_count,
                    'server_time': now.isoformat(),
                    'is_processing': True
                })

                # Check for baseline drop alerts
                alert_result = monitor.update_count(person_count, frame)
                if alert_result['alert_triggered']:
                    event_clip_id = keep_clip(f"clip_{int(time.time())}",
                                              list(global_clip_buffer))
                    alert_data = alert_result['alert_data']

                    if self.events is not None:
                        baseline = alert_data.get('baseline') or 0
                        event = self.events.emit(
                            TYPE_ABSENT,
                            f"Thiếu {max(0, baseline - person_count):02d} quân nhân so với "
                            f"sĩ số chuẩn ({person_count}/{baseline}).",
                            severity="warning",
                            frame=display_frame,
                            clip_id=event_clip_id,
                            detail={
                                "current_count": person_count,
                                "required_count": baseline,
                                "missing_count": max(0, baseline - person_count),
                                "duration_seconds": monitor.ALERT_THRESHOLD_SECONDS,
                            },
                        )
                        await on_update({"type": "event", "event": event})

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

                await asyncio.sleep(1.0 / self.fps)

        except Exception as e:
            print(f"Error processing video: {e}")
            await on_update({
                'type': 'error',
                'message': f'Lỗi luồng xử lý video: {str(e)}'
            })
        finally:
            if is_rtsp and reader:
                reader.stop()
            elif cap:
                cap.release()

            clear_frames()

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
