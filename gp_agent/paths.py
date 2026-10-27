"""Where the agent keeps exported clips, cached videos, config and its tools."""

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

APP_DIR = "GamePulse"

_TOOL_ENV = {"ffmpeg": "GP_FFMPEG", "yt-dlp": "GP_YT_DLP", "deno": "GP_DENO"}


def _check_platform(platform: str) -> None:
    if platform not in ("win32", "darwin"):
        raise ValueError(f"unsupported platform: {platform}")


def windows_documents(platform: str) -> Path | None:
    """The real Documents folder, which OneDrive or the user may have moved."""
    if platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Guid(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
    folder_id = _Guid(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value) if out.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def export_dir(platform: str, home: Path, env: Mapping[str, str], documents: Path | None = None) -> Path:
    if env.get("GP_EXPORT_DIR"):
        return Path(env["GP_EXPORT_DIR"])
    _check_platform(platform)
    if documents is not None:
        return documents / APP_DIR
    base = Path(env["USERPROFILE"]) if platform == "win32" and env.get("USERPROFILE") else home
    return base / "Documents" / APP_DIR


def cache_dir(platform: str, home: Path, env: Mapping[str, str]) -> Path:
    _check_platform(platform)
    if platform == "darwin":
        return home / "Library" / "Caches" / APP_DIR
    local = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData" / "Local"
    return local / APP_DIR / "cache"


def config_dir(platform: str, home: Path, env: Mapping[str, str]) -> Path:
    _check_platform(platform)
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR
    roaming = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming"
    return roaming / APP_DIR


def find_tool(
    name: str,
    env: Mapping[str, str],
    bundle_dir: Path | None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Env override, then the binary shipped next to the agent, then PATH."""
    override = env.get(_TOOL_ENV.get(name, ""))
    if override:
        path = Path(override)
        return path if path.is_file() else None
    if bundle_dir is not None:
        for candidate in (bundle_dir / f"{name}.exe", bundle_dir / name):
            if candidate.is_file():
                return candidate
    found = which(name)
    return Path(found) if found else None
