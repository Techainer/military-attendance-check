# Military Attendance Check System

A real-time student attendance monitoring system using YOLO person detection. The system tracks the number of people in a video and alerts when attendance drops below the baseline for more than 1 minute.

## Features

- **YOLO Person Detection + Face ID**: hai model chạy song song trên cùng khung hình
- **Điểm danh theo thời khoá biểu**: tự mở phiên N phút đầu giờ và cuối giờ, lưu ảnh bằng chứng từng mốc
- **Vi phạm giờ giấc**: suy ra Đi chậm / Về sớm / Không tham gia từ dấu vết hiện diện cả buổi (`app/presence.py`)
- **Giám sát an toàn**: phát hiện người vào vùng cấm hoặc vượt vạch an toàn (`app/safety.py`)
- **Kho sự kiện thống nhất**: mọi cảnh báo về một cấu trúc, có xác nhận xử lý và kênh SSE (`app/events.py`)
- **Web UI**: giao diện HTML/CSS/JS với luồng hình trực tiếp

## Architecture

```
Nguồn video / RTSP → FastAPI → Vòng xử lý mỗi khung hình:
  1. YOLO đếm người  +  InsightFace định danh   (song song)
  2. Lọc theo vùng điểm danh → sĩ số trong vùng
  3. Ghi dấu vết hiện diện từng quân nhân (cả buổi, không chỉ 2 mốc)
  4. Soi vùng cấm / vạch an toàn → sự kiện INTRUSION
  5. Chốt mốc điểm danh khi hết cửa sổ → biên bản + bảng vi phạm
  6. WebSocket đẩy khung hình · SSE đẩy sự kiện
```

## Hợp đồng API

`docs/api/openapi.yaml` và `docs/api/events.schema.json` là hợp đồng giao tiếp với
giao diện. Xem `docs/api/README.md` để biết endpoint nào đã chạy được.

## Kiểm thử

```bash
python tests/test_ai.py     # logic vi phạm giờ giấc, xâm nhập, kho sự kiện
python tests/test_api.py    # endpoint v1 + đối chiếu sự kiện với hợp đồng
```

## Requirements

- Python 3.10 or higher
- Webcam or video file for testing

## Installation

1. Install dependencies using `uv` (recommended) or `pip`:

```bash
# Using uv (faster)
uv pip install -e .

# Or using pip
pip install -e .
```

2. The YOLO model will be automatically downloaded on first run.

## Usage

### 1. Start the Server

```bash
python main.py
```

The server will start at `http://localhost:8000`

### 2. Open the Web Interface

Open your browser and navigate to:
```
http://localhost:8000
```

### 3. Upload and Process Video

1. **Upload Video**: Click "Choose File" and select a video file (mp4, avi, mov, etc.)
2. **Start Processing**: Click "Start Processing" to begin video analysis
3. **Set Baseline**: When all students are present in the frame, click "Set Baseline"
4. **Monitor**: Watch the live feed and wait for alerts if attendance drops

### 4. Understanding the Interface

**Control Panel (Left):**
- Upload video file
- Start/stop processing
- Set baseline count
- View current statistics

**Video Display (Right):**
- Live video feed with person detection boxes
- Person count overlay

**Event Log (Bottom):**
- Timestamp of each alert
- Alert message with count information
- Captured frame thumbnail

**Alert Banner (Top):**
- Appears when attendance drops below baseline for >60 seconds
- Shows alert message and timestamp

## How It Works

### Person Detection
- Uses YOLOv8n (nano) model for fast person detection
- Filters detections to only include "person" class
- Returns bounding boxes and confidence scores

### Attendance Monitoring
1. **Baseline Setting**: User manually sets the expected count when all students are present
2. **Continuous Tracking**: System processes video at 5 FPS and counts persons in each frame
3. **Alert Logic**:
   - If count < baseline: Start timer
   - If count remains below baseline for 60+ seconds: Trigger alert
   - If count recovers: Reset timer

### Alert Triggering
When an alert is triggered:
1. Current frame is captured and saved to `alerts/` directory
2. Alert event is sent to UI via WebSocket
3. Event is logged in the event log table
4. Alert banner is displayed

## Project Structure

```
military-attendance-check/
├── main.py                    # FastAPI entry point
├── pyproject.toml             # Dependencies
├── app/
│   ├── __init__.py
│   ├── api.py                 # FastAPI routes + WebSocket
│   ├── detector.py            # YOLO person detection
│   ├── monitor.py             # Alert logic
│   └── video_processor.py     # Video processing pipeline
├── static/
│   ├── index.html             # UI layout
│   ├── style.css              # Styling
│   └── app.js                 # WebSocket + UI logic
├── uploads/                   # Uploaded videos (gitignored)
└── alerts/                    # Captured alert frames (gitignored)
```

## Configuration

### Adjust Processing Speed

Edit `app/api.py` line 100:
```python
processor = VideoProcessor(fps=5)  # Change to 10 for faster, 3 for slower
```

### Adjust Alert Threshold

Edit `app/monitor.py` line 11:
```python
ALERT_THRESHOLD_SECONDS = 60  # Change to 30 for 30 seconds, etc.
```

### Change YOLO Model

Edit `app/detector.py` line 11:
```python
def __init__(self, model_name: str = 'yolov8n.pt'):  # Use yolov8s.pt for more accuracy
```

## Troubleshooting

### Video upload fails
- Check that the video file format is supported (mp4, avi, mov, mkv)
- Ensure the `uploads/` directory exists and has write permissions

### No detections / Count is 0
- Check video quality and lighting
- Ensure people are clearly visible in the frame
- Try using a larger YOLO model (yolov8s.pt or yolov8m.pt)

### Alerts not triggering
- Ensure baseline is set (click "Set Baseline" button)
- Verify count stays below baseline for at least 60 seconds
- Check console logs for errors

### WebSocket connection fails
- Ensure the server is running on port 8000
- Check browser console for connection errors
- Try accessing via `http://localhost:8000` instead of `127.0.0.1`

## Development

### Run in development mode

```bash
python main.py
```

### Install development dependencies

```bash
uv pip install -e ".[dev]"
```

## Demo Considerations

This system is designed as a **simple demo**. For production use, consider:

- Add user authentication
- Use a proper database for event storage
- Support multiple concurrent video streams
- Add real-time webcam support
- Implement proper state management
- Add video streaming instead of file upload
- Improve error handling and validation
- Add unit tests

## License

MIT

## Credits

Built with:
- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) - Person detection
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [OpenCV](https://opencv.org/) - Video processing
