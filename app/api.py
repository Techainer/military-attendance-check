"""FastAPI backend with routes and WebSocket endpoint."""

from fastapi import FastAPI, WebSocket, UploadFile, File, WebSocketDisconnect, Form, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import shutil
import os
from pathlib import Path

from app.video_processor import VideoProcessor
from app.monitor import AttendanceMonitor


# Initialize FastAPI app
app = FastAPI(title="Military Attendance Check")

# Mount static files
static_path = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Mount alerts directory for serving captured images
alerts_path = Path(__file__).parent.parent / "alerts"
app.mount("/alerts", StaticFiles(directory=str(alerts_path)), name="alerts")

# Global state
monitor = AttendanceMonitor()
current_video_path = None
active_connections = []
current_background_task = None
is_processing = False
last_frame_data = None

async def broadcast_update(data: dict):
    """
    Broadcast update to all connected clients.
    """
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
    """
    Internal background task wrapper.
    """
    global is_processing, last_frame_data
    is_processing = True
    last_frame_data = None
    processor = VideoProcessor(fps=5)
    await processor.process_video(video_path, broadcast_update, monitor)
    is_processing = False
    last_frame_data = None

@app.post("/api/start")
async def start_processing(background_tasks: BackgroundTasks):
    """
    Start video processing in background.
    """
    global current_video_path, is_processing
    
    if current_video_path is None:
        return {"status": "error", "message": "No video uploaded"}
        
    if not os.path.exists(current_video_path):
        return {"status": "error", "message": "Video file not found"}
        
    if is_processing:
        return {"status": "success", "message": "Already processing"}

    background_tasks.add_task(_background_process_video, current_video_path)
    
    return {
        "status": "success", 
        "message": "Processing started in background"
    }

@app.get("/")
async def root():
    """Serve the main page."""
    index_file = static_path / "index.html"
    return FileResponse(index_file)


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """
    Upload a video file for processing.

    Args:
        file: Video file uploaded by user

    Returns:
        Status and file path
    """
    global current_video_path

    # Create uploads directory if it doesn't exist
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)

    # Save uploaded file
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
    """
    Handle chunked file upload.
    """
    global current_video_path

    # Create temp directory for chunks if not exists
    temp_dir = Path("uploads/temp") / upload_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Save current chunk
    chunk_path = temp_dir / f"chunk_{chunk_index}"
    
    with open(chunk_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    received_chunks = len(list(temp_dir.glob("chunk_*")))
    
    if received_chunks == total_chunks:
        # Reassemble file
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
        
        # Cleanup temp dir
        shutil.rmtree(temp_dir)
        
        current_video_path = str(final_path)
        print(f"Video fully uploaded: {final_path}")
        
        # Auto-start processing
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
    """
    Set the baseline person count.

    Args:
        count: Number of persons to use as baseline

    Returns:
        Status and baseline value
    """
    monitor.set_baseline(count)

    return {
        "status": "success",
        "baseline": count,
        "message": f"Baseline set to {count} persons"
    }


@app.get("/api/status")
async def get_status():
    """
    Get current monitoring status.

    Returns:
        Current baseline, count, and monitoring state
    """
    status = monitor.get_status()
    return {
        "status": "success",
        **status,
        "video_path": current_video_path,
        "is_processing": is_processing
    }


    return {
        "status": "success",
        **status,
        "video_path": current_video_path,
        "is_processing": is_processing
    }


@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    """
    Get recent alerts.

    Args:
        limit: Max number of alerts to return

    Returns:
        List of recent alerts
    """
    alerts = monitor.get_recent_alerts(limit)
    return {
        "status": "success",
        "alerts": alerts
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time video processing updates.

    Args:
        websocket: WebSocket connection
    """
    await websocket.accept()
    active_connections.append(websocket)
    print("WebSocket connection established")

    try:
        if is_processing:
             # Send processing status
             await websocket.send_json({
                "type": "status",
                "message": "Processing in progress",
                "is_processing": True
            })
             # Send last frame immediately if available
             if last_frame_data:
                 await websocket.send_json(last_frame_data)
                 
             # Send recent alerts
             recent_alerts = monitor.get_recent_alerts(20)
             for alert in reversed(recent_alerts): # Send oldest first to build log
                 await websocket.send_json({
                     "type": "alert",
                     **alert
                 })
        
        # Keep connection open
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

