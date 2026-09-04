"""FastAPI backend with routes, Face Registration endpoints, and WebSocket streaming."""

from fastapi import FastAPI, WebSocket, UploadFile, File, Form, WebSocketDisconnect, BackgroundTasks, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response, StreamingResponse
import asyncio
import shutil
import time
import os
import json
import hashlib
import re
import cv2
import numpy as np
import base64
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import ValidationError

from app import clock
from app.video_processor import VideoProcessor
from app.monitor import AttendanceMonitor
from app.face_engine import FaceEngine
from app.attendance import AttendanceManager, person_label
from app.events import CAMERA_ID, CAMERA_NAME, EventStore
from app.safety import RULE_ATTENDANCE, ZoneStore
from app.auth import authenticate
from app.schemas import (AckInput, CameraInput, CameraPatch, LoginInput,
                         ScheduleInput, SchedulePatch, ZoneInput, ZonePatch)
from app.storage import read_json_list, write_json_list


# Initialize FastAPI app
app = FastAPI(title="HORUS AI - Military Attendance & Face ID")

# Paths
static_path = Path(__file__).parent.parent / "static"
alerts_path = Path(__file__).parent.parent / "alerts"
data_path = Path(__file__).parent.parent / "data"
avatars_path = data_path / "face_avatars"
evidence_path = data_path / "attendance_evidence"
event_snapshots_path = data_path / "events"

# Ensure directories
alerts_path.mkdir(exist_ok=True)
data_path.mkdir(exist_ok=True)
avatars_path.mkdir(parents=True, exist_ok=True)
evidence_path.mkdir(parents=True, exist_ok=True)
event_snapshots_path.mkdir(parents=True, exist_ok=True)

@app.middleware("http")
async def no_cache_for_ui(request, call_next):
    """Bắt trình duyệt hỏi lại máy chủ mỗi lần tải giao diện.

    Trước đây chỉ có ETag và Last-Modified, không có Cache-Control. Thiếu
    Cache-Control thì trình duyệt tự suy đoán thời gian còn "tươi" và giữ bản cũ
    hàng phút mà không hỏi lại — sửa giao diện xong mở lên vẫn thấy bản cũ, tệ
    hơn là index.html cũ ghép với app.js mới nên trang hỏng nửa vời.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# Mount static files
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
app.mount("/alerts", StaticFiles(directory=str(alerts_path)), name="alerts")
app.mount("/data/face_avatars", StaticFiles(directory=str(avatars_path)), name="face_avatars")
app.mount("/data/attendance_evidence", StaticFiles(directory=str(evidence_path)), name="attendance_evidence")
app.mount("/data/events", StaticFiles(directory=str(event_snapshots_path)), name="event_snapshots")

# Dùng chung được: model nhận diện khuôn mặt không giữ trạng thái của luồng nào,
# kho sự kiện ghi vào cùng một file.
face_engine = FaceEngine(data_dir=str(data_path))
events = EventStore(data_dir=str(data_path))
# API cũ /api/zones và API v1 đọc ghi chung kho này, nên cấu hình không bị lệch
zone_store = ZoneStore(str(data_path))

# Mỗi camera một bộ xử lý riêng, chạy song song. Trước đây chỉ có một biến duy
# nhất nên bật camera thứ hai buộc phải tắt camera thứ nhất.
class CameraRuntime:
    """Mọi thứ thuộc về một camera đang chạy."""

    def __init__(self, camera: dict):
        self.camera_id = camera["id"]
        self.camera_name = camera.get("name", camera["id"])
        self.source = camera.get("source_uri", "")
        # Ngưỡng sĩ số và phiên điểm danh là chuyện riêng của từng camera
        self.monitor = AttendanceMonitor(alerts_dir=str(alerts_path / self.camera_id))
        self.attendance = AttendanceManager(
            data_dir=str(data_path), face_engine=face_engine, events=events,
            camera_id=self.camera_id,
        )
        self.processor = VideoProcessor(
            fps=camera.get("target_fps", 5), face_engine=face_engine,
            attendance=self.attendance, events=events, data_dir=str(data_path),
            camera_id=self.camera_id, camera_name=self.camera_name,
        )
        self.last_frame_data = None


runtimes: Dict[str, CameraRuntime] = {}
active_connections: List[WebSocket] = []


# Bản chỉ để ĐỌC thời khoá biểu và kết quả điểm danh của mọi camera. Không gắn
# với luồng nào nên không mở phiên điểm danh; các route tổng hợp dùng bản này.
schedules_view = AttendanceManager(data_dir=str(data_path), face_engine=face_engine,
                                   events=events, camera_id=None)


def is_camera_running(camera_id: str) -> bool:
    return camera_id in runtimes


def _first_runtime() -> Optional[CameraRuntime]:
    """Camera đang chạy đầu tiên. Các route cũ chưa mang camera_id dùng tạm."""
    return next(iter(runtimes.values()), None)


def any_camera_running() -> bool:
    return bool(runtimes)


async def broadcast_update(data: dict):
    """Broadcast update to all connected WebSocket clients."""
    runtime = runtimes.get(data.get("camera_id"))
    if runtime is not None and data.get("type") == "frame_update":
        runtime.last_frame_data = data

    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(data)
        except Exception:
            disconnected.append(connection)
    
    for connection in disconnected:
        if connection in active_connections:
            active_connections.remove(connection)


async def _run_camera(runtime: CameraRuntime):
    """Chạy một camera cho tới khi hết nguồn hoặc bị yêu cầu dừng."""
    try:
        await runtime.processor.process_video(
            runtime.source, broadcast_update, runtime.monitor)
    finally:
        # Dọn kể cả khi luồng ném lỗi, nếu không camera kẹt ở trạng thái "đang
        # chạy" mãi và không bật lại được.
        runtimes.pop(runtime.camera_id, None)


# ----------------- Navigation & Static Routes -----------------

def _asset_version() -> str:
    """Dấu phiên bản của app.js và style.css, đổi khi file đổi."""
    stamp = "".join(
        str((static_path / name).stat().st_mtime_ns)
        for name in ("app.js", "style.css")
        if (static_path / name).exists()
    )
    return hashlib.md5(stamp.encode()).hexdigest()[:8]


@app.get("/", response_class=HTMLResponse)
async def root():
    """Trang giao diện, gắn dấu phiên bản vào CSS và JS lúc phục vụ.

    Tính tại đây chứ không ghi cứng vào file: sửa app.js xong là URL đổi theo,
    không phải nhớ cập nhật thủ công rồi lại gặp cảnh trang chạy bản cũ.
    """
    html = (static_path / "index.html").read_text(encoding="utf-8")
    html = re.sub(r"(/static/(?:app\.js|style\.css))\?v=[a-zA-Z0-9]+",
                  rf"\1?v={_asset_version()}", html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


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
    """Cấu hình vùng theo định dạng cũ, dựng từ cùng kho dữ liệu với API v1."""
    legacy = zone_store.to_legacy()
    if legacy["polygon_points"]:
        return legacy

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
    """Lưu cấu hình kiểu cũ. Các vùng cấm cấu hình qua API v1 được giữ nguyên."""
    try:
        zone_store.merge_legacy(config)
        return {
            "status": "success",
            "message": "Đã lưu cấu hình Vùng & Luật (F-06) thành công!",
            "data": zone_store.to_legacy()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi lưu cấu hình: {str(e)}")


@app.get("/api/snapshot")
async def get_current_snapshot():
    """Retrieve current/latest frame from active stream or video file for ROI drawing background."""
    runtime = _first_runtime()
    last_frame_data = runtime.last_frame_data if runtime else None

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
    else:
        runtime = _first_runtime()
        latest = runtime.last_frame_data if runtime else None
        if latest and "frame" in latest:
            frames = [latest["frame"]]
        
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
    """Get list of military shift schedules kèm trạng thái vận hành hiện tại."""
    return {"status": "success", "data": schedules_view.schedules_with_state(clock.now())}


@app.post("/api/schedules")
async def create_schedule(schedule_data: dict):
    """Create a new military shift schedule."""
    sch_file = data_path / "schedules.json"
    schedules = read_json_list(sch_file)

    schedule_data["id"] = f"sch_{int(time.time() * 1000)}"
    schedule_data["status"] = "Active"
    schedules.append(schedule_data)
    write_json_list(sch_file, schedules)

    return {"status": "success", "message": "Đã thêm ca thời khóa biểu mới!", "data": schedule_data}


@app.delete("/api/schedules/{sch_id}")
async def delete_schedule(sch_id: str):
    """Delete a schedule by ID."""
    sch_file = data_path / "schedules.json"
    if not sch_file.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy thời khóa biểu")

    schedules = [s for s in read_json_list(sch_file) if s.get("id") != sch_id]
    write_json_list(sch_file, schedules)

    return {"status": "success", "message": "Đã xóa ca thời khóa biểu"}


@app.get("/api/attendance-logs")
async def get_attendance_logs(unit: Optional[str] = None):
    """Get history of attendance roll-call logs."""
    logs = read_json_list(data_path / "attendance_logs.json")
    if unit and unit != "all" and unit != "Tất cả đơn vị":
        logs = [l for l in logs if l.get("unit") == unit]
    return {"status": "success", "data": logs}


# ----------------- Roll-call (Điểm danh) Endpoints -----------------

@app.post("/api/attendance/start")
async def start_attendance(schedule_id: Optional[str] = None, window_mins: Optional[int] = None):
    """Mở phiên điểm danh ngay lập tức, không chờ tới giờ ca."""
    now = clock.now()
    runtime = _first_runtime()
    if runtime is None:
        raise HTTPException(status_code=409, detail="Chưa có camera nào đang chạy")
    attendance = runtime.attendance

    if attendance.session is not None:
        if not attendance.session.is_due(now):
            return {"status": "error", "message": "Đang có phiên điểm danh chạy dở"}
        # Phiên cũ đã quá giờ mà chưa chốt (luồng video không chạy) -> bỏ đi
        attendance.cancel_session()

    session = attendance.start_manual(now, schedule_id=schedule_id, window_mins=window_mins)
    return {
        "status": "success",
        "message": f"Đã mở phiên điểm danh {session.window_mins} phút cho {session.schedule.get('unit', 'toàn đơn vị')}",
        "data": attendance.status(now)
    }


@app.post("/api/attendance/cancel")
async def cancel_attendance():
    """Huỷ phiên điểm danh đang mở, không ghi biên bản."""
    runtime = _first_runtime()
    attendance = runtime.attendance if runtime else None
    if attendance is None or attendance.session is None:
        return {"status": "success", "message": "Không có phiên điểm danh nào đang mở"}
    attendance.cancel_session()
    return {"status": "success", "message": "Đã huỷ phiên điểm danh"}


@app.get("/api/attendance/status")
async def attendance_status():
    """Trạng thái phiên điểm danh hiện tại."""
    runtime = _first_runtime()
    if runtime is None:
        return {"status": "success", "data": {"active": False}}
    return {"status": "success", "data": runtime.attendance.status(clock.now())}


# ----------------- Video & Stream Controls -----------------

@app.post("/api/start")
async def start_processing(
    background_tasks: BackgroundTasks,
    mode: str = "video",
    rtsp_url: Optional[str] = None
):
    """Route cũ: bật camera mặc định bằng nguồn vừa tải lên hoặc URL RTSP."""
    global current_video_path

    if mode == "rtsp":
        if not rtsp_url:
            return {"status": "error", "message": "RTSP URL is required"}
        current_video_path = rtsp_url
    else:
        if current_video_path is None:
            return {"status": "error", "message": "No video uploaded"}
        if "://" in current_video_path or not os.path.exists(current_video_path):
            return {"status": "error", "message": "Video file not found. Please upload again."}

    if is_camera_running(CAMERA_ID):
        return {"status": "success", "message": "Already processing"}

    cameras = _load_cameras()
    camera = next((c for c in cameras if c["id"] == CAMERA_ID), None) or {
        "id": CAMERA_ID, "name": CAMERA_NAME, "target_fps": 5}
    camera = {**camera, "source_uri": current_video_path}

    runtime = CameraRuntime(camera)
    runtimes[CAMERA_ID] = runtime
    background_tasks.add_task(_run_camera, runtime)

    return {
        "status": "success",
        "message": "Processing started in background",
        "mode": mode,
        "video_path": current_video_path
    }


@app.post("/api/stop")
async def stop_processing():
    """Route cũ: dừng mọi camera đang chạy."""
    if not runtimes:
        return {"status": "success", "message": "Not processing"}
    for runtime in list(runtimes.values()):
        runtime.processor.stop()
    return {"status": "success", "message": "Stop request submitted"}


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
        
        # Chỉ lưu file và trả đường dẫn. Gán vào camera nào là việc của người
        # dùng — trước đây tự chạy cam_01 nên tải video lên là camera khác cũng
        # bị đổi theo.
        
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
    runtime = _first_runtime()
    if runtime is None:
        raise HTTPException(status_code=409, detail="Chưa có camera nào đang chạy")
    runtime.monitor.set_baseline(count)
    return {
        "status": "success",
        "baseline": count,
        "message": f"Baseline set to {count} persons"
    }


@app.get("/api/time")
async def get_server_time():
    """Giờ máy chủ để giao diện hiển thị trùng với dấu thời gian trên khung hình."""
    return {"status": "success", "server_time": clock.now().isoformat(), "timezone": clock.TZ_NAME}


@app.get("/api/status")
async def get_status():
    """Get system monitoring status and registered stats."""
    runtime = _first_runtime()
    status = runtime.monitor.get_status() if runtime else {
        "baseline_count": None, "current_count": 0, "time_below_baseline": None}
    input_mode = "rtsp" if current_video_path and "://" in current_video_path else "video"
    total_registered = len(face_engine.registered_faces)
    return {
        "status": "success",
        **status,
        "video_path": current_video_path,
        "is_processing": any_camera_running(),
        "cameras_running": sorted(runtimes),
        "input_mode": input_mode,
        "total_registered": total_registered
    }


@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    """Get alert history."""
    runtime = _first_runtime()
    alerts = runtime.monitor.get_recent_alerts(limit) if runtime else []
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
        if any_camera_running():
            await websocket.send_json({
                "type": "status",
                "message": "Processing in progress",
                "is_processing": True
            })
            for runtime in runtimes.values():
                if runtime.last_frame_data:
                    await websocket.send_json(runtime.last_frame_data)
                 
            first = _first_runtime()
            recent_alerts = first.monitor.get_recent_alerts(20) if first else []
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


# ----------------- API v1: sự kiện, vi phạm giờ giấc, an toàn -----------------
# Theo hợp đồng docs/api/openapi.yaml. Các route /api/* cũ giữ nguyên làm alias
# cho giao diện hiện tại trong lúc chuyển tiếp.


def _find_log(session_id: str) -> Optional[dict]:
    return next((l for l in read_json_list(data_path / "attendance_logs.json")
                 if l.get("id") == session_id), None)


def _find_schedule(schedule_id: str) -> Optional[dict]:
    return next((s for s in read_json_list(data_path / "schedules.json")
                 if s.get("id") == schedule_id), None)


@app.get("/api/v1/events")
async def v1_list_events(
    type: Optional[str] = Query(None, description="Lọc nhiều loại, ngăn cách bởi dấu phẩy"),
    acked: Optional[bool] = None,
    session_id: Optional[str] = None,
    camera_id: Optional[str] = None,
    occurred_from: Optional[str] = None,
    occurred_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    """Lịch sử sự kiện, cũng là nguồn cho thư viện ảnh vi phạm an toàn."""
    types = [t.strip() for t in type.split(",")] if type else None
    items, total = events.list_events(
        types=types, acked=acked, session_id=session_id, camera_id=camera_id,
        occurred_from=occurred_from, occurred_to=occurred_to,
        limit=page_size, offset=(max(1, page) - 1) * page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/v1/events/stream")
async def v1_stream_events(type: Optional[str] = None, camera_id: Optional[str] = None,
                           since_event_id: Optional[str] = None):
    """Kênh sự kiện thời gian thực (SSE). Không chứa khung hình.

    ``since_event_id``: phát lại các sự kiện phát sinh sau mốc đó trước khi
    chuyển sang luồng trực tiếp, để client nối lại sau khi đứt không mất sự kiện.
    """
    types = {t.strip() for t in type.split(",")} if type else None
    queue = events.subscribe()
    backlog = events.since(since_event_id, types=types, camera_id=camera_id) \
        if since_event_id else []

    def matches(event: dict) -> bool:
        if types and event.get("type") not in types:
            return False
        if camera_id and event.get("camera_id") != camera_id:
            return False
        return True

    async def generator():
        try:
            for event in backlog:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if not matches(event):
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/v1/events/{event_id}/ack")
async def v1_ack_event(event_id: str, body: AckInput):
    """Xác nhận đã xử lý một sự kiện. Kết quả được lưu, khác với nút giả trước đây."""
    existing = events.get(event_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện")
    if existing.get("acked"):
        raise HTTPException(status_code=409, detail="Sự kiện đã được xác nhận trước đó")

    return events.ack(event_id, body.acked_by, body.note)


@app.get("/api/v1/events/{event_id}/clip")
async def v1_event_clip(event_id: str, download: int = 0):
    """Đoạn video ~10 giây quanh thời điểm sự kiện, dựng từ bộ đệm khung hình."""
    from app.video_processor import latest_event_clips

    event = events.get(event_id)
    clip_id = (event or {}).get("clip_id")
    frames = latest_event_clips.get(clip_id) if clip_id else None
    if not frames:
        raise HTTPException(status_code=404, detail="Sự kiện không có đoạn ghi kèm")

    clip_file = event_snapshots_path / f"{event_id}.mp4"
    if not clip_file.exists():
        decoded = [cv2.imdecode(np.frombuffer(base64.b64decode(f), np.uint8), cv2.IMREAD_COLOR)
                   for f in frames]
        decoded = [img for img in decoded if img is not None]
        if not decoded:
            raise HTTPException(status_code=404, detail="Không giải mã được đoạn ghi")

        h, w = decoded[0].shape[:2]
        writer = cv2.VideoWriter(str(clip_file), cv2.VideoWriter_fourcc(*"mp4v"), 5, (w, h))
        for img in decoded:
            # Khung hình lệch kích thước sẽ bị VideoWriter bỏ qua âm thầm
            if img.shape[:2] == (h, w):
                writer.write(img)
        writer.release()

        if not clip_file.exists() or clip_file.stat().st_size == 0:
            clip_file.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Không dựng được đoạn video")

    return FileResponse(clip_file, media_type="video/mp4",
                        filename=f"{event_id}.mp4" if download else None)


@app.get("/api/v1/sessions/{session_id}/attendance")
async def v1_session_attendance(session_id: str, violation: Optional[str] = None,
                                q: Optional[str] = None):
    """Trạng thái tham gia của từng quân nhân: đi chậm / về sớm / không tham gia.

    Nhận cả id biên bản lẫn id ca. Buổi đang diễn ra thì tính trực tiếp từ dấu
    vết hiện diện; buổi đã chốt thì lấy bảng đã lưu trong biên bản.
    """
    # Nhận cả ba dạng: id biên bản, id ca, và "id ca:ngày" (dạng gắn trên sự kiện)
    log = _find_log(session_id)
    schedule_id = log["schedule_id"] if log else session_id.split(":")[0]
    schedule = _find_schedule(schedule_id)
    if schedule is None and log is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi huấn luyện")

    items, summary = [], None
    if schedule is not None:
        roster = face_engine.get_registered_faces(unit=schedule.get("unit"))
        items, summary = schedules_view.attendance_table(schedule, roster, clock.now())

    if not items and log is not None:
        items = log.get("attendance", [])
        summary = log.get("attendance_summary")

    if violation:
        items = [i for i in items if violation in i.get("violations", [])]
    if q:
        needle = q.lower()
        items = [i for i in items
                 if needle in person_label(i["person"]).lower()
                 or needle in str(i["person"].get("military_id", "")).lower()]

    return {
        "session_id": session_id,
        "summary": summary or {"required": 0, "present": 0, "absent": 0, "late": 0, "early_leave": 0},
        "items": items,
    }


@app.get("/api/v1/summary/safety")
async def v1_safety_summary(date: Optional[str] = None):
    """Dashboard an toàn: trạng thái chung, cảnh báo đang chờ, thư viện ảnh vi phạm.

    ``date`` dạng YYYY-MM-DD; bỏ trống thì lấy toàn bộ lịch sử gần đây.
    """
    recent, total = events.list_events(
        types=["INTRUSION"], limit=50,
        occurred_from=f"{date}T00:00:00" if date else None,
        occurred_to=f"{date}T23:59:59" if date else None,
    )
    pending = [e for e in recent if not e.get("acked")]

    state = "danger" if pending else ("warning" if recent else "normal")
    labels = {"danger": "Cảnh báo nguy hiểm", "warning": "Có vi phạm đã xử lý", "normal": "Bình thường"}

    return {
        "date": date or clock.now().date().isoformat(),
        "state": state,
        "state_label": labels[state],
        "active_intrusion": pending[0] if pending else None,
        "pending_count": len(pending),
        "cameras": [_camera_out(c) for c in _load_cameras()],
        "events": recent,
        "total": total,
    }


@app.get("/api/v1/summary/training")
async def v1_training_summary(training_type: Optional[str] = None,
                              date: Optional[str] = None):
    """Tổng hợp giám sát quân số trong ngày: chỉ số nhanh + danh sách buổi.

    ``training_type`` tách phân hệ đào tạo và chiến đấu; bỏ trống thì lấy cả hai.
    """
    now = clock.now()
    today = date or now.date().isoformat()
    logs = {l.get("schedule_id"): l for l in read_json_list(data_path / "attendance_logs.json")
            if (l.get("date_iso") or str(l.get("started_at", ""))[:10]) == today}

    sessions, running, present_total, required_total, violations = [], 0, 0, 0, 0
    for row in schedules_view.schedules_with_state(now):
        if training_type and row.get("training_type") != training_type:
            continue
        log = logs.get(row.get("id"), {})
        checks = log.get("checks", {})
        summary = log.get("attendance_summary") or {}
        required = log.get("required", row.get("required_count") or 0)

        if row.get("state") in ("check_start", "running", "check_end"):
            running += 1
        present_total += checks.get("start", {}).get("present", 0)
        required_total += required or 0
        violations += (summary.get("absent", 0) + summary.get("late", 0)
                       + summary.get("early_leave", 0))

        sessions.append({
            "id": log.get("id", row.get("id")),
            "schedule_id": row.get("id"),
            "date": today,
            "name": row.get("name", ""),
            "shift": row.get("shift", ""),
            "unit": row.get("unit", ""),
            "training_type": row.get("training_type"),
            "camera_id": row.get("camera_id", CAMERA_ID),
            # Khung giờ và thông tin bài học: màn lịch cần hiển thị giống hệt màn
            # cấu hình, không thì hai nơi nhìn vào cùng một ca lại thấy khác nhau.
            "start_time": row.get("start_time"),
            "end_time": row.get("end_time"),
            "check_window_mins": row.get("check_window_mins"),
            "lesson_name": row.get("lesson_name"),
            "instructor": row.get("instructor"),
            "field": row.get("field"),
            "class_name": row.get("class_name"),
            "state": row.get("state"),
            "state_label": row.get("state_label"),
            "required": required,
            "present_start": checks.get("start", {}).get("present", 0),
            "present_end": checks.get("end", {}).get("present", 0),
            "actual_minutes": log.get("actual_minutes", 0),
            "scheduled_minutes": log.get("scheduled_minutes", 0),
            "progress_pct": log.get("progress_pct", 0.0),
            "violation_count": (summary.get("absent", 0) + summary.get("late", 0)
                                + summary.get("early_leave", 0)),
        })

    progress_values = [s["progress_pct"] for s in sessions if s["progress_pct"]]
    return {
        "date": today,
        "training_type": training_type,
        "stats": {
            "running_sessions": running,
            "present_total": present_total,
            "required_total": required_total,
            "violation_total": violations,
            "overall_progress_pct": round(sum(progress_values) / len(progress_values), 1)
            if progress_values else 0.0,
        },
        "sessions": sessions,
    }


# ----------------- API v1: vùng giám sát -----------------


def _zone_or_404(zone_id: str) -> tuple:
    zones = zone_store.all_zones()
    for index, zone in enumerate(zones):
        if zone.get("id") == zone_id:
            return zones, index
    raise HTTPException(status_code=404, detail="Không tìm thấy vùng giám sát")


def _reject_second_attendance_zone(zones: List[dict], rule: str, skip_id: Optional[str] = None):
    """Mỗi camera chỉ có một vùng đếm quân số; hai vùng thì sĩ số lấy theo vùng nào?"""
    if rule != RULE_ATTENDANCE:
        return
    existing = next((z for z in zones
                     if z.get("rule") == RULE_ATTENDANCE and z.get("id") != skip_id), None)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Camera đã có vùng đếm quân số '{existing.get('name')}'. "
                   f"Sửa vùng đó hoặc đổi nó sang loại khác trước."
        )


@app.get("/api/v1/cameras/{camera_id}/zones")
async def v1_list_zones(camera_id: str):
    """Các vùng đã cấu hình trên camera."""
    return [z for z in zone_store.all_zones()
            if z.get("camera_id", CAMERA_ID) == camera_id]


@app.post("/api/v1/cameras/{camera_id}/zones", status_code=201)
async def v1_create_zone(camera_id: str, payload: ZoneInput):
    """Thêm vùng cho camera. Vòng xử lý video áp dụng ngay, không cần khởi động lại."""
    zones = zone_store.all_zones()
    _reject_second_attendance_zone(
        [z for z in zones if z.get("camera_id", CAMERA_ID) == camera_id], payload.rule
    )

    zone = payload.to_record(f"zone_{int(time.time() * 1000)}", camera_id)
    zones.append(zone)
    zone_store.save(zones)
    return zone


@app.patch("/api/v1/zones/{zone_id}")
async def v1_update_zone(zone_id: str, payload: ZonePatch):
    """Cập nhật vùng. Toàn bộ bản ghi sau khi trộn được kiểm tra lại."""
    zones, index = _zone_or_404(zone_id)
    existing = zones[index]

    try:
        merged = payload.apply_to(existing)
    except ValidationError as e:
        # errors() mặc định kèm object ValueError trong ctx, không serialise được
        raise HTTPException(
            status_code=422,
            detail=e.errors(include_url=False, include_context=False, include_input=False),
        )

    camera_id = existing.get("camera_id", CAMERA_ID)
    _reject_second_attendance_zone(
        [z for z in zones if z.get("camera_id", CAMERA_ID) == camera_id],
        merged.rule, skip_id=zone_id
    )

    zones[index] = merged.to_record(zone_id, camera_id)
    zone_store.save(zones)
    return zones[index]


@app.delete("/api/v1/zones/{zone_id}", status_code=204)
async def v1_delete_zone(zone_id: str):
    """Xoá vùng khỏi cấu hình."""
    zones, index = _zone_or_404(zone_id)
    zones.pop(index)
    zone_store.save(zones)
    return Response(status_code=204)


# ----------------- API v1: luồng hình -----------------

def _current_jpeg(camera_id: str, overlay: int) -> Optional[bytes]:
    from app.video_processor import get_frame
    return get_frame(camera_id, bool(overlay))


@app.get("/api/v1/cameras/{camera_id}/stream.mjpg")
async def v1_camera_stream(camera_id: str, overlay: int = 1, fps: int = 5):
    """Luồng hình trực tiếp dạng MJPEG, dùng thẳng trong thẻ <img>.

    Không cần WebSocket, không cần canvas, trình duyệt tự nối lại khi đứt.
    """
    _camera_or_404(camera_id)
    if _current_jpeg(camera_id, overlay) is None:
        raise HTTPException(status_code=409, detail="Camera chưa được bật xử lý")

    interval = 1.0 / max(1, min(25, fps))
    boundary = b"--frame\r\n"

    async def generator():
        from app import video_processor as vp
        last_revision = -1
        while True:
            # Chỉ gửi khi có khung hình mới, không bơm lại cùng một khung
            revision = vp.get_revision(camera_id)
            if revision != last_revision:
                frame = _current_jpeg(camera_id, overlay)
                if frame is None:
                    break
                last_revision = revision
                yield (boundary + b"Content-Type: image/jpeg\r\n"
                       + f"Content-Length: {len(frame)}\r\n\r\n".encode() + frame + b"\r\n")
            await asyncio.sleep(interval)

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/cameras/{camera_id}/snapshot")
async def v1_camera_snapshot(camera_id: str, overlay: int = 0, download: int = 0):
    """Ảnh tĩnh khung hình hiện tại: nền để vẽ vùng, và nút chụp nhanh."""
    _camera_or_404(camera_id)

    frame = _current_jpeg(camera_id, overlay)
    if frame is None:
        raise HTTPException(status_code=409, detail="Camera chưa có khung hình nào")

    headers = {"Cache-Control": "no-cache, no-store"}
    if download:
        stamp = clock.now().strftime("%Y%m%d_%H%M%S")
        headers["Content-Disposition"] = f'attachment; filename="{camera_id}_{stamp}.jpg"'
    return Response(content=frame, media_type="image/jpeg", headers=headers)


# ----------------- API v1: camera và thời khoá biểu -----------------
# Hai nhóm này là ĐẦU VÀO của service AI. Tạm thời do chính service quản; khi
# nối vào hệ thống quản lý bên ngoài thì chỉ cần đồng bộ xuống hai file JSON này,
# phần AI không phải sửa gì.

cameras_file = data_path / "cameras.json"
schedules_file = data_path / "schedules.json"


def _load_cameras() -> List[dict]:
    """Danh sách camera. Lần đầu chạy thì tạo sẵn camera mặc định."""
    cameras = read_json_list(cameras_file)
    if not cameras:
        cameras = [{
            "id": CAMERA_ID,
            "code": "CAM-01",
            "name": CAMERA_NAME,
            "source_type": "file",
            "source_uri": "",
            "area_name": "Thao trường số 1",
            "enabled": True,
            "target_fps": 5,
        }]
        write_json_list(cameras_file, cameras)
    return cameras


def _camera_status(camera: dict) -> str:
    """Camera đang chạy xử lý hay không. POC chạy một luồng nên chỉ một camera online."""
    if not camera.get("enabled", True):
        return "disabled"
    return "online" if is_camera_running(camera["id"]) else "offline"


def _camera_out(camera: dict) -> dict:
    """Bản ghi trả về giao diện, kèm trạng thái và đường dẫn dựng sẵn."""
    return {
        **camera,
        "status": _camera_status(camera),
        "stream_url": f"/api/v1/cameras/{camera['id']}/stream.mjpg?overlay=1",
        "snapshot_url": f"/api/v1/cameras/{camera['id']}/snapshot?overlay=0",
    }


def _camera_or_404(camera_id: str) -> tuple:
    cameras = _load_cameras()
    for index, camera in enumerate(cameras):
        if camera.get("id") == camera_id:
            return cameras, index
    raise HTTPException(status_code=404, detail="Không tìm thấy camera")


@app.get("/api/v1/cameras")
async def v1_list_cameras(area_name: Optional[str] = None, status: Optional[str] = None):
    """Danh sách camera của service."""
    items = [_camera_out(c) for c in _load_cameras()]
    if area_name:
        items = [c for c in items if c.get("area_name") == area_name]
    if status:
        items = [c for c in items if c["status"] == status]
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


@app.get("/api/v1/cameras/{camera_id}")
async def v1_get_camera(camera_id: str):
    cameras, index = _camera_or_404(camera_id)
    return _camera_out(cameras[index])


@app.post("/api/v1/cameras", status_code=201)
async def v1_create_camera(payload: CameraInput):
    cameras = _load_cameras()
    camera = payload.model_dump()
    camera["id"] = f"cam_{int(time.time() * 1000)}"
    cameras.append(camera)
    write_json_list(cameras_file, cameras)
    return _camera_out(camera)


@app.patch("/api/v1/cameras/{camera_id}")
async def v1_update_camera(camera_id: str, payload: CameraPatch):
    cameras, index = _camera_or_404(camera_id)
    try:
        merged = payload.apply_to(cameras[index])
    except ValidationError as e:
        raise HTTPException(status_code=422,
                            detail=e.errors(include_url=False, include_context=False,
                                            include_input=False))
    merged["id"] = camera_id
    cameras[index] = merged
    write_json_list(cameras_file, cameras)
    return _camera_out(merged)


@app.delete("/api/v1/cameras/{camera_id}", status_code=204)
async def v1_delete_camera(camera_id: str):
    """Gỡ camera. Vùng giám sát của camera đó bị xoá theo, không để lại rác."""
    cameras, index = _camera_or_404(camera_id)
    if is_camera_running(camera_id):
        raise HTTPException(status_code=409, detail="Camera đang chạy, dừng xử lý trước khi xoá")

    cameras.pop(index)
    write_json_list(cameras_file, cameras)
    zone_store.save([z for z in zone_store.all_zones()
                     if z.get("camera_id", CAMERA_ID) != camera_id])
    return Response(status_code=204)


@app.post("/api/v1/cameras/{camera_id}/start", status_code=202)
async def v1_start_camera(camera_id: str, background_tasks: BackgroundTasks):
    """Bật xử lý AI cho camera, dùng nguồn đã khai trong hồ sơ camera.

    Các camera chạy song song, độc lập nhau: bật camera này không phải tắt
    camera kia.
    """
    cameras, index = _camera_or_404(camera_id)
    camera = cameras[index]

    if not camera.get("enabled", True):
        raise HTTPException(status_code=409, detail="Camera đang bị tắt")
    if is_camera_running(camera_id):
        raise HTTPException(status_code=409, detail="Camera này đang chạy rồi")

    source = camera.get("source_uri") or ""
    if not source:
        raise HTTPException(status_code=422, detail="Camera chưa khai nguồn (source_uri)")
    if "://" not in source and not os.path.exists(source):
        raise HTTPException(status_code=422, detail=f"Không tìm thấy nguồn video: {source}")

    runtime = CameraRuntime(camera)
    runtimes[camera_id] = runtime
    background_tasks.add_task(_run_camera, runtime)
    return _camera_out(camera)


@app.post("/api/v1/cameras/{camera_id}/stop", status_code=202)
async def v1_stop_camera(camera_id: str):
    """Dừng riêng camera này, các camera khác vẫn chạy tiếp."""
    cameras, index = _camera_or_404(camera_id)
    runtime = runtimes.get(camera_id)
    if runtime is not None:
        runtime.processor.stop()
    return _camera_out(cameras[index])


@app.get("/api/v1/schedules")
async def v1_list_schedules(training_type: Optional[str] = None, unit: Optional[str] = None,
                            enabled: Optional[bool] = None):
    """Thời khoá biểu kèm trạng thái vận hành hiện tại của từng ca."""
    rows = schedules_view.schedules_with_state(clock.now())
    if training_type:
        rows = [r for r in rows if r.get("training_type") == training_type]
    if unit:
        rows = [r for r in rows if r.get("unit") == unit]
    if enabled is not None:
        rows = [r for r in rows if bool(r.get("enabled", True)) is enabled]
    return {"items": rows, "total": len(rows), "page": 1, "page_size": len(rows)}


@app.post("/api/v1/schedules", status_code=201)
async def v1_create_schedule(payload: ScheduleInput):
    schedules = read_json_list(schedules_file)
    schedule = payload.model_dump()
    schedule["id"] = f"sch_{int(time.time() * 1000)}"
    schedules.append(schedule)
    write_json_list(schedules_file, schedules)
    return schedule


@app.get("/api/v1/schedules/{schedule_id}")
async def v1_get_schedule(schedule_id: str):
    row = next((r for r in schedules_view.schedules_with_state(clock.now())
                if r.get("id") == schedule_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca")
    return row


@app.patch("/api/v1/schedules/{schedule_id}")
async def v1_update_schedule(schedule_id: str, payload: SchedulePatch):
    """Sửa ca. Buổi đang chạy không bị ảnh hưởng, cấu hình mới áp cho buổi sau."""
    schedules = read_json_list(schedules_file)
    index = next((i for i, s in enumerate(schedules) if s.get("id") == schedule_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca")

    try:
        merged = payload.apply_to(schedules[index])
    except ValidationError as e:
        raise HTTPException(status_code=422,
                            detail=e.errors(include_url=False, include_context=False,
                                            include_input=False))
    merged["id"] = schedule_id
    schedules[index] = merged
    write_json_list(schedules_file, schedules)
    return merged


@app.delete("/api/v1/schedules/{schedule_id}", status_code=204)
async def v1_delete_schedule(schedule_id: str):
    schedules = read_json_list(schedules_file)
    remaining = [s for s in schedules if s.get("id") != schedule_id]
    if len(remaining) == len(schedules):
        raise HTTPException(status_code=404, detail="Không tìm thấy ca")
    write_json_list(schedules_file, remaining)
    return Response(status_code=204)


@app.get("/api/v1/sessions/{session_id}/checks")
async def v1_session_checks(session_id: str):
    """Các mốc điểm danh và ảnh bằng chứng do camera AI chụp."""
    log = _find_log(session_id)
    if log is None:
        log = next((l for l in read_json_list(data_path / "attendance_logs.json")
                    if l.get("session_id") == session_id), None)
    if log is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi huấn luyện")

    checks = log.get("checks", {})
    return [
        {**checks[phase], "evidence_url": checks[phase].get("evidence")}
        for phase in ("start", "end", "manual") if phase in checks
    ]


# ----------------- API v1: đăng nhập -----------------

@app.post("/api/v1/auth/login")
async def v1_login(body: LoginInput):
    """Kiểm tra tài khoản và trả về vai trò để giao diện hiện đúng menu.

    Không tạo phiên và không cấp token: các endpoint khác **không** kiểm quyền.
    Xem ghi chú trong ``app/auth.py``.
    """
    user = authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Sai tài khoản hoặc mật khẩu")
    return user


# ----------------- API v1: hệ thống -----------------

@app.get("/api/v1/system/time")
async def v1_system_time():
    """Giờ máy chủ. Giao diện nên đồng bộ theo đây vì mọi mốc điểm danh lấy từ nó."""
    return {"server_time": clock.iso(), "timezone": clock.TZ_NAME}


@app.get("/api/v1/system/health")
async def v1_system_health():
    cameras = _load_cameras()
    running = sum(1 for c in cameras if _camera_status(c) == "online")
    return {
        "status": "ok",
        "cameras_running": running,
        "cameras_total": len(cameras),
        "models_loaded": ["yolo-person", "insightface-buffalo_l"],
        "registered_personnel": len(face_engine.registered_faces),
        "pending_events": events.pending_count(),
    }
