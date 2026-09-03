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

    print("\nMở giao diện và vào 'Lịch & Tiến độ' để xem.")


if __name__ == "__main__":
    main()
