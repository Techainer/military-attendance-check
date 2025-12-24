# Military Attendance Check System

A real-time student attendance monitoring system using YOLO person detection. The system tracks the number of people in a video and alerts when attendance drops below the baseline for more than 1 minute.

## Features

- **YOLO v8 Person Detection**: Fast and accurate person detection using YOLOv8n
- **Real-time Monitoring**: Track person count continuously throughout video playback
- **Baseline Setting**: Manually set the expected attendance count
- **Alert System**: Automatic alerts when count drops below baseline for >60 seconds
- **Frame Capture**: Saves frames when alerts are triggered
- **Web UI**: Simple, clean HTML/CSS/JS interface with live video feed
- **Event Log**: Historical record of all attendance alerts

## Architecture

```
User uploads video → FastAPI backend → Video processing pipeline:
  1. YOLO detects persons → count + bounding boxes
  2. Monitor checks if count < baseline for >60s
  3. WebSocket streams frames + alerts to UI
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
