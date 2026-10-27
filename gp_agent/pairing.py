"""Pairing a browser with the agent: one-time 6-digit code -> long-lived token."""

import hmac
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path

from gp_agent.security import new_token

CODE_TTL_S = 300
MAX_ATTEMPTS = 5
CODE_DIGITS = 6


class PairingLocked(Exception):
    """Too many wrong codes; a new code must be requested."""


class Pairing:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires_at = 0.0
        self._failures = 0

    def start(self) -> str:
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        with self._lock:
            self._code = code
            self._expires_at = self._clock() + CODE_TTL_S
            self._failures = 0
        return code

    def confirm(self, code: str | None) -> bool:
        """True once for the right code; raises PairingLocked after MAX_ATTEMPTS failures."""
        with self._lock:
            if self._failures >= MAX_ATTEMPTS:
                raise PairingLocked()
            if self._code is None or self._clock() > self._expires_at:
                self._code = None
                return False
            if isinstance(code, str) and hmac.compare_digest(self._code.encode(), code.encode()):
                self._code = None
                return True
            self._failures += 1
            return False


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> str | None:
        try:
            token = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return token or None

    def load_or_create(self) -> str:
        token = self.load()
        if token:
            return token
        token = new_token()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token, encoding="utf-8")
        return token
