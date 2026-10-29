"""Runs the real ffmpeg on synthetic media; skipped when ffmpeg is not installed."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gp_agent.ffmpeg_cmd import FREEZE_MS, Freeze, pick_encoder
from gp_agent.runner import Runner, Tools

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(
    not FFMPEG or sys.platform not in ("win32", "darwin"), reason="needs ffmpeg + OS encoder"
)


def ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25:duration=12",
           "-f", "lavfi", "-i", "sine=duration=12:sample_rate=44100", "-c:v", "mpeg4", "-c:a", "aac", str(d / "sound.mp4"))
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25:duration=6", "-c:v", "mpeg4", str(d / "mute.mp4"))
    ffmpeg("-f", "lavfi", "-i", "color=c=red@0.5:s=1280x800,format=rgba", "-frames:v", "1", str(d / "draw.png"))
    return d


@pytest.fixture
def runner(tmp_path):
    return Runner(Tools(Path("unused"), Path(FFMPEG), pick_encoder(sys.platform)), tmp_path / "cache")


def test_freeze_clip_mute_clip_and_compilation(runner, media, tmp_path):
    sound, mute = runner.probe(media / "sound.mp4"), runner.probe(media / "mute.mp4")
    assert (sound.width, sound.height, sound.has_audio, sound.duration_ms) == (1920, 1080, True, 12_000)
    assert mute.has_audio is False
    assert sound.fps == 25.0

    frozen = tmp_path / "frozen.mp4"
    freeze = Freeze(at_ms=2_000, png=media / "draw.png", crop=(0, 40, 1280, 720))
    runner.cut(media / "sound.mp4", frozen, 3_000, 8_000, sound, (freeze,))
    silent = tmp_path / "silent.mp4"
    runner.cut(media / "mute.mp4", silent, 1_000, 4_000, mute)

    got_frozen, got_silent = runner.probe(frozen), runner.probe(silent)
    assert got_frozen.duration_ms == pytest.approx(5_000 + FREEZE_MS, abs=100)
    assert got_silent.has_audio is True  # silence added so every clip can be joined
    assert got_silent.duration_ms == pytest.approx(3_000, abs=100)

    joined = tmp_path / "out" / "all.mp4"
    runner.concat([frozen, silent], joined)
    got = runner.probe(joined)
    assert (got.width, got.height, got.has_audio) == (1920, 1080, True)
    assert got.duration_ms == pytest.approx(got_frozen.duration_ms + got_silent.duration_ms, abs=150)
