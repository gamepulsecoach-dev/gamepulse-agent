import struct
import zlib

import pytest

from gp_agent.overlay import MAX_PNG_SIDE, png_size, video_rect_in_canvas


def make_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


class TestPngSize:
    def test_reads_dimensions(self):
        assert png_size(make_png(1280, 720)) == (1280, 720)

    @pytest.mark.parametrize("data", [b"", b"GIF89a" + b"\x00" * 30, b"\x89PNG\r\n\x1a\n" + b"\x00" * 4])
    def test_rejects_non_png(self, data):
        with pytest.raises(ValueError):
            png_size(data)

    def test_rejects_png_without_leading_ihdr(self):
        bad = bytearray(make_png(10, 10))
        bad[12:16] = b"tEXt"
        with pytest.raises(ValueError):
            png_size(bytes(bad))

    @pytest.mark.parametrize(("w", "h"), [(0, 10), (10, 0), (MAX_PNG_SIDE + 1, 10)])
    def test_rejects_bad_dimensions(self, w, h):
        with pytest.raises(ValueError):
            png_size(make_png(w, h))


class TestVideoRectInCanvas:
    def test_same_aspect_fills_canvas(self):
        assert video_rect_in_canvas(1280, 720, 1920, 1080) == (0, 0, 1280, 720)

    def test_taller_canvas_letterboxes_top_and_bottom(self):
        assert video_rect_in_canvas(1280, 800, 1920, 1080) == (0, 40, 1280, 720)

    def test_wider_canvas_pillarboxes_sides(self):
        assert video_rect_in_canvas(1600, 720, 1920, 1080) == (160, 0, 1280, 720)

    def test_stays_inside_canvas_with_odd_sizes(self):
        x, y, w, h = video_rect_in_canvas(1001, 777, 1920, 1080)
        assert x >= 0 and y >= 0 and x + w <= 1001 and y + h <= 777
        assert w > 0 and h > 0

    @pytest.mark.parametrize("dims", [(0, 720, 1920, 1080), (1280, 720, 0, 1080)])
    def test_rejects_zero(self, dims):
        with pytest.raises(ValueError):
            video_rect_in_canvas(*dims)
