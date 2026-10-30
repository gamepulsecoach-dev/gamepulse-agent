"""ffmpeg command that turns a Telestrator edit into a new MP4.

The browser renders the drawings as PNG frames and says how long each one stays
on screen (output clock); the agent holds the video still at every pause and
lays that image sequence over it. Loupes (magnified parts of the video) go under
the drawings; the camera zoom crops the result, so the drawings follow it.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gp_agent.ffmpeg_cmd import _SILENCE, AUDIO_RATE, Probe, _plan, _seconds, _trim, output_size

# Re-encoding an already encoded clip: a bit more bitrate than a first cut
EDIT_BITRATE = "8M"
MAX_VERSIONS = 999
DEFAULT_FPS = 25.0
MASK_FRAME_S = "0.04"  # one frame of the loupe mask, repeated by alphamerge
_COMMA = "\\,"  # comma inside a filter option expression

_EDITED_SUFFIX = re.compile(r"_editado_v\d+$")
_PROGRESS = re.compile(r"^out_time_(?:us|ms)=(\d+)\s*$")


@dataclass(frozen=True)
class Pause:
    at_ms: int  # source frame held, on the clip's own clock
    dur_ms: int


@dataclass(frozen=True)
class OverlaySegment:
    image: int  # index into the uploaded images
    dur_ms: int  # how long it stays, on the output clock


@dataclass(frozen=True)
class Rect:
    """Part of the video in uv (fractions of its width and height)."""

    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class ZoomCrop:
    at_ms: int  # output clock; the crop moves linearly to the next one
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Loupe:
    start_ms: int  # output clock
    end_ms: int
    src: Rect  # magnified part
    dst: Rect  # where it is drawn
    shape: str  # 'circle' | 'rect'


def output_duration_ms(duration_ms: int, pauses: Sequence[Pause]) -> int:
    return duration_ms + sum(p.dur_ms for p in pauses if p.at_ms <= duration_ms)


def _quoted(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "'\\''") + "'"


def overlay_list_text(segments: Sequence[OverlaySegment], images: Sequence[Path]) -> str:
    """ffconcat list showing each drawing image for its duration."""
    if not segments:
        raise ValueError("overlay without segments")
    lines = ["ffconcat version 1.0"]
    for seg in segments:
        if not 0 <= seg.image < len(images):
            raise ValueError("overlay image out of range")
        lines += [f"file {_quoted(images[seg.image])}", f"duration {_seconds(seg.dur_ms)}"]
    # The demuxer drops the last duration unless that file is listed once more
    lines.append(f"file {_quoted(images[segments[-1].image])}")
    return "\n".join(lines) + "\n"


def edit_graph(
    probe: Probe, pauses: Sequence[Pause], zoom: Sequence[ZoomCrop] = (), loupes: Sequence[Loupe] = ()
) -> str:
    """filter_complex: clip + held pauses, loupes, drawings, then the camera zoom."""
    if not probe.duration_ms:
        raise ValueError("clip duration unknown")
    duration_ms = probe.duration_ms
    w, h = output_size(probe)
    pieces = _plan(duration_ms, sorted(pauses, key=lambda p: p.at_ms))
    seg_count = sum(1 for p in pieces if p[0] == "seg")

    graph = [f"[0:v]scale={w}:{h},setsar=1,setpts=PTS-STARTPTS,split={len(pieces)}"
             + "".join(f"[x{i}]" for i in range(len(pieces)))]
    if probe.has_audio:
        graph.append(
            f"[0:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,"
            f"asplit={seg_count}" + "".join(f"[y{i}]" for i in range(seg_count))
        )

    outputs, seg_i = [], 0
    for i, (kind, start, end, pause) in enumerate(pieces):
        if kind == "seg":
            graph.append(f"[x{i}]{_trim('v', start, end)}[v{i}]")
            if probe.has_audio:
                graph.append(f"[y{seg_i}]{_trim('a', start, end)}[a{i}]")
            else:
                graph.append(f"{_SILENCE},atrim=duration={_seconds((end or duration_ms) - start)}[a{i}]")
            seg_i += 1
        else:
            hold = _seconds(pause.dur_ms)
            graph.append(
                f"[x{i}]trim=start={_seconds(start)},setpts=PTS-STARTPTS,trim=end_frame=1,"
                f"tpad=stop_mode=clone:stop_duration={hold},trim=duration={hold}[v{i}]"
            )
            graph.append(f"{_SILENCE},atrim=duration={hold}[a{i}]")
        outputs.append(f"[v{i}][a{i}]")

    graph.append("".join(outputs) + f"concat=n={len(pieces)}:v=1:a=1[bv][ca]")
    graph.append(f"[1:v]scale={w}:{h},format=rgba,setpts=PTS-STARTPTS[ol]")
    base = "bv"
    for i, loupe in enumerate(loupes):
        graph += _loupe_chain(i, base, loupe, w, h)
        base = f"lv{i}"
    if not zoom:
        graph.append(f"[{base}][ol]overlay=0:0:format=auto:eof_action=repeat,format=yuv420p[ov]")
        return ";".join(graph)
    fps = probe.fps if probe.fps and probe.fps > 0 else DEFAULT_FPS
    z, x, y = zoom_expressions(zoom)
    graph.append(f"[{base}][ol]overlay=0:0:format=auto:eof_action=repeat[od]")
    graph.append(f"[od]fps={fps:g},zoompan=z='1/({z})':x='iw*({x})':y='ih*({y})'"
                 f":d=1:s={w}x{h}:fps={fps:g},format=yuv420p[ov]")
    return ";".join(graph)


def _even(v: float, limit: int) -> int:
    return max(2, min(limit, round(v / 2) * 2))


def _px(r: Rect, w: int, h: int) -> tuple[int, int, int, int]:
    """Rect in even pixels (yuv420), kept inside the frame."""
    pw, ph = _even(r.w * w, w), _even(r.h * h, h)
    px = min(max(0, round(r.x * w / 2) * 2), w - pw)
    py = min(max(0, round(r.y * h / 2) * 2), h - ph)
    return px, py, pw, ph


def _loupe_chain(i: int, base: str, loupe: Loupe, w: int, h: int) -> list[str]:
    sx, sy, sw, sh = _px(loupe.src, w, h)
    dx, dy, dw, dh = _px(loupe.dst, w, h)
    lens = f"crop={sw}:{sh}:{sx}:{sy},scale={dw}:{dh}"
    chain = [f"[{base}]split=2[lb{i}][lsrc{i}]"]
    if loupe.shape == "circle":
        chain += [
            f"[lsrc{i}]{lens},format=yuva420p[lc{i}]",
            f"color=c=black:s={dw}x{dh}:d={MASK_FRAME_S},format=gray,"
            f"geq=lum='255*lte(hypot((X+0.5-W/2)/(W/2){_COMMA}(Y+0.5-H/2)/(H/2)){_COMMA}1)'[lm{i}]",
            f"[lc{i}][lm{i}]alphamerge[ll{i}]",
        ]
    else:
        chain.append(f"[lsrc{i}]{lens}[ll{i}]")
    chain.append(f"[lb{i}][ll{i}]overlay={dx}:{dy}:enable='between(t,{_seconds(loupe.start_ms)},{_seconds(loupe.end_ms)})'[lv{i}]")
    return chain


def _linear(times: Sequence[float], values: Sequence[float]) -> str:
    """Piecewise-linear value of `it` (seconds): the first value plus one ramp per change."""
    expr = f"{values[0]:.6f}"
    for i in range(len(values) - 1):
        dv = values[i + 1] - values[i]
        if abs(dv) < 1e-9:
            continue
        sign = "+" if dv > 0 else "-"
        expr += f"{sign}{abs(dv):.6f}*clip((it-{times[i]:.3f})/{times[i + 1] - times[i]:.3f}{_COMMA}0{_COMMA}1)"
    return expr


def zoom_expressions(crops: Sequence[ZoomCrop]) -> tuple[str, str, str]:
    """zoompan width (uv), left and top (uv) as expressions of the input time."""
    if not crops:
        raise ValueError("zoom without crops")
    times = [c.at_ms / 1000 for c in crops]
    return (_linear(times, [c.w for c in crops]), _linear(times, [c.x for c in crops]),
            _linear(times, [c.y for c in crops]))


def build_edit_args(
    ffmpeg: Path, src: Path, overlay_list: Path, dst: Path, encoder: str, probe: Probe, pauses: Sequence[Pause],
    graph_file: Path | None = None,
) -> list[str]:
    """With `graph_file` the graph is read from that file (written by the caller with edit_graph):
    zoom paths do not fit in a Windows command line."""
    if not probe.duration_ms:
        raise ValueError("clip duration unknown")
    graph = ["-/filter_complex", str(graph_file)] if graph_file else ["-filter_complex", edit_graph(probe, pauses)]
    return [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-progress", "pipe:1",
        "-i", str(src),
        "-f", "concat", "-safe", "0", "-i", str(overlay_list),
        *graph,
        "-map", "[ov]", "-map", "[ca]",
        "-c:v", encoder, "-b:v", EDIT_BITRATE,
        "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    ]


def parse_progress_ms(line: str) -> int | None:
    """Output position from one `-progress` line (both keys are in microseconds)."""
    m = _PROGRESS.match(line.strip())
    return int(m.group(1)) // 1000 if m else None


def edited_path(src: Path) -> Path:
    """<clip>_editado_vN.mp4 next to the clip, N being the first free version."""
    base = _EDITED_SUFFIX.sub("", src.stem)
    for n in range(1, MAX_VERSIONS + 1):
        dst = src.with_name(f"{base}_editado_v{n}{src.suffix}")
        if not dst.exists() and not dst.with_name(f"{dst.stem}.part{dst.suffix}").exists():
            return dst
    raise ValueError("demasiadas versões editadas deste clip")
