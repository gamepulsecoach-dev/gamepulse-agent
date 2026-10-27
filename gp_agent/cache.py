"""Eviction policy for the full-video cache (7 days + disk limit)."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DAY_S = 86_400
GB = 1024**3
DEFAULT_MAX_AGE_S = 7 * DAY_S
DEFAULT_MAX_BYTES = 20 * GB


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    size_bytes: int
    last_used: float  # epoch seconds


def plan_eviction(
    entries: Iterable[CacheEntry],
    now: float,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    in_use: frozenset[Path] = frozenset(),
) -> tuple[Path, ...]:
    """Paths to delete: expired first, then least recently used until under max_bytes."""
    all_entries = tuple(entries)
    candidates = sorted((e for e in all_entries if e.path not in in_use), key=lambda e: e.last_used)

    expired = tuple(e for e in candidates if now - e.last_used > max_age_s)
    total = sum(e.size_bytes for e in all_entries) - sum(e.size_bytes for e in expired)

    lru: list[CacheEntry] = []
    for e in candidates:
        if total <= max_bytes:
            break
        if e in expired:
            continue
        lru.append(e)
        total -= e.size_bytes

    return tuple(e.path for e in expired) + tuple(e.path for e in lru)
