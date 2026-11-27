"""Keeps yt-dlp current without a new Studio release.

YouTube changes often break older yt-dlp builds. The bundled binary is copied to a
user-writable folder (never touching the signed app bundle) and that copy updates
itself with `yt-dlp -U` at start-up and once a day.
"""

import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

UPDATE_TIMEOUT_S = 180
UPDATE_INTERVAL_S = 24 * 3600
_STAMP = "bundled.stamp"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _bundle_stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


class YtDlpManager:
    def __init__(
        self,
        bundled: Path,
        dest_dir: Path,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        lock: threading.Lock | None = None,
    ):
        self.bundled = bundled
        self.dest_dir = dest_dir
        self.lock = lock or threading.Lock()
        self._run = run
        self._path: Path | None = None

    def prepare(self) -> Path:
        """Path to the yt-dlp to use: the user copy, re-copied when the Studio ships a new one."""
        dst = self.dest_dir / self.bundled.name
        stamp_file = self.dest_dir / _STAMP
        try:
            stamp = _bundle_stamp(self.bundled)
            if not dst.is_file() or not stamp_file.is_file() or stamp_file.read_text().strip() != stamp:
                self.dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.bundled, dst)
                stamp_file.write_text(stamp)
            self._path = dst
        except OSError:
            log.warning("Não foi possível copiar o yt-dlp; uso o do instalador (sem actualização)", exc_info=True)
            self._path = self.bundled
        return self._path

    def update(self) -> bool:
        """Runs `yt-dlp -U` on the user copy; never raises."""
        if self._path is None or self._path == self.bundled:
            return False
        with self.lock:
            try:
                result = self._run(
                    [str(self._path), "-U"], capture_output=True, text=True,
                    timeout=UPDATE_TIMEOUT_S, creationflags=_NO_WINDOW, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                log.warning("Actualização do yt-dlp falhou", exc_info=True)
                return False
        if result.returncode != 0:
            log.warning("Actualização do yt-dlp falhou: %s", (result.stderr or "").strip()[-300:])
            return False
        log.info("yt-dlp: %s", (result.stdout or "").strip().splitlines()[-1:] or "actualizado")
        return True

    def start_background(self, stop: threading.Event) -> threading.Thread:
        def loop() -> None:
            while not stop.is_set():
                self.update()
                stop.wait(UPDATE_INTERVAL_S)

        thread = threading.Thread(target=loop, name="ytdlp-update", daemon=True)
        thread.start()
        return thread
