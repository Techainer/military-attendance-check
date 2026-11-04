#!/bin/sh
# Docker chỉ ĐÁNH DẤU container unhealthy chứ không tự restart. Cron mỗi phút:
# health lỗi FAIL_LIMIT lần liên tiếp thì restart horus-ai.
#   * * * * * /mnt/ssd/horus-deploy/src/scripts/ops/watchdog.sh >> /mnt/ssd/horus-deploy/backups/watchdog.log 2>&1
set -u
PORT="${HORUS_PORT:-8199}"
CONTAINER="${CONTAINER:-src-horus-ai-1}"
FAIL_LIMIT="${FAIL_LIMIT:-3}"
STATE=/tmp/horus-watchdog.fails
if curl -fsS -m 10 "http://127.0.0.1:$PORT/api/v1/system/health" >/dev/null 2>&1; then
    rm -f "$STATE"; exit 0
fi
n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE"
echo "$(date -Is) health lỗi lần $n"
if [ "$n" -ge "$FAIL_LIMIT" ]; then
    echo "$(date -Is) restart $CONTAINER"
    docker restart "$CONTAINER" >/dev/null && rm -f "$STATE"
fi
