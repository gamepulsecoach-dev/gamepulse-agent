"""Short-lived links that let a <video> read an exported clip (it can't send X-GP-Token)."""

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

TICKET_TTL_S = 2 * 3600
MAX_TICKETS = 100
ALLOWED_SUFFIXES = frozenset({".mp4"})


def resolve_export_file(base_dir: Path, relative: str) -> Path:
    """Absolute path of an exported clip, refusing anything outside base_dir."""
    win = PureWindowsPath(relative)
    if not relative or win.is_absolute() or win.drive or PurePosixPath(relative).is_absolute():
        raise ValueError("path must be relative to the export folder")
    if ".." in win.parts:
        raise ValueError("path escapes the export folder")
    path = base_dir.joinpath(*win.parts).resolve()
    if not path.is_relative_to(base_dir.resolve()):
        raise ValueError("path escapes the export folder")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("only mp4 clips can be opened")
    if not path.is_file():
        raise FileNotFoundError(relative)
    return path


class FileTickets:
    def __init__(self, base_dir: Path, clock: Callable[[], float] = time.time):
        self._base = base_dir
        self._clock = clock
        self._lock = threading.Lock()
        self._tickets: OrderedDict[str, tuple[Path, float]] = OrderedDict()

    def issue(self, relative: str) -> str:
        path = resolve_export_file(self._base, relative)
        ticket = secrets.token_urlsafe(24)
        with self._lock:
            self._tickets[ticket] = (path, self._clock() + TICKET_TTL_S)
            while len(self._tickets) > MAX_TICKETS:
                self._tickets.popitem(last=False)
        return ticket

    def resolve(self, ticket: str) -> Path | None:
        with self._lock:
            entry = self._tickets.get(ticket)
            if entry is None:
                return None
            path, expires = entry
            if self._clock() > expires:
                del self._tickets[ticket]
                return None
        return path
