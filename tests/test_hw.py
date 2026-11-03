"""Kiểm chứng app/hw.py: chọn thiết bị, số luồng, và cấu hình theo hw_profile.json.

Chạy: uv run python tests/test_hw.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import hw

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# Biến môi trường mà hw.py đọc — lưu lại để khôi phục đúng trạng thái ban đầu sau
# mỗi nhóm kiểm thử, tránh một ca thử làm lệch ca sau.
_ENV_KEYS = [
    "AI_DEVICE", "AI_THREADS", "YOLO_MODEL", "YOLO_IMGSZ", "FACE_MODEL",
    "FACE_DET_SIZE", "MAX_FACE_RESCANS", "AI_FPS", "STREAM_FPS", "AI_BACKEND",
]
_orig_env = {k: os.environ.get(k) for k in _ENV_KEYS}
_orig_profile_path = hw.HW_PROFILE_PATH


def reset_env():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    hw.HW_PROFILE_PATH = _orig_profile_path
    hw.reload()


def restore_env():
    for k, v in _orig_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    hw.HW_PROFILE_PATH = _orig_profile_path
    hw.reload()


# ------------------------------------------------------------- mặc định theo thiết bị
print("\n[1] Mặc định theo thiết bị (monkeypatch _cuda_available, không phụ thuộc máy chạy test)")

reset_env()
_real_cuda_available = hw._cuda_available

hw._cuda_available = lambda: False
hw.reload()
s_cpu = hw.settings()
check("auto + không có CUDA -> device=cpu", s_cpu.device == "cpu")
check("CPU: yolo_imgsz=960", s_cpu.yolo_imgsz == 960, str(s_cpu.yolo_imgsz))
check("CPU: face_det_size=640", s_cpu.face_det_size == 640, str(s_cpu.face_det_size))
check("CPU: max_face_rescans=1", s_cpu.max_face_rescans == 1, str(s_cpu.max_face_rescans))
check("CPU: ai_fps=4", s_cpu.ai_fps == 4, str(s_cpu.ai_fps))
check("CPU: threads <= 4", s_cpu.threads <= 4, str(s_cpu.threads))
check("CPU: chỉ xin CPUExecutionProvider", s_cpu.face_providers == ("CPUExecutionProvider",),
      str(s_cpu.face_providers))

hw._cuda_available = lambda: True
hw.reload()
s_gpu = hw.settings()
check("auto + có CUDA -> device=cuda", s_gpu.device == "cuda")
check("GPU: yolo_imgsz=1280", s_gpu.yolo_imgsz == 1280, str(s_gpu.yolo_imgsz))
check("GPU: face_det_size=960", s_gpu.face_det_size == 960, str(s_gpu.face_det_size))
check("GPU: max_face_rescans=4", s_gpu.max_face_rescans == 4, str(s_gpu.max_face_rescans))
check("GPU: ai_fps=10", s_gpu.ai_fps == 10, str(s_gpu.ai_fps))
check("GPU: threads=2", s_gpu.threads == 2, str(s_gpu.threads))
check("GPU: thử CUDA trước, CPU sau", s_gpu.face_providers == ("CUDAExecutionProvider", "CPUExecutionProvider"),
      str(s_gpu.face_providers))
check("STREAM_FPS giống nhau cả hai thiết bị", s_cpu.stream_fps == s_gpu.stream_fps == 12)

hw._cuda_available = _real_cuda_available
reset_env()

# ------------------------------------------------------------- biến môi trường ghi đè
print("\n[2] Biến môi trường ghi đè mặc định")

hw._cuda_available = lambda: False
os.environ["AI_DEVICE"] = "cuda"      # ép cuda dù _cuda_available() giả định False
os.environ["AI_THREADS"] = "3"
os.environ["YOLO_IMGSZ"] = "1152"
os.environ["FACE_MODEL"] = "buffalo_s"
os.environ["MAX_FACE_RESCANS"] = "2"
os.environ["AI_FPS"] = "7"
hw.reload()
s = hw.settings()
check("AI_DEVICE ghi đè auto-detect", s.device == "cuda")
check("AI_THREADS ghi đè", s.threads == 3, str(s.threads))
check("YOLO_IMGSZ ghi đè", s.yolo_imgsz == 1152, str(s.yolo_imgsz))
check("FACE_MODEL ghi đè", s.face_model == "buffalo_s", str(s.face_model))
check("MAX_FACE_RESCANS ghi đè", s.max_face_rescans == 2, str(s.max_face_rescans))
check("AI_FPS ghi đè", s.ai_fps == 7, str(s.ai_fps))

os.environ["AI_DEVICE"] = "khong_hop_le"
hw.reload()
check("AI_DEVICE sai giá trị -> coi như auto (không crash)", hw.settings().device in ("cpu", "cuda"))

hw._cuda_available = _real_cuda_available
reset_env()

# ------------------------------------------------------------- hw_profile.json
print("\n[3] Đọc data/hw_profile.json (tầng recommended) khi chữ ký khớp")

tmp = Path(tempfile.mkdtemp())
profile_path = tmp / "hw_profile.json"
hw.HW_PROFILE_PATH = profile_path

real_sig = hw.hardware_signature()
profile_path.write_text(json.dumps({
    "signature": real_sig,
    "recommended": {
        "tier": "B",
        "run": {
            "person": "yolo11s@640|cpu-ov|fp32|4",
            "face_det": "det_500m@640|cpu-ov|fp32|4",
            "face_rec": "w600k_mbf@112|cpu-ov|fp32|4",
        },
        "person_fps": 6,
        "face_det_per_s": 3,
    },
}))
hw._cuda_available = lambda: False
hw.reload()
s = hw.settings()
check("signature khớp -> dùng hw_profile.json", s.hw_profile_used is True)
check("yolo_model lấy từ tier B", s.yolo_model == "yolo11s.pt", s.yolo_model)
check("yolo_imgsz lấy từ tier B (640)", s.yolo_imgsz == 640, str(s.yolo_imgsz))
check("ai_backend=openvino (person chạy cpu-ov)", s.ai_backend == "openvino", s.ai_backend)
check("face_det_size lấy từ tier B (640)", s.face_det_size == 640, str(s.face_det_size))
check("face_model suy từ face_rec=w600k_mbf -> buffalo_s", s.face_model == "buffalo_s", s.face_model)
check("ai_fps lấy từ person_fps tier B", s.ai_fps == 6, str(s.ai_fps))
check("max_face_rescans lấy từ face_det_per_s tier B", s.max_face_rescans == 3, str(s.max_face_rescans))

os.environ["YOLO_IMGSZ"] = "999"
hw.reload()
check("biến môi trường vẫn thắng hw_profile.json", hw.settings().yolo_imgsz == 999)
os.environ.pop("YOLO_IMGSZ")

print("\n[4] Chữ ký lệch -> bỏ qua hw_profile.json, dùng mặc định")

profile_path.write_text(json.dumps({
    "signature": "chu-ky-khac-may",
    "recommended": {"tier": "B", "run": {
        "person": "yolo11s@640|cpu-ov|fp32|4",
        "face_det": "det_500m@640|cpu-ov|fp32|4",
        "face_rec": "w600k_mbf@112|cpu-ov|fp32|4",
    }, "person_fps": 6, "face_det_per_s": 3},
}))
hw.reload()
s = hw.settings()
check("chữ ký lệch -> hw_profile_used=False", s.hw_profile_used is False)
check("chữ ký lệch -> rơi về mặc định CPU (imgsz=960)", s.yolo_imgsz == 960, str(s.yolo_imgsz))

print("\n[5] File hồ sơ hỏng (không phải JSON) -> không crash, dùng mặc định")
profile_path.write_text("{khong phai json")
hw.reload()
s = hw.settings()
check("JSON hỏng -> hw_profile_used=False", s.hw_profile_used is False)
check("JSON hỏng -> vẫn tính được settings() bình thường", s.device in ("cpu", "cuda"))

print("\n[6] Không có file hồ sơ -> dùng mặc định, không báo lỗi")
missing = tmp / "khong_ton_tai.json"
hw.HW_PROFILE_PATH = missing
hw.reload()
check("không có file -> hw_profile_used=False", hw.settings().hw_profile_used is False)

hw._cuda_available = _real_cuda_available
restore_env()

# ------------------------------------------------------------- chữ ký ổn định
print("\n[7] Chữ ký phần cứng ổn định giữa hai lần gọi")
check("hardware_signature() lặp lại cho cùng kết quả",
      hw.hardware_signature() == hw.hardware_signature())

# ------------------------------------------------------------- import không cần torch/onnxruntime
print("\n[8] Import app.hw không cần torch/onnxruntime cài sẵn")

_blocked = {"torch", "onnxruntime", "openvino"}
_saved_modules = {m: sys.modules.pop(m, None) for m in _blocked if m in sys.modules}


class _BlockImport:
    """Giả lập máy chưa cài torch/onnxruntime/openvino: import các tên này phải lỗi,
    còn import lại app.hw (module thuần Python) phải vẫn chạy được."""

    def __init__(self, names):
        self.names = names
        self._real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

    def __enter__(self):
        import builtins

        def fake_import(name, *a, **kw):
            if name.split(".")[0] in self.names:
                raise ImportError(f"giả lập thiếu {name}")
            return self._real_import(name, *a, **kw)

        builtins.__import__ = fake_import
        return self

    def __exit__(self, *exc):
        import builtins
        builtins.__import__ = self._real_import


hw_module_backup = sys.modules.pop("app.hw", None)
try:
    with _BlockImport(_blocked):
        import app.hw as hw_no_deps  # noqa: F401 — import tự nó là phần kiểm thử
        check("import app.hw thành công khi thiếu torch/onnxruntime/openvino", True)
        check("physical_cores() vẫn chạy được", hw_no_deps.physical_cores() >= 1)
        check("resolve_device() rơi về cpu khi thiếu cả torch và onnxruntime",
              hw_no_deps.resolve_device() == "cpu")
        check("describe() không ném lỗi", isinstance(hw_no_deps.describe(), str))
except ImportError as e:
    check("import app.hw thành công khi thiếu torch/onnxruntime/openvino", False, str(e))
finally:
    sys.modules.pop("app.hw", None)
    if hw_module_backup is not None:
        sys.modules["app.hw"] = hw_module_backup
    for m, mod in _saved_modules.items():
        if mod is not None:
            sys.modules[m] = mod

restore_env()

# ---------------------------------------------------------------- kết luận
print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
