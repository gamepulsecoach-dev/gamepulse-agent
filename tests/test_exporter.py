from pathlib import Path

import pytest
from conftest import FakeRun, inputs
from test_overlay import make_png
from test_runner import FakeCtx, FakePopen

from gp_agent.edit_cmd import OverlaySegment, Pause
from gp_agent.exporter import (
    ClipRequest,
    CompileRequest,
    EditRequest,
    Overlay,
    export,
    export_clip,
    export_compilation,
)
from gp_agent.runner import Runner

YT = "https://www.youtube.com/watch?v=abc123"


def req(**over) -> ClipRequest:
    base = dict(
        video_url=YT, timestamp_ms=65_000, pre_ms=5000, post_ms=3000,
        game="Braga vs Porto", category="Golo", index=1,
    )
    return ClipRequest(**{**base, **over})


def arg(args, flag):
    return args[args.index(flag) + 1]


def clip_calls(run: FakeRun):
    return run.tool_calls("-filter_complex")


class TestExportClip:
    def test_path_window_and_probe(self, make_runner, tmp_path):
        run = FakeRun()
        out = export_clip(req(timestamp_ms=30_000), make_runner(run), tmp_path / "GamePulse")
        assert out == tmp_path / "GamePulse" / "Braga vs Porto" / "Golo" / "Braga-vs-Porto_Golo_01_00m30s.mp4"
        assert out.exists()
        cut = clip_calls(run)[0]
        assert arg(cut, "-ss") == "25.000"
        assert arg(cut, "-t") == "8.000"

    def test_dossier_folder_under_team(self, make_runner, tmp_path):
        out = export_clip(req(timestamp_ms=30_000, team="Braga", folder="Dossier Golo"), make_runner(FakeRun()), tmp_path)
        assert out == tmp_path / "Braga" / "Dossier Golo" / "Braga-vs-Porto_Golo_01_00m30s.mp4"
        assert out.exists()

    def test_window_clamped_to_video_duration(self, make_runner, tmp_path):
        run = FakeRun()  # probe says 60 s
        export_clip(req(timestamp_ms=59_000), make_runner(run), tmp_path)
        assert arg(clip_calls(run)[0], "-t") == "6.000"

    def test_overlay_becomes_freeze_with_letterbox_crop(self, make_runner, tmp_path):
        run = FakeRun()
        overlay = Overlay(timestamp_ms=52_000, png=make_png(1280, 800))
        export_clip(req(timestamp_ms=50_000, overlays=(overlay,)), make_runner(run), tmp_path)
        cut = clip_calls(run)[0]
        assert len(inputs(cut)) == 2
        graph = arg(cut, "-filter_complex")
        assert "crop=1280:720:0:40" in graph
        assert "trim=start=0.000:end=7.000" in graph  # 52 s - clip start 45 s

    def test_overlay_outside_clip_is_ignored(self, make_runner, tmp_path):
        run = FakeRun()
        export_clip(req(timestamp_ms=30_000, overlays=(Overlay(10_000, make_png(16, 9)),)), make_runner(run), tmp_path)
        assert len(inputs(clip_calls(run)[0])) == 1

    def test_invalid_png_rejected(self, make_runner, tmp_path):
        with pytest.raises(ValueError):
            export_clip(req(timestamp_ms=30_000, overlays=(Overlay(30_000, b"not a png"),)),
                        make_runner(FakeRun()), tmp_path)

    def test_png_temp_files_are_removed(self, make_runner, tmp_path):
        run = FakeRun()
        export_clip(req(timestamp_ms=30_000, overlays=(Overlay(30_000, make_png(16, 9)),)), make_runner(run), tmp_path)
        png = Path(inputs(clip_calls(run)[0])[1])
        assert png.suffix == ".png" and not png.exists()

    def test_rejects_unsafe_url_before_running_anything(self, make_runner, tmp_path):
        run = FakeRun()
        with pytest.raises(ValueError):
            export_clip(req(video_url="http://192.168.1.1/a.mp4"), make_runner(run), tmp_path)
        assert run.calls == []


class TestExportCompilation:
    def test_cuts_each_then_joins(self, make_runner, tmp_path):
        run = FakeRun()
        clips = (req(timestamp_ms=10_000), req(timestamp_ms=20_000, overlays=(Overlay(19_000, make_png(16, 9)),)))
        out = export_compilation(CompileRequest("Braga vs Porto", "Golo", clips), make_runner(run), tmp_path)
        assert out == tmp_path / "Braga vs Porto" / "Braga-vs-Porto_Compilado_Golo.mp4"
        assert out.exists()
        assert len(clip_calls(run)) == 2
        assert len(run.tool_calls("concat")) == 1
        assert len(run.tool_calls("-o")) == 1  # same video downloaded once

    def test_parts_are_cleaned_up(self, make_runner, tmp_path):
        run = FakeRun()
        export_compilation(CompileRequest("J", "T", (req(timestamp_ms=30_000), req(timestamp_ms=5000))),
                           make_runner(run), tmp_path)
        assert all(not Path(c[-1]).exists() for c in clip_calls(run))

    def test_needs_clips(self, make_runner, tmp_path):
        with pytest.raises(ValueError):
            export_compilation(CompileRequest("J", "T", ()), make_runner(FakeRun()), tmp_path)

    def test_validates_all_urls_first(self, make_runner, tmp_path):
        run = FakeRun()
        bad = CompileRequest("J", "T", (req(), req(video_url="https://vimeo.com/1")))
        with pytest.raises(ValueError):
            export_compilation(bad, make_runner(run), tmp_path)
        assert run.calls == []


def test_export_dispatches_by_type(make_runner, tmp_path):
    run = FakeRun()
    assert export(req(timestamp_ms=30_000), make_runner(run), tmp_path).name.endswith("_01_00m30s.mp4")
    assert "Compilado" in export(CompileRequest("J", "T", (req(timestamp_ms=30_000),)), make_runner(run), tmp_path).name


class TestExportEdit:
    def make(self, tmp_path, tools, popen):
        return Runner(tools, cache_dir=tmp_path / "cache", run=FakeRun(), popen=popen)

    def edit_req(self, src, images=None, segments=None, pauses=()):
        return EditRequest(
            src=src, pauses=pauses,
            segments=segments or (OverlaySegment(0, 1_000), OverlaySegment(1, 2_000)),
            images=images or (make_png(64, 36), make_png(64, 36)),
        )

    def test_writes_next_version_with_images_and_list(self, tmp_path, tools):
        popen = FakePopen()
        src = tmp_path / "Jogo" / "Golo" / "Jogo_Golo_01_00m30s.mp4"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"clip")
        seen = {}

        def spy(args, **kwargs):
            listing = Path(args[args.index("concat") + 4])
            seen["list"] = listing.read_text(encoding="utf-8")
            seen["pngs"] = sorted(p.name for p in listing.parent.glob("*.png"))
            return popen(args, **kwargs)

        out = export(self.edit_req(src, pauses=(Pause(500, 1_000),)), self.make(tmp_path, tools, spy), tmp_path, FakeCtx())
        assert out == src.with_name("Jogo_Golo_01_00m30s_editado_v1.mp4")
        assert out.read_bytes() == b"edited"
        assert seen["pngs"] == ["overlay_000.png", "overlay_001.png"]
        assert "duration 2.000" in seen["list"]

    def test_images_must_share_one_size(self, tmp_path, tools):
        src = tmp_path / "c.mp4"
        src.write_bytes(b"clip")
        bad = self.edit_req(src, images=(make_png(64, 36), make_png(32, 18)))
        with pytest.raises(ValueError):
            export(bad, self.make(tmp_path, tools, FakePopen()), tmp_path, FakeCtx())

    def test_invalid_png_is_refused_before_ffmpeg(self, tmp_path, tools):
        popen = FakePopen()
        src = tmp_path / "c.mp4"
        src.write_bytes(b"clip")
        with pytest.raises(ValueError):
            export(self.edit_req(src, images=(b"nope", b"nope")), self.make(tmp_path, tools, popen), tmp_path, FakeCtx())
        assert popen.procs == []
