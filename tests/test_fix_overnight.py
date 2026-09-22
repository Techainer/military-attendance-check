"""Kiểm script vá biên bản ca đêm ghép nhầm hai đêm.

Dữ liệu mẫu chép đúng dạng lỗi thấy trên máy triển khai thật: mốc cuối 05:00 sáng
ngày D (thuộc đêm D-1) nằm trong biên bản ngày D.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fix_overnight_logs import fix_logs

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


SCHEDULES = [
    {"id": "sch_dem", "name": "Ca đêm", "start_time": "21:00", "end_time": "05:00",
     "unit": "Tiểu đoàn 3", "shift": "Ca đêm", "required_count": 12},
    {"id": "sch_sang", "name": "Ca sáng", "start_time": "06:00", "end_time": "11:30",
     "unit": "Đại đội 1", "shift": "Ca sáng", "required_count": 45},
]


def moc(phase, time, absent=None):
    return {"phase": phase, "phase_label": {"start": "Đầu giờ", "end": "Cuối giờ"}[phase],
            "time": time, "present": 0, "absent": len(absent or []),
            "absent_personnel": absent or []}


def bien_ban(log_id, day, checks, **extra):
    y, m, d = day.split("-")
    return {"id": log_id, "schedule_id": extra.pop("sch", "sch_dem"), "date": f"{d}/{m}/{y}",
            "date_iso": day, "shift": "Ca đêm", "schedule_name": "Ca đêm",
            "unit": "Tiểu đoàn 3", "required": 12, "checks": checks, **extra}


def du_lieu_loi():
    # Mới nhất trước, đúng thứ tự trong attendance_logs.json
    return [
        bien_ban("L22", "2026-09-22", {"end": moc("end", "05:00", ["A"]), "start": moc("start", "21:05")},
                 session_id="sch_dem:2026-09-22", attendance=[{"dem": 22}]),
        bien_ban("L21", "2026-09-21", {"start": moc("start", "21:05")}),
        bien_ban("L20", "2026-09-20", {"end": moc("end", "05:00", ["B"])},
                 session_id="sch_dem:2026-09-19", attendance=[{"dem": 19}]),
        bien_ban("L19", "2026-09-19", {"end": moc("end", "05:00", ["C"]), "start": moc("start", "21:05")}),
        bien_ban("LS", "2026-09-21", {"end": moc("end", "11:30")}, sch="sch_sang"),
        bien_ban("LMOI", "2026-09-23", {"start": moc("start", "21:05"), "end": moc("end", "05:00")},
                 date_basis="occurrence"),
    ]


print("\n[1] Tách mốc cuối về đúng đêm")
fixed, report = fix_logs(du_lieu_loi(), SCHEDULES)
by_day = {(l["schedule_id"], l["date_iso"]): l for l in fixed}


def phases(day, sch="sch_dem"):
    log = by_day.get((sch, day))
    return None if log is None else set(log.get("checks", {}))


check("đêm 22: chỉ còn mốc đầu của chính nó", phases("2026-09-22") == {"start"}, str(phases("2026-09-22")))
check("đêm 21: nhận lại mốc cuối từ biên bản 22", phases("2026-09-21") == {"start", "end"},
      str(phases("2026-09-21")))
check("đêm 21: mốc cuối mang đúng danh sách vắng của nó",
      by_day[("sch_dem", "2026-09-21")]["checks"]["end"]["absent_personnel"] == ["A"])
check("ngày 20 chỉ chứa mốc cuối của đêm 19 -> rỗng thì bỏ", ("sch_dem", "2026-09-20") not in by_day)
check("đêm 19: mốc cuối là của đêm 19 (từ biên bản 20), không phải đêm 18",
      by_day[("sch_dem", "2026-09-19")]["checks"]["end"]["absent_personnel"] == ["B"])
check("đêm 18: được dựng mới để giữ mốc cuối của nó", phases("2026-09-18") == {"end"},
      str(phases("2026-09-18")))

print("\n[2] Bảng quân số đi theo đúng đêm")
check("bảng quân số của đêm 19 chuyển theo mốc cuối",
      by_day[("sch_dem", "2026-09-19")].get("attendance") == [{"dem": 19}]
      and by_day[("sch_dem", "2026-09-19")].get("session_id") == "sch_dem:2026-09-19")
check("bảng quân số của đêm 22 ở lại biên bản 22",
      by_day[("sch_dem", "2026-09-22")].get("attendance") == [{"dem": 22}])

print("\n[3] Không đụng vào phần không liên quan")
check("biên bản ca ngày giữ nguyên", phases("2026-09-21", "sch_sang") == {"end"}
      and "date_basis" not in by_day[("sch_sang", "2026-09-21")])
check("biên bản đã theo cách tính mới giữ nguyên", phases("2026-09-23") == {"start", "end"})
check("mọi biên bản ca đêm được đóng dấu cách tính mới",
      all(l.get("date_basis") == "occurrence" for l in fixed if l["schedule_id"] == "sch_dem"))
check("tổng hợp được tính lại theo mốc mới", by_day[("sch_dem", "2026-09-21")]["absent_personnel"] == ["A"])
check("báo cáo đếm đúng số mốc đã chuyển", report["moved"] == 3, str(report))

print("\n[4] Chạy lại lần hai không đổi gì")
again, report2 = fix_logs(fixed, SCHEDULES)
check("vá lần hai không chuyển thêm mốc nào", report2["moved"] == 0, str(report2))
check("vá lần hai giữ nguyên dữ liệu", again == fixed)

print("\n[5] Dòng lệnh: mặc định chỉ chạy thử, --apply mới ghi và có sao lưu")
d = Path(tempfile.mkdtemp())
(d / "schedules.json").write_text(json.dumps(SCHEDULES, ensure_ascii=False), encoding="utf-8")
goc = json.dumps(du_lieu_loi(), ensure_ascii=False)
(d / "attendance_logs.json").write_text(goc, encoding="utf-8")

subprocess.run([sys.executable, str(ROOT / "scripts/fix_overnight_logs.py"), "--data-dir", str(d)],
               check=True, capture_output=True)
check("chạy thử không ghi gì", (d / "attendance_logs.json").read_text(encoding="utf-8") == goc)

subprocess.run([sys.executable, str(ROOT / "scripts/fix_overnight_logs.py"), "--data-dir", str(d), "--apply"],
               check=True, capture_output=True)
backups = list(d.glob("attendance_logs.json.bak-*"))
check("--apply có tạo bản sao lưu", len(backups) == 1, str(backups))
check("bản sao lưu đúng là dữ liệu gốc",
      backups and backups[0].read_text(encoding="utf-8") == goc)
written = json.loads((d / "attendance_logs.json").read_text(encoding="utf-8"))
check("--apply ghi dữ liệu đã vá",
      {(l["schedule_id"], l["date_iso"]) for l in written} == set(by_day))

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
