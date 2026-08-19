"""Face registration, embedding extraction, and recognition engine using InsightFace."""

import os
import json
import base64
import numpy as np
import cv2
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple


class FaceEngine:
    """Manages face detection, 512-d embedding extraction, persistence, and matching."""

    def __init__(
        self,
        model_name: str = "buffalo_s",
        similarity_threshold: float = 0.38,
        data_dir: str = "data"
    ):
        self.similarity_threshold = similarity_threshold
        self.data_dir = Path(data_dir)
        self.avatars_dir = self.data_dir / "face_avatars"
        self.db_file = self.data_dir / "registered_faces.json"
        self._last_db_mtime = 0

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.avatars_dir.mkdir(parents=True, exist_ok=True)

        self.registered_faces: List[Dict] = []
        self._load_database()

        # Initialize InsightFace App
        self.app = None
        self._init_insightface(model_name)

    def _init_insightface(self, model_name: str):
        """Initialize the InsightFace FaceAnalysis application."""
        try:
            import insightface
            from insightface.app import FaceAnalysis

            print(f"[FaceEngine] Initializing InsightFace ({model_name})...")
            # Providers: CUDAExecutionProvider if available, else CPUExecutionProvider
            self.app = FaceAnalysis(name=model_name, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
            self.app.prepare(ctx_id=0, det_size=(640, 640), det_thresh=0.40)
            print("[FaceEngine] InsightFace initialized successfully.")
        except Exception as e:
            print(f"[FaceEngine] Warning: Could not initialize InsightFace on GPU: {e}")
            try:
                from insightface.app import FaceAnalysis
                self.app = FaceAnalysis(name=model_name, providers=['CPUExecutionProvider'])
                self.app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.40)
                print("[FaceEngine] InsightFace initialized on CPU.")
            except Exception as ex:
                print(f"[FaceEngine] Error loading InsightFace model: {ex}")
                self.app = None

    def _load_database(self, force: bool = False):
        """Load registered faces database from JSON if modified or forced."""
        if self.db_file.exists():
            try:
                mtime = self.db_file.stat().st_mtime
                if force or mtime != self._last_db_mtime:
                    with open(self.db_file, "r", encoding="utf-8") as f:
                        self.registered_faces = json.load(f)
                    self._last_db_mtime = mtime
                    print(f"[FaceEngine] Loaded {len(self.registered_faces)} registered personnel from DB.")
            except Exception as e:
                print(f"[FaceEngine] Error reading face database: {e}")
        else:
            self.registered_faces = []

    def _save_database(self):
        """Persist registered faces database to JSON."""
        try:
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(self.registered_faces, f, ensure_ascii=False, indent=2)
            if self.db_file.exists():
                self._last_db_mtime = self.db_file.stat().st_mtime
        except Exception as e:
            print(f"[FaceEngine] Error saving face database: {e}")

    def extract_face_and_embedding(self, frame: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray, Tuple[int, int, int, int]]]:
        """
        Extract the most prominent face embedding and bounding box from an image.
        
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
        extracted = self.extract_face_and_embedding(image)
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

            person_view = {k: v for k, v in p.items() if k != "embedding"}
            results.append(person_view)

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
                return {k: v for k, v in person.items() if k != "embedding"}
        return None

    def recognize_faces_in_frame(self, frame: np.ndarray, person_boxes: Optional[List[Tuple[int, int, int, int]]] = None) -> List[Dict]:
        """
        Detect all faces in frame (and inside detected person crops) and match with registered database.
        
        Returns:
            List of detected faces with bbox and match info
        """
        self._load_database()
        if self.app is None or frame is None or len(self.registered_faces) == 0:
            return []

        # Prepare registered embeddings matrix
        reg_embeddings = []
        reg_persons = []
        for p in self.registered_faces:
            if "embedding" in p and p["embedding"] and len(p["embedding"]) > 0:
                reg_embeddings.append(p["embedding"])
                reg_persons.append(p)

        if not reg_embeddings:
            return []

        reg_matrix = np.array(reg_embeddings, dtype=np.float32)  # Shape: (N, 512)
        h_img, w_img = frame.shape[:2]

        all_detected_faces = []

        # 1. First pass: detect faces in whole frame
        try:
            full_faces = self.app.get(frame)
            if full_faces:
                for f in full_faces:
                    all_detected_faces.append({
                        "bbox": f.bbox.astype(int),
                        "embedding": f.embedding,
                        "det_score": float(f.det_score) if hasattr(f, 'det_score') else 1.0
                    })
        except Exception as e:
            print(f"[FaceEngine] Full frame recognition error: {e}")

        # 2. Second pass: detect inside cropped person regions for enhanced sensitivity
        if person_boxes:
            for pbox in person_boxes:
                px1, py1, px2, py2 = pbox
                # Crop upper body (head region)
                hx1 = max(0, px1 - 10)
                hy1 = max(0, py1 - 10)
                hx2 = min(w_img, px2 + 10)
                hy2 = min(h_img, py1 + int((py2 - py1) * 0.65))
                
                if (hx2 - hx1) < 25 or (hy2 - hy1) < 25:
                    continue

                head_crop = frame[hy1:hy2, hx1:hx2]
                try:
                    crop_faces = self.app.get(head_crop)
                    if crop_faces:
                        for cf in crop_faces:
                            cbx1, cby1, cbx2, cby2 = cf.bbox.astype(int)
                            # Translate back to global frame coordinates
                            global_bbox = np.array([hx1 + cbx1, hy1 + cby1, hx1 + cbx2, hy1 + cby2])
                            
                            # Check overlap with existing detected faces to avoid duplicates
                            is_dup = False
                            for existing in all_detected_faces:
                                e_bx1, e_by1, e_bx2, e_by2 = existing["bbox"]
                                # Compute IoU or distance
                                inter_x1 = max(global_bbox[0], e_bx1)
                                inter_y1 = max(global_bbox[1], e_by1)
                                inter_x2 = min(global_bbox[2], e_bx2)
                                inter_y2 = min(global_bbox[3], e_by2)
                                inter_w = max(0, inter_x2 - inter_x1)
                                inter_h = max(0, inter_y2 - inter_y1)
                                inter_area = inter_w * inter_h
                                
                                area1 = max(1, (global_bbox[2] - global_bbox[0]) * (global_bbox[3] - global_bbox[1]))
                                if inter_area / area1 > 0.4:
                                    is_dup = True
                                    break
                            
                            if not is_dup:
                                all_detected_faces.append({
                                    "bbox": global_bbox,
                                    "embedding": cf.embedding,
                                    "det_score": float(cf.det_score) if hasattr(cf, 'det_score') else 1.0,
                                    "associated_person_box": pbox
                                })
                except Exception:
                    pass

        # 3. Match embeddings against registered database
        results = []
        for face_dict in all_detected_faces:
            bx1, by1, bx2, by2 = face_dict["bbox"]
            emb = face_dict["embedding"]
            if emb is None:
                continue

            norm_emb = emb / np.linalg.norm(emb)  # Shape: (512,)
            sims = np.dot(reg_matrix, norm_emb)
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])

            if best_sim >= self.similarity_threshold:
                matched_person = reg_persons[best_idx]
                results.append({
                    "bbox": [int(bx1), int(by1), int(bx2), int(by2)],
                    "is_recognized": True,
                    "similarity": round(best_sim, 2),
                    "det_score": face_dict["det_score"],
                    "associated_person_box": face_dict.get("associated_person_box"),
                    "person": {
                        "id": matched_person.get("id"),
                        "name": matched_person.get("name"),
                        "military_id": matched_person.get("military_id"),
                        "rank": matched_person.get("rank"),
                        "unit": matched_person.get("unit"),
                        "avatar_path": matched_person.get("avatar_path")
                    }
                })
            else:
                results.append({
                    "bbox": [int(bx1), int(by1), int(bx2), int(by2)],
                    "is_recognized": False,
                    "similarity": round(best_sim, 2),
                    "det_score": face_dict["det_score"],
                    "associated_person_box": face_dict.get("associated_person_box"),
                    "person": None
                })

        return results
