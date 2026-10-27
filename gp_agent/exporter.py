"""What one job produces: a single clip, or several clips joined into one MP4."""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from gp_agent.ffmpeg_cmd import Freeze, Probe, clip_window
from gp_agent.naming import clip_path, compilation_path
from gp_agent.overlay import png_size, video_rect_in_canvas
from gp_agent.runner import Runner
from gp_agent.sources import classify_source


@dataclass(frozen=True)
class Overlay:
    timestamp_ms: int  # position in the full video
    png: bytes  # Telestrator drawing, rendered at the canvas size


@dataclass(frozen=True)
class ClipRequest:
    video_url: str
    timestamp_ms: int
    pre_ms: int
    post_ms: int
    game: str
    category: str
    index: int
    overlays: tuple[Overlay, ...] = ()


@dataclass(frozen=True)
class CompileRequest:
    game: str
    title: str
    clips: tuple[ClipRequest, ...]


def _validate(clip: ClipRequest) -> None:
    classify_source(clip.video_url)
    for ov in clip.overlays:
        png_size(ov.png)


def _freezes(overlays: tuple[Overlay, ...], start_ms: int, end_ms: int, probe: Probe, workdir: Path) -> tuple[Freeze, ...]:
    freezes = []
    for i, ov in enumerate(overlays):
        if not start_ms <= ov.timestamp_ms <= end_ms:
            continue  # drawing belongs to another moment of the game
        canvas_w, canvas_h = png_size(ov.png)
        png = workdir / f"overlay_{i}.png"
        png.write_bytes(ov.png)
        crop = video_rect_in_canvas(canvas_w, canvas_h, probe.width, probe.height)
        freezes.append(Freeze(at_ms=ov.timestamp_ms - start_ms, png=png, crop=crop))
    return tuple(freezes)


def _render(req: ClipRequest, src: Path, runner: Runner, dst: Path, workdir: Path) -> None:
    probe = runner.probe(src)
    start_ms, end_ms = clip_window(req.timestamp_ms, req.pre_ms, req.post_ms, probe.duration_ms)
    runner.cut(src, dst, start_ms, end_ms, probe, _freezes(req.overlays, start_ms, end_ms, probe, workdir))


def export_clip(req: ClipRequest, runner: Runner, export_dir: Path) -> Path:
    _validate(req)
    dst = clip_path(export_dir, req.game, req.category, req.index, req.timestamp_ms)
    src = runner.ensure_video(req.video_url)
    runner.evict(in_use=frozenset({src}))
    with tempfile.TemporaryDirectory(prefix="gp_clip_") as tmp:
        _render(req, src, runner, dst, Path(tmp))
    return dst


def export_compilation(req: CompileRequest, runner: Runner, export_dir: Path) -> Path:
    if not req.clips:
        raise ValueError("compilation without clips")
    for clip in req.clips:
        _validate(clip)
    dst = compilation_path(export_dir, req.game, req.title)
    sources = {url: runner.ensure_video(url) for url in dict.fromkeys(c.video_url for c in req.clips)}
    runner.evict(in_use=frozenset(sources.values()))
    with tempfile.TemporaryDirectory(prefix="gp_compile_") as tmp:
        workdir = Path(tmp)
        parts = []
        for i, clip in enumerate(req.clips):
            clip_dir = workdir / f"clip_{i:03d}"
            clip_dir.mkdir()
            part = workdir / f"part_{i:03d}.mp4"
            _render(clip, sources[clip.video_url], runner, part, clip_dir)
            parts.append(part)
        runner.concat(parts, dst)
    return dst


def export(req: ClipRequest | CompileRequest, runner: Runner, export_dir: Path) -> Path:
    if isinstance(req, CompileRequest):
        return export_compilation(req, runner, export_dir)
    return export_clip(req, runner, export_dir)
