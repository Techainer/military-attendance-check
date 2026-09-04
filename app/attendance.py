"""Roll-call session logic: điểm danh N phút đầu giờ và N phút cuối giờ học."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2

from app import clock
from app.events import TYPE_EARLY_LEAVE, TYPE_LATE
from app.presence import PresenceTracker, VIOLATION_EARLY_LEAVE
from app.storage import read_json_list, write_json_list


DEFAULT_WINDOW_MINS = 5
# Ca chưa gán camera thì thuộc về camera mặc định
DEFAULT_CAMERA_ID = "cam_01"
# Số lượt quét tối thiểu để coi là có mặt (chống nhận nhầm 1 frame)
MIN_SIGHTINGS = 3
# Cửa sổ còn lại ít hơn ngần này thì không mở phiên nữa (mở ra cũng không kịp đếm)
MIN_OPEN_REMAINING_SECONDS = 15

PHASE_START = "start"
PHASE_END = "end"
PHASE_MANUAL = "manual"

PHASE_LABELS = {
    PHASE_START: "Đầu giờ",
    PHASE_END: "Cuối giờ",
    PHASE_MANUAL: "Đột xuất",
}

# Trạng thái vận hành của một ca trong ngày
STATE_UPCOMING = "upcoming"
STATE_CHECK_START = "check_start"
STATE_RUNNING = "running"
STATE_CHECK_END = "check_end"
STATE_FINISHED = "finished"

STATE_LABELS = {
    STATE_UPCOMING: "Chưa tới giờ",
    STATE_CHECK_START: "Đang điểm danh đầu giờ",
    STATE_RUNNING: "Đang diễn ra",
    STATE_CHECK_END: "Đang điểm danh cuối giờ",
    STATE_FINISHED: "Đã kết thúc",
}


def person_label(person: dict) -> str:
    """Chuỗi hiển thị 'cấp bậc + họ tên' của một quân nhân."""
    return f"{person.get('rank', '')} {person.get('name', '')}".strip() or person.get("military_id", "?")


def _tolerance_mins(schedule: dict, field: str, default: int = 5) -> int:
    try:
        value = int(schedule.get(field))
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _schedule_window_mins(schedule: dict) -> int:
    """Số phút dùng để điểm danh mỗi mốc (ca cũ không khai thì lấy mặc định)."""
    raw = schedule.get("check_window_mins", schedule.get("tolerance_mins"))
    try:
        mins = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_MINS
    return mins if mins > 0 else DEFAULT_WINDOW_MINS


def _time_on(day: datetime, raw_time) -> Optional[datetime]:
    """Ghép chuỗi 'HH:MM' vào ngày đang xét."""
    parts = str(raw_time or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return day.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
    except ValueError:
        return None


def _occurrence(schedule: dict, now: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Lần diễn ra của ca ứng với thời điểm ``now``.

    Ca qua đêm (22:00 → 06:00) sau nửa đêm vẫn thuộc về lần diễn ra bắt đầu từ
    hôm trước. Neo cứng vào ngày của ``now`` thì suốt nửa sau của ca, giờ bắt đầu
    lại rơi vào tương lai và ca bị coi như chưa tới giờ.
    """
    def anchored(day: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
        start = _time_on(day, schedule.get("start_time"))
        end = _time_on(day, schedule.get("end_time"))
        if start is not None and end is not None and end <= start:
            end += timedelta(days=1)
        return start, end

    for day_offset in (0, -1):
        start, end = anchored(now + timedelta(days=day_offset))
        if start is None:
            return None, None
        if end is not None and start <= now < end:
            return start, end

    # Không lần nào đang diễn ra: lấy lần của hôm nay để biết sắp tới hay đã xong
    return anchored(now)


def _start_datetime(schedule: dict, now: datetime) -> Optional[datetime]:
    """Giờ bắt đầu của lần diễn ra ứng với ``now``."""
    return _occurrence(schedule, now)[0]


def _end_datetime(schedule: dict, now: datetime) -> Optional[datetime]:
    """Giờ kết thúc của lần diễn ra ứng với ``now``."""
    return _occurrence(schedule, now)[1]


def schedule_windows(schedule: dict, now: datetime) -> List[Tuple[str, datetime, datetime]]:
    """Các cửa sổ điểm danh của ca trong ngày: đầu giờ và cuối giờ."""
    start = _start_datetime(schedule, now)
    if start is None:
        return []

    mins = _schedule_window_mins(schedule)
    windows = [(PHASE_START, start, start + timedelta(minutes=mins))]

    end = _end_datetime(schedule, now)
    if end is not None:
        # Điểm danh cuối giờ = N phút cuối TRƯỚC giờ kết thúc, và không được
        # đè lên cửa sổ đầu giờ khi ca quá ngắn.
        end_win_start = max(end - timedelta(minutes=mins), start + timedelta(minutes=mins))
        if end > end_win_start:
            windows.append((PHASE_END, end_win_start, end))

    return windows


def schedule_runtime_state(schedule: dict, now: datetime) -> dict:
    """Trạng thái thực tế của ca tại thời điểm ``now`` (thay cho cờ Active cố định)."""
    start = _start_datetime(schedule, now)
    end = _end_datetime(schedule, now)
    if start is None:
        return {"state": STATE_UPCOMING, "state_label": STATE_LABELS[STATE_UPCOMING]}

    windows = {phase: (w_start, w_end) for phase, w_start, w_end in schedule_windows(schedule, now)}

    if now < start:
        state = STATE_UPCOMING
    elif end is not None and now >= end:
        state = STATE_FINISHED
    elif PHASE_START in windows and windows[PHASE_START][0] <= now < windows[PHASE_START][1]:
        state = STATE_CHECK_START
    elif PHASE_END in windows and windows[PHASE_END][0] <= now < windows[PHASE_END][1]:
        state = STATE_CHECK_END
    else:
        state = STATE_RUNNING

    return {"state": state, "state_label": STATE_LABELS[state]}


class AttendanceSession:
    """Một phiên điểm danh: gom kết quả nhận diện trong suốt cửa sổ N phút."""

    def __init__(
        self,
        schedule: dict,
        roster: List[dict],
        started_at: datetime,
        window_mins: int,
        phase: str = PHASE_MANUAL,
        ends_at: Optional[datetime] = None
    ):
        self.schedule = schedule
        self.roster = roster
        self.started_at = started_at
        self.window_mins = window_mins
        self.phase = phase
        self.ends_at = ends_at or (started_at + timedelta(minutes=window_mins))
        # person_id -> số lượt quét nhận ra người này
        self.sightings: Dict[str, int] = {}
        self.scans = 0
        # Khung hình có nhiều quân nhân được định danh nhất, dùng làm bằng chứng
        self.evidence_frame = None
        self.evidence_quality = -1

    @property
    def key(self) -> str:
        return f"{self.schedule.get('id', 'manual')}:{self.started_at.date().isoformat()}:{self.phase}"

    @property
    def phase_label(self) -> str:
        return PHASE_LABELS.get(self.phase, PHASE_LABELS[PHASE_MANUAL])

    def record(self, person_ids: List[str], frame=None, quality: int = 0) -> None:
        """Ghi nhận một lượt quét và giữ lại khung hình tốt nhất làm bằng chứng."""
        self.scans += 1
        for pid in set(person_ids):
            self.sightings[pid] = self.sightings.get(pid, 0) + 1

        if frame is not None and quality > self.evidence_quality:
            self.evidence_quality = quality
            self.evidence_frame = frame.copy()

    def remaining_seconds(self, now: datetime) -> float:
        return max(0.0, (self.ends_at - now).total_seconds())

    def is_due(self, now: datetime) -> bool:
        return now >= self.ends_at

    def present_ids(self) -> set:
        return {pid for pid, hits in self.sightings.items() if hits >= MIN_SIGHTINGS}

    def present_count(self) -> int:
        return len(self.present_ids())

    def required_count(self) -> int:
        raw = self.schedule.get("required_count")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return len(self.roster)

    def build_check(self, closed_at: datetime) -> dict:
        """Kết quả của riêng mốc điểm danh này."""
        present = self.present_ids()
        present_people = [p for p in self.roster if p.get("id") in present]
        absent_people = [p for p in self.roster if p.get("id") not in present]
        label = person_label

        return {
            "phase": self.phase,
            "phase_label": self.phase_label,
            "time": closed_at.strftime("%H:%M"),
            "present": len(present),
            "absent": len(absent_people),
            "present_personnel": [label(p) for p in present_people],
            "absent_personnel": [label(p) for p in absent_people],
            "evidence": None,
            "window_mins": self.window_mins,
            "started_at": clock.iso(self.started_at),
            "scans": self.scans,
        }


class AttendanceManager:
    """Mở/đóng phiên điểm danh theo thời khoá biểu và ghi nhật ký."""

    def __init__(self, data_dir: str, face_engine, events=None, camera_id: Optional[str] = None):
        self.data_dir = Path(data_dir)
        self.schedules_file = self.data_dir / "schedules.json"
        self.logs_file = self.data_dir / "attendance_logs.json"
        self.evidence_dir = self.data_dir / "attendance_evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.face_engine = face_engine
        self.events = events
        # ``None`` = nhận mọi ca. Mỗi camera chạy một bản riêng và chỉ nhận ca
        # được giao cho nó, nếu không hai camera sẽ cùng điểm danh một lớp.
        self.camera_id = camera_id
        self.presence = PresenceTracker()
        self.session: Optional[AttendanceSession] = None
        self.completed_keys = self._load_completed_keys()
        self._schedules_cache: List[dict] = []
        self._schedules_mtime = None

    # ---------- persistence ----------

    def _load_schedules(self) -> List[dict]:
        """Đọc lại schedules.json chỉ khi file đổi (hàm này bị gọi mỗi frame)."""
        if not self.schedules_file.exists():
            self._schedules_cache = []
            self._schedules_mtime = None
            return self._schedules_cache

        mtime = self.schedules_file.stat().st_mtime
        if mtime != self._schedules_mtime:
            rows = read_json_list(self.schedules_file)
            if self.camera_id is not None:
                # Ca chưa khai camera thì giao cho camera mặc định, giữ nguyên
                # hành vi của cấu hình cũ khi mới có một camera.
                rows = [s for s in rows
                        if (s.get("camera_id") or DEFAULT_CAMERA_ID) == self.camera_id]
            self._schedules_cache = rows
            self._schedules_mtime = mtime
        return self._schedules_cache

    def _load_completed_keys(self) -> set:
        """Mốc nào đã điểm danh trong ngày rồi thì không chạy lại."""
        keys = set()
        for log in read_json_list(self.logs_file):
            sch_id = log.get("schedule_id")
            if not sch_id:
                continue
            day = log.get("date_iso") or str(log.get("started_at", ""))[:10]
            checks = log.get("checks")
            if isinstance(checks, dict) and checks:
                for phase in checks:
                    keys.add(f"{sch_id}:{day}:{phase}")
            elif day:
                # Bản ghi theo định dạng cũ chỉ có một mốc đầu giờ
                keys.add(f"{sch_id}:{day}:{PHASE_START}")
        return keys

    def _save_evidence(self, frame, log_id: str, phase: str) -> Optional[str]:
        """Lưu khung hình bằng chứng, trả về đường dẫn phục vụ cho web."""
        if frame is None:
            return None
        filename = f"{log_id}_{phase}.jpg"
        try:
            cv2.imwrite(str(self.evidence_dir / filename), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        except Exception as e:
            print(f"[Attendance] Lỗi lưu ảnh bằng chứng: {e}")
            return None
        return f"/data/attendance_evidence/{filename}"

    def _summarize(self, log: dict) -> dict:
        """Cập nhật phần tổng hợp của bản ghi từ các mốc đã chốt."""
        checks = log.get("checks", {})
        ordered = [checks[p] for p in (PHASE_START, PHASE_END, PHASE_MANUAL) if p in checks]
        if not ordered:
            return log

        latest = ordered[-1]
        absent_union = []
        for chk in ordered:
            for name in chk.get("absent_personnel", []):
                if name not in absent_union:
                    absent_union.append(name)

        log["time"] = latest["time"]
        log["present"] = latest["present"]
        log["absent"] = len(absent_union)
        log["absent_personnel"] = absent_union
        log["status"] = "Đủ quân số" if not absent_union else f"Thiếu {len(absent_union)} quân nhân"
        log["status_type"] = "success" if not absent_union else "warning"
        return log

    def _write_check(self, session: AttendanceSession, check: dict, closed_at: datetime) -> dict:
        """Ghi kết quả một mốc vào nhật ký, gộp chung dòng của ca trong ngày."""
        logs = read_json_list(self.logs_file)
        schedule_id = session.schedule.get("id", "manual")
        date_iso = session.started_at.date().isoformat()

        target = None
        if session.phase != PHASE_MANUAL:
            for log in logs:
                if log.get("schedule_id") == schedule_id and (
                    log.get("date_iso") or str(log.get("started_at", ""))[:10]
                ) == date_iso:
                    target = log
                    break

        if target is None:
            target = {
                "id": f"log_{int(closed_at.timestamp() * 1000)}",
                "schedule_id": schedule_id,
                "date": session.started_at.strftime("%d/%m/%Y"),
                "date_iso": date_iso,
                "shift": session.schedule.get("shift", "Điểm danh"),
                "schedule_name": session.schedule.get("name", ""),
                "unit": session.schedule.get("unit", "Tất cả đơn vị"),
                "required": session.required_count(),
                "checks": {},
            }
            logs.insert(0, target)

        target.setdefault("checks", {})
        target.setdefault("date_iso", date_iso)
        target["required"] = session.required_count()
        check["evidence"] = self._save_evidence(session.evidence_frame, target["id"], session.phase)
        target["checks"][session.phase] = check
        self._summarize(target)
        self._attach_attendance_table(target, session, closed_at)

        write_json_list(self.logs_file, logs)
        return target

    def _attach_attendance_table(self, target: dict, session: AttendanceSession,
                                 closed_at: datetime) -> None:
        """Ghi bảng trạng thái từng quân nhân (đi chậm / về sớm / không tham gia).

        Chỉ ghi khi có dấu vết hiện diện thật — máy chủ khởi động lại giữa buổi
        thì dấu vết mất, lúc đó thà không có bảng còn hơn ghi nhầm tất cả là vắng.
        """
        presence = self._presence_for(session.schedule, closed_at)
        if presence is None or not presence.seen:
            return

        items, summary = presence.build_records(session.roster, closed_at)
        target["session_id"] = self._presence_key(session.schedule, presence.planned_start)
        target["attendance"] = items
        target["attendance_summary"] = summary
        target["actual_minutes"] = presence.actual_minutes(closed_at)
        if presence.planned_end is not None:
            scheduled = int((presence.planned_end - presence.planned_start).total_seconds() // 60)
            target["scheduled_minutes"] = scheduled
            target["progress_pct"] = round(
                min(100.0, target["actual_minutes"] / scheduled * 100), 1
            ) if scheduled > 0 else 0.0

        if session.phase == PHASE_END:
            self._emit_early_leave_events(session.schedule, presence, items, session.evidence_frame)

    # ---------- roster ----------

    def _roster_for(self, unit: Optional[str]) -> List[dict]:
        """Danh sách quân nhân của đơn vị áp dụng cho ca.

        Đơn vị chưa đăng ký ai thì roster rỗng, không rơi về toàn bộ CSDL.
        """
        return self.face_engine.get_registered_faces(unit=unit)

    # ---------- dấu vết hiện diện cả buổi ----------

    def _active_schedule(self, now: datetime) -> Optional[dict]:
        """Ca đang trong khung giờ tại thời điểm ``now``."""
        for schedule in self._load_schedules():
            start = _start_datetime(schedule, now)
            end = _end_datetime(schedule, now)
            if start is None or end is None:
                continue
            if start <= now < end:
                return schedule
        return None

    def _presence_key(self, schedule: dict, now: datetime) -> Optional[str]:
        """Khoá của một buổi = ``mã ca:ngày diễn ra``.

        Cũng chính là ``session_id`` gắn vào sự kiện và nhận ở API, để lọc được
        vi phạm theo từng buổi.
        """
        start = _start_datetime(schedule, now)
        if start is None:
            return None
        return f"{schedule.get('id', 'manual')}:{start.date().isoformat()}"

    def _presence_for(self, schedule: dict, now: datetime, create: bool = False):
        """Dấu vết hiện diện của ca trong ngày. ``create=False`` thì không tự tạo mới."""
        key = self._presence_key(schedule, now)
        if key is None:
            return None
        if not create:
            return self.presence.get(key)
        return self.presence.get_or_create(
            key,
            _start_datetime(schedule, now),
            _end_datetime(schedule, now),
            _tolerance_mins(schedule, "late_tolerance_mins"),
            _tolerance_mins(schedule, "early_leave_tolerance_mins"),
            now=now,
        )

    def _emit_late_events(self, schedule: dict, presence, person_ids: List[str], frame) -> None:
        """Bắn sự kiện đi chậm, mỗi quân nhân đúng một lần trong buổi."""
        if self.events is None:
            return
        newly = [pid for pid in set(person_ids) if presence.newly_late(pid)]
        if not newly:
            return

        session_id = self._presence_key(schedule, presence.planned_start)
        roster = {p.get("id"): p for p in self._roster_for(schedule.get("unit"))}
        for pid in newly:
            person = roster.get(pid)
            if person is None:
                continue
            minutes = presence.late_minutes(pid)
            name = person_label(person)
            self.events.emit(
                TYPE_LATE,
                f"{name} đi chậm {minutes} phút so với giờ tập trung.",
                severity="warning",
                frame=frame,
                person_id=pid,
                person_name=name,
                session_id=session_id,
                schedule_id=schedule.get("id"),
                detail={
                    "late_minutes": minutes,
                    "first_seen": clock.iso(presence.seen[pid]["first"]),
                    "planned_start": clock.iso(presence.planned_start),
                    "tolerance_mins": presence.late_tolerance_mins,
                },
            )

    def _emit_early_leave_events(self, schedule: dict, presence, items: List[dict], frame) -> None:
        """Bắn sự kiện về sớm, chỉ chốt được khi buổi đã kết thúc."""
        if self.events is None:
            return
        session_id = self._presence_key(schedule, presence.planned_start)
        for item in items:
            if VIOLATION_EARLY_LEAVE not in item.get("violations", []):
                continue
            name = person_label(item["person"])
            self.events.emit(
                TYPE_EARLY_LEAVE,
                f"{name} rời thao trường sớm {item['early_leave_minutes']} phút trước giờ kết thúc.",
                severity="warning",
                frame=frame,
                person_id=item["person"].get("id"),
                person_name=name,
                session_id=session_id,
                schedule_id=schedule.get("id"),
                detail={
                    "early_leave_minutes": item["early_leave_minutes"],
                    "last_seen": item["last_seen"],
                    "planned_end": clock.iso(presence.planned_end) if presence.planned_end else None,
                },
            )

    def observe(self, person_ids: List[str], now: datetime, frame=None, quality: int = 0) -> None:
        """Ghi nhận một lượt quét: vừa nuôi dấu vết cả buổi, vừa nuôi phiên đang mở.

        Khác với ``record``, hàm này chạy trên **mọi** khung hình chứ không chỉ
        trong hai cửa sổ điểm danh — có vậy mới biết ai đến muộn, ai về sớm.
        """
        schedule = self._active_schedule(now)
        if schedule is not None:
            presence = self._presence_for(schedule, now, create=True)
            if presence is not None:
                presence.record(person_ids, now)
                self._emit_late_events(schedule, presence, person_ids, frame)

        self.record(person_ids, frame=frame, quality=quality)

    def attendance_table(self, schedule: dict, roster: List[dict], now: datetime) -> tuple:
        """Bảng trạng thái từng quân nhân của ca trong ngày, kèm phần tổng hợp."""
        presence = self._presence_for(schedule, now)
        if presence is None:
            return [], None
        return presence.build_records(roster, now)

    # ---------- session lifecycle ----------

    def open_session(
        self,
        schedule: dict,
        now: datetime,
        window_mins: Optional[int] = None,
        phase: str = PHASE_MANUAL,
        ends_at: Optional[datetime] = None
    ) -> AttendanceSession:
        mins = window_mins if (window_mins and window_mins > 0) else _schedule_window_mins(schedule)
        roster = self._roster_for(schedule.get("unit"))
        self.session = AttendanceSession(schedule, roster, now, mins, phase=phase, ends_at=ends_at)
        print(f"[Attendance] Mở phiên điểm danh {self.session.phase_label.lower()} "
              f"'{schedule.get('name', 'thủ công')}' ({mins} phút, {len(roster)} quân nhân trong danh sách)")
        return self.session

    def start_manual(self, now: datetime, schedule_id: Optional[str] = None,
                     window_mins: Optional[int] = None) -> AttendanceSession:
        """Điểm danh đột xuất / dùng khi demo, không chờ tới giờ ca."""
        schedule = None
        if schedule_id:
            schedule = next((s for s in self._load_schedules() if s.get("id") == schedule_id), None)
        if schedule is None:
            # Phiên đột xuất mang id riêng, không được đánh dấu ca nào đã điểm danh xong
            schedule = {
                "id": f"manual_{int(now.timestamp())}",
                "name": "Điểm danh đột xuất",
                "shift": "Đột xuất",
                "unit": "Tất cả đơn vị",
            }
        return self.open_session(dict(schedule), now, window_mins, phase=PHASE_MANUAL)

    def maybe_open_scheduled(self, now: datetime) -> Optional[AttendanceSession]:
        """Tới cửa sổ điểm danh đầu giờ hoặc cuối giờ thì tự mở phiên."""
        if self.session is not None:
            return None

        for schedule in self._load_schedules():
            for phase, win_start, win_end in schedule_windows(schedule, now):
                if not (win_start <= now < win_end):
                    continue
                if (win_end - now).total_seconds() < MIN_OPEN_REMAINING_SECONDS:
                    # Hệ thống vào quá muộn: không chốt biên bản nửa vời, cũng không
                    # đánh dấu mốc này đã xong để còn chạy lại nếu mở sớm hơn
                    continue
                key = f"{schedule.get('id')}:{win_start.date().isoformat()}:{phase}"
                if key in self.completed_keys:
                    continue
                # Phiên tính từ đầu cửa sổ và luôn chốt đúng cuối cửa sổ, kể cả
                # khi luồng video vào muộn
                return self.open_session(
                    schedule, win_start,
                    _schedule_window_mins(schedule),
                    phase=phase,
                    ends_at=win_end
                )
        return None

    def record(self, present_person_ids: List[str], frame=None, quality: int = 0) -> None:
        if self.session is not None:
            self.session.record(present_person_ids, frame=frame, quality=quality)

    def close_if_due(self, now: datetime, force: bool = False) -> Optional[dict]:
        """Hết cửa sổ (hoặc luồng video kết thúc) thì chốt biên bản."""
        if self.session is None:
            return None
        if not force and not self.session.is_due(now):
            return None

        session = self.session
        check = session.build_check(now)
        self.completed_keys.add(session.key)
        log = self._write_check(session, check, now)
        print(f"[Attendance] Chốt điểm danh {session.phase_label.lower()}: "
              f"có mặt {check['present']}/{log['required']}, vắng {check['absent']}")
        self.session = None
        return log

    def cancel_session(self) -> None:
        """Bỏ phiên đang mở mà không ghi biên bản (dừng luồng giữa chừng)."""
        if self.session is not None:
            print(f"[Attendance] Huỷ phiên điểm danh '{self.session.schedule.get('name', '')}' giữa chừng")
            self.session = None

    def status(self, now: datetime) -> dict:
        if self.session is None:
            return {"active": False}
        return {
            "active": True,
            "schedule_name": self.session.schedule.get("name", ""),
            "unit": self.session.schedule.get("unit", ""),
            "phase": self.session.phase,
            "phase_label": self.session.phase_label,
            "remaining_seconds": int(self.session.remaining_seconds(now)),
            "window_mins": self.session.window_mins,
            "present": self.session.present_count(),
            "required": self.session.required_count(),
            "roster_size": len(self.session.roster),
        }

    def schedules_with_state(self, now: datetime) -> List[dict]:
        """Thời khoá biểu kèm trạng thái vận hành và tình hình điểm danh hôm nay."""
        rows = []
        for schedule in self._load_schedules():
            row = dict(schedule)
            row.update(schedule_runtime_state(schedule, now))
            done = {}
            for phase, win_start, _win_end in schedule_windows(schedule, now):
                key = f"{schedule.get('id')}:{win_start.date().isoformat()}:{phase}"
                done[phase] = key in self.completed_keys
            row["checked_today"] = done
            rows.append(row)
        return rows
