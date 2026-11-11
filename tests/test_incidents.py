"""Kiểm chứng biên bản vi phạm vùng cấm gộp theo (camera, vùng) và ưu tiên quét
lại mặt cho người trong vùng cấm (app/video_processor.py).

Bối cảnh: tester báo 41 sự kiện INTRUSION trong một buổi, 0 có tên — tracker
đổi ID (mất dấu rồi bắt lại) khiến mỗi lần xác nhận lại bắn một sự kiện MỚI,
và tên định danh ra SAU khi sự kiện đã chốt nên mãi ghi "Không xác định" dù
overlay sau đó đã hiện đúng tên. Từ giờ vi phạm của một vùng được gộp vào MỘT
biên bản còn MỞ trong lúc vùng còn người, chỉ đóng sau khi vùng trống liên tục
đủ ``INTRUSION_INCIDENT_GRACE_S`` giây; biên bản được CẬP NHẬT (không bắn mới)
khi tên định danh thêm được hoặc số người tăng.

Không cần chạy cả vòng AI (detector + face engine): ``_handle_restricted_violation``/
``_close_stale_incidents``/``_rescan_regions`` được gọi trực tiếp trên một
``VideoProcessor`` giả lập (xem tests/test_face_samples.py cho cùng kiểu dựng).

Chạy: uv run python tests/test_incidents.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import video_processor as _vp
from app.safety import RULE_RESTRICTED
from app.video_processor import VideoProcessor

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


print(f"Mặc định: INTRUSION_INCIDENT_GRACE_S={_vp.INTRUSION_INCIDENT_GRACE_S}s  "
      f"MAX_FACE_RESCANS={_vp.MAX_FACE_RESCANS}")


class FakeEventStore:
    """Thay cho app.events.EventStore: chỉ ghi lại lời gọi, không đụng đĩa.

    ``update_async`` mô phỏng đúng ngữ nghĩa gộp ``detail`` của bản thật (xem
    EventStore._update_locked) để bài kiểm cũng xác nhận đúng hợp đồng đó.
    """

    def __init__(self):
        self.events = {}
        self.emitted = []
        self.updated = []
        self._seq = 0

    async def emit_async(self, event_type, message, severity="info", frame=None,
                         boxes=None, detail=None, acked=False, **fields):
        self._seq += 1
        event = {
            "id": f"evt_test_{self._seq}", "type": event_type, "message": message,
            "severity": severity, "boxes": boxes or [], "detail": detail or {}, **fields,
        }
        self.events[event["id"]] = event
        self.emitted.append(dict(event))
        return event

    async def update_async(self, event_id, **fields):
        event = self.events.get(event_id)
        if event is None:
            return None
        for k, v in fields.items():
            if k == "detail" and isinstance(v, dict):
                event["detail"] = {**(event.get("detail") or {}), **v}
            else:
                event[k] = v
        self.updated.append(dict(event))
        return event


def _bare_proc():
    class _Proc:
        pass

    proc = _Proc()
    proc.events = FakeEventStore()
    proc.camera_id = "cam_test"
    proc.camera_name = "Camera Test"
    proc._open_incidents = {}
    proc._bg_tasks = set()
    proc._schedule_event_clip = lambda *a, **kw: None  # không cần dựng clip ở bài này
    for name in ("_handle_restricted_violation", "_close_stale_incidents"):
        setattr(proc, name, getattr(VideoProcessor, name).__get__(proc))
    return proc


def box(label="Không xác định"):
    return {"x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.4, "label": label}


ZONE = {"id": "zone_cam", "name": "Khu đạn dược", "rule": RULE_RESTRICTED}


async def run():
    proc = _bare_proc()
    updates = []

    async def on_update(payload):
        updates.append(payload)

    # ---------------------------------------------------------- [1] 3 lượt xác
    # nhận liên tiếp (tracker đổi ID 3 lần) ở cùng một vùng -> CHỈ MỘT sự kiện
    print("\n[1] 3 track ID liên tiếp cùng một vùng cấm -> 1 sự kiện duy nhất")
    t0 = 1000.0
    for i in range(3):
        await proc._handle_restricted_violation(
            ZONE, inside_now=[0], identified=[], boxes=[box()],
            display_frame=None, dwell_seconds=i + 1, now_mono=t0 + i, on_update=on_update)

    check("chỉ bắn đúng 1 sự kiện (không spam theo từng lần xác nhận)",
          len(proc.events.emitted) == 1, str(len(proc.events.emitted)))
    check("không có lần update nào (nội dung không đổi: vẫn 1 người, chưa định danh)",
          len(proc.events.updated) == 0, str(len(proc.events.updated)))
    check("biên bản của vùng vẫn đang MỞ", "zone_cam" in proc._open_incidents)
    check("message ban đầu ghi 'Không xác định'",
          "Không xác định" in proc.events.emitted[0]["message"], proc.events.emitted[0]["message"])
    check("đã đẩy đúng 1 lần on_update loại 'event' (sự kiện MỚI)",
          sum(1 for u in updates if u["type"] == "event") == 1, str(updates))

    # ---------------------------------------------------------- [2] định danh
    # ra được tên 3s sau -> CẬP NHẬT lại đúng sự kiện đó, không bắn mới
    print("\n[2] Định danh ra tên 3s sau -> cập nhật lại sự kiện cũ, có tên")
    person = {"person_id": "p_duy", "person_name": "Đại tá Trần Văn Duy"}
    await proc._handle_restricted_violation(
        ZONE, inside_now=[0], identified=[person], boxes=[box("Đại tá Trần Văn Duy")],
        display_frame=None, dwell_seconds=4, now_mono=t0 + 3, on_update=on_update)

    check("KHÔNG bắn sự kiện mới", len(proc.events.emitted) == 1)
    check("CÓ cập nhật sự kiện cũ (tên vừa định danh được)", len(proc.events.updated) == 1)
    check("message đã cập nhật ghi đúng tên",
          proc.events.updated[-1]["message"] == "1 người vào vùng cấm Khu đạn dược: Đại tá Trần Văn Duy",
          proc.events.updated[-1]["message"])
    check("person_id/person_name của sự kiện được gán theo người vừa định danh",
          proc.events.updated[-1]["person_id"] == "p_duy")
    check("detail.identified được cập nhật, vẫn giữ zone_name/dwell_seconds cũ (gộp detail)",
          proc.events.updated[-1]["detail"]["identified"] == [person]
          and proc.events.updated[-1]["detail"]["zone_name"] == "Khu đạn dược"
          and proc.events.updated[-1]["detail"]["dwell_seconds"] == 1, str(proc.events.updated[-1]["detail"]))
    # "event_update" (khác "event" của sự kiện MỚI) là cách kênh WebSocket cũ
    # (broadcast_update, qua on_update) phân biệt cập nhật — kênh SSE
    # (/api/v1/events/stream) phân biệt bằng cờ "updated": True ngay trên
    # CHÍNH payload sự kiện, xem EventStore thật ở phần [6] dưới.
    check("đẩy on_update loại 'event_update' (phân biệt với sự kiện mới)",
          any(u["type"] == "event_update" for u in updates), str(updates))

    # Thêm người thứ hai vào CÙNG vùng (vẫn đang mở) -> vẫn cập nhật, không bắn mới
    print("\n[2b] Thêm người thứ hai vào cùng vùng -> vẫn cập nhật biên bản cũ")
    await proc._handle_restricted_violation(
        ZONE, inside_now=[0, 1], identified=[person],
        boxes=[box("Đại tá Trần Văn Duy"), box()],
        display_frame=None, dwell_seconds=5, now_mono=t0 + 4, on_update=on_update)
    check("vẫn chỉ 1 sự kiện gốc", len(proc.events.emitted) == 1)
    check("có thêm 1 lần cập nhật nữa (số người tăng 1 -> 2)", len(proc.events.updated) == 2)
    check("message liệt kê cả tên đã biết lẫn 'Không xác định' cho người còn lại",
          proc.events.updated[-1]["message"]
          == "2 người vào vùng cấm Khu đạn dược: Đại tá Trần Văn Duy, Không xác định",
          proc.events.updated[-1]["message"])

    # ---------------------------------------------------------- [3] vùng trống
    # quá GRACE giây -> biên bản ĐÓNG, lượt vào kế tiếp là sự kiện MỚI
    print("\n[3] Vùng trống > INTRUSION_INCIDENT_GRACE_S -> lượt vào sau là sự kiện MỚI")
    last_seen = t0 + 4
    proc._close_stale_incidents(last_seen + 1)  # chưa đủ grace -> còn mở
    check("chưa đủ grace thì biên bản VẪN còn mở", "zone_cam" in proc._open_incidents)

    proc._close_stale_incidents(last_seen + _vp.INTRUSION_INCIDENT_GRACE_S + 1)
    check("đủ grace (vùng trống liên tục) -> biên bản đã ĐÓNG",
          "zone_cam" not in proc._open_incidents)

    await proc._handle_restricted_violation(
        ZONE, inside_now=[0], identified=[], boxes=[box()],
        display_frame=None, dwell_seconds=1,
        now_mono=last_seen + _vp.INTRUSION_INCIDENT_GRACE_S + 2, on_update=on_update)
    check("lượt vào mới sau khi đóng biên bản -> bắn sự kiện MỚI (tổng 2)",
          len(proc.events.emitted) == 2, str(len(proc.events.emitted)))

    # ---------------------------------------------------------- [4] không có ai
    # trong vùng ở khung này (inside_now rỗng) -> không làm gì, không lỗi
    print("\n[4] inside_now rỗng (vùng đang trống NGAY khung này) -> không bắn/cập nhật gì")
    before_emit, before_update = len(proc.events.emitted), len(proc.events.updated)
    await proc._handle_restricted_violation(
        ZONE, inside_now=[], identified=[], boxes=[],
        display_frame=None, dwell_seconds=0, now_mono=9999.0, on_update=on_update)
    check("không bắn thêm sự kiện", len(proc.events.emitted) == before_emit)
    check("không cập nhật gì", len(proc.events.updated) == before_update)


asyncio.run(run())


# ================================================================ [6] hình dạng payload SSE của EventStore.update (dùng EventStore THẬT)

print("\n[6] EventStore.update: publish payload SSE mang cờ 'updated': True")
import tempfile

from app.events import EventStore, TYPE_INTRUSION

estore = EventStore(data_dir=tempfile.mkdtemp())
queue = estore.subscribe()
created = estore.emit(TYPE_INTRUSION, "1 người vào vùng cấm X: Không xác định", severity="critical")
first_published = queue.get_nowait()
check("emit() KHÔNG có cờ 'updated' trên payload SSE (sự kiện mới)",
      "updated" not in first_published, str(first_published))

updated_event = estore.update(created["id"], message="1 người vào vùng cấm X: Đại tá Trần Văn Duy")
second_published = queue.get_nowait()
check("update() CÓ cờ 'updated': True trên payload SSE (để FE phân biệt với sự kiện mới)",
      second_published.get("updated") is True, str(second_published))
check("update() giữ đúng id sự kiện cũ (không tạo id mới)",
      second_published["id"] == created["id"])
check("update() áp dụng đúng field mới (message)",
      second_published["message"] == "1 người vào vùng cấm X: Đại tá Trần Văn Duy")
check("giá trị update() trả về (dùng để forward qua on_update) KHÔNG tự mang cờ 'updated'"
      " — cờ đó chỉ gắn vào payload SSE lúc publish, xem EventStore.update",
      "updated" not in updated_event)
check("update() với event_id không tồn tại -> trả None, không lỗi",
      estore.update("evt_khong_ton_tai", message="x") is None)


# ================================================================ [5] ưu tiên quét lại mặt

print("\n[5] _rescan_regions: track trong vùng cấm được xếp ưu tiên + ngân sách +2 khi có biên bản mở")


def _rescan_proc():
    class _Proc:
        pass

    proc = _Proc()
    proc.track_identity = {}
    proc._last_rescan = {}
    setattr(proc, "_rescan_regions", getattr(VideoProcessor, "_rescan_regions").__get__(proc))
    return proc


proc = _rescan_proc()
frame_shape = (480, 640, 3)
N = _vp.MAX_FACE_RESCANS + 3
# N track, mỗi track một box nhỏ (đủ để qua FACE_RESCAN_MAX_WIDTH), đều chưa
# quét lần nào (waited bằng nhau) -> không có tiêu chí nào khác để phân biệt
# NGOÀI việc có đang vi phạm vùng cấm hay không.
person_boxes = [(10 + i * 2, 10, 40 + i * 2, 90) for i in range(N)]
track_ids = list(range(N))
in_zone_indices = set(range(N))
# 2 track CUỐI (thường bị xếp sau theo thứ tự thường) đang vi phạm vùng cấm
restricted = {N - 2, N - 1}

chosen_no_incident = proc._rescan_regions(
    person_boxes, in_zone_indices, set(), frame_shape, track_ids, now_mono=0.0,
    restricted_indices=restricted, extra_budget=0)
check(f"không có biên bản mở -> vẫn đúng ngân sách mặc định ({_vp.MAX_FACE_RESCANS}) vùng được chọn",
      len(chosen_no_incident) == _vp.MAX_FACE_RESCANS, str(len(chosen_no_incident)))

# Reset cooldown để lần gọi dưới không bị FACE_RESCAN_COOLDOWN_S chặn
proc._last_rescan = {}
chosen_with_incident = proc._rescan_regions(
    person_boxes, in_zone_indices, set(), frame_shape, track_ids, now_mono=100.0,
    restricted_indices=restricted, extra_budget=2)
check("có biên bản mở (extra_budget=2) -> chọn thêm đúng 2 vùng",
      len(chosen_with_incident) == _vp.MAX_FACE_RESCANS + 2, str(len(chosen_with_incident)))

# Vùng của 2 track vi phạm (box cuối cùng, x1 lớn nhất) PHẢI có mặt trong danh
# sách đã chọn dù ngân sách nhỏ hơn tổng số track đang chờ quét.
restricted_regions = {person_boxes[i] for i in restricted}
# region trả về đã có pad nên so bằng cách khác: kiểm track id tương ứng còn
# "chờ" (_last_rescan vừa được set) hay không thay cho so khớp toạ độ.
proc._last_rescan = {}
proc._rescan_regions(person_boxes, in_zone_indices, set(), frame_shape, track_ids, now_mono=0.0,
                     restricted_indices=restricted, extra_budget=0)
check("track đang vi phạm vùng cấm LUÔN được chọn quét (đứng đầu hàng ưu tiên)",
      all(tid in proc._last_rescan for tid in restricted), str(proc._last_rescan))


if failures:
    print(f"\n{len(failures)} kiểm thử LỖI: {failures}")
    sys.exit(1)
else:
    print("\nTất cả kiểm thử đạt.")
