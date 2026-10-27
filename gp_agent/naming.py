"""Folder and file names for exported clips.

Layout: <base>/<Jogo>/<Categoria>/<Jogo>_<Categoria>_NN_MMmSSs.mp4
"""

import re
from pathlib import Path

MAX_SEGMENT_LEN = 60
PLACEHOLDER = "Sem_nome"

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.IGNORECASE)


def sanitize_segment(text: str | None) -> str:
    """Make one path segment safe on Windows and macOS (no separators, no '..')."""
    if not text:
        return PLACEHOLDER
    cleaned = _WHITESPACE.sub(" ", text)
    cleaned = _INVALID_CHARS.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned.replace("..", "")
    cleaned = cleaned[:MAX_SEGMENT_LEN].rstrip(". ")
    if not cleaned:
        return PLACEHOLDER
    if _RESERVED.match(cleaned):
        return "_" + cleaned
    return cleaned


def format_timestamp(ms: int) -> str:
    if ms < 0:
        raise ValueError("timestamp must be >= 0")
    total_s = ms // 1000
    return f"{total_s // 60:02d}m{total_s % 60:02d}s"


def _file_part(text: str) -> str:
    # '_' separates fields in the file name, so spaces inside a field become '-'
    return sanitize_segment(text).replace(" ", "-")


def clip_filename(game: str, category: str, index: int, timestamp_ms: int) -> str:
    if index < 1:
        raise ValueError("index starts at 1")
    return f"{_file_part(game)}_{_file_part(category)}_{index:02d}_{format_timestamp(timestamp_ms)}.mp4"


def _inside(base_dir: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(base_dir.resolve()):
        raise ValueError("clip path escapes the base directory")
    return path


def clip_path(base_dir: Path, game: str, category: str, index: int, timestamp_ms: int) -> Path:
    return _inside(base_dir, base_dir / sanitize_segment(game) / sanitize_segment(category) / clip_filename(
        game, category, index, timestamp_ms
    ))


def compilation_path(base_dir: Path, game: str, title: str) -> Path:
    """<base>/<Jogo>/<Jogo>_Compilado_<Titulo>.mp4"""
    name = f"{_file_part(game)}_Compilado_{_file_part(title)}.mp4"
    return _inside(base_dir, base_dir / sanitize_segment(game) / name)
