"""Theo dõi sự hiện diện của từng quân nhân trong suốt buổi học.

Hai mốc điểm danh đầu/cuối giờ chỉ cho biết có mặt hay vắng tại đúng hai thời
điểm. Suy trạng thái vi phạm từ đó thì người **vừa đi chậm vừa về sớm** sẽ vắng
ở cả hai mốc và bị xếp nhầm thành "không tham gia". Vì vậy ở đây ghi mốc thấy
đầu tiên và cuối cùng của mỗi người trong cả buổi, rồi mới suy ra trạng thái.
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app import clock

# Số lượt quét tối thiểu để coi là thật sự có mặt, chống một lần nhận nhầm
MIN_SIGHTINGS = 3
# Các lượt quét phải trải dài ít nhất ngần này — 3 khung dính liền hoặc 3 lần
# nhận nhầm trong chớp mắt vẫn đủ MIN_SIGHTINGS nhưng không phải "có mặt thật"
MIN_PRESENCE_SPAN_S = float(os.environ.get("MIN_PRESENCE_SPAN_S", "2.0"))

STATUS_PRESENT = "present"
STATUS_VIOLATION = "violation"
STATUS_ABSENT = "absent"

VIOLATION_LATE = "late"
VIOLATION_EARLY_LEAVE = "early_leave"
VIOLATION_ABSENT = "absent"


class SessionPresence:
    """Dấu vết hiện diện của một buổi học (một ca trong một ngày)."""

    def __init__(self, planned_start: datetime, planned_end: Optional[datetime],
                 late_tolerance_mins: int, early_leave_tolerance_mins: int,
                 watching_since: Optional[datetime] = None):
        # Thời điểm hệ thống bắt đầu quan sát buổi này. Vào muộn (máy chủ khởi
        # động lại giữa buổi) thì không thể biết ai đã có mặt từ trước, nên không
        # được phép kết luận đi chậm.
        self.watching_since = watching_since or planned_start
        self.planned_start = planned_start
        self.planned_end = planned_end
        self.late_tolerance_mins = late_tolerance_mins
        self.early_leave_tolerance_mins = early_leave_tolerance_mins
        # person_id -> {"first": datetime, "last": datetime, "sightings": int}
        self.seen: Dict[str, dict] = {}
        # Người đã bắn sự kiện đi chậm rồi thì không bắn lại
        self.late_reported: set = set()

    def record(self, person_ids: List[str], now: datetime, frame_seq: Optional[int] = None) -> None:
        """Ghi một lượt quét. ``frame_seq`` (tuỳ chọn) là số thứ tự khung hình
        gốc — truyền vào thì mới đòi hỏi các lượt quét đến từ các khung hình
        KHÁC NHAU, không chỉ khác nhau về thời điểm ghi nhận."""
        for pid in set(person_ids):
            entry = self.seen.get(pid)
            if entry is None:
                entry = {"first": now, "last": now, "sightings": 1, "frame_seqs": None}
                self.seen[pid] = entry
            else:
                entry["last"] = now
                entry["sightings"] += 1
            if frame_seq is not None:
                if entry["frame_seqs"] is None:
                    entry["frame_seqs"] = set()
                entry["frame_seqs"].add(frame_seq)

    def _confirmed(self, pid: str) -> Optional[dict]:
        """Dấu vết của một người, chỉ tính khi đủ số lượt quét tối thiểu VÀ các
        lượt đó trải dài đủ lâu — chặn cả khung trùng/lỗi lẫn nhận nhầm dồn dập.
        """
        entry = self.seen.get(pid)
        if entry is None or entry["sightings"] < MIN_SIGHTINGS:
            return None
        if (entry["last"] - entry["first"]).total_seconds() < MIN_PRESENCE_SPAN_S:
            return None
        frame_seqs = entry.get("frame_seqs")
        if frame_seqs is not None and len(frame_seqs) < MIN_SIGHTINGS:
            return None
        return entry

    @property
    def can_judge_late(self) -> bool:
        """Chỉ kết luận đi chậm khi đã quan sát từ trước lúc hết hạn có mặt."""
        return self.watching_since <= self.planned_start + timedelta(minutes=self.late_tolerance_mins)

    def late_minutes(self, pid: str) -> int:
        entry = self._confirmed(pid)
        if entry is None or not self.can_judge_late:
            return 0
        deadline = self.planned_start + timedelta(minutes=self.late_tolerance_mins)
        if entry["first"] <= deadline:
            return 0
        return int((entry["first"] - self.planned_start).total_seconds() // 60)

    def newly_late(self, pid: str) -> bool:
        """Đúng một lần cho mỗi người: vừa xác định được là đi chậm."""
        if pid in self.late_reported or self.late_minutes(pid) <= 0:
            return False
        self.late_reported.add(pid)
        return True

    def build_records(self, roster: List[dict], now: datetime) -> tuple:
        """Bảng trạng thái từng quân nhân + phần tổng hợp.

        ``now`` để biết buổi đã đủ muộn để kết luận "về sớm" hay chưa — trong lúc
        buổi còn đang diễn ra thì ai cũng "chưa thấy ở cuối giờ", kết luận sớm là sai.
        """
        end_deadline = None
        if self.planned_end is not None:
            end_deadline = self.planned_end - timedelta(minutes=self.early_leave_tolerance_mins)
        can_judge_early_leave = end_deadline is not None and now >= end_deadline

        items = []
        summary = {"required": len(roster), "present": 0, "absent": 0, "late": 0, "early_leave": 0}

        for person in roster:
            pid = person.get("id")
            entry = self._confirmed(pid)
            violations = []
            first_seen = last_seen = None
            late_mins = early_mins = None

            if entry is None:
                violations.append(VIOLATION_ABSENT)
                status = STATUS_ABSENT
            else:
                first_seen, last_seen = entry["first"], entry["last"]

                late_mins = self.late_minutes(pid)
                if late_mins > 0:
                    violations.append(VIOLATION_LATE)

                if can_judge_early_leave and last_seen < end_deadline:
                    early_mins = int((self.planned_end - last_seen).total_seconds() // 60)
                    violations.append(VIOLATION_EARLY_LEAVE)

                status = STATUS_VIOLATION if violations else STATUS_PRESENT

            if status == STATUS_ABSENT:
                summary["absent"] += 1
            elif status == STATUS_PRESENT:
                summary["present"] += 1
            if VIOLATION_LATE in violations:
                summary["late"] += 1
            if VIOLATION_EARLY_LEAVE in violations:
                summary["early_leave"] += 1

            items.append({
                "person": person,
                "status": status,
                "violations": violations,
                "first_seen": clock.iso(first_seen) if first_seen else None,
                "last_seen": clock.iso(last_seen) if last_seen else None,
                "total_seconds": int((last_seen - first_seen).total_seconds()) if entry else 0,
                "late_minutes": late_mins if late_mins else None,
                "early_leave_minutes": early_mins,
                "present_at_start": bool(entry) and late_mins == 0,
                "present_at_end": bool(entry) and VIOLATION_EARLY_LEAVE not in violations,
            })

        return items, summary

    def actual_minutes(self, now: datetime) -> int:
        """Số phút lớp thực sự diễn ra: từ lúc thấy người đầu tiên tới lúc thấy cuối cùng."""
        if not self.seen:
            return 0
        first = min(e["first"] for e in self.seen.values())
        last = max(e["last"] for e in self.seen.values())
        return max(0, int((last - first).total_seconds() // 60))


class PresenceTracker:
    """Giữ dấu vết hiện diện của các buổi đang chạy, khoá theo ``ca:ngày``."""

    def __init__(self):
        self.sessions: Dict[str, SessionPresence] = {}

    def get_or_create(self, key: str, planned_start: datetime, planned_end: Optional[datetime],
                      late_tolerance_mins: int, early_leave_tolerance_mins: int,
                      now: Optional[datetime] = None) -> SessionPresence:
        presence = self.sessions.get(key)
        if presence is None:
            presence = SessionPresence(planned_start, planned_end,
                                       late_tolerance_mins, early_leave_tolerance_mins,
                                       watching_since=now)
            self.sessions[key] = presence
        return presence

    def get(self, key: str) -> Optional[SessionPresence]:
        return self.sessions.get(key)
