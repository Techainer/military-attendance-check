"""Giám sát an toàn: phát hiện đối tượng đi vào vùng cấm hoặc vượt vạch an toàn.

Polygon vốn đã có sẵn trong hệ thống nhưng chỉ dùng để *lọc* người được tính vào
sĩ số. Ở đây polygon được gán thêm ngữ nghĩa qua trường ``rule``:

- ``attendance_area``  : chỉ đếm người bên trong (hành vi cũ, giữ nguyên)
- ``restricted_area``  : người bên trong là **vi phạm** — khối chắn trường bắn
- ``crossing_line``    : sinh vi phạm khi có người cắt qua vạch

``detect_object`` của vùng được đọc/ghi qua ``ZoneStore`` nhưng KHÔNG dùng ở
đây: bộ dò hiện chỉ nhận diện người (xem ``app/detector.py``), nên cờ này chỉ
còn ý nghĩa hiển thị, cố tình để lõi bỏ qua.
"""

import os
from datetime import datetime
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

RULE_ATTENDANCE = "attendance_area"
RULE_RESTRICTED = "restricted_area"
RULE_CROSSING = "crossing_line"

# Không bắn lại vi phạm của cùng một vùng trong khoảng này
INTRUSION_COOLDOWN_SECONDS = 20

# Biên độ trễ (hysteresis) quanh mép vùng/vạch, tính ở khung hình rộng _REF_WIDTH
# rồi co giãn theo kích thước khung hình thực tế. Thiếu biên độ này, chân box
# dính sát mép vùng sẽ nhảy vào/ra liên tục chỉ do rung nhiễu 1-2px của box.
INTRUSION_HYSTERESIS_PX = float(os.environ.get("INTRUSION_HYSTERESIS_PX", "12"))
# Số khung hình LIÊN TỤC tối thiểu ở trạng thái mới trước khi coi là đã chuyển
INTRUSION_MIN_FRAMES = int(os.environ.get("INTRUSION_MIN_FRAMES", "2"))
# Số giây tối thiểu phải "đứng" ở trạng thái mới trước khi coi là đã chuyển
INTRUSION_MIN_DWELL_S = float(os.environ.get("INTRUSION_MIN_DWELL_S", "1.0"))
# Track không thấy lại quá lâu thì dọn trạng thái, tránh rò bộ nhớ theo track_id
TRACK_STATE_TTL_SECONDS = 10.0
# Khung hình chuẩn dùng để quy đổi biên độ hysteresis theo tỉ lệ khung hình thật
_REF_WIDTH = 1280.0

# Hai phía của vạch an toàn, dùng cho bộ lọc hướng cắt qua (zone["direction"])
SIDE_A = 1
SIDE_B = -1


def _margin_px(width: int) -> float:
    """Biên độ hysteresis (px) co giãn theo chiều rộng khung hình thực tế."""
    scale = max(int(width), 1) / _REF_WIDTH
    return max(2.0, INTRUSION_HYSTERESIS_PX * scale)


def _to_pixels(points: List[dict], width: int, height: int) -> np.ndarray:
    return np.array([[int(p["x"] * width), int(p["y"] * height)] for p in points], np.int32)


def _feet_point(box) -> tuple:
    x1, y1, x2, y2 = box
    return ((float(x1) + float(x2)) / 2.0, float(y2) - 2)


def _center_point(box) -> tuple:
    x1, y1, x2, y2 = box
    return ((float(x1) + float(x2)) / 2.0, (float(y1) + float(y2)) / 2.0)


def _cross_product(p1, p2, pt) -> float:
    """Giá trị có dấu, tỉ lệ với khoảng cách từ điểm tới đường p1-p2."""
    return (p2[0] - p1[0]) * (pt[1] - p1[1]) - (p2[1] - p1[1]) * (pt[0] - p1[0])


def _direction_allows(direction: str, previous_side: int, side: int) -> bool:
    """Hướng cắt qua có khớp bộ lọc ``direction`` của vùng không."""
    if direction == "a_to_b":
        return previous_side == SIDE_A and side == SIDE_B
    if direction == "b_to_a":
        return previous_side == SIDE_B and side == SIDE_A
    return True  # "any" hoặc giá trị lạ -> không lọc hướng


class ZoneStore:
    """Đọc cấu hình vùng từ ``data/zone_rules.json``, chỉ đọc lại khi file đổi."""

    # Vùng cũ chưa khai camera_id thì coi như thuộc camera mặc định
    DEFAULT_CAMERA_ID = "cam_01"

    def __init__(self, data_dir: str, camera_id: Optional[str] = None):
        self.zone_file = Path(data_dir) / "zone_rules.json"
        # ``None`` = xem hết, dùng cho màn quản trị. Vòng xử lý video luôn truyền
        # camera_id để không lấy nhầm vùng của camera khác.
        self.camera_id = camera_id
        self._cache: List[dict] = []
        self._mtime = None

    def all_zones(self) -> List[dict]:
        """Mọi vùng đã cấu hình, kể cả vùng đang tắt (dùng cho màn quản trị)."""
        if not self.zone_file.exists():
            return []
        mtime = self.zone_file.stat().st_mtime
        if mtime != self._mtime:
            self._mtime = mtime
            self._cache = self._parse()
        return self._cache

    def zones(self) -> List[dict]:
        """Các vùng đang bật CỦA CAMERA NÀY — phần vòng xử lý video thực sự dùng."""
        return [z for z in self.all_zones()
                if z.get("enabled", True) and self._belongs(z)]

    def _belongs(self, zone: dict) -> bool:
        if self.camera_id is None:
            return True
        return zone.get("camera_id", self.DEFAULT_CAMERA_ID) == self.camera_id

    def save(self, zones: List[dict]) -> None:
        """Ghi lại toàn bộ danh sách vùng. Vòng xử lý tự nhận cấu hình mới."""
        self.zone_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.zone_file, "w", encoding="utf-8") as f:
            json.dump({"zones": zones}, f, ensure_ascii=False, indent=2)
        # Buộc đọc lại ở lần truy cập kế tiếp, không chờ so sánh mtime
        self._mtime = None

    def _parse(self) -> List[dict]:
        try:
            with open(self.zone_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[Safety] Lỗi đọc cấu hình vùng: {e}")
            return []

        if isinstance(data.get("zones"), list):
            return list(data["zones"])

        # Cấu hình theo định dạng cũ: một polygon để đếm quân số, một vạch an toàn
        zones = []
        polygon = data.get("polygon_points", [])
        if len(polygon) >= 3:
            zones.append({
                "id": "zone_attendance",
                "name": data.get("zone_name", "Khu vực tập trung"),
                "kind": "polygon",
                "rule": RULE_ATTENDANCE,
                "points": polygon,
                "enabled": True,
            })
        tripwire = data.get("tripwire_points", [])
        if len(tripwire) >= 2:
            zones.append({
                "id": "zone_tripwire",
                "name": "Vạch an toàn",
                "kind": "tripwire",
                "rule": RULE_CROSSING,
                "points": tripwire[:2],
                # Trong cấu hình cũ vạch chỉ là hình vẽ, không sinh cảnh báo. Bật
                # sẵn ở đây thì mọi hệ thống đang chạy bỗng dưng réo còi.
                "enabled": False,
            })
        return zones

    # ---------- tương thích với định dạng cũ ----------
    # Giao diện hiện tại vẫn gọi /api/zones với một polygon và một vạch. Hai API
    # đọc ghi chung một file, nên phần này quy đổi qua lại thay vì tách kho dữ liệu.

    def to_legacy(self) -> dict:
        """Dựng lại payload kiểu cũ từ danh sách vùng."""
        zones = self.all_zones()
        polygon = next((z for z in zones if z.get("rule") == RULE_ATTENDANCE), None)
        tripwire = next((z for z in zones if z.get("rule") == RULE_CROSSING), None)
        return {
            "zone_name": (polygon or {}).get("name", "Khu vực tập trung"),
            "rule_type": (polygon or {}).get("legacy_rule_type", "Cảnh báo Xâm nhập 24/7"),
            "detect_human": (polygon or {}).get("detect_human", True),
            "detect_object": (polygon or {}).get("detect_object", True),
            "polygon_points": (polygon or {}).get("points", []),
            "tripwire_points": (tripwire or {}).get("points", []),
        }

    def merge_legacy(self, payload: dict) -> List[dict]:
        """Ghi payload kiểu cũ vào kho, giữ nguyên các vùng cấm đã cấu hình riêng."""
        kept = [z for z in self.all_zones()
                if z.get("rule") not in (RULE_ATTENDANCE, RULE_CROSSING)]

        polygon = payload.get("polygon_points") or []
        if len(polygon) >= 3:
            kept.append({
                "id": "zone_attendance",
                "name": payload.get("zone_name", "Khu vực tập trung"),
                "kind": "polygon",
                "rule": RULE_ATTENDANCE,
                "points": polygon,
                "detect_human": bool(payload.get("detect_human", True)),
                "detect_object": bool(payload.get("detect_object", True)),
                "legacy_rule_type": payload.get("rule_type"),
                "enabled": True,
            })

        tripwire = payload.get("tripwire_points") or []
        if len(tripwire) >= 2:
            kept.append({
                "id": "zone_tripwire",
                "name": "Vạch an toàn",
                "kind": "tripwire",
                "rule": RULE_CROSSING,
                "points": tripwire[:2],
                # Vạch kế thừa từ cấu hình cũ vốn chỉ là hình vẽ, nên để tắt sẵn;
                # bật lên là bắt đầu sinh cảnh báo, phải do người dùng chủ động.
                "enabled": False,
                "detect_human": True,
                "detect_object": False,
            })

        self.save(kept)
        return kept

    def attendance_polygon(self, width: int, height: int) -> Optional[np.ndarray]:
        """Polygon dùng để lọc quân số. ``None`` nếu chưa cấu hình vùng nào."""
        for zone in self.zones():
            if zone.get("rule") == RULE_ATTENDANCE and len(zone.get("points", [])) >= 3:
                return _to_pixels(zone["points"], width, height)
        return None


class IntrusionDetector:
    """Soi từng khung hình xem có ai vào vùng cấm hoặc vượt vạch an toàn không.

    Mỗi (vùng, track) có một máy trạng thái riêng để chống báo động giả do
    rung nhiễu, track đổi ID hay box "giữ" qua khung hình bị che:

    - Vùng cấm: track chỉ được tính là "trong vùng" khi chân box lún sâu hơn
      biên độ hysteresis (``INTRUSION_HYSTERESIS_PX``) vào trong đa giác, liên
      tục đủ ``INTRUSION_MIN_FRAMES`` khung VÀ đủ ``INTRUSION_MIN_DWELL_S``
      giây; chỉ được tính là "đã rời" khi ra khỏi đa giác đã nới rộng thêm
      đúng biên độ đó. Alert bắn đúng một lần ở khung hình vừa chuyển sang
      "trong vùng".
    - Vạch an toàn: phải đứng ổn định ở một phía đủ lâu rồi mới đứng ổn định ở
      phía kia đủ lâu mới tính là cắt qua; có thể giới hạn hướng cắt qua bằng
      trường ``direction`` của vùng (``"any"`` | ``"a_to_b"`` | ``"b_to_a"``).

    Box bị che (``occluded``) hoặc chưa có track id thật (id âm, do tracker
    chưa gán kịp) không được đưa vào bất cứ máy trạng thái nào — vẫn đếm sĩ số
    bình thường ở nơi khác, chỉ không gây xâm nhập giả ở đây.
    """

    def __init__(self, zone_store: ZoneStore):
        self.zones = zone_store
        # zone_id -> lần bắn vi phạm gần nhất
        self._last_fired: Dict[str, datetime] = {}
        # zone_id -> {track_id: trạng thái máy của track đó trong vùng/vạch này}
        self._track_state: Dict[str, Dict[int, dict]] = {}

    def check(self, person_boxes, track_ids, width: int, height: int, now: datetime,
              occluded: Optional[List[bool]] = None) -> List[dict]:
        """Trả về danh sách vi phạm VỪA CHUYỂN TRẠNG THÁI trong khung hình này.

        Mỗi phần tử: ``{"zone", "indices", "dwell_seconds"}`` — ``indices`` là vị
        trí của các box vừa được xác nhận vi phạm (không lặp lại cho tới khi
        track đó rời khỏi rồi vào lại). ``occluded`` (tuỳ chọn, cùng độ dài với
        ``person_boxes``) đánh dấu các box đang "giữ" qua khung hình bị che —
        các box này bị loại khỏi mọi máy trạng thái.
        """
        violations = []
        for zone in self.zones.zones():
            rule = zone.get("rule")
            if rule == RULE_RESTRICTED:
                hits = self._restricted_hits(zone, person_boxes, track_ids, occluded, width, height, now)
            elif rule == RULE_CROSSING:
                hits = self._crossing_hits(zone, person_boxes, track_ids, occluded, width, height, now)
            else:
                continue
            if not hits:
                continue

            zone_id = zone.get("id", zone.get("name", "zone"))
            last = self._last_fired.get(zone_id)
            if last is not None and (now - last).total_seconds() < INTRUSION_COOLDOWN_SECONDS:
                continue
            self._last_fired[zone_id] = now

            violations.append({
                "zone": zone,
                "indices": [idx for idx, _dwell in hits],
                "dwell_seconds": int(max(dwell for _idx, dwell in hits)),
            })
        return violations

    @staticmethod
    def _real_track_id(track_ids, occluded, idx) -> Optional[int]:
        """Track id thật của box, hoặc ``None`` nếu box này không được tính (bị
        che hoặc chưa có id thật — id âm do tracker gán tạm)."""
        tid = track_ids[idx] if idx < len(track_ids) else None
        if tid is None or tid < 0:
            return None
        if occluded is not None and idx < len(occluded) and occluded[idx]:
            return None
        return tid

    @staticmethod
    def _prune_stale(state: Dict[int, dict], seen_tracks: set, now: datetime) -> None:
        """Dọn trạng thái của track không thấy lại quá ``TRACK_STATE_TTL_SECONDS``."""
        for tid in [t for t in state if t not in seen_tracks]:
            if (now - state[tid].get("last_seen", now)).total_seconds() > TRACK_STATE_TTL_SECONDS:
                del state[tid]

    def _restricted_hits(self, zone, person_boxes, track_ids, occluded,
                          width, height, now) -> List[Tuple[int, float]]:
        points = zone.get("points", [])
        if len(points) < 3:
            return []
        polygon = _to_pixels(points, width, height)
        zone_id = zone.get("id", zone.get("name", "zone"))
        state = self._track_state.setdefault(zone_id, {})
        margin = _margin_px(width)

        hits: List[Tuple[int, float]] = []
        seen_tracks = set()
        for idx, box in enumerate(person_boxes):
            tid = self._real_track_id(track_ids, occluded, idx)
            if tid is None:
                continue
            seen_tracks.add(tid)
            st = state.setdefault(tid, {"confirmed": False, "candidate_since": None,
                                         "candidate_frames": 0, "last_seen": now})
            st["last_seen"] = now

            # Khoảng cách có dấu tới mép đa giác (px): dương = trong, âm = ngoài
            dist = cv2.pointPolygonTest(polygon, _feet_point(box), True)

            if st["confirmed"]:
                if dist < -margin:
                    # Ra khỏi đa giác đã nới rộng thêm biên độ -> thật sự đã rời vùng
                    st["confirmed"] = False
                    st["candidate_since"] = None
                    st["candidate_frames"] = 0
                continue

            if dist >= margin:
                # Lún sâu hơn biên độ vào trong đa giác thu hẹp -> tích dwell
                if st["candidate_since"] is None:
                    st["candidate_since"] = now
                    st["candidate_frames"] = 1
                else:
                    st["candidate_frames"] += 1
                dwell = (now - st["candidate_since"]).total_seconds()
                if st["candidate_frames"] >= INTRUSION_MIN_FRAMES and dwell >= INTRUSION_MIN_DWELL_S:
                    st["confirmed"] = True
                    hits.append((idx, dwell))
            else:
                # Trong vùng đệm quanh mép (hoặc rõ ràng ở ngoài) -> chưa đủ rõ,
                # không tích dồn để rung nhiễu quanh mép không gây báo động giả
                st["candidate_since"] = None
                st["candidate_frames"] = 0

        self._prune_stale(state, seen_tracks, now)
        return hits

    def _crossing_hits(self, zone, person_boxes, track_ids, occluded,
                        width, height, now) -> List[Tuple[int, float]]:
        points = zone.get("points", [])
        if len(points) < 2:
            return []
        zone_id = zone.get("id", zone.get("name", "zone"))
        p1 = (points[0]["x"] * width, points[0]["y"] * height)
        p2 = (points[1]["x"] * width, points[1]["y"] * height)
        line_len = max(1e-6, ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5)
        margin = _margin_px(width)
        direction = zone.get("direction", "any")
        state = self._track_state.setdefault(zone_id, {})

        hits: List[Tuple[int, float]] = []
        seen_tracks = set()
        for idx, box in enumerate(person_boxes):
            tid = self._real_track_id(track_ids, occluded, idx)
            if tid is None:
                continue
            seen_tracks.add(tid)
            st = state.setdefault(tid, {"side": None, "candidate_side": None,
                                         "candidate_since": None, "candidate_frames": 0,
                                         "last_seen": now})
            st["last_seen"] = now

            # Khoảng cách có dấu tới vạch, cùng đơn vị px với margin
            dist = _cross_product(p1, p2, _feet_point(box)) / line_len
            if abs(dist) < margin:
                # Sát vạch: chưa đủ rõ đang ở phía nào, không tích dồn
                st["candidate_side"] = None
                st["candidate_frames"] = 0
                continue

            side = SIDE_A if dist > 0 else SIDE_B
            if st["candidate_side"] != side:
                st["candidate_side"] = side
                st["candidate_since"] = now
                st["candidate_frames"] = 1
            else:
                st["candidate_frames"] += 1

            dwell = (now - st["candidate_since"]).total_seconds()
            if st["candidate_frames"] >= INTRUSION_MIN_FRAMES and dwell >= INTRUSION_MIN_DWELL_S:
                previous_side = st["side"]
                st["side"] = side
                st["candidate_side"] = None
                st["candidate_frames"] = 0
                # previous_side None = lần đầu xác định phía của track này, chỉ
                # làm mốc chứ không tính là cắt qua
                if previous_side is not None and previous_side != side \
                        and _direction_allows(direction, previous_side, side):
                    hits.append((idx, dwell))

        self._prune_stale(state, seen_tracks, now)
        return hits
