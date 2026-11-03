"""Chọn thiết bị (CPU/GPU), số luồng, và cấu hình model AI theo đúng máy đang chạy.

Nguồn cấu hình runtime DUY NHẤT cho detector.py và face_engine.py: hai module đó
gọi ``settings()`` thay vì tự đọc ``os.environ`` rải rác, để đổi máy (CPU <-> GPU,
thêm GPU sau khi đang chạy CPU) không cần sửa code, chỉ cần biến môi trường hoặc
chạy lại ``scripts/bench_hw.py``.

Độ ưu tiên: biến môi trường  >  data/hw_profile.json (tầng "recommended" đo được
trên đúng máy này, nếu chữ ký phần cứng còn khớp)  >  mặc định theo loại thiết bị.

Import module này KHÔNG được kéo theo torch/onnxruntime/openvino — những thư viện
đó nặng và có thể chưa cài ở máy chạy test — nên mọi import của chúng nằm trong
hàm, có guard try/except.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("horus.hw")

HW_PROFILE_PATH = Path(os.environ.get("HW_PROFILE_PATH", "data/hw_profile.json"))


# --------------------------------------------------------------------- dò phần cứng

def _cpuinfo_fields() -> Tuple[str, int]:
    """(tên CPU, số nhân vật lý). Cùng công thức với scripts/bench_hw.py để chữ ký khớp."""
    model, phys, cur = "", set(), {}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if ":" not in line:
                if cur.get("physical id") is not None:
                    phys.add((cur.get("physical id"), cur.get("core id")))
                cur = {}
                continue
            k, v = (s.strip() for s in line.split(":", 1))
            cur[k] = v
            if k == "model name":
                model = v
    except OSError:
        pass
    logical = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    physical = min(len(phys) or logical, logical)
    return model, physical


@lru_cache(maxsize=1)
def physical_cores() -> int:
    """Số nhân vật lý CPU được phép dùng (bị taskset/cgroup giới hạn thì tính phần được cấp)."""
    return _cpuinfo_fields()[1]


def _nvidia_gpu_names() -> list:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        return [line.strip() for line in r.stdout.strip().splitlines() if line.strip()]
    except Exception:
        return []


def _intel_gpu_names() -> list:
    """Chỉ dò được khi có openvino cài sẵn; thiếu thì coi như không có GPU Intel
    (giống nhánh "thiếu backend" trong scripts/bench_hw.py)."""
    try:
        import openvino as ov
    except ImportError:
        return []
    try:
        core = ov.Core()
        names = []
        for d in core.available_devices:
            if d.startswith("GPU"):
                name = core.get_property(d, "FULL_DEVICE_NAME")
                if "intel" in name.lower():
                    names.append(name)
        return names
    except Exception:
        return []


def hardware_signature() -> str:
    """Chữ ký phần cứng. PHẢI khớp công thức trong scripts/bench_hw.py (cpu_info/
    nvidia_info/intel_gpu_devices) để so được với chữ ký ghi trong hw_profile.json."""
    model, cores = _cpuinfo_fields()
    nv = _nvidia_gpu_names()
    igpu = _intel_gpu_names()
    payload = json.dumps([model, cores, nv, igpu])
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def _torch_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _ort_cuda_available() -> bool:
    try:
        import onnxruntime as ort
        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def _cuda_available() -> bool:
    """CUDA dùng được cho ÍT NHẤT một trong hai: YOLO (torch) hoặc InsightFace (onnxruntime)."""
    return _torch_cuda_available() or _ort_cuda_available()


# --------------------------------------------------------------------- hồ sơ đo sẵn

def _load_hw_profile() -> Optional[dict]:
    """Đọc data/hw_profile.json nếu còn khớp chữ ký máy hiện tại.

    Hồ sơ đo trên máy khác (CPU khác, vừa cắm/rút GPU) thì số đo không còn đúng
    nữa — dùng nhầm dễ chọn cấu hình quá nặng hoặc quá nhẹ so với máy thật.
    """
    if not HW_PROFILE_PATH.exists():
        return None
    try:
        profile = json.loads(HW_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Không đọc được %s: %s", HW_PROFILE_PATH, e)
        return None

    current = hardware_signature()
    if profile.get("signature") != current:
        logger.warning(
            "data/hw_profile.json đo trên phần cứng khác (chữ ký %s, máy hiện tại %s) — "
            "bỏ qua, dùng mặc định. Chạy lại `python scripts/bench_hw.py` trên máy này.",
            profile.get("signature"), current,
        )
        return None
    return profile


def _parse_run_key(key: str) -> dict:
    """Tách khoá kiểu "yolo11s@1280|cpu-ov|fp32|4" của bench_hw.py."""
    name_size, backend, precision, threads = key.split("|")
    name, size = name_size.split("@")
    return {"name": name, "size": int(size), "backend": backend, "precision": precision,
            "threads": int(threads)}


def _profile_overrides(profile: Optional[dict]) -> dict:
    """Rút cấu hình từ tầng "recommended" của hw_profile.json, nếu có."""
    if not profile:
        return {}
    tier = profile.get("recommended")
    if not tier or not tier.get("run"):
        return {}
    run = tier["run"]
    out = {}
    try:
        person = _parse_run_key(run["person"])
        out["yolo_model"] = person["name"] + ".pt"
        out["yolo_imgsz"] = person["size"]
        out["ai_backend"] = "openvino" if person["backend"] == "cpu-ov" else (
            "cuda" if person["backend"] in ("nv-cuda", "nv-trt") else "auto")

        face_det = _parse_run_key(run["face_det"])
        face_rec = _parse_run_key(run["face_rec"])
        out["face_det_size"] = face_det["size"]
        # buffalo_l = det_10g + w600k_r50; buffalo_s = det_500m + w600k_mbf
        out["face_model"] = "buffalo_l" if face_rec["name"] == "w600k_r50" else "buffalo_s"

        if "person_fps" in tier:
            out["ai_fps"] = int(tier["person_fps"])
        # Không có số đo riêng cho rescan: dùng cùng ngân sách với face_det_per_s
        if "face_det_per_s" in tier:
            out["max_face_rescans"] = max(1, int(tier["face_det_per_s"]))
    except (KeyError, ValueError) as e:
        logger.warning("Hồ sơ phần cứng %s có dạng lạ, bỏ qua: %s", HW_PROFILE_PATH, e)
        return {}
    return out


# --------------------------------------------------------------------- cấu hình

def resolve_device() -> str:
    env = os.environ.get("AI_DEVICE", "auto").strip().lower()
    if env in ("cpu", "cuda"):
        return env
    if env != "auto":
        logger.warning("AI_DEVICE=%s không hợp lệ, coi như 'auto'", env)
    return "cuda" if _cuda_available() else "cpu"


def resolve_threads(device: str) -> int:
    env = os.environ.get("AI_THREADS", "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            logger.warning("AI_THREADS=%s không hợp lệ, dùng mặc định", env)
    if device == "cuda":
        return 2
    return max(1, min(4, physical_cores() // 2))


@dataclass(frozen=True)
class Settings:
    device: str                  # "cpu" | "cuda"
    threads: int
    yolo_model: str
    yolo_imgsz: int
    face_model: str
    face_det_size: int
    max_face_rescans: int
    ai_fps: int
    stream_fps: int
    ai_backend: str               # "auto" | "openvino" | "pt" | "cuda"
    face_providers: tuple
    hw_profile_used: bool
    signature: str = field(repr=False)


def _device_defaults(device: str) -> dict:
    if device == "cuda":
        return {
            "yolo_imgsz": 1280,
            "face_model": "buffalo_l",
            "face_det_size": 960,
            "max_face_rescans": 4,
            "ai_fps": 10,
        }
    return {
        "yolo_imgsz": 960,
        "face_model": "buffalo_l",
        "face_det_size": 640,
        "max_face_rescans": 1,
        "ai_fps": 4,
    }


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        logger.warning("%s=%s không hợp lệ, dùng %s", name, v, default)
        return default


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Cấu hình AI đã chốt cho tiến trình này (tính một lần, cache lại).

    Gọi ``settings.cache_clear()`` (hoặc ``reload()``) nếu biến môi trường đổi
    lúc chạy (chỉ dùng trong test — app thật resolve một lần lúc khởi động).
    """
    device = resolve_device()
    threads = resolve_threads(device)
    defaults = _device_defaults(device)

    profile = _load_hw_profile()
    overrides = _profile_overrides(profile)
    merged = {**defaults, **overrides}

    yolo_model = os.environ.get("YOLO_MODEL", "").strip() or merged.get("yolo_model", "yolo11s.pt")
    yolo_imgsz = _env_int("YOLO_IMGSZ", merged["yolo_imgsz"])
    face_model = os.environ.get("FACE_MODEL", "").strip() or merged["face_model"]
    face_det_size = _env_int("FACE_DET_SIZE", merged["face_det_size"])
    max_face_rescans = _env_int("MAX_FACE_RESCANS", merged["max_face_rescans"])
    ai_fps = _env_int("AI_FPS", merged["ai_fps"])
    stream_fps = _env_int("STREAM_FPS", 12)
    ai_backend = os.environ.get("AI_BACKEND", "").strip().lower() or merged.get("ai_backend", "auto")

    face_providers = ("CUDAExecutionProvider", "CPUExecutionProvider") if device == "cuda" \
        else ("CPUExecutionProvider",)

    return Settings(
        device=device, threads=threads,
        yolo_model=yolo_model, yolo_imgsz=yolo_imgsz,
        face_model=face_model, face_det_size=face_det_size,
        max_face_rescans=max_face_rescans,
        ai_fps=ai_fps, stream_fps=stream_fps,
        ai_backend=ai_backend, face_providers=face_providers,
        hw_profile_used=profile is not None,
        signature=hardware_signature(),
    )


def reload() -> Settings:
    """Xoá cache và tính lại — dùng khi test đổi biến môi trường giữa các ca."""
    settings.cache_clear()
    physical_cores.cache_clear()
    return settings()


def describe() -> str:
    """Chuỗi log một lần lúc khởi động, biết ngay máy đang chạy cấu hình gì."""
    s = settings()
    src = "hw_profile.json" if s.hw_profile_used else "mặc định"
    return (
        f"[hw] device={s.device} threads={s.threads} nguồn_cấu_hình={src} "
        f"chữ_ký={s.signature}\n"
        f"[hw] YOLO: model={s.yolo_model} imgsz={s.yolo_imgsz} backend={s.ai_backend}\n"
        f"[hw] Face: model={s.face_model} det_size={s.face_det_size} "
        f"max_rescans={s.max_face_rescans} providers={s.face_providers}\n"
        f"[hw] fps: AI={s.ai_fps} stream={s.stream_fps}"
    )


# --------------------------------------------------------------------- áp dụng

def apply_torch_threads() -> None:
    """Giới hạn luồng torch theo settings() — không gọi thì torch chiếm hết nhân,
    tranh CPU với onnxruntime (InsightFace) chạy song song."""
    try:
        import torch
        torch.set_num_threads(settings().threads)
    except ImportError:
        pass


def ort_session_options():
    """SessionOptions dùng chung cho mọi phiên onnxruntime (InsightFace): giới hạn
    luồng theo settings() và bật tối ưu hoá đồ thị tối đa."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = settings().threads
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return so


def preload_cuda_dlls() -> None:
    """Nạp trước libcudnn/libcublas từ wheel torch (nvidia-*) để onnxruntime-gpu
    tìm thấy chúng mà không cần chỉnh LD_LIBRARY_PATH thủ công trong container."""
    if settings().device != "cuda":
        return
    try:
        import onnxruntime as ort
        preload = getattr(ort, "preload_dlls", None)
        if preload:
            preload()
    except ImportError:
        pass
