"""User-facing name is "GamePulse Studio" — "agent" reads badly to users.

The internal protocol id ("gamepulse-agent" in /health), the data folder and
the installer AppId stay unchanged so existing installs keep working.
"""

import re
from pathlib import Path

from build_tools.build import APP_NAME
from gp_agent.jobs import GENERIC_ERROR
from gp_agent.tray import TITLE

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "installer" / "GamePulseStudio.iss"
OLD_APP_ID = "{{6F1C2B0E-4E1A-4C7B-9C55-3B9E2A7D4A10}"


def test_visible_names():
    assert TITLE == "GamePulse Studio"
    assert APP_NAME == "GamePulseStudio"
    assert "agente" not in GENERIC_ERROR.lower()


def test_installer_branding_and_upgrade_path():
    iss = ISS.read_text(encoding="utf-8")
    assert "AppName=GamePulse Studio" in iss
    assert "OutputBaseFilename=GamePulseStudio-Setup-{#AppVersion}" in iss
    # Same AppId: the new installer upgrades the old "GamePulse Agent" in place
    assert f"AppId={OLD_APP_ID}" in iss
    # Old exe, shortcut and autostart entry are cleaned up on upgrade
    assert re.search(r'Name: "\{app\}\\GamePulseAgent\.exe"', iss)
    assert "GamePulse Agent.lnk" in iss
    assert re.search(r'ValueName: "GamePulseAgent";.*Flags: deletevalue', iss)
    assert "taskkill" in iss and "GamePulseAgent.exe" in iss


def test_release_keeps_legacy_download_name():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "GamePulseStudio-Setup.exe" in wf
    # Old web clients still link to the old fixed name
    assert "GamePulseAgent-Setup.exe" in wf
