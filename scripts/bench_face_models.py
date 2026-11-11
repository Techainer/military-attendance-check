"""So sánh các model NHẬN DIỆN khuôn mặt (không phải detection) trên chính dữ
liệu đã thu được từ camera giám sát của hệ thống này — không phải benchmark
chuẩn công khai (LFW/IJB-C...) vì mặt camera giám sát (16-45px, mờ, nghiêng)
khác hẳn ảnh benchmark thường chụp gần, rõ.

Dữ liệu:
  - ``data/face_samples/<person_id>/*.jpg``: ảnh mặt camera đã canh thẳng
    (112x112, ArcFace norm_crop) — xem ``FaceEngine.add_samples`` (app/face_engine.py).
    Bề ngang mặt GỐC (trước khi canh thẳng) đọc lại từ ``registered_faces.json``
    (khoá ``samples[].width``, ghi kèm lúc lưu mẫu) để chia theo khoảng kích cỡ.
  - ``data/face_avatars/*.jpg``: ảnh đăng ký chính (1 ảnh/người, lưu dạng cắt có
    lề, KHÔNG phải 112x112 canh thẳng) — script tự detect + canh thẳng lại bằng
    SCRFD (chỉ dùng phần detection của InsightFace, không phụ thuộc model nhận
    diện nào đang so sánh).

Model so sánh:
  - buffalo_l   : ~/.insightface/models/buffalo_l/w600k_r50.onnx (đã có sẵn, app đang dùng)
  - antelopev2  : ~/.insightface/models/antelopev2/glintr100.onnx (tự tải qua
                  insightface nếu chưa có — cần Internet; bật bằng --antelopev2)
  - adaface_ir101: ONNX đường dẫn tự cung cấp qua --adaface PATH (không có thì bỏ
                  qua model này — xem scripts/export_adaface_onnx.md để tự xuất).
                  AdaFace mong input BGR (KHÔNG đổi RGB), chuẩn hoá (x/255-0.5)/0.5,
                  112x112 — xem ``app.face_engine._preprocess_for_model``.

Độ đo (in bảng markdown):
  - Rank-1 identification: 1-NN leave-one-out trên TOÀN bộ gallery (ảnh đăng ký +
    mọi mẫu camera) — với mỗi ảnh, bỏ chính nó ra, tìm ảnh gần nhất trong phần
    còn lại, đúng nếu cùng người.
  - Verification TAR @ FAR=1e-2 và 1e-3: trên mọi cặp (ảnh đăng ký + mẫu camera),
    ngưỡng lấy từ phân phối điểm các cặp KHÁC người (impostor), TAR đo trên các
    cặp CÙNG người (genuine) tại ngưỡng đó.
  - Chia theo khoảng bề ngang mặt GỐC: <24px, 24-48px, >48px (chỉ áp cho mẫu
    camera — ảnh đăng ký không có "bề ngang trên camera" nên luôn nằm trong
    gallery nhưng không tính riêng theo khoảng).
  - ms/khuôn mặt: thời gian suy luận TRUNG BÌNH một embedding (không tính decode
    ảnh/tiền xử lý numpy, chỉ phần chạy ONNX) trên ``--warmup`` + ``--latency-n``
    ảnh.

Chạy (máy có GPU, trong container — xem docs/review cho cách vào container):

    uv run python scripts/bench_face_models.py --data data --antelopev2 \
        --adaface /path/to/adaface_ir101_webface12m.onnx --device cuda

Không truyền ``--antelopev2``/``--adaface`` thì chỉ bench buffalo_l (model đang
chạy thật) để có đường mốc (baseline) so sánh.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.face_engine import _preprocess_for_model  # tái dùng đúng công thức tiền xử lý đã dùng lúc chạy thật

WIDTH_BUCKETS = [("<24px", 0, 24), ("24-48px", 24, 48), (">48px", 48, 10**9)]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- dữ liệu

def load_registered(data_dir: Path) -> List[dict]:
    db = data_dir / "registered_faces.json"
    if not db.exists():
        log(f"[bench] Không thấy {db}")
        return []
    return json.loads(db.read_text(encoding="utf-8"))


def gather_camera_samples(data_dir: Path, persons: List[dict]) -> List[Tuple[str, np.ndarray, Optional[int]]]:
    """[(person_id, ảnh BGR 112x112, bề_ngang_gốc|None)] từ data/face_samples/."""
    width_by_crop: Dict[str, int] = {}
    for p in persons:
        for smp in p.get("samples") or []:
            if smp.get("crop") and smp.get("width"):
                width_by_crop[smp["crop"]] = int(smp["width"])

    out = []
    samples_dir = data_dir / "face_samples"
    if not samples_dir.is_dir():
        log(f"[bench] Không thấy {samples_dir} — chưa có mẫu camera nào (xem app/face_engine.py add_samples)")
        return out
    for pid_dir in sorted(p for p in samples_dir.iterdir() if p.is_dir()):
        pid = pid_dir.name
        for f in sorted(pid_dir.glob("*.jpg")):
            img = cv2.imread(str(f))
            if img is None:
                continue
            rel = f"{samples_dir.name}/{pid}/{f.name}"
            out.append((pid, img, width_by_crop.get(rel)))
    return out


def gather_avatars(data_dir: Path, persons: List[dict], aligner) -> List[Tuple[str, np.ndarray]]:
    """[(person_id, ảnh BGR 112x112 ĐÃ canh thẳng)] từ ảnh đăng ký chính —
    avatar lưu dạng cắt có lề (xem FaceEngine.register_person), không phải
    112x112 canh thẳng, nên phải detect + norm_crop lại."""
    out = []
    avatars_dir = data_dir / "face_avatars"
    for p in persons:
        pid, ap = p.get("id"), p.get("avatar_path")
        if not pid or not ap:
            continue
        path = avatars_dir / Path(str(ap)).name
        if not path.is_file():
            continue
        img = cv2.imread(str(path))
        if img is None:
            continue
        aligned = aligner(img)
        if aligned is not None:
            out.append((pid, aligned))
        else:
            log(f"[bench] Không detect được mặt trong ảnh đăng ký {path.name} (bỏ qua)")
    return out


def make_aligner(device: str):
    """Chỉ dùng phần DETECTION của InsightFace (độc lập model nhận diện đang so
    sánh) để canh thẳng lại ảnh đăng ký."""
    from insightface.app import FaceAnalysis
    from insightface.utils import face_align

    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection"])
    app.prepare(ctx_id=0 if device == "cuda" else -1, det_size=(640, 640))

    def _align(img_bgr: np.ndarray) -> Optional[np.ndarray]:
        faces = app.get(img_bgr)
        if not faces:
            return None
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        if f.kps is None:
            return None
        return face_align.norm_crop(img_bgr, landmark=f.kps)

    return _align


# ---------------------------------------------------------------- model nhận diện

class OnnxRecognizer:
    """Một model nhận diện ONNX trần (không qua insightface.model_zoo) để chắc
    chắn tiền xử lý đúng theo ``preprocess`` khai báo, không phụ thuộc insightface
    tự đoán input_mean/input_std (xem ArcFaceONNX.__init__)."""

    def __init__(self, name: str, model_path: Path, preprocess: str, device: str):
        import onnxruntime as ort

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device == "cuda" else ["CPUExecutionProvider"]
        self.name = name
        self.preprocess = preprocess
        self.session = ort.InferenceSession(str(model_path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def embed(self, img_bgr_112: np.ndarray) -> np.ndarray:
        blob = _preprocess_for_model(img_bgr_112, self.preprocess)
        out = self.session.run([self.output_name], {self.input_name: blob})[0]
        vec = out.reshape(-1).astype(np.float32)
        return vec / (np.linalg.norm(vec) or 1.0)


def resolve_buffalo_l() -> Optional[Path]:
    path = Path("~/.insightface/models/buffalo_l/w600k_r50.onnx").expanduser()
    return path if path.is_file() else None


def resolve_antelopev2() -> Optional[Path]:
    path = Path("~/.insightface/models/antelopev2/glintr100.onnx").expanduser()
    if path.is_file():
        return path
    log("[bench] antelopev2 chưa có, đang tải qua insightface (cần Internet)...")
    try:
        from insightface.app import FaceAnalysis
        FaceAnalysis(name="antelopev2", allowed_modules=["recognition"])
    except Exception as e:
        log(f"[bench] Không tải được antelopev2: {e}")
    return path if path.is_file() else None


# ---------------------------------------------------------------- độ đo

def width_bucket(width: Optional[int]) -> Optional[str]:
    if width is None:
        return None
    for name, lo, hi in WIDTH_BUCKETS:
        if lo <= width < hi:
            return name
    return None


def rank1_identification(embs: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """1-NN leave-one-out trên toàn bộ ``embs`` (gallery = đăng ký + mọi mẫu
    camera, trừ chính ảnh đang xét) — trả vector bool đúng/sai theo từng hàng."""
    sims = embs @ embs.T
    np.fill_diagonal(sims, -2.0)  # bỏ chính nó ra khỏi gallery khi xét từng hàng
    nearest = labels[np.argmax(sims, axis=1)]
    return nearest == labels


def verification_tar_at_far(embs: np.ndarray, labels: np.ndarray, far_targets=(1e-2, 1e-3),
                             pair_mask: Optional[np.ndarray] = None) -> Dict[float, float]:
    """TAR tại các FAR mục tiêu, tính trên mọi cặp (tam giác trên, không lặp
    cặp/không tự so với chính mình). ``pair_mask`` (nếu có) lọc thêm cặp nào
    được tính (dùng để chia theo khoảng bề ngang)."""
    n = len(labels)
    iu, ju = np.triu_indices(n, k=1)
    if pair_mask is not None:
        keep = pair_mask[iu] & pair_mask[ju]
        iu, ju = iu[keep], ju[keep]
    if len(iu) == 0:
        return {f: float("nan") for f in far_targets}
    sims = np.sum(embs[iu] * embs[ju], axis=1)
    same = labels[iu] == labels[ju]
    genuine, impostor = sims[same], sims[~same]
    out = {}
    for far in far_targets:
        if len(impostor) == 0 or len(genuine) == 0:
            out[far] = float("nan")
            continue
        # Ngưỡng: chỉ FAR tỉ lệ cặp impostor được điểm >= ngưỡng
        thresh = np.quantile(impostor, 1.0 - far)
        out[far] = float(np.mean(genuine >= thresh))
    return out


def measure_latency(recognizer: OnnxRecognizer, imgs: List[np.ndarray], warmup: int, n: int) -> float:
    sample = imgs[: max(1, min(len(imgs), warmup + n))]
    if not sample:
        return float("nan")
    for img in sample[:warmup]:
        recognizer.embed(img)
    t0 = time.perf_counter()
    count = 0
    for img in sample[warmup:warmup + n] or sample[:1]:
        recognizer.embed(img)
        count += 1
    elapsed = time.perf_counter() - t0
    return (elapsed / max(1, count)) * 1000.0


# ---------------------------------------------------------------- main

def bench_one_model(recognizer: OnnxRecognizer, avatars, camera_samples, warmup: int, latency_n: int) -> dict:
    labels, embs, widths, is_avatar = [], [], [], []
    for pid, img in avatars:
        embs.append(recognizer.embed(img))
        labels.append(pid)
        widths.append(None)
        is_avatar.append(True)
    for pid, img, width in camera_samples:
        embs.append(recognizer.embed(img))
        labels.append(pid)
        widths.append(width)
        is_avatar.append(False)

    embs = np.asarray(embs, dtype=np.float32)
    labels = np.asarray(labels)
    widths = np.asarray(widths, dtype=object)
    is_avatar = np.asarray(is_avatar, dtype=bool)

    correct = rank1_identification(embs, labels)
    camera_mask = ~is_avatar
    result = {
        "n_avatars": int(is_avatar.sum()),
        "n_camera_samples": int(camera_mask.sum()),
        "n_persons": int(len(set(labels.tolist()))),
        "rank1_overall": float(correct[camera_mask].mean()) if camera_mask.any() else float("nan"),
        "tar_overall": verification_tar_at_far(embs, labels),
        "by_width": {},
        "ms_per_face": measure_latency(recognizer, [img for _, img, _ in camera_samples] or
                                        [img for _, img in avatars], warmup, latency_n),
    }
    for name, lo, hi in WIDTH_BUCKETS:
        bucket_mask = camera_mask & np.array([w is not None and lo <= w < hi for w in widths])
        if not bucket_mask.any():
            result["by_width"][name] = {"n": 0, "rank1": float("nan"), "tar": {}}
            continue
        full_mask = bucket_mask | is_avatar  # ảnh đăng ký luôn trong gallery của cặp verification
        result["by_width"][name] = {
            "n": int(bucket_mask.sum()),
            "rank1": float(correct[bucket_mask].mean()),
            "tar": verification_tar_at_far(embs, labels, pair_mask=full_mask),
        }
    return result


def print_markdown(results: Dict[str, dict]) -> None:
    print("\n## Tổng quan\n")
    print("| model | #người | #ảnh đăng ký | #mẫu camera | Rank-1 | TAR@1e-2 | TAR@1e-3 | ms/mặt |")
    print("|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        tar = r["tar_overall"]
        print(f"| {name} | {r['n_persons']} | {r['n_avatars']} | {r['n_camera_samples']} | "
              f"{r['rank1_overall']:.3f} | {tar.get(1e-2, float('nan')):.3f} | "
              f"{tar.get(1e-3, float('nan')):.3f} | {r['ms_per_face']:.2f} |")

    print("\n## Theo khoảng bề ngang mặt gốc (trên camera)\n")
    print("| model | khoảng | #mẫu | Rank-1 | TAR@1e-2 | TAR@1e-3 |")
    print("|---|---|---|---|---|---|")
    for name, r in results.items():
        for bucket, b in r["by_width"].items():
            tar = b["tar"]
            if b["n"] == 0:
                print(f"| {name} | {bucket} | 0 | - | - | - |")
                continue
            print(f"| {name} | {bucket} | {b['n']} | {b['rank1']:.3f} | "
                  f"{tar.get(1e-2, float('nan')):.3f} | {tar.get(1e-3, float('nan')):.3f} |")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="Thư mục data/ (chứa face_samples/, face_avatars/, registered_faces.json)")
    ap.add_argument("--antelopev2", action="store_true", help="Bench thêm antelopev2 (glintr100.onnx, tự tải nếu thiếu)")
    ap.add_argument("--adaface", default=None, help="Đường dẫn ONNX AdaFace IR-101 (không truyền thì bỏ qua model này)")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    ap.add_argument("--warmup", type=int, default=5, help="Số ảnh chạy khởi động trước khi đo ms/mặt")
    ap.add_argument("--latency-n", type=int, default=100, help="Số ảnh dùng để đo ms/mặt (trung bình)")
    args = ap.parse_args()

    data_dir = Path(args.data)
    persons = load_registered(data_dir)
    if not persons:
        log("[bench] Không có quân nhân nào trong registered_faces.json — không có gì để bench.")
        return

    camera_samples = gather_camera_samples(data_dir, persons)
    log(f"[bench] {len(camera_samples)} mẫu camera, {len(persons)} quân nhân trong CSDL.")
    if not camera_samples:
        log("[bench] Chưa có mẫu camera nào (data/face_samples/) — hãy gán tay vài 'mặt chưa xác định' "
            "hoặc để tự học chạy một thời gian trước khi bench.")

    aligner = make_aligner(args.device)
    avatars = gather_avatars(data_dir, persons, aligner)
    log(f"[bench] Canh thẳng được {len(avatars)}/{len(persons)} ảnh đăng ký.")

    model_specs = []
    buffalo = resolve_buffalo_l()
    if buffalo:
        model_specs.append(("buffalo_l", buffalo, "arcface"))
    else:
        log("[bench] Không thấy buffalo_l (w600k_r50.onnx) — bỏ qua.")
    if args.antelopev2:
        antelope = resolve_antelopev2()
        if antelope:
            model_specs.append(("antelopev2", antelope, "arcface"))
        else:
            log("[bench] Không có antelopev2 — bỏ qua.")
    if args.adaface:
        adaface_path = Path(args.adaface)
        if adaface_path.is_file():
            model_specs.append(("adaface_ir101", adaface_path, "adaface"))
        else:
            log(f"[bench] --adaface {args.adaface} không tồn tại — bỏ qua "
                f"(xem scripts/export_adaface_onnx.md).")

    if not model_specs:
        log("[bench] Không có model nào để bench.")
        return

    results = {}
    for name, path, preprocess in model_specs:
        log(f"[bench] Đang tính embedding với {name} ({path})...")
        recognizer = OnnxRecognizer(name, path, preprocess, args.device)
        results[name] = bench_one_model(recognizer, avatars, camera_samples, args.warmup, args.latency_n)

    print_markdown(results)


if __name__ == "__main__":
    main()
