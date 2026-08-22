"""Person detection module using YOLO v8/v11 with multi-object tracking."""

import time
from ultralytics import YOLO
import numpy as np


def _iou(a, b) -> float:
    """Tỉ lệ chồng lấn giữa hai bounding box."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


class PersonDetector:
    """Detects and tracks persons in video frames using YOLO."""

    # Track phải xuất hiện đủ số lượt này mới được giữ lại khi bị che
    MIN_HITS_TO_HOLD = 3
    # Giữ lại track bị che tối đa bao nhiêu giây
    OCCLUSION_GRACE_SECONDS = 5.0
    # Box giữ lại trùng với box đang thấy quá mức này thì bỏ, tránh đếm đôi
    HOLD_DUPLICATE_IOU = 0.45
    # Track biến mất sát mép khung hình là người đi ra ngoài, không phải bị che
    BORDER_MARGIN_PX = 24

    def __init__(
        self,
        model_name: str = 'yolo11s.pt',
        conf: float = 0.25,
        imgsz: int = 1280,
        occlusion_grace: float = OCCLUSION_GRACE_SECONDS
    ):
        """
        Initialize the person detector.

        Args:
            model_name: YOLO model to use
            conf: Ngưỡng tin cậy. Hạ thấp hơn mặc định vì có tracking lọc nhiễu.
            imgsz: Kích thước ảnh đưa vào model
            occlusion_grace: Số giây tiếp tục tính một người đã bị vật che
        """
        self.model = YOLO(model_name)
        # COCO dataset class 0 is 'person'
        self.person_class_id = 0
        self.conf = conf
        self.imgsz = imgsz
        self.occlusion_grace = occlusion_grace
        # track_id -> {box, conf, hits, last_seen}
        self._tracks: dict = {}

    def reset(self) -> None:
        """Xoá trạng thái tracking khi bắt đầu một luồng video mới."""
        self._tracks.clear()
        try:
            for tracker in getattr(self.model.predictor, "trackers", []) or []:
                tracker.reset()
        except Exception:
            pass

    def _touches_border(self, box, frame_shape) -> bool:
        """Box nằm sát mép khung hình."""
        h, w = frame_shape[:2]
        x1, y1, x2, y2 = box
        m = self.BORDER_MARGIN_PX
        return x1 <= m or y1 <= m or x2 >= w - m or y2 >= h - m

    def detect_persons(self, frame: np.ndarray) -> dict:
        """
        Detect and track persons in a video frame.

        Người đang bị đồ vật che khuất vẫn được giữ trong kết quả (cờ ``occluded``)
        trong ``occlusion_grace`` giây để sĩ số không tụt mỗi khi model miss.

        Returns:
            Dictionary with count, boxes, confidences, track_ids, occluded flags
        """
        results = self.model.track(
            frame,
            classes=[self.person_class_id],
            conf=self.conf,
            imgsz=self.imgsz,
            tracker="bytetrack.yaml",
            persist=True,
            verbose=False
        )

        now = time.monotonic()
        boxes, confidences, track_ids = [], [], []
        seen_ids = set()
        fallback_id = -1

        for result in results:
            if result.boxes is None:
                continue
            ids = result.boxes.id
            ids = ids.int().tolist() if ids is not None else [None] * len(result.boxes)
            for box, tid in zip(result.boxes, ids):
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                bbox = (int(x1), int(y1), int(x2), int(y2))
                conf = box.conf[0].item()

                if tid is None:
                    # Chưa được gán ID: vẫn hiển thị nhưng không đưa vào bộ nhớ che khuất
                    boxes.append(bbox)
                    confidences.append(conf)
                    track_ids.append(fallback_id)
                    fallback_id -= 1
                    continue

                seen_ids.add(tid)
                entry = self._tracks.get(tid)
                hits = (entry["hits"] + 1) if entry else 1
                self._tracks[tid] = {"box": bbox, "conf": conf, "hits": hits, "last_seen": now}

                boxes.append(bbox)
                confidences.append(conf)
                track_ids.append(tid)

        # Giữ lại các track vừa biến mất (bị che) đủ tin cậy
        occluded = [False] * len(boxes)
        for tid, entry in list(self._tracks.items()):
            age = now - entry["last_seen"]
            if age > self.occlusion_grace:
                del self._tracks[tid]
                continue
            if tid in seen_ids:
                continue
            if entry["hits"] < self.MIN_HITS_TO_HOLD:
                continue
            if self._touches_border(entry["box"], frame.shape):
                # Đi ra khỏi khung hình chứ không phải bị vật che
                del self._tracks[tid]
                continue
            if any(_iou(entry["box"], b) >= self.HOLD_DUPLICATE_IOU for b in boxes):
                continue
            boxes.append(entry["box"])
            confidences.append(entry["conf"])
            track_ids.append(tid)
            occluded.append(True)

        return {
            'count': len(boxes),
            'boxes': boxes,
            'confidences': confidences,
            'track_ids': track_ids,
            'occluded': occluded
        }
