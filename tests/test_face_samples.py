"""Kiểm chứng nhận diện mặt nhỏ/mờ từ camera giám sát (app/face_engine.py +
app/video_processor.py): kho nhiều mẫu mỗi người, gộp embedding theo track,
danh sách mặt chưa xác định, tự học mẫu.

Chạy: uv run python tests/test_face_samples.py
"""

import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import video_processor as _vp
from app.face_engine import FaceEngine, UnknownFaceLog, FACE_MAX_AUTO_SAMPLES
from app.video_processor import VideoProcessor

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


rng = np.random.default_rng(7)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def noisy(base, sim):
    """Vector đơn vị có cos ~= ``sim`` với ``base`` (mặt CCTV nhỏ: rất nhiễu)."""
    noise = unit(rng.normal(size=base.shape))
    noise = unit(noise - (noise @ base) * base)
    return unit(sim * base + np.sqrt(max(0.0, 1 - sim ** 2)) * noise)


def bare_engine(persons, tmp):
    fe = object.__new__(FaceEngine)
    fe.model_name = "buffalo_l"
    fe.similarity_threshold = 0.40
    fe.data_dir = Path(tmp)
    fe.db_file = Path(tmp) / "registered_faces.json"
    fe._last_db_mtime = 0
    fe._matrix_cache = None
    fe._db_lock = threading.Lock()
    fe.registered_faces = persons
    fe.unknown_log = UnknownFaceLog()
    fe._save_database()
    return fe


A, B, C = unit(rng.normal(size=512)), unit(rng.normal(size=512)), unit(rng.normal(size=512))

with tempfile.TemporaryDirectory() as tmp:
    persons = [
        {"id": "a", "name": "A", "embedding": A.tolist(), "embedding_model": "buffalo_l"},
        {"id": "b", "name": "B", "embedding": B.tolist(), "embedding_model": "buffalo_l"},
    ]
    fe = bare_engine(persons, tmp)

    print("\n[1] Kho nhiều mẫu: điểm mỗi người = MAX trên các mẫu của người đó")
    cctv_a = noisy(A, 0.30)                       # mặt camera: giống ảnh đăng ký rất ít
    person, sim, _ = fe.match_embedding(cctv_a, 0.40, 0.05)
    check("chỉ có ảnh đăng ký -> mặt camera KHÔNG đạt ngưỡng", person is None, f"sim={sim:.2f}")

    sample_a = noisy(cctv_a, 0.85)
    added = fe.add_samples("a", [sample_a], "camera")
    check("thêm 1 mẫu từ camera", added == 1)
    probe = noisy(cctv_a, 0.80)                   # lần sau, cùng người, cùng camera
    person, sim, _ = fe.match_embedding(probe, 0.40, 0.05)
    check("có mẫu cùng miền camera -> nhận ra A", person is not None and person["id"] == "a",
          f"sim={sim:.2f}")

    added_dup = fe.add_samples("a", [sample_a + 1e-4], "camera")
    check("mẫu gần trùng mẫu đã có -> không thêm", added_dup == 0)

    print("\n[2] Hai mẫu của CÙNG một người không bị coi là 'mập mờ'")
    fe.add_samples("a", [noisy(A, 0.97)], "upload")
    person, sim, margin = fe.match_embedding(noisy(A, 0.9), 0.40, 0.05)
    check("nhiều mẫu của A gần nhau -> vẫn nhận A (khoảng cách tính theo NGƯỜI)",
          person is not None and person["id"] == "a", f"sim={sim:.2f} margin={margin:.2f}")

    print("\n[3] Giới hạn mẫu tự học + API không lộ embedding")
    for _ in range(FACE_MAX_AUTO_SAMPLES + 5):
        fe.add_samples("b", [unit(rng.normal(size=512))], "auto")
    rec_b = next(p for p in fe.registered_faces if p["id"] == "b")
    n_auto = sum(1 for x in rec_b["samples"] if x["source"] == "auto")
    check(f"mẫu auto bị chặn ở {FACE_MAX_AUTO_SAMPLES}", n_auto == FACE_MAX_AUTO_SAMPLES, str(n_auto))
    view = FaceEngine.public_view(rec_b)
    check("public_view bỏ embedding + samples, chỉ còn số lượng",
          "embedding" not in view and "samples" not in view and view["sample_count"] == n_auto, str(view.keys()))
    removed = fe.clear_samples("b", "auto")
    check("xoá riêng mẫu auto", removed == n_auto and not rec_b.get("samples"))

    print("\n[4] Gộp embedding theo track: khung lẻ không đủ điểm, gộp nhiều khung thì đủ")
    fe2 = bare_engine([
        {"id": "a", "name": "A", "embedding": A.tolist(), "embedding_model": "buffalo_l"},
        {"id": "b", "name": "B", "embedding": B.tolist(), "embedding_model": "buffalo_l"},
        {"id": "c", "name": "C", "embedding": C.tolist(), "embedding_model": "buffalo_l"},
    ], tmp)

    class _Proc:
        pass

    proc = _Proc()
    proc.face_engine = fe2
    proc.camera_id = "cam_test"
    proc.track_identity = {}
    proc._track_faces = {}
    proc._last_auto_enroll = {}
    for name in ("_faces_to_people", "_face_jpeg"):
        setattr(proc, name, getattr(VideoProcessor, name))
    for name in ("_pool_track_face", "_update_face_pools", "_remember_identity", "_auto_enroll_candidates"):
        setattr(proc, name, getattr(VideoProcessor, name).__get__(proc))

    frame = np.zeros((400, 400, 3), dtype=np.uint8)
    box = [(100, 100, 200, 380)]
    per_frame = [noisy(A, 0.30) for _ in range(12)]
    single_hits = sum(1 for e in per_frame if fe2.match_embedding(e, 0.40, 0.05)[0] is not None)
    check("từng khung lẻ (cos~0.30) không khung nào đạt ngưỡng 0.40", single_hits == 0, str(single_hits))
    for i, e in enumerate(per_frame):
        face = {"bbox": [130, 110, 160, 145], "_emb": e, "det_score": 0.7, "width": 30}
        proc._update_face_pools([face], box, [5], frame, float(i))
    bound = (proc.track_identity.get(5) or {}).get("person")
    check("gộp 12 khung -> track 5 được khoá vào A", bound is not None and bound["id"] == "a",
          str(proc.track_identity.get(5)))

    stranger = unit(rng.normal(size=512))
    for i in range(12):
        face = {"bbox": [130, 110, 160, 145], "_emb": noisy(stranger, 0.6), "det_score": 0.7, "width": 30}
        proc._update_face_pools([face], box, [9], frame, float(i))
    check("người lạ (không đăng ký) gộp bao nhiêu khung cũng KHÔNG bị gán nhầm",
          (proc.track_identity.get(9) or {}).get("person") is None, str(proc.track_identity.get(9)))
    unk = fe2.unknown_log.list()
    check("người lạ xuất hiện trong danh sách mặt chưa xác định (track 9), track đã nhận (5) thì không",
          [u["track_id"] for u in unk] == [9], str(unk))
    item = fe2.unknown_log.get(unk[0]["id"])
    check("mặt chưa xác định có ảnh JPEG để cán bộ xem",
          item["samples"][0][2][:2] == b"\xff\xd8")

    print("\n[5] Gắn mặt vào người: box chứa 2 mặt khác nhau -> bỏ qua; 1 mặt bị bắt 2 lần -> gộp")
    f1 = {"bbox": [110, 110, 140, 140], "_emb": A, "width": 30}
    f2 = {"bbox": [160, 110, 190, 140], "_emb": B, "width": 30}
    check("2 mặt trong cùng box -> không gắn",
          VideoProcessor._faces_to_people([f1, f2], box, [0]) == {})
    f1_dup = {"bbox": [108, 108, 142, 142], "_emb": A, "width": 34}
    got = VideoProcessor._faces_to_people([f1, f1_dup], box, [0])
    check("cùng mặt bị bắt 2 lần (quét cả khung + quét lại) -> gắn bản to hơn",
          0 in got and got[0]["width"] == 34, str(got))

    print("\n[6] Tự học mẫu: chỉ khi track đã khoá đúng người + khớp rất chắc + mặt đủ lớn")
    proc.track_identity = {1: {"person": {"id": "a"}}, 2: {"person": {"id": "b"}}}
    good = {"_emb": A, "similarity": 0.7, "margin": 0.3, "width": 40}
    asg = {
        0: {"person": {"id": "a"}, "face": good},                                    # đủ điều kiện
        1: {"person": {"id": "a"}, "face": dict(good)},                              # track khoá người KHÁC
        2: {"person": {"id": "c"}, "face": dict(good, width=10)},                    # mặt quá nhỏ
    }
    cands = proc._auto_enroll_candidates(asg, [1, 2, 3], 1000.0)
    check("chỉ 1 ứng viên hợp lệ (track 1 -> A)", [c[0] for c in cands] == ["a"], str(cands))
    again = proc._auto_enroll_candidates(asg, [1, 2, 3], 1000.0 + 5)
    check("trong khoảng giãn cách -> không tự học tiếp", again == [])
    later = proc._auto_enroll_candidates(asg, [1, 2, 3], 1000.0 + _vp.FACE_AUTO_ENROLL_INTERVAL_S + 1)
    check("hết giãn cách -> được tự học tiếp", [c[0] for c in later] == ["a"])

    print("\n[7] UnknownFaceLog: hết hạn + giới hạn kích thước")
    log = UnknownFaceLog(size=2, ttl_s=100)
    for t, base in enumerate((A, B, C)):
        log.upsert("cam", t, [(0.5, base, b"\xff\xd8x", 30)])
    check("giới hạn 2 mục, bỏ mục cũ nhất", sorted(u["track_id"] for u in log.list()) == [1, 2])

    print("\n[8] Cùng một người bị tracker cấp nhiều id -> gộp chung một thẻ")
    log2 = UnknownFaceLog(size=10, ttl_s=100)
    for t in range(4):
        log2.upsert("cam", 100 + t, [(0.5 + t / 10, noisy(A, 0.8), b"\xff\xd8x", 30)])
    log2.upsert("cam", 200, [(0.5, B, b"\xff\xd8y", 30)])
    rows = log2.list()
    check("4 track của A -> 1 thẻ (4 track), người khác -> thẻ riêng",
          sorted(r["tracks"] for r in rows) == [1, 4], str(rows))
    a_item = next(r for r in rows if r["tracks"] == 4)
    log2.drop_track("cam", 100)
    check("1 track của cụm được nhận ra -> cụm còn 3 track",
          log2.list() and next(r for r in log2.list() if r["id"] == a_item["id"])["tracks"] == 3)
    uid = log.list()[0]["id"]
    check("pop lấy ra và xoá", log.pop(uid) is not None and log.get(uid) is None)

print()
if failures:
    print(f"FAILED: {len(failures)}")
    sys.exit(1)
print("ALL PASSED")
