"""ffmpeg command that turns a Telestrator edit into a new MP4.

The browser renders the drawings as PNG frames and says how long each one stays
on screen (output clock); the agent holds the video still at every pause and
lays that image sequence over it.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gp_agent.ffmpeg_cmd import _SILENCE, AUDIO_RATE, Probe, _plan, _seconds, _trim, output_size

# Re-encoding an already encoded clip: a bit more bitrate than a first cut
EDIT_BITRATE = "8M"
MAX_VERSIONS = 999

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


def _edit_graph(duration_ms: int, probe: Probe, pauses: Sequence[Pause]) -> str:
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
    graph.append("[bv][ol]overlay=0:0:format=auto:eof_action=repeat,format=yuv420p[ov]")
    return ";".join(graph)


def build_edit_args(
    ffmpeg: Path, src: Path, overlay_list: Path, dst: Path, encoder: str, probe: Probe, pauses: Sequence[Pause]
) -> list[str]:
    if not probe.duration_ms:
        raise ValueError("clip duration unknown")
    return [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-progress", "pipe:1",
        "-i", str(src),
        "-f", "concat", "-safe", "0", "-i", str(overlay_list),
        "-filter_complex", _edit_graph(probe.duration_ms, probe, pauses),
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
