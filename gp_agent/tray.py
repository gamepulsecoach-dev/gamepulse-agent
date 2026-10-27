"""System tray icon: shows the pairing code, opens the clips folder, quits the agent."""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

TITLE = "GamePulse Agent"
ICON_SIZE = 64
ICON_GREEN = (34, 197, 94, 255)


def pairing_message(code: str) -> str:
    return f"Código de emparelhamento: {code}\nIntroduza-o no GamePulse (válido 5 min)."


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
    ) -> None:
        self._export_dir = export_dir
        self._on_quit = on_quit
        self._open_dir = open_dir
        menu = backend.Menu(
            backend.MenuItem("Abrir pasta dos clips", lambda *_: self._open_dir(self._export_dir)),
            backend.MenuItem("Sair", lambda *_: self.quit()),
        )
        self._icon = backend.Icon("gamepulse-agent", _icon_image(), TITLE, menu)

    def show_code(self, code: str) -> None:
        self._icon.notify(pairing_message(code), TITLE)

    def quit(self) -> None:
        self._on_quit()
        self._icon.stop()

    def run(self) -> None:
        # Blocks; macOS needs this on the main thread
        self._icon.run()
