"""Person detection module using YOLO v8/v11 with multi-object tracking."""

import time
from pathlib import Path

import numpy as np
import yaml
from ultralytics import YOLO

from app import hw


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


# Mặc định của ultralytics/cfg/trackers/bytetrack.yaml — chép lại để chỉnh track_buffer
# mà không đụng file trong package (tránh mất khi cập nhật ultralytics).
_BYTETRACK_DEFAULTS = {
    "tracker_type": "bytetrack",
    "track_high_thresh": 0.25,
    "track_low_thresh": 0.1,
    "new_track_thresh": 0.25,
    "track_buffer": 30,
    "match_thresh": 0.8,
    "fuse_score": True,
}
_DEFAULT_TRACKER_FPS = 30  # fps mà bộ mặc định trên giả định


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
        model_name: str = None,
        conf: float = 0.25,
        imgsz: int = None,
        occlusion_grace: float = OCCLUSION_GRACE_SECONDS
    ):
        """
        Initialize the person detector.

        Args:
            model_name: YOLO model to use. None thì lấy từ app.hw.settings() (tự chọn
                theo CPU/GPU và hw_profile.json).
            conf: Ngưỡng tin cậy. Hạ thấp hơn mặc định vì có tracking lọc nhiễu.
            imgsz: Kích thước ảnh đưa vào model. None thì lấy từ app.hw.settings().
            occlusion_grace: Số giây tiếp tục tính một người đã bị vật che
        """
        settings = hw.settings()
        hw.apply_torch_threads()  # torch mặc định chiếm hết nhân, tranh CPU với onnxruntime (FaceEngine)
        model_name = model_name or settings.yolo_model
        self.imgsz = imgsz or settings.yolo_imgsz
        self.device = settings.device
        # GPU: half precision nhanh gần gấp đôi, sai số không đáng kể cho detection.
        # OpenVINO/CPU không hỗ trợ half theo cách này nên chỉ bật khi có CUDA.
        self.half = self.device == "cuda"

        resolved_path = self._resolve_model_path(model_name, settings)
        self.model = YOLO(resolved_path)
        if self.device == "cuda":
            self.model.to("cuda")

        # COCO dataset class 0 is 'person'
        self.person_class_id = 0
        self.conf = conf
        self.occlusion_grace = occlusion_grace
        # track_id -> {box, conf, hits, last_seen}
        self._tracks: dict = {}
        self._tracker_cfg_path = self._write_tracker_config(settings.ai_fps)

    @staticmethod
    def _resolve_model_path(model_name: str, settings) -> str:
        """Dùng bản xuất OpenVINO cạnh file .pt khi có, nhanh hơn .pt nhiều trên CPU
        không có GPU (xem docs/review — YOLO11s@960 ~300ms .pt vs OpenVINO nhanh hơn
        hẳn). Bật qua AI_BACKEND=openvino, hoặc tự dùng khi "auto" trên CPU và thấy
        sẵn thư mục xuất. Thiếu thư mục hoặc load lỗi thì rơi về .pt, không chết tiến
        trình vì một bước tối ưu không bắt buộc.
        """
        if settings.device == "cuda":
            return model_name
        stem = Path(model_name).stem
        ov_dir = Path(model_name).with_name(f"{stem}_openvino_model")
        wants_ov = settings.ai_backend == "openvino" or (
            settings.ai_backend == "auto" and ov_dir.is_dir()
        )
        if not wants_ov:
            return model_name
        if not ov_dir.is_dir():
            print(f"[PersonDetector] AI_BACKEND=openvino nhưng không thấy {ov_dir}, "
                  f"dùng {model_name}")
            return model_name
        return str(ov_dir)

    def _write_tracker_config(self, ai_fps: int) -> str:
        """Sinh file cấu hình ByteTrack riêng cho tốc độ khung thực tế của pipeline.

        ultralytics LUÔN khởi tạo BYTETracker với frame_rate=30 cố định trong code
        (track.py: on_predict_start), bất kể giá trị nào ghi trong yaml — nên không
        thể "sửa frame_rate" để có tác dụng. Hệ quả: track_buffer (số LƯỢT GỌI
        track(), không phải giây) bị tính theo giả định 30 khung/giây. Pipeline này
        gọi track() ở AI_FPS thấp hơn hẳn (CPU ~4-10 khung/giây), nên nếu giữ
        track_buffer=30 mặc định thì một track mất dấu sẽ bị giữ "sống" quá lâu theo
        thời gian thực (30 lượt gọi ở 4fps = 7.5s thay vì ý định ~1s). Scale lại
        track_buffer theo đúng AI_FPS để bù việc frame_rate bị khoá cứng ở 30.
        """
        cfg = dict(_BYTETRACK_DEFAULTS)
        cfg["frame_rate"] = ai_fps  # chỉ để tham khảo/log, ultralytics không đọc field này
        cfg["track_buffer"] = max(1, round(_BYTETRACK_DEFAULTS["track_buffer"] * ai_fps / _DEFAULT_TRACKER_FPS))

        out_dir = Path("data")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = out_dir / f"bytetrack_{ai_fps}fps.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            return str(cfg_path.resolve())
        except OSError:
            # Không ghi được file (quyền, đĩa đầy...) thì dùng bộ mặc định của ultralytics
            # thay vì làm hỏng cả detector.
            return "bytetrack.yaml"

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
            tracker=self._tracker_cfg_path,
            persist=True,
            half=self.half,
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
