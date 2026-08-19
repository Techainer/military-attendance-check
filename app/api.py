"""FastAPI backend with routes, Face Registration endpoints, and WebSocket streaming."""

from fastapi import FastAPI, WebSocket, UploadFile, File, Form, WebSocketDisconnect, BackgroundTasks, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
import shutil
import os
import json
import cv2
import numpy as np
import base64
from pathlib import Path
from typing import Optional, List

from app.video_processor import VideoProcessor
from app.monitor import AttendanceMonitor
from app.face_engine import FaceEngine


# Initialize FastAPI app
app = FastAPI(title="HORUS AI - Military Attendance & Face ID")

# Paths
static_path = Path(__file__).parent.parent / "static"
alerts_path = Path(__file__).parent.parent / "alerts"
data_path = Path(__file__).parent.parent / "data"
avatars_path = data_path / "face_avatars"

# Ensure directories
alerts_path.mkdir(exist_ok=True)
data_path.mkdir(exist_ok=True)
avatars_path.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
app.mount("/alerts", StaticFiles(directory=str(alerts_path)), name="alerts")
app.mount("/data/face_avatars", StaticFiles(directory=str(avatars_path)), name="face_avatars")

# Global instances
monitor = AttendanceMonitor(alerts_dir=str(alerts_path))
face_engine = FaceEngine(data_dir=str(data_path))

current_video_path = None
active_connections: List[WebSocket] = []
is_processing = False
last_frame_data = None
current_processor = None


async def broadcast_update(data: dict):
    """Broadcast update to all connected WebSocket clients."""
    global is_processing, last_frame_data
    if "is_processing" in data:
        is_processing = data["is_processing"]
        
    if data.get("type") == "frame_update":
        last_frame_data = data
        
    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(data)
        except Exception:
            disconnected.append(connection)
    
    for connection in disconnected:
        if connection in active_connections:
            active_connections.remove(connection)


async def _background_process_video(video_path: str):
    """Background task processing video with YOLO & InsightFace."""
    global is_processing, last_frame_data, current_processor
    is_processing = True
    last_frame_data = None
    current_processor = VideoProcessor(fps=5, face_engine=face_engine)
    await current_processor.process_video(video_path, broadcast_update, monitor)
    is_processing = False
    last_frame_data = None
    current_processor = None


# ----------------- Navigation & Static Routes -----------------

@app.get("/")
async def root():
    """Serve the main Horus AI web page."""
    index_file = static_path / "index.html"
    return FileResponse(index_file)


# ----------------- Face Registration Endpoints -----------------

@app.get("/api/faces")
async def list_registered_faces(
    q: Optional[str] = Query(None, description="Search query for name or military ID"),
    unit: Optional[str] = Query(None, description="Filter by military unit"),
    rank: Optional[str] = Query(None, description="Filter by rank")
):
    """Get list of registered military personnel."""
    faces = face_engine.get_registered_faces(query=q, unit=unit, rank=rank)
    return {
        "status": "success",
        "total": len(faces),
        "data": faces
    }


@app.post("/api/faces/register")
async def register_face(
    name: str = Form(...),
    military_id: str = Form(...),
    rank: str = Form(...),
    unit: str = Form(...),
    status: str = Form("Active"),
    image: Optional[UploadFile] = File(None),
    image_base64: Optional[str] = Form(None)
):
    """
    Register military personnel with biometric face embedding.
    Accepts either an uploaded image file or a base64 webcam snapshot.
    """
    frame = None

    if image is not None:
        contents = await image.read()
        nparr = np.frombuffer(contents, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    elif image_base64:
        # Base64 string from webcam
        if "," in image_base64:
            image_base64 = image_base64.split(",")[1]
        decoded = base64.b64decode(image_base64)
        nparr = np.frombuffer(decoded, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if frame is None:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp ảnh hợp lệ (tải file hoặc chụp từ webcam).")

    try:
        registered_person = face_engine.register_person(
            name=name,
            military_id=military_id,
            rank=rank,
            unit=unit,
            image=frame,
            status=status
        )
        return {
            "status": "success",
            "message": f"Đăng ký Face ID thành công cho {rank} {name} ({military_id})",
            "data": {k: v for k, v in registered_person.items() if k != "embedding"}
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/faces/{person_id}")
async def delete_registered_face(person_id: str):
    """Delete a registered person by ID or military ID."""
    success = face_engine.delete_person(person_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy quân nhân để xóa")
    return {
        "status": "success",
        "message": "Đã xóa quân nhân khỏi danh sách Face ID"
    }


@app.put("/api/faces/{person_id}")
async def update_registered_face(
    person_id: str,
    name: Optional[str] = Form(None),
    military_id: Optional[str] = Form(None),
    rank: Optional[str] = Form(None),
    unit: Optional[str] = Form(None),
    status: Optional[str] = Form(None)
):
    """Update metadata for registered personnel."""
    updates = {}
    if name is not None: updates["name"] = name
    if military_id is not None: updates["military_id"] = military_id
    if rank is not None: updates["rank"] = rank
    if unit is not None: updates["unit"] = unit
    if status is not None: updates["status"] = status

    updated = face_engine.update_person(person_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy quân nhân để cập nhật")
    return {
        "status": "success",
        "message": "Cập nhật thông tin thành công",
        "data": updated
    }


# ----------------- Zone & ROI Rules Endpoints -----------------

@app.get("/api/zones")
async def get_zone_rules():
    """Get configured ROI polygon and tripwire rules."""
    zone_file = data_path / "zone_rules.json"
    if zone_file.exists():
        try:
            with open(zone_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading zone rules: {e}")
    
    return {
        "zone_name": "Khu vực tập trung",
        "rule_type": "Cảnh báo Xâm nhập 24/7",
        "detect_human": True,
        "detect_object": True,
        "polygon_points": [
            {"x": 0.08, "y": 0.75},
            {"x": 0.35, "y": 0.50},
            {"x": 0.70, "y": 0.56},
            {"x": 0.92, "y": 0.85},
            {"x": 0.12, "y": 0.90}
        ],
        "tripwire_points": [
            {"x": 0.10, "y": 0.45},
            {"x": 0.90, "y": 0.40}
        ]
    }


@app.post("/api/zones")
async def save_zone_rules(config: dict):
    """Save configured ROI polygon and tripwire rules."""
    zone_file = data_path / "zone_rules.json"
    try:
        with open(zone_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return {
            "status": "success",
            "message": "Đã lưu cấu hình Vùng & Luật (F-06) thành công!",
            "data": config
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi lưu cấu hình: {str(e)}")


@app.get("/api/snapshot")
async def get_current_snapshot():
    """Retrieve current/latest frame from active stream or video file for ROI drawing background."""
    global last_frame_data, current_video_path

    if last_frame_data and "frame" in last_frame_data:
        return {
            "status": "success",
            "frame": last_frame_data["frame"],
            "source": "live_stream"
        }

    if current_video_path and os.path.exists(current_video_path):
        try:
            cap = cv2.VideoCapture(current_video_path)
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')
                return {
                    "status": "success",
                    "frame": frame_b64,
                    "source": "video_file"
                }
        except Exception as e:
            print(f"Error capturing frame: {e}")

@app.get("/api/events/clip")
async def get_event_clip(clip_id: Optional[str] = None):
    """Retrieve 10-second replay frame buffer for tactical event playback."""
    from app.video_processor import global_clip_buffer, latest_event_clips
    
    frames = []
    if clip_id and clip_id in latest_event_clips:
        frames = latest_event_clips[clip_id]
    elif len(global_clip_buffer) > 0:
        frames = list(global_clip_buffer)
    elif last_frame_data and "frame" in last_frame_data:
        frames = [last_frame_data["frame"]]
        
    return {
        "status": "success",
        "clip_id": clip_id or "latest",
        "total_frames": len(frames),
        "fps": 5,
        "frames": frames
    }


# ----------------- Schedules & Attendance Logs Endpoints -----------------

@app.get("/api/schedules")
async def get_schedules():
    """Get list of military shift schedules."""
    sch_file = data_path / "schedules.json"
    if sch_file.exists():
        try:
            with open(sch_file, "r", encoding="utf-8") as f:
                return {"status": "success", "data": json.load(f)}
        except Exception as e:
            print(f"Error loading schedules: {e}")
    return {"status": "success", "data": []}


@app.post("/api/schedules")
async def create_schedule(schedule_data: dict):
    """Create a new military shift schedule."""
    sch_file = data_path / "schedules.json"
    schedules = []
    if sch_file.exists():
        try:
            with open(sch_file, "r", encoding="utf-8") as f:
                schedules = json.load(f)
        except Exception:
            schedules = []
            
    schedule_data["id"] = f"sch_{int(time.time() * 1000)}"
    schedule_data["status"] = "Active"
    schedules.append(schedule_data)
    
    with open(sch_file, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)
        
    return {"status": "success", "message": "Đã thêm ca thời khóa biểu mới!", "data": schedule_data}


@app.delete("/api/schedules/{sch_id}")
async def delete_schedule(sch_id: str):
    """Delete a schedule by ID."""
    sch_file = data_path / "schedules.json"
    if not sch_file.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy thời khóa biểu")
        
    with open(sch_file, "r", encoding="utf-8") as f:
        schedules = json.load(f)
        
    schedules = [s for s in schedules if s.get("id") != sch_id]
    
    with open(sch_file, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)
        
    return {"status": "success", "message": "Đã xóa ca thời khóa biểu"}


@app.get("/api/attendance-logs")
async def get_attendance_logs(unit: Optional[str] = None):
    """Get history of attendance roll-call logs."""
    log_file = data_path / "attendance_logs.json"
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
                if unit and unit != "all" and unit != "Tất cả đơn vị":
                    logs = [l for l in logs if l.get("unit") == unit]
                return {"status": "success", "data": logs}
        except Exception as e:
            print(f"Error loading attendance logs: {e}")
    return {"status": "success", "data": []}


# ----------------- Video & Stream Controls -----------------

@app.post("/api/start")
async def start_processing(
    background_tasks: BackgroundTasks,
    mode: str = "video",
    rtsp_url: Optional[str] = None
):
    """Start video or RTSP stream processing in the background."""
    global current_video_path, is_processing
    
    if mode == "rtsp":
        if not rtsp_url:
            return {"status": "error", "message": "RTSP URL is required"}
        current_video_path = rtsp_url
    else:
        if current_video_path is None:
            return {"status": "error", "message": "No video uploaded"}
            
        if "://" in current_video_path or not os.path.exists(current_video_path):
            return {"status": "error", "message": "Video file not found. Please upload again."}
        
    if is_processing:
        return {"status": "success", "message": "Already processing"}

    background_tasks.add_task(_background_process_video, current_video_path)
    
    return {
        "status": "success", 
        "message": "Processing started in background",
        "mode": mode,
        "video_path": current_video_path
    }


@app.post("/api/stop")
async def stop_processing():
    """Stop the current video or RTSP processing."""
    global current_processor, is_processing
    if not is_processing or current_processor is None:
        return {"status": "success", "message": "Not processing"}
        
    current_processor.stop()
    return {
        "status": "success",
        "message": "Stop request submitted"
    }


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload a video file for processing."""
    global current_video_path

    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)

    filepath = uploads_dir / file.filename

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    current_video_path = str(filepath)
    print(f"Video uploaded: {filepath}")

    return {
        "status": "success",
        "message": "Video uploaded successfully",
        "video_path": str(filepath),
        "filename": file.filename
    }


@app.post("/api/upload_chunk")
async def upload_chunk(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    upload_id: str = Form(...),
    filename: str = Form(...)
):
    """Handle chunked file upload for large video files."""
    global current_video_path

    temp_dir = Path("uploads/temp") / upload_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    chunk_path = temp_dir / f"chunk_{chunk_index}"
    
    with open(chunk_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    received_chunks = len(list(temp_dir.glob("chunk_*")))
    
    if received_chunks == total_chunks:
        uploads_dir = Path("uploads")
        uploads_dir.mkdir(exist_ok=True)
        final_path = uploads_dir / filename
        
        with open(final_path, "wb") as final_file:
            for i in range(total_chunks):
                chunk_p = temp_dir / f"chunk_{i}"
                if not chunk_p.exists():
                     return {
                        "status": "error",
                        "message": f"Missing chunk {i}"
                    }
                with open(chunk_p, "rb") as chunk_f:
                    shutil.copyfileobj(chunk_f, final_file)
        
        shutil.rmtree(temp_dir)
        
        current_video_path = str(final_path)
        print(f"Video fully uploaded: {final_path}")
        
        background_tasks.add_task(_background_process_video, current_video_path)
        
        return {
            "status": "success",
            "message": "Upload complete, processing started",
            "video_path": str(final_path),
            "filename": filename
        }

    return {
        "status": "partial",
        "message": f"Chunk {chunk_index} received",
        "chunk_index": chunk_index
    }


@app.post("/api/set-baseline")
async def set_baseline(count: int):
    """Set the expected baseline count."""
    monitor.set_baseline(count)
    return {
        "status": "success",
        "baseline": count,
        "message": f"Baseline set to {count} persons"
    }


@app.get("/api/status")
async def get_status():
    """Get system monitoring status and registered stats."""
    status = monitor.get_status()
    input_mode = "rtsp" if current_video_path and "://" in current_video_path else "video"
    total_registered = len(face_engine.registered_faces)
    return {
        "status": "success",
        **status,
        "video_path": current_video_path,
        "is_processing": is_processing,
        "input_mode": input_mode,
        "total_registered": total_registered
    }


@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    """Get alert history."""
    alerts = monitor.get_recent_alerts(limit)
    return {
        "status": "success",
        "alerts": alerts
    }


# ----------------- WebSocket Streaming -----------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for live frames, recognitions, and alert events."""
    await websocket.accept()
    active_connections.append(websocket)
    print("WebSocket connection established")

    try:
        if is_processing:
            await websocket.send_json({
                "type": "status",
                "message": "Processing in progress",
                "is_processing": True
            })
            if last_frame_data:
                await websocket.send_json(last_frame_data)
                 
            recent_alerts = monitor.get_recent_alerts(20)
            for alert in reversed(recent_alerts):
                await websocket.send_json({
                    "type": "alert",
                    **alert
                })
        
        while True:
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        print("WebSocket disconnected")
        if websocket in active_connections:
            active_connections.remove(websocket)
    except Exception as e:
        print(f"Error in WebSocket: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)
