"""Tạo dữ liệu giả lập để chạy thử luồng nghiệp vụ.

Các ca được đặt giờ **tương đối so với lúc chạy script**, không phải giờ cố
định — có vậy mới thấy đủ trạng thái ngay khi mở giao diện: ca đã kết thúc, ca
đang điểm danh đầu giờ, ca đang diễn ra, ca sắp tới.

    python scripts/seed_demo.py            # thêm vào dữ liệu đang có
    python scripts/seed_demo.py --reset    # xoá lịch cũ rồi tạo mới
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clock
from app.storage import read_json_list, write_json_list

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def hhmm(moment) -> str:
    return moment.strftime("%H:%M")


def build_schedules() -> list:
    """Bộ ca mẫu phủ đủ các trạng thái vận hành và cả hai loại huấn luyện."""
    now = clock.now()

    def at(minutes: int):
        return now + timedelta(minutes=minutes)

    # (lệch giờ bắt đầu, lệch giờ kết thúc, trạng thái mong đợi khi mở giao diện)
    plans = [
        {
            "offsets": (-210, -60),  # đã xong từ sáng
            "name": "Huấn luyện điều lệnh đội ngũ",
            "training_type": "dao_tao",
            "shift": "Ca sáng",
            "unit": "Đại đội 1",
            "class_name": "Đội học A1",
            "lesson_name": "Bài 1 — Đội hình đội ngũ cơ bản",
            "instructor": "Thượng uý Trần Văn Bình",
            "field": "Sân tập trung",
            "required_count": 45,
        },
        {
            "offsets": (-2, 148),  # vừa tới giờ: đang điểm danh đầu giờ
            "name": "Huấn luyện bắn súng tiểu liên AK",
            "training_type": "dao_tao",
            "shift": "Ca sáng",
            "unit": "Đại đội 1",
            "class_name": "Đội học A2",
            "lesson_name": "Bài 3 — Ngắm bắn mục tiêu cố định",
            "instructor": "Đại uý Phạm Minh Đức",
            "field": "Trường bắn số 1",
            "required_count": 40,
        },
        {
            "offsets": (-45, 95),  # đang diễn ra giữa buổi
            "name": "Huấn luyện chiến thuật tiểu đội tiến công",
            "training_type": "chien_dau",
            "shift": "Ca sáng",
            "unit": "Đại đội 2",
            "class_name": "Tiểu đội 3",
            "lesson_name": "Bài 5 — Vận động tiếp cận mục tiêu",
            "instructor": "Thiếu tá Nguyễn Hữu Thắng",
            "field": "Thao trường số 2",
            "required_count": 32,
        },
        {
            "offsets": (90, 240),  # sắp tới
            "name": "Huấn luyện bắn đạn thật ban ngày",
            "training_type": "chien_dau",
            "shift": "Ca chiều",
            "unit": "Tiểu đoàn 3",
            "class_name": "Đại đội 2 + 3",
            "lesson_name": "Bài 7 — Bắn mục tiêu ẩn hiện",
            "instructor": "Trung tá Vũ Đình Long",
            "field": "Trường bắn số 1",
            "required_count": 60,
        },
        {
            "offsets": (600, 1080),  # ca đêm, vắt qua nửa đêm
            "name": "Canh gác bảo vệ mục tiêu ban đêm",
            "training_type": "chien_dau",
            "shift": "Ca đêm",
            "unit": "Đại đội 3",
            "class_name": "Tổ gác số 1",
            "lesson_name": "Bài 2 — Quan sát và báo động ban đêm",
            "instructor": "Thượng uý Đỗ Quang Huy",
            "field": "Vọng gác số 4",
            "required_count": 12,
        },
    ]

    schedules = []
    for index, plan in enumerate(plans):
        start_off, end_off = plan.pop("offsets")
        schedules.append({
            "id": f"sch_demo_{index + 1}",
            "start_time": hhmm(at(start_off)),
            "end_time": hhmm(at(end_off)),
            "check_window_mins": 5,
            "late_tolerance_mins": 5,
            "early_leave_tolerance_mins": 5,
            "camera_id": "cam_01",
            "enabled": True,
            **plan,
        })
    return schedules


def build_logs(schedules: list) -> list:
    """Biên bản điểm danh cho các ca đã diễn ra, để thử màn nhật ký."""
    now = clock.now()
    today = now.date().isoformat()

    roster = [
        ("Binh nhất", "Nguyễn Văn An", "QN-10231"),
        ("Binh nhì", "Trần Quốc Bảo", "QN-10232"),
        ("Hạ sĩ", "Lê Minh Cường", "QN-10233"),
        ("Binh nhất", "Phạm Văn Dũng", "QN-10234"),
        ("Trung sĩ", "Hoàng Đức Em", "QN-10235"),
    ]

    def label(i):
        rank, name, _ = roster[i]
        return f"{rank} {name}"

    def person(i):
        rank, name, mid = roster[i]
        return {"id": f"per_{mid}", "name": name, "rank": rank,
                "military_id": mid, "unit": "Đại đội 1"}

    # Ca đầu tiên đã kết thúc: có đủ hai mốc và các loại vi phạm
    done = schedules[0]
    start_dt = now.replace(hour=int(done["start_time"][:2]),
                           minute=int(done["start_time"][3:]), second=0, microsecond=0)
    end_dt = now.replace(hour=int(done["end_time"][:2]),
                         minute=int(done["end_time"][3:]), second=0, microsecond=0)

    # An: đủ giờ · Bảo: đi chậm · Cường: về sớm · Dũng: chậm và sớm · Em: vắng
    marks = [
        (0, start_dt + timedelta(minutes=1), end_dt - timedelta(minutes=1), []),
        (1, start_dt + timedelta(minutes=18), end_dt - timedelta(minutes=2), ["late"]),
        (2, start_dt + timedelta(minutes=2), end_dt - timedelta(minutes=40), ["early_leave"]),
        (3, start_dt + timedelta(minutes=22), end_dt - timedelta(minutes=35), ["late", "early_leave"]),
        (4, None, None, ["absent"]),
    ]

    items, summary = [], {"required": len(roster), "present": 0, "absent": 0,
                          "late": 0, "early_leave": 0}
    for idx, first, last, violations in marks:
        status = "absent" if "absent" in violations else ("violation" if violations else "present")
        if status == "present":
            summary["present"] += 1
        elif status == "absent":
            summary["absent"] += 1
        if "late" in violations:
            summary["late"] += 1
        if "early_leave" in violations:
            summary["early_leave"] += 1

        items.append({
            "person": person(idx),
            "status": status,
            "violations": violations,
            "first_seen": clock.iso(first) if first else None,
            "last_seen": clock.iso(last) if last else None,
            "total_seconds": int((last - first).total_seconds()) if first and last else 0,
            "late_minutes": int((first - start_dt).total_seconds() // 60) if "late" in violations else None,
            "early_leave_minutes": int((end_dt - last).total_seconds() // 60) if "early_leave" in violations else None,
            "present_at_start": bool(first) and "late" not in violations,
            "present_at_end": bool(last) and "early_leave" not in violations,
        })

    absent_names = [label(i) for i, _f, _l, v in marks if "absent" in v]
    scheduled = int((end_dt - start_dt).total_seconds() // 60)
    actual = int((marks[0][2] - marks[0][1]).total_seconds() // 60)

    return [{
        "id": "log_demo_1",
        "schedule_id": done["id"],
        "session_id": f"{done['id']}:{today}",
        "date": start_dt.strftime("%d/%m/%Y"),
        "date_iso": today,
        "shift": done["shift"],
        "schedule_name": done["name"],
        "unit": done["unit"],
        "required": len(roster),
        "time": end_dt.strftime("%H:%M"),
        "present": 4,
        "absent": len(absent_names),
        "absent_personnel": absent_names,
        "status": f"Thiếu {len(absent_names)} quân nhân" if absent_names else "Đủ quân số",
        "status_type": "warning" if absent_names else "success",
        "commander": "Đại uý Nguyễn Văn Hùng",
        "actual_minutes": actual,
        "scheduled_minutes": scheduled,
        "progress_pct": round(min(100.0, actual / scheduled * 100), 1) if scheduled else 0.0,
        "attendance": items,
        "attendance_summary": summary,
        "checks": {
            "start": {
                "phase": "start", "phase_label": "Đầu giờ",
                "time": (start_dt + timedelta(minutes=5)).strftime("%H:%M"),
                "present": 3, "absent": 2,
                "present_personnel": [label(0), label(2), label(4)][:3],
                "absent_personnel": [label(1), label(3)],
                "evidence": None, "window_mins": 5, "scans": 42,
            },
            "end": {
                "phase": "end", "phase_label": "Cuối giờ",
                "time": end_dt.strftime("%H:%M"),
                "present": 2, "absent": 3,
                "present_personnel": [label(0), label(1)],
                "absent_personnel": [label(2), label(3), label(4)],
                "evidence": None, "window_mins": 5, "scans": 39,
            },
        },
    }]


def main() -> None:
    reset = "--reset" in sys.argv
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "schedules.json"

    existing = [] if reset else read_json_list(path)
    demo = build_schedules()

    # Chạy lại script thì ghi đè đúng ca mẫu, không nhân bản chúng lên
    demo_ids = {s["id"] for s in demo}
    kept = [s for s in existing if s.get("id") not in demo_ids]
    write_json_list(path, kept + demo)

    now = clock.now()
    print(f"Đã tạo {len(demo)} ca mẫu (giờ máy chủ: {now:%d/%m/%Y %H:%M}). "
          f"Giữ lại {len(kept)} ca sẵn có.\n")

    from app.attendance import schedule_runtime_state
    for sch in demo:
        state = schedule_runtime_state(sch, now)
        loai = "Đào tạo " if sch["training_type"] == "dao_tao" else "Chiến đấu"
        print(f"  {loai} · {sch['start_time']}–{sch['end_time']} · "
              f"{state['state_label']:<24} {sch['name']}")

    logs_path = DATA_DIR / "attendance_logs.json"
    existing_logs = [] if reset else read_json_list(logs_path)
    demo_logs = build_logs(demo)
    demo_log_ids = {l["id"] for l in demo_logs}
    kept_logs = [l for l in existing_logs if l.get("id") not in demo_log_ids]
    write_json_list(logs_path, demo_logs + kept_logs)

    sm = demo_logs[0]["attendance_summary"]
    print(f"\nĐã tạo 1 biên bản điểm danh mẫu cho ca đã kết thúc: "
          f"{sm['present']} đủ giờ, {sm['late']} đi chậm, "
          f"{sm['early_leave']} về sớm, {sm['absent']} không tham gia.")
    print("Ảnh bằng chứng để trống — ảnh thật do camera AI chụp khi chạy luồng.")

    print("\nMở giao diện: 'Lịch & Tiến độ' xem lịch, "
          "'Nhật ký điểm danh' bấm vào một dòng để xem chi tiết.")


if __name__ == "__main__":
    main()
