"""Đọc/ghi các file JSON dạng danh sách dùng chung (thời khoá biểu, nhật ký điểm danh)."""

import json
from pathlib import Path
from typing import List


def read_json_list(path: Path) -> List[dict]:
    """Đọc file JSON dạng danh sách. File thiếu hoặc hỏng thì trả về danh sách rỗng."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[storage] Lỗi đọc {path.name}: {e}")
        return []


def write_json_list(path: Path, data: List[dict]) -> None:
    """Ghi danh sách ra file JSON."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[storage] Lỗi ghi {path.name}: {e}")
