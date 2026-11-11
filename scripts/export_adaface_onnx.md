# Xuất AdaFace IR-101 (WebFace12M) ra ONNX

Dùng cho `scripts/bench_face_models.py --adaface PATH` và `FACE_REC_MODEL` (xem
`app/face_engine.py`). AdaFace không có sẵn trong insightface model zoo nên
phải tự tải checkpoint PyTorch gốc rồi xuất ONNX một lần.

## 1. Tải checkpoint

Checkpoint chính thức (tác giả AdaFace, repo `mk-minchul/AdaFace`):

- IR-101, train trên WebFace12M: `adaface_ir101_webface12m.ckpt`
- Link tải nằm trong README của repo gốc (Google Drive — địa chỉ thay đổi theo
  thời điểm, xem `https://github.com/mk-minchul/AdaFace` mục "Pretrained Models").

```bash
git clone https://github.com/mk-minchul/AdaFace.git
cd AdaFace
# đặt checkpoint đã tải vào pretrained/adaface_ir101_webface12m.ckpt
```

## 2. Xuất ONNX

Checkpoint lưu dạng `state_dict` của backbone IR-101 (`net.py` trong repo gốc).
Script xuất tối giản (chạy trong venv đã cài `torch` + repo AdaFace trên
`PYTHONPATH`):

```python
# export_onnx.py — chạy trong thư mục gốc repo AdaFace đã clone ở bước 1
import torch
from net import build_model  # định nghĩa trong AdaFace/net.py

model = build_model('ir_101')
statedict = torch.load('pretrained/adaface_ir101_webface12m.ckpt', map_location='cpu')['state_dict']
model_statedict = {k[6:]: v for k, v in statedict.items() if k.startswith('model.')}
model.load_state_dict(model_statedict)
model.eval()

dummy = torch.randn(1, 3, 112, 112)  # BGR, (x/255-0.5)/0.5 — xem bước 3
torch.onnx.export(
    model, dummy, "adaface_ir101_webface12m.onnx",
    input_names=["input"], output_names=["embedding"],
    dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
    opset_version=12,
)
```

Chạy: `python export_onnx.py` -> sinh `adaface_ir101_webface12m.onnx`.

## 3. Tiền xử lý PHẢI khớp (quan trọng)

AdaFace train với ảnh **BGR** (không đổi sang RGB như ArcFace), chuẩn hoá
`(pixel/255 - 0.5) / 0.5` (tương đương `(pixel - 127.5) / 127.5`), input
112x112, layout NCHW. `app/face_engine._preprocess_for_model(crop, "adaface")`
và `scripts/bench_face_models.py` (preprocess `"adaface"`) đã làm đúng công
thức này — không cần sửa gì thêm, chỉ cần trỏ đúng đường dẫn ONNX.

Dùng sai tiền xử lý (vd. lỡ đổi RGB hoặc chuẩn hoá kiểu ArcFace với mean/std
khác) sẽ KHÔNG lỗi khi chạy (ONNX vẫn ra một vector 512 chiều) nhưng độ chính
xác sẽ tụt rất mạnh — nếu bench cho AdaFace thấp bất thường, kiểm tra lại bước
này trước tiên.

## 4. Kiểm tra nhanh

```bash
uv run python scripts/bench_face_models.py --data data --adaface adaface_ir101_webface12m.onnx --device cuda
```

Bảng markdown in ra sẽ có dòng `adaface_ir101` cạnh `buffalo_l`/`antelopev2` để so sánh.
