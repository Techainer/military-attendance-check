"""FastAPI backend with routes and WebSocket endpoint."""

from fastapi import FastAPI, WebSocket, UploadFile, File, WebSocketDisconnect, Form
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

# Global state (simple for demo - would use proper state management in production)
monitor = AttendanceMonitor()
current_video_path = None


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

    # Check if all chunks are received
    # This is a simple check. For production, we might want to track which chunks are received.
    # Here we count files in temp dir.
    # NOTE: This assumes sequential upload or at least all chunks arriving eventually.
    # Ideally frontend sends sequential.
    
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
        
        return {
            "status": "success",
            "message": "Upload complete",
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
        "video_path": current_video_path
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time video processing updates.

    Args:
        websocket: WebSocket connection
    """
    await websocket.accept()

    print("WebSocket connection established")

    if current_video_path is None:
        await websocket.send_json({
            "type": "error",
            "message": "No video uploaded. Please upload a video first."
        })
        await websocket.close()
        return

    if not os.path.exists(current_video_path):
        await websocket.send_json({
            "type": "error",
            "message": f"Video file not found: {current_video_path}"
        })
        await websocket.close()
        return

    # Create video processor
    processor = VideoProcessor(fps=5)

    try:
        # Process video and stream results
        await processor.process_video(current_video_path, websocket, monitor)
    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"Error in WebSocket: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Processing error: {str(e)}"
            })
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass
        print("WebSocket connection closed")
