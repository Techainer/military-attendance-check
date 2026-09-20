"""Kiểm chứng endpoint v1 và đối chiếu sự kiện thật với hợp đồng events.schema.json."""

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import app.api as api
from app.events import CAMERA_ID

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


client = TestClient(api.app)
schema_file = Path(__file__).resolve().parent.parent / "docs" / "api" / "events.schema.json"
validator = Draft202012Validator(json.load(open(schema_file)))

print("\n[1] Sự kiện do backend sinh ra có đúng hợp đồng không")

produced = [
    api.events.emit(
        "INTRUSION", "Phát hiện 01 đối tượng đi vào khối chắn tuyến bắn.",
        severity="critical", zone_id="z_cam",
        boxes=[{"x1": 0.6, "y1": 0.4, "x2": 0.7, "y2": 0.8, "confidence": 0.88,
                "label": "Không xác định"}],
        detail={"zone_name": "Khối chắn tuyến bắn", "zone_rule": "restricted_area",
                "object_count": 1, "object_class": "person", "identified": [],
                "dwell_seconds": 3},
    ),
    api.events.emit(
        "LATE", "Binh nhất Nguyễn Văn A đi chậm 14 phút.", severity="warning",
        person_id="p2", person_name="Binh nhất Nguyễn Văn A", session_id="ses_1",
        schedule_id="sch_1",
        detail={"late_minutes": 14, "first_seen": "2026-08-30T07:14:05",
                "planned_start": "2026-08-30T07:00:00", "tolerance_mins": 5},
    ),
    api.events.emit(
        "ABSENT", "Thiếu 05 quân nhân so với sĩ số chuẩn (40/45).", severity="warning",
        detail={"current_count": 40, "required_count": 45, "missing_count": 5,
                "duration_seconds": 30},
    ),
    api.events.emit(
        "EARLY_LEAVE", "Binh nhất B rời thao trường sớm 20 phút.", severity="warning",
        person_id="p3", person_name="Binh nhất B", session_id="ses_1",
        detail={"early_leave_minutes": 20, "last_seen": "2026-08-30T11:10:00",
                "planned_end": "2026-08-30T11:30:00"},
    ),
    api.events.emit(
        "SYSTEM", "Đã chốt điểm danh đầu giờ: 43/45 quân nhân.", severity="info",
        acked=True, session_id="ses_1",
        detail={"code": "check_closed", "check_phase": "start", "present": 43, "required": 45},
    ),
]

for event in produced:
    errors = sorted(validator.iter_errors(event), key=lambda e: list(e.path))
    check(f"{event['type']} khớp events.schema.json", not errors,
          errors[0].message if errors else "")

check("occurred_at kèm offset múi giờ như hợp đồng",
      produced[0]["occurred_at"].endswith("+07:00"), produced[0]["occurred_at"])
check("sự kiện vi phạm cá nhân có session_id để lọc theo buổi",
      all(e["session_id"] for e in produced if e["type"] in ("LATE", "EARLY_LEAVE")))

r0 = client.get("/api/v1/events?session_id=ses_1")
check("lọc sự kiện theo buổi trả về kết quả",
      r0.status_code == 200 and r0.json()["total"] >= 2, r0.text[:200])

print("\n[2] Endpoint v1")

r = client.get("/api/v1/events")
check("GET /events trả 200", r.status_code == 200, r.text[:200])
body = r.json()
check("có phân trang theo hợp đồng",
      set(body) == {"items", "total", "page", "page_size"}, str(set(body)))
check("liệt kê đủ sự kiện vừa sinh", body["total"] >= 5, str(body["total"]))

r = client.get("/api/v1/events?type=INTRUSION")
check("lọc theo loại", all(e["type"] == "INTRUSION" for e in r.json()["items"]))

r = client.get("/api/v1/events?acked=false")
check("lọc chờ xử lý", all(not e["acked"] for e in r.json()["items"]))

intrusion_id = produced[0]["id"]
r = client.post(f"/api/v1/events/{intrusion_id}/ack",
                json={"acked_by": "Đại uý Nguyễn Văn Hùng", "note": "Đã xử lý tại chỗ"})
check("POST ack trả 200", r.status_code == 200, r.text[:200])
check("ack được lưu", r.json()["acked"] and r.json()["acked_by"].endswith("Hùng"))

r = client.post(f"/api/v1/events/{intrusion_id}/ack", json={"acked_by": "X"})
check("ack lần hai -> 409", r.status_code == 409, str(r.status_code))

r = client.post(f"/api/v1/events/{intrusion_id}/ack", json={})
check("thiếu acked_by -> 422", r.status_code == 422, str(r.status_code))

r = client.post("/api/v1/events/evt_khong_ton_tai/ack", json={"acked_by": "X"})
check("ack sự kiện lạ -> 404", r.status_code == 404, str(r.status_code))

r = client.get("/api/v1/summary/safety")
check("GET /summary/safety trả 200", r.status_code == 200, r.text[:200])
safety = r.json()
check("dashboard an toàn có đủ trường hợp đồng",
      {"state", "state_label", "active_intrusion", "pending_count", "events"} <= set(safety),
      str(set(safety)))
check("trạng thái an toàn là giá trị hợp lệ",
      safety["state"] in ("normal", "warning", "danger"), safety["state"])

r = client.get("/api/v1/summary/training")
check("GET /summary/training trả 200", r.status_code == 200, r.text[:200])
stats = r.json()["stats"]
check("chỉ số nhanh đủ trường",
      {"running_sessions", "present_total", "required_total", "violation_total",
       "overall_progress_pct"} <= set(stats), str(set(stats)))

r = client.get("/api/v1/sessions/khong_ton_tai/attendance")
check("buổi không tồn tại -> 404", r.status_code == 404, str(r.status_code))

print("\n[2b] Đoạn video sự kiện")

import base64

import cv2
import numpy as np

import app.video_processor as vp

frame = np.zeros((120, 160, 3), np.uint8)
encoded = base64.b64encode(cv2.imencode(".jpg", frame)[1]).decode()
for _ in range(10):
    vp.push_clip_frame(CAMERA_ID, encoded)
vp.keep_clip(CAMERA_ID, "clip_test")

with_clip = api.events.emit("INTRUSION", "Có đoạn ghi kèm.", severity="critical",
                            zone_id="z1", clip_id="clip_test",
                            detail={"zone_name": "z", "object_count": 1})
check("sự kiện có clip thì dựng được clip_url",
      with_clip["clip_url"] == f"/api/v1/events/{with_clip['id']}/clip", str(with_clip["clip_url"]))

r = client.get(f"/api/v1/events/{with_clip['id']}/clip")
check("GET clip trả 200", r.status_code == 200, r.text[:200])
check("clip là video mp4", r.headers["content-type"] == "video/mp4",
      r.headers.get("content-type", ""))
check("clip không rỗng", len(r.content) > 0, str(len(r.content)))

no_clip = api.events.emit("ABSENT", "Không kèm đoạn ghi.",
                          detail={"current_count": 1, "required_count": 2,
                                  "duration_seconds": 30})
r = client.get(f"/api/v1/events/{no_clip['id']}/clip")
check("sự kiện không có đoạn ghi -> 404", r.status_code == 404, str(r.status_code))

# Bộ đệm khung hình chỉ nằm trong RAM và bị dọn dần. Đoạn ghi đã ra file thì
# phải xem lại được kể cả khi bộ đệm trống — lỗi thật: hàm hỏi bộ đệm trước rồi
# mới ngó tới file, nên khởi động lại máy chủ là mọi nút "Xem clip" đều chết.
vp.latest_event_clips.clear()
r = client.get(f"/api/v1/events/{with_clip['id']}/clip")
check("bộ đệm RAM trống vẫn xem lại được đoạn ghi đã ra file",
      r.status_code == 200, f"{r.status_code} {r.text[:120]}")
check("đoạn ghi lấy từ file vẫn là mp4 có nội dung",
      r.headers.get("content-type") == "video/mp4" and len(r.content) > 0,
      f"{r.headers.get('content-type')} {len(r.content)} byte")

clip_url = api.events.save_clip("evt_kiem_thu_clip", [encoded] * 5)
clip_file = api.event_snapshots_path / "evt_kiem_thu_clip.mp4"
check("save_clip dựng được đoạn mp4",
      clip_url == "/api/v1/events/evt_kiem_thu_clip/clip", str(clip_url))
check("file nằm cạnh ảnh bằng chứng và không rỗng",
      clip_file.exists() and clip_file.stat().st_size > 0)

# Đoạn ghi phải là H.264. cv2.VideoWriter bản PyPI chỉ ghi được mp4v (MPEG-4
# Part 2): file hợp lệ, tải về được, nhưng thẻ <video> của trình duyệt từ chối
# phát — giao diện báo "không tải được đoạn ghi" mà API vẫn trả 200.
raw = clip_file.read_bytes()
tags = sorted(t.decode() for t in set(re.findall(rb"(mp4v|avc1|hev1|av01)", raw)))
check("đoạn ghi mã hoá H.264 để trình duyệt phát được", tags == ["avc1"], str(tags))
check("moov atom nằm trước mdat để phát được ngay khi chưa tải xong",
      raw.index(b"moov") < raw.index(b"mdat"))
check("không có khung nào thì không dựng file rỗng",
      api.events.save_clip("evt_kiem_thu_rong", []) is None)
check("không đẻ file cho đoạn ghi rỗng",
      not (api.event_snapshots_path / "evt_kiem_thu_rong.mp4").exists())
clip_file.unlink(missing_ok=True)

# Sự kiện phải tự ghi đoạn ghi ra file ngay lúc phát sinh, không đợi ai bấm xem:
# bộ đệm trong RAM bị dọn dần nên đợi là mất.
for _ in range(6):
    vp.push_clip_frame(CAMERA_ID, encoded)
vp.keep_clip(CAMERA_ID, "clip_luc_phat_sinh")
stub = SimpleNamespace(events=api.events)
asyncio.run(vp.VideoProcessor._archive_clip(stub, {"id": "evt_kiem_thu_ghi"}, "clip_luc_phat_sinh"))
ghi_file = api.event_snapshots_path / "evt_kiem_thu_ghi.mp4"
check("sự kiện phát sinh là ghi đoạn ghi ra file luôn",
      ghi_file.exists() and ghi_file.stat().st_size > 0)
ghi_file.unlink(missing_ok=True)

# Hàm không trả gì thì FastAPI gửi null kèm mã 200, client tưởng thành công rồi
# nổ khi đọc thuộc tính. Trình phát clip cũ chết đúng vì chỗ này.
r = client.get("/api/snapshot")
check("/api/snapshot không trả null kèm mã 200",
      not (r.status_code == 200 and r.json() is None), f"{r.status_code} {r.text[:80]}")

# Camera riêng cho ca này: bộ đệm clip tích luỹ theo từng camera, dùng lại
# camera ở trên thì đoạn "hỏng" vẫn còn lẫn các khung hợp lệ đẩy vào trước đó.
for _ in range(3):
    vp.push_clip_frame("cam_clip_hong", "khong-phai-anh")
vp.keep_clip("cam_clip_hong", "clip_hong")
broken = api.events.emit("INTRUSION", "Đoạn ghi hỏng.", severity="critical",
                         zone_id="z1", clip_id="clip_hong",
                         detail={"zone_name": "z", "object_count": 1})
r = client.get(f"/api/v1/events/{broken['id']}/clip")
check("đoạn ghi hỏng -> báo lỗi thay vì sập", r.status_code in (404, 500), str(r.status_code))

print("\n[3] Kênh SSE")

# Gọi thẳng vào ASGI trong một vòng lặp duy nhất. TestClient chạy app ở thread
# khác nên emit() từ thread chính không đánh thức được asyncio.Queue của kênh —
# đó là giới hạn của công cụ test, còn trong app emit() luôn được gọi từ chính
# vòng lặp xử lý video.
import asyncio


async def sse_roundtrip():
    sent = []
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/api/v1/events/stream", "raw_path": b"/api/v1/events/stream",
             "query_string": b"", "root_path": "", "headers": [(b"host", b"test")],
             "client": ("127.0.0.1", 1234), "server": ("test", 80)}

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(api.app(scope, receive, send))
    # Chờ tới khi kênh mở và đã có client đăng ký
    for _ in range(100):
        await asyncio.sleep(0.02)
        if sent and api.events._subscribers:
            break

    emitted = api.events.emit("SYSTEM", "Camera đã kết nối lại.",
                              detail={"code": "camera_online"})
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any(m["type"] == "http.response.body" and m.get("body") for m in sent):
            break

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return emitted, sent


emitted, sent = asyncio.run(sse_roundtrip())

start = next((m for m in sent if m["type"] == "http.response.start"), None)
headers = {k.decode(): v.decode() for k, v in (start or {}).get("headers", [])}
check("SSE trả 200", start is not None and start["status"] == 200, str(start))
check("SSE đúng content-type",
      headers.get("content-type", "").startswith("text/event-stream"), str(headers))
check("SSE tắt buffering của proxy", headers.get("x-accel-buffering") == "no", str(headers))

chunks = [m["body"].decode() for m in sent
          if m["type"] == "http.response.body" and m.get("body")]
data_lines = [c for c in chunks if c.startswith("data: ")]
check("client đang mở kênh nhận được bản tin", bool(data_lines), str(chunks))

if data_lines:
    payload = json.loads(data_lines[0][6:].strip())
    check("bản tin đúng sự kiện vừa phát", payload["id"] == emitted["id"])
    check("bản tin khớp events.schema.json", not list(validator.iter_errors(payload)))
    check("khung SSE kết thúc bằng dòng trống", data_lines[0].endswith("\n\n"))

check("đóng kênh thì huỷ đăng ký client", api.events._subscribers == [],
      str(api.events._subscribers))

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
