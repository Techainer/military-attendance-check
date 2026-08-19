"""Roll-call session logic: điểm danh trong N phút đầu giờ học."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from app.storage import read_json_list, write_json_list


DEFAULT_WINDOW_MINS = 5
# Số lượt quét tối thiểu để coi là có mặt (chống nhận nhầm 1 frame)
MIN_SIGHTINGS = 3
# Cửa sổ còn lại ít hơn ngần này thì không mở phiên nữa (mở ra cũng không kịp đếm)
MIN_OPEN_REMAINING_SECONDS = 30


def _schedule_window_mins(schedule: dict) -> int:
    """Số phút đầu giờ dùng để điểm danh (ca cũ không khai thì lấy mặc định)."""
    raw = schedule.get("check_window_mins", schedule.get("tolerance_mins"))
    try:
        mins = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_MINS
    return mins if mins > 0 else DEFAULT_WINDOW_MINS


def _start_datetime(schedule: dict, now: datetime) -> Optional[datetime]:
    """Giờ bắt đầu của ca, quy về ngày hôm nay."""
    raw = str(schedule.get("start_time") or "").strip()
    parts = raw.split(":")
    if len(parts) < 2:
        return None
    try:
        return now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
    except ValueError:
        return None


class AttendanceSession:
    """Một phiên điểm danh: gom kết quả nhận diện trong suốt cửa sổ N phút."""

    def __init__(self, schedule: dict, roster: List[dict], started_at: datetime, window_mins: int):
        self.schedule = schedule
        self.roster = roster
        self.started_at = started_at
        self.window_mins = window_mins
        self.ends_at = started_at + timedelta(minutes=window_mins)
        # person_id -> số lượt quét nhận ra người này
        self.sightings: Dict[str, int] = {}
        self.scans = 0

    @property
    def key(self) -> str:
        return f"{self.schedule.get('id', 'manual')}:{self.started_at.date().isoformat()}"

    def record(self, person_ids: List[str]) -> None:
        """Ghi nhận một lượt quét."""
        self.scans += 1
        for pid in set(person_ids):
            self.sightings[pid] = self.sightings.get(pid, 0) + 1

    def remaining_seconds(self, now: datetime) -> float:
        return max(0.0, (self.ends_at - now).total_seconds())

    def is_due(self, now: datetime) -> bool:
        return now >= self.ends_at

    def present_ids(self) -> set:
        return {pid for pid, hits in self.sightings.items() if hits >= MIN_SIGHTINGS}

    def present_count(self) -> int:
        return len(self.present_ids())

    def build_log(self, closed_at: datetime) -> dict:
        """Chốt phiên thành bản ghi điểm danh."""
        present = self.present_ids()
        absent_people = [p for p in self.roster if p.get("id") not in present]

        required = self.schedule.get("required_count")
        try:
            required = int(required)
        except (TypeError, ValueError):
            required = len(self.roster)

        absent_names = [
            f"{p.get('rank', '')} {p.get('name', '')}".strip() or p.get("military_id", "?")
            for p in absent_people
        ]

        return {
            "id": f"log_{int(closed_at.timestamp() * 1000)}",
            "date": closed_at.strftime("%d/%m/%Y"),
            "time": closed_at.strftime("%H:%M"),
            "shift": self.schedule.get("shift", "Điểm danh"),
            "schedule_name": self.schedule.get("name", ""),
            "unit": self.schedule.get("unit", "Tất cả đơn vị"),
            "required": required,
            "present": len(present),
            "absent": len(absent_people),
            "absent_personnel": absent_names,
            "status": "Đủ quân số" if not absent_people else f"Thiếu {len(absent_people)} quân nhân",
            "status_type": "success" if not absent_people else "warning",
            "window_mins": self.window_mins,
            "started_at": self.started_at.isoformat(),
        }


class AttendanceManager:
    """Mở/đóng phiên điểm danh theo thời khoá biểu và ghi nhật ký."""

    def __init__(self, data_dir: str, face_engine):
        self.data_dir = Path(data_dir)
        self.schedules_file = self.data_dir / "schedules.json"
        self.logs_file = self.data_dir / "attendance_logs.json"
        self.face_engine = face_engine
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
            self._schedules_cache = read_json_list(self.schedules_file)
            self._schedules_mtime = mtime
        return self._schedules_cache

    def _load_completed_keys(self) -> set:
        """Ca nào đã điểm danh trong ngày rồi thì không chạy lại."""
        keys = set()
        for log in read_json_list(self.logs_file):
            sch_id = log.get("schedule_id")
            started = log.get("started_at", "")
            if sch_id and started:
                keys.add(f"{sch_id}:{started[:10]}")
        return keys

    def _write_log(self, log: dict) -> None:
        logs = read_json_list(self.logs_file)
        logs.insert(0, log)
        write_json_list(self.logs_file, logs)

    # ---------- roster ----------

    def _roster_for(self, unit: Optional[str]) -> List[dict]:
        """Danh sách quân nhân của đơn vị áp dụng cho ca.

        Đơn vị chưa đăng ký ai thì roster rỗng, không rơi về toàn bộ CSDL.
        """
        return self.face_engine.get_registered_faces(unit=unit)

    # ---------- session lifecycle ----------

    def open_session(self, schedule: dict, now: datetime, window_mins: Optional[int] = None) -> AttendanceSession:
        mins = window_mins if (window_mins and window_mins > 0) else _schedule_window_mins(schedule)
        roster = self._roster_for(schedule.get("unit"))
        self.session = AttendanceSession(schedule, roster, now, mins)
        print(f"[Attendance] Mở phiên điểm danh '{schedule.get('name', 'thủ công')}' "
              f"({mins} phút, {len(roster)} quân nhân trong danh sách)")
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
        return self.open_session(dict(schedule), now, window_mins)

    def maybe_open_scheduled(self, now: datetime) -> Optional[AttendanceSession]:
        """Tới N phút đầu giờ học thì tự mở phiên."""
        if self.session is not None:
            return None

        for schedule in self._load_schedules():
            start = _start_datetime(schedule, now)
            if start is None:
                continue
            mins = _schedule_window_mins(schedule)
            ends = start + timedelta(minutes=mins)
            if not (start <= now < ends):
                continue
            if (ends - now).total_seconds() < MIN_OPEN_REMAINING_SECONDS:
                # Hệ thống vào quá muộn: không chốt biên bản nửa vời, cũng không
                # đánh dấu ca này đã xong để còn chạy lại nếu mở sớm hơn
                continue
            key = f"{schedule.get('id')}:{now.date().isoformat()}"
            if key in self.completed_keys:
                continue
            # Mở phiên tính từ giờ bắt đầu ca, không phải từ lúc phát hiện
            session = self.open_session(schedule, start, mins)
            return session
        return None

    def record(self, present_person_ids: List[str]) -> None:
        if self.session is not None:
            self.session.record(present_person_ids)

    def close_if_due(self, now: datetime, force: bool = False) -> Optional[dict]:
        """Hết cửa sổ (hoặc luồng video kết thúc) thì chốt biên bản."""
        if self.session is None:
            return None
        if not force and not self.session.is_due(now):
            return None

        log = self.session.build_log(now)
        log["schedule_id"] = self.session.schedule.get("id", "manual")
        self.completed_keys.add(self.session.key)
        self._write_log(log)
        print(f"[Attendance] Chốt điểm danh: có mặt {log['present']}/{log['required']}, "
              f"vắng {log['absent']}")
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
            "remaining_seconds": int(self.session.remaining_seconds(now)),
            "window_mins": self.session.window_mins,
            "present": self.session.present_count(),
            "roster_size": len(self.session.roster),
        }
