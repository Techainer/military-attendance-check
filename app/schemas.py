"""Schema kiểm tra dữ liệu vào cho API v1.

Chỉ khai những gì backend thực sự nhận. Ràng buộc ở đây phải khớp với
``docs/api/openapi.yaml``; FastAPI tự trả 422 kèm tên trường sai để giao diện
đánh dấu đúng ô nhập liệu.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.safety import RULE_ATTENDANCE, RULE_CROSSING, RULE_RESTRICTED

KIND_POLYGON = "polygon"
KIND_TRIPWIRE = "tripwire"

# Luật nào đi với hình dạng nào. Vạch không thể là vùng đếm quân số, và vùng kín
# không thể dùng luật "cắt qua vạch".
RULES_BY_KIND = {
    KIND_POLYGON: {RULE_ATTENDANCE, RULE_RESTRICTED},
    KIND_TRIPWIRE: {RULE_CROSSING},
}


class Point(BaseModel):
    """Toạ độ chuẩn hoá theo khung hình, để không phụ thuộc độ phân giải camera."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class ZoneInput(BaseModel):
    """Vùng giám sát trên một camera."""

    name: str = Field(min_length=1, max_length=120)
    kind: str
    rule: str
    points: List[Point]
    detect_human: bool = True
    detect_object: bool = False
    enabled: bool = True

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v not in RULES_BY_KIND:
            raise ValueError(f"kind phải là một trong {sorted(RULES_BY_KIND)}")
        return v

    @field_validator("rule")
    @classmethod
    def _check_rule(cls, v: str) -> str:
        valid = {RULE_ATTENDANCE, RULE_RESTRICTED, RULE_CROSSING}
        if v not in valid:
            raise ValueError(f"rule phải là một trong {sorted(valid)}")
        return v

    @model_validator(mode="after")
    def _check_shape(self):
        allowed = RULES_BY_KIND.get(self.kind, set())
        if self.rule not in allowed:
            raise ValueError(
                f"kind '{self.kind}' chỉ dùng được với rule {sorted(allowed)}, "
                f"không phải '{self.rule}'"
            )

        if self.kind == KIND_POLYGON and len(self.points) < 3:
            raise ValueError("vùng kín cần ít nhất 3 điểm")
        # Phần phát hiện cắt vạch chỉ dùng hai điểm đầu; nhận nhiều hơn sẽ tạo ảo
        # tưởng là có đường gấp khúc.
        if self.kind == KIND_TRIPWIRE and len(self.points) != 2:
            raise ValueError("vạch an toàn phải có đúng 2 điểm")

        if len({(p.x, p.y) for p in self.points}) < len(self.points):
            raise ValueError("các điểm không được trùng nhau")

        if not self.detect_human and not self.detect_object:
            raise ValueError("phải bật phát hiện người hoặc vật, nếu không vùng vô nghĩa")

        return self

    def to_record(self, zone_id: str, camera_id: str) -> dict:
        record = self.model_dump()
        record["points"] = [{"x": p["x"], "y": p["y"]} for p in record["points"]]
        record["id"] = zone_id
        record["camera_id"] = camera_id
        return record


class ZonePatch(BaseModel):
    """Cập nhật một phần thông tin vùng. Trường bỏ trống thì giữ nguyên."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    kind: Optional[str] = None
    rule: Optional[str] = None
    points: Optional[List[Point]] = None
    detect_human: Optional[bool] = None
    detect_object: Optional[bool] = None
    enabled: Optional[bool] = None

    def apply_to(self, existing: dict) -> ZoneInput:
        """Trộn vào bản ghi hiện có rồi kiểm tra lại toàn bộ.

        Kiểm lại cả bản ghi chứ không chỉ phần sửa: đổi mỗi ``kind`` có thể làm
        số điểm hoặc luật trở nên không hợp lệ.
        """
        merged = {
            "name": existing.get("name", ""),
            "kind": existing.get("kind", KIND_POLYGON),
            "rule": existing.get("rule", RULE_ATTENDANCE),
            "points": existing.get("points", []),
            "detect_human": existing.get("detect_human", True),
            "detect_object": existing.get("detect_object", False),
            "enabled": existing.get("enabled", True),
        }
        merged.update(self.model_dump(exclude_unset=True, exclude_none=True))
        return ZoneInput.model_validate(merged)


class AckInput(BaseModel):
    """Xác nhận đã xử lý một sự kiện."""

    acked_by: str = Field(min_length=1, max_length=120)
    note: Optional[str] = Field(default=None, max_length=1000)


# ---------------------------------------------------------------- camera & ca

# Lõi AI chỉ đọc vài trường (giờ giấc, cửa sổ điểm danh, đơn vị, sĩ số). Những
# trường còn lại — giáo viên, thao trường, tên bài học, loại huấn luyện — giao
# diện cần hiển thị nên vẫn phải lưu và trả lại nguyên vẹn. Vì vậy phần AI phụ
# thuộc thì kiểm chặt, phần còn lại cho đi xuyên qua: đội giao diện thêm trường
# mới không phải chờ sửa backend.

SOURCE_TYPES = {"rtsp", "file", "webcam"}
TRAINING_TYPES = {"dao_tao", "chien_dau"}
HHMM = r"^([01]\d|2[0-3]):([0-5]\d)$"


class CameraInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=120)
    source_type: str
    source_uri: str = Field(min_length=1, max_length=2000)
    code: Optional[str] = Field(default=None, max_length=60)
    area_name: Optional[str] = Field(default=None, max_length=120)
    enabled: bool = True
    target_fps: int = Field(default=5, ge=1, le=25)

    @field_validator("source_type")
    @classmethod
    def _check_source(cls, v: str) -> str:
        if v not in SOURCE_TYPES:
            raise ValueError(f"source_type phải là một trong {sorted(SOURCE_TYPES)}")
        return v

    @model_validator(mode="after")
    def _check_uri(self):
        if self.source_type == "rtsp" and "://" not in self.source_uri:
            raise ValueError("nguồn rtsp phải là URL đầy đủ, ví dụ rtsp://10.0.0.21:554/stream1")
        return self


class CameraPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    source_type: Optional[str] = None
    source_uri: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    code: Optional[str] = Field(default=None, max_length=60)
    area_name: Optional[str] = Field(default=None, max_length=120)
    enabled: Optional[bool] = None
    target_fps: Optional[int] = Field(default=None, ge=1, le=25)

    def apply_to(self, existing: dict) -> dict:
        merged = {**existing, **self.model_dump(exclude_unset=True, exclude_none=True)}
        merged.pop("id", None)
        merged.pop("status", None)
        return CameraInput.model_validate(merged).model_dump()


class ScheduleInput(BaseModel):
    """Ca theo thời khoá biểu.

    Trường ngoài danh sách này vẫn được lưu và trả lại — giao diện dùng để hiển
    thị tên bài học, giáo viên, thao trường mà lõi AI không cần biết.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=200)
    start_time: str = Field(pattern=HHMM, examples=["07:00"])
    end_time: str = Field(pattern=HHMM, examples=["11:30"])
    unit: Optional[str] = Field(default=None, max_length=120)
    shift: Optional[str] = Field(default=None, max_length=60)
    training_type: Optional[str] = None
    check_window_mins: int = Field(default=5, ge=1, le=60)
    late_tolerance_mins: int = Field(default=5, ge=0, le=240)
    early_leave_tolerance_mins: int = Field(default=5, ge=0, le=240)
    required_count: Optional[int] = Field(default=None, ge=0, le=100000)
    camera_id: Optional[str] = Field(default=None, max_length=60)
    enabled: bool = True

    @field_validator("training_type")
    @classmethod
    def _check_training_type(cls, v):
        if v is not None and v not in TRAINING_TYPES:
            raise ValueError(f"training_type phải là một trong {sorted(TRAINING_TYPES)}")
        return v

    @model_validator(mode="after")
    def _check_window(self):
        if self.start_time == self.end_time:
            raise ValueError("giờ bắt đầu và giờ kết thúc không được trùng nhau")

        # Ca qua đêm được phép (22:00 -> 06:00), nhưng ca trong ngày phải đủ dài
        # cho hai cửa sổ điểm danh, nếu không mốc cuối giờ sẽ đè lên mốc đầu giờ.
        start_h, start_m = (int(x) for x in self.start_time.split(":"))
        end_h, end_m = (int(x) for x in self.end_time.split(":"))
        length = (end_h * 60 + end_m) - (start_h * 60 + start_m)
        if length > 0 and length < self.check_window_mins * 2:
            raise ValueError(
                f"ca dài {length} phút không đủ cho hai cửa sổ điểm danh "
                f"{self.check_window_mins} phút; rút ngắn check_window_mins hoặc kéo dài ca"
            )
        return self


class SchedulePatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    start_time: Optional[str] = Field(default=None, pattern=HHMM)
    end_time: Optional[str] = Field(default=None, pattern=HHMM)
    unit: Optional[str] = Field(default=None, max_length=120)
    shift: Optional[str] = Field(default=None, max_length=60)
    training_type: Optional[str] = None
    check_window_mins: Optional[int] = Field(default=None, ge=1, le=60)
    late_tolerance_mins: Optional[int] = Field(default=None, ge=0, le=240)
    early_leave_tolerance_mins: Optional[int] = Field(default=None, ge=0, le=240)
    required_count: Optional[int] = Field(default=None, ge=0, le=100000)
    camera_id: Optional[str] = Field(default=None, max_length=60)
    enabled: Optional[bool] = None

    def apply_to(self, existing: dict) -> dict:
        merged = {**existing, **self.model_dump(exclude_unset=True, exclude_none=True)}
        merged.pop("id", None)
        return ScheduleInput.model_validate(merged).model_dump()
