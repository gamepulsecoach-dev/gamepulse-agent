"""Runs the real ffmpeg on synthetic media; skipped when ffmpeg is not installed."""

import re
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
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60:duration=6",
           "-f", "lavfi", "-i", "sine=duration=6:sample_rate=48000", "-c:v", "mpeg4", "-c:a", "aac", str(d / "fast.mp4"))
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


class _Ctx:
    cancelled = False

    def __init__(self):
        self.reports: list[float] = []

    def report(self, fraction: float) -> None:
        self.reports.append(fraction)


def _pixel(video: Path, at_s: float, crop: str = "crop=8:8:0:0") -> tuple[int, int, int]:
    """RGB of the top-left pixel at `at_s` in the video (or of the given 8x8 crop)."""
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.3f}", "-i", str(video),
         "-vf", f"{crop},scale=1:1", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return out[0], out[1], out[2]


def test_edit_holds_pauses_and_lays_drawing_frames(runner, media, tmp_path):
    from gp_agent.edit_cmd import OverlaySegment, Pause
    from gp_agent.exporter import EditRequest, export

    clip = tmp_path / "Jogo" / "clip.mp4"
    clip.parent.mkdir()
    runner.cut(media / "sound.mp4", clip, 0, 4_000, runner.probe(media / "sound.mp4"))
    d = tmp_path / "png"
    d.mkdir()
    ffmpeg("-f", "lavfi", "-i", "color=c=black@0.0:s=640x360,format=rgba", "-frames:v", "1", str(d / "clear.png"))
    ffmpeg("-f", "lavfi", "-i", "color=c=0x00ff00:s=640x360,format=rgba", "-frames:v", "1", str(d / "green.png"))
    clear, green = (d / "clear.png").read_bytes(), (d / "green.png").read_bytes()

    ctx = _Ctx()
    req = EditRequest(
        src=clip, pauses=(Pause(1_000, 2_000),),
        # output clock: clear 0-2 s, green 2-4 s (inside/after the pause), clear to the end
        segments=(OverlaySegment(0, 2_000), OverlaySegment(1, 2_000), OverlaySegment(0, 2_000)),
        images=(clear, green),
    )
    out = export(req, runner, tmp_path, ctx)

    assert out.name == "clip_editado_v1.mp4"
    got = runner.probe(out)
    assert (got.width, got.height, got.has_audio) == (1920, 1080, True)
    assert got.duration_ms == pytest.approx(6_000, abs=150)
    assert ctx.reports and ctx.reports[-1] == pytest.approx(1.0, abs=0.05)
    r, g, b = _pixel(out, 3.0)
    assert g > 200 and r < 60 and b < 60  # drawing over the frozen frame
    assert _pixel(out, 5.0)[1] < 200 or _pixel(out, 5.0)[0] > 60  # drawing gone afterwards
    assert clip.read_bytes()  # original untouched


def test_edit_zoom_follows_the_drawings_and_loupe_renders(runner, media, tmp_path):
    from gp_agent.edit_cmd import Loupe, OverlaySegment, Rect, ZoomCrop
    from gp_agent.exporter import EditRequest, export

    clip = tmp_path / "Jogo" / "clip.mp4"
    clip.parent.mkdir()
    runner.cut(media / "sound.mp4", clip, 0, 3_000, runner.probe(media / "sound.mp4"))
    png = tmp_path / "corner.png"
    # green drawing only on the top-left quarter
    ffmpeg("-f", "lavfi", "-i", "color=c=0x00ff00:s=320x180,format=rgba,pad=640:360:0:0:color=black@0.0",
           "-frames:v", "1", str(png))

    req = EditRequest(
        src=clip, pauses=(), segments=(OverlaySegment(0, 3_000),), images=(png.read_bytes(),),
        # full frame until 0.5 s, 2x on the top-left quarter from 1 s
        zoom=(ZoomCrop(500, 0, 0, 1, 1), ZoomCrop(1_000, 0, 0, 0.5, 0.5)),
        loupes=(Loupe(0, 3_000, Rect(0.6, 0.6, 0.1, 0.1), Rect(0.7, 0.7, 0.2, 0.2), "circle"),),
    )
    out = export(req, runner, tmp_path, _Ctx())

    got = runner.probe(out)
    assert (got.width, got.height) == (1920, 1080)
    assert got.duration_ms == pytest.approx(3_000, abs=150)
    def centre(at_s):
        return _pixel(out, at_s, crop="crop=8:8:956:536")

    def green(rgb):
        return rgb[1] > 200 and rgb[0] < 60 and rgb[2] < 60

    assert green(centre(2.0))  # zoomed: the green quarter fills the frame, drawing included
    assert not green(centre(0.2))  # full frame: the centre is video
    assert clip.read_bytes()


def test_compilation_of_games_with_different_fps_has_no_frozen_gap(runner, media, tmp_path):
    # Real case: one game at 60 fps, another at 25/29.97 fps -> stream copy froze the picture
    fast, slow = tmp_path / "fast.mp4", tmp_path / "slow.mp4"
    runner.cut(media / "fast.mp4", fast, 1_000, 4_000, runner.probe(media / "fast.mp4"))
    runner.cut(media / "sound.mp4", slow, 1_000, 4_000, runner.probe(media / "sound.mp4"))
    joined = tmp_path / "out" / "mixed.mp4"
    runner.concat([fast, slow], joined)
    got = runner.probe(joined)
    assert got.fps == 60.0
    assert got.duration_ms == pytest.approx(6_000, abs=200)
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(joined), "-map", "0:v", "-f", "null", "-"],
                         capture_output=True, text=True)
    frames = int(re.findall(r"frame=\s*(\d+)", out.stderr)[-1])
    assert frames == pytest.approx(6 * 60, abs=6)  # real pictures for the whole duration
