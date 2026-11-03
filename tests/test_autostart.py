import plistlib
from pathlib import Path

from gp_agent.autostart import LABEL, ensure_launch_agent, launch_agent_plist

EXE = Path("/Applications/GamePulseStudio.app/Contents/MacOS/GamePulseStudio")


def agent_file(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


class TestLaunchAgentPlist:
    def test_runs_app_at_login(self):
        info = plistlib.loads(launch_agent_plist(EXE))
        assert info["Label"] == LABEL
        assert info["ProgramArguments"] == [str(EXE)]
        assert info["RunAtLoad"] is True
        assert info["ProcessType"] == "Interactive"

    def test_does_not_respawn_after_quit(self):
        # "Sair" in the menu must really quit
        assert "KeepAlive" not in plistlib.loads(launch_agent_plist(EXE))


class TestEnsureLaunchAgent:
    def test_writes_when_missing(self, tmp_path):
        assert ensure_launch_agent("darwin", tmp_path, EXE) is True
        assert plistlib.loads(agent_file(tmp_path).read_bytes())["ProgramArguments"] == [str(EXE)]

    def test_noop_when_up_to_date(self, tmp_path):
        ensure_launch_agent("darwin", tmp_path, EXE)
        assert ensure_launch_agent("darwin", tmp_path, EXE) is False

    def test_rewrites_when_app_moved(self, tmp_path):
        ensure_launch_agent("darwin", tmp_path, EXE)
        moved = Path("/Users/x/Applications/GamePulseStudio.app/Contents/MacOS/GamePulseStudio")
        assert ensure_launch_agent("darwin", tmp_path, moved) is True
        assert plistlib.loads(agent_file(tmp_path).read_bytes())["ProgramArguments"] == [str(moved)]

    def test_skips_app_run_from_disk_image(self, tmp_path):
        # Running straight from the mounted .dmg: the path vanishes after eject
        dmg = Path("/Volumes/GamePulse Studio/GamePulseStudio.app/Contents/MacOS/GamePulseStudio")
        assert ensure_launch_agent("darwin", tmp_path, dmg) is False
        assert not agent_file(tmp_path).exists()

    def test_skips_translocated_app(self, tmp_path):
        # Gatekeeper App Translocation runs quarantined apps from a random read-only path
        moved = Path("/private/var/folders/ab/T/AppTranslocation/1234/d/GamePulseStudio.app/Contents/MacOS/GamePulseStudio")
        assert ensure_launch_agent("darwin", tmp_path, moved) is False

    def test_other_platforms_noop(self, tmp_path):
        assert ensure_launch_agent("win32", tmp_path, EXE) is False
        assert not (tmp_path / "Library").exists()
