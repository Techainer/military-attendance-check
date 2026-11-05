"""Kiểm chứng ROI/motion-gating/định danh/cổng chất lượng đăng ký (AI_ROI,
AI_MOTION_GATING, thu hồi danh tính, cổng chất lượng enroll — xem app/hw.py
PHẦN "ROI inference"/"Motion gating" và app/video_processor.py/app/face_engine.py).

Chạy: uv run python tests/test_ai_quality.py
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.detector import PersonDetector, YOLO_NMS_IOU
from app.face_engine import FaceEngine
from app.video_processor import (
    MotionGate, VideoProcessor, compute_roi, _zones_signature, _dedupe_zone_count,
    IDENTITY_BIND_SCORE, IDENTITY_REVOKE_HITS,
)

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- ROI: toán học
print("\n[1] Hộp ROI: hộp bao vùng, nới biên, dùng cả khung khi không lợi")

W, H = 1000, 1000
zone_small = [{"id": "z1", "points": [
    {"x": 0.1, "y": 0.1}, {"x": 0.3, "y": 0.1}, {"x": 0.3, "y": 0.3}, {"x": 0.1, "y": 0.3},
]}]

# x2/y2 lệch thêm +1 so với mép vùng: compute_roi() cố tình làm vậy để cắt
# frame[y1:y2, x1:x2] theo slicing (chặn phải loại trừ) vẫn LẤY ĐỦ pixel biên.
roi_no_pad = compute_roi(zone_small, W, H, pad_frac=0.0, max_area_frac=0.9)
check("Không nới biên -> đúng hộp bao vùng (pixel, +1 cho slicing)",
      roi_no_pad == (100, 100, 301, 301), str(roi_no_pad))

roi_pad = compute_roi(zone_small, W, H, pad_frac=0.10, max_area_frac=0.9)
check("Nới 10% mỗi phía -> đúng công thức pad_frac * cạnh", roi_pad == (80, 80, 321, 321), str(roi_pad))

check("Không có vùng nào -> None (dùng cả khung)", compute_roi([], W, H) is None)

zone_huge = [{"id": "zb", "points": [
    {"x": 0.0, "y": 0.0}, {"x": 0.95, "y": 0.0}, {"x": 0.95, "y": 0.95}, {"x": 0.0, "y": 0.95},
]}]
check("ROI nới ra > 70% diện tích khung -> None (dùng cả khung)",
      compute_roi(zone_huge, W, H) is None)

z_a = [{"id": "z1", "points": [{"x": 0.1, "y": 0.1}]}]
z_b = [{"id": "z1", "points": [{"x": 0.1, "y": 0.1}]}]
z_c = [{"id": "z1", "points": [{"x": 0.2, "y": 0.1}]}]
check("Vùng giống hệt -> khoá chữ ký giống nhau (khỏi tính lại ROI)",
      _zones_signature(z_a) == _zones_signature(z_b))
check("Đổi điểm vùng -> khoá chữ ký đổi (tính lại ROI)",
      _zones_signature(z_a) != _zones_signature(z_c))

# ------------------------------------------- ROI: toạ độ box quy về khung đầy đủ
print("\n[2] detect_persons() cộng offset ROI vào box trả về (tracker nhìn ROI,")
print("    người gọi nhận toạ độ TOÀN khung)")


class _Scalar:
    def __init__(self, v):
        self.v = v

    def item(self):
        return self.v


class _XY:
    def __init__(self, vals):
        self.vals = vals

    def tolist(self):
        return list(self.vals)


class _FakeBox:
    def __init__(self, xyxy, conf):
        self.xyxy = [_XY(xyxy)]
        self.conf = [_Scalar(conf)]


class _IdsTensor:
    def __init__(self, ids):
        self._ids = ids

    def int(self):
        return self

    def tolist(self):
        return list(self._ids)


class _FakeBoxes:
    def __init__(self, boxes, ids):
        self._boxes = boxes
        self.id = ids

    def __iter__(self):
        return iter(self._boxes)

    def __len__(self):
        return len(self._boxes)


class _FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class _FakeYoloModel:
    def __init__(self, results):
        self._results = results
        self.last_kwargs = None

    def track(self, frame, **kwargs):
        self.last_kwargs = kwargs
        return self._results


def _bare_detector(results) -> PersonDetector:
    """PersonDetector KHÔNG qua __init__ (không tải YOLO thật) — chỉ gắn đúng
    những thuộc tính mà detect_persons() cần, để kiểm riêng phần cộng offset."""
    det = object.__new__(PersonDetector)
    det.model = _FakeYoloModel(results)
    det.person_class_id = 0
    det.conf = 0.25
    det.imgsz = 640
    det.device = "cpu"
    det.half = False
    det._tracker_cfg_path = "bytetrack.yaml"
    det.occlusion_grace = PersonDetector.OCCLUSION_GRACE_SECONDS
    det._tracks = {}
    return det


fake_results = [_FakeResult(_FakeBoxes([_FakeBox([5, 5, 15, 15], 0.9)], _IdsTensor([1])))]
det = _bare_detector(fake_results)
out = det.detect_persons(np.zeros((20, 20, 3), dtype=np.uint8), offset=(100, 50))
check("Box cục bộ (5,5,15,15) + offset (100,50) -> (105,55,115,65)",
      out["boxes"] == [(105, 55, 115, 65)], str(out["boxes"]))

det2 = _bare_detector(fake_results)
out2 = det2.detect_persons(np.zeros((20, 20, 3), dtype=np.uint8))
check("Không truyền offset (full-frame) -> box giữ nguyên toạ độ cục bộ",
      out2["boxes"] == [(5, 5, 15, 15)], str(out2["boxes"]))

det_nms = _bare_detector(fake_results)
det_nms.detect_persons(np.zeros((20, 20, 3), dtype=np.uint8))
check("model.track() nhận đúng ngưỡng NMS từ YOLO_NMS_IOU (loại box đôi chồng lấn)",
      det_nms.model.last_kwargs.get("iou") == YOLO_NMS_IOU, str(det_nms.model.last_kwargs))

# --------------------------------------------- ghost (giữ qua che khuất) trùng box mới
print("\n[2b] Box 'ma' (giữ qua che khuất) bị bỏ khi đè lên box MỚI đang di chuyển qua")


def _detector_with_ghost(ghost_box, live_box, live_tid=20, frame_shape=(400, 400, 3)):
    """Giả lập: track 10 đã đủ hits để được giữ làm 'ma' qua che khuất, rồi model
    trả về đúng MỘT box MỚI (tid khác — tracker gán lại id cho người di chuyển
    qua chỗ che) — kiểm có bỏ 'ma' đi (không đếm đôi) theo logic mới hay không."""
    results = [_FakeResult(_FakeBoxes([_FakeBox(list(live_box), 0.9)], _IdsTensor([live_tid])))]
    det = _bare_detector(results)
    det._tracks = {10: {"box": tuple(ghost_box), "conf": 0.9, "hits": PersonDetector.MIN_HITS_TO_HOLD,
                         "last_seen": time.monotonic()}}
    return det.detect_persons(np.zeros(frame_shape, dtype=np.uint8))


ghost = (150, 150, 250, 250)  # 100x100, tâm (200, 200)

# IoU ~0.33 — dưới ngưỡng cũ (HOLD_DUPLICATE_IOU=0.45) nên TRƯỚC ĐÂY vẫn giữ "ma"
# (đếm đôi cùng một người); ngưỡng mới (HOLD_DUPLICATE_IOU_LOOSE=0.2) phải bỏ.
out_mid_iou = _detector_with_ghost(ghost, (200, 150, 300, 250))
check("box mới đè IoU~0.33 (dưới ngưỡng cũ 0.45, trên ngưỡng mới 0.2) -> bỏ 'ma'",
      10 not in out_mid_iou["track_ids"] and len(out_mid_iou["boxes"]) == 1, str(out_mid_iou))

# IoU thấp (<0.2) nhưng tâm box mới còn gần tâm "ma" (< 0.75 * chiều rộng "ma")
# -> người đang DI CHUYỂN qua, box lệch nhiều nhưng vẫn là cùng một người
out_center_close = _detector_with_ghost(ghost, (203, 203, 303, 303))
check("IoU thấp (0.12) nhưng tâm box mới còn gần tâm 'ma' -> vẫn bỏ 'ma' (người đang di chuyển)",
      10 not in out_center_close["track_ids"] and len(out_center_close["boxes"]) == 1,
      str(out_center_close))

# Box mới ở xa hẳn (rõ ràng không phải cùng người) -> GIỮ "ma", không bỏ nhầm
out_far = _detector_with_ghost(ghost, (900, 900, 980, 980), frame_shape=(1000, 1000, 3))
check("box mới ở xa hẳn, không liên quan -> vẫn GIỮ 'ma', không bỏ nhầm sĩ số",
      10 in out_far["track_ids"] and len(out_far["boxes"]) == 2, str(out_far))

# ----------------------------------------- sĩ số trong vùng đếm theo track duy nhất
print("\n[2c] person_count: không đếm đôi track trùng / 'ma' đè lên box đang thấy")

zone_boxes = [
    (0, 0, 100, 100),      # idx0: track 1, đang thấy thật
    (10, 10, 110, 110),    # idx1: track 1 (TRÙNG track idx0 — box kép cùng track)
    (500, 500, 600, 600),  # idx2: track 2, đang thấy thật
    (505, 505, 605, 605),  # idx3: "ma" của track KHÁC (99), chồng lấn box idx2 -> không đếm đôi
    (900, 0, 950, 50),     # idx4: "ma" của track 100, KHÔNG chồng ai đang thấy -> vẫn đếm
]
zone_tids = [1, 1, 2, 99, 100]
zone_occluded = [False, False, False, True, True]
zone_indices = {0, 1, 2, 3, 4}

check("track 1 trùng (2 box CÙNG track) -> chỉ đếm 1; 'ma' track KHÁC đè lên box "
      "đang thấy -> không đếm đôi; 'ma' không chồng ai -> vẫn đếm riêng (tổng 3)",
      _dedupe_zone_count(zone_indices, zone_tids, zone_occluded, zone_boxes) == 3,
      str(_dedupe_zone_count(zone_indices, zone_tids, zone_occluded, zone_boxes)))

no_tid_boxes = [(0, 0, 10, 10), (0, 0, 10, 10)]
check("box chưa có track id thật (id âm) -> đếm riêng từng cái, không gộp",
      _dedupe_zone_count({0, 1}, [-1, -2], [False, False], no_tid_boxes) == 2)

# ---------------------------------------------------------------- motion gate
print("\n[3] MotionGate: tĩnh -> idle, vừa đổi hẳn khung -> có động")

gate = MotionGate(threshold=10.0)
still = np.full((80, 120, 3), 50, dtype=np.uint8)

check("Khung ĐẦU TIÊN chưa có nền để so -> coi như có động (an toàn)",
      gate.update(still) is True)
check("Khung tĩnh liên tiếp -> hết động", gate.update(still) is False)
check("Vẫn tĩnh -> vẫn không động", gate.update(still) is False)

moved = np.full((80, 120, 3), 220, dtype=np.uint8)
check("Đổi hẳn sang khung khác (sáng khác xa nền) -> phát hiện có động",
      gate.update(moved) is True)

gate_empty = MotionGate()
check("Vùng rỗng (ROI tính sai) -> coi như có động, không vỡ", gate_empty.update(None) is True)
check("Vùng 0 pixel -> coi như có động", gate_empty.update(np.zeros((0, 0, 3), dtype=np.uint8)) is True)

# ------------------------------------------------- gán khuôn mặt toàn cục (assignment)
print("\n[4] Gán mặt-người TOÀN CỤC: không bỏ sót người chỉ vì một mặt")
print("    tham lam chiếm nhầm box nhỏ nhất trước (lỗi của bản greedy theo thứ tự cũ)")

# P1 (22500px^2) chứa tâm mặt A nhưng KHÔNG chứa tâm B (ngoài cả biên độ nới);
# P2 nhỏ hơn (15625px^2) chứa CẢ tâm A và tâm B. Greedy "luôn chọn box nhỏ nhất
# CHO MẶT ĐANG XÉT" xử lý A trước sẽ cướp P2 (nhỏ hơn P1), để B mất lựa chọn DUY
# NHẤT của nó -> chỉ khớp được 1/2 người. Gán toàn cục phải thấy trước và nhường
# P2 cho B, khớp được cả hai.
person_boxes = [
    (0, 0, 150, 150),       # P1: diện tích 22500, chỉ chứa tâm A
    (100, 100, 225, 225),   # P2: diện tích 15625, chứa cả tâm A và tâm B
]
faces = [
    {"bbox": [115, 115, 135, 135], "is_recognized": True, "similarity": 0.8,
     "person": {"id": "A", "name": "Mặt A"}},   # tâm (125,125): trong cả P1 và P2
    {"bbox": [195, 195, 205, 205], "is_recognized": True, "similarity": 0.8,
     "person": {"id": "B", "name": "Mặt B"}},   # tâm (200,200): chỉ trong P2 (ngoài P1 + biên độ)
]
claimed = set()
assignments = {}
matched = VideoProcessor._assign_faces(faces, person_boxes, {0, 1}, claimed, assignments)

check("Gán toàn cục khớp được CẢ HAI người (không chỉ 1 như greedy cũ)",
      len(assignments) == 2, str(assignments))
check("Người A được gán cho P1 (nhường P2 cho B vì B không còn lựa chọn khác)",
      assignments.get(0, {}).get("person", {}).get("id") == "A", str(assignments))
check("Người B được gán cho P2 (lựa chọn DUY NHẤT của B)",
      assignments.get(1, {}).get("person", {}).get("id") == "B", str(assignments))
check("Cả hai box khuôn mặt đều được trả về", len(matched) == 2, str(matched))

# Mặt không khớp ai (tâm ngoài mọi box trong vùng) -> bị bỏ, không gán bừa
claimed2, assignments2 = set(), {}
faces_miss = [{"bbox": [900, 900, 910, 910], "is_recognized": True, "similarity": 0.9,
               "person": {"id": "C", "name": "Mặt C"}}]
matched_miss = VideoProcessor._assign_faces(faces_miss, person_boxes, {0, 1}, claimed2, assignments2)
check("Mặt ngoài mọi box trong vùng -> không gán được ai", assignments2 == {} and matched_miss == [])

# ---------------------------------------------------------------- thu hồi danh tính
print("\n[5] Thu hồi danh tính track khi người khác khớp nhiều lần với điểm cao hơn")


class _FakeSelf:
    """Đứng thay VideoProcessor thật: _remember_identity chỉ đọc/ghi track_identity."""
    track_identity = {}


fs = _FakeSelf()
fs.track_identity = {}
remember = VideoProcessor._remember_identity

person_a = {"id": "a", "name": "Binh nhất A"}
person_b = {"id": "b", "name": "Binh nhất B"}

# Khoá nhầm vào A ngay từ đầu (hai lượt khớp điểm thấp, vượt ngưỡng BIND nhờ cộng dồn)
remember(fs, 42, person_a, IDENTITY_BIND_SCORE, 0.0)
check("Vừa đủ điểm -> khoá cứng vào A", fs.track_identity[42]["person"]["id"] == "a")

# B khớp nhiều lần với điểm cao hơn A (A chỉ có một lượt điểm thấp đứng khoá)
for hit in range(IDENTITY_REVOKE_HITS - 1):
    remember(fs, 42, person_b, 0.95, 1.0 + hit)
    check(f"Chưa đủ {IDENTITY_REVOKE_HITS} lượt khớp -> CHƯA thu hồi (lượt {hit + 1})",
          fs.track_identity[42]["person"]["id"] == "a")

remember(fs, 42, person_b, 0.95, 10.0)  # lượt khớp thứ IDENTITY_REVOKE_HITS
check(f"Đủ {IDENTITY_REVOKE_HITS} lượt khớp VÀ điểm trung bình cao hơn -> thu hồi, đổi sang B",
      fs.track_identity[42]["person"]["id"] == "b", str(fs.track_identity[42]))

# Track khác: B khớp nhiều lần nhưng điểm THẤP hơn A đang khoá -> không thu hồi
fs2 = _FakeSelf()
fs2.track_identity = {}
remember(fs2, 7, person_a, 1.0, 0.0)   # 1 lượt, điểm rất cao -> khoá ngay (>= BIND_SCORE)
for hit in range(IDENTITY_REVOKE_HITS + 2):
    remember(fs2, 7, person_b, 0.11, 1.0 + hit)  # nhiều lượt nhưng điểm trung bình thấp hơn A
check("Điểm trung bình thách thức KHÔNG cao hơn người đang khoá -> giữ nguyên A",
      fs2.track_identity[7]["person"]["id"] == "a", str(fs2.track_identity[7]))

# ---------------------------------------------------------------- cổng chất lượng enroll
print("\n[6] Cổng chất lượng đăng ký khuôn mặt: từ chối ảnh mờ/mặt quá nhỏ")


class _FakeFace:
    def __init__(self, bbox, det_score, embedding_dim=512):
        self.bbox = np.array(bbox, dtype=float)
        self.embedding = np.ones(embedding_dim, dtype=np.float32)
        self.det_score = det_score


class _FakeInsightFaceApp:
    def __init__(self, faces):
        self._faces = faces

    def get(self, frame):
        return self._faces


def _bare_face_engine(faces) -> FaceEngine:
    """FaceEngine KHÔNG qua __init__ (không tải InsightFace thật) — chỉ gắn
    ``app`` giả để kiểm riêng cổng chất lượng trong extract_face_and_embedding."""
    fe = object.__new__(FaceEngine)
    fe.app = _FakeInsightFaceApp(faces)
    return fe

frame = np.zeros((200, 200, 3), dtype=np.uint8)

fe_good = _bare_face_engine([_FakeFace([10, 10, 90, 90], det_score=0.9)])  # rộng 80px, score cao
extracted = fe_good.extract_face_and_embedding(frame, enforce_quality=True)
check("Mặt rõ, đủ lớn -> ĐƯỢC chấp nhận đăng ký", extracted is not None)

fe_small = _bare_face_engine([_FakeFace([10, 10, 50, 50], det_score=0.9)])  # rộng 40px < 60
try:
    fe_small.extract_face_and_embedding(frame, enforce_quality=True)
    check("Mặt quá nhỏ (<60px) -> bị từ chối (ValueError)", False)
except ValueError as e:
    check("Mặt quá nhỏ (<60px) -> bị từ chối (ValueError)", True, str(e))

fe_blur = _bare_face_engine([_FakeFace([10, 10, 90, 90], det_score=0.4)])  # score thấp < 0.6
try:
    fe_blur.extract_face_and_embedding(frame, enforce_quality=True)
    check("Mặt mờ (det_score<0.6) -> bị từ chối (ValueError)", False)
except ValueError as e:
    check("Mặt mờ (det_score<0.6) -> bị từ chối (ValueError)", True, str(e))

# Không bật cổng chất lượng (enforce_quality=False, dùng cho đường nhận diện lúc
# chạy) -> không ném lỗi dù mặt nhỏ/mờ, giữ hành vi cũ
extracted_lenient = fe_small.extract_face_and_embedding(frame, enforce_quality=False)
check("Không bật enforce_quality -> không từ chối (đường nhận diện lúc chạy không đổi)",
      extracted_lenient is not None)

# ---------------------------------------------------------------- quét lại mặt có giãn cách
print("\n[rescan] Không quét lại mặt người lạ ở mọi khung, bỏ qua track đã khoá")
from app import video_processor as _vp  # noqa: E402
rs = _FakeSelf()
rs.track_identity = {7: {"person": {"id": "a"}}}
rs._last_rescan = {}
boxes = [(100, 100, 200, 400), (300, 100, 400, 400), (500, 100, 600, 400)]
tids = [7, 8, 9]
pick = lambda t: VideoProcessor._rescan_regions(rs, boxes, {0, 1, 2}, set(), (720, 1280, 3), tids, t)
r0 = pick(100.0)
check("track đã khoá danh tính (7) không bị quét lại", len(r0) == 2, str(r0))
r1 = pick(100.2)
check("trong thời gian giãn cách -> không quét lại", r1 == [], str(r1))
r2 = pick(100.0 + _vp.FACE_RESCAN_COOLDOWN_S + 0.01)
check("hết giãn cách -> quét lại", len(r2) == 2, str(r2))

# ---------------------------------------------------------------- kết luận
print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
