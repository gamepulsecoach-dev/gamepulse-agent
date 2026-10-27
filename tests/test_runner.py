import os
import subprocess
from pathlib import Path

import pytest
from conftest import FakeRun

from gp_agent.ffmpeg_cmd import Freeze, Probe
from gp_agent.runner import RunnerError, build_download_args, cache_key

YT = "https://www.youtube.com/watch?v=abc123"
HD = Probe(1920, 1080, True, 60_000)


class TestCacheKey:
    def test_stable_and_hex(self):
        assert cache_key(YT) == cache_key(YT)
        assert all(c in "0123456789abcdef" for c in cache_key(YT))

    def test_differs_per_url(self):
        assert cache_key(YT) != cache_key(YT + "x")


class TestBuildDownloadArgs:
    def test_shape(self, tmp_path):
        args = build_download_args(Path("yt"), Path("ff/ffmpeg.exe"), YT, tmp_path / "k")
        assert args[0] == "yt"
        assert args[-1] == YT
        assert "--no-playlist" in args
        assert args[args.index("--ffmpeg-location") + 1] == str(Path("ff/ffmpeg.exe"))
        assert args[args.index("-o") + 1] == str(tmp_path / "k") + ".%(ext)s"
        assert "height<=1080" in args[args.index("-f") + 1]

    def test_url_after_double_dash(self, tmp_path):
        # A url starting with '-' must never be read as an option
        args = build_download_args(Path("yt"), Path("ff"), YT, tmp_path / "k")
        assert args[-2] == "--"


class TestEnsureVideo:
    def test_downloads_when_missing(self, make_runner):
        run = FakeRun()
        path = make_runner(run).ensure_video(YT)
        assert path.exists() and path.suffix == ".mp4"
        assert len(run.calls) == 1

    def test_cache_hit_skips_download_and_touches(self, make_runner):
        run = FakeRun()
        runner = make_runner(run, clock=lambda: 2_000_000.0)
        first = runner.ensure_video(YT)
        os.utime(first, (10, 10))
        assert runner.ensure_video(YT) == first
        assert len(run.calls) == 1
        assert first.stat().st_mtime == pytest.approx(2_000_000.0)

    def test_tool_failure_raises_with_stderr_tail(self, make_runner):
        with pytest.raises(RunnerError, match="Video unavailable"):
            make_runner(FakeRun(returncode=1, stderr="ERROR: Video unavailable")).ensure_video(YT)

    def test_missing_output_raises(self, make_runner):
        with pytest.raises(RunnerError):
            make_runner(FakeRun(write_output=False)).ensure_video(YT)

    def test_timeout_raises_runner_error(self, make_runner):
        def run(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 1)

        with pytest.raises(RunnerError, match="tempo"):
            make_runner(run).ensure_video(YT)

    def test_missing_binary_raises_runner_error(self, make_runner):
        def run(args, **kwargs):
            raise FileNotFoundError(args[0])

        with pytest.raises(RunnerError):
            make_runner(run).ensure_video(YT)


class TestProbe:
    def test_parses_ffmpeg_output_even_with_nonzero_exit(self, make_runner, tmp_path):
        assert make_runner(FakeRun()).probe(tmp_path / "v.mp4") == HD

    def test_unreadable_video(self, make_runner, tmp_path):
        with pytest.raises(RunnerError, match="ler o vídeo"):
            make_runner(FakeRun(probe_stderr="Invalid data found")).probe(tmp_path / "v.mp4")


class TestCut:
    def test_writes_atomically_to_destination(self, make_runner, tmp_path):
        run = FakeRun()
        dst = tmp_path / "out" / "Jogo" / "clip.mp4"
        make_runner(run).cut(tmp_path / "src.mp4", dst, 1000, 6000, HD)
        assert dst.read_bytes() == b"clip"
        assert not list(dst.parent.glob("*.part*"))
        assert "h264_mf" in run.calls[0]

    def test_passes_freezes_as_inputs(self, make_runner, tmp_path):
        run = FakeRun()
        freeze = Freeze(1000, tmp_path / "d.png", (0, 0, 10, 10))
        make_runner(run).cut(tmp_path / "src.mp4", tmp_path / "o.mp4", 0, 5000, HD, (freeze,))
        assert str(tmp_path / "d.png") in run.calls[0]

    def test_failure_leaves_no_partial_file(self, make_runner, tmp_path):
        dst = tmp_path / "out" / "clip.mp4"
        with pytest.raises(RunnerError):
            make_runner(FakeRun(returncode=1, stderr="boom")).cut(tmp_path / "src.mp4", dst, 0, 1000, HD)
        assert not dst.exists()
        assert not list(dst.parent.glob("*"))


class TestConcat:
    def test_joins_parts_and_cleans_list_file(self, make_runner, tmp_path):
        run = FakeRun()
        parts = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
        dst = tmp_path / "out" / "all.mp4"
        make_runner(run).concat(parts, dst)
        assert dst.read_bytes() == b"clip"
        assert sorted(p.name for p in dst.parent.iterdir()) == ["all.mp4"]
        assert "concat" in run.calls[0]

    def test_needs_parts(self, make_runner, tmp_path):
        with pytest.raises(ValueError):
            make_runner(FakeRun()).concat([], tmp_path / "x.mp4")


class TestEvict:
    def test_removes_expired_but_keeps_in_use(self, make_runner):
        now = 100 * 86_400.0
        runner = make_runner(FakeRun(), clock=lambda: now)
        runner.cache_dir.mkdir(parents=True)
        old, busy, fresh = (runner.cache_dir / n for n in ("old.mp4", "busy.mp4", "fresh.mp4"))
        for p in (old, busy, fresh):
            p.write_bytes(b"x")
        os.utime(old, (0, 0))
        os.utime(busy, (0, 0))
        os.utime(fresh, (now, now))
        assert runner.evict(in_use=frozenset({busy})) == (old,)
        assert busy.exists() and fresh.exists() and not old.exists()

    def test_missing_cache_dir_is_noop(self, make_runner):
        assert make_runner(FakeRun()).evict() == ()
