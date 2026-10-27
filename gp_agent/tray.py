"""System tray icon: shows the pairing code, opens the clips folder, quits the agent."""

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

log = logging.getLogger(__name__)

TITLE = "GamePulse Agent"
ICON_SIZE = 64
ICON_GREEN = (34, 197, 94, 255)

# MessageBoxW flags
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000


def pairing_message(code: str) -> str:
    return f"Código de emparelhamento: {code}\nIntroduza-o no GamePulse (válido 5 min)."


def _user32():
    import ctypes

    return ctypes.windll.user32  # type: ignore[attr-defined]


def code_dialog(message: str, title: str, platform: str = sys.platform) -> None:
    """Blocking always-on-top box with the code (Windows notifications can be silenced)."""
    if platform != "win32":
        return  # TODO(F5 macOS): osascript dialog
    _user32().MessageBoxW(None, message, title, MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)


def open_folder(path: Path, platform: str = sys.platform) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open", str(path)])


def _icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((4, 4, ICON_SIZE - 4, ICON_SIZE - 4), fill=ICON_GREEN)
    return img


def load_backend() -> ModuleType | None:
    """pystray, or None when it can't run here (missing package, no desktop session)."""
    try:
        import pystray
    except Exception:  # any import failure means console mode
        return None
    return pystray


class Tray:
    def __init__(
        self,
        backend: ModuleType,
        export_dir: Path,
        on_quit: Callable[[], None],
        open_dir: Callable[[Path], None] = open_folder,
        show_dialog: Callable[[str, str], None] = code_dialog,
    ) -> None:
        self._export_dir = export_dir
        self._on_quit = on_quit
        self._open_dir = open_dir
        self._show_dialog = show_dialog
        self._dialog_thread: threading.Thread | None = None
        menu = backend.Menu(
            backend.MenuItem("Abrir pasta dos clips", lambda *_: self._open_dir(self._export_dir)),
            backend.MenuItem("Sair", lambda *_: self.quit()),
        )
        self._icon = backend.Icon("gamepulse-agent", _icon_image(), TITLE, menu)

    def show_code(self, code: str) -> None:
        message = pairing_message(code)
        try:
            self._icon.notify(message, TITLE)
        except Exception:  # notification is best-effort; the dialog below is the reliable path
            log.warning("Notificação do código falhou", exc_info=True)
        # The box blocks until closed: own thread so /pair/start answers immediately
        self._dialog_thread = threading.Thread(target=self._show_dialog, args=(message, TITLE), daemon=True)
        self._dialog_thread.start()

    def quit(self) -> None:
        self._on_quit()
        self._icon.stop()

    def run(self) -> None:
        # Blocks; macOS needs this on the main thread
        self._icon.run()
