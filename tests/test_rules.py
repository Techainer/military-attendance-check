"""Kiểm chứng bộ chống báo động giả: hysteresis + dwell cho xâm nhập/vượt vạch,
và yêu cầu khoảng thời gian cho việc tính "có mặt".

Bối cảnh: bản demo đã log 157 cảnh báo INTRUSION trong một buổi vì luật cũ bắn
ngay khung đầu tiên, không có hysteresis, không lọc track bị che/chưa có id
thật. File này khoá lại đúng các hành vi đã sửa — không được để tái phát.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import tempfile

from app.attendance import AttendanceSession
from app.presence import MIN_PRESENCE_SPAN_S, MIN_SIGHTINGS, SessionPresence
from app.safety import INTRUSION_HYSTERESIS_PX, INTRUSION_MIN_DWELL_S, INTRUSION_MIN_FRAMES, \
    IntrusionDetector, ZoneStore

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


print(f"Mặc định: hysteresis={INTRUSION_HYSTERESIS_PX}px  "
      f"min_dwell={INTRUSION_MIN_DWELL_S}s  min_frames={INTRUSION_MIN_FRAMES}  "
      f"min_sightings={MIN_SIGHTINGS}  min_span={MIN_PRESENCE_SPAN_S}s")

# Khung hình chuẩn 1280x720: margin hysteresis đúng bằng INTRUSION_HYSTERESIS_PX (12px)
W, H = 1280, 720


def _zone_store(zones):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "zone_rules.json").write_text(
        json.dumps({"zones": zones}, ensure_ascii=False), encoding="utf-8")
    return ZoneStore(str(tmp))


def box_at(center_x, center_y=300):
    """Box 100x2 có chân (feet point) đúng tại (center_x, center_y)."""
    return [center_x - 50, center_y - 298, center_x + 50, center_y + 2]


def fired_only(violations):
    """Lọc lại chỉ những vi phạm THẬT SỰ vừa bắn sự kiện mới ở khung đó —
    ``check()`` giờ còn trả cả entry chỉ để báo 'vẫn đang ở trong vùng'
    (``indices`` rỗng, xem docstring ``IntrusionDetector.check``), không phải
    lúc nào có entry cũng là một sự kiện mới."""
    return [v for v in violations if v["indices"]]


# ---------------------------------------------------------------- vùng cấm
print("\n[1] Vùng cấm: hysteresis chặn rung nhiễu ở mép, dwell chặn bắn sớm")

restricted = {"id": "z_cam", "name": "Khối chắn", "kind": "polygon", "rule": "restricted_area",
              "points": [{"x": 0.5, "y": 0.0}, {"x": 1.0, "y": 0.0},
                         {"x": 1.0, "y": 1.0}, {"x": 0.5, "y": 1.0}]}
# Mép trái của vùng cấm ở x=640 (0.5 * 1280)

det = IntrusionDetector(_zone_store([restricted]))
now = datetime(2026, 9, 10, 8, 0, 0)

# Chân box rung quanh mép ±5px (trong biên độ hysteresis 12px): không bao giờ
# "lún sâu" đủ margin để tích dwell, dù rung rất nhiều khung trong thời gian dài
jitter_xs = [645, 635, 646, 634, 648, 636, 644, 637, 649, 633]
any_violation = False
for i, x in enumerate(jitter_xs):
    v = det.check([box_at(x)], [100], W, H, now + timedelta(seconds=0.3 * i))
    any_violation = any_violation or bool(v)
check("rung nhiễu quanh mép vùng cấm -> không bao giờ sinh vi phạm", not any_violation)

# Vào sâu hẳn trong vùng (cách mép > margin) nhưng dwell chưa đủ -> chưa vi phạm
det2 = IntrusionDetector(_zone_store([restricted]))
deep_inside = box_at(900)   # cách mép trái 260px, vượt xa margin 12px

v = det2.check([deep_inside], [101], W, H, now)
check("mới vào sâu 1 khung -> chưa đủ khung tối thiểu", v == [], str(v))

v = det2.check([deep_inside], [101], W, H, now + timedelta(seconds=0.3))
check("đủ khung nhưng dwell 0.3s < ngưỡng -> vẫn chưa vi phạm", v == [], str(v))

v = det2.check([deep_inside], [101], W, H, now + timedelta(seconds=0.6))
check("dwell 0.6s vẫn < ngưỡng 1s -> chưa vi phạm", v == [], str(v))

v = det2.check([deep_inside], [101], W, H, now + timedelta(seconds=1.2))
check("đủ khung VÀ dwell >= ngưỡng -> xác nhận vi phạm, đúng MỘT vi phạm",
      len(v) == 1 and v[0]["indices"] == [0], str(v))
first_dwell = v[0]["dwell_seconds"]

for i in range(1, 6):
    v = det2.check([deep_inside], [101], W, H, now + timedelta(seconds=1.2 + i))
    check(f"vẫn đứng trong vùng (khung +{i}) -> không bắn lặp lại",
          len(v) == 1 and v[0]["indices"] == [], str(v))
    check(f"vẫn đứng trong vùng (khung +{i}) -> nhưng vẫn báo 'inside' để vẽ liên tục",
          v[0]["inside"] == [0], str(v))

# ------------------------------------------------- track bị che / chưa có id
print("\n[2] Box bị che hoặc chưa có track id thật không được tính")

det3 = IntrusionDetector(_zone_store([restricted]))
now3 = datetime(2026, 9, 10, 9, 0, 0)

no_violation = False
for i in range(6):
    t = now3 + timedelta(seconds=0.3 * i)
    v = det3.check([deep_inside], [-1], W, H, t)   # id âm = chưa gán được track thật
    no_violation = no_violation or bool(v)
check("track id âm (chưa gán được) -> không bao giờ sinh vi phạm dù đứng lâu",
      not no_violation)

det4 = IntrusionDetector(_zone_store([restricted]))
now4 = datetime(2026, 9, 10, 9, 30, 0)
no_violation = False
for i in range(6):
    t = now4 + timedelta(seconds=0.3 * i)
    v = det4.check([deep_inside], [202], W, H, t, occluded=[True])   # box "giữ" qua khung bị che
    no_violation = no_violation or bool(v)
check("box occluded=True -> không bao giờ sinh vi phạm dù đứng lâu", not no_violation)

# Track vừa hết bị che (occluded=False) thì lại tính như bình thường, từ đầu
det5 = IntrusionDetector(_zone_store([restricted]))
now5 = datetime(2026, 9, 10, 10, 0, 0)
v = det5.check([deep_inside], [303], W, H, now5, occluded=[True])
check("đang bị che -> chưa tích dwell", v == [])
v = det5.check([deep_inside], [303], W, H, now5 + timedelta(seconds=0.3), occluded=[False])
v = det5.check([deep_inside], [303], W, H, now5 + timedelta(seconds=1.3), occluded=[False])
check("hết bị che rồi mới tính dwell từ lúc đó -> xác nhận được vi phạm",
      len(v) == 1, str(v))

# ---------------------------------------------------------------- vạch an toàn
print("\n[3] Vượt vạch an toàn: cần ổn định hai phía + lọc theo hướng")

LINE_POINTS = [{"x": 0.0, "y": 0.5}, {"x": 1.0, "y": 0.5}]   # y pixel = 360


def line_zone(direction=None, zone_id="z_line"):
    z = {"id": zone_id, "name": "Vạch an toàn", "kind": "tripwire", "rule": "crossing_line",
         "points": LINE_POINTS}
    if direction is not None:
        z["direction"] = direction
    return z


above = box_at(400, center_y=300)   # y feet = 300 -> above vạch (y < 360)
below = box_at(400, center_y=420)   # y feet = 420 -> below vạch (y > 360)

det_any = IntrusionDetector(_zone_store([line_zone()]))
now_l = datetime(2026, 9, 10, 11, 0, 0)
det_any.check([above], [7], W, H, now_l)
det_any.check([above], [7], W, H, now_l + timedelta(seconds=1))   # xác lập mốc, không vi phạm
v = det_any.check([below], [7], W, H, now_l + timedelta(seconds=2))
check("mới sang phía kia 1 khung -> chưa đủ dwell", v == [], str(v))
v = det_any.check([below], [7], W, H, now_l + timedelta(seconds=3))
check("direction=any -> cắt qua theo chiều nào cũng sinh vi phạm",
      len(v) == 1 and v[0]["indices"] == [0], str(v))

# direction="a_to_b": SIDE_A = dist>0 (below), SIDE_B = dist<0 (above) -> chỉ
# cho phép below -> above
det_ab = IntrusionDetector(_zone_store([line_zone(direction="a_to_b")]))
det_ab.check([below], [8], W, H, now_l)
det_ab.check([below], [8], W, H, now_l + timedelta(seconds=1))
v = det_ab.check([above], [8], W, H, now_l + timedelta(seconds=2))
check("direction=a_to_b, chưa đủ dwell phía mới -> chưa vi phạm", v == [], str(v))
v = det_ab.check([above], [8], W, H, now_l + timedelta(seconds=3))
check("direction=a_to_b, cắt từ A sang B (below->above) -> sinh vi phạm",
      len(v) == 1, str(v))

det_ab2 = IntrusionDetector(_zone_store([line_zone(direction="a_to_b")]))
det_ab2.check([above], [9], W, H, now_l)
det_ab2.check([above], [9], W, H, now_l + timedelta(seconds=1))
det_ab2.check([below], [9], W, H, now_l + timedelta(seconds=2))
v = det_ab2.check([below], [9], W, H, now_l + timedelta(seconds=3))
check("direction=a_to_b, cắt ngược B sang A (above->below) -> KHÔNG sinh vi phạm",
      v == [], str(v))

# ------------------------------------------------------------- presence span
print("\n[4] 'Có mặt' phải trải dài đủ thời gian, không chỉ đủ số lượt quét")

start = datetime(2026, 9, 10, 7, 0, 0)
end = start + timedelta(hours=4)
roster = [{"id": "p1", "name": "A", "rank": "Binh nhất", "military_id": "QN-1"}]
# Quét ngay từ phút đầu (= planned_start) để không dính vi phạm "đi chậm", và
# luôn chốt bảng NGAY sau lượt quét cuối (chưa tới hạn "về sớm") — mục tiêu ở
# đây chỉ là tách present/absent theo khoảng thời gian trải dài, không phải
# late/early-leave (đã có bài kiểm thử riêng ở test_ai.py).
t0 = start


def _query_after(last_offset_s):
    return t0 + timedelta(seconds=last_offset_s + 1)


# 3 lượt quét dính liền trong < MIN_PRESENCE_SPAN_S giây -> giống 1 lần nhận
# nhầm lặp lại do xử lý dồn khung, KHÔNG phải có mặt thật
p_burst = SessionPresence(start, end, late_tolerance_mins=5, early_leave_tolerance_mins=5)
for k in range(MIN_SIGHTINGS):
    p_burst.record(["p1"], t0 + timedelta(milliseconds=200 * k))
items, _ = p_burst.build_records(roster, _query_after(0.4))
check(f"{MIN_SIGHTINGS} lượt quét dồn trong < {MIN_PRESENCE_SPAN_S}s -> vẫn tính là vắng",
      items[0]["status"] == "absent", str(items[0]))

span_s = MIN_PRESENCE_SPAN_S + 1
last_offset = span_s * (MIN_SIGHTINGS - 1)

p_span = SessionPresence(start, end, late_tolerance_mins=5, early_leave_tolerance_mins=5)
for k in range(MIN_SIGHTINGS):
    p_span.record(["p1"], t0 + timedelta(seconds=span_s * k))
items, _ = p_span.build_records(roster, _query_after(last_offset))
check(f"{MIN_SIGHTINGS} lượt quét trải dài >= {MIN_PRESENCE_SPAN_S}s -> tính có mặt",
      items[0]["status"] == "present", str(items[0]))

# frame_seq: cùng một khung hình xử lý lại nhiều lần (ví dụ retry) không được
# tính là nhiều lượt quét độc lập khi caller cung cấp frame_seq
p_dup_frame = SessionPresence(start, end, late_tolerance_mins=5, early_leave_tolerance_mins=5)
for k in range(MIN_SIGHTINGS):
    p_dup_frame.record(["p1"], t0 + timedelta(seconds=span_s * k), frame_seq=42)
items, _ = p_dup_frame.build_records(roster, _query_after(last_offset))
check("cùng frame_seq lặp lại -> không đủ số khung KHÁC NHAU, vẫn tính là vắng",
      items[0]["status"] == "absent", str(items[0]))

p_distinct_frame = SessionPresence(start, end, late_tolerance_mins=5, early_leave_tolerance_mins=5)
for k in range(MIN_SIGHTINGS):
    p_distinct_frame.record(["p1"], t0 + timedelta(seconds=span_s * k), frame_seq=100 + k)
items, _ = p_distinct_frame.build_records(roster, _query_after(last_offset))
check("frame_seq khác nhau đủ số lượt -> tính có mặt",
      items[0]["status"] == "present", str(items[0]))

# -------------------------------------------------- AttendanceSession (phiên)
print("\n[5] AttendanceSession cũng đòi hỏi khoảng thời gian, không chỉ số lượt quét")

sch = {"id": "manual", "name": "Đột xuất"}
sess_burst = AttendanceSession(sch, roster, start, window_mins=5)
t1 = start
for k in range(MIN_SIGHTINGS):
    sess_burst.record(["p1"], now=t1 + timedelta(milliseconds=100 * k))
check("phiên: 3 lượt quét dồn trong chớp mắt -> KHÔNG tính có mặt",
      "p1" not in sess_burst.present_ids(), str(sess_burst.sightings))

sess_span = AttendanceSession(sch, roster, start, window_mins=5)
for k in range(MIN_SIGHTINGS):
    sess_span.record(["p1"], now=t1 + timedelta(seconds=3 * k))
check("phiên: 3 lượt quét trải dài đủ lâu -> tính có mặt",
      "p1" in sess_span.present_ids(), str(sess_span.sightings))

sess_no_now = AttendanceSession(sch, roster, start, window_mins=5)
for k in range(MIN_SIGHTINGS):
    sess_no_now.record(["p1"])   # không truyền now -> giữ đúng hành vi cũ (chỉ đếm)
check("phiên: không truyền 'now' -> giữ hành vi cũ, chỉ cần đủ số lượt",
      "p1" in sess_no_now.present_ids())

# ---------------------------------------------------------------- ngưỡng conf vùng cấm
print("\n[conf] Box mờ (đồ vật nhận nhầm ban đêm) không kích hoạt vùng cấm")
det_c = IntrusionDetector(_zone_store([restricted]))
fired = []
for i in range(20):  # 6 giây đứng sâu trong vùng, conf 0.3
    fired += fired_only(det_c.check([deep_inside], [300], W, H, now + timedelta(seconds=0.3 * i),
                                    confidences=[0.3]))
check("conf 0.3 đứng yên 6 s trong vùng -> không báo", fired == [], str(fired))
fired = []
for i in range(20):
    fired += fired_only(det_c.check([deep_inside], [301], W, H, now + timedelta(seconds=0.3 * i),
                                    confidences=[0.85]))
check("conf 0.85 -> báo đúng một lần", len(fired) == 1, str(fired))
det_c2 = IntrusionDetector(_zone_store([restricted]))
fired = []
for i, c in enumerate([0.8, 0.4, 0.8, 0.4, 0.8, 0.8, 0.8, 0.8]):
    fired += fired_only(det_c2.check([deep_inside], [302], W, H, now + timedelta(seconds=0.3 * i),
                                     confidences=[c]))
check("conf dao động quanh ngưỡng vẫn báo được (không reset dwell)", len(fired) == 1, str(fired))

# ----------------------------------- cooldown theo track + hàng chờ của vùng
print("\n[6] Nhiều người vào vùng cấm: mỗi track một sự kiện, không bị trộn/mất")

posA, posB = box_at(900), box_at(1100)   # hai vị trí khác nhau, cùng trong vùng cấm

# Hai track CÙNG xác nhận trong MỘT khung -> gộp thành đúng MỘT sự kiện, báo đủ cả hai
det_both = IntrusionDetector(_zone_store([restricted]))
same_frame_fires = []
for i in range(5):
    v = det_both.check([posA, posB], [501, 502], W, H, now + timedelta(seconds=0.3 * i))
    same_frame_fires += fired_only(v)
check("hai track cùng xác nhận một khung -> đúng MỘT sự kiện", len(same_frame_fires) == 1,
      str(same_frame_fires))
check("sự kiện đó báo đủ CẢ HAI box", sorted(same_frame_fires[0]["indices"]) == [0, 1],
      str(same_frame_fires))

# Người thứ hai vào sau người thứ nhất 5s (xa hơn khoảng đệm vùng 3s mặc định)
# -> vẫn sinh thêm một sự kiện RIÊNG, không bị gộp mất hay bỏ sót
det_5s = IntrusionDetector(_zone_store([restricted]))
fires_5s = []
for i in range(5):  # A xác nhận ở t=1.2s (giống kịch bản chuẩn ở mục [1])
    v = det_5s.check([posA], [601], W, H, now + timedelta(seconds=0.3 * i))
    fires_5s += fired_only(v)
check("người A vào trước -> 1 sự kiện", len(fires_5s) == 1, str(fires_5s))

T_B = 5.0  # B bắt đầu đứng vào 5s sau mốc xuất phát của A
for i in range(5):
    v = det_5s.check([posA, posB], [601, 602], W, H, now + timedelta(seconds=T_B + 0.3 * i))
    fires_5s += fired_only(v)
check("người B vào 5s sau A -> thêm một sự kiện RIÊNG (tổng 2, không bị chặn vì đã có A)",
      len(fires_5s) == 2, str(fires_5s))
check("sự kiện của B chỉ gồm box của B", fires_5s[-1]["indices"] == [1], str(fires_5s))

v = det_5s.check([posA, posB], [601, 602], W, H, now + timedelta(seconds=T_B + 1.8))
check("cả hai còn đứng trong vùng -> 'inside' liệt kê đủ CẢ HAI, liên tục",
      sorted(v[0]["inside"]) == [0, 1], str(v))

# Track thứ hai xác nhận TRONG lúc khoảng đệm của vùng (INTRUSION_COOLDOWN_SECONDS)
# còn đang khoá vì track thứ nhất vừa bắn -> phải CHỜ (không mất), bắn bù ngay
# khung đầu tiên sau khi khoảng đệm hết.
det_wait = IntrusionDetector(_zone_store([restricted]))
fires_wait = []
for i in range(5):  # A xác nhận ở t=1.2s -> khoá khoảng đệm vùng tới t=1.2+3=4.2s
    v = det_wait.check([posA], [701], W, H, now + timedelta(seconds=0.3 * i))
    fires_wait += fired_only(v)
check("A xác nhận ngay, khoá khoảng đệm của vùng", len(fires_wait) == 1, str(fires_wait))

for i in range(5):  # C bắt đầu vào ngay sau đó, xác nhận ở t=1.5+1.2=2.7s (< 4.2s)
    v = det_wait.check([posA, posB], [701, 702], W, H, now + timedelta(seconds=1.5 + 0.3 * i))
    fires_wait += fired_only(v)
check("C xác nhận trong lúc khoảng đệm vùng còn khoá -> CHƯA bắn, không bị mất",
      len(fires_wait) == 1, str(fires_wait))

v_catchup = det_wait.check([posA, posB], [701, 702], W, H, now + timedelta(seconds=4.5))
fires_wait += fired_only(v_catchup)
check("qua khoảng đệm của vùng -> C được bắn BÙ ở khung sau, dù khung đó không "
      "có gì mới xác nhận", len(fires_wait) == 2, str(fires_wait))

# ---------------------------------------------------------------- kết luận
print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
