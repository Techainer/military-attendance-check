"""Face registration, embedding extraction, and recognition engine using InsightFace."""

import os
import json
import base64
import threading
import time
import uuid
from collections import OrderedDict
import numpy as np
import cv2
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from app import hw

# Mẫu khuôn mặt BỔ SUNG cho mỗi quân nhân (ngoài ảnh đăng ký chính). Ảnh đăng ký
# là ảnh chụp gần, nét; còn camera giám sát đặt cao cho mặt chỉ vài chục pixel,
# mờ, nghiêng — so hai thứ khác hẳn nhau thì độ tương đồng rất thấp dù đúng
# người. Thêm mẫu lấy từ CHÍNH camera (gán tay từ "mặt chưa xác định" hoặc tự
# học khi đã nhận ra rất chắc chắn) kéo hai phía về cùng một "miền ảnh".
FACE_MAX_SAMPLES = int(os.environ.get("FACE_MAX_SAMPLES", "30"))
FACE_MAX_AUTO_SAMPLES = int(os.environ.get("FACE_MAX_AUTO_SAMPLES", "10"))
# Mẫu mới gần như trùng một mẫu đã có (cos >= ngưỡng) thì không thêm — không
# thêm thông tin, chỉ làm phình ma trận.
FACE_SAMPLE_DEDUP_SIM = 0.95
UNKNOWN_FACE_LOG_SIZE = int(os.environ.get("UNKNOWN_FACE_LOG_SIZE", "60"))
# Hai track (cùng camera) có mặt trung bình giống nhau từ ngưỡng này -> coi là
# cùng một người, gộp chung một thẻ "mặt chưa xác định".
UNKNOWN_FACE_MERGE_SIM = float(os.environ.get("UNKNOWN_FACE_MERGE_SIM", "0.55"))
UNKNOWN_FACE_TTL_S = float(os.environ.get("UNKNOWN_FACE_TTL_S", str(6 * 3600)))


class UnknownFaceLog:
    """Các khuôn mặt CHƯA xác định gần đây (theo track), để cán bộ gán tay vào
    đúng quân nhân -> trở thành mẫu bổ sung lấy từ chính camera.

    Chỉ giữ trong RAM (mất khi khởi động lại — chủ đích, không lưu ảnh người
    lạ lâu dài). Mỗi track một mục, giữ tối đa ``top_k`` khung tốt nhất.
    """

    def __init__(self, size: int = UNKNOWN_FACE_LOG_SIZE, ttl_s: float = UNKNOWN_FACE_TTL_S, top_k: int = 3,
                 max_samples: int = 5, merge_sim: float = UNKNOWN_FACE_MERGE_SIM):
        self.size = size
        self.ttl_s = ttl_s
        self.top_k = top_k
        self.max_samples = max_samples
        self.merge_sim = merge_sim
        self._items: "OrderedDict[str, dict]" = OrderedDict()
        self._by_track: Dict[Tuple[str, int], str] = {}
        self._lock = threading.Lock()

    def upsert(self, camera_id: str, track_id: int, samples: List[Tuple[float, np.ndarray, bytes, int]]) -> None:
        """``samples`` = [(quality, norm_emb, jpeg, face_width)], tốt nhất trước.

        Tracker hay cấp id mới cho CÙNG một người (bị che, đi qua lại) -> track
        mới mà mặt giống hẳn một mục đã có (cùng camera) thì GỘP vào mục đó, để
        cán bộ gán một lần cho cả cụm thay vì thấy cùng một người lặp lại chục thẻ.
        """
        if not samples:
            return
        now = time.time()
        with self._lock:
            key = (camera_id, int(track_id))
            uid = self._by_track.get(key)
            item = self._items.get(uid) if uid else None
            if item is None:
                item = self._find_similar(camera_id, samples)
                if item is None:
                    item = {"id": uuid.uuid4().hex[:12], "camera_id": camera_id, "track_id": int(track_id),
                            "first_seen": now, "tracks": {}}
                uid = item["id"]
                self._by_track[key] = uid
            item["tracks"][int(track_id)] = list(samples[: self.top_k])
            merged = [smp for rows in item["tracks"].values() for smp in rows]
            merged.sort(key=lambda t: -t[0])
            item["samples"] = merged[: self.max_samples]
            item["centroid"] = self._centroid(item["samples"])
            item["last_seen"] = now
            self._items[uid] = item
            self._items.move_to_end(uid)
            self._evict(now)

    @staticmethod
    def _centroid(samples) -> np.ndarray:
        c = np.sum([smp[1] for smp in samples], axis=0).astype(np.float32)
        return c / (np.linalg.norm(c) or 1.0)

    def _find_similar(self, camera_id: str, samples) -> Optional[dict]:
        probe = self._centroid(samples)
        best, best_sim = None, self.merge_sim
        for it in self._items.values():
            if it["camera_id"] != camera_id or it.get("centroid") is None:
                continue
            sim = float(it["centroid"] @ probe)
            if sim >= best_sim:
                best, best_sim = it, sim
        return best

    def drop_track(self, camera_id: str, track_id: int) -> None:
        """Track đã được nhận ra -> bỏ phần đóng góp của track đó (mục chỉ còn
        track này thì bỏ cả mục)."""
        with self._lock:
            uid = self._by_track.pop((camera_id, int(track_id)), None)
            item = self._items.get(uid) if uid else None
            if item is None:
                return
            item["tracks"].pop(int(track_id), None)
            if not item["tracks"]:
                self._items.pop(uid, None)
                return
            merged = sorted((smp for rows in item["tracks"].values() for smp in rows), key=lambda t: -t[0])
            item["samples"] = merged[: self.max_samples]
            item["centroid"] = self._centroid(item["samples"])

    def _evict(self, now: float) -> None:
        for uid in [u for u, it in self._items.items() if now - it["last_seen"] > self.ttl_s]:
            self._drop(uid)
        while len(self._items) > self.size:
            self._drop(next(iter(self._items)))

    def _drop(self, uid: str) -> None:
        item = self._items.pop(uid, None)
        if item:
            for tid in list(item.get("tracks") or [item["track_id"]]):
                self._by_track.pop((item["camera_id"], tid), None)

    def list(self) -> List[Dict]:
        with self._lock:
            self._evict(time.time())
            out = []
            for it in reversed(self._items.values()):
                q, _emb, _jpg, width = it["samples"][0]
                out.append({
                    "id": it["id"], "camera_id": it["camera_id"], "track_id": it["track_id"],
                    "first_seen": datetime.fromtimestamp(it["first_seen"]).isoformat(timespec="seconds"),
                    "last_seen": datetime.fromtimestamp(it["last_seen"]).isoformat(timespec="seconds"),
                    "face_width": int(width), "quality": round(float(q), 2),
                    "samples": len(it["samples"]), "tracks": len(it.get("tracks") or {}),
                })
            return out

    def get(self, uid: str) -> Optional[dict]:
        with self._lock:
            return self._items.get(uid)

    def pop(self, uid: str) -> Optional[dict]:
        with self._lock:
            item = self._items.get(uid)
            if item:
                self._drop(uid)
            return item


class FaceEngine:
    """Manages face detection, 512-d embedding extraction, persistence, and matching."""

    # Mặt nhỏ hơn ngưỡng này (tính bằng pixel bề ngang) cho embedding kém tin cậy
    # nên phải vượt ngưỡng tương đồng cao hơn mới được chấp nhận.
    SMALL_FACE_PIXELS = 44
    SMALL_FACE_PENALTY = 0.06
    # Khoảng cách tối thiểu giữa người khớp nhất và người khớp nhì, tránh nhận nhầm
    # giữa hai quân nhân có nét giống nhau.
    AMBIGUITY_MARGIN = 0.05

    # Cổng chất lượng lúc ĐĂNG KÝ (không áp cho nhận diện lúc chạy): ảnh mờ/mặt
    # quá nhỏ cho embedding không đáng tin — đăng ký vào rồi cả ca nhận nhầm còn
    # tệ hơn từ chối ngay lúc đăng ký.
    MIN_ENROLL_DET_SCORE = 0.6
    MIN_ENROLL_FACE_WIDTH = 60

    def __init__(
        self,
        model_name: Optional[str] = None,
        similarity_threshold: Optional[float] = None,
        data_dir: str = "data"
    ):
        settings = hw.settings()
        # buffalo_l (R50) chính xác hơn hẳn buffalo_s (MobileFaceNet) trên ảnh camera
        # mờ; buffalo_s chỉ nên dùng khi CPU yếu không chạy kịp R50 (xem app/hw.py).
        self.model_name = model_name or os.environ.get("FACE_MODEL") or settings.face_model
        # 0.36 (mặc định cũ) với 0.30 (từng chỉnh lúc triển khai) đều quá thấp cho
        # ArcFace trên ảnh CCTV mờ/góc xấu -> dễ nhận nhầm hai quân nhân khác nhau.
        # Nâng mặc định lên 0.40; vẫn ghi đè được qua biến môi trường khi cần.
        self.similarity_threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else float(os.environ.get("FACE_SIM_THRESHOLD", "0.40"))
        )
        if self.similarity_threshold < 0.35:
            print(f"[FaceEngine] CẢNH BÁO: FACE_SIM_THRESHOLD={self.similarity_threshold} quá thấp "
                  f"cho ArcFace trên ảnh CCTV (khuyến nghị >= 0.40), dễ nhận nhầm người.")
        # det_size lớn hơn để bắt được khuôn mặt nhỏ ở xa; det_thresh thấp hơn để
        # không bỏ sót mặt mờ. Cả hai chỉnh được qua biến môi trường.
        det = int(os.environ.get("FACE_DET_SIZE") or settings.face_det_size)
        self.det_size = (det, det)
        self.det_thresh = float(os.environ.get("FACE_DET_THRESHOLD", "0.30"))
        self.data_dir = Path(data_dir)
        self.avatars_dir = self.data_dir / "face_avatars"
        self.db_file = self.data_dir / "registered_faces.json"
        self._last_db_mtime = 0
        self._matrix_cache = None
        # Nhiều thread camera gọi recognize_faces_in_frame() song song, còn đăng ký
        # khuôn mặt mới (register_person/_save_database) có thể chạy từ request HTTP
        # khác cùng lúc — khoá lại để tránh đọc nửa file hoặc dựng ma trận từ dữ liệu
        # đang bị ghi đè.
        self._db_lock = threading.Lock()
        self.unknown_log = UnknownFaceLog()

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.avatars_dir.mkdir(parents=True, exist_ok=True)

        self.registered_faces: List[Dict] = []
        self._load_database()

        # Initialize InsightFace App
        self.app = None
        self._init_insightface(self.model_name, settings)

    def _init_insightface(self, model_name: str, settings=None):
        """Initialize the InsightFace FaceAnalysis application."""
        settings = settings or hw.settings()
        try:
            # Wheel torch (cu128) đem theo libcudnn/libcublas riêng, không nằm trong
            # LD_LIBRARY_PATH hệ thống — nạp trước thì onnxruntime-gpu tìm thấy mà
            # không cần chỉnh biến môi trường trong container (no-op nếu không phải
            # CUDA hoặc bản onnxruntime không có preload_dlls()).
            hw.preload_cuda_dlls()
            from insightface.app import FaceAnalysis

            print(f"[FaceEngine] Initializing InsightFace ({model_name})...")
            # Chỉ tải detection + recognition: landmark_2d_106, landmark_3d_68,
            # genderage không được dùng ở đâu trong app/static (đã rà soát), tải
            # thêm chỉ tốn thời gian khởi động và RAM vô ích.
            self.app = FaceAnalysis(
                name=model_name,
                providers=list(settings.face_providers),
                allowed_modules=['detection', 'recognition'],
            )
            ctx_id = 0 if settings.device == "cuda" else -1
            self.app.prepare(ctx_id=ctx_id, det_size=self.det_size, det_thresh=self.det_thresh)
            self._rebuild_sessions(settings)
            providers_applied = self._applied_providers()
            print(f"[FaceEngine] InsightFace initialized. Providers: {providers_applied}")
        except Exception as e:
            print(f"[FaceEngine] Warning: Could not initialize InsightFace on {settings.device}: {e}")
            try:
                from insightface.app import FaceAnalysis
                self.app = FaceAnalysis(
                    name=model_name,
                    providers=['CPUExecutionProvider'],
                    allowed_modules=['detection', 'recognition'],
                )
                self.app.prepare(ctx_id=-1, det_size=self.det_size, det_thresh=self.det_thresh)
                self._rebuild_sessions(hw.settings())
                print(f"[FaceEngine] InsightFace initialized on CPU. Providers: {self._applied_providers()}")
            except Exception as ex:
                print(f"[FaceEngine] Error loading InsightFace model: {ex}")
                self.app = None

    def _rebuild_sessions(self, settings) -> None:
        """Dựng lại ORT session của từng model con (detection/recognition) với
        SessionOptions đúng số luồng + tối ưu đồ thị tối đa.

        insightface tự tạo session bên trong FaceAnalysis(...) (model_zoo.get_model)
        chỉ với providers, KHÔNG có SessionOptions — mỗi model tự xin hết số nhân CPU,
        tranh nhau và tranh với torch (YOLO) chạy cùng tiến trình. Giữ nguyên file
        .onnx + providers đang áp dụng, chỉ đổi SessionOptions nên không cần load
        lại từ đầu hay gọi lại prepare().
        """
        try:
            import onnxruntime as ort
        except ImportError:
            return
        for model in getattr(self.app, "models", {}).values():
            session = getattr(model, "session", None)
            model_file = getattr(model, "model_file", None)
            if session is None or model_file is None:
                continue
            try:
                so = hw.ort_session_options()
                model.session = ort.InferenceSession(
                    model_file, sess_options=so, providers=session.get_providers()
                )
            except Exception as e:
                print(f"[FaceEngine] Không dựng lại được session cho {type(model).__name__}: {e}")

    def _applied_providers(self) -> list:
        """Providers THỰC SỰ đang chạy (onnxruntime có thể rơi về CPU dù xin CUDA
        nếu máy cài bản CPU-only), lấy từ session của model detection để log đúng
        sự thật thay vì lặp lại danh sách đã xin."""
        try:
            det_model = self.app.models.get("detection")
            if det_model is not None and getattr(det_model, "session", None) is not None:
                return det_model.session.get_providers()
        except Exception:
            pass
        return []

    def _load_database(self, force: bool = False):
        """Load registered faces database from JSON if modified or forced.

        Khoá lại vì nhiều thread camera gọi recognize_faces_in_frame() (qua đây)
        cùng lúc với request HTTP đăng ký/xoá quân nhân — không khoá thì có thể
        đọc registered_faces giữa lúc đang bị _save_database() ghi đè.
        """
        with self._db_lock:
            if self.db_file.exists():
                try:
                    mtime = self.db_file.stat().st_mtime
                    if force or mtime != self._last_db_mtime:
                        with open(self.db_file, "r", encoding="utf-8") as f:
                            self.registered_faces = json.load(f)
                        self._last_db_mtime = mtime
                        self._matrix_cache = None
                        print(f"[FaceEngine] Loaded {len(self.registered_faces)} registered personnel from DB.")
                except Exception as e:
                    print(f"[FaceEngine] Error reading face database: {e}")
            else:
                self.registered_faces = []

    def _save_database(self):
        """Persist registered faces database to JSON."""
        with self._db_lock:
            try:
                with open(self.db_file, "w", encoding="utf-8") as f:
                    json.dump(self.registered_faces, f, ensure_ascii=False, indent=2)
                self._matrix_cache = None
                if self.db_file.exists():
                    self._last_db_mtime = self.db_file.stat().st_mtime
            except Exception as e:
                print(f"[FaceEngine] Error saving face database: {e}")

    def extract_face_and_embedding(
        self, frame: np.ndarray, enforce_quality: bool = False
    ) -> Optional[Tuple[np.ndarray, np.ndarray, Tuple[int, int, int, int]]]:
        """
        Extract the most prominent face embedding and bounding box from an image.

        ``enforce_quality=True`` (dùng lúc ĐĂNG KÝ, xem ``register_person``) ném
        ``ValueError`` tiếng Việt khi mặt quá mờ/quá nhỏ, theo đúng đường lỗi mà
        API đăng ký đã bắt sẵn (``except Exception`` -> HTTPException 400).

        Returns:
            (norm_embedding, cropped_face_img, (x1, y1, x2, y2)) or None
        """
        if self.app is None or frame is None:
            return None

        # InsightFace expects BGR as standard OpenCV
        faces = self.app.get(frame)
        if not faces or len(faces) == 0:
            return None

        # Pick the largest face by bounding box area
        largest_face = None
        max_area = 0
        for f in faces:
            bbox = f.bbox.astype(int)
            w = max(0, bbox[2] - bbox[0])
            h = max(0, bbox[3] - bbox[1])
            area = w * h
            if area > max_area:
                max_area = area
                largest_face = f

        if largest_face is None or largest_face.embedding is None:
            return None

        if enforce_quality:
            det_score = float(getattr(largest_face, "det_score", 1.0))
            fb = largest_face.bbox.astype(int)
            face_width = int(fb[2] - fb[0])
            if det_score < self.MIN_ENROLL_DET_SCORE or face_width < self.MIN_ENROLL_FACE_WIDTH:
                raise ValueError(
                    "Ảnh khuôn mặt chưa đủ rõ để đăng ký (độ tin cậy phát hiện "
                    f"{det_score:.2f}, bề ngang {face_width}px). Vui lòng chụp lại gần hơn, "
                    "đủ sáng và nhìn thẳng vào camera."
                )

        # Normalize embedding vector
        emb = largest_face.embedding
        norm_emb = emb / np.linalg.norm(emb)

        # Crop face for avatar
        h_img, w_img = frame.shape[:2]
        bx1, by1, bx2, by2 = largest_face.bbox.astype(int)
        # Add slight margin
        pad_x = int((bx2 - bx1) * 0.15)
        pad_y = int((by2 - by1) * 0.15)
        x1 = max(0, bx1 - pad_x)
        y1 = max(0, by1 - pad_y)
        x2 = min(w_img, bx2 + pad_x)
        y2 = min(h_img, by2 + pad_y)

        cropped_face = frame[y1:y2, x1:x2]
        return norm_emb, cropped_face, (int(bx1), int(by1), int(bx2), int(by2))

    def register_person(
        self,
        name: str,
        military_id: str,
        rank: str,
        unit: str,
        image: np.ndarray,
        status: str = "Active"
    ) -> Dict:
        """
        Extract face embedding from image and register a new soldier into the database.
        """
        extracted = self.extract_face_and_embedding(image, enforce_quality=True)
        if extracted is None:
            raise ValueError("Không phát hiện được khuôn mặt trong ảnh. Vui lòng căn chỉnh góc chụp rõ nét và đủ ánh sáng.")

        norm_emb, cropped_face, _ = extracted

        # Check if military ID already exists
        for idx, person in enumerate(self.registered_faces):
            if person.get("military_id", "").strip().upper() == military_id.strip().upper():
                avatar_filename = f"{military_id.strip().upper().replace('/', '_')}.jpg"
                avatar_path = self.avatars_dir / avatar_filename
                cv2.imwrite(str(avatar_path), cropped_face)

                person.update({
                    "name": name.strip(),
                    "rank": rank.strip(),
                    "unit": unit.strip(),
                    "status": status,
                    "avatar_path": f"/data/face_avatars/{avatar_filename}",
                    "embedding": norm_emb.tolist(),
                    "embedding_model": self.model_name,
                    "updated_at": datetime.now().isoformat()
                })
                self._save_database()
                return person

        # Generate new registration record
        person_id = f"person_{int(datetime.now().timestamp() * 1000)}"
        avatar_filename = f"{military_id.strip().upper().replace('/', '_')}_{person_id}.jpg"
        avatar_path = self.avatars_dir / avatar_filename
        cv2.imwrite(str(avatar_path), cropped_face)

        new_person = {
            "id": person_id,
            "name": name.strip(),
            "military_id": military_id.strip(),
            "rank": rank.strip(),
            "unit": unit.strip(),
            "status": status,
            "avatar_path": f"/data/face_avatars/{avatar_filename}",
            "embedding": norm_emb.tolist(),
            "embedding_model": self.model_name,
            "created_at": datetime.now().strftime("%d/%m/%Y"),
            "timestamp": datetime.now().isoformat()
        }

        self.registered_faces.append(new_person)
        self._save_database()
        return new_person

    def get_registered_faces(
        self,
        query: Optional[str] = None,
        unit: Optional[str] = None,
        rank: Optional[str] = None
    ) -> List[Dict]:
        """Return list of registered faces with embeddings removed for UI efficiency."""
        self._load_database()
        results = []
        for p in self.registered_faces:
            if query:
                q = query.lower().strip()
                if q not in p.get("name", "").lower() and q not in p.get("military_id", "").lower():
                    continue

            if unit and unit != "all" and unit != "Tất cả đơn vị":
                if p.get("unit") != unit:
                    continue

            if rank and rank != "all" and rank != "Tất cả cấp bậc":
                if p.get("rank") != rank:
                    continue

            results.append(self.public_view(p))

        return results

    def delete_person(self, person_id: str) -> bool:
        """Delete a registered person by ID."""
        self._load_database()
        initial_len = len(self.registered_faces)
        self.registered_faces = [p for p in self.registered_faces if p.get("id") != person_id and p.get("military_id") != person_id]
        if len(self.registered_faces) < initial_len:
            self._save_database()
            return True
        return False

    def update_person(self, person_id: str, updates: Dict) -> Optional[Dict]:
        """Update person metadata."""
        self._load_database()
        for person in self.registered_faces:
            if person.get("id") == person_id or person.get("military_id") == person_id:
                for k in ["name", "military_id", "rank", "unit", "status"]:
                    if k in updates and updates[k] is not None:
                        person[k] = str(updates[k]).strip()
                person["updated_at"] = datetime.now().isoformat()
                self._save_database()
                return self.public_view(person)
        return None

    # ---------- kho mẫu (ảnh đăng ký + mẫu bổ sung) ----------

    @staticmethod
    def _person_ref(p: Dict) -> Dict:
        return {
            "id": p.get("id"),
            "name": p.get("name"),
            "military_id": p.get("military_id"),
            "rank": p.get("rank"),
            "unit": p.get("unit"),
            "avatar_path": p.get("avatar_path"),
        }

    @staticmethod
    def public_view(p: Dict) -> Dict:
        """Bản ghi quân nhân cho API: bỏ embedding (sinh trắc, nặng), mẫu bổ sung
        chỉ còn số lượng + nguồn."""
        view = {k: v for k, v in p.items() if k not in ("embedding", "samples")}
        samples = p.get("samples") or []
        view["sample_count"] = len(samples)
        view["sample_sources"] = {
            src: sum(1 for x in samples if x.get("source") == src)
            for src in sorted({x.get("source", "?") for x in samples})
        }
        return view

    def _gallery(self):
        """(ma trận mẫu [R,512], chỉ số người của từng hàng [R], danh sách người)
        — dựng lại chỉ khi CSDL đổi. Mỗi người có 1 hàng ảnh đăng ký + các hàng
        mẫu bổ sung; điểm của một người = MAX trên các hàng của người đó."""
        self._load_database()
        # Khoá lại vì thread camera khác hoặc request đăng ký có thể đang đổi
        # registered_faces/_matrix_cache cùng lúc (đặt lại None trong _save_database).
        with self._db_lock:
            if self._matrix_cache is None:
                rows, owners, persons, stale = [], [], [], []
                for p in self.registered_faces:
                    if not p.get("embedding"):
                        continue
                    # Mỗi model sinh ra một không gian vector riêng. Đem embedding của
                    # model cũ so với model đang chạy chỉ cho ra số ngẫu nhiên, dễ nhận
                    # nhầm người, nên phải loại ra thay vì dùng bừa.
                    if p.get("embedding_model") != self.model_name:
                        stale.append(p.get("name") or p.get("military_id") or "?")
                        continue
                    pidx = len(persons)
                    persons.append(p)
                    rows.append(p["embedding"])
                    owners.append(pidx)
                    for smp in p.get("samples") or []:
                        if smp.get("embedding") and smp.get("model", self.model_name) == self.model_name:
                            rows.append(smp["embedding"])
                            owners.append(pidx)

                if stale:
                    print(f"[FaceEngine] BỎ QUA {len(stale)} quân nhân đăng ký bằng model khác "
                          f"'{self.model_name}': {', '.join(stale[:10])}"
                          f"{'...' if len(stale) > 10 else ''}. Cần đăng ký lại khuôn mặt "
                          f"(hoặc đặt FACE_MODEL về model cũ).")
                if not rows:
                    return None
                self._matrix_cache = (np.array(rows, dtype=np.float32), np.array(owners, dtype=np.int64), persons)
            return self._matrix_cache

    @staticmethod
    def _match(norm_emb: np.ndarray, gallery, threshold: float, margin_req: float):
        """(người khớp hoặc None, điểm tốt nhất, khoảng cách tới NGƯỜI khớp nhì).

        Khoảng cách "mập mờ" tính giữa các NGƯỜI khác nhau, không giữa các mẫu —
        hai mẫu của cùng một người đương nhiên gần nhau, không được coi là mập mờ.
        """
        matrix, owners, persons = gallery
        sims = matrix @ norm_emb
        per_person = np.full(len(persons), -1.0, dtype=np.float32)
        np.maximum.at(per_person, owners, sims)
        best_idx = int(np.argmax(per_person))
        best = float(per_person[best_idx])
        second = float(np.partition(per_person, -2)[-2]) if len(persons) > 1 else -1.0
        margin = best - second
        if best >= threshold and margin >= margin_req:
            return persons[best_idx], best, margin
        return None, best, margin

    def match_embedding(self, norm_emb: np.ndarray, threshold: float, margin_req: float):
        """So một embedding (vd. embedding GỘP của cả track) với kho mẫu.
        Trả (person_ref | None, điểm, khoảng cách tới người nhì)."""
        gallery = self._gallery()
        if gallery is None or norm_emb is None:
            return None, 0.0, 0.0
        person, best, margin = self._match(np.asarray(norm_emb, dtype=np.float32), gallery, threshold, margin_req)
        return (self._person_ref(person) if person is not None else None), best, margin

    def add_samples(self, person_id: str, embeddings: List[np.ndarray], source: str) -> int:
        """Thêm mẫu bổ sung cho một quân nhân. ``source``: "camera" (gán tay từ
        mặt chưa xác định), "upload" (ảnh thêm), "auto" (tự học). Mẫu "auto" bị
        giới hạn riêng (FIFO) để tự học sai không lấn át mẫu do người gán.
        Trả số mẫu thực sự được thêm."""
        self._load_database()
        added = 0
        with self._db_lock:
            person = next((p for p in self.registered_faces
                           if p.get("id") == person_id or p.get("military_id") == person_id), None)
            if person is None:
                raise KeyError(person_id)
            samples = person.setdefault("samples", [])
            existing = [np.asarray(x["embedding"], dtype=np.float32) for x in samples if x.get("embedding")]
            if person.get("embedding"):
                existing.append(np.asarray(person["embedding"], dtype=np.float32))
            for emb in embeddings:
                e = np.asarray(emb, dtype=np.float32)
                e = e / (np.linalg.norm(e) or 1.0)
                if existing and max(float(x @ e) for x in existing) >= FACE_SAMPLE_DEDUP_SIM:
                    continue
                samples.append({"embedding": [round(float(v), 6) for v in e], "source": source,
                                "model": self.model_name, "created_at": datetime.now().isoformat(timespec="seconds")})
                existing.append(e)
                added += 1
            auto = [x for x in samples if x.get("source") == "auto"]
            while len(auto) > FACE_MAX_AUTO_SAMPLES:
                samples.remove(auto.pop(0))
            while len(samples) > FACE_MAX_SAMPLES:
                # Bỏ mẫu cũ nhất, ưu tiên bỏ mẫu tự học trước
                victim = next((x for x in samples if x.get("source") == "auto"), samples[0])
                samples.remove(victim)
        if added:
            self._save_database()
        return added

    def clear_samples(self, person_id: str, source: Optional[str] = None) -> int:
        """Xoá mẫu bổ sung (tất cả, hoặc chỉ một nguồn). Trả số mẫu đã xoá."""
        self._load_database()
        with self._db_lock:
            person = next((p for p in self.registered_faces
                           if p.get("id") == person_id or p.get("military_id") == person_id), None)
            if person is None:
                raise KeyError(person_id)
            before = len(person.get("samples") or [])
            person["samples"] = [x for x in person.get("samples") or []
                                 if source is not None and x.get("source") != source]
            removed = before - len(person["samples"])
        if removed:
            self._save_database()
        return removed

    def sample_from_image(self, image: np.ndarray) -> np.ndarray:
        """Embedding mặt lớn nhất trong ảnh tải lên (để thêm mẫu)."""
        extracted = self.extract_face_and_embedding(image, enforce_quality=False)
        if extracted is None:
            raise ValueError("Không phát hiện được khuôn mặt trong ảnh.")
        return extracted[0]

    def recognize_faces_in_frame(self, frame: np.ndarray) -> List[Dict]:
        """
        Quét khuôn mặt trên TOÀN khung hình một lượt duy nhất rồi so với CSDL.

        Chạy độc lập với YOLO nên hai model có thể xử lý song song; việc gắn mặt
        vào từng người do phía gọi đảm nhiệm.

        Returns:
            List of detected faces with bbox and match info
        """
        self._load_database()
        if self.app is None or frame is None or len(self.registered_faces) == 0:
            return []

        gallery = self._gallery()
        if gallery is None:
            return []

        all_detected_faces = []

        try:
            for f in self.app.get(frame):
                all_detected_faces.append({
                    "bbox": f.bbox.astype(int),
                    "embedding": f.embedding,
                    "det_score": float(f.det_score) if hasattr(f, 'det_score') else 1.0
                })
        except Exception as e:
            print(f"[FaceEngine] Lỗi quét khuôn mặt: {e}")

        # Match embeddings against registered database
        results = []
        for face_dict in all_detected_faces:
            bx1, by1, bx2, by2 = face_dict["bbox"]
            emb = face_dict["embedding"]
            if emb is None:
                continue

            norm_emb = (emb / np.linalg.norm(emb)).astype(np.float32)  # Shape: (512,)
            width = int(bx2 - bx1)

            # Mặt càng nhỏ, embedding càng nhiễu -> siết ngưỡng lại
            threshold = self.similarity_threshold
            if width < self.SMALL_FACE_PIXELS:
                threshold += self.SMALL_FACE_PENALTY

            matched_person, best_sim, margin = self._match(norm_emb, gallery, threshold, self.AMBIGUITY_MARGIN)
            row = {
                "bbox": [int(bx1), int(by1), int(bx2), int(by2)],
                "is_recognized": matched_person is not None,
                "similarity": round(best_sim, 2),
                "margin": round(margin, 3),
                "det_score": face_dict["det_score"],
                "width": width,
                # Embedding đã chuẩn hoá — để phía gọi gộp theo track/bổ sung mẫu.
                # KHÔNG đưa ra API/SSE (mảng numpy, và là dữ liệu sinh trắc).
                "_emb": norm_emb,
                "person": None,
            }
            if matched_person is not None:
                row["person"] = self._person_ref(matched_person)
            results.append(row)

        return results

    def recognize_in_regions(self, frame: np.ndarray, regions: List[Tuple[int, int, int, int]]) -> List[Dict]:
        """Quét lại khuôn mặt bên trong từng vùng ảnh nhỏ rồi quy chiếu về toạ độ gốc.

        Camera độ phân giải thấp cho khuôn mặt chỉ vài chục pixel trên khung hình
        đầy đủ, quét cả khung thường bỏ sót. Cắt riêng vùng người rồi đưa vào
        InsightFace sẽ được phóng to lên det_size nên bắt được nhiều mặt hơn.
        """
        if self.app is None or frame is None or not regions:
            return []

        h_img, w_img = frame.shape[:2]
        results = []
        for (rx1, ry1, rx2, ry2) in regions:
            x1 = max(0, int(rx1))
            y1 = max(0, int(ry1))
            x2 = min(w_img, int(rx2))
            y2 = min(h_img, int(ry2))
            if x2 - x1 < 20 or y2 - y1 < 20:
                continue

            crop = frame[y1:y2, x1:x2]
            for face in self.recognize_faces_in_frame(crop):
                fb = face["bbox"]
                face["bbox"] = [fb[0] + x1, fb[1] + y1, fb[2] + x1, fb[3] + y1]
                results.append(face)

        return results
