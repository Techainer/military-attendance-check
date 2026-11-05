"""Kiểm chứng clip sự kiện 10s (5s TRƯỚC + 5s SAU thời điểm vi phạm).

Trước đây ``keep_clip`` chốt ngay lúc phát hiện vi phạm nên chỉ có khung TRƯỚC
(bộ đệm phát lại khi đó mới có đúng chừng đó) — giờ phải chờ đủ ``CLIP_POST_S``
giây rồi mới chốt, để clip có cả khung SAU thời điểm vi phạm
(``app/video_processor.py`` push_clip_frame/keep_clip/``_schedule_event_clip``).

Chạy: uv run python tests/test_clip.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.video_processor as vp

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


print(f"Mặc định: CLIP_PRE_S={vp.CLIP_PRE_S}s  CLIP_POST_S={vp.CLIP_POST_S}s")

# ---------------------------------------------------------------- keep_clip(t0)
print("\n[1] keep_clip(camera_id, clip_id, t0): chỉ lấy khung trong [t0-PRE, t0+POST]")

CAM = "cam_clip_centered"
t0 = 1_000.0  # mốc monotonic giả định của "thời điểm vi phạm"

# Khung quá xa TRƯỚC t0 (ngoài CLIP_PRE_S) -> không được lấy
vp.push_clip_frame(CAM, "qua_xua", ts=t0 - vp.CLIP_PRE_S - 1)
# Khung ngay đầu cửa sổ trước
vp.push_clip_frame(CAM, "dau_truoc", ts=t0 - vp.CLIP_PRE_S)
# Khung ngay lúc vi phạm
vp.push_clip_frame(CAM, "luc_vi_pham", ts=t0)
# Khung SAU thời điểm vi phạm (đây chính là phần clip CŨ không bao giờ có được)
vp.push_clip_frame(CAM, "sau_vi_pham", ts=t0 + 1)
# Khung cuối cửa sổ sau
vp.push_clip_frame(CAM, "cuoi_sau", ts=t0 + vp.CLIP_POST_S)
# Khung quá xa SAU t0 (ngoài CLIP_POST_S) -> không được lấy
vp.push_clip_frame(CAM, "qua_sau", ts=t0 + vp.CLIP_POST_S + 1)

clip_id = vp.keep_clip(CAM, "clip_centered_test", t0)
frames = vp.latest_event_clips[clip_id]

check("bỏ khung quá xa TRƯỚC cửa sổ", "qua_xua" not in frames, str(frames))
check("bỏ khung quá xa SAU cửa sổ", "qua_sau" not in frames, str(frames))
check("giữ khung đầu/cuối cửa sổ và khung ngay lúc vi phạm",
      {"dau_truoc", "luc_vi_pham", "cuoi_sau"} <= set(frames), str(frames))
check("clip CÓ khung với ts > t0 (phần SAU vi phạm — khác hành vi cũ)",
      "sau_vi_pham" in frames, str(frames))
check("đúng thứ tự thời gian (không xáo trộn)",
      frames == ["dau_truoc", "luc_vi_pham", "sau_vi_pham", "cuoi_sau"], str(frames))

# Không truyền t0 (hành vi cũ) -> lấy TOÀN BỘ đệm hiện có, không lọc theo thời gian
clip_id_legacy = vp.keep_clip(CAM, "clip_legacy_test")
check("không truyền t0 -> giữ hành vi cũ (lấy toàn bộ đệm)",
      len(vp.latest_event_clips[clip_id_legacy]) == 6, str(vp.latest_event_clips[clip_id_legacy]))

# ------------------------------------------- _schedule_event_clip chạy nền, có chờ
print("\n[2] _schedule_event_clip: chờ đủ CLIP_POST_S rồi mới chốt clip + lưu đĩa")

CAM2 = "cam_clip_schedule"
archived = []


class _FakeEvents:
    """Đứng thay EventStore thật: chỉ ghi lại đã được gọi lưu đoạn ghi cho sự
    kiện nào, không đụng ffmpeg/đĩa thật."""

    def save_clip(self, event_id, frames):
        archived.append((event_id, list(frames)))
        return f"/api/v1/events/{event_id}/clip"


async def _run():
    # Ép CLIP_POST_S nhỏ để test không phải chờ thật 5s
    old_post = vp.CLIP_POST_S
    vp.CLIP_POST_S = 0.05
    try:
        stub = object.__new__(vp.VideoProcessor)
        stub.camera_id, stub.events, stub._bg_tasks = CAM2, _FakeEvents(), set()

        t0_mono = time.monotonic()
        vp.push_clip_frame(CAM2, "truoc", ts=t0_mono - 1)
        vp.push_clip_frame(CAM2, "luc_bat_dau", ts=t0_mono)

        event = {"id": "evt_clip_test"}
        vp.VideoProcessor._schedule_event_clip(stub, "clip_sched_test", t0_mono, event)
        check("có đúng một task nền được giữ tham chiếu", len(stub._bg_tasks) == 1)

        # Khung "sau vi phạm" chỉ xuất hiện SAU khi đã gọi lịch — mô phỏng đúng
        # thực tế: nửa sau của clip chỉ có được SAU lúc phát hiện vi phạm.
        await asyncio.sleep(0.01)
        vp.push_clip_frame(CAM2, "sau_khi_len_lich", ts=time.monotonic())

        # Đợi task nền chạy xong (ngủ CLIP_POST_S rồi mới chốt clip + lưu đĩa)
        for _ in range(200):
            if not stub._bg_tasks:
                break
            await asyncio.sleep(0.01)

        return stub
    finally:
        vp.CLIP_POST_S = old_post


stub_done = asyncio.run(_run())

check("task nền tự dọn khỏi _bg_tasks khi xong", len(stub_done._bg_tasks) == 0)
check("clip đã được chốt vào latest_event_clips",
      "clip_sched_test" in vp.latest_event_clips, str(list(vp.latest_event_clips)))
clip_frames = vp.latest_event_clips.get("clip_sched_test", [])
check("clip chốt SAU khi lên lịch nên có cả khung đẩy vào SAU thời điểm vi phạm",
      "sau_khi_len_lich" in clip_frames, str(clip_frames))
check("sự kiện có clip_id đã được lưu đĩa (archive) đúng MỘT lần",
      len(archived) == 1 and archived[0][0] == "evt_clip_test", str(archived))

# Không có event (vd. cảnh báo thiếu quân số lúc chưa bật EventStore) -> vẫn
# chốt clip vào latest_event_clips, chỉ KHÔNG lưu đĩa (không có gì để archive)
archived.clear()


async def _run_no_event():
    old_post = vp.CLIP_POST_S
    vp.CLIP_POST_S = 0.02
    try:
        stub = object.__new__(vp.VideoProcessor)
        stub.camera_id, stub.events, stub._bg_tasks = "cam_clip_no_event", _FakeEvents(), set()
        vp.VideoProcessor._schedule_event_clip(stub, "clip_no_event", time.monotonic(), None)
        for _ in range(200):
            if not stub._bg_tasks:
                break
            await asyncio.sleep(0.01)
    finally:
        vp.CLIP_POST_S = old_post


asyncio.run(_run_no_event())
check("không có sự kiện (event=None) -> vẫn chốt clip, không lưu đĩa",
      "clip_no_event" in vp.latest_event_clips and len(archived) == 0,
      f"clip tồn tại: {'clip_no_event' in vp.latest_event_clips}, archived: {archived}")

# ---------------------------------------------------------------- kết luận
print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
