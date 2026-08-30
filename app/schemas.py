"""Schema kiểm tra dữ liệu vào cho API v1.

Chỉ khai những gì backend thực sự nhận. Ràng buộc ở đây phải khớp với
``docs/api/openapi.yaml``; FastAPI tự trả 422 kèm tên trường sai để giao diện
đánh dấu đúng ô nhập liệu.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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
