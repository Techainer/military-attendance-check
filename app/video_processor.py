"""Video processing pipeline for attendance monitoring and face recognition with Horus HUD and 10s clip buffer."""

import cv2
import base64
import asyncio
import threading
import time
import json
import numpy as np
from pathlib import Path
from collections import deque
from datetime import datetime
from typing import Optional, List, Dict, Tuple

from app.detector import PersonDetector
from app.monitor import AttendanceMonitor
from app.face_engine import FaceEngine
from app.attendance import AttendanceManager


# Global rolling buffer for 10-second clip replay (stores last 60 frames ~ 12 seconds at 5fps)
global_clip_buffer = deque(maxlen=60)
latest_event_clips: Dict[str, List[str]] = {}


def _bbox_center(bbox) -> Tuple[float, float]:
    """Tâm của một bounding box, dạng float cho cv2.pointPolygonTest."""
    x1, y1, x2, y2 = bbox
    return ((float(x1) + float(x2)) / 2.0, (float(y1) + float(y2)) / 2.0)


class RTSPStreamReader:
    """Continuously reads frames from an RTSP / network stream in a background thread."""

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url
        self.cap = cv2.VideoCapture(rtsp_url)
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
        attendance: Optional[AttendanceManager] = None
    ):
        self.detector = PersonDetector(model_name="yolo11s.pt")
        self.face_engine = face_engine or FaceEngine()
        self.attendance = attendance
        self.fps = fps
        self._stop_requested = False

    def stop(self) -> None:
        """Request the processing pipeline to stop."""
        self._stop_requested = True

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

                # 1. Person Detection via YOLO
                detection = self.detector.detect_persons(frame)
                person_boxes = detection['boxes']
                confidences = detection['confidences']

                # 2. Clean Frame + Annotated HUD Frame
                h_img, w_img = frame.shape[:2]
                display_frame = frame.copy()

                # Load Dynamic Zone Rules from data/zone_rules.json
                zone_file = Path("data/zone_rules.json")
                np_pts = None
                if zone_file.exists():
                    try:
                        with open(zone_file, "r", encoding="utf-8") as f:
                            z_data = json.load(f)
                            # Draw polygon
                            poly_pts = z_data.get("polygon_points", [])
                            if len(poly_pts) >= 3:
                                raw_pts = [[int(pt["x"] * w_img), int(pt["y"] * h_img)] for pt in poly_pts]
                                np_pts = np.array(raw_pts, np.int32)
                                # Semi-transparent green polygon fill + solid boundary
                                overlay = display_frame.copy()
                                cv2.fillPoly(overlay, [np_pts], (20, 160, 60))
                                cv2.addWeighted(overlay, 0.15, display_frame, 0.85, 0, display_frame)
                                cv2.polylines(display_frame, [np_pts], isClosed=True, color=(30, 220, 90), thickness=2)
                            
                            # Draw tripwire
                            tw_pts = z_data.get("tripwire_points", [])
                            if len(tw_pts) >= 2:
                                p1 = (int(tw_pts[0]["x"] * w_img), int(tw_pts[0]["y"] * h_img))
                                p2 = (int(tw_pts[1]["x"] * w_img), int(tw_pts[1]["y"] * h_img))
                                cv2.line(display_frame, p1, p2, (0, 140, 255), 2, cv2.LINE_AA)
                    except Exception:
                        pass
                
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

                # 4. Nhận diện khuôn mặt — chỉ chạy trên người TRONG vùng
                in_zone_boxes = [person_boxes[i] for i in sorted(in_zone_indices)]
                recognized_faces = self.face_engine.recognize_faces_in_frame(
                    frame, person_boxes=in_zone_boxes
                )
                # Lượt quét toàn khung có thể bắt được mặt ngoài vùng -> loại bỏ
                recognized_faces = [
                    rf for rf in recognized_faces
                    if cv2.pointPolygonTest(np_pts, _bbox_center(rf['bbox']), False) >= 0
                ]

                present_personnel = []
                seen_ids = set()
                person_to_face_match = {}
                claimed_boxes = set()

                for rf in recognized_faces:
                    if not (rf.get("is_recognized") and rf.get("person")):
                        continue

                    p_info = rf["person"]
                    if p_info["id"] not in seen_ids:
                        seen_ids.add(p_info["id"])
                        present_personnel.append(p_info)

                    # Gán mặt vào box người NHỎ NHẤT có chứa mặt; mỗi box chỉ nhận một người
                    fcx, fcy = _bbox_center(rf['bbox'])
                    best_idx = None
                    best_area = None
                    for p_idx in in_zone_indices:
                        if p_idx in claimed_boxes:
                            continue
                        px1, py1, px2, py2 = person_boxes[p_idx]
                        if not (px1 - 20 <= fcx <= px2 + 20 and py1 - 20 <= fcy <= py2 + 40):
                            continue
                        area = max(1, (px2 - px1) * (py2 - py1))
                        if best_area is None or area < best_area:
                            best_area = area
                            best_idx = p_idx

                    if best_idx is not None:
                        person_to_face_match[best_idx] = p_info
                        claimed_boxes.add(best_idx)

                # Draw Person Bounding Boxes with In-Zone / Out-of-Zone distinction
                for p_idx, (box, conf) in enumerate(zip(person_boxes, confidences)):
                    x1, y1, x2, y2 = box
                    is_in_zone = p_idx in in_zone_indices

                    if is_in_zone:
                        if p_idx in person_to_face_match:
                            # RECOGNIZED SOLDIER IN ZONE: Bright glowing green box & identification banner
                            p_info = person_to_face_match[p_idx]
                            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 120), 2)
                            
                            rank_str = p_info.get('rank', '')
                            name_str = p_info.get('name', '')
                            mid_str = p_info.get('military_id', '')
                            id_label = f"[{rank_str}] {name_str} ({mid_str})" if rank_str else f"{name_str} ({mid_str})"
                            
                            (tw, th), _ = cv2.getTextSize(id_label, cv2.FONT_HERSHEY_DUPLEX, 0.45, 1)
                            cv2.rectangle(display_frame, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, y1), (0, 180, 70), -1)
                            cv2.putText(
                                display_frame,
                                id_label,
                                (x1 + 3, max(12, y1 - 4)),
                                cv2.FONT_HERSHEY_DUPLEX,
                                0.45,
                                (255, 255, 255),
                                1
                            )
                        else:
                            # UNIDENTIFIED SOLDIER IN ZONE: Active green detection box
                            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (20, 220, 80), 2)
                            tag_text = f"Trong vung: {int(conf * 100)}%"
                            (tw, th), _ = cv2.getTextSize(tag_text, cv2.FONT_HERSHEY_DUPLEX, 0.4, 1)
                            cv2.rectangle(display_frame, (x1, max(0, y1 - th - 6)), (x1 + tw + 4, y1), (10, 140, 50), -1)
                            cv2.putText(
                                display_frame,
                                tag_text,
                                (x1 + 2, max(10, y1 - 3)),
                                cv2.FONT_HERSHEY_DUPLEX,
                                0.4,
                                (255, 255, 255),
                                1
                            )
                    else:
                        # SOLDIER OUTSIDE ZONE (FAR AWAY): Subtle grey/dimmed box, not counted
                        cv2.rectangle(display_frame, (x1, y1), (x2, y2), (120, 130, 140), 1)
                        tag_text = "Ngoai vung"
                        (tw, th), _ = cv2.getTextSize(tag_text, cv2.FONT_HERSHEY_DUPLEX, 0.35, 1)
                        cv2.rectangle(display_frame, (x1, max(0, y1 - th - 4)), (x1 + tw + 4, y1), (60, 65, 75), -1)
                        cv2.putText(
                            display_frame,
                            tag_text,
                            (x1 + 2, max(8, y1 - 2)),
                            cv2.FONT_HERSHEY_DUPLEX,
                            0.35,
                            (200, 205, 215),
                            1
                        )

                # Draw any standalone recognized face tags
                for rf in recognized_faces:
                    fx1, fy1, fx2, fy2 = rf['bbox']
                    if rf['is_recognized'] and rf['person']:
                        cv2.rectangle(display_frame, (fx1, fy1), (fx2, fy2), (0, 255, 120), 2)

                # 4. Top & Bottom HUD Overlays
                cv2.circle(display_frame, (25, 28), 6, (0, 0, 240), -1)
                cv2.putText(display_frame, "CAM-01 . SAN TAP TRUNG", (38, 33), cv2.FONT_HERSHEY_DUPLEX, 0.55, (230, 230, 230), 1)

                att_status = self.attendance.status(datetime.now()) if self.attendance else {"active": False}

                baseline_val = monitor.baseline_count
                if baseline_val is None and att_status.get("active"):
                    baseline_val = att_status.get("roster_size", 0)
                if baseline_val is None:
                    baseline_val = 0

                hud_stat = f"CHUAN: {baseline_val} | TRONG VUNG: {person_count} | DINH DANH: {len(present_personnel)}"
                (hw, hh), _ = cv2.getTextSize(hud_stat, cv2.FONT_HERSHEY_DUPLEX, 0.5, 1)
                cv2.rectangle(display_frame, (w_img - hw - 30, 12), (w_img - 10, 44), (15, 30, 20), -1)
                cv2.rectangle(display_frame, (w_img - hw - 30, 12), (w_img - 10, 44), (20, 180, 70), 1)
                cv2.putText(display_frame, hud_stat, (w_img - hw - 20, 32), cv2.FONT_HERSHEY_DUPLEX, 0.5, (0, 255, 120), 1)

                if att_status.get("active"):
                    remain = int(att_status.get("remaining_seconds", 0))
                    roll_label = f"DANG DIEM DANH . CON {remain // 60:02d}:{remain % 60:02d} . CO MAT {att_status.get('present', 0)}/{att_status.get('roster_size', 0)}"
                    (rw, rh), _ = cv2.getTextSize(roll_label, cv2.FONT_HERSHEY_DUPLEX, 0.5, 1)
                    cv2.rectangle(display_frame, (14, 52), (30 + rw, 84), (10, 45, 90), -1)
                    cv2.rectangle(display_frame, (14, 52), (30 + rw, 84), (0, 170, 255), 1)
                    cv2.putText(display_frame, roll_label, (22, 73), cv2.FONT_HERSHEY_DUPLEX, 0.5, (0, 200, 255), 1)

                rtsp_label = "RTSP://10.24.4.1:554 . QUANG HOC" if is_rtsp else f"TIEP VIDEO: {Path(video_path).name} . QUANG HOC"
                cv2.putText(display_frame, rtsp_label, (20, h_img - 18), cv2.FONT_HERSHEY_DUPLEX, 0.45, (160, 175, 190), 1)

                # Encode to base64 JPEG
                _, buffer = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')

                # Store into global 10-second rolling clip buffer
                global_clip_buffer.append(frame_b64)

                # Send frame update over WebSocket
                await on_update({
                    'type': 'frame_update',
                    'frame': frame_b64,
                    'count': person_count,
                    'baseline': baseline_val,
                    'recognized_count': len(present_personnel),
                    'unidentified_count': max(0, person_count - len(present_personnel)),
                    'present_personnel': present_personnel,
                    'attendance': att_status,
                    'boxes': person_boxes,
                    'frame_number': frame_count,
                    'is_processing': True
                })

                # Phiên điểm danh: mở theo thời khoá biểu, gom người nhận diện được, chốt khi hết giờ
                if self.attendance is not None:
                    now = datetime.now()
                    if self.attendance.session is None:
                        self.attendance.maybe_open_scheduled(now)
                    if self.attendance.session is not None:
                        self.attendance.record([p["id"] for p in present_personnel])
                    closed_log = self.attendance.close_if_due(now)
                    if closed_log:
                        await on_update({'type': 'attendance_complete', 'log': closed_log})

                # Check for baseline drop alerts
                alert_result = monitor.update_count(person_count, frame)
                if alert_result['alert_triggered']:
                    event_clip_id = f"clip_{int(time.time())}"
                    latest_event_clips[event_clip_id] = list(global_clip_buffer)

                    await on_update({
                        'type': 'alert',
                        'category': 'ABSENT',
                        'clip_id': event_clip_id,
                        **alert_result['alert_data']
                    })
                elif alert_result.get('recovered'):
                    await on_update({
                        'type': 'system_event',
                        'category': 'SYSTEM',
                        'message': f'Quân số đã đủ trở lại ({person_count} quân nhân)',
                        'timestamp': datetime.now().isoformat()
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

            if self.attendance is not None and self.attendance.session is not None:
                closed_log = self.attendance.close_if_due(datetime.now(), force=True)
                if closed_log:
                    await on_update({'type': 'attendance_complete', 'log': closed_log})

            await on_update({
                'type': 'processing_complete',
                'total_frames': frame_count,
                'is_processing': False
            })
