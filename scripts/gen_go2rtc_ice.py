"""Sinh data/go2rtc/ice.yaml cho go2rtc từ TURN_URLS / TURN_SECRET trong .env.

go2rtc cũng phải dùng TURN: máy chạy go2rtc nằm sau NAT không mở cổng, nên
trình duyệt ở ngoài không gửi thẳng tới nó được — hai bên gặp nhau qua relay.
Mật khẩu ký từ TURN_SECRET (coturn use-auth-secret) với hạn dài (mặc định
365 ngày) vì go2rtc chỉ đọc cấu hình lúc khởi động; hết hạn thì chạy lại
script này rồi `docker compose restart go2rtc`.

    uv run python scripts/gen_go2rtc_ice.py [--days 365] [--out data/go2rtc/ice.yaml]
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--out", default=str(ROOT / "data" / "go2rtc" / "ice.yaml"))
    args = ap.parse_args()

    _load_env(ROOT / ".env")
    from app import go2rtc  # đọc TURN_* sau khi đã nạp .env

    servers = [{"urls": ["stun:stun.cloudflare.com:3478"]}]
    turn = go2rtc.turn_credentials(user="go2rtc", ttl_s=args.days * 86400)
    if turn:
        servers.append(turn)
    else:
        print("Chưa set TURN_URLS / TURN_SECRET — chỉ ghi STUN.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # JSON là YAML hợp lệ, khỏi cần thư viện yaml
    out.write_text(json.dumps({"webrtc": {"ice_servers": servers}}, indent=2) + "\n")
    os.chmod(out, 0o600)
    print(f"Đã ghi {out} ({len(servers)} ICE server)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
