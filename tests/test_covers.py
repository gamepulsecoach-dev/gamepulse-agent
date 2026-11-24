"""Title cards ("capas") inside a dossier compilation."""

import base64
from pathlib import Path

import pytest
from conftest import FakeRun, inputs
from test_overlay import make_png
from test_runner import FakeCtx, FakePopen
from test_server import ORIGIN, Harness, compile_body

from gp_agent.exporter import ClipRequest, CompileRequest, Cover, export_compilation, interleave
from gp_agent.ffmpeg_cmd import AUDIO_RATE, COVER_FADE_MS, Probe, build_cover_args
from gp_agent.runner import Runner

YT = "https://www.youtube.com/watch?v=abc123"
HD25 = Probe(1920, 1080, True, 8_000, fps=25.0)


def arg(args, flag):
    return args[args.index(flag) + 1]


def req(**over) -> ClipRequest:
    base = dict(video_url=YT, timestamp_ms=30_000, pre_ms=5000, post_ms=3000, game="J", category="C", index=1)
    return ClipRequest(**{**base, **over})


class TestBuildCoverArgs:
    def args(self, target=HD25, duration_ms=3000):
        return build_cover_args(Path("ffmpeg"), Path("cover.png"), Path("out.mp4"), duration_ms, "h264_mf", target)

    def test_loops_the_png_for_the_duration_at_the_clip_frame_rate(self):
        a = self.args()
        assert inputs(a)[0] == "cover.png"
        assert arg(a, "-loop") == "1"
        assert arg(a, "-framerate") == "25"
        assert a.count("-t") == 2 and arg(a, "-t") == "3.000"

    def test_adds_silent_stereo_audio(self):
        a = self.args()
        assert f"anullsrc=r={AUDIO_RATE}:cl=stereo" in inputs(a)
        assert arg(a, "-ac") == "2"

    def test_fits_the_clip_size_with_fades(self):
        graph = arg(self.args(Probe(1280, 720, True, 1000, fps=50.0)), "-filter_complex")
        assert "scale=1280:720:force_original_aspect_ratio=decrease" in graph
        assert "pad=1280:720" in graph
        fade_s = f"{COVER_FADE_MS / 1000:.3f}"
        assert f"fade=t=in:st=0:d={fade_s}" in graph
        assert f"fade=t=out:st={3 - COVER_FADE_MS / 1000:.3f}:d={fade_s}" in graph
        assert "format=yuv420p" in graph

    def test_unknown_fps_uses_default(self):
        assert arg(self.args(Probe(1920, 1080, True, 1000, fps=None)), "-framerate") == "25"

    def test_ends_with_destination_and_faststart(self):
        a = self.args()
        assert a[-1] == "out.mp4"
        assert "-movflags" in a

    def test_rejects_too_short(self):
        with pytest.raises(ValueError):
            self.args(duration_ms=COVER_FADE_MS * 2)


class TestInterleave:
    def test_covers_go_before_their_clip_in_order(self):
        parts = [Path("p0"), Path("p1"), Path("p2")]
        covers = [(2, Path("c2")), (0, Path("c0"))]
        assert interleave(parts, covers) == [Path("c0"), Path("p0"), Path("p1"), Path("c2"), Path("p2")]

    def test_two_covers_before_the_same_clip_keep_their_order(self):
        # start card + first category card both sit before clip 0
        out = interleave([Path("p0")], [(0, Path("start")), (0, Path("cat"))])
        assert out == [Path("start"), Path("cat"), Path("p0")]

    def test_no_covers_returns_parts(self):
        assert interleave([Path("p0")], []) == [Path("p0")]

    def test_cover_index_outside_clips_is_rejected(self):
        with pytest.raises(ValueError):
            interleave([Path("p0")], [(1, Path("c"))])


class TestCompilationWithCovers:
    def runner(self, tmp_path, tools, run, popen):
        return Runner(tools, cache_dir=tmp_path / "cache", run=run, popen=popen)

    def test_renders_cards_at_first_clip_format_and_reencodes_join(self, tmp_path, tools):
        run, popen = FakeRun(), FakePopen()
        covers = (Cover(0, make_png(1920, 1080), 3000), Cover(1, make_png(1920, 1080), 2500))
        compile_req = CompileRequest("J", "T", (req(), req(timestamp_ms=50_000)), covers=covers)
        out = export_compilation(compile_req, self.runner(tmp_path, tools, run, popen), tmp_path, FakeCtx())
        assert out.exists()
        cards = run.tool_calls("-loop")
        assert len(cards) == 2
        assert [arg(c, "-t") for c in cards] == ["3.000", "2.500"]
        assert run.tool_calls("concat") == []  # no stream copy: cards are always re-encoded with the clips
        names = [Path(p).name for p in inputs(popen.procs[0].args)]
        assert len(names) == 4
        assert names[0].startswith("cover_") and names[2].startswith("cover_")
        assert names[1].startswith("part_") and names[3].startswith("part_")

    def test_card_files_are_cleaned_up(self, tmp_path, tools):
        run = FakeRun()
        compile_req = CompileRequest("J", "T", (req(),), covers=(Cover(0, make_png(16, 9), 3000),))
        export_compilation(compile_req, self.runner(tmp_path, tools, run, FakePopen()), tmp_path)
        card = run.tool_calls("-loop")[0]
        assert not Path(inputs(card)[0]).exists()
        assert not Path(card[-1]).exists()

    def test_progress_keeps_rising_into_the_join(self, tmp_path, tools):
        ctx = FakeCtx()
        compile_req = CompileRequest("J", "T", (req(),), covers=(Cover(0, make_png(16, 9), 3000),))
        export_compilation(compile_req, self.runner(tmp_path, tools, FakeRun(), FakePopen()), tmp_path, ctx)
        assert ctx.reports == sorted(ctx.reports)
        assert ctx.reports[-1] > 0.75  # join step reached (the job itself ends at 100 %)

    def test_invalid_card_png_fails_before_running_anything(self, tmp_path, tools):
        run = FakeRun()
        compile_req = CompileRequest("J", "T", (req(),), covers=(Cover(0, b"nope", 3000),))
        with pytest.raises(ValueError):
            export_compilation(compile_req, self.runner(tmp_path, tools, run, FakePopen()), tmp_path)
        assert run.calls == []


def b64_png() -> str:
    return base64.b64encode(make_png(16, 9)).decode()


class TestCompileEndpointCovers:
    @pytest.fixture
    def h(self, tmp_path):
        return Harness(tmp_path)

    def post(self, h, body):
        token = h.pair()
        return h.client.post("/compile", json=body, headers={"Origin": ORIGIN, "X-GP-Token": token})

    def test_covers_reach_the_request(self, h):
        body = compile_body(covers=[{"before": 0, "png": b64_png(), "durationMs": 3000},
                                    {"before": 1, "png": b64_png(), "durationMs": 2500}])
        assert self.post(h, body).status_code == 202
        covers = h.requests[0].covers
        assert [(c.before, c.duration_ms) for c in covers] == [(0, 3000), (1, 2500)]
        assert covers[0].png == make_png(16, 9)

    def test_without_covers_still_works(self, h):
        assert self.post(h, compile_body()).status_code == 202
        assert h.requests[0].covers == ()

    def test_cover_after_last_clip_is_rejected(self, h):
        body = compile_body(covers=[{"before": 2, "png": b64_png(), "durationMs": 3000}])
        assert self.post(h, body).status_code == 400
        assert h.requests == []

    def test_bad_png_is_rejected(self, h):
        body = compile_body(covers=[{"before": 0, "png": "bm90IGEgcG5n", "durationMs": 3000}])
        assert self.post(h, body).status_code == 400

    @pytest.mark.parametrize("dur", [500, 20_000])
    def test_duration_out_of_range(self, h, dur):
        body = compile_body(covers=[{"before": 0, "png": b64_png(), "durationMs": dur}])
        assert self.post(h, body).status_code == 422
