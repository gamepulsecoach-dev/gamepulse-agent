"""Build GamePulse Studio with PyInstaller: python -m build_tools.build --tools <dir with yt-dlp + ffmpeg>.

The tools dir must hold the binaries that ship with the agent (CI fetches the
LGPL ffmpeg build and the yt-dlp release; locally any copy works for testing).
"""

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

APP_NAME = "GamePulseStudio"
DISPLAY_NAME = "GamePulse Studio"
BUNDLE_ID = "com.gamepulsecoach.agent"
ROOT = Path(__file__).resolve().parent.parent


def binary_names(platform: str) -> list[str]:
    suffix = ".exe" if platform == "win32" else ""
    # deno: JS runtime yt-dlp needs for YouTube
    return [f"yt-dlp{suffix}", f"ffmpeg{suffix}", f"deno{suffix}"]


def pyinstaller_args(entry: Path, binaries: list[Path], dist: Path, work: Path, platform: str) -> list[str]:
    if platform not in ("win32", "darwin"):
        raise ValueError(f"unsupported platform: {platform}")
    sep = ";" if platform == "win32" else ":"
    args = [
        "--noconfirm",
        "--clean",
        "--onedir",  # onefile unpacks to %TEMP% on every start: slower and flagged more by antivirus
        "--windowed",
        "--name", APP_NAME,
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(work),
        "--collect-submodules", "uvicorn",
    ]
    if platform == "darwin":
        args += ["--osx-bundle-identifier", BUNDLE_ID]
    # PyInstaller resolves relative paths against --specpath, not the cwd
    for binary in binaries:
        args += ["--add-binary", f"{binary.resolve()}{sep}."]
    return [*args, str(entry.resolve())]


def patch_info_plist(app: Path, version: str) -> None:
    """Menu-bar app (no Dock icon) with the release version; PyInstaller's CLI can't set these."""
    plist = app / "Contents" / "Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info.update({
        "LSUIElement": True,
        "CFBundleDisplayName": DISPLAY_NAME,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
    })
    plist.write_bytes(plistlib.dumps(info))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--work", type=Path, default=ROOT / "build")
    parser.add_argument("--version", default="0.0.0")
    opts = parser.parse_args()

    binaries = [opts.tools / name for name in binary_names(sys.platform)]
    missing = [str(b) for b in binaries if not b.is_file()]
    if missing:
        print("Em falta: " + ", ".join(missing), file=sys.stderr)
        return 1

    import PyInstaller.__main__

    PyInstaller.__main__.run(pyinstaller_args(ROOT / "build_tools" / "entry.py", binaries, opts.dist, opts.work, sys.platform))
    if sys.platform == "darwin":
        app = opts.dist / f"{APP_NAME}.app"
        patch_info_plist(app, opts.version)
        # Editing Info.plist breaks PyInstaller's ad-hoc signature: re-sign (Apple Silicon refuses unsigned code)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
