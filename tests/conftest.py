import subprocess
from pathlib import Path

import pytest

from gp_agent.runner import Runner, Tools

PROBE_STDERR = (
    "  Duration: 00:01:00.00, start: 0.000000, bitrate: 1205 kb/s\n"
    "  Stream #0:0(und): Video: h264 (High), yuv420p, 1920x1080 [SAR 1:1 DAR 16:9], 25 fps\n"
    "  Stream #0:1(und): Audio: aac (LC), 44100 Hz, stereo\n"
)


class FakeRun:
    """Stands in for subprocess.run; creates the file each tool would write."""

    def __init__(self, returncode=0, stderr="", write_output=True, probe_stderr=PROBE_STDERR):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr
        self.write_output = write_output
        self.probe_stderr = probe_stderr

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if "-movflags" not in args and "-o" not in args:  # ffmpeg -i <src>: probe
            # probe_stderr may be a function of the probed file (per-file formats)
            stderr = self.probe_stderr(args[-1]) if callable(self.probe_stderr) else self.probe_stderr
            return subprocess.CompletedProcess(args, 1, "", stderr)
        if self.returncode == 0 and self.write_output:
            if "-o" in args:  # yt-dlp: template "<dir>/<key>.%(ext)s"
                Path(args[args.index("-o") + 1].replace("%(ext)s", "mp4")).write_bytes(b"video")
            else:  # ffmpeg: destination is the last argument
                Path(args[-1]).write_bytes(b"clip")
        return subprocess.CompletedProcess(args, self.returncode, "", self.stderr)

    def tool_calls(self, marker: str) -> list[list[str]]:
        return [c for c in self.calls if marker in c]


def inputs(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "-i"]


@pytest.fixture
def tools(tmp_path):
    return Tools(ytdlp=tmp_path / "yt-dlp.exe", ffmpeg=tmp_path / "ffmpeg.exe", encoder="h264_mf")


@pytest.fixture
def make_runner(tmp_path, tools):
    def make(run, clock=lambda: 1_000_000.0):
        return Runner(tools, cache_dir=tmp_path / "cache", run=run, clock=clock)

    return make
