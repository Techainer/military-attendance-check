"""Kiểm chứng ảnh bằng chứng điểm danh lấy từ ĐOẠN CUỐI cửa sổ, có đóng dấu
thời gian chụp (app/attendance.py AttendanceSession.record/final_evidence_frame,
AttendanceManager._save_evidence).

Bối cảnh: tester phản hồi ảnh bằng chứng chụp giữa cửa sổ (lúc sĩ số đông nhất)
dễ thiếu người tới muộn — giờ phải ưu tiên khung ở ATTENDANCE_EVIDENCE_TAIL_S
giây CUỐI cửa sổ, chỉ rơi về khung tốt nhất toàn cửa sổ khi không có khung nào
ở đoạn cuối (camera rớt đúng lúc).

Chạy: uv run python tests/test_attendance_evidence.py
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.attendance import ATTENDANCE_EVIDENCE_TAIL_S, AttendanceManager, AttendanceSession

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class _FakeFaceEngine:
    """Không cần mô hình thật để kiểm việc chọn/lưu ảnh bằng chứng."""

    def get_registered_faces(self, unit=None, **kwargs):
        return []


print(f"Mặc định: ATTENDANCE_EVIDENCE_TAIL_S={ATTENDANCE_EVIDENCE_TAIL_S}s")

sch = {"id": "manual", "name": "Đột xuất"}
roster = []
start = datetime(2026, 9, 10, 8, 0, 0)
WINDOW_MINS = 5

frame_early = np.full((10, 10, 3), 1, dtype=np.uint8)
frame_tail = np.full((10, 10, 3), 2, dtype=np.uint8)

# ---------------------------------------------------------------- AttendanceSession
print("\n[1] final_evidence_frame ưu tiên khung ở đoạn cuối cửa sổ")

sess = AttendanceSession(sch, roster, start, WINDOW_MINS)
tail_start = sess.ends_at - timedelta(seconds=ATTENDANCE_EVIDENCE_TAIL_S)

# Khung ĐẦU cửa sổ, chất lượng CAO (nhiều người định danh) nhưng KHÔNG ở đoạn cuối
sess.record([], frame=frame_early, quality=100, now=start + timedelta(seconds=5))
# Khung ở đoạn CUỐI cửa sổ, chất lượng THẤP hơn nhưng vẫn phải được ưu tiên chọn
sess.record([], frame=frame_tail, quality=10, now=tail_start + timedelta(seconds=1))

check("ảnh bằng chứng lấy từ ĐOẠN CUỐI cửa sổ dù chất lượng thấp hơn khung đầu",
      np.array_equal(sess.final_evidence_frame, frame_tail))
check("vẫn giữ khung TỐT NHẤT toàn cửa sổ làm dự phòng (evidence_frame)",
      np.array_equal(sess.evidence_frame, frame_early))

# Không có khung nào trong đoạn cuối (camera rớt đúng lúc) -> rơi về khung tốt
# nhất toàn cửa sổ, còn hơn không có bằng chứng
sess2 = AttendanceSession(sch, roster, start, WINDOW_MINS)
sess2.record([], frame=frame_early, quality=50, now=start + timedelta(seconds=5))
check("không có khung nào ở đoạn cuối -> rơi về khung tốt nhất toàn cửa sổ",
      np.array_equal(sess2.final_evidence_frame, frame_early))

# Không truyền 'now' (hành vi cũ, vd. nơi gọi chưa cập nhật) -> không phân biệt
# được đoạn cuối, coi mọi khung đều "ở cuối" như trước đây
sess3 = AttendanceSession(sch, roster, start, WINDOW_MINS)
sess3.record([], frame=frame_early, quality=5)
sess3.record([], frame=frame_tail, quality=50)
check("không truyền 'now' -> giữ hành vi cũ: ảnh bằng chứng = khung tốt nhất bất kể lúc nào",
      np.array_equal(sess3.final_evidence_frame, frame_tail))

# Phiên thủ công (manual) cũng áp dụng đúng cửa sổ của chính nó
print("\n[2] Phiên điểm danh thủ công (manual) cũng áp dụng đúng logic đoạn cuối")
sess_manual = AttendanceSession({"id": "manual_x", "name": "Đột xuất"}, roster, start,
                                WINDOW_MINS, phase="manual")
tail_start_m = sess_manual.ends_at - timedelta(seconds=ATTENDANCE_EVIDENCE_TAIL_S)
sess_manual.record([], frame=frame_early, quality=100, now=start + timedelta(seconds=2))
sess_manual.record([], frame=frame_tail, quality=1, now=tail_start_m + timedelta(seconds=1))
check("phiên thủ công: ảnh bằng chứng vẫn lấy từ đoạn cuối cửa sổ CỦA RIÊNG NÓ",
      np.array_equal(sess_manual.final_evidence_frame, frame_tail))

# ---------------------------------------------------------------- _save_evidence
print("\n[3] _save_evidence: đóng dấu thời gian chụp, trả về (đường dẫn, iso)")

tmp_dir = Path(tempfile.mkdtemp())
mgr = AttendanceManager(data_dir=str(tmp_dir), face_engine=_FakeFaceEngine(), camera_id=None)

frame_to_save = np.zeros((60, 200, 3), dtype=np.uint8)
frame_snapshot_before = frame_to_save.copy()
url, captured_iso = mgr._save_evidence(frame_to_save, "log_test", "start")

check("trả về đúng đường dẫn phục vụ cho web",
      url == "/data/attendance_evidence/log_test_start.jpg", str(url))
check("trả về giờ chụp dạng ISO parse được",
      bool(captured_iso) and "T" in captured_iso, str(captured_iso))

saved_path = tmp_dir / "attendance_evidence" / "log_test_start.jpg"
check("ảnh bằng chứng (đã đóng dấu) được ghi ra đĩa",
      saved_path.exists() and saved_path.stat().st_size > 0)
check("khung gốc KHÔNG bị sửa trực tiếp (có copy trước khi đóng dấu)",
      np.array_equal(frame_to_save, frame_snapshot_before))

check("frame=None -> không lưu gì, trả về (None, None)",
      mgr._save_evidence(None, "log_test", "end") == (None, None))

# -------------------------------------------- tích hợp: chốt biên bản thủ công
print("\n[4] close_if_due ghi 'evidence' + 'evidence_captured_at' vào biên bản")

tmp_dir2 = Path(tempfile.mkdtemp())
mgr2 = AttendanceManager(data_dir=str(tmp_dir2), face_engine=_FakeFaceEngine(), camera_id=None)
sched = {"id": "manual_evt", "name": "Đột xuất", "unit": "Tất cả đơn vị"}
session2 = mgr2.open_session(dict(sched), start, window_mins=WINDOW_MINS, phase="manual")
tail_start2 = session2.ends_at - timedelta(seconds=ATTENDANCE_EVIDENCE_TAIL_S)

mgr2.record([], frame=np.full((20, 20, 3), 9, dtype=np.uint8), quality=1,
           now=tail_start2 + timedelta(seconds=1))
closed = mgr2.close_if_due(session2.ends_at, force=True)

manual_check = (closed or {}).get("checks", {}).get("manual", {})
check("biên bản có đường dẫn ảnh bằng chứng", bool(manual_check.get("evidence")), str(manual_check))
check("biên bản có 'evidence_captured_at' dạng ISO",
      bool(manual_check.get("evidence_captured_at")), str(manual_check))

# ---------------------------------------------------------------- kết luận
print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
