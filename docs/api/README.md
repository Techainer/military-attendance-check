# Hợp đồng giao tiếp — Service AI Giám sát Huấn luyện

| File | Nội dung |
|---|---|
| `openapi.yaml` | REST API v1 (OpenAPI 3.1) |
| `events.schema.json` | Cấu trúc bản tin sự kiện cho kênh SSE (JSON Schema 2020-12) |

## Đây là service gì

**Không phải hệ thống quản lý.** Nó nhận cấu hình → chạy AI trên luồng hình →
nhả sự kiện ra cho hệ thống quản lý xử lý tiếp.

```
ĐẦU VÀO                    LÕI AI                      ĐẦU RA
camera, nguồn RTSP    →  phát hiện người         →  sự kiện (SSE + REST)
vùng giám sát / ROI   →  nhận diện khuôn mặt     →  vi phạm giờ giấc
thời khoá biểu        →  điểm danh theo ca       →  ảnh bằng chứng
hồ sơ khuôn mặt       →  giám sát an toàn        →  chỉ số tổng hợp
```

Vì vậy ở đây **không có** khu vực, lớp/đội học, duyệt biên bản của chỉ huy, hay
quy trình nghiệp vụ — hệ thống quản lý sở hữu những thứ đó.

Giai đoạn hiện tại cấu hình do chính service giữ (file JSON trong `data/`). Khi
nối vào hệ thống quản lý bên ngoài thì chỉ cần đồng bộ xuống các file đó, phần
AI không phải sửa gì.

## Trạng thái

**Toàn bộ 20 đường dẫn trong hợp đồng đã hiện thực xong.** Có kiểm thử đối chiếu
hai chiều: mọi path khai trong `openapi.yaml` đều có route thật, và không route
v1 nào nằm ngoài hợp đồng.

Ngoại lệ duy nhất còn dùng API cũ: **`/api/faces`** (GET/POST/PUT/DELETE) cho
đăng ký và quản lý hồ sơ khuôn mặt. Chưa có bản v1.

## Xem tài liệu

```bash
npx @redocly/cli preview-docs docs/api/openapi.yaml      # đọc
npx openapi-typescript docs/api/openapi.yaml -o types.ts # sinh client TS
```

## Bốn nguyên tắc quan trọng nhất

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

Màn tổng hợp nhiều lớp cần sự kiện mà không cần hình → chỉ mở SSE.

`overlay=0` trả **khung hình gốc**, không phải bản đã vẽ — đây là dữ liệu thật
cho nút "Bật/Tắt lớp phủ AI". Ảnh nền để vẽ vùng nên lấy
`/cameras/{id}/snapshot?overlay=0`, nếu không sẽ vẽ đè lên chính các vùng cũ.

**2. Mọi tài nguyên mang `camera_id`,** kể cả khi mới chạy một camera. Đừng
hardcode `cam_01` ở giao diện. Giai đoạn POC chỉ một luồng chạy được cùng lúc;
bật camera thứ hai khi đang chạy trả 409.

**3. Trường lạ đi xuyên qua.** `Camera` và `Schedule` chỉ kiểm chặt phần lõi AI
thực sự đọc. Trường giao diện cần mà AI không dùng — `lesson_name`,
`instructor`, `field`, `class_name`… — vẫn được lưu và trả lại nguyên vẹn. Thêm
trường mới **không phải chờ sửa backend**.

**4. Chuỗi hiển thị do máy chủ dựng sẵn.** `message`, `state_label`,
`phase_label`, `display_name` đã là tiếng Việt hoàn chỉnh — hiển thị thẳng,
đừng tự ghép chuỗi từ enum. Enum (`state`, `type`, `violations`…) dùng để lọc và
chọn màu/biểu tượng.

## Màn hình → Endpoint

| Màn | Endpoint chính |
|---|---|
| 1.1 Tổng hợp lịch & tiến độ | `GET /summary/training?training_type=dao_tao` |
| 2.1 Tổng hợp giám sát quân số | `GET /summary/training` (`stats` + `sessions`) |
| 2.2 Chi tiết giám sát + ảnh AI | `GET /sessions/{id}/attendance`, `/sessions/{id}/checks`, `stream.mjpg` |
| 3.1 / 5.1 An toàn bắn đạn thật | `GET /summary/safety`, SSE lọc `type=INTRUSION`, `POST /events/{id}/ack` |
| 4.1 / 4.2 Quân số chiến đấu | Như 2.1 / 2.2, đổi `training_type=chien_dau` |
| 6.1 Cấu hình giám sát tự động | `/schedules`, `/cameras/{id}/zones` |
| 8.1 Quản lý camera | `/cameras` |

Nút **"Tải ảnh điểm danh"** / **"Tải ảnh vi phạm"**: thêm `?download=1` vào
`evidence_url` / `snapshot_url`.

Màn 1.2 (chi tiết bài học, giáo viên, thao trường) và 7.1 (khu vực) lấy dữ liệu
từ hệ thống quản lý; service này chỉ trả lại những trường đã được gửi kèm khi
tạo ca.

## Phân loại vi phạm giờ giấc

`AttendanceRecord.status` là trạng thái tổng hợp; `violations` là **mảng** loại
vi phạm — một quân nhân có thể vừa đi chậm vừa về sớm. Lọc nhanh:
`GET /sessions/{id}/attendance?violation=late`.

Backend suy các trạng thái này từ `first_seen` / `last_seen` của từng người
trong suốt buổi, không phải từ hai mốc điểm danh — dựa vào hai mốc thì người vừa
đi chậm vừa về sớm bị xếp nhầm thành "không tham gia".

## Giới hạn cần biết

- **Dấu vết hiện diện nằm trong RAM.** Máy chủ khởi động lại giữa buổi thì buổi
  đó không có bảng vi phạm, và hệ thống **không kết luận đi chậm** cho ai — vì
  không thể biết ai đã có mặt từ trước. Im lặng còn hơn vu oan.
- **Một luồng camera cùng lúc.** Payload đã sẵn sàng cho đa camera, phần xử lý
  thì chưa.
- **Vạch an toàn chuyển từ cấu hình cũ luôn ở trạng thái tắt.** Trước đây nó chỉ
  là hình vẽ; bật sẵn là mọi hệ thống đang chạy bỗng dưng réo còi. Muốn dùng thì
  `PATCH /zones/{id} {"enabled": true}`.
