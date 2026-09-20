"""Gọi thử MỌI route để bắt lỗi chỉ lộ ra lúc chạy.

Bài này không kiểm nghiệp vụ. Nó chỉ khẳng định: không route nào ném
NameError, AttributeError hay TypeError — loại lỗi mà đọc code và kiểm cú pháp
không thấy, chỉ hiện khi có ai đó gọi vào.

Lý do có bài này: khi tách đa camera, dòng khai báo ``current_video_path`` bị
xoá nhưng vài route cũ vẫn đọc nó. Bộ test cũ không gọi các route đó nên không
ai phát hiện, mãi tới lúc người dùng bấm nút mới lòi ra NameError.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from starlette.routing import Mount

import app.api as api

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


client = TestClient(api.app, raise_server_exceptions=False)

# Bài này gọi cả POST/PATCH/DELETE nên có sửa dữ liệu. Chụp lại các file cấu
# hình trước khi chạy và trả về nguyên trạng sau khi xong, nếu không bộ test
# chạy sau sẽ đỏ vì camera mặc định bị xoá mất.
DATA_FILES = ["cameras.json", "schedules.json", "zone_rules.json", "attendance_logs.json"]
_backup = {}
for _name in DATA_FILES:
    _path = api.data_path / _name
    _backup[_name] = _path.read_text(encoding="utf-8") if _path.exists() else None


def restore_data():
    for name, content in _backup.items():
        path = api.data_path / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content, encoding="utf-8")

# Giá trị mẫu cho các tham số đường dẫn. Không tồn tại cũng được — mục tiêu là
# route chạy tới nơi và trả mã lỗi tử tế, chứ không phải nổ giữa chừng.
PATH_VALUES = {
    # Id giả: route vẫn chạy hết thân hàm rồi trả 404, mà không xoá camera thật
    "camera_id": "cam_smoke_khong_co",
    "zone_id": "zone_khong_co",
    "event_id": "evt_khong_co",
    "session_id": "ses_khong_co",
    "schedule_id": "sch_khong_co",
    "person_id": "per_khong_co",
    "sch_id": "sch_khong_co",
}

# Thân request tối thiểu để qua được tầng kiểm dữ liệu, tới được thân hàm
BODIES = {
    ("/api/v1/auth/login", "POST"): {"username": "cbqh", "password": "sai"},
    ("/api/v1/events/{event_id}/ack", "POST"): {"acked_by": "Kiểm thử"},
    ("/api/v1/cameras", "POST"): {"name": "x", "source_type": "file", "source_uri": "/tmp/x.mp4"},
    ("/api/v1/cameras/{camera_id}", "PATCH"): {"name": "x"},
    ("/api/v1/cameras/{camera_id}/zones", "POST"): {
        "name": "x", "kind": "polygon", "rule": "restricted_area",
        "points": [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.1}, {"x": 0.4, "y": 0.4}]},
    ("/api/v1/zones/{zone_id}", "PATCH"): {"name": "x"},
    ("/api/v1/schedules", "POST"): {"name": "x", "start_time": "07:00", "end_time": "11:30"},
    ("/api/v1/schedules/{schedule_id}", "PATCH"): {"name": "x"},
    ("/api/zones", "POST"): {"polygon_points": []},
}

# Route bỏ qua kèm lý do — không phải bỏ vì ngại, mà vì không gọi được kiểu này
SKIP = {
    "/ws": "WebSocket, không gọi bằng HTTP thường",
    "/api/v1/events/stream": "SSE chạy vô hạn, đã có bài kiểm riêng ở test_api",
    "/api/v1/cameras/{camera_id}/stream.mjpg": "MJPEG chạy vô hạn, đã kiểm ở test_zones_stream",
    "/api/faces/register": "cần ảnh thật, đã kiểm ở luồng đăng ký",
    "/api/upload": "cần file thật",
    "/api/upload_chunk": "cần file thật",
}

routes = []
for route in api.app.routes:
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not path or not methods or isinstance(route, Mount):
        continue
    for method in sorted(methods - {"HEAD", "OPTIONS"}):
        routes.append((path, method))

print(f"\nGọi thử {len(routes)} route\n")

called = skipped = 0
for path, method in sorted(routes):
    if path in SKIP:
        skipped += 1
        continue

    url = path
    for name, value in PATH_VALUES.items():
        url = url.replace("{" + name + "}", value)
    if "{" in url:
        skipped += 1
        print(f"  BỎ QUA  {method:6s} {path}  (tham số đường dẫn chưa khai giá trị mẫu)")
        continue

    body = BODIES.get((path, method))
    try:
        response = client.request(method, url, json=body)
    except Exception as e:
        check(f"{method:6s} {path}", False, f"NÉM LỖI: {type(e).__name__}: {e}")
        continue

    called += 1
    # 5xx nghĩa là route nổ bên trong. 4xx là bình thường: dữ liệu mẫu không
    # tồn tại nên 404/409/422 mới đúng.
    ok = response.status_code < 500
    detail = ""
    if not ok:
        detail = response.text[:300]
    check(f"{method:6s} {path} -> {response.status_code}", ok, detail)

print(f"\nĐã gọi {called} route, bỏ qua {skipped} route có lý do.")

# Các route cũ hay bị bỏ quên nhất: phải chắc chắn có mặt trong danh sách trên
print("\nRoute cũ dễ bị bỏ quên khi refactor")
for path, method in [("/api/status", "GET"), ("/api/snapshot", "GET"),
                     ("/api/start", "POST"), ("/api/stop", "POST"),
                     ("/api/set-baseline", "POST"), ("/api/alerts", "GET"),
                     ("/api/attendance/status", "GET"), ("/api/attendance-logs", "GET"),
                     ("/api/zones", "GET"), ("/api/time", "GET")]:
    check(f"{method} {path} có trong danh sách đã gọi", (path, method) in routes)

restore_data()

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Mọi route đều chạy được, không route nào nổ 5xx.")
