"""Telestrator drawing images: validate the PNG and place it over the video."""

import struct

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_SIDE = 8192


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the IHDR chunk; raises ValueError for anything else."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise ValueError("not a png image")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= MAX_PNG_SIDE and 0 < height <= MAX_PNG_SIDE):
        raise ValueError("png dimensions out of range")
    return width, height


def video_rect_in_canvas(canvas_w: int, canvas_h: int, video_w: int, video_h: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the video inside the canvas when shown with object-fit: contain."""
    if min(canvas_w, canvas_h, video_w, video_h) <= 0:
        raise ValueError("dimensions must be positive")
    scale = min(canvas_w / video_w, canvas_h / video_h)
    w = min(canvas_w, max(1, round(video_w * scale)))
    h = min(canvas_h, max(1, round(video_h * scale)))
    return (canvas_w - w) // 2, (canvas_h - h) // 2, w, h
