"""Runs yt-dlp (full video into the cache) and ffmpeg (probe, cut, join)."""

import hashlib
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gp_agent.cache import CacheEntry, plan_eviction
from gp_agent.ffmpeg_cmd import (
    MAX_HEIGHT,
    Freeze,
    Probe,
    build_clip_args,
    build_concat_args,
    concat_list_text,
    parse_probe,
)

DOWNLOAD_TIMEOUT_S = 2 * 3600
CUT_TIMEOUT_S = 10 * 60
PROBE_TIMEOUT_S = 60
STDERR_TAIL = 400

# Keep the agent's child processes from flashing console windows on Windows
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class RunnerError(Exception):
    """A tool failed; the message is safe to show to the user."""


@dataclass(frozen=True)
class Tools:
    ytdlp: Path
    ffmpeg: Path
    encoder: str


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def build_download_args(ytdlp: Path, ffmpeg: Path, url: str, dst_stem: Path) -> list[str]:
    return [
        str(ytdlp),
        "--no-playlist",
        "--no-progress",
        "-f", f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/b",
        "--merge-output-format", "mp4",
        "--remux-video", "mp4",
        "--ffmpeg-location", str(ffmpeg),
        "-o", f"{dst_stem}.%(ext)s",
        "--",
        url,
    ]


Run = Callable[..., subprocess.CompletedProcess]


def _part_path(dst: Path) -> Path:
    return dst.with_name(f"{dst.stem}.part{dst.suffix}")


class Runner:
    def __init__(
        self,
        tools: Tools,
        cache_dir: Path,
        run: Run = subprocess.run,
        clock: Callable[[], float] = time.time,
    ):
        self.tools = tools
        self.cache_dir = cache_dir
        self._run = run
        self._clock = clock

    def _call(self, args: list[str], timeout: int, what: str) -> subprocess.CompletedProcess:
        try:
            return self._run(
                args, capture_output=True, text=True, timeout=timeout,
                creationflags=_NO_WINDOW, check=False,
            )
        except subprocess.TimeoutExpired:
            raise RunnerError(f"{what}: excedeu o tempo limite") from None
        except OSError as exc:
            raise RunnerError(f"{what}: não foi possível executar ({exc.__class__.__name__})") from None

    def _exec(self, args: list[str], timeout: int, what: str) -> None:
        result = self._call(args, timeout, what)
        if result.returncode != 0:
            tail = (result.stderr or "").strip()[-STDERR_TAIL:]
            raise RunnerError(f"{what} falhou: {tail}" if tail else f"{what} falhou")

    def _write_atomically(self, dst: Path, args_for: Callable[[Path], list[str]], timeout: int, what: str) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = _part_path(dst)
        try:
            self._exec(args_for(tmp), timeout, what)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)

    def ensure_video(self, url: str) -> Path:
        """Full video in the cache (downloaded once, then reused), marked as just used."""
        stem = self.cache_dir / cache_key(url)
        path = stem.with_suffix(".mp4")
        if not path.is_file():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._exec(build_download_args(self.tools.ytdlp, self.tools.ffmpeg, url, stem),
                       DOWNLOAD_TIMEOUT_S, "Download do vídeo")
            if not path.is_file():
                raise RunnerError("Download do vídeo terminou sem ficheiro mp4")
        now = self._clock()
        os.utime(path, (now, now))
        return path

    def probe(self, src: Path) -> Probe:
        # `ffmpeg -i` without an output always exits non-zero; the info is on stderr
        result = self._call([str(self.tools.ffmpeg), "-hide_banner", "-i", str(src)], PROBE_TIMEOUT_S, "Leitura do vídeo")
        try:
            return parse_probe(result.stderr or "")
        except ValueError:
            raise RunnerError("Não foi possível ler o vídeo descarregado") from None

    def cut(
        self, src: Path, dst: Path, start_ms: int, end_ms: int, probe: Probe, freezes: Iterable[Freeze] = ()
    ) -> None:
        frozen = tuple(freezes)
        self._write_atomically(
            dst,
            lambda tmp: build_clip_args(self.tools.ffmpeg, src, tmp, start_ms, end_ms, self.tools.encoder, probe, frozen),
            CUT_TIMEOUT_S, "Corte do clip",
        )

    def concat(self, parts: Sequence[Path], dst: Path) -> None:
        if not parts:
            raise ValueError("nothing to join")
        dst.parent.mkdir(parents=True, exist_ok=True)
        list_file = dst.with_name(f"{dst.stem}.list.txt")
        list_file.write_text(concat_list_text(parts), encoding="utf-8")
        try:
            self._write_atomically(
                dst, lambda tmp: build_concat_args(self.tools.ffmpeg, list_file, tmp), CUT_TIMEOUT_S, "Compilação"
            )
        finally:
            list_file.unlink(missing_ok=True)

    def evict(self, in_use: frozenset[Path] = frozenset()) -> tuple[Path, ...]:
        if not self.cache_dir.is_dir():
            return ()
        entries = []
        for p in self.cache_dir.glob("*.mp4"):
            st = p.stat()
            entries.append(CacheEntry(path=p, size_bytes=st.st_size, last_used=st.st_mtime))
        victims = plan_eviction(entries, now=self._clock(), in_use=in_use)
        for p in victims:
            p.unlink(missing_ok=True)
        return victims
