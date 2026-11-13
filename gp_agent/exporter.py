"""What one job produces: a single clip, several clips joined into one MP4, or an edited clip."""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from gp_agent.edit_cmd import Loupe, OverlaySegment, Pause, ZoomCrop, edited_path, overlay_list_text
from gp_agent.ffmpeg_cmd import Freeze, Probe, clip_window
from gp_agent.naming import clip_path, compilation_path
from gp_agent.overlay import png_size, video_rect_in_canvas
from gp_agent.runner import Cancelled, Progress, Runner
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
    team: str = ""
    folder: str = ""  # dossier: one folder for clips of several games


@dataclass(frozen=True)
class CompileRequest:
    game: str
    title: str
    clips: tuple[ClipRequest, ...]
    team: str = ""


@dataclass(frozen=True)
class EditRequest:
    src: Path  # clip inside the export folder, already resolved
    pauses: tuple[Pause, ...]
    segments: tuple[OverlaySegment, ...]  # drawing images on the output clock, in order
    images: tuple[bytes, ...]  # PNGs of the video area, all the same size
    zoom: tuple[ZoomCrop, ...] = ()  # camera crops on the output clock
    loupes: tuple[Loupe, ...] = ()


class _NoProgress:
    cancelled = False

    def report(self, fraction: float) -> None:
        pass


# Share of a compilation's progress bar: download the videos, cut each clip, join them
COMPILE_DOWNLOAD_SHARE = 0.15
COMPILE_CUT_SHARE = 0.60
COMPILE_JOIN_SHARE = 1.0 - COMPILE_DOWNLOAD_SHARE - COMPILE_CUT_SHARE


class _Span:
    """Maps a step's own 0..1 progress onto its slice [start, start + size] of the whole job."""

    def __init__(self, ctx: Progress, start: float, size: float):
        self._ctx, self._start, self._size = ctx, start, size

    @property
    def cancelled(self) -> bool:
        return self._ctx.cancelled

    def report(self, fraction: float) -> None:
        self._ctx.report(self._start + self._size * max(0.0, min(1.0, fraction)))


def _check_cancel(ctx: Progress) -> None:
    if ctx.cancelled:
        raise Cancelled()


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
    dst = clip_path(export_dir, req.game, req.category, req.index, req.timestamp_ms, req.team, req.folder)
    src = runner.ensure_video(req.video_url)
    runner.evict(in_use=frozenset({src}))
    with tempfile.TemporaryDirectory(prefix="gp_clip_") as tmp:
        _render(req, src, runner, dst, Path(tmp))
    return dst


def _download_sources(req: CompileRequest, runner: Runner, ctx: Progress) -> dict[str, Path]:
    urls = list(dict.fromkeys(c.video_url for c in req.clips))
    sources = {}
    for n, url in enumerate(urls, start=1):
        _check_cancel(ctx)
        sources[url] = runner.ensure_video(url)
        ctx.report(COMPILE_DOWNLOAD_SHARE * n / len(urls))
    return sources


def export_compilation(
    req: CompileRequest, runner: Runner, export_dir: Path, ctx: Progress | None = None
) -> Path:
    """Downloads, cuts each clip and joins them, reporting 0..1 on `ctx` and stopping on cancel."""
    if not req.clips:
        raise ValueError("compilation without clips")
    for clip in req.clips:
        _validate(clip)
    ctx = ctx or _NoProgress()
    dst = compilation_path(export_dir, req.game, req.title, req.team)
    sources = _download_sources(req, runner, ctx)
    runner.evict(in_use=frozenset(sources.values()))
    with tempfile.TemporaryDirectory(prefix="gp_compile_") as tmp:
        workdir = Path(tmp)
        parts = []
        for i, clip in enumerate(req.clips):
            _check_cancel(ctx)
            clip_dir = workdir / f"clip_{i:03d}"
            clip_dir.mkdir()
            part = workdir / f"part_{i:03d}.mp4"
            _render(clip, sources[clip.video_url], runner, part, clip_dir)
            parts.append(part)
            ctx.report(COMPILE_DOWNLOAD_SHARE + COMPILE_CUT_SHARE * (i + 1) / len(req.clips))
        _check_cancel(ctx)
        runner.concat(parts, dst, _Span(ctx, COMPILE_DOWNLOAD_SHARE + COMPILE_CUT_SHARE, COMPILE_JOIN_SHARE))
    return dst


def export_edit(req: EditRequest, runner: Runner, ctx: Progress) -> Path:
    """<clip>_editado_vN.mp4 next to the clip; the original is never touched."""
    if len({png_size(img) for img in req.images}) != 1:
        raise ValueError("Imagens do desenho com tamanhos diferentes")
    probe = runner.probe(req.src)
    dst = edited_path(req.src)
    with tempfile.TemporaryDirectory(prefix="gp_edit_") as tmp:
        workdir = Path(tmp)
        pngs = []
        for i, img in enumerate(req.images):
            png = workdir / f"overlay_{i:03d}.png"
            png.write_bytes(img)
            pngs.append(png)
        listing = workdir / "overlay.txt"
        listing.write_text(overlay_list_text(req.segments, pngs), encoding="utf-8")
        runner.edit(req.src, dst, listing, probe, req.pauses, ctx, zoom=req.zoom, loupes=req.loupes)
    return dst


def export(
    req: ClipRequest | CompileRequest | EditRequest, runner: Runner, export_dir: Path, ctx: Progress | None = None
) -> Path:
    if isinstance(req, EditRequest):
        return export_edit(req, runner, ctx or _NoProgress())
    if isinstance(req, CompileRequest):
        return export_compilation(req, runner, export_dir, ctx)
    return export_clip(req, runner, export_dir)
