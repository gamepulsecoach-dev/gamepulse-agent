import os
import subprocess
from pathlib import Path

import pytest
from conftest import FakeRun

from gp_agent.ffmpeg_cmd import Freeze, Probe
from gp_agent.edit_cmd import Loupe, Pause, Rect, ZoomCrop
from gp_agent.runner import Cancelled, Runner, RunnerError, build_download_args, cache_key

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

    def test_deno_passed_as_js_runtime(self, tmp_path):
        # YouTube needs a JS runtime; the bundled deno is not on PATH
        args = build_download_args(Path("yt"), Path("ff"), YT, tmp_path / "k", deno=Path("d/deno.exe"))
        assert args[args.index("--js-runtimes") + 1] == "deno:" + str(Path("d/deno.exe"))
        assert args.index("--js-runtimes") < args.index("--")

    def test_without_deno_no_js_runtime_flag(self, tmp_path):
        assert "--js-runtimes" not in build_download_args(Path("yt"), Path("ff"), YT, tmp_path / "k")


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
        assert make_runner(FakeRun()).probe(tmp_path / "v.mp4") == Probe(1920, 1080, True, 60_000, fps=25.0)

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


class FakeProc:
    """Stands in for an ffmpeg Popen that writes `-progress` lines on stdout."""

    def __init__(self, args, lines, returncode, stderr, stderr_file, write_output):
        self.args = args
        self.stdout = iter(lines)
        self.returncode = None
        self._rc = returncode
        self.killed = False
        stderr_file.write(stderr.encode())
        if write_output and returncode == 0:
            Path(args[-1]).write_bytes(b"edited")

    def wait(self, timeout=None):
        self.returncode = -9 if self.killed else self._rc
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


class FakePopen:
    def __init__(self, lines=("out_time_us=1000000", "out_time_us=2000000", "progress=end"),
                 returncode=0, stderr="", write_output=True):
        self.lines, self.returncode, self.stderr, self.write_output = lines, returncode, stderr, write_output
        self.procs: list[FakeProc] = []

    def __call__(self, args, **kwargs):
        proc = FakeProc(args, [f"{line}\n" for line in self.lines], self.returncode, self.stderr,
                        kwargs["stderr"], self.write_output)
        self.procs.append(proc)
        return proc


class FakeCtx:
    def __init__(self, cancel_after=None):
        self.reports: list[float] = []
        self._cancel_after = cancel_after

    @property
    def cancelled(self):
        return self._cancel_after is not None and len(self.reports) >= self._cancel_after

    def report(self, fraction):
        self.reports.append(fraction)


class TestEdit:
    CLIP = Probe(1920, 1080, True, 3_000, fps=25.0)

    def runner(self, tmp_path, tools, popen):
        return Runner(tools, cache_dir=tmp_path / "cache", run=FakeRun(), popen=popen)

    def test_writes_atomically_and_reports_progress(self, tmp_path, tools):
        popen = FakePopen()
        dst = tmp_path / "out" / "clip_editado_v1.mp4"
        ctx = FakeCtx()
        self.runner(tmp_path, tools, popen).edit(
            tmp_path / "clip.mp4", dst, tmp_path / "list.txt", self.CLIP, (Pause(1_000, 1_000),), ctx)
        assert dst.read_bytes() == b"edited"
        assert not list(dst.parent.glob("*.part*"))
        assert ctx.reports == [0.25, 0.5]  # output = 3 s clip + 1 s pause
        assert popen.procs[0].args[popen.procs[0].args.index("-progress") + 1] == "pipe:1"

    def test_zoom_and_loupes_go_through_a_graph_file(self, tmp_path, tools):
        popen = FakePopen()
        listing = tmp_path / "list.txt"
        zoom = (ZoomCrop(0, 0, 0, 1, 1), ZoomCrop(1_000, 0.25, 0.25, 0.5, 0.5))
        loupe = Loupe(0, 1_000, Rect(0, 0, 0.1, 0.1), Rect(0.5, 0.5, 0.2, 0.2), "rect")
        self.runner(tmp_path, tools, popen).edit(
            tmp_path / "clip.mp4", tmp_path / "o.mp4", listing, self.CLIP, (), FakeCtx(), zoom=zoom, loupes=(loupe,))
        args = popen.procs[0].args
        graph_file = Path(args[args.index("-/filter_complex") + 1])
        assert graph_file == tmp_path / "graph.txt"
        text = graph_file.read_text(encoding="utf-8")
        assert "zoompan" in text and "crop=192:108:0:0" in text

    def test_without_zoom_the_graph_stays_inline(self, tmp_path, tools):
        popen = FakePopen()
        self.runner(tmp_path, tools, popen).edit(
            tmp_path / "clip.mp4", tmp_path / "o.mp4", tmp_path / "list.txt", self.CLIP, (), FakeCtx())
        assert "-filter_complex" in popen.procs[0].args
        assert not (tmp_path / "graph.txt").exists()

    def test_cancel_kills_ffmpeg_and_leaves_nothing(self, tmp_path, tools):
        popen = FakePopen(write_output=False)
        dst = tmp_path / "out" / "clip_editado_v1.mp4"
        with pytest.raises(Cancelled):
            self.runner(tmp_path, tools, popen).edit(
                tmp_path / "clip.mp4", dst, tmp_path / "list.txt", self.CLIP, (), FakeCtx(cancel_after=1))
        assert popen.procs[0].killed
        assert not dst.exists()

    def test_failure_reports_stderr_tail(self, tmp_path, tools):
        popen = FakePopen(returncode=1, stderr="Invalid data found")
        with pytest.raises(RunnerError, match="Invalid data found"):
            self.runner(tmp_path, tools, popen).edit(
                tmp_path / "clip.mp4", tmp_path / "o.mp4", tmp_path / "l.txt", self.CLIP, (), FakeCtx())

    def test_missing_binary(self, tmp_path, tools):
        def boom(args, **kwargs):
            raise FileNotFoundError(args[0])

        with pytest.raises(RunnerError, match="não foi possível executar"):
            self.runner(tmp_path, tools, boom).edit(
                tmp_path / "clip.mp4", tmp_path / "o.mp4", tmp_path / "l.txt", self.CLIP, (), FakeCtx())
