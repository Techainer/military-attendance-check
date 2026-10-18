"""Thu dữ liệu AI metadata (ONVIF Profile M) từ camera để thiết kế phần tích hợp.

Chỉ dùng thư viện chuẩn Python 3.8+. Chạy trên máy vào được camera:

    python3 probe_ai_camera.py "rtsp://admin:MATKHAU@192.168.1.113:554/MediaInput/stream_2" --seconds 90

Trong lúc chạy, cho 1–3 người đi lại trước camera (có lúc đứng yên, có lúc ra
khỏi khung, có lúc không còn ai). Kết quả nằm trong thư mục probe_<giờ>/ và
file .zip cùng tên — gửi lại file .zip. Không file nào chứa mật khẩu.

Nếu máy có ffmpeg, script chụp thêm ảnh luồng video mỗi 3 giây để đối chiếu
toạ độ hộp với hình thật.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urlunsplit


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def strip_credentials(url):
    p = urlsplit(url)
    host = p.hostname + (f":{p.port}" if p.port else "")
    return urlunsplit((p.scheme, host, p.path, p.query, p.fragment))


class RtspClient:
    """RTSP tối thiểu qua TCP, dữ liệu RTP xen kẽ (interleaved) trên cùng kết nối."""

    def __init__(self, url, timeout=10):
        p = urlsplit(url)
        self.user = unquote(p.username or "")
        self.pwd = unquote(p.password or "")
        self.host, self.port = p.hostname, p.port or 554
        self.url = strip_credentials(url)
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.buf = b""
        self.cseq = 0
        self.session = None
        self.auth = None  # (kind, realm, nonce, qop)

    def _auth_header(self, method, uri):
        if not self.auth:
            return None
        kind, realm, nonce, qop = self.auth
        if kind == "basic":
            import base64
            return "Basic " + base64.b64encode(f"{self.user}:{self.pwd}".encode()).decode()
        ha1 = hashlib.md5(f"{self.user}:{realm}:{self.pwd}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        if qop:
            nc, cnonce = "00000001", "0a4f113b"
            resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()
            return (f'Digest username="{self.user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
                    f'response="{resp}", qop=auth, nc={nc}, cnonce="{cnonce}"')
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        return f'Digest username="{self.user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"'

    def _recv_more(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("camera đóng kết nối")
        self.buf += chunk

    def _read_response(self, on_rtp=None):
        """Đọc một phản hồi RTSP; gói RTP xen giữa (nếu có) chuyển cho on_rtp."""
        while True:
            while self.buf.startswith(b"$"):
                if not self._take_interleaved(on_rtp):
                    break
            if b"\r\n\r\n" in self.buf and not self.buf.startswith(b"$"):
                head, rest = self.buf.split(b"\r\n\r\n", 1)
                lines = head.decode(errors="replace").split("\r\n")
                status = int(lines[0].split()[1])
                headers = {}
                for line in lines[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers.setdefault(k.strip().lower(), v.strip())
                n = int(headers.get("content-length", 0))
                while len(rest) < n:
                    self._recv_more()
                    rest = self.buf.split(b"\r\n\r\n", 1)[1]
                body = rest[:n].decode(errors="replace")
                self.buf = rest[n:]
                return status, headers, body
            self._recv_more()

    def _take_interleaved(self, on_rtp):
        if len(self.buf) < 4:
            return False
        channel, length = self.buf[1], struct.unpack(">H", self.buf[2:4])[0]
        if len(self.buf) < 4 + length:
            return False
        packet = self.buf[4:4 + length]
        self.buf = self.buf[4 + length:]
        if on_rtp:
            on_rtp(channel, packet)
        return True

    def request(self, method, uri=None, headers=None, on_rtp=None):
        uri = uri or self.url
        for _attempt in range(2):
            self.cseq += 1
            h = {"CSeq": str(self.cseq), "User-Agent": "horus-probe/1.0"}
            if self.session:
                h["Session"] = self.session
            auth = self._auth_header(method, uri)
            if auth:
                h["Authorization"] = auth
            h.update(headers or {})
            msg = f"{method} {uri} RTSP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
            self.sock.sendall(msg.encode())
            status, rh, body = self._read_response(on_rtp)
            if status == 401 and not self.auth:
                wa = rh.get("www-authenticate", "")
                if wa.lower().startswith("digest"):
                    realm = re.search(r'realm="([^"]*)"', wa).group(1)
                    nonce = re.search(r'nonce="([^"]*)"', wa).group(1)
                    qop = re.search(r'qop="([^"]*)"', wa)
                    self.auth = ("digest", realm, nonce, qop.group(1) if qop else None)
                else:
                    self.auth = ("basic", None, None, None)
                continue
            if "session" in rh:
                self.session = rh["session"].split(";")[0]
            return status, rh, body
        return status, rh, body

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def parse_sdp(sdp):
    """Tách các media trong SDP: kiểu, payload, rtpmap, control."""
    medias, cur = [], None
    for line in sdp.splitlines():
        if line.startswith("m="):
            parts = line[2:].split()
            cur = {"media": parts[0], "payloads": parts[3:], "rtpmap": [], "control": None}
            medias.append(cur)
        elif cur is not None and line.startswith("a=rtpmap:"):
            cur["rtpmap"].append(line[9:])
        elif cur is not None and line.startswith("a=control:"):
            cur["control"] = line[10:]
    return medias


def control_url(base, control):
    if control.startswith("rtsp://"):
        return control
    if "?" in base:
        path, query = base.split("?", 1)
        return f"{path.rstrip('/')}/{control}?{query}"
    return f"{base.rstrip('/')}/{control}"


def describe(url):
    c = RtspClient(url)
    try:
        status, _h, sdp = c.request("DESCRIBE", headers={"Accept": "application/sdp"})
        return status, sdp
    finally:
        c.close()


def snapshot_loop(url, out_dir, stop, every=3.0):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log("Không có ffmpeg: bỏ qua chụp ảnh (vẫn thu metadata bình thường).")
        return
    os.makedirs(out_dir, exist_ok=True)
    while not stop.is_set():
        t0 = time.time()
        name = os.path.join(out_dir, datetime.fromtimestamp(t0).strftime("%H%M%S_%f")[:-3] + ".jpg")
        subprocess.run([ffmpeg, "-loglevel", "error", "-rtsp_transport", "tcp", "-y", "-i", url,
                        "-frames:v", "1", "-q:v", "3", name], timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t1 = time.time()
        if os.path.exists(name):
            # tên file = lúc bắt đầu gọi; ghi thêm lúc xong để biết khoảng sai số
            with open(os.path.join(out_dir, "snapshots.jsonl"), "a") as f:
                f.write(json.dumps({"file": os.path.basename(name), "t_start": t0, "t_end": t1}) + "\n")
        stop.wait(max(0.0, every - (t1 - t0)))


def capture_metadata(url, seconds, out_dir):
    c = RtspClient(url)
    status, _h, sdp = c.request("DESCRIBE", headers={"Accept": "application/sdp"})
    if status != 200:
        raise RuntimeError(f"DESCRIBE {strip_credentials(url)} trả {status}")
    meta = [m for m in parse_sdp(sdp) if m["media"] == "application"]
    if not meta:
        raise RuntimeError("SDP không có track application (metadata). Xem sdp_*.txt.")
    ctrl = meta[0]["control"] or ""
    # Mỗi hãng ghép URL track một kiểu; thử lần lượt
    candidates = [control_url(c.url, ctrl), f"{c.url.rstrip('/')}/{ctrl}"]
    if ctrl.startswith("rtsp://"):
        candidates = [ctrl]
    for track in dict.fromkeys(candidates):
        log(f"SETUP track metadata: {track}")
        status, rh, _ = c.request("SETUP", uri=track,
                                  headers={"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
        if status == 200:
            break
        log(f"  -> {status}")
    if status != 200:
        raise RuntimeError(f"SETUP trả {status}")
    timeout = 60
    m = re.search(r"timeout=(\d+)", rh.get("session", ""))
    if m:
        timeout = int(m.group(1))

    docs = open(os.path.join(out_dir, "metadata.jsonl"), "w", encoding="utf-8")
    rtcp = open(os.path.join(out_dir, "rtcp.jsonl"), "w")
    state = {"parts": [], "first_ts": None, "n_docs": 0, "n_pkts": 0}

    def on_rtp(channel, pkt):
        now = time.time()
        if channel == 1:
            # RTCP: lưu Sender Report để quy RTP timestamp ra giờ thật
            if len(pkt) >= 20 and pkt[1] == 200:
                ntp_sec, ntp_frac, rtp_ts = struct.unpack(">IIl", pkt[8:20])
                rtcp.write(json.dumps({"t_recv": now, "ntp_sec": ntp_sec, "ntp_frac": ntp_frac,
                                       "rtp_ts": rtp_ts & 0xFFFFFFFF}) + "\n")
            return
        if len(pkt) < 12:
            return
        state["n_pkts"] += 1
        b0, b1 = pkt[0], pkt[1]
        marker = bool(b1 & 0x80)
        seq, ts = struct.unpack(">HI", pkt[2:8])
        cc = b0 & 0x0F
        off = 12 + 4 * cc
        if b0 & 0x10 and len(pkt) >= off + 4:  # extension header
            off += 4 + 4 * struct.unpack(">H", pkt[off + 2:off + 4])[0]
        payload = pkt[off:]
        if b0 & 0x20 and payload:  # padding
            payload = payload[:-payload[-1]]
        state["parts"].append((seq, payload))
        if marker:
            xml = b"".join(p for _s, p in state["parts"]).decode("utf-8", errors="replace")
            docs.write(json.dumps({"t_recv": now, "rtp_ts": ts, "seqs": [s for s, _p in state["parts"]],
                                   "xml": xml}, ensure_ascii=False) + "\n")
            state["parts"] = []
            state["n_docs"] += 1

    status, _h, _ = c.request("PLAY", headers={"Range": "npt=0.000-"}, on_rtp=on_rtp)
    if status != 200:
        raise RuntimeError(f"PLAY trả {status}")
    log(f"Đang thu metadata {seconds} giây...")
    c.sock.settimeout(1.0)
    end = time.time() + seconds
    last_keepalive = time.time()
    last_report = time.time()
    while time.time() < end:
        try:
            c._recv_more()
        except socket.timeout:
            pass
        while c.buf.startswith(b"$") and c._take_interleaved(on_rtp):
            pass
        if c.buf and not c.buf.startswith(b"$") and b"\r\n\r\n" in c.buf:
            c._read_response(on_rtp)  # phản hồi GET_PARAMETER
        if time.time() - last_keepalive > timeout / 2:
            c.cseq += 1
            c.sock.sendall((f"GET_PARAMETER {c.url} RTSP/1.0\r\nCSeq: {c.cseq}\r\nSession: {c.session}\r\n"
                            + (f"Authorization: {c._auth_header('GET_PARAMETER', c.url)}\r\n" if c.auth else "")
                            + "\r\n").encode())
            last_keepalive = time.time()
        if time.time() - last_report > 10:
            log(f"  {state['n_docs']} gói XML, {state['n_pkts']} gói RTP")
            last_report = time.time()
    try:
        c.request("TEARDOWN")
    except Exception:
        pass
    c.close()
    docs.close()
    rtcp.close()
    return state["n_docs"]


def summarize(out_dir):
    ns_obj = re.compile(r"<(?:\w+:)?Object\b[^>]*ObjectId=\"([^\"]+)\"")
    ns_type = re.compile(r"<(?:\w+:)?Type\b[^>]*>([^<]+)<")
    ns_box = re.compile(r"<(?:\w+:)?BoundingBox\b([^>]*)/?>")
    n, empty, ids, types, frames_utc = 0, 0, set(), {}, 0
    times, boxes_sample = [], []
    with open(os.path.join(out_dir, "metadata.jsonl"), encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            n += 1
            times.append(d["t_recv"])
            xml = d["xml"]
            objs = ns_obj.findall(xml)
            if not objs:
                empty += 1
            ids.update(objs)
            for t in ns_type.findall(xml):
                types[t.strip()] = types.get(t.strip(), 0) + 1
            if "UtcTime" in xml:
                frames_utc += 1
            if len(boxes_sample) < 5:
                boxes_sample += ns_box.findall(xml)[:2]
    gaps = [b - a for a, b in zip(times, times[1:])]
    s = {
        "xml_docs": n,
        "docs_without_objects": empty,
        "docs_with_UtcTime": frames_utc,
        "distinct_object_ids": len(ids),
        "object_types": types,
        "avg_interval_s": round(sum(gaps) / len(gaps), 3) if gaps else None,
        "max_gap_s": round(max(gaps), 3) if gaps else None,
        "bbox_attr_samples": boxes_sample[:5],
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="RTSP luồng video, vd rtsp://admin:pw@ip:554/MediaInput/stream_2")
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--query", default="analytics=1",
                    help="tham số bật metadata thêm vào URL (mặc định analytics=1)")
    ap.add_argument("--no-snapshots", action="store_true")
    a = ap.parse_args()

    out = f"probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(out)
    sep = "&" if "?" in a.url else "?"
    meta_url = f"{a.url}{sep}{a.query}"
    info = {"video_url": strip_credentials(a.url), "metadata_url": strip_credentials(meta_url),
            "started_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0]}

    for name, url in (("plain", a.url), ("analytics", meta_url)):
        try:
            status, sdp = describe(url)
            with open(os.path.join(out, f"sdp_{name}.txt"), "w") as f:
                f.write(f"# DESCRIBE {strip_credentials(url)} -> {status}\n{sdp}")
            info[f"describe_{name}"] = status
            log(f"DESCRIBE {name}: {status}, media = {[m['media'] for m in parse_sdp(sdp)]}")
        except Exception as e:
            info[f"describe_{name}"] = repr(e)
            log(f"DESCRIBE {name} lỗi: {e}")

    stop = threading.Event()
    snap = None
    if not a.no_snapshots:
        snap = threading.Thread(target=snapshot_loop, args=(a.url, os.path.join(out, "snapshots"), stop))
        snap.start()
    try:
        info["xml_docs"] = capture_metadata(meta_url, a.seconds, out)
        info["summary"] = summarize(out)
        log(f"Tóm tắt: {json.dumps(info['summary'], ensure_ascii=False)}")
    except Exception as e:
        info["capture_error"] = repr(e)
        log(f"Lỗi khi thu metadata: {e}")
    finally:
        stop.set()
        if snap:
            snap.join(timeout=30)
        info["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with open(os.path.join(out, "info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        with zipfile.ZipFile(out + ".zip", "w", zipfile.ZIP_DEFLATED) as z:
            for root, _d, files in os.walk(out):
                for fn in files:
                    z.write(os.path.join(root, fn))
        log(f"Xong. Gửi lại file {out}.zip")


if __name__ == "__main__":
    main()
