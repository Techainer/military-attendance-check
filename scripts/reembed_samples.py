"""Tính lại embedding (ảnh đăng ký + mọi mẫu bổ sung) cho TOÀN BỘ quân nhân
bằng một model nhận diện khác, sau khi đã chọn model tốt hơn qua
``scripts/bench_face_models.py``.

Đổi model KHÔNG tự động làm lại embedding cũ (xem app/face_engine.py _gallery():
mẫu/người gắn ``embedding_model`` khác model đang chạy bị BỎ QUA, phải đăng ký
lại từng người) — script này làm việc đó một lượt cho cả CSDL, dùng lại ảnh ĐÃ
có sẵn trên đĩa (không cần chụp lại):

  - Mẫu bổ sung (``samples[].crop``, xem ``FaceEngine.add_samples``): ảnh
    112x112 đã canh thẳng — tính embedding trực tiếp.
  - Ảnh đăng ký chính (``avatar_path``): ảnh cắt có lề, KHÔNG phải 112x112 canh
    thẳng — phải detect + canh thẳng lại (SCRFD, độc lập model nhận diện) trước
    khi tính embedding.

Mẫu KHÔNG có ``crop`` (tạo trước khi tính năng lưu ảnh cắt có, hoặc nguồn
"camera" gán tay qua API — xem ghi chú trong app/face_engine.py add_samples)
không thể tính lại vì không còn ảnh gốc — bị XOÁ khỏi danh sách mẫu (KHÔNG xoá
``embedding`` chính của người, chỉ xoá mẫu bổ sung đó) và báo cáo lại số lượng
để cán bộ chủ động chụp/gán lại nếu cần.

AN TOÀN: mặc định chỉ chạy DRY-RUN (in báo cáo, KHÔNG ghi gì). Thêm ``--apply``
để ghi thật — khi đó tự sao lưu ``registered_faces.json`` ra
``registered_faces.json.bak-<timestamp>`` trước khi ghi đè.

Chạy:

    # xem trước sẽ đổi gì, không ghi
    uv run python scripts/reembed_samples.py --data data --model-path /path/to/model.onnx --preprocess arcface

    # ghi thật (đã xem dry-run và đồng ý)
    uv run python scripts/reembed_samples.py --data data --model-path /path/to/model.onnx --preprocess arcface --apply
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.face_engine import _preprocess_for_model  # cùng công thức tiền xử lý app dùng lúc chạy thật


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


class Embedder:
    def __init__(self, model_path: Path, preprocess: str, device: str):
        import onnxruntime as ort

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device == "cuda" else ["CPUExecutionProvider"]
        self.preprocess = preprocess
        self.session = ort.InferenceSession(str(model_path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def embed(self, img_bgr_112: np.ndarray) -> np.ndarray:
        blob = _preprocess_for_model(img_bgr_112, self.preprocess)
        out = self.session.run([self.output_name], {self.input_name: blob})[0]
        vec = out.reshape(-1).astype(np.float32)
        return vec / (np.linalg.norm(vec) or 1.0)


def make_aligner(device: str):
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    ap.add_argument("--model-path", required=True, help="ONNX model nhận diện mới")
    ap.add_argument("--preprocess", default="arcface", choices=["arcface", "adaface"])
    ap.add_argument("--model-name", default=None,
                     help="Tên ghi vào embedding_model (mặc định: alt:<tên file>:<preprocess>, "
                          "khớp quy ước FACE_REC_MODEL trong app/face_engine.py)")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    ap.add_argument("--apply", action="store_true", help="Ghi thật xuống registered_faces.json (có sao lưu)")
    args = ap.parse_args()

    data_dir = Path(args.data)
    db_path = data_dir / "registered_faces.json"
    if not db_path.exists():
        log(f"[reembed] Không thấy {db_path}")
        return
    persons = json.loads(db_path.read_text(encoding="utf-8"))

    model_path = Path(args.model_path)
    if not model_path.is_file():
        log(f"[reembed] Không thấy model {model_path}")
        return
    model_name = args.model_name or f"alt:{model_path.stem}:{args.preprocess}"
    log(f"[reembed] Model mới -> embedding_model='{model_name}' (preprocess={args.preprocess})")

    embedder = Embedder(model_path, args.preprocess, args.device)
    aligner = make_aligner(args.device)

    n_person_ok = n_person_fail = n_sample_ok = n_sample_dropped = 0
    for p in persons:
        avatar_rel = p.get("avatar_path")
        avatar_path = data_dir / "face_avatars" / Path(str(avatar_rel)).name if avatar_rel else None
        if avatar_path and avatar_path.is_file():
            img = cv2.imread(str(avatar_path))
            aligned = aligner(img) if img is not None else None
            if aligned is not None:
                p["embedding"] = [round(float(v), 6) for v in embedder.embed(aligned)]
                p["embedding_model"] = model_name
                n_person_ok += 1
            else:
                n_person_fail += 1
                log(f"[reembed] {p.get('name')} ({p.get('military_id')}): không detect được mặt trong "
                    f"{avatar_path.name} — GIỮ embedding cũ (sẽ bị _gallery() bỏ qua cho tới khi đăng ký lại ảnh).")
        else:
            n_person_fail += 1
            log(f"[reembed] {p.get('name')} ({p.get('military_id')}): thiếu ảnh đăng ký ({avatar_rel}) — bỏ qua.")

        kept_samples = []
        for smp in p.get("samples") or []:
            crop_rel = smp.get("crop")
            crop_path = data_dir / crop_rel if crop_rel else None
            if not crop_path or not crop_path.is_file():
                n_sample_dropped += 1
                continue
            img = cv2.imread(str(crop_path))
            if img is None:
                n_sample_dropped += 1
                continue
            smp["embedding"] = [round(float(v), 6) for v in embedder.embed(img)]
            smp["model"] = model_name
            kept_samples.append(smp)
            n_sample_ok += 1
        p["samples"] = kept_samples

    log(f"[reembed] Người: {n_person_ok} tính lại OK, {n_person_fail} lỗi/thiếu ảnh (giữ embedding cũ).")
    log(f"[reembed] Mẫu bổ sung: {n_sample_ok} tính lại OK, {n_sample_dropped} bị bỏ (không có ảnh cắt lưu "
        f"lại để tính lại — mẫu cũ trước khi có tính năng lưu crop, hoặc nguồn 'camera' qua API).")

    if not args.apply:
        log("[reembed] DRY-RUN — chưa ghi gì. Thêm --apply để ghi thật (sẽ tự sao lưu trước).")
        return

    backup_path = db_path.with_name(f"{db_path.name}.bak-{int(time.time())}")
    shutil.copy2(db_path, backup_path)
    log(f"[reembed] Đã sao lưu {db_path.name} -> {backup_path.name}")
    db_path.write_text(json.dumps(persons, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[reembed] Đã ghi {db_path}")


if __name__ == "__main__":
    main()
