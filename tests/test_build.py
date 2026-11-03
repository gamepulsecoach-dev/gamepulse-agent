import plistlib
from pathlib import Path

import pytest

from build_tools.build import APP_NAME, binary_names, patch_info_plist, pyinstaller_args


class TestBinaryNames:
    def test_windows_exe(self):
        assert binary_names("win32") == ["yt-dlp.exe", "ffmpeg.exe", "deno.exe"]

    def test_macos_plain(self):
        assert binary_names("darwin") == ["yt-dlp", "ffmpeg", "deno"]


class TestPyinstallerArgs:
    def test_windowed_onedir_named(self, tmp_path):
        args = pyinstaller_args(tmp_path / "entry.py", [], tmp_path / "dist", tmp_path / "work", "win32")
        assert "--windowed" in args and "--onedir" in args and "--noconfirm" in args
        assert args[args.index("--name") + 1] == APP_NAME

    def test_binaries_go_next_to_agent(self, tmp_path):
        tools = [tmp_path / "yt-dlp.exe", tmp_path / "ffmpeg.exe"]
        args = pyinstaller_args(tmp_path / "entry.py", tools, tmp_path / "d", tmp_path / "w", "win32")
        assert f"{tools[0]};." in args and f"{tools[1]};." in args

    def test_macos_uses_colon_and_bundle_id(self, tmp_path):
        tool = tmp_path / "ffmpeg"
        args = pyinstaller_args(tmp_path / "entry.py", [tool], tmp_path / "d", tmp_path / "w", "darwin")
        assert f"{tool}:." in args
        assert args[args.index("--osx-bundle-identifier") + 1] == "com.gamepulsecoach.agent"

    def test_relative_binaries_become_absolute(self, tmp_path, monkeypatch):
        # PyInstaller resolves relative --add-binary paths against --specpath, not the cwd
        monkeypatch.chdir(tmp_path)
        args = pyinstaller_args(Path("e.py"), [Path("tools-bin/ffmpeg.exe")], tmp_path / "d", tmp_path / "w", "win32")
        assert f"{tmp_path / 'tools-bin' / 'ffmpeg.exe'};." in args
        assert args[-1] == str(tmp_path / "e.py")

    def test_entry_is_last(self, tmp_path):
        entry = tmp_path / "entry.py"
        assert pyinstaller_args(entry, [], tmp_path / "d", tmp_path / "w", "win32")[-1] == str(entry)

    def test_unsupported_platform(self, tmp_path):
        with pytest.raises(ValueError):
            pyinstaller_args(Path("e.py"), [], tmp_path, tmp_path, "linux")


class TestPatchInfoPlist:
    def make_app(self, tmp_path):
        plist = tmp_path / "GamePulseStudio.app" / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.gamepulsecoach.agent", "CFBundleShortVersionString": "0.0.0"}))
        return plist

    def test_menu_bar_only_with_version_and_name(self, tmp_path):
        plist = self.make_app(tmp_path)
        patch_info_plist(tmp_path / "GamePulseStudio.app", "0.8.0")
        info = plistlib.loads(plist.read_bytes())
        assert info["LSUIElement"] is True  # tray app: no Dock icon
        assert info["CFBundleShortVersionString"] == "0.8.0" and info["CFBundleVersion"] == "0.8.0"
        assert info["CFBundleDisplayName"] == "GamePulse Studio"
        assert info["CFBundleIdentifier"] == "com.gamepulsecoach.agent"  # untouched
