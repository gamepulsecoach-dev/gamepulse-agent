"""macOS start-at-login: a per-user LaunchAgent pointing at the installed app (Windows: installer's HKCU Run)."""

import plistlib
from pathlib import Path

LABEL = "com.gamepulsecoach.studio"

# Paths that don't survive: the mounted .dmg and Gatekeeper's App Translocation copy
_TRANSIENT_MARKERS = ("/Volumes/", "/AppTranslocation/")


def launch_agent_plist(executable: Path) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(executable)],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    })


def ensure_launch_agent(platform: str, home: Path, executable: Path) -> bool:
    """Write/refresh the LaunchAgent; True when the file changed. Loaded by launchd at next login."""
    if platform != "darwin":
        return False
    if any(marker in executable.as_posix() for marker in _TRANSIENT_MARKERS):
        return False
    target = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    content = launch_agent_plist(executable)
    if target.is_file() and target.read_bytes() == content:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return True
