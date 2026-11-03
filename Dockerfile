# HORUS AI — service điểm danh & giám sát huấn luyện.
# Mặc định build bản CPU (máy đích không có GPU). Bản GPU: TORCH_VARIANT=cu128,
# xem docker-compose.gpu.yml.
ARG TORCH_VARIANT=cpu

FROM python:3.10-slim AS build
ARG TORCH_VARIANT
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv
# insightface phải biên dịch phần mở rộng C khi cài; libgl1, libglib2.0-0 để
# import được cv2 lúc tải model
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml uv.lock ./
# torch lấy từ index của PyTorch theo biến thể (cpu / cu128), còn lại đúng phiên
# bản trong uv.lock. Bỏ torch và gói CUDA khỏi danh sách để bản CPU không kéo
# theo vài GB thư viện nvidia. lap cài thêm vì ultralytics tự cài nó lúc chạy
# tracker lần đầu, máy không có internet sẽ hỏng.
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o req.txt \
 && grep -vE '^(torch|torchvision|triton|nvidia-)' req.txt > req-app.txt \
 && python -m venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python \
      --index-url https://download.pytorch.org/whl/${TORCH_VARIANT} \
      torch==2.9.1 torchvision==0.24.1 \
 && uv pip install --python /opt/venv/bin/python -r req-app.txt lap==0.5.13

# Bản GPU: InsightFace chạy CUDA qua onnxruntime-gpu. Cùng tên import "onnxruntime"
# với bản CPU đã cài ở req-app.txt (cùng tệp, cài chồng sẽ lẫn lộn bản) nên phải gỡ
# bản CPU trước khi cài bản GPU. Bản CPU (TORCH_VARIANT=cpu) thì bỏ qua bước này.
RUN if [ "$TORCH_VARIANT" != "cpu" ]; then \
      /opt/venv/bin/python -m pip uninstall -y onnxruntime \
      && uv pip install --python /opt/venv/bin/python "onnxruntime-gpu>=1.20,<1.24"; \
    fi

# Tải sẵn model vào image để máy đích chạy được không cần internet
WORKDIR /build/models
RUN /opt/venv/bin/python -c "from ultralytics import YOLO; YOLO('yolo11s.pt')" \
 && /opt/venv/bin/python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])" \
 && rm -f /root/.insightface/models/*.zip

# Bản CPU: xuất sẵn OpenVINO cho yolo11s.pt lúc build (app/detector.py tự dùng khi
# AI_BACKEND=auto trên CPU) — nhanh hơn .pt hẳn trên CPU không có GPU (đo tại
# docs/review). Bản GPU chạy .pt thẳng trên CUDA nên không cần, nhưng vẫn tạo thư
# mục rỗng để bước COPY ở stage sau không phải phân nhánh theo TORCH_VARIANT.
RUN if [ "$TORCH_VARIANT" = "cpu" ]; then \
      uv pip install --python /opt/venv/bin/python "openvino>=2025.0" \
      && /opt/venv/bin/python -c "from ultralytics import YOLO; YOLO('yolo11s.pt').export(format='openvino', imgsz=960)"; \
    else \
      mkdir -p yolo11s_openvino_model; \
    fi

FROM python:3.10-slim
# libgl1, libglib2.0-0: opencv-python; fonts-dejavu-core: chữ tiếng Việt vẽ lên ảnh;
# ffmpeg: dựng clip H.264 cho sự kiện (app/events.py)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 fonts-dejavu-core ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv
COPY --from=build /root/.insightface /root/.insightface
# Bản GPU: libcudnn/libcublas nằm trong wheel nvidia-* của torch, không ở vị trí hệ
# thống — onnxruntime-gpu (InsightFace) cần thấy để dùng CUDAExecutionProvider.
# app/hw.py gọi onnxruntime.preload_dlls() trước; biến LD_LIBRARY_PATH dưới đây là
# lưới an toàn thứ hai cho bản onnxruntime-gpu cũ không có preload_dlls(). Bản CPU
# không có các thư mục nvidia/* này — linker bỏ qua phần không tồn tại, không lỗi.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/opt/venv/lib/python3.10/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.10/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.10/site-packages/nvidia/cuda_runtime/lib

WORKDIR /app
COPY --from=build /build/models/yolo11s.pt ./
COPY --from=build /build/models/yolo11s_openvino_model ./yolo11s_openvino_model
COPY main.py ./
COPY app ./app
COPY static ./static
COPY scripts ./scripts

EXPOSE 8199
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8199/api/v1/system/health', timeout=4)"
CMD ["python", "main.py"]
