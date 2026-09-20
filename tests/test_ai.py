"""Kiểm chứng ba tính năng AI mới: vi phạm giờ giấc, xâm nhập vùng cấm, kho sự kiện."""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.presence import SessionPresence
from app.safety import IntrusionDetector, ZoneStore

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- presence
print("\n[1] Suy trạng thái vi phạm giờ giấc từ dấu vết cả buổi")

day = datetime(2026, 8, 30)
start = day.replace(hour=7)
end = day.replace(hour=11, minute=30)
p = SessionPresence(start, end, late_tolerance_mins=5, early_leave_tolerance_mins=5)

roster = [{"id": f"p{i}", "name": f"Quân nhân {i}", "rank": "Binh nhất", "military_id": f"QN-{i}"}
          for i in range(1, 6)]


def seen(pid, first, last, times=5):
    """Ghi nhận pid xuất hiện đều đặn từ first tới last."""
    step = (last - first) / max(1, times - 1)
    for k in range(times):
        p.record([pid], first + step * k)


seen("p1", start + timedelta(minutes=2), end - timedelta(minutes=1))    # đúng giờ
seen("p2", start + timedelta(minutes=20), end - timedelta(minutes=1))   # đi chậm
seen("p3", start + timedelta(minutes=1), end - timedelta(minutes=90))   # về sớm
seen("p4", start + timedelta(minutes=30), end - timedelta(minutes=90))  # vừa chậm vừa sớm
p.record(["p5"], start + timedelta(minutes=10))                         # 1 lượt: dưới ngưỡng

items, summary = p.build_records(roster, end)
by_id = {i["person"]["id"]: i for i in items}

check("p1 đúng giờ -> present, không vi phạm",
      by_id["p1"]["status"] == "present" and by_id["p1"]["violations"] == [])
check("p2 -> late 20 phút",
      by_id["p2"]["violations"] == ["late"] and by_id["p2"]["late_minutes"] == 20,
      str(by_id["p2"]))
check("p3 -> early_leave",
      by_id["p3"]["violations"] == ["early_leave"] and by_id["p3"]["early_leave_minutes"] == 90,
      str(by_id["p3"]))
check("p4 vừa đi chậm vừa về sớm -> HAI vi phạm (không bị xếp nhầm thành vắng)",
      by_id["p4"]["violations"] == ["late", "early_leave"], str(by_id["p4"]))
check("p5 chỉ 1 lượt quét -> vẫn tính là vắng (chống nhận nhầm)",
      by_id["p5"]["status"] == "absent" and by_id["p5"]["violations"] == ["absent"])
check("tổng hợp đúng",
      summary == {"required": 5, "present": 1, "absent": 1, "late": 2, "early_leave": 2},
      str(summary))

# Giữa buổi thì chưa được kết luận "về sớm"
mid = start + timedelta(hours=1)
items_mid, summary_mid = p.build_records(roster, mid)
check("đang giữa buổi -> chưa kết luận về sớm cho ai", summary_mid["early_leave"] == 0,
      str(summary_mid))

check("sự kiện đi chậm bắn đúng một lần cho mỗi người",
      p.newly_late("p2") is True and p.newly_late("p2") is False)
check("người đúng giờ không bắn sự kiện đi chậm", p.newly_late("p1") is False)

check("dấu thời gian kèm offset múi giờ",
      by_id["p1"]["first_seen"].endswith("+07:00"), by_id["p1"]["first_seen"])

# Máy chủ khởi động lại giữa buổi: vào quan sát muộn thì không thể biết ai đã có
# mặt từ trước, kết luận đi chậm lúc này là oan.
late_boot = SessionPresence(start, end, 5, 5, watching_since=start + timedelta(minutes=40))
# Có mặt liên tục từ lúc hệ thống vào quan sát cho tới hết buổi
for k in range(5):
    late_boot.record(["p1"], start + timedelta(minutes=45) + (end - start) / 8 * k)
late_boot.record(["p1"], end - timedelta(minutes=1))

check("vào quan sát muộn -> không kết luận đi chậm", late_boot.can_judge_late is False)
check("vào muộn thì late_minutes = 0", late_boot.late_minutes("p1") == 0)
check("vào muộn thì không bắn sự kiện đi chậm", late_boot.newly_late("p1") is False)
lb_items, _ = late_boot.build_records([roster[0]], end)
check("vào muộn: người có mặt vẫn tính là present, không phải vi phạm",
      lb_items[0]["status"] == "present", str(lb_items[0]["violations"]))

on_time_boot = SessionPresence(start, end, 5, 5, watching_since=start)
check("quan sát từ đầu -> vẫn kết luận được đi chậm", on_time_boot.can_judge_late is True)

# --------------------------------------------------------- ca qua đêm
print("\n[1b] Ca qua đêm vẫn được theo dõi sau nửa đêm")

from app.attendance import _end_datetime, _start_datetime, schedule_runtime_state

night = {"id": "s_night", "start_time": "22:00", "end_time": "06:00", "check_window_mins": 5}
day_shift = {"id": "s_day", "start_time": "07:00", "end_time": "11:30", "check_window_mins": 5}


def state_at(sch, iso_time):
    return schedule_runtime_state(sch, datetime.fromisoformat(iso_time))["state"]


check("ca đêm lúc 22:02 -> đang điểm danh đầu giờ",
      state_at(night, "2026-08-30 22:02") == "check_start")
check("ca đêm lúc 01:00 hôm sau -> vẫn đang diễn ra (không phải 'chưa tới giờ')",
      state_at(night, "2026-08-31 01:00") == "running")
check("ca đêm lúc 05:57 -> đang điểm danh cuối giờ",
      state_at(night, "2026-08-31 05:57") == "check_end")
check("ca đêm neo về đúng ngày bắt đầu",
      _start_datetime(night, datetime.fromisoformat("2026-08-31 01:00"))
      == datetime.fromisoformat("2026-08-30 22:00"))
check("ca ban ngày không bị đổi hành vi",
      [state_at(day_shift, t) for t in ("2026-08-30 06:00", "2026-08-30 07:02",
                                        "2026-08-30 09:00", "2026-08-30 13:00")]
      == ["upcoming", "check_start", "running", "finished"])

# ---------------------------------------------------------------- safety
print("\n[2] Phát hiện xâm nhập vùng cấm và vượt vạch an toàn")

import json
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
(tmp / "zone_rules.json").write_text(json.dumps({
    "zones": [
        {"id": "z_att", "name": "Khu tập trung", "kind": "polygon", "rule": "attendance_area",
         "points": [{"x": 0.0, "y": 0.0}, {"x": 0.4, "y": 0.0},
                    {"x": 0.4, "y": 1.0}, {"x": 0.0, "y": 1.0}]},
        {"id": "z_cam", "name": "Khối chắn tuyến bắn", "kind": "polygon", "rule": "restricted_area",
         "points": [{"x": 0.6, "y": 0.0}, {"x": 1.0, "y": 0.0},
                    {"x": 1.0, "y": 1.0}, {"x": 0.6, "y": 1.0}]},
    ]
}, ensure_ascii=False), encoding="utf-8")

W, H = 1000, 1000
store = ZoneStore(str(tmp))
det = IntrusionDetector(store)
now = datetime(2026, 8, 30, 9, 0, 0)

check("polygon điểm danh vẫn lấy được (hành vi cũ không đổi)",
      store.attendance_polygon(W, H) is not None)

outside = [100, 100, 200, 400]      # trong vùng điểm danh, không phải vùng cấm
inside = [700, 100, 800, 400]       # trong vùng cấm

v = det.check([outside], [1], W, H, now)
check("người trong vùng điểm danh -> không có vi phạm", v == [])

v = det.check([outside, inside], [1, 2], W, H, now)
check("người trong vùng cấm -> sinh vi phạm", len(v) == 1 and v[0]["indices"] == [1], str(v))
check("vi phạm gắn đúng vùng", v[0]["zone"]["id"] == "z_cam")

v = det.check([inside], [2], W, H, now + timedelta(seconds=5))
check("còn trong thời gian chờ -> không bắn lại", v == [])

v = det.check([inside], [2], W, H, now + timedelta(seconds=25))
check("quá thời gian chờ -> bắn lại", len(v) == 1)
check("dwell_seconds cộng dồn từ lúc vào vùng", v[0]["dwell_seconds"] == 25, str(v[0]))

# vạch an toàn
(tmp / "zone_rules.json").write_text(json.dumps({
    "zones": [{"id": "z_line", "name": "Vạch an toàn", "kind": "tripwire", "rule": "crossing_line",
               "points": [{"x": 0.0, "y": 0.5}, {"x": 1.0, "y": 0.5}]}]
}, ensure_ascii=False), encoding="utf-8")
store2 = ZoneStore(str(tmp))
det2 = IntrusionDetector(store2)

above = [400, 100, 500, 300]   # chân ở y=298, phía trên vạch
below = [400, 600, 500, 800]   # chân ở y=798, phía dưới vạch

det2.check([above], [7], W, H, now)
v = det2.check([above], [7], W, H, now + timedelta(seconds=1))
check("đứng yên một phía -> không vi phạm", v == [])
v = det2.check([below], [7], W, H, now + timedelta(seconds=2))
check("cắt qua vạch -> sinh vi phạm", len(v) == 1 and v[0]["indices"] == [0], str(v))

# ---------------------------------------------------------------- events
print("\n[3] Kho sự kiện: ghi, lọc, xác nhận xử lý")

from app.events import EventStore, normalize_box

store_dir = Path(tempfile.mkdtemp())
es = EventStore(str(store_dir))

e1 = es.emit("INTRUSION", "Phát hiện 01 đối tượng.", severity="critical",
             zone_id="z_cam", detail={"zone_name": "Khối chắn", "object_count": 1})
e2 = es.emit("LATE", "A đi chậm 14 phút.", severity="warning", person_id="p2",
             detail={"late_minutes": 14})

check("sự kiện có camera_id theo hợp đồng", e1["camera_id"] == "cam_01")
check("sự kiện mới mặc định chưa xử lý", e1["acked"] is False)

items, total = es.list_events()
check("liệt kê được cả hai, mới nhất trước", total == 2 and items[0]["id"] == e2["id"])

items, total = es.list_events(types=["INTRUSION"])
check("lọc theo loại", total == 1 and items[0]["type"] == "INTRUSION")

check("đếm chờ xử lý", es.pending_count() == 2)
acked = es.ack(e1["id"], "Đại uý Nguyễn Văn Hùng", "Đã nhắc nhở tại chỗ")
check("xác nhận xử lý được lưu lại", acked["acked"] and acked["acked_by"].endswith("Hùng"))
check("đã xử lý thì không còn tính là chờ", es.pending_count() == 1)
check("lọc theo trạng thái xử lý", es.list_events(acked=False)[1] == 1)
check("ack sự kiện không tồn tại -> None", es.ack("evt_khong_co", "X") is None)

nb = normalize_box([100, 200, 300, 400], 1000, 800, 0.9, "A")
check("chuẩn hoá toạ độ box về [0,1]",
      nb["x1"] == 0.1 and nb["y1"] == 0.25 and nb["x2"] == 0.3 and nb["y2"] == 0.5, str(nb))

# ---------------------------------------------------------------- clip buffer
print("\n[4] Bộ đệm đoạn phát lại không phình vô hạn")

from app.video_processor import (MAX_STORED_CLIPS, keep_clip, latest_event_clips,
                                 push_clip_frame)

for i in range(MAX_STORED_CLIPS + 5):
    push_clip_frame("cam_test", "khung")
    keep_clip("cam_test", f"clip_{i:03d}")

check("số đoạn giữ lại bị chặn", len(latest_event_clips) == MAX_STORED_CLIPS,
      str(len(latest_event_clips)))
check("đoạn mới nhất được giữ", f"clip_{MAX_STORED_CLIPS + 4:03d}" in latest_event_clips)
check("đoạn cũ nhất bị dọn", "clip_000" not in latest_event_clips)

# Bộ đệm tách theo camera: đoạn của camera này không lẫn khung của camera kia
push_clip_frame("cam_x", "khung-cua-X")
keep_clip("cam_x", "clip_x")
check("đoạn clip chỉ chứa khung của đúng camera",
      latest_event_clips["clip_x"] == ["khung-cua-X"],
      str(latest_event_clips.get("clip_x")))

# ---------------------------------------------------------------- kết luận
print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
