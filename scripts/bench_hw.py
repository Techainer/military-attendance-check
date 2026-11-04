"""Dò phần cứng (CPU, GPU NVIDIA, GPU Intel), đo tốc độ model và đề xuất cấu hình AI.

Hệ thống phải chạy được trên máy chỉ có CPU hôm nay và tự tận dụng GPU khi được lắp
thêm, mà không biết trước là loại nào. Vì vậy không chọn model hay runtime theo cảm
giác: chạy script này trên chính máy đích, nó đo mọi backend có sẵn rồi ghi
``hw_profile.json`` (thiết bị, độ trễ từng model, tầng cấu hình, số camera tối đa).
App đọc file này khi khởi động, và chạy lại script khi chữ ký phần cứng thay đổi
(ví dụ vừa cắm thêm GPU).

Backend được dò:
  cpu-ov     OpenVINO trên CPU (FP32 từ ONNX, INT8 nếu có ``<tên>_int8.xml``)
  cpu-ort    ONNX Runtime CPU (FP32)
  nv-cuda    ONNX Runtime CUDA EP (FP32)          — cần onnxruntime-gpu + driver NVIDIA
  nv-trt     ONNX Runtime TensorRT EP (FP16)      — cần thêm TensorRT
  intel-gpu  OpenVINO trên iGPU/Arc (FP16)

Chạy (không cần torch):

    uv run --with openvino --with nncf python scripts/bench_hw.py \
        --models models/ --cameras 4 --duty 0.3 --clip luong_phu_360p.mp4 --out data/hw_profile.json
    # máy có GPU NVIDIA: thêm --with onnxruntime-gpu (và tensorrt-cu12 nếu muốn TensorRT)

``--models`` chứa ONNX: yolo11s.onnx (hoặc yolo11s_640/960/1280.onnx), yolo11n_640.onnx,
det_10g.onnx, det_500m.onnx, w600k_r50.onnx, w600k_mbf.onnx. Thiếu file nào thì bỏ qua
tầng dùng file đó.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

INTERESTING_FLAGS = ["avx2", "avx512f", "avx512_vnni", "avx_vnni", "amx_int8", "amx_bf16", "amx_fp16"]

# Thang cấu hình từ tốt nhất tới rẻ nhất. Mỗi thành phần là (tên file, cỡ ảnh vào).
# person_fps: số lần phát hiện người mỗi giây khi vùng có chuyển động;
# face_det_per_s / face_rec_per_s: số lần tìm và nhận diện mặt mỗi giây (chỉ cho người
# chưa định danh). Tầng S, A chủ yếu dành cho máy có GPU. Lưu ý: cảnh góc rộng người
# nhỏ thì yolo11n mất phần lớn người (đo được chỉ còn 18% so với yolo11s@1280), nên
# tầng D chỉ dùng cho camera đặt gần.
TIERS = [
    {"name": "S", "person": ("yolo11s", 1280), "face_det": ("det_10g", 960), "face_rec": ("w600k_r50", 112),
     "person_fps": 10, "face_det_per_s": 4, "face_rec_per_s": 4},
    {"name": "A", "person": ("yolo11s", 960), "face_det": ("det_10g", 640), "face_rec": ("w600k_r50", 112),
     "person_fps": 5, "face_det_per_s": 2, "face_rec_per_s": 2},
    {"name": "B", "person": ("yolo11s", 640), "face_det": ("det_10g", 640), "face_rec": ("w600k_mbf", 112),
     "person_fps": 5, "face_det_per_s": 2, "face_rec_per_s": 2},
    {"name": "C", "person": ("yolo11s", 640), "face_det": ("det_500m", 640), "face_rec": ("w600k_mbf", 112),
     "person_fps": 3, "face_det_per_s": 2, "face_rec_per_s": 2},
    {"name": "D (camera gần)", "person": ("yolo11n", 640), "face_det": ("det_500m", 640),
     "face_rec": ("w600k_mbf", 112), "person_fps": 3, "face_det_per_s": 1, "face_rec_per_s": 1},
]
IDLE_FPS = 1.0            # vùng không có chuyển động: chỉ kiểm 1 lần/giây
RESERVED_CORES = 1.5      # API, SSE, go2rtc, hệ điều hành
TARGET_UTIL = 0.7         # chừa 30% cho lúc đông người / đột biến
GPU_PREP_CPU_MS = 3.0     # CPU vẫn phải resize/chuẩn hoá ảnh và hậu xử lý khi model chạy trên GPU


# ----------------------------------------------------------------------------- dò thiết bị

def cpu_info():
    flags, model, phys, cur = set(), platform.processor(), set(), {}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if ":" not in line:
                if cur.get("physical id") is not None:
                    phys.add((cur.get("physical id"), cur.get("core id")))
                cur = {}
                continue
            k, v = [s.strip() for s in line.split(":", 1)]
            cur[k] = v
            if k == "model name":
                model = v
            elif k == "flags":
                flags = set(v.split())
    except OSError:
        pass
    logical = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return {
        "model": model,
        "logical_cpus": logical,
        # Bị giới hạn bởi taskset/cgroup thì chỉ tính số nhân được phép dùng
        "physical_cores": min(len(phys) or logical, logical),
        "flags": [f for f in INTERESTING_FLAGS if f in flags],
        "int8_accel": any(f in flags for f in ("avx512_vnni", "avx_vnni", "amx_int8")),
    }


def nvidia_info():
    if not shutil.which("nvidia-smi"):
        return []
    r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                        "--format=csv,noheader,nounits"], capture_output=True, text=True)
    gpus = []
    for line in r.stdout.strip().splitlines():
        name, total, used, drv = [s.strip() for s in line.split(",")]
        gpus.append({"name": name, "vram_mb": int(total), "vram_used_mb": int(used), "driver": drv})
    return gpus


def intel_gpu_devices(core):
    out = []
    for d in core.available_devices:
        if d.startswith("GPU"):
            name = core.get_property(d, "FULL_DEVICE_NAME")
            if "intel" in name.lower():          # OpenVINO GPU plugin chỉ hỗ trợ GPU Intel
                out.append({"device": d, "name": name})
    return out


def ffmpeg_caps():
    caps = {"hwaccels": [], "encoders": []}
    if shutil.which("ffmpeg"):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-hwaccels"], capture_output=True, text=True)
        caps["hwaccels"] = [l.strip() for l in r.stdout.splitlines()[1:] if l.strip()]
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
        caps["encoders"] = [w for w in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_vaapi", "libx264")
                            if w in r.stdout]
    return caps


def ort_providers():
    try:
        import onnxruntime as ort
        return ort.get_available_providers()
    except ImportError:
        return []


# ----------------------------------------------------------------------------- đo model

def _time(fn, n):
    for _ in range(5):
        fn()
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    return float(np.median(ts))


def bench_ov(core, path, size, device, threads=None, n=40):
    m = core.read_model(str(path))
    m.reshape([1, 3, size, size])           # IR INT8 lượng tử ở cỡ khác vẫn đổi cỡ được
    cfg = {"PERFORMANCE_HINT": "LATENCY"}
    if device == "CPU":
        cfg["INFERENCE_NUM_THREADS"] = threads
    else:
        cfg["INFERENCE_PRECISION_HINT"] = "f16"
    req = core.compile_model(m, device, cfg).create_infer_request()
    x = np.random.rand(1, 3, size, size).astype(np.float32)
    return _time(lambda: req.infer({0: x}), n)


def bench_ort(path, size, provider, threads=None, n=40, cache_dir=None):
    import onnxruntime as ort
    so = ort.SessionOptions()
    if threads:
        so.intra_op_num_threads = threads
    if provider == "TensorrtExecutionProvider":
        providers = [(provider, {"trt_fp16_enable": True, "trt_engine_cache_enable": True,
                                 "trt_engine_cache_path": str(cache_dir)}), "CUDAExecutionProvider"]
    else:
        providers = [provider]
    sess = ort.InferenceSession(str(path), so, providers=providers)
    inp = sess.get_inputs()[0]
    # ONNX có trục động (SCRFD) thì đo đúng cỡ yêu cầu; ONNX đã cố định cỡ thì dùng cỡ đó
    shape = [d if isinstance(d, int) else s for d, s in zip(inp.shape, (1, 3, size, size))]
    x = np.random.rand(*shape).astype(np.float32)
    return _time(lambda: sess.run(None, {inp.name: x}), n)


def model_file(models, name, size, suffix=".onnx"):
    for cand in (f"{name}_{size}{suffix}", f"{name}{suffix}"):
        if (models / cand).exists():
            return models / cand
    return None


def bench_decode(clip, hwaccel=None):
    """CPU-giây để giải mã 1 giây video, tức số nhân CPU cần cho 1 camera."""
    if not clip or not shutil.which("ffmpeg"):
        return None
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", clip],
                               capture_output=True, text=True).stdout.strip() or 0)
    cmd = ["ffmpeg", "-benchmark", "-threads", "1"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    r = subprocess.run(cmd + ["-i", clip, "-pix_fmt", "bgr24", "-f", "null", "-"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    for line in r.stderr.splitlines():
        if "utime=" in line and dur:
            return round(float(line.split("utime=")[1].split("s")[0]) / dur, 3)
    return None


# ----------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, type=Path)
    ap.add_argument("--cameras", type=int, default=0, help="số camera cần chạy (0 = chỉ báo số tối đa)")
    ap.add_argument("--clip", help="clip mẫu luồng AI (vd 360p/720p của camera thật) để đo giải mã")
    ap.add_argument("--threads", default="", help="số luồng CPU thử, mặc định 1,2,4")
    ap.add_argument("--near", action="store_true",
                    help="camera đặt gần (người cao >150 px): cho phép tầng D dùng yolo11n")
    ap.add_argument("--duty", type=float, default=1.0,
                    help="tỉ lệ thời gian vùng có chuyển động (1.0 = tệ nhất; thao trường thường 0.2-0.4)")
    ap.add_argument("--cpu-only", action="store_true", help="bỏ qua GPU dù có")
    ap.add_argument("--out", type=Path, default=Path("hw_profile.json"))
    a = ap.parse_args()

    import openvino as ov
    core = ov.Core()
    cpu = cpu_info()
    nv = [] if a.cpu_only else nvidia_info()
    igpu = [] if a.cpu_only else intel_gpu_devices(core)
    providers = ort_providers()
    ff = ffmpeg_caps()
    threads = [int(t) for t in a.threads.split(",")] if a.threads else [t for t in (1, 2, 4) if t <= cpu["physical_cores"]]

    backends = ["cpu-ov"] + (["cpu-ort"] if "CPUExecutionProvider" in providers else [])
    if nv and "CUDAExecutionProvider" in providers:
        backends.append("nv-cuda")
    if nv and "TensorrtExecutionProvider" in providers:
        backends.append("nv-trt")
    if igpu:
        backends.append("intel-gpu")

    signature = hashlib.sha1(json.dumps([cpu["model"], cpu["physical_cores"], [g["name"] for g in nv],
                                         [g["name"] for g in igpu]]).encode()).hexdigest()[:12]

    # Đo mọi (model, cỡ) cần cho các tầng trên mọi backend có sẵn.
    # Khoá: "tên@cỡ|backend|độ chính xác|số luồng"; luồng = 0 với GPU.
    needed = sorted({tier[k] for tier in TIERS for k in ("person", "face_det", "face_rec")})
    lat = {}
    trt_cache = a.out.parent / "trt_cache"
    for name, size in needed:
        onnx = model_file(a.models, name, size)
        int8 = model_file(a.models, name, size, "_int8.xml")
        if onnx is None:
            continue
        runs = []
        for t in threads:
            runs.append(("cpu-ov", "fp32", t, lambda t=t: bench_ov(core, onnx, size, "CPU", t)))
            if int8:
                runs.append(("cpu-ov", "int8", t, lambda t=t: bench_ov(core, int8, size, "CPU", t)))
            if "cpu-ort" in backends:
                runs.append(("cpu-ort", "fp32", t, lambda t=t: bench_ort(onnx, size, "CPUExecutionProvider", t)))
        if "nv-cuda" in backends:
            runs.append(("nv-cuda", "fp32", 0, lambda: bench_ort(onnx, size, "CUDAExecutionProvider")))
        if "nv-trt" in backends:
            trt_cache.mkdir(parents=True, exist_ok=True)
            runs.append(("nv-trt", "fp16", 0, lambda: bench_ort(onnx, size, "TensorrtExecutionProvider",
                                                                cache_dir=trt_cache)))
        if igpu:
            runs.append(("intel-gpu", "fp16", 0, lambda: bench_ov(core, onnx, size, igpu[0]["device"])))
        for backend, prec, t, fn in runs:
            try:
                ms = fn()
            except Exception as e:                  # một backend hỏng không được làm hỏng cả lần đo
                print(f"{name}@{size} {backend}: lỗi {str(e).splitlines()[0][:120]}", flush=True)
                continue
            lat[f"{name}@{size}|{backend}|{prec}|{t}"] = round(ms, 2)
            print(f"{name}@{size:<5} {backend:9s} {prec:4s} {t or '-'} luồng: {ms:8.1f} ms", flush=True)

    def options(name, size):
        """Các cách chạy một thành phần: (CPU-ms, GPU-ms, độ trễ, khoá) cho mỗi lần chạy."""
        out = []
        for k, ms in lat.items():
            mk, backend, _prec, t = k.split("|")
            if mk != f"{name}@{size}":
                continue
            if backend.startswith("cpu"):
                out.append((ms * int(t), 0.0, ms, k))
            else:
                out.append((GPU_PREP_CPU_MS, ms, ms, k))
        return out

    hw_decode = None
    if not a.cpu_only:
        hw_decode = next((h for h in ("cuda", "qsv", "vaapi") if h in ff["hwaccels"]
                          and ((h == "cuda" and nv) or (h != "cuda" and igpu))), None)
    decode_sw = bench_decode(a.clip)
    decode_hw = bench_decode(a.clip, hw_decode) if hw_decode else None
    measured = [x for x in (decode_sw, decode_hw) if x is not None]
    decode_cores = min(measured) if measured else 0.05      # không có clip: giả định luồng phụ 360p

    cpu_budget = max(0.5, (cpu["physical_cores"] - RESERVED_CORES) * TARGET_UTIL) * 1000   # CPU-ms mỗi giây
    gpu_budget = 1000 * TARGET_UTIL if (nv or igpu) else 0.0                               # GPU-ms mỗi giây

    tiers_out = []
    for tier in TIERS:
        opts = {k: options(*tier[k]) for k in ("person", "face_det", "face_rec")}
        if not all(opts.values()):
            continue
        rate = {"person": a.duty * tier["person_fps"] + (1 - a.duty) * IDLE_FPS,
                "face_det": a.duty * tier["face_det_per_s"], "face_rec": a.duty * tier["face_rec_per_s"]}
        best = None
        # Thử mọi cách gán thành phần vào backend, giữ cách cho nhiều camera nhất
        for p in opts["person"]:
            for d in opts["face_det"]:
                for r in opts["face_rec"]:
                    chosen = {"person": p, "face_det": d, "face_rec": r}
                    cpu_ms = sum(chosen[k][0] * rate[k] for k in chosen) + decode_cores * 1000
                    gpu_ms = sum(chosen[k][1] * rate[k] for k in chosen)
                    if gpu_ms and not gpu_budget:
                        continue
                    cams = int(min(cpu_budget / cpu_ms, gpu_budget / gpu_ms if gpu_ms else float("inf")))
                    if best is None or cams > best[0]:
                        best = (cams, chosen, cpu_ms, gpu_ms)
        cams, chosen, cpu_ms, gpu_ms = best
        tiers_out.append({
            "tier": tier["name"],
            "run": {k: chosen[k][3] for k in chosen},
            "person_fps": tier["person_fps"],
            "cpu_cores_per_camera": round(cpu_ms / 1000, 2),
            "gpu_util_per_camera": round(gpu_ms / 1000, 3),
            # Ước tính tuyến tính, là trần trên: nhiều camera còn tranh băng thông RAM / PCIe
            "max_cameras": cams,
            "person_latency_ms": chosen["person"][2],
        })

    usable = [t for t in tiers_out if a.near or not t["tier"].startswith("D")]
    pick = next((t for t in usable if t["max_cameras"] >= max(1, a.cameras)), usable[-1] if usable else None)

    profile = {
        "signature": signature, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "duty": a.duty,
        "cpu": cpu, "nvidia": nv, "intel_gpu": igpu, "ort_providers": providers, "ffmpeg": ff,
        "backends": backends, "decode": {"sw_cores": decode_sw, "hwaccel": hw_decode, "hw_cores": decode_hw},
        # Encoder phần cứng chỉ tính khi thật sự có thiết bị (ffmpeg build sẵn nvenc không có nghĩa là có GPU)
        "transcode_encoder": next((e for e, ok in (("h264_nvenc", bool(nv)), ("h264_qsv", bool(igpu)),
                                                   ("h264_vaapi", bool(igpu)), ("libx264", True))
                                   if ok and e in ff["encoders"]), None),
        "latency_ms": lat, "tiers": tiers_out, "recommended": pick,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))

    isa_cap = os.environ.get("ONEDNN_MAX_CPU_ISA")
    print("\nCPU:", cpu["model"], f"| {cpu['physical_cores']} nhân | flags: {', '.join(cpu['flags'])}"
          + (f" | đang giả lập ISA={isa_cap}" if isa_cap else ""))
    print("GPU NVIDIA:", [g["name"] for g in nv] or "không", "| GPU Intel:", [g["name"] for g in igpu] or "không")
    print("Backend đo được:", ", ".join(backends), "| giải mã HW:", hw_decode or "không",
          "| encoder chuyển mã:", profile["transcode_encoder"])
    for t in tiers_out:
        where = ", ".join(f"{k}={v.split('|')[1]}" for k, v in t["run"].items())
        print(f"{t['tier']}: {t['cpu_cores_per_camera']} nhân CPU + {t['gpu_util_per_camera']:.0%} GPU mỗi camera "
              f"→ tối đa {t['max_cameras']} camera ({where})")
    if pick:
        need = f" cho {a.cameras} camera" if a.cameras else ""
        warn = "" if pick["max_cameras"] >= max(1, a.cameras) else " — KHÔNG ĐỦ, cần giảm fps/camera hoặc thêm phần cứng"
        print(f"Đề xuất{need}: tầng {pick['tier']}{warn}")
    elif tiers_out:
        print("Không có tầng phù hợp: chỉ đo được tầng D (yolo11n), mà tầng này chỉ dùng khi camera đặt gần "
              "(thêm --near). Hãy bổ sung yolo11s vào --models.")
    else:
        print("Không đủ file model để tính tầng nào, kiểm tra lại --models.")
    print("Chữ ký phần cứng:", signature, "| đã ghi", a.out)


if __name__ == "__main__":
    main()
