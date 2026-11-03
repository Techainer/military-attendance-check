"""Dọn dẹp file sự kiện cũ và file mồ côi (không trong events.json)."""

import os
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timedelta
import shutil


def cleanup(
    data_dir: str = "data",
    clip_days: int = 7,
    image_days: int = 30,
    orphan_grace_hours: int = 1,
    min_free_pct: int = 10,
    dry_run: bool = False,
) -> Dict[str, any]:
    """Dọn dẹp file cũ và mồ côi, trả về số/byte xoá theo từng loại.

    Args:
        data_dir: Đường dẫn thư mục dữ liệu (chứa events.json và events/)
        clip_days: Xoá clip .mp4 tham chiếu cũ hơn N ngày
        image_days: Xoá ảnh .jpg tham chiếu cũ hơn N ngày
        orphan_grace_hours: Xoá file mồ côi cũ hơn N giờ (không trong events.json)
        min_free_pct: Nếu % free disk < N, xoá file cũ nhất đến khi >= N%
        dry_run: Chỉ báo cáo, không xoá

    Returns:
        Dict với keys: orphan_files, orphan_bytes, old_clips, old_clips_bytes,
                      old_images, old_images_bytes, min_free_files, min_free_bytes
    """
    data_path = Path(data_dir)
    events_file = data_path / "events.json"
    events_dir = data_path / "events"

    result = {
        "orphan_files": 0, "orphan_bytes": 0,
        "old_clips": 0, "old_clips_bytes": 0,
        "old_images": 0, "old_images_bytes": 0,
        "min_free_files": 0, "min_free_bytes": 0,
    }

    # Đọc danh sách file được tham chiếu trong events.json
    referenced_files = set()
    if events_file.exists():
        try:
            with open(events_file, "r") as f:
                events = json.load(f)
                if isinstance(events, list):
                    for event in events:
                        if event.get("snapshot_url"):
                            # snapshot_url = "/data/events/evt_123.jpg"
                            fname = event["snapshot_url"].split("/")[-1]
                            referenced_files.add(fname)
                        if event.get("clip_url"):
                            # clip_url = "/api/v1/events/evt_123/clip"
                            # Thực tế file lưu là evt_123.mp4 trong events/
                            evt_id = event["clip_url"].split("/")[-2]
                            referenced_files.add(f"{evt_id}.mp4")
        except Exception as e:
            print(f"[Retention] Lỗi đọc events.json: {e}")

    if not events_dir.exists():
        return result

    now = datetime.now()
    orphan_threshold = now - timedelta(hours=orphan_grace_hours)
    clip_threshold = now - timedelta(days=clip_days)
    image_threshold = now - timedelta(days=image_days)

    # Bước 1: Xoá file mồ côi và file tham chiếu cũ
    for fpath in events_dir.iterdir():
        if not fpath.is_file():
            continue

        fname = fpath.name
        fstat = fpath.stat()
        fmtime = datetime.fromtimestamp(fstat.st_mtime)

        should_delete = False
        delete_reason = None

        if fpath.suffix == ".json":
            # Không xoá file JSON
            continue

        # Kiểm tra file mồ côi
        if fname not in referenced_files:
            if fmtime < orphan_threshold:
                should_delete = True
                delete_reason = "orphan"
        else:
            # File được tham chiếu - kiểm tra tuổi
            if fname.endswith(".mp4") and fmtime < clip_threshold:
                should_delete = True
                delete_reason = "old_clip"
            elif fname.endswith(".jpg") and fmtime < image_threshold:
                should_delete = True
                delete_reason = "old_image"

        if should_delete:
            if not dry_run:
                try:
                    fpath.unlink()
                except Exception as e:
                    print(f"[Retention] Lỗi xoá {fname}: {e}")
                    continue

            # Ghi thống kê
            if delete_reason == "orphan":
                result["orphan_files"] += 1
                result["orphan_bytes"] += fstat.st_size
            elif delete_reason == "old_clip":
                result["old_clips"] += 1
                result["old_clips_bytes"] += fstat.st_size
            elif delete_reason == "old_image":
                result["old_images"] += 1
                result["old_images_bytes"] += fstat.st_size

    # Bước 2: Nếu disk vẫn chật, xoá file cũ nhất
    free_pct = _get_free_percent(str(data_path))
    if free_pct < min_free_pct:
        # Sắp xếp file theo mtime (cũ nhất trước)
        files_to_delete = sorted(
            [f for f in events_dir.iterdir() if f.is_file() and f.suffix in [".mp4", ".jpg"]],
            key=lambda f: f.stat().st_mtime
        )

        # Trần số file xoá mỗi lượt: nếu đĩa đầy vì thứ khác (docker, model...)
        # thì xoá hết events cũng không đủ — dừng lại và cảnh báo thay vì dọn sạch
        max_delete = int(os.environ.get("RETENTION_MAX_DELETE_PER_RUN", "2000"))
        deleted = 0
        for fpath in files_to_delete:
            if free_pct >= min_free_pct:
                break
            if deleted >= max_delete:
                print(f"[Retention] Cảnh báo: đã xoá {deleted} file mà đĩa vẫn còn "
                      f"{free_pct}% trống (< {min_free_pct}%) — kiểm tra thứ khác chiếm đĩa")
                break

            # Không xoá file của sự kiện trong 2 ngày gần nhất
            fmtime = datetime.fromtimestamp(fpath.stat().st_mtime)
            if fmtime >= now - timedelta(days=2):
                continue

            if not dry_run:
                fsize = fpath.stat().st_size
                try:
                    fpath.unlink()
                    deleted += 1
                    result["min_free_files"] += 1
                    result["min_free_bytes"] += fsize
                except Exception as e:
                    print(f"[Retention] Lỗi xoá {fpath.name}: {e}")
                    continue

            free_pct = _get_free_percent(str(data_path))

    return result


def _get_free_percent(path: str) -> float:
    """Tính % free disk của phân vùng chứa đường dẫn."""
    try:
        stat = shutil.disk_usage(path)
        return round(100 * stat.free / stat.total, 1)
    except Exception:
        return 100.0
