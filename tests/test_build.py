from pathlib import Path

import pytest

from build_tools.build import APP_NAME, binary_names, pyinstaller_args


class TestBinaryNames:
    def test_windows_exe(self):
        assert binary_names("win32") == ["yt-dlp.exe", "ffmpeg.exe"]

    def test_macos_plain(self):
        assert binary_names("darwin") == ["yt-dlp", "ffmpeg"]


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
