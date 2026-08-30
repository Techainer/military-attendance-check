"""Giám sát an toàn: phát hiện đối tượng đi vào vùng cấm hoặc vượt vạch an toàn.

Polygon vốn đã có sẵn trong hệ thống nhưng chỉ dùng để *lọc* người được tính vào
sĩ số. Ở đây polygon được gán thêm ngữ nghĩa qua trường ``rule``:

- ``attendance_area``  : chỉ đếm người bên trong (hành vi cũ, giữ nguyên)
- ``restricted_area``  : người bên trong là **vi phạm** — khối chắn trường bắn
- ``crossing_line``    : sinh vi phạm khi có người cắt qua vạch
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

RULE_ATTENDANCE = "attendance_area"
RULE_RESTRICTED = "restricted_area"
RULE_CROSSING = "crossing_line"

# Không bắn lại vi phạm của cùng một vùng trong khoảng này
INTRUSION_COOLDOWN_SECONDS = 20


def _to_pixels(points: List[dict], width: int, height: int) -> np.ndarray:
    return np.array([[int(p["x"] * width), int(p["y"] * height)] for p in points], np.int32)


def _feet_point(box) -> tuple:
    x1, y1, x2, y2 = box
    return ((float(x1) + float(x2)) / 2.0, float(y2) - 2)


def _center_point(box) -> tuple:
    x1, y1, x2, y2 = box
    return ((float(x1) + float(x2)) / 2.0, (float(y1) + float(y2)) / 2.0)


def _line_side(p1, p2, pt) -> int:
    """Điểm nằm phía nào của vạch: trả về 1, -1 hoặc 0."""
    cross = (p2[0] - p1[0]) * (pt[1] - p1[1]) - (p2[1] - p1[1]) * (pt[0] - p1[0])
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0


class ZoneStore:
    """Đọc cấu hình vùng từ ``data/zone_rules.json``, chỉ đọc lại khi file đổi."""

    def __init__(self, data_dir: str):
        self.zone_file = Path(data_dir) / "zone_rules.json"
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
        """Các vùng đang bật — phần vòng xử lý video thực sự dùng."""
        return [z for z in self.all_zones() if z.get("enabled", True)]

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
    """Soi từng khung hình xem có ai vào vùng cấm hoặc vượt vạch an toàn không."""

    def __init__(self, zone_store: ZoneStore):
        self.zones = zone_store
        # zone_id -> lần bắn vi phạm gần nhất
        self._last_fired: Dict[str, datetime] = {}
        # zone_id -> thời điểm vùng bắt đầu có người, để tính dwell_seconds
        self._occupied_since: Dict[str, datetime] = {}
        # zone_id -> {track_id: phía của vạch ở khung hình trước}
        self._track_side: Dict[str, Dict[int, int]] = {}

    def check(self, person_boxes, track_ids, width: int, height: int, now: datetime) -> List[dict]:
        """Trả về danh sách vi phạm mới phát hiện trong khung hình này.

        Mỗi phần tử: ``{"zone", "indices", "dwell_seconds"}`` — ``indices`` là vị
        trí của các box vi phạm trong ``person_boxes``.
        """
        violations = []
        for zone in self.zones.zones():
            rule = zone.get("rule")
            if rule == RULE_RESTRICTED:
                indices = self._inside_polygon(zone, person_boxes, width, height)
            elif rule == RULE_CROSSING:
                indices = self._crossed_line(zone, person_boxes, track_ids, width, height)
            else:
                continue

            zone_id = zone.get("id", zone.get("name", "zone"))
            if not indices:
                self._occupied_since.pop(zone_id, None)
                continue

            since = self._occupied_since.setdefault(zone_id, now)

            last = self._last_fired.get(zone_id)
            if last is not None and (now - last).total_seconds() < INTRUSION_COOLDOWN_SECONDS:
                continue
            self._last_fired[zone_id] = now

            violations.append({
                "zone": zone,
                "indices": indices,
                "dwell_seconds": int((now - since).total_seconds()),
            })
        return violations

    def _inside_polygon(self, zone, person_boxes, width, height) -> List[int]:
        points = zone.get("points", [])
        if len(points) < 3:
            return []
        polygon = _to_pixels(points, width, height)
        return [
            idx for idx, box in enumerate(person_boxes)
            if cv2.pointPolygonTest(polygon, _feet_point(box), False) >= 0
        ]

    def _crossed_line(self, zone, person_boxes, track_ids, width, height) -> List[int]:
        points = zone.get("points", [])
        if len(points) < 2:
            return []
        zone_id = zone.get("id", zone.get("name", "zone"))
        p1 = (points[0]["x"] * width, points[0]["y"] * height)
        p2 = (points[1]["x"] * width, points[1]["y"] * height)
        sides = self._track_side.setdefault(zone_id, {})

        crossed = []
        seen_tracks = set()
        for idx, box in enumerate(person_boxes):
            track_id = track_ids[idx] if idx < len(track_ids) else None
            if track_id is None or track_id < 0:
                continue
            seen_tracks.add(track_id)
            side = _line_side(p1, p2, _feet_point(box))
            if side == 0:
                continue
            previous = sides.get(track_id)
            sides[track_id] = side
            # Đổi phía so với khung hình trước nghĩa là vừa cắt qua vạch
            if previous is not None and previous != side:
                crossed.append(idx)

        for track_id in [t for t in sides if t not in seen_tracks]:
            del sides[track_id]
        return crossed
