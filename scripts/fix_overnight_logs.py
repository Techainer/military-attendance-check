"""Vá biên bản ca đêm bị ghép nhầm hai đêm.

Trước bản sửa, biên bản lấy ngày theo lúc chốt mốc. Ca đêm 21:00 → 05:00 chốt
cuối giờ lúc 05:00 sáng hôm sau, nên mốc cuối của đêm D-1 bị ghi vào biên bản
ngày D. Mỗi dòng đối chiếu đầu/cuối buổi của ca đêm vì thế ghép đầu giờ đêm này
với cuối giờ đêm trước.

Script chuyển từng mốc cuối đó về đúng biên bản của đêm nó thuộc về, rồi đóng
dấu ``date_basis`` để máy chủ biết biên bản đã theo cách tính mới.

    python scripts/fix_overnight_logs.py                 # chạy thử: chỉ in, không ghi
    python scripts/fix_overnight_logs.py --apply         # ghi thật, tự sao lưu trước
    python scripts/fix_overnight_logs.py --data-dir /đường/dẫn/data --apply

**Dừng máy chủ trước khi --apply** (máy chủ đang chạy cũng ghi vào file này),
chạy xong thì khởi động lại để nó nạp lại các mốc đã chốt. Chạy lại nhiều lần
vẫn an toàn: biên bản đã vá sẽ được bỏ qua.
"""

import argparse
import copy
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.attendance import DATE_BASIS, PHASE_END, is_overnight, summarize_log
from app.storage import read_json_list, write_json_list

# Bảng quân số từng người được ghi lúc chốt mốc. Nếu nó được ghi lúc chốt mốc
# cuối (session_id chỉ về đêm trước) thì phải đi theo mốc cuối sang đêm trước.
PRESENCE_FIELDS = ("session_id", "attendance", "attendance_summary",
                   "actual_minutes", "scheduled_minutes", "progress_pct")


def _day_of(log: dict) -> str:
    return log.get("date_iso") or str(log.get("started_at", ""))[:10]


def _previous(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def fix_logs(logs: list, schedules: list) -> tuple:
    """Trả về ``(biên bản đã vá, báo cáo)``. Không sửa danh sách đầu vào."""
    logs = copy.deepcopy(logs)
    overnight = {s.get("id") for s in schedules if is_overnight(s)}
    report = {"moved": 0, "created": 0, "removed": 0, "conflicts": 0}

    # 1) Tách mọi mốc cuối nằm sai chỗ ra trước, dựa trên dữ liệu gốc, nên thứ tự
    #    xử lý các biên bản không ảnh hưởng kết quả.
    carried = []
    for log in logs:
        sch_id = log.get("schedule_id")
        if sch_id not in overnight or log.get("date_basis") == DATE_BASIS:
            continue
        day = _day_of(log)
        checks = log.get("checks")
        if not day or not isinstance(checks, dict) or PHASE_END not in checks:
            continue
        end = checks.pop(PHASE_END)
        presence = None
        if log.get("session_id") == f"{sch_id}:{_previous(day)}":
            presence = {k: log.pop(k) for k in PRESENCE_FIELDS if k in log}
        carried.append((sch_id, _previous(day), end, presence, log))

    # Biên bản của đêm, ưu tiên biên bản có mốc đầu/cuối hơn biên bản đột xuất
    index = {}
    for log in logs:
        key = (log.get("schedule_id"), _day_of(log))
        if key not in index or "manual" in (index[key].get("checks") or {}):
            index[key] = log

    # 2) Đặt mỗi mốc cuối vào biên bản đêm trước; chưa có thì dựng mới
    keep_legacy = set()
    for sch_id, day, end, presence, source in carried:
        target = index.get((sch_id, day))
        if target is None:
            y, m, d = day.split("-")
            target = {
                "id": f"{source['id']}_dem_{day}",
                "schedule_id": sch_id,
                "date": f"{d}/{m}/{y}",
                "date_iso": day,
                "shift": source.get("shift"),
                "schedule_name": source.get("schedule_name"),
                "unit": source.get("unit"),
                "required": source.get("required"),
                "checks": {},
            }
            # Tệp xếp mới nhất trước: đêm trước đứng ngay sau biên bản nguồn
            logs.insert(logs.index(source) + 1, target)
            index[(sch_id, day)] = target
            report["created"] += 1

        target_checks = target.setdefault("checks", {})
        if PHASE_END in target_checks:
            # Đêm trước đã có mốc cuối ghi theo cách mới: không đè, trả mốc cũ
            # về chỗ để không mất dữ liệu, và giữ nguyên biên bản nguồn như cũ.
            source["checks"][PHASE_END] = end
            if presence:
                source.update(presence)
            keep_legacy.add(id(source))
            report["conflicts"] += 1
            continue

        target_checks[PHASE_END] = end
        if presence and "session_id" not in target:
            target.update(presence)
        report["moved"] += 1

    # 3) Bỏ biên bản chỉ còn rỗng do vừa tách; tính lại tổng hợp; đóng dấu
    emptied = {id(src) for _, _, _, _, src in carried
               if not src.get("checks") and id(src) not in keep_legacy}
    result = []
    for log in logs:
        if id(log) in emptied:
            report["removed"] += 1
            continue
        if log.get("schedule_id") in overnight and id(log) not in keep_legacy:
            summarize_log(log)
            log["date_basis"] = DATE_BASIS
        result.append(log)
    return result, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Vá biên bản ca đêm ghép nhầm hai đêm.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"),
                        help="thư mục chứa attendance_logs.json và schedules.json")
    parser.add_argument("--apply", action="store_true",
                        help="ghi thật (mặc định chỉ chạy thử và in báo cáo)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    logs_file = data_dir / "attendance_logs.json"
    logs = read_json_list(logs_file)
    schedules = read_json_list(data_dir / "schedules.json")
    fixed, report = fix_logs(logs, schedules)

    print(f"Ca đêm nhận diện được: {sum(1 for s in schedules if is_overnight(s))}")
    print(f"Mốc cuối chuyển về đúng đêm: {report['moved']}")
    print(f"Biên bản dựng mới cho đêm thiếu: {report['created']}")
    print(f"Biên bản rỗng sau khi tách, đã bỏ: {report['removed']}")
    print(f"Xung đột (giữ nguyên, không đè): {report['conflicts']}")

    if fixed == logs:
        print("Không có gì cần vá.")
        return 0
    if not args.apply:
        print("\nChạy thử: CHƯA ghi gì. Dừng máy chủ rồi thêm --apply để ghi thật.")
        return 0

    backup = data_dir / f"attendance_logs.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(logs_file, backup)
    write_json_list(logs_file, fixed)
    print(f"\nĐã ghi. Bản sao lưu: {backup}")
    print("Khởi động lại máy chủ để nạp lại các mốc đã chốt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
