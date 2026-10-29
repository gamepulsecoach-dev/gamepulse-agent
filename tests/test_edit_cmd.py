from pathlib import Path

import pytest

from gp_agent.edit_cmd import (
    EDIT_BITRATE,
    OverlaySegment,
    Pause,
    build_edit_args,
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
