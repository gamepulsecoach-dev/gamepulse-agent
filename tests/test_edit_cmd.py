from pathlib import Path

import pytest

from gp_agent.edit_cmd import (
    EDIT_BITRATE,
    Loupe,
    OverlaySegment,
    Pause,
    Rect,
    ZoomCrop,
    build_edit_args,
    edit_graph,
    edited_path,
    output_duration_ms,
    overlay_list_text,
    parse_progress_ms,
)
from gp_agent.ffmpeg_cmd import Probe

SOUND = Probe(1920, 1080, True, 10_000, fps=25.0)
MUTE = Probe(1280, 720, False, 6_000, fps=30.0)


def graph(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


class TestOutputDuration:
    def test_adds_pauses_inside_the_clip(self):
        assert output_duration_ms(10_000, (Pause(2_000, 1_500), Pause(9_000, 500))) == 12_000

    def test_ignores_pauses_after_the_end(self):
        assert output_duration_ms(10_000, (Pause(12_000, 2_000),)) == 10_000


class TestOverlayList:
    def test_durations_in_seconds_and_last_image_repeated(self, tmp_path):
        imgs = [tmp_path / "a.png", tmp_path / "b.png"]
        text = overlay_list_text([OverlaySegment(0, 1_000), OverlaySegment(1, 40), OverlaySegment(0, 2_000)], imgs)
        lines = text.splitlines()
        assert lines[0] == "ffconcat version 1.0"
        assert lines[1:7] == [
            f"file '{imgs[0].as_posix()}'", "duration 1.000",
            f"file '{imgs[1].as_posix()}'", "duration 0.040",
            f"file '{imgs[0].as_posix()}'", "duration 2.000",
        ]
        # concat demuxer ignores the last duration unless the file is listed again
        assert lines[-1] == f"file '{imgs[0].as_posix()}'"

    def test_quotes_are_escaped(self, tmp_path):
        img = tmp_path / "it's.png"
        assert "it'\\''s.png" in overlay_list_text([OverlaySegment(0, 100)], [img])

    def test_needs_segments(self):
        with pytest.raises(ValueError):
            overlay_list_text([], [Path("a.png")])

    def test_image_index_must_exist(self):
        with pytest.raises(ValueError):
            overlay_list_text([OverlaySegment(2, 100)], [Path("a.png")])


class TestBuildEditArgs:
    def args(self, probe=SOUND, pauses=()):
        return build_edit_args(Path("ffmpeg"), Path("in.mp4"), Path("list.txt"), Path("out.mp4"), "h264_mf", probe, pauses)

    def test_inputs_overlay_list_and_progress(self):
        args = self.args()
        assert args[args.index("-i") + 1] == "in.mp4"
        assert ["-f", "concat", "-safe", "0", "-i", "list.txt"] == args[args.index("-f"):args.index("-f") + 6]
        assert args[args.index("-progress") + 1] == "pipe:1"
        assert "-nostats" in args
        assert args[args.index("-b:v") + 1] == EDIT_BITRATE
        assert args[-1] == "out.mp4"

    def test_without_pauses_overlays_whole_clip(self):
        g = graph(self.args())
        assert "split=1" in g
        assert "[1:v]scale=1920:1080,format=rgba,setpts=PTS-STARTPTS[ol]" in g
        assert "overlay=0:0:format=auto:eof_action=repeat" in g
        assert "concat=n=1:v=1:a=1" in g

    def test_pause_holds_frame_with_silence(self):
        g = graph(self.args(pauses=(Pause(2_000, 1_500),)))
        assert "concat=n=3:v=1:a=1" in g
        assert "trim=start=0.000:end=2.000" in g
        assert "trim=start=2.000,setpts=PTS-STARTPTS,trim=end_frame=1,tpad=stop_mode=clone:stop_duration=1.500" in g
        assert "anullsrc=r=48000:cl=stereo,atrim=duration=1.500" in g
        assert "asplit=2" in g  # audio only for the two moving segments

    def test_mute_clip_gets_silence(self):
        g = graph(self.args(probe=MUTE, pauses=(Pause(1_000, 500),)))
        assert "[0:a]" not in g
        assert g.count("anullsrc") == 3

    def test_pauses_are_ordered(self):
        g = graph(self.args(pauses=(Pause(5_000, 500), Pause(1_000, 500))))
        assert g.index("trim=start=1.000,setpts") < g.index("trim=start=5.000,setpts")

    def test_pause_at_the_very_end_still_has_a_frame(self):
        g = graph(self.args(pauses=(Pause(10_000, 500),)))
        assert "trim=start=9.900,setpts" in g

    def test_needs_duration(self):
        with pytest.raises(ValueError):
            self.args(probe=Probe(1920, 1080, True, None))

    def test_graph_file_replaces_inline_graph(self):
        args = build_edit_args(Path("ffmpeg"), Path("in.mp4"), Path("list.txt"), Path("out.mp4"), "h264_mf",
                               SOUND, (), graph_file=Path("graph.txt"))
        assert "-filter_complex" not in args
        assert args[args.index("-/filter_complex") + 1] == "graph.txt"


ZOOM_IN = (ZoomCrop(0, 0.0, 0.0, 1.0, 1.0), ZoomCrop(1_000, 0.25, 0.25, 0.5, 0.5))


class TestZoom:
    def test_without_zoom_or_loupes_graph_is_unchanged(self):
        g = edit_graph(SOUND, ())
        assert g.endswith("[bv][ol]overlay=0:0:format=auto:eof_action=repeat,format=yuv420p[ov]")
        assert "zoompan" not in g

    def test_zoom_runs_after_the_drawings_at_a_fixed_rate(self):
        g = edit_graph(SOUND, (), zoom=ZOOM_IN)
        assert "[bv][ol]overlay=0:0:format=auto:eof_action=repeat[od]" in g
        assert g.endswith("[od]fps=25,zoompan=z='1/(1.000000-0.500000*clip((it-0.000)/1.000\,0\,1))'"
                          ":x='iw*(0.000000+0.250000*clip((it-0.000)/1.000\,0\,1))'"
                          ":y='ih*(0.000000+0.250000*clip((it-0.000)/1.000\,0\,1))'"
                          ":d=1:s=1920x1080:fps=25,format=yuv420p[ov]")

    def test_unknown_fps_falls_back_to_25(self):
        g = edit_graph(Probe(1280, 720, False, 6_000), (), zoom=ZOOM_IN)
        assert "fps=25,zoompan" in g and ":s=1280x720:fps=25," in g

    def test_steady_stretches_add_no_terms(self):
        crops = ZOOM_IN + (ZoomCrop(3_000, 0.25, 0.25, 0.5, 0.5), ZoomCrop(4_000, 0.0, 0.0, 1.0, 1.0))
        g = edit_graph(SOUND, (), zoom=crops)
        assert g.count("clip((it-1.000)") == 0  # 1 s -> 3 s holds the same crop
        assert g.count("clip((it-3.000)/1.000") == 3

    def test_single_crop_is_constant(self):
        g = edit_graph(MUTE, (), zoom=(ZoomCrop(0, 0.1, 0.2, 0.5, 0.5),))
        assert "zoompan=z='1/(0.500000)':x='iw*(0.100000)':y='ih*(0.200000)'" in g
        assert "fps=30,zoompan" in g


class TestLoupes:
    CIRCLE = Loupe(1_000, 4_000, Rect(0.1, 0.2, 0.1, 0.1), Rect(0.5, 0.4, 0.2, 0.3), "circle")

    def test_circle_is_cropped_scaled_masked_and_shown_in_its_window(self):
        g = edit_graph(SOUND, (), loupes=(self.CIRCLE,))
        assert "[bv]split=2[lb0][lsrc0]" in g
        assert "[lsrc0]crop=192:108:192:216,scale=384:324,format=yuva420p[lc0]" in g
        assert "color=c=black:s=384x324:d=0.04,format=gray,geq=lum='255*lte(hypot((X+0.5-W/2)/(W/2)\,(Y+0.5-H/2)/(H/2))\,1)'[lm0]" in g
        assert "[lc0][lm0]alphamerge[ll0]" in g
        assert "[lb0][ll0]overlay=960:432:enable='between(t,1.000,4.000)'[lv0]" in g
        assert "[lv0][ol]overlay=0:0" in g

    def test_rect_has_no_mask(self):
        rect = Loupe(0, 500, Rect(0.0, 0.0, 0.25, 0.25), Rect(0.75, 0.75, 0.25, 0.25), "rect")
        g = edit_graph(SOUND, (), loupes=(rect,))
        assert "alphamerge" not in g
        assert "[lsrc0]crop=480:270:0:0,scale=480:270[ll0]" in g

    def test_sizes_are_even_and_inside_the_frame(self):
        odd = Loupe(0, 500, Rect(0.9, 0.9, 0.1003, 0.1003), Rect(0.0, 0.0, 0.1003, 0.1003), "rect")
        g = edit_graph(MUTE, (), loupes=(odd,))
        assert "crop=128:72:1152:648,scale=128:72" in g

    def test_two_loupes_chain(self):
        other = Loupe(5_000, 6_000, Rect(0.0, 0.0, 0.1, 0.1), Rect(0.0, 0.0, 0.2, 0.2), "rect")
        g = edit_graph(SOUND, (), loupes=(self.CIRCLE, other))
        assert "[lv0]split=2[lb1][lsrc1]" in g
        assert "[lv1][ol]overlay=0:0" in g

    def test_loupes_then_zoom(self):
        g = edit_graph(SOUND, (), zoom=ZOOM_IN, loupes=(self.CIRCLE,))
        assert g.index("alphamerge") < g.index("[lv0][ol]overlay") < g.index("zoompan")


class TestParseProgress:
    @pytest.mark.parametrize("line,ms", [
        ("out_time_us=2500000\n", 2500),
        ("out_time_ms=1000000", 1000),  # ffmpeg writes microseconds here too
        ("progress=continue", None),
        ("out_time_us=N/A", None),
    ])
    def test_reads_output_time(self, line, ms):
        assert parse_progress_ms(line) == ms


class TestEditedPath:
    def test_first_version(self, tmp_path):
        src = tmp_path / "Jogo_Golo_01_12m30s.mp4"
        assert edited_path(src) == tmp_path / "Jogo_Golo_01_12m30s_editado_v1.mp4"

    def test_next_free_version(self, tmp_path):
        src = tmp_path / "Clip.mp4"
        (tmp_path / "Clip_editado_v1.mp4").write_bytes(b"x")
        (tmp_path / "Clip_editado_v2.part.mp4").write_bytes(b"x")  # export in progress
        assert edited_path(src).name == "Clip_editado_v3.mp4"

    def test_editing_an_edited_clip_keeps_one_suffix(self, tmp_path):
        src = tmp_path / "Clip_editado_v1.mp4"
        src.write_bytes(b"x")
        assert edited_path(src).name == "Clip_editado_v2.mp4"
