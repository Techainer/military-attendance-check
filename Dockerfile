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

# Tải sẵn model vào image để máy đích chạy được không cần internet
WORKDIR /build/models
RUN /opt/venv/bin/python -c "from ultralytics import YOLO; YOLO('yolo11s.pt')" \
 && /opt/venv/bin/python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])" \
 && rm -f /root/.insightface/models/*.zip


FROM python:3.10-slim
# libgl1, libglib2.0-0: opencv-python; fonts-dejavu-core: chữ tiếng Việt vẽ lên ảnh;
# ffmpeg: dựng clip H.264 cho sự kiện (app/events.py)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 fonts-dejavu-core ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv
COPY --from=build /root/.insightface /root/.insightface
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --from=build /build/models/yolo11s.pt ./
COPY main.py ./
COPY app ./app
COPY static ./static
COPY scripts ./scripts

EXPOSE 8199
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8199/api/v1/system/health', timeout=4)"
CMD ["python", "main.py"]
