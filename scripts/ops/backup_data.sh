#!/bin/sh
# Sao lưu data/ hằng ngày (cấu hình, người dùng, khuôn mặt, biên bản điểm danh,
# events.json). Bỏ ảnh/clip sự kiện trong data/events — lớn, đã có retention.
# Giữ KEEP_DAYS bản gần nhất. Cài cron (root):
#   15 2 * * * /mnt/ssd/horus-deploy/src/scripts/ops/backup_data.sh >> /mnt/ssd/horus-deploy/backups/backup.log 2>&1
set -eu
SRC_DIR="${SRC_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
DEST="${DEST:-$(dirname "$SRC_DIR")/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$DEST"
out="$DEST/horus-data-$(date +%Y%m%d-%H%M).tgz"
tar czf "$out" -C "$SRC_DIR" --exclude='data/events/*.jpg' --exclude='data/events/*.mp4' data
chmod 600 "$out"
find "$DEST" -name 'horus-data-*.tgz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup $out $(du -h "$out" | cut -f1)"
