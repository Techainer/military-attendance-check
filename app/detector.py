"""Person detection module using YOLO v8/v11 with multi-object tracking."""

import os
import time
from pathlib import Path
from typing import Tuple

import numpy as np
import yaml
from ultralytics import YOLO

from app import hw

# Ngưỡng NMS (Non-Max Suppression) của chính YOLO: hai box cùng che một người
# (model đôi khi tách một người thành 2 box sát nhau, đặc biệt lúc nửa người bị
# che) chồng lấn trên ngưỡng này thì MỘT trong hai bị loại trước khi ra tới
# bước tracking — khác với HOLD_DUPLICATE_IOU bên dưới (lọc box "ma" giữ qua
# khung bị che).
YOLO_NMS_IOU = float(os.environ.get("YOLO_NMS_IOU", "0.5"))


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


# Box nhỏ nằm gần trọn trong box lớn hơn (tỉ lệ diện tích box nhỏ bị box lớn phủ
# >= ngưỡng này) thì coi là CÙNG một người bị YOLO tách đôi (box nửa thân trên
# lồng trong box cả người). NMS theo IoU không bắt được ca này vì box nhỏ lồng
# trong box to cho IoU thấp.
YOLO_CONTAIN_THR = float(os.environ.get("YOLO_CONTAIN_THR", "0.8"))


def _containment(small, big) -> float:
    """Phần diện tích của ``small`` nằm trong ``big`` (0..1)."""
    ax1, ay1, ax2, ay2 = small
    bx1, by1, bx2, by2 = big
    iw = max(0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0, min(ay2, by2) - max(ay1, by1))
    area = max(1, (ax2 - ax1) * (ay2 - ay1))
    return (iw * ih) / area


def _contained_indices(boxes, thr: float = YOLO_CONTAIN_THR) -> set:
    """Chỉ số các box nằm gần trọn trong một box khác lớn hơn — cần bỏ đi."""
    areas = [max(0, (b[2] - b[0]) * (b[3] - b[1])) for b in boxes]
    drop = set()
    order = sorted(range(len(boxes)), key=lambda i: areas[i], reverse=True)
    for pos, i in enumerate(order):
        if i in drop:
            continue
        for j in order[pos + 1:]:
            if j not in drop and _containment(boxes[j], boxes[i]) >= thr:
                drop.add(j)
    return drop


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
# Mặc định của ultralytics/cfg/trackers/botsort.yaml — chép lại cùng lý do.
# gmc_method "none" (mặc định gốc là sparseOptFlow, dùng bù chuyển động NỀN cho
# camera DI ĐỘNG): camera giám sát ở đây đứng yên, bù chuyển động chỉ tốn CPU
# vô ích mà không cải thiện gì -> tắt để BoT-SORT không chậm hơn ByteTrack một
# cách không cần thiết. with_reid False (mặc định gốc) vì cần tải thêm model
# ReID + chạy suy luận thêm mỗi khung — chỉ bật qua YOLO_TRACKER_REID=1 khi đã
# chấp nhận đổi tốc độ lấy độ chính xác track cao hơn.
_BOTSORT_DEFAULTS = {
    "tracker_type": "botsort",
    "track_high_thresh": 0.25,
    "track_low_thresh": 0.1,
    "new_track_thresh": 0.25,
    "track_buffer": 30,
    "match_thresh": 0.8,
    "fuse_score": True,
    "gmc_method": "none",
    "proximity_thresh": 0.5,
    "appearance_thresh": 0.8,
    "with_reid": False,
    "model": "auto",
}
_DEFAULT_TRACKER_FPS = 30  # fps mà bộ mặc định trên giả định

# Chọn thuật toán theo dõi: "bytetrack" (mặc định, nhẹ) hoặc "botsort" (có thêm
# bù chuyển động + ReID tuỳ chọn, nặng hơn). Sai giá trị -> rơi về bytetrack,
# không chết tiến trình vì một biến môi trường gõ nhầm.
YOLO_TRACKER = os.environ.get("YOLO_TRACKER", "bytetrack").strip().lower()
if YOLO_TRACKER not in ("bytetrack", "botsort"):
    print(f"[PersonDetector] YOLO_TRACKER='{YOLO_TRACKER}' không hợp lệ (chỉ nhận "
          f"bytetrack|botsort), dùng bytetrack.")
    YOLO_TRACKER = "bytetrack"
# BoT-SORT + ReID: so khớp track còn dựa thêm vào đặc trưng hình ảnh (không chỉ
# IoU/chuyển động) — chính xác hơn khi nhiều người đi ngang nhau, nhưng tốn
# thêm một lượt suy luận mỗi khung nên mặc định tắt.
YOLO_TRACKER_REID = os.environ.get("YOLO_TRACKER_REID", "0").strip() == "1"


class PersonDetector:
    """Detects and tracks persons in video frames using YOLO."""

    # Track phải xuất hiện đủ số lượt này mới được giữ lại khi bị che
    MIN_HITS_TO_HOLD = 3
    # Giữ lại track bị che tối đa bao nhiêu giây. Hạ từ 5.0 xuống 3.0: 5s cũ giữ
    # "ma" quá lâu khi người di chuyển qua chỗ che rồi tracker gán lại id mới —
    # ma cũ đứng yên + người thật đang đi tạo cảm giác "nhân bản" trên màn hình.
    OCCLUSION_GRACE_SECONDS = float(os.environ.get("OCCLUSION_GRACE_SECONDS", "3.0"))
    # Box giữ lại ("ma") trùng với box đang thấy quá mức này thì bỏ, tránh đếm đôi
    HOLD_DUPLICATE_IOU = 0.45
    # Ngưỡng IoU thấp hơn: người di chuyển nên box mới có thể lệch khỏi box "ma"
    # khá nhiều mà vẫn là CÙNG một người (không đủ để qua HOLD_DUPLICATE_IOU) —
    # chỉ cần chồng lấn một phần đáng kể cũng đủ để bỏ "ma", tránh vẽ nhân bản.
    HOLD_DUPLICATE_IOU_LOOSE = 0.2
    # Hoặc tâm box mới gần tâm "ma" trong phạm vi này (tỉ lệ theo chiều rộng box
    # "ma") thì cũng coi là cùng một người đang di chuyển qua, dù IoU thấp.
    HOLD_DUPLICATE_CENTER_FRAC = 0.75
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
        if YOLO_TRACKER == "botsort":
            cfg = dict(_BOTSORT_DEFAULTS)
            if YOLO_TRACKER_REID:
                cfg["with_reid"] = True
                cfg["model"] = "auto"
        else:
            cfg = dict(_BYTETRACK_DEFAULTS)
        cfg["frame_rate"] = ai_fps  # chỉ để tham khảo/log, ultralytics không đọc field này
        cfg["track_buffer"] = max(1, round(cfg["track_buffer"] * ai_fps / _DEFAULT_TRACKER_FPS))

        out_dir = Path("data")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = out_dir / f"{YOLO_TRACKER}_{ai_fps}fps.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            return str(cfg_path.resolve())
        except OSError:
            # Không ghi được file (quyền, đĩa đầy...) thì dùng bộ mặc định của ultralytics
            # thay vì làm hỏng cả detector.
            return f"{YOLO_TRACKER}.yaml"

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

    def _is_duplicate_of_live(self, ghost_box, boxes) -> bool:
        """Box "ma" (giữ lại qua khung bị che) có trùng một box ĐANG THẤY THẬT
        không — chồng lấn rõ (IoU cao) hoặc chỉ chồng lấn một phần nhưng tâm hai
        box đủ gần thì đều coi là CÙNG một người, phải bỏ "ma" đi. Không chỉ xét
        IoU cao (``HOLD_DUPLICATE_IOU``) như trước: người đang DI CHUYỂN qua chỗ
        che, lúc tracker gán lại id mới, box mới có thể lệch khá xa box "ma" dù
        vẫn là cùng một người — không bắt được trường hợp này thì "ma" đứng yên
        + người thật đang đi tạo cảm giác nhân bản trên màn hình.
        """
        gx1, gy1, gx2, gy2 = ghost_box
        ghost_w = max(1.0, gx2 - gx1)
        gcx, gcy = (gx1 + gx2) / 2.0, (gy1 + gy2) / 2.0
        for b in boxes:
            if _iou(ghost_box, b) >= self.HOLD_DUPLICATE_IOU_LOOSE:
                return True
            if _containment(ghost_box, b) >= YOLO_CONTAIN_THR or _containment(b, ghost_box) >= YOLO_CONTAIN_THR:
                return True
            bx1, by1, bx2, by2 = b
            bcx, bcy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
            center_dist = ((bcx - gcx) ** 2 + (bcy - gcy) ** 2) ** 0.5
            if center_dist < self.HOLD_DUPLICATE_CENTER_FRAC * ghost_w:
                return True
        return False

    def detect_persons(self, frame: np.ndarray, offset: Tuple[int, int] = (0, 0)) -> dict:
        """
        Detect and track persons in a video frame.

        ``frame`` có thể là một vùng ROI đã cắt sẵn (xem app/video_processor.py,
        AI_ROI) thay vì cả khung hình — model.track() và bộ nhớ che khuất bên
        dưới hoàn toàn không biết điều đó, mọi thứ vẫn tính theo toạ độ CỤC BỘ
        của ``frame``. ``offset`` (dx, dy) chỉ cộng vào box ở bước trả kết quả
        cuối cùng, để phía gọi (tracker nhìn thấy ROI cố định qua từng khung,
        giữ track ổn định) nhận lại toạ độ TOÀN khung cho vẽ/luật vùng.

        Người đang bị đồ vật che khuất vẫn được giữ trong kết quả (cờ ``occluded``)
        trong ``occlusion_grace`` giây để sĩ số không tụt mỗi khi model miss.

        Returns:
            Dictionary with count, boxes, confidences, track_ids, occluded flags
        """
        results = self.model.track(
            frame,
            classes=[self.person_class_id],
            conf=self.conf,
            iou=YOLO_NMS_IOU,
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

        # Bỏ box lồng trong box khác (1 người bị tách thành 2 box). Track của
        # box bị bỏ cũng xoá khỏi bộ nhớ che khuất để không hiện lại thành "ma".
        drop = _contained_indices(boxes)
        if drop:
            for i in drop:
                if track_ids[i] is not None and track_ids[i] >= 0:
                    self._tracks.pop(track_ids[i], None)
            keep = [i for i in range(len(boxes)) if i not in drop]
            boxes = [boxes[i] for i in keep]
            confidences = [confidences[i] for i in keep]
            track_ids = [track_ids[i] for i in keep]

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
            if self._is_duplicate_of_live(entry["box"], boxes):
                continue
            boxes.append(entry["box"])
            confidences.append(entry["conf"])
            track_ids.append(tid)
            occluded.append(True)

        if offset != (0, 0):
            dx, dy = offset
            boxes = [(x1 + dx, y1 + dy, x2 + dx, y2 + dy) for x1, y1, x2, y2 in boxes]

        return {
            'count': len(boxes),
            'boxes': boxes,
            'confidences': confidences,
            'track_ids': track_ids,
            'occluded': occluded
        }
