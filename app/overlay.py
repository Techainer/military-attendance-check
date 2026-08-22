"""Vẽ chữ tiếng Việt có dấu lên khung hình.

cv2.putText chỉ dựng được ký tự ASCII nên tên quân nhân ("Nguyễn Văn Hùng") bị
hiện thành dấu hỏi cả trên màn hình giám sát lẫn trong ảnh bằng chứng. Ở đây dùng
Pillow với một font Unicode.

Chỉ dựng ảnh cho riêng ô chữ rồi dán vào khung hình: chuyển cả khung 4K sang
Pillow và ngược lại tốn gần nửa giây mỗi lần, không kịp nhịp xử lý.
"""

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    os.environ.get("OVERLAY_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

PAD_X = 5
PAD_Y = 3
# Chừa lề để nhãn không dán sát mép khung hình
MARGIN = 10

_font_cache = {}


def _load_font(size: int):
    if size in _font_cache:
        return _font_cache[size]

    font = None
    for path in FONT_CANDIDATES:
        if path and Path(path).exists():
            try:
                font = ImageFont.truetype(path, size)
                break
            except Exception:
                continue
    if font is None:
        print("[overlay] Không tìm thấy font Unicode, chữ tiếng Việt sẽ hiển thị xấu")
        font = ImageFont.load_default()

    _font_cache[size] = font
    return font


def draw_label(frame: np.ndarray, text: str, origin, font_size: int = 16,
               text_color=(255, 255, 255), bg_color=(0, 180, 70), anchor: str = "top") -> None:
    """Vẽ một nhãn chữ có nền lên ``frame`` (sửa trực tiếp).

    Args:
        origin: toạ độ (x, y) theo hệ OpenCV. x vượt quá mép phải thì nhãn được
            kéo về canh sát lề phải.
        anchor: "top" thì y là cạnh trên của nhãn, "bottom" thì y là cạnh dưới.
    """
    font = _load_font(font_size)
    left, top, right, bottom = font.getbbox(text)
    box_w = right - left + 2 * PAD_X
    box_h = bottom - top + 2 * PAD_Y

    h_img, w_img = frame.shape[:2]
    if box_w + 2 * MARGIN > w_img or box_h + 2 * MARGIN > h_img:
        return

    x = int(origin[0])
    y = int(origin[1]) - (box_h if anchor == "bottom" else 0)
    x = max(MARGIN, min(x, w_img - box_w - MARGIN))
    y = max(MARGIN, min(y, h_img - box_h - MARGIN))

    tile = Image.new("RGB", (box_w, box_h), bg_color[::-1])
    ImageDraw.Draw(tile).text((PAD_X - left, PAD_Y - top), text, font=font, fill=text_color[::-1])
    frame[y:y + box_h, x:x + box_w] = np.asarray(tile)[:, :, ::-1]
