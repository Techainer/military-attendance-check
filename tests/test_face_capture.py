"""Kiểm chứng thu khuôn mặt chủ động qua camera (POST /api/faces/capture):
``_pick_diverse_samples`` (chọn mẫu đa dạng) và ``capture_faces``/
``_feed_capture_sessions`` (gom mặt theo track trong N giây, không chặn vòng
AI) — app/video_processor.py.

Chạy: uv run python tests/test_face_capture.py
"""

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.video_processor import FACE_CAPTURE_MIN_WIDTH, VideoProcessor, _pick_diverse_samples

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


rng = np.random.default_rng(3)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


print("\n[1] _pick_diverse_samples: ít hơn k -> giữ hết, sắp theo chất lượng")
a, b = unit(rng.normal(size=512)), unit(rng.normal(size=512))
samples = [(0.5, a, b"x", 30), (0.9, b, b"y", 30)]
picked = _pick_diverse_samples(samples, 10)
check("giữ cả 2 mẫu, chất lượng cao trước", picked[0][0] == 0.9 and len(picked) == 2, str(picked))

print("\n[2] _pick_diverse_samples: nhiều hơn k -> giữ chất lượng cao nhất + xa nhau nhất")
base = unit(rng.normal(size=512))
near = unit(base + 0.01 * rng.normal(size=512))   # gần giống base
far = -base                                        # đối cực, khác xa nhất
samples2 = [(0.9, base, b"a", 30), (0.8, near, b"b", 30), (0.3, far, b"c", 30)]
picked2 = _pick_diverse_samples(samples2, 2)
check("giữ mẫu chất lượng cao nhất trước", picked2[0][1] is base)
check("mẫu thứ 2 là mẫu XA nhất với mẫu đã chọn (không phải mẫu gần giống)",
      picked2[1][1] is far, str([round(float(p[0]), 2) for p in picked2]))

print("\n[3] _pick_diverse_samples: rỗng -> trả []")
check("không có mẫu nào -> []", _pick_diverse_samples([], 10) == [])


print("\n[4] _feed_capture_sessions: bỏ mặt nhỏ/nghiêng, gom mặt đủ rõ theo track")


class _Proc:
    pass


proc = _Proc()
proc._capture_sessions = []
proc._face_jpeg = lambda frame, bbox: b"\xff\xd8fake"
for name in ("_feed_capture_sessions",):
    setattr(proc, name, getattr(VideoProcessor, name).__get__(proc))

frame = np.zeros((200, 200, 3), dtype=np.uint8)
session = {"per_track": {}}
proc._capture_sessions.append(session)

small_face = {"bbox": [10, 10, 10 + FACE_CAPTURE_MIN_WIDTH - 4, 40], "_emb": a,
              "width": FACE_CAPTURE_MIN_WIDTH - 4, "det_score": 0.8}
good_face = {"bbox": [10, 10, 10 + FACE_CAPTURE_MIN_WIDTH + 10, 40], "_emb": a,
             "width": FACE_CAPTURE_MIN_WIDTH + 10, "det_score": 0.8}
sideways_face = dict(good_face, frontal=False)

proc._feed_capture_sessions(5, small_face, frame, 0.0)
check("mặt nhỏ hơn FACE_CAPTURE_MIN_WIDTH -> không gom", session["per_track"] == {})

proc._feed_capture_sessions(5, sideways_face, frame, 0.0)
check("mặt nghiêng (frontal=False) -> không gom", session["per_track"] == {})

proc._feed_capture_sessions(None, good_face, frame, 0.0)
check("track_id None -> không gom (không biết gộp cho ai)", session["per_track"] == {})

proc._feed_capture_sessions(5, good_face, frame, 0.0)
proc._feed_capture_sessions(5, good_face, frame, 0.1)
proc._feed_capture_sessions(6, good_face, frame, 0.1)
check("mặt đủ rõ -> gom theo đúng track",
      len(session["per_track"].get(5, [])) == 2 and len(session["per_track"].get(6, [])) == 1,
      str(session["per_track"]))

proc._capture_sessions.remove(session)
check("không có phiên nào mở -> _feed_capture_sessions no-op, không lỗi",
      proc._feed_capture_sessions(5, good_face, frame, 0.2) is None)


print("\n[5] capture_faces: gom trong N giây (không chặn), chọn track nhiều mặt dùng được nhất")


async def run_capture_test():
    proc2 = _Proc()
    proc2._capture_sessions = []
    proc2._face_jpeg = lambda frame, bbox: b"\xff\xd8fake"
    for name in ("_feed_capture_sessions", "capture_faces"):
        setattr(proc2, name, getattr(VideoProcessor, name).__get__(proc2))

    async def feeder():
        # Track 5: nhiều mặt rõ liên tiếp (đứng yên trước camera).
        # Track 6: chỉ một mặt mờ thoáng qua.
        for i in range(6):
            proc2._feed_capture_sessions(5, dict(good_face, _emb=unit(rng.normal(size=512))),
                                         frame, float(i))
            await asyncio.sleep(0.01)
        proc2._feed_capture_sessions(6, dict(good_face, _emb=unit(rng.normal(size=512))), frame, 0.0)

    result, _ = await asyncio.gather(proc2.capture_faces(0.08), feeder())
    return proc2, result


proc2, result = asyncio.run(run_capture_test())
check("không block vòng AI: capture_faces chạy song song với feeder và trả kết quả",
      result is not None and result["track_id"] == 5, str(result))
check("số mẫu trả về không vượt FACE_CAPTURE_MAX_SAMPLES", len(result["samples"]) <= 10 if result else False)
check("phiên thu đã được dọn khỏi _capture_sessions sau khi xong", proc2._capture_sessions == [])


print("\n[6] capture_faces: không ai đứng trước camera trong window -> trả None")


async def run_empty():
    proc3 = _Proc()
    proc3._capture_sessions = []
    for name in ("capture_faces",):
        setattr(proc3, name, getattr(VideoProcessor, name).__get__(proc3))
    return await proc3.capture_faces(0.02)


check("không có mặt nào trong phiên -> None", asyncio.run(run_empty()) is None)


if failures:
    print(f"\n{len(failures)} kiểm thử LỖI: {failures}")
    sys.exit(1)
print("\nTất cả kiểm thử đạt.")
