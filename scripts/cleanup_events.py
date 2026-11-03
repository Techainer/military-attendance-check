#!/usr/bin/env python3
"""CLI wrapper cho dọn dẹp file sự kiện cũ và mồ côi."""

import argparse
import sys
from pathlib import Path

# Thêm thư mục app vào path để import retention
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.retention import cleanup


def format_bytes(b: int) -> str:
    """Format bytes thành đơn vị dễ đọc."""
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def main():
    parser = argparse.ArgumentParser(
        description="Dọn dẹp file sự kiện cũ và mồ côi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Mặc định là dry-run (chỉ báo cáo), dùng --apply để thực hiện xoá."
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Đường dẫn thư mục dữ liệu (mặc định: data)"
    )
    parser.add_argument(
        "--clip-days",
        type=int,
        default=7,
        help="Xoá clip .mp4 cũ hơn N ngày (mặc định: 7)"
    )
    parser.add_argument(
        "--image-days",
        type=int,
        default=30,
        help="Xoá ảnh .jpg cũ hơn N ngày (mặc định: 30)"
    )
    parser.add_argument(
        "--orphan-grace-hours",
        type=int,
        default=1,
        help="Xoá file mồ côi cũ hơn N giờ (mặc định: 1)"
    )
    parser.add_argument(
        "--min-free-pct",
        type=int,
        default=10,
        help="Nếu % free disk < N, xoá file cũ nhất (mặc định: 10)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Thực hiện xoá (mặc định là dry-run)"
    )

    args = parser.parse_args()

    dry_run = not args.apply
    mode_str = "dry-run (báo cáo)" if dry_run else "áp dụng (xoá thực)"

    print(f"[cleanup_events] Chế độ: {mode_str}")
    print(f"  data_dir={args.data_dir}")
    print(f"  clip_days={args.clip_days}")
    print(f"  image_days={args.image_days}")
    print(f"  orphan_grace_hours={args.orphan_grace_hours}")
    print(f"  min_free_pct={args.min_free_pct}")
    print()

    result = cleanup(
        data_dir=args.data_dir,
        clip_days=args.clip_days,
        image_days=args.image_days,
        orphan_grace_hours=args.orphan_grace_hours,
        min_free_pct=args.min_free_pct,
        dry_run=dry_run,
    )

    # In báo cáo
    print("Kết quả dọn dẹp:")
    if result["orphan_files"] > 0:
        print(f"  File mồ côi: {result['orphan_files']} file, {format_bytes(result['orphan_bytes'])}")
    if result["old_clips"] > 0:
        print(f"  Clip cũ: {result['old_clips']} file, {format_bytes(result['old_clips_bytes'])}")
    if result["old_images"] > 0:
        print(f"  Ảnh cũ: {result['old_images']} file, {format_bytes(result['old_images_bytes'])}")
    if result["min_free_files"] > 0:
        print(f"  Xoá để trống disk: {result['min_free_files']} file, {format_bytes(result['min_free_bytes'])}")

    total_files = sum([result["orphan_files"], result["old_clips"], result["old_images"], result["min_free_files"]])
    total_bytes = sum([result["orphan_bytes"], result["old_clips_bytes"], result["old_images_bytes"], result["min_free_bytes"]])

    if total_files == 0:
        print("  (không có gì cần xoá)")
    else:
        print(f"Tổng cộng: {total_files} file, {format_bytes(total_bytes)}")

    if dry_run:
        print()
        print("Đây là dry-run. Dùng --apply để thực hiện xoá.")


if __name__ == "__main__":
    main()
