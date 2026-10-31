"""Clip window maths and the ffmpeg commands that cut, freeze-and-draw and join clips."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_HEIGHT = 1080
VIDEO_BITRATE = "6M"
FREEZE_MS = 3000
# A freeze needs at least one real frame after it to grab from
MIN_TAIL_MS = 100
AUDIO_RATE = 48_000

# LGPL ffmpeg build has no libx264, so use the OS hardware/system encoder.
_ENCODERS = {"win32": "h264_mf", "darwin": "h264_videotoolbox"}

_DURATION = re.compile(r"Duration: (\d+):(\d{2}):(\d{2})\.(\d+)")
_VIDEO = re.compile(r"Stream #\S+.*?: Video: .*?\b(\d{2,5})x(\d{2,5})\b.*")
_FPS = re.compile(r"\b(\d+(?:\.\d+)?) fps\b")
_AUDIO = re.compile(r"Stream #\S+.*?: Audio:")
_SILENCE = f"anullsrc=r={AUDIO_RATE}:cl=stereo"


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    has_audio: bool
    duration_ms: int | None
    fps: float | None = None


@dataclass(frozen=True)
class Freeze:
    at_ms: int  # relative to the clip start
    png: Path
    crop: tuple[int, int, int, int]  # x, y, w, h of the video area inside the png


def clip_window(
    timestamp_ms: int, pre_ms: int, post_ms: int, duration_ms: int | None = None
) -> tuple[int, int]:
    if pre_ms < 0 or post_ms < 0:
        raise ValueError("pre/post must be >= 0")
    start = max(0, timestamp_ms - pre_ms)
    end = timestamp_ms + post_ms
    if duration_ms is not None:
        end = min(end, duration_ms)
    if end <= start:
        raise ValueError("empty clip window")
    return start, end


def pick_encoder(platform: str) -> str:
    try:
        return _ENCODERS[platform]
    except KeyError:
        raise ValueError(f"unsupported platform: {platform}") from None


def parse_probe(stderr: str) -> Probe:
    """Read `ffmpeg -i <file>` output (ffprobe is not shipped)."""
    video = _VIDEO.search(stderr)
    if not video:
        raise ValueError("no video stream")
    duration = _DURATION.search(stderr)
    duration_ms = None
    if duration:
        h, m, s, frac = duration.groups()
        duration_ms = ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])
    fps = _FPS.search(video.group(0))
    return Probe(
        int(video.group(1)), int(video.group(2)), bool(_AUDIO.search(stderr)), duration_ms,
        fps=float(fps.group(1)) if fps else None,
    )


def output_size(probe: Probe) -> tuple[int, int]:
    height = min(MAX_HEIGHT, probe.height) // 2 * 2
    width = max(2, round(probe.width * height / probe.height / 2) * 2)
    return width, height


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _plan(duration_ms: int, freezes: Sequence[Freeze]) -> list[tuple[str, int, int | None, Freeze | None]]:
    """Ordered pieces: ("seg", start, end|None, None) and ("freeze", at, None, freeze)."""
    pieces: list[tuple[str, int, int | None, Freeze | None]] = []
    cursor = 0
    for f in freezes:
        at = min(f.at_ms, duration_ms - MIN_TAIL_MS)
        if at > cursor:
            pieces.append(("seg", cursor, at, None))
        pieces.append(("freeze", at, None, f))
        cursor = max(cursor, at)
    pieces.append(("seg", cursor, None, None))
    return pieces


def _trim(kind: str, start: int, end: int | None) -> str:
    end_part = f":end={_seconds(end)}" if end is not None else ""
    pts = "setpts" if kind == "v" else "asetpts"
    name = "trim" if kind == "v" else "atrim"
    return f"{name}=start={_seconds(start)}{end_part},{pts}=PTS-STARTPTS"


def _filter_graph(duration_ms: int, probe: Probe, freezes: Sequence[Freeze]) -> str:
    w, h = output_size(probe)
    pieces = _plan(duration_ms, freezes)
    seg_count = sum(1 for p in pieces if p[0] == "seg")
    freeze_s = _seconds(FREEZE_MS)

    vsrc = [f"x{i}" for i in range(len(pieces))]
    graph = [f"[0:v]scale={w}:{h},setsar=1,setpts=PTS-STARTPTS,split={len(pieces)}" + "".join(f"[{s}]" for s in vsrc)]
    if probe.has_audio:
        graph.append(
            f"[0:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,"
            f"asplit={seg_count}" + "".join(f"[y{i}]" for i in range(seg_count))
        )

    outputs, seg_i, png_input = [], 0, 1
    for i, (kind, start, end, freeze) in enumerate(pieces):
        if kind == "seg":
            graph.append(f"[{vsrc[i]}]{_trim('v', start, end)}[v{i}]")
            if probe.has_audio:
                graph.append(f"[y{seg_i}]{_trim('a', start, end)}[a{i}]")
            else:
                graph.append(f"{_SILENCE},atrim=duration={_seconds((end or duration_ms) - start)}[a{i}]")
            seg_i += 1
        else:
            x, y, cw, ch = freeze.crop
            graph.append(
                f"[{vsrc[i]}]trim=start={_seconds(start)},setpts=PTS-STARTPTS,trim=end_frame=1,"
                f"tpad=stop_mode=clone:stop_duration={freeze_s},trim=duration={freeze_s}[z{i}]"
            )
            graph.append(f"[{png_input}:v]crop={cw}:{ch}:{x}:{y},scale={w}:{h},format=rgba[p{i}]")
            graph.append(f"[z{i}][p{i}]overlay=0:0:format=auto[v{i}]")
            graph.append(f"{_SILENCE},atrim=duration={freeze_s}[a{i}]")
            png_input += 1
        outputs.append(f"[v{i}][a{i}]")

    graph.append("".join(outputs) + f"concat=n={len(pieces)}:v=1:a=1[cv][ca]")
    graph.append("[cv]format=yuv420p[ov]")
    return ";".join(graph)


def build_clip_args(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    start_ms: int,
    end_ms: int,
    encoder: str,
    probe: Probe,
    freezes: Iterable[Freeze] = (),
) -> list[str]:
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("invalid clip range")
    duration = end_ms - start_ms
    ordered = sorted(freezes, key=lambda f: f.at_ms)
    if any(not 0 <= f.at_ms <= duration for f in ordered):
        raise ValueError("freeze outside the clip")

    args = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        # Input options: -t after the inputs would cap the output and cut off the freezes
        "-ss", _seconds(start_ms), "-t", _seconds(duration), "-i", str(src),
    ]
    for f in ordered:
        args += ["-i", str(f.png)]
    return args + [
        "-filter_complex", _filter_graph(duration, probe, ordered),
        "-map", "[ov]", "-map", "[ca]",
        "-c:v", encoder, "-b:v", VIDEO_BITRATE,
        # Same audio format everywhere so clips can be joined with stream copy
        "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    ]


def concat_list_text(parts: Iterable[Path]) -> str:
    lines = []
    for p in parts:
        escaped = p.as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


DEFAULT_JOIN_FPS = 25.0


def same_format(probes: Sequence[Probe]) -> bool:
    """Stream copy only joins clips cleanly when size and frame rate match."""
    first = probes[0]
    return all((p.width, p.height, p.fps) == (first.width, first.height, first.fps) for p in probes)


def build_concat_reencode_args(
    ffmpeg: Path, parts: Sequence[Path], dst: Path, encoder: str, target: Probe
) -> list[str]:
    """Join clips from different videos (e.g. games at 60 and 29.97 fps) re-encoded to the first clip's format."""
    w, h = target.width, target.height
    fps = f"{target.fps if target.fps and target.fps > 0 else DEFAULT_JOIN_FPS:g}"
    args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    graph, outputs = [], []
    for i, part in enumerate(parts):
        args += ["-i", str(part)]
        graph.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},format=yuv420p,setpts=PTS-STARTPTS[v{i}]"
        )
        # Every cut clip has audio (silence is added when the source has none)
        graph.append(f"[{i}:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")
        outputs.append(f"[v{i}][a{i}]")
    graph.append("".join(outputs) + f"concat=n={len(parts)}:v=1:a=1[cv][ca]")
    return args + [
        "-filter_complex", ";".join(graph),
        "-map", "[cv]", "-map", "[ca]",
        "-c:v", encoder, "-b:v", VIDEO_BITRATE,
        "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    ]


def build_concat_args(ffmpeg: Path, list_file: Path, dst: Path) -> list[str]:
    return [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart",
        str(dst),
    ]
