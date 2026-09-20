# Chỉnh sửa Horus theo phản hồi nghiệm thu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sửa 30 điểm phản hồi nghiệm thu của khách cho giao diện Horus AI Web: gộp màn Lịch & Tiến độ với Giám sát quân số, sửa các lỗi khiến bộ lọc / ảnh bằng chứng / tên camera không hoạt động, và bổ sung bộ lọc, phóng to camera, đổi mật khẩu.

**Architecture:** Phần lớn phản hồi bắt nguồn từ bốn lỗi thật ở tầng dữ liệu và một hàm JS chưa bao giờ được định nghĩa. Nên plan sửa gốc trước (chuẩn hoá bản ghi ca huấn luyện, đếm quân số trực tiếp, hộp xem ảnh dùng chung), rồi mới đổi bố cục màn. Không thêm thư viện: phóng to dùng Fullscreen API và `transform: scale()` sẵn có của trình duyệt.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, OpenCV; giao diện là HTML/CSS/JS thuần trong `static/`, không build step, không framework.

**Spec:** `Những vấn đề cần chỉnh sửa Horus.pdf` (thư mục gốc repo, 9 trang, kèm ảnh chụp màn hình). Trích văn bản: `pdftotext -layout "Những vấn đề cần chỉnh sửa Horus.pdf" -`.

## Global Constraints

- Mọi chữ hiển thị cho người dùng và mọi chú thích trong code viết bằng **tiếng Việt**, theo đúng văn phong đang có trong repo.
- **Không thêm thư viện mới** ở cả hai phía. Giao diện không có bước build; `static/app.js` là một file script thuần, mọi hàm gọi từ HTML `onclick` phải được gán `window.<tên hàm> = <tên hàm>;`.
- Test trong repo **không phải pytest**: mỗi file là một script chạy bằng `python tests/<file>.py`, dùng helper `check(name, cond, extra="")` và `sys.exit(1)` khi có lỗi. Test mới phải theo đúng khuôn đó.
- Test giao diện `tests/test_ui.mjs` cần **máy chủ đang chạy ở cổng 8199** (`python main.py`) và gói `jsdom` (`npm install jsdom`).
- Test backend ghi đè `data/cameras.json`, `data/schedules.json`, `data/zone_rules.json`. Luôn gọi `reset()` trước và sau mỗi nhóm.
- Màu sắc lấy từ token trong `:root` của `static/style.css`: `--primary-green: #0a8f4c`, `--danger-red: #dc2626`, `--warning-orange: #d97706`, `--military-muted: #64748b`, `--card-border: #e2e8f0`, `--font-mono: 'JetBrains Mono', monospace`.
- Dấu phiên bản của `app.js` / `style.css` tự tính từ mtime trong `_asset_version()` (`app/api.py:163`) — **không** sửa tay chuỗi `?v=` trong `index.html`.
- Phân quyền giao diện dùng `class="role-only" data-role="qtht"`; `applyRole()` ẩn/hiện theo `el.dataset.role`. Đây **không** phải ranh giới bảo mật, chỉ để hiện đúng menu.
- Mọi mốc thời gian lấy qua `app/clock.py` (`clock.now()` trả `datetime` naive theo giờ Việt Nam, `clock.iso()` trả ISO kèm `+07:00`). Không dùng `datetime.now()` trần.
- Hai loại huấn luyện hợp lệ: `dao_tao`, `chien_dau` (`TRAINING_TYPES` — `app/schemas.py:143`).
- Camera mặc định: `DEFAULT_CAMERA_ID = "cam_01"` (`app/attendance.py:17`), trùng `CAMERA_ID` trong `app/events.py:25`.

---

## File Structure

| File | Trách nhiệm | Việc trong plan |
|---|---|---|
| `app/attendance.py` | Logic ca huấn luyện, phiên điểm danh, biên bản | Thêm `normalize_schedule()`; dùng trong `schedules_with_state()` |
| `app/api.py` | Toàn bộ route HTTP | Đếm quân số trực tiếp, bộ lọc khoảng thời gian, trạng thái an toàn, route đổi mật khẩu |
| `app/auth.py` | Tài khoản POC | Chuyển sang kho `data/users.json`, thêm `change_password()` / `update_profile()` |
| `app/schemas.py` | Pydantic model cho đầu vào API | Thêm `PasswordChangeInput`, `ProfilePatch` |
| `static/index.html` | Khung mọi màn hình | Bỏ tab Giám sát quân số, thêm hộp phóng to, đổi cột bảng, thêm bộ lọc, avatar sidebar |
| `static/app.js` | Toàn bộ hành vi giao diện | `openEvidence()`, `makeZoomable()`, định tuyến mới, render bảng mới |
| `static/style.css` | Toàn bộ trình bày | Lớp phủ phóng to, avatar sidebar, banner cảnh báo giữa trên, cột sự kiện cao hết màn |
| `tests/test_config_api.py` | Kiểm endpoint cấu hình | Thêm nhóm kiểm chuẩn hoá ca, quân số trực tiếp, trạng thái an toàn, bộ lọc |
| `tests/test_auth_api.py` | **Tạo mới** — kiểm đăng nhập / đổi mật khẩu | Toàn bộ Task 6 |
| `tests/test_ui.mjs` | Chạy giao diện thật trong jsdom | Cập nhật theo bố cục mới, thêm kiểm cho từng màn |

**Thứ tự thực hiện:** Task 1–6 là backend (sửa gốc). Task 7 là hạ tầng giao diện dùng chung. Task 8–17 là từng màn. Task 9 (bỏ tab) phải chạy **sau** Task 8 (dựng xong màn chi tiết gộp), nếu không sẽ có lúc không vào được màn chi tiết quân số.

---

## Nguyên nhân gốc đã xác minh

Ghi lại để người thực hiện không đi tìm lại:

1. **`openEvidence()` chưa bao giờ được định nghĩa.** Được gọi ở `static/app.js:697, 1924, 2226, 2343, 2539` nhưng cả file chỉ có `openEvidenceModal`. Mỗi lần bấm ảnh bằng chứng là một `ReferenceError`. → Task 7.
2. **Ca huấn luyện cũ thiếu trường.** `training_type`, `camera_id`, `check_window_mins`, `late_tolerance_mins`, `lesson_name`, … không có trong bản ghi. Lõi AI tự chịu được (`_tolerance_mins` — `app/attendance.py:54`), nhưng API trả nguyên bản ghi nên giao diện hiện `undefined phút`, bộ lọc `training_type` bỏ sót toàn bộ ca, bảng an toàn không có tên bài học, bảng thời khoá biểu không có tên camera. → Task 1.
3. **`Camera: 1/1 Trực tuyến` là chữ chết trong HTML** (`static/index.html:133`), không có `id`, không ai cập nhật. → Task 11.
4. **`present_total` cộng `checks.start.present` của biên bản hôm nay** (`app/api.py:945`) nên bằng 0 cho tới khi cửa sổ điểm danh đóng, trong khi thanh trên cùng đã có số đếm trực tiếp. → Task 2.
5. **Bảng thời khoá biểu chỉ có nút xoá** (`renderSchedulesTable` — `static/app.js:1665`), không có nút sửa, nên ca đã tạo không bao giờ gán được camera. → Task 17.

---

## Task 1: Chuẩn hoá bản ghi ca huấn luyện

Sửa nguyên nhân gốc số 2. Một hàm duy nhất, mọi route đọc ca đều đi qua.

**Files:**
- Modify: `app/attendance.py` (thêm hằng + hàm sau `_schedule_window_mins`, dòng ~70; sửa `schedules_with_state`, dòng 633-645)
- Modify: `app/api.py:715-717` (`_find_schedule`) và dòng 26 (import)
- Test: `tests/test_config_api.py` (thêm nhóm mới trước khối `reset()` cuối file)

**Interfaces:**
- Produces: `app.attendance.normalize_schedule(schedule: dict) -> dict` — trả bản sao có đủ `training_type`, `camera_id`, `check_window_mins`, `late_tolerance_mins`, `early_leave_tolerance_mins` và các trường chữ (`lesson_name`, `instructor`, `field`, `class_name`, `shift`, `unit`) không bao giờ là `None`.
- Produces: `app.attendance.DEFAULT_TRAINING_TYPE = "dao_tao"`.
- Consumes: `_schedule_window_mins()`, `_tolerance_mins()`, `DEFAULT_CAMERA_ID` — đã có sẵn trong `app/attendance.py`.

- [ ] **Step 1: Viết test cho ca cũ thiếu trường**

Thêm vào cuối `tests/test_config_api.py`, ngay **trước** dòng `reset()` cuối cùng:

```python
# ================================================ ca cũ thiếu trường
print("\n[6] Ca cũ thiếu trường vẫn hiển thị đủ")

reset()
write_json_list(api.schedules_file, [{
    "id": "sch_cu",
    "name": "Ca sáng - Huấn luyện điều lệnh",
    "start_time": "06:00",
    "end_time": "11:30",
    "unit": "Đại đội 1",
    "shift": "Ca sáng",
    "required_count": 45,
}])

row = client.get("/api/v1/schedules/sch_cu").json()
check("ca cũ được gán loại huấn luyện mặc định",
      row.get("training_type") == "dao_tao", str(row)[:200])
check("ca cũ có cửa sổ điểm danh, không để undefined",
      row.get("check_window_mins") == 5, str(row.get("check_window_mins")))
check("ca cũ có dung sai đi chậm và về sớm",
      row.get("late_tolerance_mins") == 5 and row.get("early_leave_tolerance_mins") == 5,
      str(row)[:200])
check("ca cũ được gán camera mặc định", row.get("camera_id") == CAMERA_ID,
      str(row.get("camera_id")))
check("trường giao diện thiếu thì là chuỗi rỗng, không phải None",
      row.get("lesson_name") == "" and row.get("instructor") == "", str(row)[:200])

tat_ca = client.get("/api/v1/summary/training").json()["sessions"]
dao_tao = client.get("/api/v1/summary/training?training_type=dao_tao").json()["sessions"]
chien_dau = client.get("/api/v1/summary/training?training_type=chien_dau").json()["sessions"]
check("đào tạo + chiến đấu = tất cả",
      len(dao_tao) + len(chien_dau) == len(tat_ca),
      f"{len(dao_tao)} + {len(chien_dau)} != {len(tat_ca)}")
check("ca cũ rơi vào nhóm đào tạo chứ không biến mất",
      len(dao_tao) == 1, str(len(dao_tao)))
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
cd /home/ubuntu/minhdq/vision-agent-deployment/military-attendance-check
python tests/test_config_api.py
```

Kỳ vọng: FAIL ở "ca cũ được gán loại huấn luyện mặc định" (`None`), "ca cũ có cửa sổ điểm danh" (`None`), "đào tạo + chiến đấu = tất cả" (`0 + 0 != 1`).

- [ ] **Step 3: Thêm `normalize_schedule` vào `app/attendance.py`**

Chèn ngay sau hàm `_schedule_window_mins` (kết thúc ở dòng ~69):

```python
DEFAULT_TRAINING_TYPE = "dao_tao"

# Trường chỉ để hiển thị: lõi AI không đọc, nhưng thiếu thì giao diện hiện trống
_DISPLAY_FIELDS = ("lesson_name", "instructor", "field", "class_name", "shift", "unit")


def normalize_schedule(schedule: dict) -> dict:
    """Điền các trường một ca cũ còn thiếu, trả về bản sao.

    Lõi AI vốn đã tự chịu được ca thiếu trường (xem ``_tolerance_mins``), nhưng
    API trả nguyên bản ghi nên giao diện hiện 'undefined phút' và bộ lọc theo
    loại huấn luyện bỏ sót sạch ca cũ. Chuẩn hoá tại đúng một chỗ để mọi route
    đọc ca đều thấy cùng một dạng dữ liệu.
    """
    row = dict(schedule)
    row["training_type"] = row.get("training_type") or DEFAULT_TRAINING_TYPE
    row["camera_id"] = row.get("camera_id") or DEFAULT_CAMERA_ID
    row["check_window_mins"] = _schedule_window_mins(schedule)
    row["late_tolerance_mins"] = _tolerance_mins(schedule, "late_tolerance_mins")
    row["early_leave_tolerance_mins"] = _tolerance_mins(schedule, "early_leave_tolerance_mins")
    for name in _DISPLAY_FIELDS:
        row[name] = row.get(name) or ""
    return row
```

- [ ] **Step 4: Dùng nó trong `schedules_with_state`**

Trong `app/attendance.py`, hàm `schedules_with_state` (dòng ~633), đổi đúng một dòng:

```python
        for schedule in self._load_schedules():
            row = normalize_schedule(schedule)      # was: row = dict(schedule)
            row.update(schedule_runtime_state(schedule, now))
```

Giữ nguyên `schedule_runtime_state(schedule, now)` — nó nhận bản gốc và đã tự xử lý trường thiếu.

- [ ] **Step 5: Dùng nó trong `_find_schedule` của API**

Trong `app/api.py`, sửa import ở dòng 26:

```python
from app.attendance import AttendanceManager, normalize_schedule, person_label
```

và sửa `_find_schedule` (dòng 715):

```python
def _find_schedule(schedule_id: str) -> Optional[dict]:
    row = next((s for s in read_json_list(data_path / "schedules.json")
                if s.get("id") == schedule_id), None)
    return normalize_schedule(row) if row is not None else None
```

- [ ] **Step 6: Chạy lại toàn bộ test backend**

```bash
python tests/test_config_api.py
python tests/test_api.py
python tests/test_ai.py
python tests/test_smoke_routes.py
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 7: Commit**

```bash
git add app/attendance.py app/api.py tests/test_config_api.py
git commit -m "fix: ca huấn luyện cũ thiếu trường làm bộ lọc loại và tên camera trống

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Quân số thực tế trực tiếp và số camera trực tuyến

Sửa nguyên nhân gốc số 4. Khách: *"Quân số thực tế phải hiển thị số lượng người đang có trong toàn bộ ca đang diễn ra"* và *"hiển thị số lượng camera đang trực tuyến trên tổng số camera hiện có"*.

**Files:**
- Modify: `app/api.py` — thêm `_live_count()` cạnh `_camera_status()` (dòng ~1160), sửa `_camera_out()` (dòng 1167), sửa `v1_training_summary()` (dòng 920-987)
- Test: `tests/test_config_api.py`

**Interfaces:**
- Produces: `_live_count(camera_id: str) -> int` — số người camera đang thấy ngay lúc này, 0 nếu camera chưa chạy.
- Produces: mỗi phần tử của `GET /api/v1/cameras` có thêm khoá `live_count: int`.
- Produces: `GET /api/v1/summary/training` trả thêm `stats.cameras_online: int`, `stats.cameras_total: int`, và mỗi session có `live_present: int`.
- Consumes: `runtimes: Dict[str, CameraRuntime]` (`app/api.py:105`), `runtime.monitor.get_status()` trả dict có `current_count`.

- [ ] **Step 1: Viết test**

Thêm vào `tests/test_config_api.py` sau nhóm `[6]`:

```python
# ============================== quân số trực tiếp và đếm camera
print("\n[7] Quân số thực tế và số camera trực tuyến")

cams = client.get("/api/v1/cameras").json()["items"]
check("mỗi camera mang sẵn số người đang thấy",
      all("live_count" in c for c in cams), str(cams)[:200])
check("camera chưa chạy thì đếm 0",
      all(c["live_count"] == 0 for c in cams), str(cams)[:200])

stats = client.get("/api/v1/summary/training").json()["stats"]
check("tổng hợp có số camera trực tuyến trên tổng số",
      stats.get("cameras_total") == len(cams) and stats.get("cameras_online") == 0,
      str(stats))

sessions = client.get("/api/v1/summary/training").json()["sessions"]
check("mỗi ca mang số quân đang thấy trực tiếp",
      all("live_present" in s for s in sessions), str(sessions)[:200])
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
python tests/test_config_api.py
```

Kỳ vọng: FAIL ở "mỗi camera mang sẵn số người đang thấy" và "tổng hợp có số camera trực tuyến trên tổng số".

- [ ] **Step 3: Thêm `_live_count` và gắn vào `_camera_out`**

Trong `app/api.py`, chèn ngay sau `_camera_status()` (dòng ~1165):

```python
def _live_count(camera_id: str) -> int:
    """Số người camera đang thấy ngay lúc này. Camera chưa chạy thì 0.

    Biên bản điểm danh chỉ có số sau khi cửa sổ điểm danh đóng, nên màn tổng hợp
    phải hỏi thẳng luồng đang chạy mới ra được 'quân số thực tế'.
    """
    runtime = runtimes.get(camera_id)
    if runtime is None:
        return 0
    try:
        return int(runtime.monitor.get_status().get("current_count") or 0)
    except (TypeError, ValueError):
        return 0
```

và thêm một khoá vào `_camera_out()`:

```python
def _camera_out(camera: dict) -> dict:
    """Bản ghi trả về giao diện, kèm trạng thái và đường dẫn dựng sẵn."""
    return {
        **camera,
        "status": _camera_status(camera),
        "live_count": _live_count(camera["id"]),
        "stream_url": f"/api/v1/cameras/{camera['id']}/stream.mjpg?overlay=1",
        "snapshot_url": f"/api/v1/cameras/{camera['id']}/snapshot?overlay=0",
    }
```

- [ ] **Step 4: Đổi cách tính `present_total` trong `v1_training_summary`**

Trong `app/api.py`, hàm `v1_training_summary`: thay biến đếm và vòng lặp.

Đổi dòng khởi tạo (dòng ~929):

```python
    sessions, running, required_total, violations = [], 0, 0, 0
    active_cameras = set()          # gộp theo camera để không đếm trùng người
```

Trong vòng lặp, **bỏ** dòng `present_total += checks.get("start", {}).get("present", 0)` và thay bằng:

```python
        live_present = 0
        if row.get("state") in ACTIVE_STATES:
            running += 1
            active_cameras.add(row.get("camera_id") or CAMERA_ID)
            live_present = _live_count(row.get("camera_id") or CAMERA_ID)
        required_total += required or 0
```

(dòng `if row.get("state") in ("check_start", "running", "check_end"): running += 1` cũ bị thay bằng đoạn trên — dùng `ACTIVE_STATES` cho khỏi lặp chuỗi.)

Thêm `"live_present": live_present,` vào dict `sessions.append({...})`, ngay dưới `"present_end"`.

Và sửa khối `return`:

```python
    all_cameras = _load_cameras()
    return {
        "date": today,
        "training_type": training_type,
        "stats": {
            "running_sessions": running,
            "present_total": sum(_live_count(cid) for cid in active_cameras),
            "required_total": required_total,
            "violation_total": violations,
            "cameras_online": sum(1 for c in all_cameras if _camera_status(c) == "online"),
            "cameras_total": len(all_cameras),
            "overall_progress_pct": round(sum(progress_values) / len(progress_values), 1)
            if progress_values else 0.0,
        },
        "sessions": sessions,
    }
```

Giữ `overall_progress_pct` lại trong payload: Task 10 bỏ nó khỏi giao diện, nhưng `tests/test_api.py` và `docs/api/openapi.yaml` còn tham chiếu — bỏ khỏi API là việc khác.

- [ ] **Step 5: Chạy test**

```bash
python tests/test_config_api.py && python tests/test_api.py && python tests/test_smoke_routes.py
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api.py tests/test_config_api.py
git commit -m "feat: quân số thực tế đếm trực tiếp từ camera đang chạy, kèm số camera trực tuyến

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Bỏ mức trạng thái "Có vi phạm đã xử lý"

Khách: *"Bỏ phần thông báo 'Có vi phạm đã xử lý' đi hộ em."*

**Files:**
- Modify: `app/api.py:884-885` (trong `v1_safety_summary`)
- Test: `tests/test_config_api.py`

**Interfaces:**
- Produces: `GET /api/v1/summary/safety` chỉ còn hai giá trị `state`: `"danger"` (còn vi phạm chưa xử lý) và `"normal"`. `state_label` tương ứng `"Cảnh báo nguy hiểm"` / `"Bình thường"`. Mức `"warning"` / `"Có vi phạm đã xử lý"` bị loại bỏ.

- [ ] **Step 1: Viết test**

Thêm vào `tests/test_config_api.py` sau nhóm `[7]`:

```python
# ===================================== trạng thái an toàn chỉ hai mức
print("\n[8] Trạng thái an toàn chỉ còn hai mức")

ev = api.events.emit("INTRUSION", "Phát hiện 1 đối tượng đi vào vùng cấm.",
                     severity="critical")
r = client.get("/api/v1/summary/safety").json()
check("còn vi phạm chưa xử lý thì báo động",
      r["state"] == "danger" and r["state_label"] == "Cảnh báo nguy hiểm", str(r["state_label"]))

api.events.ack(ev["id"], "Chỉ huy trực ban")
r = client.get("/api/v1/summary/safety").json()
check("xử lý xong thì về bình thường, không còn mức 'đã xử lý'",
      r["state"] == "normal" and r["state_label"] == "Bình thường", str(r))
check("xử lý xong thì không còn cảnh báo đang treo",
      r["active_intrusion"] is None and r["pending_count"] == 0, str(r["pending_count"]))
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
python tests/test_config_api.py
```

Kỳ vọng: FAIL ở "xử lý xong thì về bình thường" — nhận được `warning` / `"Có vi phạm đã xử lý"`.

- [ ] **Step 3: Sửa `v1_safety_summary`**

Trong `app/api.py`, thay hai dòng ở ~884:

```python
    # Chỉ hai mức: còn vi phạm chưa xử lý là báo động, xử lý xong là bình thường.
    # Mức trung gian "có vi phạm đã xử lý" bị bỏ theo yêu cầu nghiệm thu: xong
    # rồi thì không việc gì phải để một cái nhãn vàng treo trên màn suốt ngày.
    state = "danger" if pending else "normal"
    labels = {"danger": "Cảnh báo nguy hiểm", "normal": "Bình thường"}
```

- [ ] **Step 4: Chạy test**

```bash
python tests/test_config_api.py && python tests/test_api.py
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api.py tests/test_config_api.py
git commit -m "fix: bỏ mức trạng thái an toàn 'có vi phạm đã xử lý'

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Lọc theo khoảng thời gian, ca, trạng thái và từ khoá cho tổng hợp huấn luyện

Khách: *"Phần lọc theo thời gian, anh cho em lọc theo khoảng thời gian nữa ạ, có bộ lọc theo trạng thái, theo ca và thanh tìm kiếm."* Và: *"thanh tiến độ thực tế là tiến độ tính theo thời gian từ lúc bắt đầu đến lúc kết thúc để biết lớp đang học bao giờ kết thúc."*

Thời khoá biểu là mẫu lặp hằng ngày, nên "khoảng thời gian" nghĩa là dựng một dòng cho mỗi cặp (ca, ngày) trong khoảng. Tiến độ theo đồng hồ tính ở máy chủ vì phải xử lý ca vắt qua nửa đêm.

**Files:**
- Modify: `app/api.py` — thêm import `datetime`, thêm `_range_days()`, `_time_on_day()`, `_time_progress()`, `_state_on_day()`; viết lại `v1_training_summary()` (dòng 920-987)
- Test: `tests/test_config_api.py`

**Interfaces:**
- Produces: `GET /api/v1/summary/training` nhận thêm `date_from`, `date_to` (YYYY-MM-DD), `shift`, `state`, `q`.
- Produces: mỗi session có thêm `day: str` (YYYY-MM-DD), `time_progress_pct: float`, `elapsed_minutes: int`, `total_minutes: int`, `remaining_minutes: int`.
- Produces: `_range_days(date_from, date_to, default_day) -> List[str]`, tối đa 31 ngày.
- Consumes: `normalize_schedule` (Task 1) đã bảo đảm `shift`, `lesson_name`, `training_type` không bao giờ `None`.

- [ ] **Step 1: Viết test**

Thêm vào `tests/test_config_api.py` sau nhóm `[8]`:

```python
# ====================================== bộ lọc của màn lịch & tiến độ
print("\n[9] Lọc lịch theo khoảng thời gian, ca, trạng thái, từ khoá")

reset()
write_json_list(api.schedules_file, [
    {"id": "sch_sang", "name": "Huấn luyện điều lệnh", "start_time": "06:00",
     "end_time": "11:30", "unit": "Đại đội 1", "shift": "Ca sáng",
     "training_type": "dao_tao", "lesson_name": "Bài 1 — Đội ngũ", "required_count": 45},
    {"id": "sch_chieu", "name": "Bắn súng tiểu liên AK", "start_time": "13:30",
     "end_time": "17:30", "unit": "Đại đội 2", "shift": "Ca chiều",
     "training_type": "chien_dau", "lesson_name": "Bài 5 — Ngắm bắn", "required_count": 50},
])

one_day = client.get("/api/v1/summary/training").json()["sessions"]
check("không khai khoảng thì chỉ dựng một ngày", len(one_day) == 2, str(len(one_day)))
check("mỗi dòng mang ngày của nó", all(s.get("day") for s in one_day), str(one_day)[:200])

# Mốc ngày tính lùi từ hôm nay để test không hỏng khi chạy ở thời điểm khác
from datetime import date, timedelta
hom_nay = date.today()
qua_3 = (hom_nay - timedelta(days=3)).isoformat()
qua_1 = (hom_nay - timedelta(days=1)).isoformat()
qua_5 = (hom_nay - timedelta(days=5)).isoformat()

r = client.get(f"/api/v1/summary/training?date_from={qua_3}&date_to={qua_1}").json()
check("khoảng 3 ngày dựng 3 dòng cho mỗi ca",
      len(r["sessions"]) == 6, str(len(r["sessions"])))
check("ngày trong quá khứ thì ca đã kết thúc",
      all(s["state"] == "finished" for s in r["sessions"]), str(r["sessions"])[:200])

r = client.get("/api/v1/summary/training?shift=Ca%20chiều").json()["sessions"]
check("lọc theo ca", len(r) == 1 and r[0]["shift"] == "Ca chiều", str(r)[:200])

r = client.get("/api/v1/summary/training?q=tiểu%20liên").json()["sessions"]
check("tìm theo tên ca", len(r) == 1 and r[0]["id"].startswith("sch_chieu"), str(r)[:200])

r = client.get("/api/v1/summary/training?q=ngắm%20bắn").json()["sessions"]
check("tìm theo tên bài học", len(r) == 1, str(r)[:200])

r = client.get(f"/api/v1/summary/training?date_from={qua_3}&date_to={qua_1}"
               "&state=finished").json()["sessions"]
check("lọc theo trạng thái", len(r) == 6, str(len(r)))

r = client.get("/api/v1/summary/training?date_from=2020-01-01&date_to=2030-01-01").json()
check("khoảng quá dài bị chặn ở 31 ngày",
      len(r["sessions"]) == 62, str(len(r["sessions"])))

past = client.get(f"/api/v1/summary/training?date_from={qua_5}&date_to={qua_5}"
                  ).json()["sessions"][0]
check("ngày đã qua thì tiến độ theo giờ là 100%",
      past["time_progress_pct"] == 100.0, str(past["time_progress_pct"]))
check("có tổng số phút và số phút còn lại của ca",
      past["total_minutes"] == 330 and past["remaining_minutes"] == 0, str(past))

reset()
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
python tests/test_config_api.py
```

Kỳ vọng: FAIL ở "mỗi dòng mang ngày của nó", "khoảng 3 ngày dựng 3 dòng", "lọc theo ca", "tìm theo tên ca" — tham số bị bỏ qua.

- [ ] **Step 3: Thêm import và ba hàm trợ giúp vào `app/api.py`**

Sửa import ở đầu file (sau dòng 16 `from pathlib import Path`):

```python
from datetime import date as date_cls, datetime, time as time_cls, timedelta
```

Đổi import từ `app.attendance` (dòng 26) để lấy thêm nhãn trạng thái:

```python
from app.attendance import (STATE_LABELS, AttendanceManager, normalize_schedule,
                            person_label)
```

Chèn khối sau ngay **trên** `@app.get("/api/v1/summary/training")` (dòng ~920):

```python
# Thời khoá biểu là mẫu lặp hằng ngày, nên "lọc theo khoảng thời gian" nghĩa là
# dựng một dòng cho mỗi cặp (ca, ngày). Chặn ở 31 ngày cho khỏi dựng vài nghìn
# dòng khi người dùng gõ nhầm năm.
MAX_RANGE_DAYS = 31


def _range_days(date_from: Optional[str], date_to: Optional[str],
                default_day: str) -> List[str]:
    """Danh sách ngày cần dựng dòng lịch. Không khai khoảng thì chỉ một ngày."""
    if not date_from and not date_to:
        return [default_day]
    try:
        start = date_cls.fromisoformat(date_from or date_to)
        end = date_cls.fromisoformat(date_to or date_from)
    except ValueError:
        return [default_day]
    if end < start:
        start, end = end, start
    span = min((end - start).days, MAX_RANGE_DAYS - 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(span + 1)]


def _time_on_day(day: str, hhmm) -> Optional[datetime]:
    """Ghép 'HH:MM' vào một ngày cụ thể."""
    parts = str(hhmm or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return datetime.combine(date_cls.fromisoformat(day),
                                time_cls(int(parts[0]), int(parts[1])))
    except ValueError:
        return None


def _time_progress(row: dict, day: str, now: datetime) -> dict:
    """Tiến độ theo đồng hồ: đã trôi bao nhiêu phần khung giờ của ca.

    Khác với ``progress_pct`` trong biên bản (tính theo số phút camera thực sự
    quan sát được), cái này chỉ nhìn đồng hồ — màn lịch cần biết 'lớp còn bao
    lâu nữa thì tan', không phải 'camera đã chạy bao lâu'.
    """
    start = _time_on_day(day, row.get("start_time"))
    end = _time_on_day(day, row.get("end_time"))
    if start is None or end is None:
        return {"time_progress_pct": 0.0, "elapsed_minutes": 0,
                "total_minutes": 0, "remaining_minutes": 0}
    if end <= start:
        end += timedelta(days=1)        # ca vắt qua nửa đêm
    total = max(1, int((end - start).total_seconds() // 60))
    elapsed = max(0, min(int((now - start).total_seconds() // 60), total))
    return {
        "time_progress_pct": round(elapsed * 100 / total, 1),
        "elapsed_minutes": elapsed,
        "total_minutes": total,
        "remaining_minutes": total - elapsed,
    }


def _state_on_day(row: dict, day: str, today: str) -> tuple:
    """Trạng thái của ca trong một ngày. Chỉ hôm nay mới có trạng thái sống."""
    if day == today:
        return row.get("state"), row.get("state_label")
    if day < today:
        return "finished", STATE_LABELS["finished"]
    return "upcoming", STATE_LABELS["upcoming"]
```

- [ ] **Step 4: Viết lại `v1_training_summary`**

Thay trọn hàm `v1_training_summary` (dòng 920 tới hết `return`) bằng:

```python
@app.get("/api/v1/summary/training")
async def v1_training_summary(training_type: Optional[str] = None,
                              date: Optional[str] = None,
                              date_from: Optional[str] = None,
                              date_to: Optional[str] = None,
                              shift: Optional[str] = None,
                              state: Optional[str] = None,
                              q: Optional[str] = None):
    """Tổng hợp lịch và tiến độ huấn luyện: chỉ số nhanh + danh sách buổi.

    ``training_type`` tách đào tạo và chiến đấu; bỏ trống thì lấy cả hai.
    ``date_from``/``date_to`` dựng một dòng cho mỗi cặp (ca, ngày) trong khoảng.
    """
    now = clock.now()
    today = now.date().isoformat()
    default_day = date or today
    days = _range_days(date_from, date_to, default_day)

    # Biên bản tra theo (mã ca, ngày) để mỗi dòng lấy đúng buổi của nó
    logs = {}
    for l in read_json_list(data_path / "attendance_logs.json"):
        key = (l.get("schedule_id"), l.get("date_iso") or str(l.get("started_at", ""))[:10])
        logs[key] = l

    needle = (q or "").strip().lower()
    rows = schedules_view.schedules_with_state(now)

    sessions, running, required_total, violations = [], 0, 0, 0
    active_cameras = set()
    for day in days:
        for row in rows:
            if training_type and row.get("training_type") != training_type:
                continue
            if shift and row.get("shift") != shift:
                continue
            if needle and needle not in (
                    f"{row.get('name', '')} {row.get('unit', '')} "
                    f"{row.get('lesson_name', '')} {row.get('instructor', '')}").lower():
                continue

            day_state, day_state_label = _state_on_day(row, day, today)
            if state and day_state != state:
                continue

            log = logs.get((row.get("id"), day), {})
            checks = log.get("checks", {})
            summary = log.get("attendance_summary") or {}
            required = log.get("required", row.get("required_count") or 0)
            camera_id = row.get("camera_id") or CAMERA_ID

            live_present = 0
            if day == today and day_state in ACTIVE_STATES:
                running += 1
                active_cameras.add(camera_id)
                live_present = _live_count(camera_id)

            required_total += required or 0
            violation_count = (summary.get("absent", 0) + summary.get("late", 0)
                               + summary.get("early_leave", 0))
            violations += violation_count

            sessions.append({
                "id": log.get("id", f"{row.get('id')}:{day}"),
                "schedule_id": row.get("id"),
                "day": day,
                "date": day,
                "name": row.get("name", ""),
                "shift": row.get("shift", ""),
                "unit": row.get("unit", ""),
                "training_type": row.get("training_type"),
                "camera_id": camera_id,
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
                "check_window_mins": row.get("check_window_mins"),
                "lesson_name": row.get("lesson_name"),
                "instructor": row.get("instructor"),
                "field": row.get("field"),
                "class_name": row.get("class_name"),
                "state": day_state,
                "state_label": day_state_label,
                "required": required,
                "present_start": checks.get("start", {}).get("present", 0),
                "present_end": checks.get("end", {}).get("present", 0),
                "live_present": live_present,
                "actual_minutes": log.get("actual_minutes", 0),
                "scheduled_minutes": log.get("scheduled_minutes", 0),
                "progress_pct": log.get("progress_pct", 0.0),
                "violation_count": violation_count,
                **_time_progress(row, day, now),
            })

    progress_values = [s["progress_pct"] for s in sessions if s["progress_pct"]]
    all_cameras = _load_cameras()
    return {
        "date": default_day,
        "date_from": days[0],
        "date_to": days[-1],
        "training_type": training_type,
        "stats": {
            "running_sessions": running,
            "present_total": sum(_live_count(cid) for cid in active_cameras),
            "required_total": required_total,
            "violation_total": violations,
            "cameras_online": sum(1 for c in all_cameras if _camera_status(c) == "online"),
            "cameras_total": len(all_cameras),
            "overall_progress_pct": round(sum(progress_values) / len(progress_values), 1)
            if progress_values else 0.0,
        },
        "sessions": sessions,
    }
```

Lưu ý: khối này đã bao gồm luôn thay đổi của Task 2 (`live_present`, `cameras_online`) — nếu Task 2 đã làm, chỉ cần đối chiếu cho khớp chứ không viết chồng.

- [ ] **Step 5: Chạy test**

```bash
python tests/test_config_api.py && python tests/test_api.py && python tests/test_smoke_routes.py
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api.py tests/test_config_api.py
git commit -m "feat: lọc lịch huấn luyện theo khoảng ngày, ca, trạng thái, từ khoá và tiến độ theo giờ

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Nhật ký điểm danh — bổ sung trường và bộ lọc

Khách: *"bổ sung mục lọc theo ca, theo khoảng thời gian và thanh tìm kiếm có thể tìm theo tên bài"*, *"thêm 1 cột tên bài học"*, và modal chi tiết phải có *"tên bài, người dạy, đơn vị, ngày, sĩ số chuẩn"* cùng bảng vi phạm có **chức vụ**.

Biên bản cũ không lưu `lesson_name` / `instructor` / `training_type`. Ghép từ thời khoá biểu lúc đọc thì bản ghi cũ cũng hiện đủ, không phải vá dữ liệu.

**Files:**
- Modify: `app/api.py:426-434` (`get_attendance_logs`)
- Test: `tests/test_config_api.py`

**Interfaces:**
- Produces: `GET /api/attendance-logs` nhận thêm `shift`, `date_from`, `date_to` (YYYY-MM-DD), `q`.
- Produces: mỗi bản ghi trả về có thêm `lesson_name`, `instructor`, `field`, `class_name`, `training_type`, `start_time`, `end_time` lấy từ ca tương ứng; và mỗi phần tử `attendance[].person` chắc chắn có khoá `rank` (chức vụ) — đã có sẵn từ `face_engine`, chỉ xác nhận không bị rơi.
- Consumes: `normalize_schedule` (Task 1).

- [ ] **Step 1: Viết test**

Thêm vào `tests/test_config_api.py` sau nhóm `[9]`:

```python
# ============================================ nhật ký điểm danh
print("\n[10] Nhật ký điểm danh: ghép tên bài và bộ lọc")

reset()
write_json_list(api.schedules_file, [
    {"id": "sch_sang", "name": "Huấn luyện điều lệnh", "start_time": "06:00",
     "end_time": "11:30", "unit": "Đại đội 1", "shift": "Ca sáng",
     "training_type": "dao_tao", "lesson_name": "Bài 1 — Đội ngũ",
     "instructor": "Đại uý Phạm Minh Đức", "required_count": 45},
])
write_json_list(api.data_path / "attendance_logs.json", [
    {"id": "log_1", "schedule_id": "sch_sang", "date": "18/09/2026",
     "date_iso": "2026-09-18", "shift": "Ca sáng", "schedule_name": "Huấn luyện điều lệnh",
     "unit": "Đại đội 1", "required": 45, "checks": {}},
    {"id": "log_2", "schedule_id": "sch_sang", "date": "19/09/2026",
     "date_iso": "2026-09-19", "shift": "Ca chiều", "schedule_name": "Huấn luyện điều lệnh",
     "unit": "Đại đội 2", "required": 45, "checks": {}},
])

rows = client.get("/api/attendance-logs").json()["data"]
check("nhật ký ghép được tên bài học từ ca",
      all(r.get("lesson_name") == "Bài 1 — Đội ngũ" for r in rows), str(rows)[:250])
check("nhật ký ghép được giáo viên phụ trách",
      rows[0].get("instructor") == "Đại uý Phạm Minh Đức", str(rows[0])[:250])
check("nhật ký mang loại huấn luyện",
      rows[0].get("training_type") == "dao_tao", str(rows[0].get("training_type")))

r = client.get("/api/attendance-logs?shift=Ca%20chiều").json()["data"]
check("lọc nhật ký theo ca", len(r) == 1 and r[0]["id"] == "log_2", str(r)[:200])

r = client.get("/api/attendance-logs?date_from=2026-09-19&date_to=2026-09-19").json()["data"]
check("lọc nhật ký theo khoảng ngày", len(r) == 1 and r[0]["id"] == "log_2", str(r)[:200])

r = client.get("/api/attendance-logs?q=đội%20ngũ").json()["data"]
check("tìm nhật ký theo tên bài học", len(r) == 2, str(len(r)))

r = client.get("/api/attendance-logs?q=khong-co-gi").json()["data"]
check("từ khoá không khớp thì trả rỗng", r == [], str(r)[:120])

reset()
write_json_list(api.data_path / "attendance_logs.json", [])
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
python tests/test_config_api.py
```

Kỳ vọng: FAIL ở "nhật ký ghép được tên bài học từ ca", "lọc nhật ký theo ca", "lọc nhật ký theo khoảng ngày", "tìm nhật ký theo tên bài học".

- [ ] **Step 3: Viết lại `get_attendance_logs`**

Thay trọn hàm ở `app/api.py:426`:

```python
@app.get("/api/attendance-logs")
async def get_attendance_logs(unit: Optional[str] = None, shift: Optional[str] = None,
                              date_from: Optional[str] = None, date_to: Optional[str] = None,
                              q: Optional[str] = None):
    """Lịch sử điểm danh, ghép thêm thông tin bài học lấy từ thời khoá biểu.

    Biên bản chỉ lưu những gì lõi AI cần. Tên bài, giáo viên, loại huấn luyện
    nằm ở ca; ghép lúc đọc thì bản ghi cũ cũng hiện đủ, khỏi phải vá dữ liệu.
    """
    logs = read_json_list(data_path / "attendance_logs.json")
    schedules = {s["id"]: normalize_schedule(s)
                 for s in read_json_list(schedules_file) if s.get("id")}

    enriched = []
    for log in logs:
        sch = schedules.get(log.get("schedule_id"), {})
        enriched.append({
            **log,
            "lesson_name": log.get("lesson_name") or sch.get("lesson_name", ""),
            "instructor": log.get("instructor") or sch.get("instructor", ""),
            "field": log.get("field") or sch.get("field", ""),
            "class_name": log.get("class_name") or sch.get("class_name", ""),
            "training_type": log.get("training_type") or sch.get("training_type", ""),
            "start_time": sch.get("start_time", ""),
            "end_time": sch.get("end_time", ""),
        })

    if unit and unit not in ("all", "Tất cả đơn vị"):
        enriched = [l for l in enriched if l.get("unit") == unit]
    if shift and shift not in ("all", "Tất cả ca"):
        enriched = [l for l in enriched if l.get("shift") == shift]

    def day_of(log):
        return log.get("date_iso") or str(log.get("started_at", ""))[:10]

    if date_from:
        enriched = [l for l in enriched if day_of(l) >= date_from]
    if date_to:
        enriched = [l for l in enriched if day_of(l) <= date_to]

    needle = (q or "").strip().lower()
    if needle:
        enriched = [l for l in enriched if needle in (
            f"{l.get('lesson_name', '')} {l.get('schedule_name', '')} "
            f"{l.get('shift', '')} {l.get('unit', '')} {l.get('instructor', '')}").lower()]

    return {"status": "success", "data": enriched}
```

- [ ] **Step 4: Chạy test**

```bash
python tests/test_config_api.py && python tests/test_api.py && python tests/test_smoke_routes.py
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api.py tests/test_config_api.py
git commit -m "feat: nhật ký điểm danh ghép tên bài học và lọc theo ca, khoảng ngày, từ khoá

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Đổi mật khẩu và thông tin cá nhân

Khách: *"ấn vào avatar có nút đăng xuất và thay đổi thông tin cá nhân (như thay đổi password)"*. Hiện `app/auth.py` khai cứng hai tài khoản trong biến môi trường nên đổi mật khẩu không thể có tác dụng.

Chuyển sang kho `data/users.json` băm mật khẩu bằng PBKDF2. Vẫn là POC: **không** có phiên, **không** có token, các endpoint khác vẫn không kiểm quyền — phần này chỉ làm cho việc đổi mật khẩu thật sự lưu lại được.

**Files:**
- Modify: `app/auth.py` (viết lại)
- Modify: `app/schemas.py` (thêm `PasswordChangeInput`, `ProfilePatch` sau `LoginInput`)
- Modify: `app/api.py` (thêm hai route cạnh `v1_login`, dòng ~1360; sửa import dòng 27)
- Create: `tests/test_auth_api.py`

**Interfaces:**
- Produces: `app.auth.authenticate(username: str, password: str) -> Optional[dict]` — giữ nguyên chữ ký và dạng trả về hiện tại (`username`, `role`, `display_name`, `role_label`, `avatar`).
- Produces: `app.auth.change_password(username: str, old_password: str, new_password: str) -> bool`.
- Produces: `app.auth.update_profile(username: str, display_name: str) -> Optional[dict]` — trả hồ sơ mới như `authenticate`.
- Produces: `POST /api/v1/auth/password` → `{"status": "success"}` hoặc 400; `PATCH /api/v1/auth/profile` → hồ sơ mới.
- Produces: `app.schemas.PasswordChangeInput`, `app.schemas.ProfilePatch`.

- [ ] **Step 1: Viết test**

Tạo `tests/test_auth_api.py`:

```python
"""Kiểm đăng nhập, đổi mật khẩu và sửa thông tin cá nhân.

Bản POC không có phiên và không kiểm quyền ở endpoint khác; test ở đây chỉ
khẳng định mật khẩu đổi được và lưu lại được, không khẳng định gì về bảo mật.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import app.auth as auth
import app.api as api

failures = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


client = TestClient(api.app)


def reset():
    if auth.USERS_FILE.exists():
        auth.USERS_FILE.unlink()
    auth.reload_users()


print("\n[1] Đăng nhập bằng tài khoản mặc định")
reset()
r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "cbqh@2026"})
check("đăng nhập đúng -> 200", r.status_code == 200, r.text[:200])
check("trả về vai trò cán bộ quản lý", r.json().get("role") == "cbqh", r.text[:200])
check("lần đầu chạy tự tạo kho tài khoản", auth.USERS_FILE.exists())

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "sai"})
check("sai mật khẩu -> 401", r.status_code == 401, str(r.status_code))

r = client.post("/api/v1/auth/login", json={"username": "khong-co", "password": "gi-do"})
check("tài khoản lạ -> 401", r.status_code == 401, str(r.status_code))


print("\n[2] Đổi mật khẩu")
r = client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "sai-rồi", "new_password": "matkhau-moi-1"})
check("sai mật khẩu cũ thì không cho đổi", r.status_code == 400, str(r.status_code))

r = client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "cbqh@2026", "new_password": "ngan"})
check("mật khẩu mới quá ngắn bị từ chối", r.status_code == 422, str(r.status_code))

r = client.post("/api/v1/auth/password", json={
    "username": "cbqh", "old_password": "cbqh@2026", "new_password": "matkhau-moi-1"})
check("đổi mật khẩu đúng -> 200", r.status_code == 200, r.text[:200])

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "cbqh@2026"})
check("mật khẩu cũ hết hiệu lực", r.status_code == 401, str(r.status_code))

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "matkhau-moi-1"})
check("mật khẩu mới đăng nhập được", r.status_code == 200, r.text[:200])

auth.reload_users()
r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "matkhau-moi-1"})
check("mật khẩu mới sống sót qua lần nạp lại", r.status_code == 200, r.text[:200])

check("mật khẩu không được lưu dạng chữ thường",
      "matkhau-moi-1" not in auth.USERS_FILE.read_text(encoding="utf-8"))


print("\n[3] Sửa thông tin cá nhân")
r = client.patch("/api/v1/auth/profile", json={
    "username": "cbqh", "display_name": "Thiếu tá Nguyễn Văn Hùng"})
check("đổi tên hiển thị -> 200", r.status_code == 200, r.text[:200])
check("trả về tên mới",
      r.json().get("display_name") == "Thiếu tá Nguyễn Văn Hùng", r.text[:200])

r = client.post("/api/v1/auth/login", json={"username": "cbqh", "password": "matkhau-moi-1"})
check("đăng nhập lần sau thấy tên mới",
      r.json().get("display_name") == "Thiếu tá Nguyễn Văn Hùng", r.text[:200])

r = client.patch("/api/v1/auth/profile", json={
    "username": "khong-co", "display_name": "Ai đó"})
check("sửa hồ sơ tài khoản lạ -> 404", r.status_code == 404, str(r.status_code))

reset()

print()
if failures:
    print(f"{len(failures)} kiểm thử KHÔNG đạt:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Tất cả kiểm thử đạt.")
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
python tests/test_auth_api.py
```

Kỳ vọng: lỗi ngay khi import — `module 'app.auth' has no attribute 'USERS_FILE'`.

- [ ] **Step 3: Viết lại `app/auth.py`**

```python
"""Tài khoản cho bản POC: kho JSON nhỏ, mật khẩu băm PBKDF2.

**Đây không phải ranh giới bảo mật.** Không có phiên, không có token ký, và các
endpoint khác không kiểm quyền — ai gọi thẳng API vẫn làm được mọi thứ. Phần
này chỉ để giao diện biết đang là vai trò nào và để người dùng đổi được mật
khẩu của chính mình, đổi xong thì lưu lại được.

Muốn dùng thật thì phải thay bằng xác thực có phiên và kiểm quyền ở từng
endpoint; chỗ đó cố ý để trống chứ không phải quên.
"""

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Optional

ROLE_CBQH = "cbqh"
ROLE_QTHT = "qtht"

USERS_FILE = Path(__file__).parent.parent / "data" / "users.json"

PBKDF2_ROUNDS = 120_000

# Tài khoản dựng sẵn lần đầu chạy. Mật khẩu lấy từ biến môi trường nếu có, để
# lúc demo trước khách không phải dùng đúng chuỗi ghi trong mã nguồn công khai.
SEED_USERS = {
    "cbqh": {
        "password": os.environ.get("CBQH_PASSWORD", "cbqh@2026"),
        "role": ROLE_CBQH,
        "display_name": "Đại uý Nguyễn Văn Hùng",
        "role_label": "Cán bộ quản lý",
        "avatar": "H",
    },
    "qtht": {
        "password": os.environ.get("QTHT_PASSWORD", "qtht@2026"),
        "role": ROLE_QTHT,
        "display_name": "Thiếu tá Lê Quang Trung",
        "role_label": "Quản trị hệ thống",
        "avatar": "T",
    },
}

_users = None


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS
    ).hex()


def _seed() -> dict:
    users = {}
    for username, seed in SEED_USERS.items():
        salt = secrets.token_hex(16)
        users[username] = {
            "role": seed["role"],
            "display_name": seed["display_name"],
            "role_label": seed["role_label"],
            "avatar": seed["avatar"],
            "salt": salt,
            "password_hash": _hash(seed["password"], salt),
        }
    return users


def _save(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def reload_users() -> dict:
    """Nạp lại kho tài khoản từ đĩa; chưa có thì dựng sẵn hai tài khoản mặc định."""
    global _users
    if USERS_FILE.exists():
        try:
            _users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _users = _seed()
            _save(_users)
    else:
        _users = _seed()
        _save(_users)
    return _users


def _load() -> dict:
    return _users if _users is not None else reload_users()


def _profile(username: str, user: dict) -> dict:
    return {
        "username": username,
        "role": user["role"],
        "display_name": user["display_name"],
        "role_label": user["role_label"],
        "avatar": user["avatar"],
    }


def authenticate(username: str, password: str) -> Optional[dict]:
    """Trả hồ sơ người dùng nếu đúng tài khoản, ``None`` nếu sai.

    So sánh bằng ``compare_digest`` để thời gian so sánh không phụ thuộc độ
    giống nhau của chuỗi.
    """
    name = (username or "").strip().lower()
    user = _load().get(name)
    if user is None:
        return None
    if not hmac.compare_digest(user["password_hash"], _hash(password or "", user["salt"])):
        return None
    return _profile(name, user)


def change_password(username: str, old_password: str, new_password: str) -> bool:
    """Đổi mật khẩu. Trả ``False`` nếu sai tài khoản hoặc sai mật khẩu cũ."""
    name = (username or "").strip().lower()
    users = _load()
    user = users.get(name)
    if user is None:
        return False
    if not hmac.compare_digest(user["password_hash"], _hash(old_password or "", user["salt"])):
        return False

    user["salt"] = secrets.token_hex(16)
    user["password_hash"] = _hash(new_password, user["salt"])
    _save(users)
    return True


def update_profile(username: str, display_name: str) -> Optional[dict]:
    """Đổi tên hiển thị. Trả ``None`` nếu không có tài khoản đó."""
    name = (username or "").strip().lower()
    users = _load()
    user = users.get(name)
    if user is None:
        return None

    user["display_name"] = display_name.strip()
    user["avatar"] = (display_name.strip()[-1:] or user["avatar"]).upper()
    _save(users)
    return _profile(name, user)
```

- [ ] **Step 4: Thêm schema đầu vào**

Trong `app/schemas.py`, chèn ngay sau lớp `LoginInput`:

```python
class PasswordChangeInput(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class ProfilePatch(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    display_name: str = Field(min_length=1, max_length=120)
```

- [ ] **Step 5: Thêm hai route**

Trong `app/api.py`, sửa import dòng 27:

```python
from app.auth import authenticate, change_password, update_profile
```

và thêm `PasswordChangeInput, ProfilePatch` vào danh sách import từ `app.schemas` (dòng 28-29).

Thêm ngay sau hàm `v1_login` (dòng ~1373):

```python
@app.post("/api/v1/auth/password")
async def v1_change_password(body: PasswordChangeInput):
    """Đổi mật khẩu của chính tài khoản đó.

    POC không có phiên nên phải gửi kèm tên tài khoản và mật khẩu cũ; mật khẩu
    cũ chính là thứ đứng thay cho phiên đăng nhập ở đây.
    """
    if not change_password(body.username, body.old_password, body.new_password):
        raise HTTPException(status_code=400, detail="Sai tài khoản hoặc mật khẩu hiện tại")
    return {"status": "success", "message": "Đã đổi mật khẩu"}


@app.patch("/api/v1/auth/profile")
async def v1_update_profile(body: ProfilePatch):
    """Đổi tên hiển thị của tài khoản."""
    profile = update_profile(body.username, body.display_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    return profile
```

- [ ] **Step 6: Chạy test**

```bash
python tests/test_auth_api.py && python tests/test_smoke_routes.py && python tests/test_api.py
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 7: Thêm `data/users.json` vào `.gitignore`**

Kho tài khoản là dữ liệu vận hành, không phải mã nguồn. Thêm một dòng vào `.gitignore`:

```
data/users.json
```

- [ ] **Step 8: Cập nhật README**

Trong `README.md`, ở mục **Tài khoản**, thay câu *"Đổi mật khẩu bằng biến môi trường `CBQH_PASSWORD` và `QTHT_PASSWORD`"* bằng:

```markdown
Hai biến môi trường `CBQH_PASSWORD` và `QTHT_PASSWORD` chỉ dùng cho **lần chạy
đầu tiên**, khi hệ thống dựng `data/users.json`. Sau đó đổi mật khẩu ngay trên
giao diện: bấm vào avatar ở góc trái dưới cùng → Đổi mật khẩu. Xoá
`data/users.json` là quay về hai tài khoản mặc định.
```

- [ ] **Step 9: Commit**

```bash
git add app/auth.py app/schemas.py app/api.py tests/test_auth_api.py .gitignore README.md
git commit -m "feat: đổi mật khẩu và tên hiển thị, tài khoản lưu ở data/users.json

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Hộp xem ảnh phóng to và camera zoom dùng chung

Sửa nguyên nhân gốc số 1 và cấp hạ tầng cho mọi yêu cầu *"phóng to toàn màn hình và có thể zoom"* ở các task sau. Một hộp ảnh, một hàm gắn zoom, dùng lại ở 6 chỗ.

**Files:**
- Modify: `static/index.html` — thêm `#zoom-modal` ngay trước `<script src="/static/app.js">` (cuối `<body>`)
- Modify: `static/app.js` — thêm khối mới ngay sau `closeEvidenceModal()` (dòng ~1980)
- Modify: `static/style.css` — thêm khối CSS ở cuối file
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Produces: `window.openEvidence(src: string, caption?: string)` — mở lớp phủ ảnh, đặt lại zoom về 100%. Đây chính là hàm 5 chỗ đang gọi mà chưa bao giờ tồn tại.
- Produces: `window.closeZoomModal(event?: Event)`.
- Produces: `window.makeZoomable(container: HTMLElement)` — gắn nút toàn màn hình + lăn chuột để zoom + kéo để di ảnh vào một khung chứa `<img>`. Gọi lại nhiều lần trên cùng phần tử là vô hại (có cờ `data-zoomable`). Gắn API lên phần tử: `container._zoom = { zoomBy(delta), reset(), scale }`.
- Produces: `window.toggleFullscreen(el: HTMLElement)`.
- Consumes: không có.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs`, ngay trước khối `console.log('\n[8] Màn vẽ vùng...')` ở cuối file:

```js
console.log('\n[7b] Hộp xem ảnh phóng to dùng chung');

check('có hàm mở ảnh bằng chứng', typeof window.openEvidence === 'function');
check('có hàm gắn zoom cho khung camera', typeof window.makeZoomable === 'function');

window.openEvidence('/static/khong-co-that.jpg', 'Ảnh thử');
check('mở hộp ảnh thì lớp phủ hiện ra',
    doc.getElementById('zoom-modal').style.display === 'flex',
    doc.getElementById('zoom-modal').style.display);
check('hộp ảnh trỏ đúng ảnh được bấm',
    doc.getElementById('zoom-img').getAttribute('src') === '/static/khong-co-that.jpg');
check('hộp ảnh hiện chú thích', doc.getElementById('zoom-caption').textContent === 'Ảnh thử');
check('có nút tải ảnh trỏ đúng ảnh',
    doc.getElementById('zoom-download').getAttribute('href') === '/static/khong-co-that.jpg');
check('mở ra thì luôn bắt đầu ở 100%',
    doc.getElementById('zoom-level').textContent === '100%',
    doc.getElementById('zoom-level').textContent);

const stage = doc.getElementById('zoom-stage');
stage._zoom.zoomBy(0.5);
check('phóng to đổi được tỉ lệ', doc.getElementById('zoom-level').textContent === '150%',
    doc.getElementById('zoom-level').textContent);
stage._zoom.reset();
check('nút vừa khung đưa về 100%', doc.getElementById('zoom-level').textContent === '100%');

window.closeZoomModal();
check('đóng hộp ảnh', doc.getElementById('zoom-modal').style.display === 'none');

const box = doc.createElement('div');
box.appendChild(doc.createElement('img'));
doc.body.appendChild(box);
window.makeZoomable(box);
check('gắn zoom thì thêm nút toàn màn hình',
    !!box.querySelector('.zoom-fullscreen-btn'));
window.makeZoomable(box);
check('gọi lại không nhân đôi nút',
    box.querySelectorAll('.zoom-fullscreen-btn').length === 1,
    String(box.querySelectorAll('.zoom-fullscreen-btn').length));
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
# cửa sổ 1
python main.py
# cửa sổ 2
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "có hàm mở ảnh bằng chứng" và "có hàm gắn zoom cho khung camera" (`undefined`).

- [ ] **Step 3: Thêm lớp phủ vào `static/index.html`**

Chèn ngay trước thẻ `<script src="/static/app.js"></script>` ở cuối `<body>`:

```html
    <!-- Lớp phủ xem ảnh phóng to: mọi ảnh bằng chứng trong hệ thống dùng chung -->
    <div id="zoom-modal" class="zoom-modal" style="display: none;" onclick="closeZoomModal(event)">
        <div class="zoom-toolbar" onclick="event.stopPropagation()">
            <span class="zoom-caption" id="zoom-caption"></span>
            <button type="button" class="zoom-btn" onclick="zoomStageBy(-0.25)" title="Thu nhỏ">−</button>
            <span class="zoom-level" id="zoom-level">100%</span>
            <button type="button" class="zoom-btn" onclick="zoomStageBy(0.25)" title="Phóng to">+</button>
            <button type="button" class="zoom-btn" onclick="zoomStageReset()" title="Vừa khung">⤢</button>
            <a class="zoom-btn" id="zoom-download" download title="Tải ảnh">⬇</a>
            <button type="button" class="zoom-btn" onclick="closeZoomModal()" title="Đóng">×</button>
        </div>
        <div class="zoom-stage" id="zoom-stage" onclick="event.stopPropagation()">
            <img id="zoom-img" alt="Ảnh bằng chứng phóng to">
        </div>
    </div>
```

- [ ] **Step 4: Thêm khối JS dùng chung**

Trong `static/app.js`, chèn ngay sau hàm `closeEvidenceModal()` (dòng ~1980):

```js
// =====================================================================
// XEM ẢNH PHÓNG TO VÀ ZOOM CAMERA — DÙNG CHUNG TOÀN HỆ THỐNG
// Trước đây nhiều chỗ gọi openEvidence() nhưng hàm chưa bao giờ được định
// nghĩa, nên bấm vào ảnh bằng chứng chỉ ném ReferenceError và không mở gì cả.
// Phóng to dùng Fullscreen API và transform: scale() sẵn có, không thêm thư viện.
// =====================================================================

function toggleFullscreen(el) {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (el && el.requestFullscreen) el.requestFullscreen();
}
window.toggleFullscreen = toggleFullscreen;

// Gắn khả năng phóng to / kéo di cho một khung có chứa <img>: lăn chuột để
// zoom, kéo để di khi đã phóng, bấm đúp để về vừa khung, nút ⛶ để toàn màn hình.
function makeZoomable(container) {
    if (!container || container.dataset.zoomable === '1') return;
    container.dataset.zoomable = '1';
    container.classList.add('zoomable');

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'zoom-fullscreen-btn';
    btn.title = 'Phóng to toàn màn hình';
    btn.textContent = '⛶';
    btn.onclick = (e) => { e.stopPropagation(); toggleFullscreen(container); };
    container.appendChild(btn);

    let scale = 1, x = 0, y = 0, drag = null;

    const apply = () => {
        const img = container.querySelector('img');
        if (img) img.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
        container.classList.toggle('is-zoomed', scale > 1);
        const label = container.querySelector('.zoom-level')
            || document.getElementById(container.dataset.zoomLevelId || '');
        if (label) label.textContent = `${Math.round(scale * 100)}%`;
    };

    const zoomBy = (delta) => {
        scale = Math.min(6, Math.max(1, Math.round((scale + delta) * 100) / 100));
        if (scale === 1) { x = 0; y = 0; }
        apply();
    };

    const reset = () => { scale = 1; x = 0; y = 0; apply(); };

    container.addEventListener('wheel', (e) => {
        e.preventDefault();
        zoomBy(e.deltaY < 0 ? 0.25 : -0.25);
    }, { passive: false });

    container.addEventListener('pointerdown', (e) => {
        if (scale === 1) return;
        drag = { sx: e.clientX - x, sy: e.clientY - y };
        container.classList.add('is-panning');
        if (container.setPointerCapture) container.setPointerCapture(e.pointerId);
    });
    container.addEventListener('pointermove', (e) => {
        if (!drag) return;
        x = e.clientX - drag.sx;
        y = e.clientY - drag.sy;
        apply();
    });
    const endDrag = (e) => {
        if (!drag) return;
        drag = null;
        container.classList.remove('is-panning');
        if (container.releasePointerCapture) container.releasePointerCapture(e.pointerId);
    };
    container.addEventListener('pointerup', endDrag);
    container.addEventListener('pointercancel', endDrag);
    container.addEventListener('dblclick', reset);

    container._zoom = { zoomBy, reset, get scale() { return scale; } };
}
window.makeZoomable = makeZoomable;

function zoomStage() {
    const stage = document.getElementById('zoom-stage');
    if (stage && !stage._zoom) {
        stage.dataset.zoomLevelId = 'zoom-level';
        makeZoomable(stage);
    }
    return stage;
}

function zoomStageBy(delta) { const s = zoomStage(); if (s) s._zoom.zoomBy(delta); }
window.zoomStageBy = zoomStageBy;

function zoomStageReset() { const s = zoomStage(); if (s) s._zoom.reset(); }
window.zoomStageReset = zoomStageReset;

// Hàm mà toàn bộ ảnh bằng chứng trong hệ thống gọi tới
function openEvidence(src, caption) {
    const modal = document.getElementById('zoom-modal');
    const img = document.getElementById('zoom-img');
    if (!modal || !img || !src) return;

    img.src = src;
    const cap = document.getElementById('zoom-caption');
    if (cap) cap.textContent = caption || '';
    const dl = document.getElementById('zoom-download');
    if (dl) dl.href = src;

    modal.style.display = 'flex';
    zoomStageReset();
}
window.openEvidence = openEvidence;

function closeZoomModal(event) {
    // Bấm vào chính ảnh hoặc thanh công cụ thì không đóng
    if (event && event.target && event.target.id !== 'zoom-modal') return;
    const modal = document.getElementById('zoom-modal');
    if (modal) modal.style.display = 'none';
    if (document.fullscreenElement) document.exitFullscreen();
}
window.closeZoomModal = closeZoomModal;

document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('zoom-modal');
    if (modal && modal.style.display !== 'none') closeZoomModal();
});
```

Lưu ý: `openEvidenceModal()` cũ (dùng cho bảng nhật ký) vẫn giữ nguyên ở Task này; Task 15 sẽ chuyển nó sang gọi `openEvidence`.

- [ ] **Step 5: Thêm CSS**

Thêm vào cuối `static/style.css`:

```css
/* ===================================================================
   XEM ẢNH PHÓNG TO VÀ ZOOM CAMERA — DÙNG CHUNG
   =================================================================== */

.zoom-modal {
    position: fixed;
    inset: 0;
    z-index: 200;
    background: rgba(2, 6, 23, 0.92);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 16px;
}

.zoom-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.35);
    border-radius: 10px;
    padding: 8px 12px;
    max-width: 100%;
}

.zoom-caption {
    color: #e2e8f0;
    font-size: 12.5px;
    font-weight: 600;
    margin-right: 8px;
    max-width: 48vw;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.zoom-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 30px;
    height: 30px;
    padding: 0 8px;
    border-radius: 7px;
    border: 1px solid rgba(148, 163, 184, 0.4);
    background: rgba(255, 255, 255, 0.06);
    color: #e2e8f0;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
    text-decoration: none;
}
.zoom-btn:hover { background: rgba(255, 255, 255, 0.16); }

.zoom-level {
    color: #cbd5e1;
    font-family: var(--font-mono);
    font-size: 12px;
    min-width: 46px;
    text-align: center;
}

.zoom-stage {
    flex: 1;
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
}

.zoom-stage img {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    transform-origin: center center;
    transition: transform 0.08s linear;
}

/* Khung camera có thể phóng to */
.zoomable { position: relative; overflow: hidden; }
.zoomable img { transform-origin: center center; transition: transform 0.08s linear; }
.zoomable.is-zoomed { cursor: grab; }
.zoomable.is-panning { cursor: grabbing; }
.zoomable.is-panning img { transition: none; }

.zoom-fullscreen-btn {
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 5;
    width: 30px;
    height: 30px;
    border-radius: 7px;
    border: 1px solid rgba(255, 255, 255, 0.35);
    background: rgba(15, 23, 42, 0.6);
    color: #fff;
    font-size: 14px;
    cursor: pointer;
}
.zoom-fullscreen-btn:hover { background: rgba(15, 23, 42, 0.85); }

/* Khi khung camera ở chế độ toàn màn hình */
.zoomable:fullscreen {
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
}
.zoomable:fullscreen img {
    max-width: 100vw;
    max-height: 100vh;
    object-fit: contain;
}
```

- [ ] **Step 6: Chạy test giao diện**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: tất cả PASS, và mục "[1] app.js nạp và chạy không ném lỗi" vẫn PASS.

- [ ] **Step 7: Kiểm bằng mắt**

Mở `http://localhost:8199`, vào **Nhật ký điểm danh**, bấm vào một ảnh bằng chứng. Ảnh phải mở to, lăn chuột phóng được, kéo di được, Escape đóng được.

- [ ] **Step 8: Commit**

```bash
git add static/index.html static/app.js static/style.css tests/test_ui.mjs
git commit -m "fix: thêm openEvidence còn thiếu và hộp xem ảnh phóng to dùng chung

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Màn chi tiết ca huấn luyện — gộp camera và bảng quân nhân

Gom bốn phản hồi về màn chi tiết:
- *"các thông tin lại hiện ở tiêu đề mà không hiện vào các mục như 'Tên bài học'"* → đã sửa gốc ở Task 1, chỉ cần render.
- *"Thêm hộ em 2 ô thay cho 'Cửa sổ điểm danh' và 'Dung sai đi chậm' bằng 'Sĩ số đầu buổi' và 'Sĩ số cuối buổi'."*
- *"phần đối chiếu quân số thì thêm 1 cột ngay bên phải là tên quân nhân vắng dạng hàng dọc theo từng gạch đầu dòng theo đầu buổi và cuối buổi."*
- *"Thay hộ em chữ Giám sát quân số bằng camera của lịch đó luôn... có thể phóng to camera toàn bộ màn hình và có thể zoom."*

Đồng thời kéo luôn nội dung của `view-attendance-detail` (ảnh AI chụp, thẻ chỉ số, bảng từng quân nhân) vào đây, để Task 9 bỏ được hẳn màn kia.

**Files:**
- Modify: `static/index.html:585-613` (`view-session-detail`)
- Modify: `static/app.js` — `openSessionDetail()` (dòng 2173), `loadSessionChecks()` (dòng 2211), `watchSessionAttendance()` (dòng 2238); thêm `loadSessionAttendance()`, `renderSessionAttendance()`
- Modify: `static/style.css` — thêm `.absent-name-list`
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `GET /api/v1/schedules/{id}` nay luôn có `lesson_name`, `instructor`, `field`, `class_name`, `check_window_mins`, `camera_id`, `training_type` (Task 1).
- Consumes: `GET /api/v1/sessions/{id}/checks` → mảng `{phase, phase_label, time, present, absent, absent_personnel: string[], evidence_url}`.
- Consumes: `GET /api/v1/sessions/{id}/attendance` → `{summary: {required, present, late, early_leave, absent}, items: [{person: {rank, name, military_id, unit}, first_seen, last_seen, violations: string[], late_minutes, early_leave_minutes}]}`.
- Consumes: `window.openEvidence`, `window.makeZoomable` (Task 7); `attachStream`, `detachStream`, `TRAINING_TAG`, `VIOLATION_TAG`, `esc`, `fmtTime`, `getJson` (đã có trong `app.js`).
- Produces: `window.loadSessionAttendance(sessionId)`, `window.renderSessionAttendance()`, biến `sessionAttendanceData = { items: [], summary: {} }`.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs`, sau khối `[7b]`:

```js
console.log('\n[7c] Màn chi tiết ca huấn luyện');

window.switchNavTab('schedule-progress');
await sleep(900);
const firstRow = doc.querySelector('#dt-schedule-tbody tr button');
check('bảng lịch có nút xem chi tiết', !!firstRow);

firstRow.click();
await sleep(1200);

check('mở đúng màn chi tiết ca',
    doc.querySelector('.page-view.active').id === 'view-session-detail',
    doc.querySelector('.page-view.active').id);

const infoKeys = [...doc.querySelectorAll('#sd-info .detail-key')].map(e => e.textContent.trim());
check('có ô Sĩ số đầu buổi', infoKeys.includes('Sĩ số đầu buổi'), infoKeys.join(' | '));
check('có ô Sĩ số cuối buổi', infoKeys.includes('Sĩ số cuối buổi'), infoKeys.join(' | '));
check('bỏ ô Cửa sổ điểm danh', !infoKeys.includes('Cửa sổ điểm danh'), infoKeys.join(' | '));
check('bỏ ô Dung sai đi chậm', !infoKeys.includes('Dung sai đi chậm'), infoKeys.join(' | '));

const infoVals = [...doc.querySelectorAll('#sd-info .detail-val')].map(e => e.textContent.trim());
check('không còn ô nào hiện undefined',
    infoVals.every(v => !v.includes('undefined')), infoVals.join(' | '));
check('tên bài học điền vào ô chứ không chỉ ở tiêu đề',
    infoKeys.includes('Tên bài học'), infoKeys.join(' | '));

const headers = [...doc.querySelectorAll('#view-session-detail table th')].map(e => e.textContent.trim());
check('bảng đối chiếu có cột quân nhân vắng',
    headers.includes('QUÂN NHÂN VẮNG'), headers.join(' | '));

check('màn chi tiết nhúng camera của ca', !!doc.getElementById('sd-stream'));
check('khung camera gắn được zoom',
    doc.getElementById('sd-camera-box').dataset.zoomable === '1');
check('bỏ nút Giám sát quân số', doc.getElementById('sd-btn-watch') === null);
check('có bảng từng quân nhân trong ca', !!doc.getElementById('sd-attendance-tbody'));
check('có khu ảnh điểm danh do AI chụp', !!doc.getElementById('sd-evidence'));
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "có ô Sĩ số đầu buổi", "bảng đối chiếu có cột quân nhân vắng", "màn chi tiết nhúng camera của ca", "bỏ nút Giám sát quân số".

- [ ] **Step 3: Thay HTML của `view-session-detail`**

Thay trọn khối `static/index.html:585-613` bằng:

```html
                <!-- MÀN 1.2 — CHI TIẾT LỊCH HUẤN LUYỆN (gộp cả giám sát quân số) -->
                <section id="view-session-detail" class="page-view">
                    <div class="glass-card section-card">
                        <div class="card-header flex-between">
                            <div>
                                <button class="btn-toolbar-neutral" onclick="backFromSessionDetail()">← Quay lại</button>
                                <h2 id="sd-title" style="margin-top: 12px;">CHI TIẾT LỊCH HUẤN LUYỆN</h2>
                                <p class="card-subtitle" id="sd-subtitle"></p>
                            </div>
                        </div>

                        <div class="detail-grid" id="sd-info"></div>

                        <!-- Camera của chính ca này, thay cho nút "Giám sát quân số" -->
                        <div class="attendance-detail-grid" style="margin-top: 20px;">
                            <div class="surveillance-screen-card">
                                <div class="video-container camera-tile-video" id="sd-camera-box">
                                    <img id="sd-stream" alt="Camera giám sát lớp">
                                    <div class="camera-tile-idle" id="sd-camera-idle">Camera của ca này chưa chạy</div>
                                </div>
                                <p class="camera-caption" id="sd-camera-caption"></p>
                            </div>

                            <div class="evidence-column">
                                <h3 class="evidence-title">ẢNH ĐIỂM DANH DO AI CHỤP</h3>
                                <div id="sd-evidence" class="evidence-thumbs"></div>
                            </div>
                        </div>

                        <div class="card-header" style="margin-top: 20px;">
                            <h2>ĐỐI CHIẾU QUÂN SỐ ĐẦU BUỔI · CUỐI BUỔI</h2>
                        </div>
                        <div class="table-responsive">
                            <table class="personnel-table">
                                <thead>
                                    <tr>
                                        <th>MỐC ĐIỂM DANH</th>
                                        <th>GIỜ CHỐT</th>
                                        <th>CÓ MẶT</th>
                                        <th>VẮNG</th>
                                        <th>QUÂN NHÂN VẮNG</th>
                                        <th>BẰNG CHỨNG</th>
                                    </tr>
                                </thead>
                                <tbody id="sd-checks-tbody"></tbody>
                            </table>
                        </div>

                        <div class="log-metrics-grid" id="sd-metrics" style="margin-top: 20px;"></div>

                        <div class="card-header flex-between" style="margin-top: 8px;">
                            <h2>QUÂN SỐ THAM GIA TRONG CA</h2>
                            <div class="table-filters">
                                <input type="text" id="sd-search" class="filter-select"
                                       placeholder="Tìm quân nhân" oninput="renderSessionAttendance()">
                                <select id="sd-filter" class="filter-select" onchange="renderSessionAttendance()">
                                    <option value="all">Tất cả trạng thái</option>
                                    <option value="absent">Không tham gia</option>
                                    <option value="late">Đi chậm</option>
                                    <option value="early_leave">Chưa hết giờ đã về</option>
                                </select>
                            </div>
                        </div>
                        <div class="table-responsive">
                            <table class="personnel-table">
                                <thead>
                                    <tr>
                                        <th>QUÂN NHÂN</th>
                                        <th>SỐ HIỆU</th>
                                        <th>CHỨC VỤ</th>
                                        <th>ĐƠN VỊ</th>
                                        <th>THẤY LẦN ĐẦU</th>
                                        <th>THẤY LẦN CUỐI</th>
                                        <th>TRẠNG THÁI</th>
                                    </tr>
                                </thead>
                                <tbody id="sd-attendance-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </section>
```

- [ ] **Step 4: Viết lại `openSessionDetail` trong `static/app.js`**

Thay trọn hàm `openSessionDetail` (dòng 2173-2209):

```js
async function openSessionDetail(sessionId, scheduleId) {
    sessionDetailId = sessionId || scheduleId;
    sessionDetailFrom = 'schedule-progress';
    switchNavTab('session-detail');

    // Hai mốc điểm danh cần cho cả ô "Sĩ số đầu/cuối buổi" lẫn bảng đối chiếu,
    // nên nạp một lần rồi dùng lại chứ không gọi hai lượt.
    let checks = [];
    try {
        checks = await getJson(`/api/v1/sessions/${encodeURIComponent(sessionDetailId)}/checks`);
    } catch (e) {
        checks = [];
    }
    const phaseOf = (ph) => checks.find(c => c.phase === ph);
    const headcount = (ph) => {
        const c = phaseOf(ph);
        return c ? `${c.present} có mặt · ${c.absent} vắng` : 'Chưa chốt';
    };

    try {
        const sch = await getJson(`/api/v1/schedules/${scheduleId}`);
        document.getElementById('sd-title').textContent = (sch.name || '').toUpperCase();
        document.getElementById('sd-subtitle').textContent =
            `${sch.shift || ''} · ${sch.start_time}–${sch.end_time} · ${sch.unit || 'Toàn đơn vị'}`;

        // Trường giáo viên / thao trường / bài học do hệ thống quản lý gửi kèm khi
        // tạo ca; service AI giữ nguyên và trả lại, ở đây chỉ hiển thị.
        const info = [
            ['Tên bài học', sch.lesson_name],
            ['Loại huấn luyện', TRAINING_LABEL[sch.training_type] || '—'],
            ['Giáo viên phụ trách', sch.instructor],
            ['Thao trường', sch.field],
            ['Đội học / Lớp', sch.class_name],
            ['Khung giờ', `${sch.start_time} – ${sch.end_time}`],
            ['Sĩ số đầu buổi', headcount('start')],
            ['Sĩ số cuối buổi', headcount('end')],
            ['Sĩ số chuẩn', sch.required_count || '—'],
            ['Trạng thái', sch.state_label]
        ];
        document.getElementById('sd-info').innerHTML = info.map(([k, v]) =>
            `<div class="detail-item"><span class="detail-key">${k}</span>
             <span class="detail-val">${esc(v || '—')}</span></div>`).join('');

        attachSessionCamera(sch);
    } catch (e) {
        document.getElementById('sd-subtitle').textContent = `Lỗi tải ca: ${e.message}`;
    }

    renderSessionChecks(checks);
    renderSessionEvidence(checks);
    await loadSessionAttendance(sessionDetailId);
}
window.openSessionDetail = openSessionDetail;

// Camera của chính ca này, thay cho nút "Giám sát quân số" ngày trước: người
// trực mở chi tiết ca là thấy luôn lớp đang học, không phải bấm thêm một lần.
function attachSessionCamera(sch) {
    const box = document.getElementById('sd-camera-box');
    const img = document.getElementById('sd-stream');
    const idle = document.getElementById('sd-camera-idle');
    if (!box || !img) return;

    makeZoomable(box);
    const live = ['check_start', 'running', 'check_end'].includes(sch.state);
    if (live && sch.camera_id) {
        attachStream(img, sch.camera_id, true);
        if (idle) idle.style.display = 'none';
    } else {
        detachStream(img);
        if (idle) {
            idle.style.display = '';
            idle.textContent = live ? 'Ca chưa gán camera giám sát' : 'Ca không diễn ra, camera không chạy';
        }
    }
    // scheduleCameras chỉ được nạp ở màn cấu hình thời khoá biểu, vào thẳng màn
    // này thì nó còn rỗng và tên camera sẽ hiện ra mã. Nạp trước rồi mới ghi chú.
    const caption = document.getElementById('sd-camera-caption');
    caption.textContent = 'Đang lấy thông tin camera…';
    fillScheduleCameraSelect(sch.camera_id || '').then(() => {
        caption.textContent =
            `${scheduleCameraName(sch.camera_id)} — khung xanh là quân nhân đã định danh`;
    });
}
```

- [ ] **Step 5: Thay `loadSessionChecks` bằng `renderSessionChecks` + `renderSessionEvidence`**

Thay trọn hàm `loadSessionChecks` (dòng 2211-2234) bằng:

```js
function renderSessionChecks(checks) {
    const tbody = document.getElementById('sd-checks-tbody');
    if (!tbody) return;

    tbody.innerHTML = checks.length ? '' :
        `<tr><td colspan="6" class="empty-row">Buổi chưa diễn ra — cả hai mốc đều bằng 0</td></tr>`;

    checks.forEach(c => {
        const names = c.absent_personnel || [];
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${esc(c.phase_label)}</strong></td>
            <td class="font-mono">${esc(c.time || '—')}</td>
            <td class="text-green"><strong>${c.present}</strong></td>
            <td class="${c.absent > 0 ? 'text-amber' : ''}">${c.absent}</td>
            <td>${names.length
                ? `<ul class="absent-name-list">${names.map(n => `<li>${esc(n)}</li>`).join('')}</ul>`
                : '<span class="muted">Không vắng ai</span>'}</td>
            <td>${c.evidence_url
                ? `<img class="evidence-thumb" src="${c.evidence_url}"
                        onclick="openEvidence('${c.evidence_url}','Điểm danh ${esc(c.phase_label)} — ${c.present} có mặt')"
                        alt="Ảnh bằng chứng">`
                : '<span class="muted">Chưa có</span>'}</td>`;
        tbody.appendChild(tr);
    });
}

function renderSessionEvidence(checks) {
    const box = document.getElementById('sd-evidence');
    if (!box) return;
    const withPhoto = checks.filter(c => c.evidence_url);
    box.innerHTML = withPhoto.length ? '' :
        '<p class="empty-hint">Chưa có ảnh điểm danh nào được chụp</p>';

    withPhoto.forEach(c => {
        box.insertAdjacentHTML('beforeend', `
            <figure class="evidence-figure">
                <img src="${c.evidence_url}" alt="Ảnh điểm danh ${esc(c.phase_label)}"
                     onclick="openEvidence('${c.evidence_url}','Điểm danh ${esc(c.phase_label)} — ${c.present} có mặt')">
                <figcaption>
                    <strong>${esc(c.phase_label)}</strong> · ${esc(c.time || '')} · ${c.present} có mặt
                    <a class="btn-download" href="${c.evidence_url}" download>⬇ Tải ảnh</a>
                </figcaption>
            </figure>`);
    });
}
```

- [ ] **Step 6: Thêm bảng từng quân nhân**

Thay hàm `watchSessionAttendance` (dòng 2238) bằng khối sau (nút gọi nó đã bị bỏ ở Step 3):

```js
let sessionAttendanceData = { items: [], summary: {} };

async function loadSessionAttendance(sessionId) {
    const metrics = document.getElementById('sd-metrics');
    try {
        const data = await getJson(`/api/v1/sessions/${encodeURIComponent(sessionId)}/attendance`);
        sessionAttendanceData = { items: data.items || [], summary: data.summary || {} };
    } catch (e) {
        sessionAttendanceData = { items: [], summary: {} };
    }

    const sm = sessionAttendanceData.summary;
    if (metrics) {
        metrics.innerHTML = `
            <div class="metric-card"><span class="metric-label">Sĩ số yêu cầu</span><span class="metric-val">${sm.required || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Đủ giờ</span><span class="metric-val text-green">${sm.present || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Đi chậm</span><span class="metric-val text-amber">${sm.late || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Về sớm</span><span class="metric-val text-amber">${sm.early_leave || 0}</span></div>
            <div class="metric-card"><span class="metric-label">Không tham gia</span><span class="metric-val text-red">${sm.absent || 0}</span></div>`;
    }
    renderSessionAttendance();
}
window.loadSessionAttendance = loadSessionAttendance;

function renderSessionAttendance() {
    const tbody = document.getElementById('sd-attendance-tbody');
    if (!tbody) return;

    const filter = (document.getElementById('sd-filter') || {}).value || 'all';
    const q = ((document.getElementById('sd-search') || {}).value || '').toLowerCase();

    let items = sessionAttendanceData.items;
    if (filter !== 'all') items = items.filter(i => (i.violations || []).includes(filter));
    if (q) items = items.filter(i => {
        const p = i.person || {};
        return `${p.rank || ''} ${p.name || ''} ${p.military_id || ''}`.toLowerCase().includes(q);
    });

    tbody.innerHTML = items.length ? '' :
        `<tr><td colspan="7" class="empty-row">Chưa có dữ liệu điểm danh cho ca này</td></tr>`;

    items.forEach(i => {
        const p = i.person || {};
        const tags = (i.violations || []).map(v => VIOLATION_TAG[v] || v).join(' ')
            || '<span class="viol-tag viol-ok">Đủ giờ</span>';
        const extra = [];
        if (i.late_minutes) extra.push(`chậm ${i.late_minutes}′`);
        if (i.early_leave_minutes) extra.push(`về sớm ${i.early_leave_minutes}′`);

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${esc(p.name || '')}</strong></td>
            <td class="font-mono">${esc(p.military_id || '—')}</td>
            <td>${esc(p.rank || '—')}</td>
            <td>${esc(p.unit || '—')}</td>
            <td class="font-mono">${fmtTime(i.first_seen)}</td>
            <td class="font-mono">${fmtTime(i.last_seen)}</td>
            <td>${tags}${extra.length ? `<br><span class="muted">${extra.join(' · ')}</span>` : ''}</td>`;
        tbody.appendChild(tr);
    });
}
window.renderSessionAttendance = renderSessionAttendance;
```

- [ ] **Step 7: Ngắt luồng camera khi rời màn**

Trong `switchNavTab` (`static/app.js:133`), ngay dưới khối ngắt `ad-stream`, thêm:

```js
    const sdStream = document.getElementById('sd-stream');
    if (sdStream && !sdStream.closest('.page-view').classList.contains('active')) detachStream(sdStream);
```

- [ ] **Step 8: Thêm CSS cho danh sách tên vắng**

Thêm vào cuối `static/style.css`:

```css
.absent-name-list {
    margin: 0;
    padding-left: 16px;
    list-style: disc;
    max-width: 260px;
}
.absent-name-list li {
    font-size: 12.5px;
    color: var(--warning-orange);
    line-height: 1.6;
}
```

- [ ] **Step 9: Chạy test**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: tất cả PASS, đặc biệt "không còn ô nào hiện undefined".

- [ ] **Step 10: Commit**

```bash
git add static/index.html static/app.js static/style.css tests/test_ui.mjs
git commit -m "feat: chi tiết ca huấn luyện gộp camera, sĩ số đầu/cuối buổi và danh sách vắng

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Bỏ tab "Giám sát quân số", gộp về "Lịch & Tiến độ"

Khách: *"Bỏ luôn tab 'Giám sát quân số' hộ em luôn. Em gộp lịch, tiến độ với giám sát vào 1 trang luôn ạ."*

Task 8 đã dựng xong màn chi tiết gộp, giờ mới được phép gỡ màn cũ. Sau task này còn đúng hai cấp: **Lịch & Tiến độ** → **Chi tiết ca huấn luyện**.

**Files:**
- Modify: `static/index.html` — xoá `<a id="nav-attendance">` (dòng 79-82), xoá `<section id="view-attendance-summary">` (dòng 615-670) và `<section id="view-attendance-detail">` (dòng 672-725)
- Modify: `static/app.js` — `SHARED_VIEW` (dòng 119), `switchNavTab` (dòng 124-212), `setTrainingFilter` (dòng 2082), `ROLES` (dòng 2875); xoá `loadAttendanceSummary`, `openAttendanceDetail`, `loadAttendanceEvidence`, `renderAttendanceDetail`, `backFromAttendanceDetail`, `attendanceDetailData`
- Modify: `tests/test_ui.mjs` — nhóm `[0]`, `[1]`, `[2]`, `[3]`, `[3b]`

**Interfaces:**
- Produces: tab `attendance` và các view `view-attendance-summary`, `view-attendance-detail` **không còn tồn tại**. Mã nào còn gọi `switchNavTab('attendance')` hoặc `openAttendanceDetail(...)` là lỗi.
- Produces: `ROLES.cbqh.home === 'schedule-progress'`.

- [ ] **Step 1: Sửa test giao diện trước**

Trong `tests/test_ui.mjs`:

a) Nhóm `[0]`, đổi hai dòng cuối:

```js
check('vào bằng CBQH thì mở màn lịch và tiến độ',
    doc.querySelector('.page-view.active').id === 'view-schedule-progress',
    doc.querySelector('.page-view.active').id);
```

b) Nhóm `[1]`, đổi khối `activeView`:

```js
const activeView = doc.querySelector('.page-view.active');
check('có đúng một màn hình đang hiển thị',
    doc.querySelectorAll('.page-view.active').length === 1, String(doc.querySelectorAll('.page-view.active').length));
check('vào thẳng màn lịch và tiến độ',
    activeView && activeView.id === 'view-schedule-progress', activeView && activeView.id);
```

c) Nhóm `[2]`, bỏ `'attendance'` khỏi mảng `tabs`:

```js
const tabs = ['schedule-progress', 'safety', 'logs',
              'monitoring', 'schedule', 'zones', 'cameras', 'registration'];
```

d) Nhóm `[3]`, đổi toàn bộ sang màn lịch:

```js
console.log('\n[3] Phân hệ I và II là một màn, tách bằng bộ lọc loại huấn luyện');
window.switchNavTab('schedule-progress');
await sleep(900);
const rowsAll = doc.querySelectorAll('#dt-schedule-tbody tr').length;

window.setTrainingFilter('dao_tao');
await sleep(900);
const rowsDt = doc.querySelectorAll('#dt-schedule-tbody tr').length;

window.setTrainingFilter('chien_dau');
await sleep(900);
const rowsCd = doc.querySelectorAll('#dt-schedule-tbody tr').length;

window.setTrainingFilter('');
await sleep(900);

check('không lọc thì thấy cả hai loại', rowsAll >= 1, String(rowsAll));
check('lọc đào tạo ra ít ca hơn tổng', rowsDt < rowsAll, `${rowsDt} / ${rowsAll}`);
check('lọc chiến đấu ra ít ca hơn tổng', rowsCd < rowsAll, `${rowsCd} / ${rowsAll}`);
check('hai loại cộng lại bằng tổng', rowsDt + rowsCd === rowsAll, `${rowsDt}+${rowsCd} vs ${rowsAll}`);
check('đã bỏ hẳn tab giám sát quân số',
    doc.getElementById('nav-attendance') === null
    && doc.getElementById('view-attendance-summary') === null);
```

e) Nhóm `[3b]`, đổi dòng kiểm menu CBQH:

```js
check('CBQH vẫn thấy nghiệp vụ huấn luyện',
    doc.getElementById('nav-schedule-progress') !== null);
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "vào bằng CBQH thì mở màn lịch và tiến độ" và "đã bỏ hẳn tab giám sát quân số".

- [ ] **Step 3: Gỡ khỏi `static/index.html`**

Xoá ba khối:
1. `<a href="#attendance" class="nav-item active" id="nav-attendance" ...>...</a>` (dòng 79-82). Sau khi xoá, thêm class `active` vào `<a ... id="nav-schedule-progress">` để mục đầu tiên sáng sẵn.
2. Trọn `<section id="view-attendance-summary" class="page-view"> … </section>`.
3. Trọn `<section id="view-attendance-detail" class="page-view"> … </section>`.

- [ ] **Step 4: Gỡ khỏi `static/app.js`**

a) `SHARED_VIEW` (dòng 119) còn lại:

```js
// Phân hệ I và II không phải hai nhóm màn riêng: cùng một nghiệp vụ, chỉ khác
// loại lịch. Nên dùng chung màn và tách bằng bộ lọc training_type.
const SHARED_VIEW = {
    'safety': 'safety'
};
```

b) Trong `switchNavTab`: xoá khối ngắt `ad-stream` (dòng 133-134), xoá dòng `'attendance': 'Giám sát quân số',` và `'attendance-detail': 'Chi tiết giám sát quân số',` khỏi `titles`, xoá `case 'attendance': loadAttendanceSummary(); break;`.

c) Đổi giá trị mặc định của biến trạng thái ở dòng 2060:

```js
let currentTabName = 'schedule-progress';
```

d) Trong `setTrainingFilter` (dòng 2082), xoá nhánh `attendance`:

```js
    if (currentTabName === 'schedule-progress') loadTrainingSchedule();
    else if (currentTabName === 'safety') {
        currentSafetyType = currentTrainingType;
        loadSafetyDashboard();
    }
```

e) Xoá trọn các hàm `loadAttendanceSummary`, `openAttendanceDetail`, `loadAttendanceEvidence`, `renderAttendanceDetail`, `backFromAttendanceDetail` cùng dòng `window.<tên> = ...` của chúng, và biến `let attendanceDetailData = ...` (dòng 2062). Giữ lại `VIOLATION_TAG` và `TRAINING_LABEL` — Task 8 đang dùng.

f) `ROLES` (dòng 2875):

```js
const ROLES = {
    cbqh: { home: 'schedule-progress' },
    qtht: { home: 'monitoring' }
};
```

- [ ] **Step 5: Tìm các chỗ còn sót**

```bash
grep -n "ad-stream\|ad-tbody\|ad-evidence\|ad-metrics\|as-tbody\|as-metric\|as-title\|as-subtitle\|tt-filter-attendance\|attendanceDetailData\|openAttendanceDetail\|loadAttendanceSummary\|view-attendance" static/ tests/ -r
```

Kỳ vọng: **không** còn kết quả nào. Có kết quả nào thì xoá nốt.

- [ ] **Step 6: Chạy test**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: tất cả PASS, riêng "app.js nạp và chạy không ném lỗi" phải PASS (bắt được lỗi tham chiếu hàm đã xoá).

- [ ] **Step 7: Commit**

```bash
git add static/index.html static/app.js tests/test_ui.mjs
git commit -m "refactor: bỏ tab Giám sát quân số, gộp vào trang Lịch & Tiến độ

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Trang "Lịch & Tiến độ" — thẻ chỉ số, cột loại, tiến độ theo giờ, bộ lọc

Gom sáu phản hồi cho màn này:
- *"hiển thị số lượng camera đang trực tuyến trên tổng số camera hiện có thay vào mục 'Tiến độ hoàn thành chung'"* và *"Anh bỏ hộ em phần 'Tiến độ hoàn thành chung'"*.
- *"Quân số thực tế thì phải hiển thị số lượng người đang có trong toàn bộ ca đang diễn ra"*.
- *"thanh tiến độ thực tế là tiến độ tính theo thời gian từ lúc bắt đầu đến lúc kết thúc"*; *"cột Quân số chỉ hiển thị số quân nhân chuẩn theo yêu cầu thui"*.
- *"thêm hộ em 1 cột để hiện loại"*.
- *"lọc theo khoảng thời gian, có bộ lọc theo trạng thái, theo ca và thanh tìm kiếm"*.
- *"Role của người dùng thì không có mục 'Thêm ca huấn luyện'. Phần này chỉ có ở role quản trị."*

**Files:**
- Modify: `static/index.html:523-583` (`view-schedule-progress`)
- Modify: `static/app.js` — `loadTrainingSchedule()` (dòng 2118-2166), `startAppSession()` (dòng 2962, đặt ngày mặc định)
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `GET /api/v1/summary/training` với `date_from`, `date_to`, `shift`, `state`, `q`, `training_type`; mỗi session có `day`, `time_progress_pct`, `remaining_minutes`, `total_minutes`, `live_present`, `required`; `stats` có `cameras_online`, `cameras_total`, `present_total`, `required_total`, `running_sessions`, `violation_total` (Task 2 + Task 4).
- Produces: id mới trong DOM — `dt-date-from`, `dt-date-to`, `dt-filter-shift`, `dt-filter-state`, `dt-metric-cameras`, `dt-btn-add`.
- Produces: `dt-metric-progress` và `dt-metric-progress-bar` **bị xoá** — mã nào còn tham chiếu là lỗi.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[3]`:

```js
console.log('\n[3c] Trang Lịch & Tiến độ');

window.switchNavTab('schedule-progress');
await sleep(1000);

const labels = [...doc.querySelectorAll('#view-schedule-progress .metric-label')]
    .map(e => e.textContent.trim());
check('bỏ thẻ Tiến độ hoàn thành chung',
    !labels.some(l => l.includes('Tiến độ hoàn thành chung')), labels.join(' | '));
check('có thẻ camera trực tuyến trên tổng',
    labels.some(l => l.includes('Camera trực tuyến')), labels.join(' | '));
check('thẻ camera hiện dạng n/m',
    /^\d+\/\d+$/.test(doc.getElementById('dt-metric-cameras').textContent.trim()),
    doc.getElementById('dt-metric-cameras').textContent);
check('không còn thanh tiến độ chung',
    doc.getElementById('dt-metric-progress-bar') === null);

const th = [...doc.querySelectorAll('#view-schedule-progress thead th')].map(e => e.textContent.trim());
check('bảng lịch có cột LOẠI', th.includes('LOẠI'), th.join(' | '));
check('vẫn có cột tiến độ thực tế', th.includes('TIẾN ĐỘ THỰC TẾ'), th.join(' | '));

check('có ô lọc từ ngày', !!doc.getElementById('dt-date-from'));
check('có ô lọc đến ngày', !!doc.getElementById('dt-date-to'));
check('có bộ lọc theo ca', !!doc.getElementById('dt-filter-shift'));
check('có bộ lọc theo trạng thái', !!doc.getElementById('dt-filter-state'));
check('có thanh tìm kiếm', !!doc.getElementById('dt-schedule-search'));

const addBtn = doc.getElementById('dt-btn-add');
check('nút thêm ca huấn luyện thuộc nhóm chỉ quản trị mới thấy',
    !!addBtn && addBtn.classList.contains('role-only') && addBtn.dataset.role === 'qtht');

window.applyRole(cbqhUser);
await sleep(200);
check('CBQH không thấy nút thêm ca huấn luyện', addBtn.style.display === 'none',
    addBtn.style.display);
window.applyRole(qtht);
await sleep(200);
check('QTHT thấy nút thêm ca huấn luyện', addBtn.style.display !== 'none');

const quanSo = doc.querySelector('#dt-schedule-tbody tr td:nth-child(9)');
check('cột quân số chỉ hiện sĩ số chuẩn, không kèm dấu gạch chéo',
    !!quanSo && !quanSo.textContent.includes('/'), quanSo && quanSo.textContent.trim());
```

Đặt khối này **sau** nhóm `[3b]` để dùng được biến `cbqhUser` và `qtht`.

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "có thẻ camera trực tuyến trên tổng", "bảng lịch có cột LOẠI", "có ô lọc từ ngày", "nút thêm ca huấn luyện thuộc nhóm chỉ quản trị mới thấy".

- [ ] **Step 3: Thay HTML của `view-schedule-progress`**

Thay trọn `static/index.html:523-583` bằng:

```html
                <!-- MÀN 1.1 — TỔNG HỢP LỊCH & TIẾN ĐỘ HUẤN LUYỆN -->
                <section id="view-schedule-progress" class="page-view">
                    <div class="glass-card section-card">
                        <div class="card-header flex-between">
                            <div>
                                <h2>TỔNG HỢP LỊCH VÀ TIẾN ĐỘ HUẤN LUYỆN</h2>
                                <p class="card-subtitle">LỊCH HUẤN LUYỆN TOÀN ĐƠN VỊ VÀ TIẾN ĐỘ THỰC HIỆN THEO THỜI GIAN THỰC</p>
                            </div>
                            <button class="btn-primary-green role-only" data-role="qtht"
                                    id="dt-btn-add" onclick="openScheduleModal()">➕ Thêm ca huấn luyện</button>
                        </div>

                        <div class="training-filter" id="tt-filter-schedule">
                            <button class="tt-btn active" data-tt="" onclick="setTrainingFilter('')">Tất cả</button>
                            <button class="tt-btn" data-tt="dao_tao" onclick="setTrainingFilter('dao_tao')">Huấn luyện đào tạo</button>
                            <button class="tt-btn" data-tt="chien_dau" onclick="setTrainingFilter('chien_dau')">Huấn luyện chiến đấu</button>
                        </div>

                        <div class="table-filters" style="margin: 12px 0;">
                            <label class="filter-inline">Từ ngày
                                <input type="date" id="dt-date-from" class="filter-select" onchange="loadTrainingSchedule()">
                            </label>
                            <label class="filter-inline">Đến ngày
                                <input type="date" id="dt-date-to" class="filter-select" onchange="loadTrainingSchedule()">
                            </label>
                            <select id="dt-filter-shift" class="filter-select" onchange="loadTrainingSchedule()">
                                <option value="">Tất cả ca</option>
                                <option value="Ca sáng">Ca sáng</option>
                                <option value="Ca chiều">Ca chiều</option>
                                <option value="Ca đêm">Ca đêm</option>
                            </select>
                            <select id="dt-filter-state" class="filter-select" onchange="loadTrainingSchedule()">
                                <option value="">Tất cả trạng thái</option>
                                <option value="upcoming">Chưa tới giờ</option>
                                <option value="check_start">Đang điểm danh đầu giờ</option>
                                <option value="running">Đang diễn ra</option>
                                <option value="check_end">Đang điểm danh cuối giờ</option>
                                <option value="finished">Đã kết thúc</option>
                            </select>
                            <input type="text" id="dt-schedule-search" class="filter-select"
                                   placeholder="Tìm theo tên ca / bài học / đơn vị" oninput="loadTrainingSchedule()">
                        </div>

                        <div class="log-metrics-grid">
                            <div class="metric-card">
                                <span class="metric-label">Lớp đang huấn luyện</span>
                                <span class="metric-val" id="dt-metric-running">0</span>
                            </div>
                            <div class="metric-card">
                                <span class="metric-label">Camera trực tuyến / tổng số</span>
                                <span class="metric-val text-green" id="dt-metric-cameras">0/0</span>
                            </div>
                            <div class="metric-card">
                                <span class="metric-label">Quân số thực tế / yêu cầu</span>
                                <span class="metric-val" id="dt-metric-headcount">0/0</span>
                            </div>
                            <div class="metric-card">
                                <span class="metric-label">Vi phạm giờ giấc</span>
                                <span class="metric-val text-amber" id="dt-metric-violations">0</span>
                            </div>
                        </div>

                        <div class="table-responsive">
                            <table class="personnel-table">
                                <thead>
                                    <tr>
                                        <th>NGÀY</th>
                                        <th>CA</th>
                                        <th>LOẠI</th>
                                        <th>NỘI DUNG HUẤN LUYỆN</th>
                                        <th>ĐƠN VỊ</th>
                                        <th>KHUNG GIỜ</th>
                                        <th>TRẠNG THÁI</th>
                                        <th>TIẾN ĐỘ THỰC TẾ</th>
                                        <th>QUÂN SỐ</th>
                                        <th>THAO TÁC</th>
                                    </tr>
                                </thead>
                                <tbody id="dt-schedule-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </section>
```

Thứ tự cột: NGÀY · CA · LOẠI · NỘI DUNG · ĐƠN VỊ · KHUNG GIỜ · TRẠNG THÁI · TIẾN ĐỘ · **QUÂN SỐ (cột 9)** · THAO TÁC — khớp với `td:nth-child(9)` mà test ở Step 1 kiểm.

- [ ] **Step 4: Viết lại `loadTrainingSchedule`**

Thay trọn hàm (dòng 2118-2166):

```js
async function loadTrainingSchedule() {
    const tbody = document.getElementById('dt-schedule-tbody');
    if (!tbody) return;

    const val = (id) => ((document.getElementById(id) || {}).value || '').trim();
    const params = new URLSearchParams();
    if (currentTrainingType) params.set('training_type', currentTrainingType);
    if (val('dt-date-from')) params.set('date_from', val('dt-date-from'));
    if (val('dt-date-to')) params.set('date_to', val('dt-date-to'));
    if (val('dt-filter-shift')) params.set('shift', val('dt-filter-shift'));
    if (val('dt-filter-state')) params.set('state', val('dt-filter-state'));
    if (val('dt-schedule-search')) params.set('q', val('dt-schedule-search'));

    try {
        const data = await getJson(`/api/v1/summary/training?${params.toString()}`);
        const st = data.stats;

        document.getElementById('dt-metric-running').textContent = st.running_sessions;
        document.getElementById('dt-metric-cameras').textContent =
            `${st.cameras_online}/${st.cameras_total}`;
        document.getElementById('dt-metric-headcount').textContent =
            `${st.present_total}/${st.required_total}`;
        document.getElementById('dt-metric-violations').textContent = st.violation_total;

        tbody.innerHTML = data.sessions.length ? '' :
            `<tr><td colspan="10" class="empty-row">Không có lịch huấn luyện nào khớp bộ lọc</td></tr>`;

        data.sessions.forEach(s => {
            // Tiến độ ở đây là tiến độ theo đồng hồ: lớp đã học bao lâu trong
            // khung giờ của nó, còn bao lâu nữa thì tan. Không phải số phút
            // camera quan sát được.
            const prog = Math.round(s.time_progress_pct || 0);
            const remain = s.remaining_minutes || 0;
            const conLai = s.state === 'finished' ? 'Đã kết thúc'
                : s.state === 'upcoming' ? 'Chưa bắt đầu'
                : `còn ${remain} phút`;

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td class="font-mono">${esc(s.day || '')}</td>
                <td>${esc(s.shift || '—')}</td>
                <td>${TRAINING_TAG[s.training_type] || '<span class="tt-tag">—</span>'}</td>
                <td><strong>${esc(s.name)}</strong>
                    ${s.lesson_name ? `<div class="cell-subtext">${esc(s.lesson_name)}</div>` : ''}</td>
                <td>${esc(s.unit || '—')}
                    ${s.instructor ? `<div class="cell-subtext">${esc(s.instructor)}</div>` : ''}</td>
                <td class="font-mono">${esc(s.start_time || '--:--')} – ${esc(s.end_time || '--:--')}</td>
                <td><span class="status-tag ${STATE_CLASS[s.state] || 'status-neutral'}">${esc(s.state_label)}</span></td>
                <td>
                    <div class="progress-track"><div class="progress-fill" style="width:${prog}%"></div></div>
                    <span class="progress-text">${prog}% · ${conLai}</span>
                </td>
                <td><strong>${s.required || 0}</strong></td>
                <td><button class="btn-event-clip" onclick="openSessionDetail('${s.id}','${s.schedule_id}')">Xem chi tiết</button></td>`;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="10" class="empty-row">Lỗi tải lịch: ${esc(e.message)}</td></tr>`;
    }
}
window.loadTrainingSchedule = loadTrainingSchedule;
```

- [ ] **Step 5: Sửa ngày mặc định lúc khởi động**

Trong `startAppSession()` (`static/app.js:2962`), thay khối đặt `dt-schedule-date` (id này đã bị xoá) bằng:

```js
    // Mặc định xem lịch hôm nay; người dùng mở rộng bằng hai ô khoảng ngày
    const today = new Date().toISOString().slice(0, 10);
    ['dt-date-from', 'dt-date-to'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = today;
    });
```

- [ ] **Step 6: Thêm CSS cho nhãn bộ lọc**

Thêm vào cuối `static/style.css`:

```css
.filter-inline {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 600;
    color: var(--military-muted);
}
```

- [ ] **Step 7: Chạy test**

```bash
node tests/test_ui.mjs
grep -n "dt-metric-progress\|dt-schedule-date" static/ -r
```

Kỳ vọng: test PASS; `grep` không còn kết quả.

- [ ] **Step 8: Commit**

```bash
git add static/index.html static/app.js static/style.css tests/test_ui.mjs
git commit -m "feat: trang Lịch & Tiến độ có cột loại, tiến độ theo giờ, bộ lọc đầy đủ và đếm camera

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: Avatar ở góc trái dưới cùng và thanh trên cùng

Khách: *"Phần avatar để góc bên trái dưới cùng, ấn vào avatar có nút đăng xuất và thay đổi thông tin cá nhân (như thay đổi password)."* Và: *"Phần 'Quân số: 7/45' … không cần nữa."* Và sửa nguyên nhân gốc số 3 — pill camera là chữ chết.

**Files:**
- Modify: `static/index.html` — chuyển khối `.user-badge` + `.logout-btn` từ `<header>` (dòng 153-162) xuống cuối `<aside class="sidebar">`; sửa pill camera (dòng 131-141); thêm `#account-modal` cuối `<body>`
- Modify: `static/app.js` — `applyRole()` (dòng 2883), `pollLiveStatus()` (dòng 733); thêm `toggleAccountMenu`, `openAccountModal`, `submitPasswordForm`, `submitProfileForm`
- Modify: `static/style.css` — `.sidebar-footer`, `.account-menu`
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `POST /api/v1/auth/password`, `PATCH /api/v1/auth/profile` (Task 6); `GET /api/v1/cameras` để đếm trực tuyến.
- Produces: id mới — `sidebar-user`, `account-menu`, `account-modal`, `topbar-camera-stat`; `topbar-attendance-stat` **bị xoá**.
- Produces: `window.toggleAccountMenu()`, `window.openAccountModal()`, `window.closeAccountModal()`, `window.submitPasswordForm(event)`, `window.submitProfileForm(event)`.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[3c]`:

```js
console.log('\n[3d] Tài khoản ở góc trái dưới cùng');

check('avatar nằm trong sidebar chứ không ở thanh trên',
    !!doc.querySelector('.sidebar #sidebar-user'));
check('thanh trên không còn khối tài khoản',
    doc.querySelector('.top-header .user-badge') === null);
check('bỏ pill Quân số trên thanh trên',
    doc.getElementById('topbar-attendance-stat') === null);
check('pill camera có id để cập nhật được',
    !!doc.getElementById('topbar-camera-stat'));
check('pill camera hiện dạng n/m trực tuyến',
    /\d+\/\d+/.test(doc.getElementById('topbar-camera-stat').textContent),
    doc.getElementById('topbar-camera-stat').textContent);

const menu = doc.getElementById('account-menu');
check('menu tài khoản mặc định đóng', menu.style.display === 'none', menu.style.display);
window.toggleAccountMenu();
check('bấm avatar thì mở menu', menu.style.display === 'flex', menu.style.display);
check('menu có nút đăng xuất',
    menu.textContent.includes('Đăng xuất'), menu.textContent);
check('menu có nút đổi thông tin cá nhân',
    menu.textContent.includes('Thông tin cá nhân'), menu.textContent);
window.toggleAccountMenu();
check('bấm lần nữa thì đóng menu', menu.style.display === 'none');

window.openAccountModal();
check('mở được hộp thông tin cá nhân',
    doc.getElementById('account-modal').style.display === 'flex');
check('hộp điền sẵn tên hiển thị hiện tại',
    doc.getElementById('acc-display-name').value.length > 0,
    doc.getElementById('acc-display-name').value);
check('hộp có ô đổi mật khẩu',
    !!doc.getElementById('acc-old-password') && !!doc.getElementById('acc-new-password'));
window.closeAccountModal();
check('đóng được hộp', doc.getElementById('account-modal').style.display === 'none');
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "avatar nằm trong sidebar", "bỏ pill Quân số", "pill camera có id".

- [ ] **Step 3: Sửa thanh trên cùng trong `static/index.html`**

Thay khối `.header-badges` (dòng 129-142) bằng:

```html
                <div class="header-badges">
                    <div class="status-pill pill-online">
                        <span class="pill-dot"></span>
                        <span id="topbar-camera-stat">Camera: 0/0 trực tuyến</span>
                    </div>
                    <div class="status-pill pill-neutral">
                        <span class="pill-icon">💡</span>
                        <span id="topbar-alert-stat">Cảnh báo: 0</span>
                    </div>
                </div>
```

Và xoá trọn `<div class="user-badge">…</div>` cùng `<button class="logout-btn" …>` khỏi `.header-right` (dòng 153-162); `.header-right` chỉ còn đồng hồ và nút chuông.

- [ ] **Step 4: Thêm khối tài khoản vào cuối sidebar**

Chèn ngay **trước** `</aside>` trong `static/index.html`:

```html
            <!-- Tài khoản: góc trái dưới cùng, bấm vào mở menu -->
            <div class="sidebar-footer">
                <div id="account-menu" class="account-menu" style="display: none;">
                    <button type="button" class="account-menu-item" onclick="openAccountModal()">
                        👤 Thông tin cá nhân
                    </button>
                    <button type="button" class="account-menu-item account-menu-danger" onclick="handleLogout()">
                        ⏻ Đăng xuất
                    </button>
                </div>
                <button type="button" class="sidebar-user" id="sidebar-user" onclick="toggleAccountMenu()">
                    <span class="avatar-letter" id="user-avatar">H</span>
                    <span class="user-block">
                        <span class="user-name" id="user-name">—</span>
                        <span class="user-role" id="user-role">—</span>
                    </span>
                    <span class="account-caret">⌃</span>
                </button>
            </div>
```

- [ ] **Step 5: Thêm hộp thông tin cá nhân**

Chèn trước `<script src="/static/app.js"></script>`:

```html
    <!-- Thông tin cá nhân và đổi mật khẩu -->
    <div id="account-modal" class="modal-backdrop" style="display: none;">
        <div class="modal-content">
            <div class="modal-header">
                <h3>Thông tin cá nhân</h3>
                <button class="modal-close" onclick="closeAccountModal()">×</button>
            </div>

            <form id="account-profile-form" onsubmit="submitProfileForm(event)">
                <div class="form-group">
                    <label for="acc-display-name">Tên hiển thị</label>
                    <input type="text" id="acc-display-name" class="form-input" required>
                </div>
                <p id="acc-profile-status" class="status-message"></p>
                <button type="submit" class="btn-primary-green">Lưu tên hiển thị</button>
            </form>

            <hr style="margin: 20px 0; border: none; border-top: 1px solid var(--card-border);">

            <form id="account-password-form" onsubmit="submitPasswordForm(event)">
                <div class="form-group">
                    <label for="acc-old-password">Mật khẩu hiện tại</label>
                    <input type="password" id="acc-old-password" class="form-input"
                           autocomplete="current-password" required>
                </div>
                <div class="form-group">
                    <label for="acc-new-password">Mật khẩu mới (tối thiểu 8 ký tự)</label>
                    <input type="password" id="acc-new-password" class="form-input"
                           autocomplete="new-password" minlength="8" required>
                </div>
                <p id="acc-password-status" class="status-message"></p>
                <button type="submit" class="btn-primary-green">Đổi mật khẩu</button>
            </form>
        </div>
    </div>
```

- [ ] **Step 6: Thêm JS**

Chèn vào `static/app.js` ngay trước khối `// ----------------- INITIALIZATION -----------------`:

```js
// =====================================================================
// MENU TÀI KHOẢN Ở GÓC TRÁI DƯỚI CÙNG
// =====================================================================

function toggleAccountMenu() {
    const menu = document.getElementById('account-menu');
    if (!menu) return;
    menu.style.display = menu.style.display === 'flex' ? 'none' : 'flex';
}
window.toggleAccountMenu = toggleAccountMenu;

document.addEventListener('click', (e) => {
    const footer = e.target.closest && e.target.closest('.sidebar-footer');
    if (footer) return;
    const menu = document.getElementById('account-menu');
    if (menu) menu.style.display = 'none';
});

function openAccountModal() {
    const modal = document.getElementById('account-modal');
    if (!modal) return;
    const menu = document.getElementById('account-menu');
    if (menu) menu.style.display = 'none';

    document.getElementById('acc-display-name').value =
        (currentUser && currentUser.display_name) || '';
    ['acc-old-password', 'acc-new-password'].forEach(id => {
        document.getElementById(id).value = '';
    });
    ['acc-profile-status', 'acc-password-status'].forEach(id => {
        document.getElementById(id).textContent = '';
    });
    modal.style.display = 'flex';
}
window.openAccountModal = openAccountModal;

function closeAccountModal() {
    const modal = document.getElementById('account-modal');
    if (modal) modal.style.display = 'none';
}
window.closeAccountModal = closeAccountModal;

function setStatus(id, text, ok) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.style.color = ok ? 'var(--primary-green)' : 'var(--danger-red)';
}

async function submitProfileForm(event) {
    event.preventDefault();
    if (!currentUser) return;
    const displayName = document.getElementById('acc-display-name').value.trim();
    try {
        const res = await fetch('/api/v1/auth/profile', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUser.username, display_name: displayName })
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        const user = await res.json();
        try { localStorage.setItem('horus_user', JSON.stringify(user)); } catch (e) { /* chế độ riêng tư */ }
        applyRole(user);
        setStatus('acc-profile-status', '✓ Đã lưu tên hiển thị', true);
    } catch (e) {
        setStatus('acc-profile-status', `✗ ${e.message}`, false);
    }
}
window.submitProfileForm = submitProfileForm;

async function submitPasswordForm(event) {
    event.preventDefault();
    if (!currentUser) return;
    try {
        const res = await fetch('/api/v1/auth/password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser.username,
                old_password: document.getElementById('acc-old-password').value,
                new_password: document.getElementById('acc-new-password').value
            })
        });
        if (!res.ok) throw new Error(describeApiError(await res.json()));
        ['acc-old-password', 'acc-new-password'].forEach(id => {
            document.getElementById(id).value = '';
        });
        setStatus('acc-password-status', '✓ Đã đổi mật khẩu', true);
    } catch (e) {
        setStatus('acc-password-status', `✗ ${e.message}`, false);
    }
}
window.submitPasswordForm = submitPasswordForm;
```

- [ ] **Step 7: Cập nhật pill camera, bỏ pill quân số**

`pollLiveStatus()` chạy 2 giây một lần, nên không hỏi `/api/v1/cameras` trong đó — nhớ tổng số camera vào một biến và chỉ làm mới khi danh sách camera thật sự đổi.

a) Thêm ngay trên `pollLiveStatus()` (`static/app.js:733`):

```js
// Tổng số thiết bị camera. Chỉ đổi khi thêm / xoá camera, nên không việc gì
// phải hỏi lại máy chủ mỗi nhịp 2 giây của pollLiveStatus.
let knownCameraTotal = 0;

async function refreshCameraTotal() {
    try { knownCameraTotal = (await getJson('/api/v1/cameras')).total; } catch (e) { /* bỏ qua nhịp này */ }
}
```

b) Gọi `refreshCameraTotal()` một lần trong `startAppSession()` (cạnh `connectEventStream()`), và thêm `knownCameraTotal = cameraWallData.length;` vào `loadCameraWall()` ngay sau khi gán `cameraWallData = data.items;`, cùng `knownCameraTotal = data.total;` trong `loadCameras()` sau khi nạp danh sách.

c) Trong `pollLiveStatus()`, thay khối cập nhật `topbarAttendanceStat` bằng:

```js
        const camPill = document.getElementById('topbar-camera-stat');
        if (camPill) {
            const online = (status.cameras_running || []).length;
            camPill.textContent = `Camera: ${online}/${knownCameraTotal} trực tuyến`;
        }
```

Xoá dòng khai báo `const topbarAttendanceStat = ...` (dòng 20) và mọi chỗ dùng nó.

- [ ] **Step 8: Thêm CSS**

Thêm vào cuối `static/style.css`:

```css
/* ----------------- TÀI KHOẢN Ở GÓC TRÁI DƯỚI CÙNG ----------------- */
.sidebar-footer {
    margin-top: auto;
    position: relative;
    padding: 10px;
    border-top: 1px solid var(--sidebar-border);
}

.sidebar-user {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 8px 10px;
    border: 1px solid transparent;
    border-radius: 10px;
    background: none;
    cursor: pointer;
    text-align: left;
}
.sidebar-user:hover { background: #ffffff; border-color: var(--sidebar-border); }
.sidebar-user .user-block { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.sidebar-user .user-name,
.sidebar-user .user-role { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-caret { color: #6b937e; font-size: 12px; }

.account-menu {
    position: absolute;
    left: 10px;
    right: 10px;
    bottom: calc(100% - 4px);
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: #ffffff;
    border: 1px solid var(--sidebar-border);
    border-radius: 10px;
    padding: 6px;
    box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
    z-index: 20;
}

.account-menu-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 10px;
    border: none;
    border-radius: 7px;
    background: none;
    color: #435b4f;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    text-align: left;
}
.account-menu-item:hover { background: var(--primary-green-light); }
.account-menu-danger { color: var(--danger-red); }
.account-menu-danger:hover { background: rgba(220, 38, 38, 0.08); }
```

- [ ] **Step 9: Chạy test**

```bash
node tests/test_ui.mjs
grep -n "topbarAttendanceStat\|logout-btn" static/ -r
```

Kỳ vọng: test PASS; `grep` không còn kết quả (trừ CSS `.logout-btn` có thể xoá luôn).

- [ ] **Step 10: Kiểm bằng mắt**

Đăng nhập, bấm avatar góc trái dưới → menu mở. Vào **Thông tin cá nhân**, đổi mật khẩu, đăng xuất, đăng nhập lại bằng mật khẩu mới.

- [ ] **Step 11: Commit**

```bash
git add static/index.html static/app.js static/style.css tests/test_ui.mjs
git commit -m "feat: avatar và menu tài khoản ở góc trái dưới cùng, pill camera đếm động

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: An toàn bắn đạn thật — cảnh báo đỏ toàn hệ thống và bộ lọc

Khách: *"Khi có người vi phạm thì sẽ hiện thông báo toàn bộ hệ thống màu đỏ kèm lỗi như 'Có người đi vào vùng cấm' hiện ngay bên trên cùng ở giữa màn hình. Và khi ấn vào thông báo thì vào trang An toàn bắn đạn thật và có hiển thị trạng thái là Cảnh báo cho camera nào."* Cộng: *"Bổ sung thêm bộ lọc và bộ tìm kiếm theo camera/bài học/loại/trạng thái."*

Lớp phủ `#intrusion-overlay` hiện tại che kín màn hình và chặn thao tác. Đổi thành một dải đỏ hẹp bám đỉnh màn hình, giữa trang, bấm vào thì nhảy sang trang An toàn.

**Files:**
- Modify: `static/index.html` — thêm `#global-alert` ngay sau `<body>`; thêm hàng bộ lọc vào `view-safety` (sau `#tt-filter-safety`, dòng 753-758)
- Modify: `static/app.js` — `showIntrusionAlert()` (dòng 2592), `dismissIntrusion()`, `openSafetyFromAlert()`, `renderSafetyCameraTable()` (dòng 2432), `loadSafetyDashboard()` (dòng 2406)
- Modify: `static/style.css` — `.global-alert`
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `GET /api/v1/summary/safety` — `state` chỉ còn `danger`/`normal` (Task 3); mỗi camera có `lesson_name`, `training_type`, `safety_state`, `safety_state_label`, `alarm_count` (đã có, nay có dữ liệu thật nhờ Task 1).
- Produces: id mới — `global-alert`, `global-alert-text`, `sf-search`, `sf-filter-state`.
- Produces: `window.showGlobalAlert(message)`, `window.hideGlobalAlert()`, `window.renderSafetyCameraTable` giữ nguyên tên.
- Lớp phủ `#intrusion-overlay` và các hàm `showIntrusionAlert`, `dismissIntrusion`, `ackIntrusionFromAlert` **bị thay** bằng dải cảnh báo; `toggleSafetySiren` giữ nguyên nhưng chỉ còn tác động lên `#safety-alert-banner` và `#global-alert`.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[3d]`:

```js
console.log('\n[5b] Cảnh báo an toàn toàn hệ thống');

const gAlert = doc.getElementById('global-alert');
check('có dải cảnh báo toàn hệ thống', !!gAlert);
check('mặc định ẩn', gAlert.style.display === 'none', gAlert.style.display);

window.switchNavTab('logs');
await sleep(400);
window.showGlobalAlert('Có người đi vào vùng cấm');
check('có vi phạm thì dải đỏ hiện dù đang ở màn khác',
    gAlert.style.display === 'flex', gAlert.style.display);
check('dải đỏ ghi rõ lỗi',
    doc.getElementById('global-alert-text').textContent.includes('vùng cấm'),
    doc.getElementById('global-alert-text').textContent);

gAlert.querySelector('.global-alert-body').click();
await sleep(900);
check('bấm vào thông báo thì sang trang An toàn bắn đạn thật',
    doc.querySelector('.page-view.active').id === 'view-safety',
    doc.querySelector('.page-view.active').id);

window.hideGlobalAlert();
check('đóng được dải cảnh báo', gAlert.style.display === 'none');

console.log('\n[5c] Bộ lọc màn An toàn');
window.switchNavTab('safety');
await sleep(900);
check('có thanh tìm kiếm camera / bài học', !!doc.getElementById('sf-search'));
check('có bộ lọc theo trạng thái', !!doc.getElementById('sf-filter-state'));

const sfTh = [...doc.querySelectorAll('#view-safety thead th')].map(e => e.textContent.trim());
check('bảng camera có cột tên bài học', sfTh.includes('TÊN BÀI HỌC'), sfTh.join(' | '));
check('bảng camera có cột loại', sfTh.includes('LOẠI'), sfTh.join(' | '));

const sfRow = doc.querySelector('#sf-camera-tbody tr td:nth-child(3)');
check('cột tên bài học có dữ liệu chứ không để trống',
    !!sfRow && sfRow.textContent.trim() !== '' && sfRow.textContent.trim() !== '—',
    sfRow && sfRow.textContent.trim());
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "có dải cảnh báo toàn hệ thống", "có thanh tìm kiếm camera / bài học".

- [ ] **Step 3: Thêm dải cảnh báo vào `static/index.html`**

Chèn ngay sau thẻ mở `<body>`:

```html
    <!-- Cảnh báo vi phạm an toàn: bám đỉnh màn hình, nổi trên mọi trang -->
    <div id="global-alert" class="global-alert" style="display: none;">
        <div class="global-alert-body" onclick="openSafetyFromAlert()" title="Bấm để xem trang An toàn bắn đạn thật">
            <span class="global-alert-icon">⚠</span>
            <span id="global-alert-text">Có người đi vào vùng cấm</span>
        </div>
        <button type="button" class="global-alert-close" onclick="hideGlobalAlert()" title="Ẩn cảnh báo">×</button>
    </div>
```

- [ ] **Step 4: Thêm hàng bộ lọc vào `view-safety`**

Chèn ngay sau khối `<div class="training-filter" id="tt-filter-safety"> … </div>`:

```html
                        <div class="table-filters" style="margin: 12px 0;">
                            <input type="text" id="sf-search" class="filter-select"
                                   placeholder="Tìm theo camera / khu vực / bài học" oninput="renderSafetyCameraTable()">
                            <select id="sf-filter-state" class="filter-select" onchange="renderSafetyCameraTable()">
                                <option value="">Tất cả trạng thái</option>
                                <option value="danger">Cảnh báo</option>
                                <option value="normal">Bình thường</option>
                            </select>
                        </div>
```

- [ ] **Step 5: Thay lớp phủ bằng dải cảnh báo trong `static/app.js`**

Thay trọn `showIntrusionAlert`, `dismissIntrusion`, `ackIntrusionFromAlert`, `openSafetyFromAlert` (dòng 2590-2653) bằng:

```js
let pendingIntrusion = null;

function showGlobalAlert(message) {
    const bar = document.getElementById('global-alert');
    if (!bar) return;
    document.getElementById('global-alert-text').textContent = message;
    bar.style.display = 'flex';
    bar.classList.toggle('blinking', !isSafetySirenMuted);
}
window.showGlobalAlert = showGlobalAlert;

function hideGlobalAlert() {
    const bar = document.getElementById('global-alert');
    if (!bar) return;
    bar.style.display = 'none';
    bar.classList.remove('blinking');
    // Chỉ ẩn khỏi màn hình, sự kiện vẫn nằm trong danh sách chờ xử lý
    pendingIntrusion = null;
}
window.hideGlobalAlert = hideGlobalAlert;

// Vi phạm an toàn phải thấy ngay dù đang ở màn nào, nhưng che kín màn hình thì
// người trực không làm được gì khác. Nên chỉ một dải đỏ bám đỉnh, bấm vào là
// sang thẳng trang An toàn và thấy camera nào đang báo động.
function showIntrusionAlert(event) {
    pendingIntrusion = event;
    const zone = (event.detail || {}).zone_name;
    showGlobalAlert(zone
        ? `Có người đi vào ${zone}`
        : (event.message || 'Có người đi vào vùng cấm'));
}

function openSafetyFromAlert() {
    const bar = document.getElementById('global-alert');
    if (bar) bar.classList.remove('blinking');
    switchNavTab('safety');
}
window.openSafetyFromAlert = openSafetyFromAlert;
```

Xoá trọn `<div id="intrusion-overlay"> … </div>` khỏi `static/index.html` (dòng 1144-1168) và xoá `btn-intrusion-siren` khỏi mảng trong `toggleSafetySiren` (dòng 2665). Trong `toggleSafetySiren`, thay khối cuối bằng:

```js
    const banner = document.getElementById('safety-alert-banner');
    if (banner) banner.classList.toggle('blinking', !isSafetySirenMuted && !!activeIntrusion);
    const bar = document.getElementById('global-alert');
    if (bar) bar.classList.toggle('blinking', !isSafetySirenMuted && !!pendingIntrusion);
```

- [ ] **Step 6: Lọc và tìm kiếm bảng camera**

Thay `renderSafetyCameraTable` (dòng 2432-2461). Bảng cần dữ liệu ngay cả khi gọi lại từ ô lọc, nên nhớ danh sách camera vào biến ngoài:

```js
let safetyCameras = [];

function renderSafetyCameraTable(cameras) {
    const tbody = document.getElementById('sf-camera-tbody');
    if (!tbody) return;
    if (cameras) safetyCameras = cameras;

    const q = ((document.getElementById('sf-search') || {}).value || '').toLowerCase();
    const stateFilter = (document.getElementById('sf-filter-state') || {}).value || '';

    let rows = currentTrainingType
        ? safetyCameras.filter(c => c.training_type === currentTrainingType)
        : safetyCameras.slice();
    if (stateFilter) rows = rows.filter(c => c.safety_state === stateFilter);
    if (q) rows = rows.filter(c =>
        `${c.name || ''} ${c.area_name || ''} ${c.lesson_name || ''}`.toLowerCase().includes(q));

    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Không có camera nào khớp bộ lọc</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    rows.forEach((cam, index) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${index + 1}</td>
            <td><strong>${esc(cam.name)}</strong>${cam.area_name ? `<br><span class="muted">${esc(cam.area_name)}</span>` : ''}</td>
            <td>${esc(cam.lesson_name || '—')}</td>
            <td>${TRAINING_TAG[cam.training_type] || '—'}</td>
            <td>${cam.safety_state === 'danger'
                    ? `<span class="status-tag status-danger">Cảnh báo${cam.alarm_count > 1 ? ` (${cam.alarm_count})` : ''}</span>`
                    : '<span class="status-tag status-ok">Bình thường</span>'}</td>
            <td><button class="btn-event-clip" onclick="openSafetyDetail('${cam.id}')">Xem chi tiết</button></td>`;
        tbody.appendChild(tr);
    });
}
window.renderSafetyCameraTable = renderSafetyCameraTable;
```

- [ ] **Step 7: Thêm CSS cho dải cảnh báo**

Thêm vào cuối `static/style.css`:

```css
/* ----------------- CẢNH BÁO AN TOÀN TOÀN HỆ THỐNG ----------------- */
.global-alert {
    position: fixed;
    top: 12px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 300;
    display: flex;
    align-items: center;
    gap: 8px;
    max-width: min(720px, 92vw);
    padding: 10px 12px 10px 16px;
    border-radius: 10px;
    background: var(--danger-red);
    color: #ffffff;
    box-shadow: 0 10px 30px rgba(220, 38, 38, 0.35);
}

.global-alert-body {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    cursor: pointer;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
.global-alert-icon { font-size: 18px; }

.global-alert-close {
    border: none;
    background: rgba(255, 255, 255, 0.18);
    color: #ffffff;
    width: 24px;
    height: 24px;
    border-radius: 6px;
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
}
.global-alert-close:hover { background: rgba(255, 255, 255, 0.32); }

.global-alert.blinking { animation: global-alert-pulse 1s ease-in-out infinite; }

@keyframes global-alert-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
}
```

- [ ] **Step 8: Chạy test**

```bash
node tests/test_ui.mjs
grep -n "intrusion-overlay\|intrusion-photo\|intrusion-meta\|ackIntrusionFromAlert" static/ -r
```

Kỳ vọng: test PASS; `grep` không còn kết quả.

- [ ] **Step 9: Commit**

```bash
git add static/index.html static/app.js static/style.css tests/test_ui.mjs
git commit -m "feat: cảnh báo an toàn thành dải đỏ giữa đỉnh màn hình, thêm bộ lọc bảng camera

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 13: Chi tiết camera an toàn — nhật ký đã xử lý và camera phóng to

Khách: *"Phần phát hiện xâm nhập khi đã xử lý thì biến mất và ghi log ở bên dưới thay cho phần thư viện hình ảnh vi phạm gồm lỗi, thời gian cụ thể, video bằng chứng kèm theo."* Và: *"em cũng muốn phần camera có thể phóng to toàn màn hình và có thể zoom."*

**Files:**
- Modify: `static/index.html:780-822` (`view-safety-detail`)
- Modify: `static/app.js` — `loadSafetyDetail()` (dòng 2471-2521); xoá `renderViolationGallery()` (dòng 2527)
- Modify: `static/style.css` — bỏ `.violation-gallery` nếu không còn ai dùng
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `GET /api/v1/summary/safety` → `events: [{id, type, message, occurred_at, camera_id, camera_name, area_name, snapshot_url, clip_url, acked, acked_by, acked_at, detail: {zone_name}}]`.
- Consumes: `window.viewEventClip(eventId)` (đã có, `static/app.js:249`), `window.makeZoomable`, `window.openEvidence` (Task 7).
- Produces: id mới — `sfd-camera-box`, `sfd-log-tbody`. `sfd-gallery` và `renderViolationGallery` **bị xoá**.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[5c]`:

```js
console.log('\n[5d] Chi tiết camera an toàn');

const sfDetailBtn = doc.querySelector('#sf-camera-tbody tr button');
check('bảng camera có nút xem chi tiết', !!sfDetailBtn);
sfDetailBtn.click();
await sleep(1200);

check('mở đúng màn chi tiết camera',
    doc.querySelector('.page-view.active').id === 'view-safety-detail',
    doc.querySelector('.page-view.active').id);
check('khung camera gắn được zoom',
    doc.getElementById('sfd-camera-box').dataset.zoomable === '1');
check('bỏ thư viện hình ảnh vi phạm', doc.getElementById('sfd-gallery') === null);
check('có bảng nhật ký vi phạm đã xử lý', !!doc.getElementById('sfd-log-tbody'));

const logTh = [...doc.querySelectorAll('#view-safety-detail table th')].map(e => e.textContent.trim());
check('nhật ký có cột lỗi', logTh.includes('LỖI'), logTh.join(' | '));
check('nhật ký có cột thời gian', logTh.includes('THỜI GIAN'), logTh.join(' | '));
check('nhật ký có cột video bằng chứng',
    logTh.includes('VIDEO BẰNG CHỨNG'), logTh.join(' | '));
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "bỏ thư viện hình ảnh vi phạm" và "có bảng nhật ký vi phạm đã xử lý".

- [ ] **Step 3: Thay HTML của `view-safety-detail`**

Thay từ `<div class="safety-grid">` tới hết `<div class="violation-gallery" id="sfd-gallery"></div>` (dòng 794-821) bằng:

```html
                        <div class="safety-grid">
                            <div class="camera-tile is-running">
                                <div class="camera-tile-video" id="sfd-camera-box">
                                    <img id="sfd-stream" alt="Luồng camera giám sát an toàn">
                                    <div class="camera-tile-idle" id="sfd-idle">Camera chưa chạy</div>
                                </div>
                                <div class="camera-tile-foot">
                                    <span class="camera-tile-area" id="sfd-camera-caption"></span>
                                </div>
                            </div>

                            <div class="glass-card events-feed-card">
                                <div class="events-feed-header">
                                    <h2>PHÁT HIỆN XÂM NHẬP</h2>
                                    <span class="pending-badge" id="sfd-pending-badge">0 chờ xử lý</span>
                                </div>
                                <div class="events-list" id="sfd-events-list"></div>
                            </div>
                        </div>

                        <div class="card-header" style="margin-top: 20px;">
                            <div>
                                <h2>NHẬT KÝ VI PHẠM ĐÃ XỬ LÝ</h2>
                                <p class="card-subtitle">VI PHẠM XÁC NHẬN XONG RỜI KHỎI DANH SÁCH TRÊN VÀ ĐƯỢC GHI LẠI Ở ĐÂY</p>
                            </div>
                        </div>
                        <div class="table-responsive">
                            <table class="personnel-table">
                                <thead>
                                    <tr>
                                        <th>THỜI GIAN</th>
                                        <th>LỖI</th>
                                        <th>KHU VỰC</th>
                                        <th>ẢNH</th>
                                        <th>VIDEO BẰNG CHỨNG</th>
                                        <th>NGƯỜI XỬ LÝ</th>
                                    </tr>
                                </thead>
                                <tbody id="sfd-log-tbody"></tbody>
                            </table>
                        </div>
```

- [ ] **Step 4: Sửa `loadSafetyDetail` trong `static/app.js`**

Trong `loadSafetyDetail`, thay khối gắn luồng camera:

```js
        // Chỉ gắn khi ô chưa có luồng: gán lại src là mở lại kết nối MJPEG
        const box = document.getElementById('sfd-camera-box');
        const img = document.getElementById('sfd-stream');
        const idle = document.getElementById('sfd-idle');
        makeZoomable(box);
        if (cam.status === 'online') {
            if (!img.getAttribute('src')) attachStream(img, cam.id, true);
            idle.style.display = 'none';
        } else {
            detachStream(img);
            idle.style.display = '';
        }
```

và thay hai khối cuối (danh sách sự kiện + thư viện ảnh):

```js
        const mine = (data.events || []).filter(e => e.camera_id === cam.id);
        // Xử lý xong thì rời danh sách trên, xuống nhật ký bên dưới
        const pending = mine.filter(e => !e.acked);
        const handled = mine.filter(e => e.acked);
        document.getElementById('sfd-pending-badge').textContent = `${pending.length} chờ xử lý`;

        const list = document.getElementById('sfd-events-list');
        list.innerHTML = '';
        if (!pending.length) {
            list.innerHTML = '<p class="empty-hint">Không còn vi phạm nào chờ xử lý trên camera này</p>';
        } else {
            pending.forEach(ev => renderEventCard(list, ev, false));
        }

        renderSafetyLog(handled);
        setActiveIntrusion(data.active_intrusion);
```

- [ ] **Step 5: Thay `renderViolationGallery` bằng `renderSafetyLog`**

Thay trọn hàm `renderViolationGallery` (dòng 2527-2553):

```js
// Vi phạm đã xác nhận xử lý: rời danh sách trực tiếp và rơi xuống nhật ký, kèm
// lỗi, thời điểm chính xác, ảnh và đoạn ghi 10 giây làm bằng chứng.
function renderSafetyLog(events) {
    const tbody = document.getElementById('sfd-log-tbody');
    if (!tbody) return;

    if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Chưa có vi phạm nào được xử lý trên camera này</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    events.forEach(ev => {
        const when = new Date(ev.occurred_at).toLocaleString('vi-VN');
        const zone = (ev.detail || {}).zone_name || ev.area_name || '—';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="font-mono">${esc(when)}</td>
            <td><strong>${esc(ev.message)}</strong></td>
            <td>${esc(zone)}</td>
            <td>${ev.snapshot_url
                ? `<img class="evidence-thumb" src="${ev.snapshot_url}"
                        onclick="openEvidence('${ev.snapshot_url}','${esc(ev.message)} — ${esc(when)}')"
                        alt="Ảnh vi phạm">`
                : '<span class="muted">—</span>'}</td>
            <td>${ev.clip_url
                ? `<button class="btn-event-clip" onclick="viewEventClip('${ev.id}')">▶️ Xem clip 10s</button>`
                : '<span class="muted">Không có</span>'}</td>
            <td>${esc(ev.acked_by || '—')}
                ${ev.acked_at ? `<div class="cell-subtext font-mono">${esc(new Date(ev.acked_at).toLocaleString('vi-VN'))}</div>` : ''}</td>`;
        tbody.appendChild(tr);
    });
}
```

- [ ] **Step 6: Chạy test**

```bash
node tests/test_ui.mjs
grep -n "renderViolationGallery\|sfd-gallery" static/ -r
```

Kỳ vọng: test PASS; `grep` không còn kết quả.

- [ ] **Step 7: Kiểm bằng mắt**

Vào **An toàn bắn đạn thật** → **Xem chi tiết** một camera. Bấm ⛶ trên khung camera → toàn màn hình; lăn chuột → phóng to. Xác nhận xử lý một vi phạm → nó rời danh sách bên phải và xuất hiện trong bảng nhật ký bên dưới.

- [ ] **Step 8: Commit**

```bash
git add static/index.html static/app.js tests/test_ui.mjs
git commit -m "feat: vi phạm an toàn đã xử lý xuống nhật ký có video, camera phóng to zoom được

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 14: Nhật ký điểm danh — bộ lọc, cột tên bài học, cột xem chi tiết

Khách: *"bổ sung mục lọc theo ca, theo khoảng thời gian và thanh tìm kiếm có thể tìm kiếm theo tên bài"*, *"bỏ phần tên bài dưới ca điểm danh, thay vào đó thêm 1 cột tên bài học"*, *"Bổ sung thêm 1 cột cuối cùng là xem chi tiết nữa ạ."*

**Files:**
- Modify: `static/index.html:880-919` (hàng bộ lọc và `<thead>` của `view-logs`)
- Modify: `static/app.js` — `loadAttendanceLogs()` (dòng 1766), `renderAttendanceLogsTable()` (dòng 1833), `filterAttendanceLogsByStatus()` (dòng 1982)
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `GET /api/attendance-logs` với `unit`, `shift`, `date_from`, `date_to`, `q`; bản ghi có thêm `lesson_name`, `instructor`, `training_type` (Task 5).
- Produces: id mới — `log-filter-shift`, `log-date-from`, `log-date-to`, `log-search`.
- Produces: bảng có 12 cột; cột cuối là nút **Xem chi tiết** gọi `openLogModal(id)`.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[5d]`:

```js
console.log('\n[6b] Nhật ký điểm danh');

window.switchNavTab('logs');
await sleep(1000);

check('có bộ lọc theo ca', !!doc.getElementById('log-filter-shift'));
check('có ô lọc từ ngày', !!doc.getElementById('log-date-from'));
check('có ô lọc đến ngày', !!doc.getElementById('log-date-to'));
check('có thanh tìm kiếm theo tên bài', !!doc.getElementById('log-search'));

const logTh2 = [...doc.querySelectorAll('#view-logs thead th')].map(e => e.textContent.trim());
check('bảng nhật ký có cột tên bài học',
    logTh2.includes('TÊN BÀI HỌC'), logTh2.join(' | '));
check('cột cuối cùng là xem chi tiết',
    logTh2[logTh2.length - 1] === 'XEM CHI TIẾT', logTh2.join(' | '));

const logRow = doc.querySelector('#attendance-logs-tbody tr');
if (logRow && !logRow.querySelector('.empty-row')) {
    check('ô ca điểm danh không còn kèm tên bài bên dưới',
        !logRow.querySelector('td:nth-child(2) .cell-subtext'),
        logRow.querySelector('td:nth-child(2)').innerHTML.slice(0, 120));
    check('dòng nhật ký có nút xem chi tiết',
        !!logRow.querySelector('td:last-child button'));
} else {
    console.log('  BỎ QUA  chưa có bản ghi điểm danh nào để kiểm dòng');
}
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "có bộ lọc theo ca", "bảng nhật ký có cột tên bài học", "cột cuối cùng là xem chi tiết".

- [ ] **Step 3: Thay hàng bộ lọc và `<thead>` trong `static/index.html`**

Thay khối `<div class="table-filters" style="margin: 16px 0 12px;"> … </div>` (dòng 881-896) bằng:

```html
                        <div class="table-filters" style="margin: 16px 0 12px;">
                            <select id="log-filter-unit" class="filter-select" onchange="loadAttendanceLogs()">
                                <option value="all">Tất cả đơn vị</option>
                                <option value="Đại đội 1">Đại đội 1</option>
                                <option value="Đại đội 2">Đại đội 2</option>
                                <option value="Tiểu đoàn 3">Tiểu đoàn 3</option>
                            </select>
                            <select id="log-filter-shift" class="filter-select" onchange="loadAttendanceLogs()">
                                <option value="">Tất cả ca</option>
                                <option value="Ca sáng">Ca sáng</option>
                                <option value="Ca chiều">Ca chiều</option>
                                <option value="Ca đêm">Ca đêm</option>
                            </select>
                            <label class="filter-inline">Từ ngày
                                <input type="date" id="log-date-from" class="filter-select" onchange="loadAttendanceLogs()">
                            </label>
                            <label class="filter-inline">Đến ngày
                                <input type="date" id="log-date-to" class="filter-select" onchange="loadAttendanceLogs()">
                            </label>
                            <input type="text" id="log-search" class="filter-select"
                                   placeholder="Tìm theo tên bài học" oninput="loadAttendanceLogs()">
                            <select id="log-filter-status" class="filter-select" onchange="filterAttendanceLogsByStatus()">
                                <option value="all">Tất cả trạng thái</option>
                                <option value="success">Đủ quân số</option>
                                <option value="warning">Thiếu quân số</option>
                            </select>
                        </div>
```

Thay `<thead>` của bảng (dòng 900-913) bằng:

```html
                                <thead>
                                    <tr>
                                        <th>THỜI GIAN</th>
                                        <th>CA ĐIỂM DANH</th>
                                        <th>TÊN BÀI HỌC</th>
                                        <th>ĐƠN VỊ</th>
                                        <th>SĨ SỐ YÊU CẦU</th>
                                        <th>ĐẦU GIỜ</th>
                                        <th>BẰNG CHỨNG ĐẦU GIỜ</th>
                                        <th>CUỐI GIỜ</th>
                                        <th>BẰNG CHỨNG CUỐI GIỜ</th>
                                        <th>QUÂN NHÂN VẮNG</th>
                                        <th>TRẠNG THÁI</th>
                                        <th>XEM CHI TIẾT</th>
                                    </tr>
                                </thead>
```

Cột **CHỈ HUY DUYỆT** chuyển xuống hộp chi tiết (Task 15) để bảng khỏi quá rộng.

- [ ] **Step 4: Sửa `loadAttendanceLogs`**

Thay trọn hàm (dòng 1766-1782):

```js
async function loadAttendanceLogs() {
    if (!attendanceLogsTbody) return;
    const val = (id) => ((document.getElementById(id) || {}).value || '').trim();

    const params = new URLSearchParams();
    params.set('unit', val('log-filter-unit') || 'all');
    if (val('log-filter-shift')) params.set('shift', val('log-filter-shift'));
    if (val('log-date-from')) params.set('date_from', val('log-date-from'));
    if (val('log-date-to')) params.set('date_to', val('log-date-to'));
    if (val('log-search')) params.set('q', val('log-search'));

    try {
        const res = await fetch(`/api/attendance-logs?${params.toString()}`);
        const result = await res.json();
        if (result.status === 'success' && result.data) {
            attendanceLogsData = result.data;
            filterAttendanceLogsByStatus();
            updateLogMetrics(attendanceLogsData);
        }
    } catch (e) {
        console.error('Error loading attendance logs:', e);
    }
}
window.loadAttendanceLogs = loadAttendanceLogs;
```

Gọi `filterAttendanceLogsByStatus()` thay vì `renderAttendanceLogsTable()` để bộ lọc trạng thái không bị bỏ qua mỗi lần nạp lại.

- [ ] **Step 5: Sửa `renderAttendanceLogsTable`**

Thay trọn hàm (dòng 1833-1878):

```js
function renderAttendanceLogsTable(logs) {
    if (!attendanceLogsTbody) return;
    attendanceLogsTbody.innerHTML = '';

    if (logs.length === 0) {
        attendanceLogsTbody.innerHTML = `<tr><td colspan="12" class="empty-row">Không có bản ghi điểm danh nào phù hợp</td></tr>`;
        return;
    }

    logs.forEach(log => {
        const row = document.createElement('tr');
        const statusClass = log.status_type === 'success' ? 'status-ok' : 'status-warning';
        const startCheck = getCheck(log, 'start');
        const endCheck = getCheck(log, 'end');

        row.innerHTML = `
            <td class="font-mono"><strong>${esc(log.date)}</strong> ${esc(log.time || '')}</td>
            <td>${esc(log.shift)}</td>
            <td><strong>${esc(log.lesson_name || log.schedule_name || '—')}</strong></td>
            <td><strong>${esc(log.unit)}</strong></td>
            <td>${log.required}</td>
            <td>${renderCheckCell(startCheck, log.required)}</td>
            <td>${renderEvidenceCell(startCheck, log, 'Đầu giờ')}</td>
            <td>${renderCheckCell(endCheck, log.required)}</td>
            <td>${renderEvidenceCell(endCheck, log, 'Cuối giờ')}</td>
            <td style="max-width: 260px;">${renderAbsentList(log)}</td>
            <td><span class="status-tag ${statusClass}">${esc(log.status)}</span></td>
            <td><button class="btn-event-clip" onclick="openLogModal('${log.id}')">Xem chi tiết</button></td>
        `;
        attendanceLogsTbody.appendChild(row);
    });
}
```

Bỏ luôn `row.onclick` bấm cả dòng: đã có nút riêng, bấm cả dòng dễ mở nhầm khi người dùng chỉ muốn bấm ảnh.

- [ ] **Step 6: Bỏ cột "chỉ huy duyệt" khỏi CSV? Không — giữ nguyên**

`exportAttendanceLogsCsv()` (dòng 2011) xuất đủ cột kể cả `commander`, không phụ thuộc bảng trên màn. Không sửa. Chỉ **xác nhận** bằng cách chạy thử xuất CSV ở Step 8.

- [ ] **Step 7: Chạy test**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 8: Kiểm bằng mắt**

Vào **Nhật ký điểm danh**: chọn ca, đặt khoảng ngày, gõ tên bài vào ô tìm kiếm — bảng phải lọc đúng. Bấm **Xuất Báo Cáo CSV** — file tải về vẫn đủ cột.

- [ ] **Step 9: Commit**

```bash
git add static/index.html static/app.js tests/test_ui.mjs
git commit -m "feat: nhật ký điểm danh có cột tên bài học, nút xem chi tiết và bộ lọc ca/ngày/từ khoá

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 15: Hộp chi tiết một ca trong nhật ký

Khách: *"phải hiện đầy đủ thông tin từ ca học, tên bài, người dạy, đơn vị, ngày, sĩ số chuẩn… Bên dưới gồm bằng chứng đầu giờ với cuối giờ nhưng em không ấn để xem toàn màn hình ảnh đó. Bên dưới là danh sách quân nhân vi phạm: anh để dạng table hộ em là tên gì, chức vụ, tên vi phạm."*

**Files:**
- Modify: `static/app.js` — `openLogModal()` (dòng 1873-1953), `openEvidenceModal()` (dòng 1962), `renderEvidenceCell()` (dòng 1809)
- Modify: `static/index.html:1246-1275` (`#log-modal`) — nới rộng hộp
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: bản ghi nhật ký nay có `lesson_name`, `instructor`, `field`, `class_name`, `training_type`, `start_time`, `end_time` (Task 5).
- Consumes: `window.openEvidence` (Task 7).
- Produces: `openEvidenceModal(src, phaseLabel, caption)` trở thành lớp mỏng gọi `openEvidence` — giữ tên vì `renderEvidenceCell` đang gọi; hộp `#evidence-modal` cũ bị xoá.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[6b]`:

```js
console.log('\n[6c] Hộp chi tiết một ca trong nhật ký');

const detailBtn = doc.querySelector('#attendance-logs-tbody tr td:last-child button');
if (detailBtn) {
    detailBtn.click();
    await sleep(600);
    check('mở được hộp chi tiết ca điểm danh',
        doc.getElementById('log-modal').style.display === 'flex');

    const keys = [...doc.querySelectorAll('#log-modal-info .detail-key')].map(e => e.textContent.trim());
    ['Ca', 'Tên bài học', 'Giáo viên phụ trách', 'Đơn vị', 'Ngày', 'Sĩ số yêu cầu']
        .forEach(k => check(`hộp chi tiết có ô ${k}`, keys.includes(k), keys.join(' | ')));

    const violTh = [...doc.querySelectorAll('#log-modal-violations th')].map(e => e.textContent.trim());
    if (violTh.length) {
        check('bảng vi phạm có cột chức vụ', violTh.includes('CHỨC VỤ'), violTh.join(' | '));
        check('bảng vi phạm có cột tên vi phạm', violTh.includes('TÊN VI PHẠM'), violTh.join(' | '));
    }

    const ev = doc.querySelector('#log-modal-evidence img');
    if (ev) {
        ev.click();
        await sleep(300);
        check('bấm ảnh bằng chứng trong hộp thì mở xem toàn màn hình',
            doc.getElementById('zoom-modal').style.display === 'flex');
        window.closeZoomModal();
    }
    window.closeLogModal();
} else {
    console.log('  BỎ QUA  chưa có bản ghi điểm danh nào để mở chi tiết');
}

check('đã bỏ hộp ảnh bằng chứng cũ', doc.getElementById('evidence-modal') === null);
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "đã bỏ hộp ảnh bằng chứng cũ", và các ô "Tên bài học" / "Giáo viên phụ trách" nếu có bản ghi.

- [ ] **Step 3: Gộp hộp ảnh cũ vào hộp phóng to chung**

Xoá trọn `<div id="evidence-modal"> … </div>` khỏi `static/index.html` (dòng 944-959).

Trong `static/app.js`, thay `openEvidenceModal` và `closeEvidenceModal` (dòng 1962-1980) bằng một lớp mỏng:

```js
// Giữ tên cũ vì bảng nhật ký đang gọi; thực chất dùng chung hộp phóng to
function openEvidenceModal(src, phaseLabel, caption) {
    openEvidence(src, caption || `Bằng chứng điểm danh ${String(phaseLabel).toLowerCase()}`);
}
window.openEvidenceModal = openEvidenceModal;
```

Xoá `closeEvidenceModal` và mọi tham chiếu tới nó.

- [ ] **Step 4: Viết lại phần thông tin của `openLogModal`**

Trong `openLogModal` (dòng 1873), thay mảng `info`:

```js
    const sm = log.attendance_summary || {};
    const info = [
        ['Ca', log.shift],
        ['Tên bài học', log.lesson_name || log.schedule_name],
        ['Loại huấn luyện', TRAINING_LABEL[log.training_type] || ''],
        ['Giáo viên phụ trách', log.instructor],
        ['Thao trường', log.field],
        ['Đội học / Lớp', log.class_name],
        ['Đơn vị', log.unit],
        ['Ngày', log.date],
        ['Khung giờ', log.start_time && log.end_time ? `${log.start_time} – ${log.end_time}` : null],
        ['Sĩ số yêu cầu', log.required],
        ['Trạng thái', log.status],
        ['Thời gian diễn ra thực tế', log.actual_minutes != null
            ? `${log.actual_minutes}/${log.scheduled_minutes || '?'} phút` : null],
        ['Đủ giờ', sm.present],
        ['Đi chậm', sm.late],
        ['Về sớm', sm.early_leave],
        ['Không tham gia', sm.absent],
        ['Chỉ huy duyệt', log.commander]
    ];
```

Khối `.filter(([, v]) => …)` ngay bên dưới giữ nguyên — nó đã bỏ các ô rỗng.

- [ ] **Step 5: Đổi bảng vi phạm sang đúng ba cột khách yêu cầu**

Trong cùng hàm, thay khối `log-modal-violations` (dòng 1934-1952):

```js
    // Bảng vi phạm chỉ có khi máy chủ quan sát được cả buổi
    const violators = (log.attendance || []).filter(i => (i.violations || []).length);
    document.getElementById('log-modal-violations').innerHTML = violators.length
        ? `<div class="table-responsive"><table class="personnel-table">
             <thead><tr><th>QUÂN NHÂN</th><th>CHỨC VỤ</th><th>ĐƠN VỊ</th><th>TÊN VI PHẠM</th></tr></thead>
             <tbody>${violators.map(i => {
                 const p = i.person || {};
                 const extra = [];
                 if (i.late_minutes) extra.push(`chậm ${i.late_minutes}′`);
                 if (i.early_leave_minutes) extra.push(`về sớm ${i.early_leave_minutes}′`);
                 return `<tr>
                    <td><strong>${esc(p.name || '')}</strong>
                        <div class="cell-subtext font-mono">${esc(p.military_id || '')}</div></td>
                    <td>${esc(p.rank || '—')}</td>
                    <td>${esc(p.unit || '—')}</td>
                    <td>${(i.violations || []).map(v => VIOLATION_TAG[v] || v).join(' ')}
                        ${extra.length ? `<div class="cell-subtext">${extra.join(' · ')}</div>` : ''}</td>
                 </tr>`;
             }).join('')}</tbody></table></div>`
        : (log.absent_personnel || []).length
            ? `<p class="muted">Danh sách vắng: ${esc((log.absent_personnel || []).join(', '))}</p>`
            : '<p class="empty-hint">Không có vi phạm giờ giấc trong ca này</p>';
```

- [ ] **Step 6: Nới rộng hộp chi tiết**

Trong `static/index.html`, đổi thẻ mở của nội dung `#log-modal`:

```html
        <div class="modal-content" style="width: 900px;">
```

- [ ] **Step 7: Chạy test**

```bash
node tests/test_ui.mjs
grep -n "evidence-modal\|closeEvidenceModal" static/ -r
```

Kỳ vọng: test PASS; `grep` không còn kết quả.

- [ ] **Step 8: Commit**

```bash
git add static/index.html static/app.js tests/test_ui.mjs
git commit -m "feat: hộp chi tiết nhật ký hiện đủ thông tin, ảnh phóng to được, vi phạm dạng bảng

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 16: Giám sát trực tiếp (quản trị) — camera phóng to, sự kiện đã xử lý biến mất, cột sự kiện cao hết màn

Khách: *"khi ấn vào 1 camera nào đó thì có thể xem camera đó toàn màn hình và zoom"*, *"ấn vào ảnh vi phạm có thể xem toàn màn hình và zoom"*, *"những sự kiện nào đã được xử lý thì sẽ không hiển thị đấy nữa"*, *"chiều dài của phần Dòng sự kiện trực tiếp là hết chiều dài màn hình cơ"*.

**Files:**
- Modify: `static/app.js` — `loadCameraWall()` (dòng 530-608), `renderEventCard()` (dòng 674-704), `ackEvent()` (dòng 706-731), `startAppSession()` (dòng 2962, nạp sự kiện gần đây)
- Modify: `static/style.css` — `.main-monitoring-grid`, `.events-feed-card`, `.events-list`
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `window.makeZoomable`, `window.openEvidence` (Task 7).
- Produces: `renderEventCard(container, event, prepend)` bỏ qua sự kiện đã `acked` khi container là `#events-list-container`; `ackEvent()` gỡ hẳn thẻ khỏi dòng sự kiện trực tiếp thay vì chỉ đổi nút.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[6c]`:

```js
console.log('\n[6d] Màn giám sát trực tiếp');

window.switchNavTab('monitoring');
await sleep(1200);

const tile = doc.querySelector('.camera-tile .camera-tile-video');
check('ô camera có khung video', !!tile);
check('ô camera gắn được zoom và toàn màn hình',
    !!tile && tile.dataset.zoomable === '1', tile && tile.dataset.zoomable);

const ackedEvent = {
    id: 'evt_da_xu_ly', type: 'INTRUSION', severity: 'critical',
    message: 'Sự kiện đã xử lý', occurred_at: new Date().toISOString(),
    acked: true, acked_by: 'Trực ban'
};
const before = doc.querySelectorAll('#events-list-container .event-card').length;
window.renderEventCard(doc.getElementById('events-list-container'), ackedEvent, true);
check('sự kiện đã xử lý không hiện trong dòng sự kiện trực tiếp',
    doc.querySelectorAll('#events-list-container .event-card').length === before,
    String(doc.querySelectorAll('#events-list-container .event-card').length));

const pendingEvent = { ...ackedEvent, id: 'evt_cho_xu_ly', acked: false, snapshot_url: '/static/x.jpg' };
window.renderEventCard(doc.getElementById('events-list-container'), pendingEvent, true);
const card = doc.getElementById('evt-evt_cho_xu_ly');
check('sự kiện chờ xử lý vẫn hiện', !!card);
check('ảnh trong thẻ sự kiện bấm được để phóng to',
    !!card.querySelector('img') && card.querySelector('img').getAttribute('onclick').includes('openEvidence'),
    card.querySelector('img') && card.querySelector('img').getAttribute('onclick'));
card.remove();
```

`renderEventCard` chưa được gán ra `window`; thêm `window.renderEventCard = renderEventCard;` ngay dưới định nghĩa hàm để test gọi được.

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "ô camera gắn được zoom và toàn màn hình" và "sự kiện đã xử lý không hiện trong dòng sự kiện trực tiếp".

- [ ] **Step 3: Gắn zoom cho từng ô camera**

Trong `loadCameraWall()` (`static/app.js:530`), ngay sau khi tạo `tile` và gắn `wall.appendChild(tile)`, thêm:

```js
            makeZoomable(tile.querySelector('.camera-tile-video'));
```

Đặt trong nhánh `if (!tile) { … }` để mỗi ô chỉ gắn một lần khi mới dựng.

- [ ] **Step 4: Ẩn sự kiện đã xử lý khỏi dòng sự kiện trực tiếp**

Thay đầu hàm `renderEventCard` (dòng 674):

```js
function renderEventCard(container, event, prepend) {
    if (!container) return;

    // Dòng sự kiện trực tiếp chỉ để việc còn phải làm. Xử lý xong thì biến mất
    // khỏi đây; muốn tra lại thì có nhật ký vi phạm ở màn chi tiết camera.
    if (container.id === 'events-list-container' && event.acked) return;

    const hint = container.querySelector('.empty-hint');
    if (hint) hint.remove();
```

và thêm một dòng **ngay sau dấu `}` đóng hàm** (ngoài thân hàm, cạnh các dòng `window.* = *` khác trong file):

```js
window.renderEventCard = renderEventCard;
```

- [ ] **Step 5: Gỡ thẻ khi xác nhận xử lý**

Trong `ackEvent()` (dòng 706), thay khối cập nhật DOM:

```js
        // Ở dòng sự kiện trực tiếp thì gỡ hẳn; ở danh sách khác thì đổi nút
        document.querySelectorAll(`#evt-${eventId}`).forEach(el => {
            if (el.closest('#events-list-container')) el.remove();
            else {
                const actions = el.querySelector('.event-card-actions');
                if (actions) actions.innerHTML =
                    `<button class="btn-event-processed" disabled>✓ ${updated.acked_by}</button>`;
            }
        });

        const feed = document.getElementById('events-list-container');
        if (feed && !feed.querySelector('.event-card')) {
            feed.innerHTML = '<p class="empty-hint">Không còn sự kiện nào chờ xử lý.</p>';
        }
```

- [ ] **Step 6: Chỉ nạp sự kiện chưa xử lý lúc khởi động**

Trong `startAppSession()` (dòng 2962), đổi lời gọi:

```js
        const recent = await getJson('/api/v1/events?page_size=20&acked=false');
```

- [ ] **Step 7: Cho cột sự kiện cao hết màn hình**

Trong `static/style.css`, thay ba khối:

```css
.main-monitoring-grid {
    display: grid;
    grid-template-columns: 1fr 380px;
    gap: 20px;
    align-items: stretch;
    /* Trừ chiều cao thanh trên cùng và khoảng đệm của khung nội dung, để cột
       sự kiện cao bằng đúng phần màn hình còn lại chứ không cụt ở giữa trang */
    min-height: calc(100vh - 150px);
}

.events-feed-column {
    display: flex;
    flex-direction: column;
    min-height: 0;
}

.events-feed-card {
    padding: 16px;
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
}

.events-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
    flex: 1;
    min-height: 0;
}
```

Bỏ hẳn `min-height: 540px` của `.events-feed-card` và `max-height: 520px` của `.events-list`.

- [ ] **Step 8: Chạy test**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 9: Kiểm bằng mắt**

Vào **Giám sát trực tiếp** (tài khoản `qtht`): bấm ⛶ trên một ô camera → toàn màn hình, lăn chuột phóng to. Bấm ảnh trong thẻ sự kiện → mở hộp phóng to. Bấm **Xác nhận xử lý** → thẻ biến mất. Cột sự kiện kéo dài hết chiều cao màn hình.

- [ ] **Step 10: Commit**

```bash
git add static/app.js static/style.css tests/test_ui.mjs
git commit -m "feat: giám sát trực tiếp phóng to camera, ẩn sự kiện đã xử lý, cột sự kiện cao hết màn

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 17: Cấu hình thời khoá biểu — nút sửa ca và tên camera

Khách: *"Phần camera chưa hiện tên camera giám sát của ca đó."* Task 1 đã làm `camera_id` luôn có giá trị, nhưng ca cũ vẫn chỉ được gán camera mặc định — muốn đổi sang camera khác thì phải sửa được ca, mà bảng lại chỉ có nút xoá (nguyên nhân gốc số 5).

**Files:**
- Modify: `static/app.js` — `renderSchedulesTable()` (dòng 1665-1699); thêm `editSchedule()`
- Test: `tests/test_ui.mjs`

**Interfaces:**
- Consumes: `GET /api/schedules` → mảng ca đã chuẩn hoá (Task 1), mỗi ca có `camera_id`, `training_type`, `check_window_mins`.
- Consumes: `openScheduleModal(schedule)` (đã có, `static/app.js:1575`) — truyền cả bản ghi vào là nó tự điền form, kể cả ô chọn camera.
- Produces: `window.editSchedule(scheduleId)`.

- [ ] **Step 1: Viết test giao diện**

Thêm vào `tests/test_ui.mjs` sau nhóm `[6d]`:

```js
console.log('\n[6e] Cấu hình thời khoá biểu');

window.switchNavTab('schedule');
await sleep(1200);

const schRow = doc.querySelector('#schedules-tbody tr');
check('bảng thời khoá biểu có dòng', !!schRow && !schRow.querySelector('.empty-row'));

if (schRow && !schRow.querySelector('.empty-row')) {
    const camCell = schRow.querySelector('td:nth-child(5)').textContent.trim();
    check('cột camera hiện tên camera chứ không để trống',
        camCell !== '' && camCell !== '—', camCell);
    check('mỗi dòng có nút sửa ca',
        !!schRow.querySelector('td:last-child .icon-btn-edit'));

    schRow.querySelector('td:last-child .icon-btn-edit').click();
    await sleep(700);
    check('bấm sửa thì mở hộp cập nhật ca',
        doc.getElementById('schedule-modal').style.display === 'flex');
    check('tiêu đề hộp là cập nhật chứ không phải thêm mới',
        doc.getElementById('schedule-modal-title').textContent.includes('Cập nhật'),
        doc.getElementById('schedule-modal-title').textContent);
    check('hộp điền sẵn ô chọn camera của ca',
        doc.getElementById('sch-camera-select').value !== undefined);
    window.closeScheduleModal();
}
```

- [ ] **Step 2: Chạy test để thấy nó hỏng**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: FAIL ở "mỗi dòng có nút sửa ca".

- [ ] **Step 3: Thêm nút sửa vào bảng**

Trong `renderSchedulesTable` (`static/app.js:1665`), thay ô thao tác cuối dòng:

```js
            <td>
                <button class="icon-btn icon-btn-edit" title="Sửa ca"
                        onclick="editSchedule('${sch.id}')">✏️</button>
                <button class="icon-btn icon-btn-delete" title="Xoá ca"
                        onclick="deleteSchedule('${sch.id}')">🗑️</button>
            </td>
```

Và nhớ danh sách ca đang hiển thị để `editSchedule` dùng lại — thêm ngay đầu hàm:

```js
let scheduleRows = [];

function renderSchedulesTable(schedules) {
    if (!schedulesTbody) return;
    scheduleRows = schedules;
    schedulesTbody.innerHTML = '';
```

- [ ] **Step 4: Thêm `editSchedule`**

Chèn ngay sau `renderSchedulesTable`:

```js
// Không có nút này thì ca đã tạo không bao giờ gán được camera giám sát, nên
// cột CAMERA của bảng mãi mãi trống.
function editSchedule(scheduleId) {
    const sch = scheduleRows.find(s => s.id === scheduleId);
    if (!sch) return;
    openScheduleModal(sch);
}
window.editSchedule = editSchedule;
```

- [ ] **Step 5: Thêm CSS cho nút sửa**

Thêm vào cuối `static/style.css`, cạnh các nút biểu tượng đã có:

```css
.icon-btn-edit { color: var(--primary-green); }
.icon-btn-edit:hover { background: var(--primary-green-light); }
```

- [ ] **Step 6: Chạy test**

```bash
node tests/test_ui.mjs
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 7: Kiểm bằng mắt**

Vào **Cấu hình thời khoá biểu** (tài khoản `qtht`): mỗi dòng có nút ✏️. Bấm vào, đổi camera, lưu. Bảng phải hiện tên camera mới, và màn **An toàn bắn đạn thật** phải hiện tên bài học của ca đó ở đúng camera.

- [ ] **Step 8: Chạy lại toàn bộ bộ test**

```bash
python tests/test_ai.py
python tests/test_api.py
python tests/test_auth_api.py
python tests/test_config_api.py
python tests/test_zones_stream.py
python tests/test_smoke_routes.py
node tests/test_ui.mjs
```

Kỳ vọng: tất cả PASS.

- [ ] **Step 9: Commit**

```bash
git add static/app.js static/style.css tests/test_ui.mjs
git commit -m "fix: thêm nút sửa ca trong thời khoá biểu để gán được camera giám sát

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Đối chiếu với spec

Bảng này để tự kiểm: mỗi gạch đầu dòng trong PDF phải có một task.

| # | Yêu cầu trong PDF | Task |
|---|---|---|
| 1 | Số lượng camera trực tuyến / tổng, thay chỗ "Tiến độ hoàn thành chung" | 2, 10 |
| 2 | Bỏ pill "Quân số: 7/45" trên thanh trên | 11 |
| 3 | Avatar góc trái dưới cùng, có đăng xuất và đổi thông tin / mật khẩu | 6, 11 |
| 4 | Role người dùng không có "Thêm ca huấn luyện" | 10 |
| 5 | Đào tạo + Chiến đấu = Tất cả | 1 |
| 6 | Bỏ "Tiến độ hoàn thành chung" | 10 |
| 7 | Quân số thực tế = người đang có trong ca đang diễn ra | 2, 10 |
| 8 | Tiến độ thực tế tính theo thời gian; cột Quân số chỉ hiện sĩ số chuẩn | 4, 10 |
| 9 | Thêm cột LOẠI | 10 |
| 10 | Lọc theo khoảng thời gian, trạng thái, ca, thanh tìm kiếm | 4, 10 |
| 11 | Chi tiết ca: thông tin điền vào ô, không chỉ ở tiêu đề | 1, 8 |
| 12 | Thay "Cửa sổ điểm danh" / "Dung sai đi chậm" bằng "Sĩ số đầu buổi" / "Sĩ số cuối buổi" | 8 |
| 13 | Cột tên quân nhân vắng dạng gạch đầu dòng theo đầu / cuối buổi | 8 |
| 14 | Ảnh bằng chứng bấm vào phóng to được | 7 |
| 15 | Thay nút "Giám sát quân số" bằng camera của lịch, phóng to + zoom | 8 |
| 16 | Bỏ tab "Giám sát quân số", gộp vào một trang | 9 |
| 17 | An toàn: phân loại Tất cả / Đào tạo / Chiến đấu | 1, 12 |
| 18 | An toàn: chưa có tên bài học và loại | 1, 12 |
| 19 | Bỏ "Có vi phạm đã xử lý"; cảnh báo đỏ toàn hệ thống giữa đỉnh màn hình; bấm vào sang trang An toàn, camera hiện trạng thái Cảnh báo | 3, 12 |
| 20 | Vi phạm đã xử lý rời danh sách, xuống nhật ký có lỗi / thời gian / video | 13 |
| 21 | Camera an toàn phóng to toàn màn hình và zoom | 7, 13 |
| 22 | An toàn: bộ lọc và tìm kiếm theo camera / bài học / loại / trạng thái | 12 |
| 23 | Nhật ký: lọc theo ca, khoảng thời gian, tìm theo tên bài | 5, 14 |
| 24 | Nhật ký: bỏ tên bài dưới ca, thêm cột tên bài học | 14 |
| 25 | Nhật ký: thêm cột cuối là xem chi tiết | 14 |
| 26 | Chi tiết nhật ký: đủ thông tin, bằng chứng xem toàn màn hình | 5, 15 |
| 27 | Chi tiết nhật ký: vi phạm dạng bảng tên / chức vụ / tên vi phạm | 15 |
| 28 | Giám sát trực tiếp: camera toàn màn hình và zoom | 16 |
| 29 | Giám sát trực tiếp: ảnh vi phạm phóng to; sự kiện đã xử lý biến mất; cột sự kiện cao hết màn | 7, 16 |
| 30 | Cấu hình thời khoá biểu: hiện tên camera của ca | 1, 17 |

## Việc cần kiểm bằng mắt sau khi xong hết

Tự động hoá không bắt được mấy thứ này:

1. **Fullscreen thật.** jsdom không có `requestFullscreen`. Phải mở trình duyệt thật, bấm ⛶ trên ô camera ở cả ba chỗ (giám sát trực tiếp, chi tiết ca, chi tiết camera an toàn).
2. **Zoom bằng lăn chuột và kéo di.** Kiểm trên camera đang phát luồng MJPEG thật, không phải ảnh tĩnh.
3. **Dải cảnh báo đỏ.** Kích một sự kiện INTRUSION thật (đi vào vùng cấm trước camera hoặc gọi `POST /api/v1/events` nếu có), xác nhận dải đỏ nhấp nháy giữa đỉnh màn hình ở **mọi** trang, bấm vào thì sang trang An toàn.
4. **Ca vắt qua nửa đêm.** Tạo một ca 22:00–06:00 và kiểm cột tiến độ theo giờ không âm, không vượt 100%.
5. **Đổi mật khẩu sống sót qua khởi động lại máy chủ.** Đổi mật khẩu, `Ctrl+C`, chạy lại `python main.py`, đăng nhập bằng mật khẩu mới.
