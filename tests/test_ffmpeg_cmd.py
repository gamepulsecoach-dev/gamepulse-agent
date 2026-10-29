from pathlib import Path

import pytest

from gp_agent.ffmpeg_cmd import (
    FREEZE_MS,
    Freeze,
    Probe,
    build_clip_args,
    build_concat_args,
    clip_window,
    concat_list_text,
    output_size,
    parse_probe,
    pick_encoder,
)

HD = Probe(width=1920, height=1080, has_audio=True, duration_ms=60_000)
FF, SRC, DST = Path("ffmpeg"), Path("in.mp4"), Path("out.mp4")


class TestClipWindow:
    def test_applies_pre_and_post(self):
        assert clip_window(60_000, pre_ms=5_000, post_ms=3_000) == (55_000, 63_000)

    def test_zero_pre_is_respected(self):
        assert clip_window(60_000, pre_ms=0, post_ms=5_000) == (60_000, 65_000)

    def test_clamps_start_at_zero(self):
        assert clip_window(2_000, pre_ms=5_000, post_ms=5_000) == (0, 7_000)

    def test_clamps_end_at_duration(self):
        assert clip_window(98_000, pre_ms=5_000, post_ms=5_000, duration_ms=100_000) == (93_000, 100_000)

    @pytest.mark.parametrize(("pre", "post"), [(-1, 5_000), (5_000, -1)])
    def test_rejects_negative_offsets(self, pre, post):
        with pytest.raises(ValueError):
            clip_window(10_000, pre_ms=pre, post_ms=post)

    def test_rejects_empty_window(self):
        with pytest.raises(ValueError):
            clip_window(0, pre_ms=0, post_ms=0)

    def test_rejects_event_after_end_of_video(self):
        with pytest.raises(ValueError):
            clip_window(200_000, pre_ms=0, post_ms=5_000, duration_ms=100_000)


class TestPickEncoder:
    def test_windows_uses_media_foundation(self):
        assert pick_encoder("win32") == "h264_mf"

    def test_macos_uses_videotoolbox(self):
        assert pick_encoder("darwin") == "h264_videotoolbox"

    def test_other_platforms_unsupported(self):
        with pytest.raises(ValueError):
            pick_encoder("linux")


FFMPEG_STDERR = """
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'in.mp4':
  Duration: 00:01:05.50, start: 0.000000, bitrate: 1205 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 1920x1080 [SAR 1:1 DAR 16:9], 1072 kb/s, 25 fps
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 128 kb/s
At least one output file must be specified
"""


class TestParseProbe:
    def test_reads_video_audio_and_duration(self):
        assert parse_probe(FFMPEG_STDERR) == Probe(1920, 1080, True, 65_500, fps=25.0)

    def test_fractional_fps(self):
        assert parse_probe(FFMPEG_STDERR.replace("25 fps", "29.97 fps")).fps == 29.97

    def test_fps_missing(self):
        assert parse_probe(FFMPEG_STDERR.replace(", 25 fps", "")).fps is None

    def test_audio_line_does_not_supply_fps(self):
        text = FFMPEG_STDERR.replace(", 25 fps", "").replace("128 kb/s", "128 kb/s, 30 fps")
        assert parse_probe(text).fps is None

    def test_no_audio(self):
        text = "\n".join(line for line in FFMPEG_STDERR.splitlines() if "Audio" not in line)
        assert parse_probe(text).has_audio is False

    def test_unknown_duration(self):
        text = FFMPEG_STDERR.replace("00:01:05.50", "N/A")
        assert parse_probe(text).duration_ms is None

    def test_no_video_stream(self):
        with pytest.raises(ValueError):
            parse_probe("Stream #0:0: Audio: aac, 44100 Hz\n")


class TestOutputSize:
    def test_keeps_1080(self):
        assert output_size(HD) == (1920, 1080)

    def test_caps_4k_at_1080(self):
        assert output_size(Probe(3840, 2160, True, None)) == (1920, 1080)

    def test_small_video_not_upscaled(self):
        assert output_size(Probe(320, 240, True, None)) == (320, 240)

    def test_dimensions_are_even(self):
        w, h = output_size(Probe(853, 479, True, None))
        assert w % 2 == 0 and h % 2 == 0


def arg(args, flag):
    return args[args.index(flag) + 1]


def inputs(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "-i"]


class TestBuildClipArgs:
    def test_plain_cut(self):
        args = build_clip_args(FF, SRC, DST, 55_500, 63_000, "h264_mf", HD)
        assert args[0] == "ffmpeg" and args[-1] == "out.mp4"
        assert arg(args, "-ss") == "55.500"
        assert arg(args, "-t") == "7.500"
        # -ss/-t must be input options, otherwise -t trims the output and drops the freezes
        assert args.index("-ss") < args.index("-t") < args.index("-i")
        assert arg(args, "-c:v") == "h264_mf"
        assert arg(args, "-ar") == "48000" and arg(args, "-ac") == "2"
        assert "-y" in args and "-hide_banner" in args
        graph = arg(args, "-filter_complex")
        assert "scale=1920:1080" in graph
        assert "concat=n=1:v=1:a=1" in graph
        assert arg(args, "-map") == "[ov]"

    def test_freeze_adds_png_input_and_segments(self):
        freeze = Freeze(at_ms=2_000, png=Path("d.png"), crop=(0, 40, 1280, 720))
        args = build_clip_args(FF, SRC, DST, 0, 8_000, "h264_mf", HD, (freeze,))
        assert inputs(args) == ["in.mp4", "d.png"]
        graph = arg(args, "-filter_complex")
        assert "concat=n=3:v=1:a=1" in graph
        assert "[1:v]crop=1280:720:0:40,scale=1920:1080" in graph
        assert f"tpad=stop_mode=clone:stop_duration={FREEZE_MS / 1000:.3f}" in graph
        assert "trim=start=0.000:end=2.000" in graph
        assert "anullsrc" in graph  # silence while frozen

    def test_freezes_are_sorted_and_each_gets_own_input(self):
        f1 = Freeze(5_000, Path("b.png"), (0, 0, 10, 10))
        f2 = Freeze(1_000, Path("a.png"), (0, 0, 10, 10))
        args = build_clip_args(FF, SRC, DST, 0, 8_000, "h264_mf", HD, (f1, f2))
        assert inputs(args) == ["in.mp4", "a.png", "b.png"]
        assert "concat=n=5:v=1:a=1" in arg(args, "-filter_complex")

    def test_freeze_at_start_skips_empty_segment(self):
        f = Freeze(0, Path("a.png"), (0, 0, 10, 10))
        graph = arg(build_clip_args(FF, SRC, DST, 0, 8_000, "h264_mf", HD, (f,)), "-filter_complex")
        assert "concat=n=2:v=1:a=1" in graph

    def test_freeze_at_end_is_pulled_inside_the_clip(self):
        f = Freeze(8_000, Path("a.png"), (0, 0, 10, 10))
        graph = arg(build_clip_args(FF, SRC, DST, 0, 8_000, "h264_mf", HD, (f,)), "-filter_complex")
        assert "trim=start=8.000," not in graph
        assert "concat=n=3:v=1:a=1" in graph

    def test_source_without_audio_gets_silence(self):
        mute = Probe(1920, 1080, False, None)
        graph = arg(build_clip_args(FF, SRC, DST, 0, 4_000, "h264_mf", mute), "-filter_complex")
        assert "[0:a]" not in graph
        assert "anullsrc=r=48000:cl=stereo,atrim=duration=4.000" in graph

    def test_freeze_outside_clip_rejected(self):
        f = Freeze(9_000, Path("a.png"), (0, 0, 10, 10))
        with pytest.raises(ValueError):
            build_clip_args(FF, SRC, DST, 0, 8_000, "h264_mf", HD, (f,))

    def test_rejects_invalid_range(self):
        with pytest.raises(ValueError):
            build_clip_args(FF, SRC, DST, 5_000, 5_000, "h264_mf", HD)


class TestConcat:
    def test_list_text_quotes_and_escapes(self):
        text = concat_list_text([Path("C:/a/b.mp4"), Path("/x/it's.mp4")])
        assert text.splitlines() == ["file 'C:/a/b.mp4'", "file '/x/it'\\''s.mp4'"]

    def test_concat_args_stream_copy(self):
        args = build_concat_args(FF, Path("list.txt"), DST)
        assert arg(args, "-f") == "concat"
        assert arg(args, "-safe") == "0"
        assert arg(args, "-i") == "list.txt"
        assert arg(args, "-c") == "copy"
        assert args[-1] == "out.mp4"
