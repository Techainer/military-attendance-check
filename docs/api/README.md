# Hợp đồng giao tiếp — Hệ thống Giám sát Huấn luyện AI

| File | Nội dung |
|---|---|
| `openapi.yaml` | Toàn bộ REST API v1 (OpenAPI 3.1) |
| `events.schema.json` | Cấu trúc bản tin sự kiện cho kênh SSE (JSON Schema 2020-12) |

Trạng thái: hợp đồng đầy đủ cho cả hệ thống. Backend **đã hiện thực phần lõi AI**;
phần còn lại (CRUD khu vực / camera / lớp, MJPEG đa camera) vẫn là hợp đồng để
đội giao diện làm song song.

| Nhóm endpoint | Backend |
|---|---|
| `/events`, `/events/stream`, `/events/{id}/ack`, `/events/{id}/clip` | ✅ chạy được |
| `/sessions/{id}/attendance` | ✅ chạy được |
| `/summary/safety`, `/summary/training` | ✅ chạy được |
| `/cameras/{id}/zones`, `/zones/{id}` | ✅ chạy được |
| `/cameras/{id}/stream.mjpg`, `/cameras/{id}/snapshot` | ✅ chạy được |
| `/areas`, `/cameras` (CRUD), `/classes`, `/schedules` v1 | ⏳ chưa hiện thực |

Giai đoạn POC chạy một camera, `camera_id` cố định là `cam_01`; gọi id khác trả 404.

## Xem tài liệu

```bash
# Giao diện đọc được
npx @redocly/cli preview-docs docs/api/openapi.yaml

# Sinh client TypeScript
npx openapi-typescript docs/api/openapi.yaml -o src/api/types.ts
```

## Ba nguyên tắc quan trọng nhất

**1. Hình và dữ liệu đi hai đường khác nhau.**

```html
<!-- video: không cần WebSocket, không cần canvas -->
<img src="/api/v1/cameras/cam_01/stream.mjpg?overlay=1">
```

```js
// dữ liệu: SSE, không kèm khung hình
const es = new EventSource('/api/v1/events/stream');
es.onmessage = (e) => handleEvent(JSON.parse(e.data));
```

Màn tổng hợp nhiều lớp cần sự kiện mà không cần hình → chỉ mở SSE, không mở
`stream.mjpg`. Đừng nhồi hai thứ vào một kênh.

`overlay=0` trả **khung hình gốc**, không phải bản đã vẽ — đây là dữ liệu thật
cho nút "Bật/Tắt lớp phủ AI". Ảnh nền để vẽ vùng nên lấy
`/cameras/{id}/snapshot?overlay=0`, nếu không sẽ vẽ đè lên chính các vùng cũ.

**2. Mọi tài nguyên đều mang `camera_id`,** kể cả giai đoạn hệ thống mới chạy
một camera. Đừng hardcode `CAM-01` ở giao diện.

**3. Chuỗi hiển thị do máy chủ dựng sẵn.** Các trường `message`, `state_label`,
`phase_label`, `display_name` đã là tiếng Việt hoàn chỉnh — hiển thị thẳng,
không tự ghép chuỗi từ enum. Enum (`state`, `type`, `violations`…) dùng để lọc
và chọn màu/biểu tượng.

## Màn hình → Endpoint

| Màn | Endpoint chính |
|---|---|
| 1.1 Tổng hợp lịch & tiến độ | `GET /summary/training?date=&training_type=dao_tao` |
| 1.2 Chi tiết lịch huấn luyện | `GET /sessions/{id}` |
| 2.1 Tổng hợp giám sát quân số | `GET /summary/training` (`stats` + `sessions`) |
| 2.2 Chi tiết giám sát + ảnh AI | `GET /sessions/{id}/attendance`, `GET /sessions/{id}/checks`, `stream.mjpg` |
| 3.1 / 5.1 An toàn bắn đạn thật | `GET /summary/safety`, SSE lọc `type=INTRUSION`, `POST /events/{id}/ack` |
| 4.1 / 4.2 Quân số chiến đấu | Như 2.1 / 2.2, đổi `training_type=chien_dau` |
| 6.1 Cấu hình giám sát tự động | `GET/POST/PATCH /schedules`, `GET/POST/PATCH /cameras/{id}/zones` |
| 7.1 Quản lý khu vực | `/areas`, `GET /areas/{id}` trả kèm camera trực thuộc |
| 8.1 Quản lý camera | `/cameras` |

Nút **"Tải ảnh điểm danh"** và **"Tải ảnh vi phạm"**: thêm `?download=1` vào
`evidence_url` / `snapshot_url`.

## Phân loại vi phạm giờ giấc

`AttendanceRecord.status` là trạng thái tổng hợp; `violations` là mảng loại vi
phạm. Một quân nhân **có thể vừa đi chậm vừa về sớm** — vì vậy `violations` là
mảng chứ không phải một enum duy nhất. Thanh lọc nhanh ở màn 2.2 / 4.2 dùng
`GET /sessions/{id}/attendance?violation=late`.

Backend suy ra các trạng thái này từ `first_seen` / `last_seen` của từng quân
nhân trong suốt buổi, không phải từ hai mốc điểm danh — nếu chỉ dựa vào hai mốc
thì người vừa đi chậm vừa về sớm sẽ bị xếp nhầm thành "không tham gia".

## Hai điểm còn chờ chốt nghiệp vụ

1. **Lớp / đội học.** Hợp đồng hiện tách `TrainingClass` khỏi `unit`: một lớp
   ghép được nhiều đơn vị, roster điểm danh khoá theo `class_id`. Nếu thực tế
   lớp luôn trùng khít đơn vị thì có thể bỏ thực thể này.

2. **Công thức tiến độ.** Đang dùng
   `progress_pct = actual_minutes / scheduled_minutes × 100` cho từng buổi, với
   `actual_minutes` là khoảng từ lúc thấy quân nhân đầu tiên tới lúc thấy người
   cuối cùng trên thao trường. Tiến độ toàn chương trình
   (`overall_progress_pct`) là trung bình theo buổi.

Cả hai đều nằm trong response, đổi sau sẽ phá hợp đồng — cần chốt trước khi
đội giao diện code màn 1.1 và 1.2.
