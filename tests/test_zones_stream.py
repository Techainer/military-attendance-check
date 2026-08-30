"""Kiểm chứng API vùng giám sát (validate) và luồng hình MJPEG."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import base64
import json

import cv2
import numpy as np
import yaml
from fastapi.testclient import TestClient

import app.api as api
import app.video_processor as vp
from app.events import CAMERA_ID

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


client = TestClient(api.app)
ZONES = f"/api/v1/cameras/{CAMERA_ID}/zones"


def reset_zones():
    api.zone_store.save([])


def square(offset=0.0):
    return [{"x": 0.1 + offset, "y": 0.1}, {"x": 0.4 + offset, "y": 0.1},
            {"x": 0.4 + offset, "y": 0.4}, {"x": 0.1 + offset, "y": 0.4}]


# ================================================================ validate
print("\n[1] Kiểm tra dữ liệu vào của vùng giám sát")

reset_zones()

r = client.post(ZONES, json={"name": "Khu tập trung", "kind": "polygon",
                             "rule": "attendance_area", "points": square()})
check("tạo vùng hợp lệ -> 201", r.status_code == 201, r.text[:250])
zone = r.json() if r.status_code == 201 else {}
check("vùng có id và camera_id", bool(zone.get("id")) and zone.get("camera_id") == CAMERA_ID,
      str(zone))

invalid_cases = [
    ("thiếu tên", {"name": "", "kind": "polygon", "rule": "restricted_area", "points": square()}),
    ("kind lạ", {"name": "x", "kind": "hinh_tron", "rule": "restricted_area", "points": square()}),
    ("rule lạ", {"name": "x", "kind": "polygon", "rule": "bay_gio", "points": square()}),
    ("polygon chỉ 2 điểm", {"name": "x", "kind": "polygon", "rule": "restricted_area",
                            "points": square()[:2]}),
    ("vạch 3 điểm", {"name": "x", "kind": "tripwire", "rule": "crossing_line",
                     "points": square()[:3]}),
    ("vạch 1 điểm", {"name": "x", "kind": "tripwire", "rule": "crossing_line",
                     "points": square()[:1]}),
    ("polygon đi với luật cắt vạch", {"name": "x", "kind": "polygon", "rule": "crossing_line",
                                      "points": square()}),
    ("vạch đi với luật đếm quân số", {"name": "x", "kind": "tripwire", "rule": "attendance_area",
                                      "points": square()[:2]}),
    ("toạ độ ngoài [0,1]", {"name": "x", "kind": "polygon", "rule": "restricted_area",
                            "points": [{"x": 1.5, "y": 0.1}, {"x": 0.4, "y": 0.1},
                                       {"x": 0.4, "y": 0.4}]}),
    ("toạ độ âm", {"name": "x", "kind": "polygon", "rule": "restricted_area",
                   "points": [{"x": -0.1, "y": 0.1}, {"x": 0.4, "y": 0.1},
                              {"x": 0.4, "y": 0.4}]}),
    ("điểm trùng nhau", {"name": "x", "kind": "polygon", "rule": "restricted_area",
                         "points": [{"x": 0.1, "y": 0.1}, {"x": 0.1, "y": 0.1},
                                    {"x": 0.4, "y": 0.4}]}),
    ("tắt cả người lẫn vật", {"name": "x", "kind": "polygon", "rule": "restricted_area",
                              "points": square(), "detect_human": False,
                              "detect_object": False}),
]

for label, body in invalid_cases:
    r = client.post(ZONES, json=body)
    check(f"từ chối: {label} -> 422", r.status_code == 422, f"nhận {r.status_code}")

# ================================================================ nghiệp vụ
print("\n[2] Ràng buộc nghiệp vụ")

r = client.post(ZONES, json={"name": "Vùng đếm thứ hai", "kind": "polygon",
                             "rule": "attendance_area", "points": square(0.4)})
check("camera chỉ được một vùng đếm quân số -> 409", r.status_code == 409, r.text[:200])

r = client.post(ZONES, json={"name": "Khối chắn tuyến bắn", "kind": "polygon",
                             "rule": "restricted_area", "points": square(0.4)})
check("thêm vùng cấm bên cạnh vùng đếm -> 201", r.status_code == 201, r.text[:200])
restricted = r.json() if r.status_code == 201 else {}

r = client.post(ZONES, json={"name": "Vạch an toàn", "kind": "tripwire",
                             "rule": "crossing_line",
                             "points": [{"x": 0.0, "y": 0.5}, {"x": 1.0, "y": 0.5}]})
check("thêm vạch an toàn -> 201", r.status_code == 201, r.text[:200])
tripwire = r.json() if r.status_code == 201 else {}

r = client.get(ZONES)
check("liệt kê đủ ba vùng", r.status_code == 200 and len(r.json()) == 3,
      str(len(r.json()) if r.status_code == 200 else r.text[:150]))

# ================================================================ cập nhật
print("\n[3] Cập nhật và xoá")

r = client.patch(f"/api/v1/zones/{restricted['id']}", json={"name": "Khối chắn số 2"})
check("đổi tên -> 200", r.status_code == 200, r.text[:200])
check("tên đã đổi", r.json().get("name") == "Khối chắn số 2", str(r.json()))
check("các trường khác giữ nguyên",
      r.json().get("rule") == "restricted_area" and len(r.json().get("points", [])) == 4,
      str(r.json()))

r = client.patch(f"/api/v1/zones/{restricted['id']}", json={"enabled": False})
check("tắt vùng -> 200", r.status_code == 200 and r.json()["enabled"] is False, r.text[:200])

r = client.patch(f"/api/v1/zones/{restricted['id']}", json={"kind": "tripwire"})
check("đổi kind làm số điểm sai -> 422 (kiểm lại cả bản ghi)",
      r.status_code == 422, f"nhận {r.status_code}")

r = client.patch(f"/api/v1/zones/{restricted['id']}", json={"rule": "attendance_area"})
check("đổi sang vùng đếm khi đã có vùng đếm -> 409", r.status_code == 409, r.text[:200])

r = client.patch("/api/v1/zones/khong_ton_tai", json={"name": "x"})
check("sửa vùng không tồn tại -> 404", r.status_code == 404, str(r.status_code))

r = client.delete(f"/api/v1/zones/{tripwire['id']}")
check("xoá vùng -> 204", r.status_code == 204, str(r.status_code))
check("xoá xong còn hai vùng", len(client.get(ZONES).json()) == 2)

r = client.delete("/api/v1/zones/khong_ton_tai")
check("xoá vùng không tồn tại -> 404", r.status_code == 404, str(r.status_code))

# ================================================================ tác động thật
print("\n[4] Cấu hình có tác dụng thật lên phần phát hiện")

reset_zones()
client.post(ZONES, json={"name": "Vùng cấm", "kind": "polygon", "rule": "restricted_area",
                         "points": [{"x": 0.6, "y": 0.0}, {"x": 1.0, "y": 0.0},
                                    {"x": 1.0, "y": 1.0}, {"x": 0.6, "y": 1.0}]})

from datetime import datetime, timedelta

from app.safety import IntrusionDetector, ZoneStore

store = ZoneStore(str(api.data_path))
det = IntrusionDetector(store)
now = datetime(2026, 8, 30, 9, 0, 0)
inside = [700, 100, 800, 400]

v = det.check([inside], [1], 1000, 1000, now)
check("vùng cấm vừa tạo qua API sinh được vi phạm", len(v) == 1, str(v))

zone_id = client.get(ZONES).json()[0]["id"]
client.patch(f"/api/v1/zones/{zone_id}", json={"enabled": False})
det2 = IntrusionDetector(ZoneStore(str(api.data_path)))
v = det2.check([inside], [1], 1000, 1000, now)
check("tắt vùng thì không còn sinh vi phạm", v == [], str(v))

# ================================================================ tương thích cũ
print("\n[5] API cũ /api/zones dùng chung kho dữ liệu")

reset_zones()
legacy_payload = {
    "zone_name": "Sân tập trung",
    "rule_type": "Cảnh báo Xâm nhập 24/7",
    "detect_human": True,
    "detect_object": True,
    "polygon_points": square(),
    "tripwire_points": [{"x": 0.0, "y": 0.5}, {"x": 1.0, "y": 0.5}],
}
r = client.post("/api/zones", json=legacy_payload)
check("lưu cấu hình kiểu cũ -> 200", r.status_code == 200, r.text[:200])

v1 = client.get(ZONES).json()
check("API v1 nhìn thấy vùng do API cũ lưu", len(v1) == 2, str(len(v1)))
check("polygon cũ thành vùng đếm quân số",
      any(z["rule"] == "attendance_area" for z in v1), str([z["rule"] for z in v1]))
check("vạch kế thừa để TẮT sẵn, không tự réo còi",
      all(not z["enabled"] for z in v1 if z["rule"] == "crossing_line"), str(v1))

back = client.get("/api/zones").json()
check("API cũ đọc lại đúng polygon đã lưu", back["polygon_points"] == square(), str(back))
check("API cũ đọc lại đúng vạch", len(back["tripwire_points"]) == 2, str(back))

client.post(ZONES, json={"name": "Khối chắn", "kind": "polygon", "rule": "restricted_area",
                         "points": square(0.4)})
client.post("/api/zones", json=legacy_payload)
kinds = [z["rule"] for z in client.get(ZONES).json()]
check("lưu lại kiểu cũ KHÔNG xoá mất vùng cấm của API v1",
      "restricted_area" in kinds, str(kinds))

# ================================================================ MJPEG
print("\n[6] Luồng hình MJPEG")

vp.clear_frames()
r = client.get(f"/api/v1/cameras/{CAMERA_ID}/stream.mjpg")
check("chưa chạy xử lý -> 409", r.status_code == 409, str(r.status_code))
r = client.get(f"/api/v1/cameras/{CAMERA_ID}/snapshot")
check("snapshot khi chưa có hình -> 409", r.status_code == 409, str(r.status_code))

overlay_img = np.full((60, 80, 3), 200, np.uint8)
clean_img = np.zeros((60, 80, 3), np.uint8)
overlay_jpeg = cv2.imencode(".jpg", overlay_img)[1].tobytes()
clean_jpeg = cv2.imencode(".jpg", clean_img)[1].tobytes()
vp.publish_frame(overlay_jpeg, clean_jpeg)

r = client.get(f"/api/v1/cameras/{CAMERA_ID}/snapshot?overlay=1")
check("snapshot có lớp phủ -> 200 ảnh jpeg",
      r.status_code == 200 and r.headers["content-type"] == "image/jpeg", str(r.status_code))
check("snapshot overlay=1 trả bản có lớp phủ", r.content == overlay_jpeg)

r = client.get(f"/api/v1/cameras/{CAMERA_ID}/snapshot?overlay=0")
check("snapshot overlay=0 trả bản GỐC, khác hẳn bản có lớp phủ",
      r.content == clean_jpeg and r.content != overlay_jpeg)

r = client.get(f"/api/v1/cameras/{CAMERA_ID}/snapshot?download=1")
check("tham số download gắn Content-Disposition",
      "attachment" in r.headers.get("content-disposition", ""),
      r.headers.get("content-disposition", ""))

r = client.get("/api/v1/cameras/cam_khong_co/snapshot")
check("camera lạ -> 404", r.status_code == 404, str(r.status_code))

# Gọi thẳng ASGI: luồng MJPEG không bao giờ tự kết thúc, TestClient sẽ chờ mãi.
import asyncio


async def pull_mjpeg(overlay: int, want_frames: int):
    sent = []
    query = f"overlay={overlay}&fps=25".encode()
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": f"/api/v1/cameras/{CAMERA_ID}/stream.mjpg",
             "raw_path": f"/api/v1/cameras/{CAMERA_ID}/stream.mjpg".encode(),
             "query_string": query, "root_path": "", "headers": [(b"host", b"test")],
             "client": ("127.0.0.1", 1234), "server": ("test", 80)}

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(api.app(scope, receive, send))
    for _ in range(200):
        await asyncio.sleep(0.02)
        bodies = [m for m in sent if m["type"] == "http.response.body" and m.get("body")]
        if len(bodies) >= want_frames:
            break
        # Khung hình mới để luồng có cái mà gửi tiếp
        vp.publish_frame(overlay_jpeg, clean_jpeg)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return sent


sent = asyncio.run(pull_mjpeg(overlay=1, want_frames=2))

start = next((m for m in sent if m["type"] == "http.response.start"), None)
headers = {k.decode(): v.decode() for k, v in (start or {}).get("headers", [])}
check("luồng MJPEG trả 200", start is not None and start["status"] == 200, str(start))
check("đúng content-type multipart",
      headers.get("content-type", "").startswith("multipart/x-mixed-replace"), str(headers))
check("khai báo boundary cho trình duyệt", "boundary=frame" in headers.get("content-type", ""),
      headers.get("content-type", ""))
check("tắt buffering của proxy", headers.get("x-accel-buffering") == "no", str(headers))

payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
check("có ranh giới khung hình", payload.count(b"--frame") >= 1, str(payload[:80]))
check("mỗi khung khai báo image/jpeg", b"Content-Type: image/jpeg" in payload)
check("mỗi khung khai báo Content-Length", b"Content-Length:" in payload)
check("thân khung là JPEG thật (magic bytes)", b"\xff\xd8\xff" in payload)
check("gửi được nhiều khung liên tiếp", payload.count(b"--frame") >= 2,
      str(payload.count(b"--frame")))
check("luồng overlay=1 mang đúng bản có lớp phủ", overlay_jpeg in payload)

sent_clean = asyncio.run(pull_mjpeg(overlay=0, want_frames=1))
payload_clean = b"".join(m.get("body", b"") for m in sent_clean
                         if m["type"] == "http.response.body")
check("luồng overlay=0 mang bản gốc, không phải bản có lớp phủ",
      clean_jpeg in payload_clean and overlay_jpeg not in payload_clean)

# ================================================================ hợp đồng
print("\n[7] Đối chiếu với openapi.yaml")

spec = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi.yaml"))
declared = set(spec["paths"])
implemented = {r.path.replace("/api/v1", "", 1) for r in api.app.routes
               if hasattr(r, "path") and r.path.startswith("/api/v1")}

for path in ["/cameras/{camera_id}/zones", "/zones/{zone_id}",
             "/cameras/{camera_id}/stream.mjpg", "/cameras/{camera_id}/snapshot"]:
    check(f"{path} có trong hợp đồng", path in declared)
    check(f"{path} đã hiện thực", path in implemented, str(sorted(implemented)))

zone_schema = spec["components"]["schemas"]["ZoneInput"]
check("hợp đồng khai đủ trường của ZoneInput",
      {"name", "kind", "rule", "points", "detect_human", "detect_object", "enabled"}
      <= set(zone_schema["properties"]), str(set(zone_schema["properties"])))
check("hợp đồng liệt kê đúng ba loại luật",
      set(spec["components"]["schemas"]["ZoneRule"]["enum"])
      == {"attendance_area", "restricted_area", "crossing_line"})

reset_zones()

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
