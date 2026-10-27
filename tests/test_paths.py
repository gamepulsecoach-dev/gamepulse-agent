import sys
from pathlib import Path

import pytest

from gp_agent.paths import cache_dir, config_dir, export_dir, find_tool, windows_documents

HOME = Path("/home/u")


class TestExportDir:
    def test_windows_uses_documents(self):
        env = {"USERPROFILE": r"C:\Users\coach"}
        assert export_dir("win32", HOME, env) == Path(r"C:\Users\coach") / "Documents" / "GamePulse"

    def test_macos_uses_documents(self):
        assert export_dir("darwin", HOME, {}) == HOME / "Documents" / "GamePulse"

    def test_override_wins(self):
        assert export_dir("win32", HOME, {"GP_EXPORT_DIR": "/x/clips"}) == Path("/x/clips")

    def test_windows_known_folder_wins_over_userprofile(self):
        # Documents redirected by OneDrive
        env = {"USERPROFILE": r"C:\Users\coach"}
        docs = Path(r"C:\Users\coach\OneDrive\Documentos")
        assert export_dir("win32", HOME, env, documents=docs) == docs / "GamePulse"

    def test_override_wins_over_known_folder(self):
        env = {"GP_EXPORT_DIR": "/x/clips"}
        assert export_dir("win32", HOME, env, documents=Path("/d")) == Path("/x/clips")


class TestWindowsDocuments:
    def test_non_windows_is_none(self):
        assert windows_documents("darwin") is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_windows_resolves_existing_folder(self):
        docs = windows_documents("win32")
        assert docs is not None and docs.is_dir()


class TestCacheDir:
    def test_windows_local_appdata(self):
        env = {"LOCALAPPDATA": r"C:\Users\coach\AppData\Local"}
        assert cache_dir("win32", HOME, env) == Path(env["LOCALAPPDATA"]) / "GamePulse" / "cache"

    def test_windows_without_env_falls_back_to_home(self):
        assert cache_dir("win32", HOME, {}) == HOME / "AppData" / "Local" / "GamePulse" / "cache"

    def test_macos_library_caches(self):
        assert cache_dir("darwin", HOME, {}) == HOME / "Library" / "Caches" / "GamePulse"


class TestConfigDir:
    def test_windows_appdata(self):
        env = {"APPDATA": r"C:\Users\coach\AppData\Roaming"}
        assert config_dir("win32", HOME, env) == Path(env["APPDATA"]) / "GamePulse"

    def test_macos_application_support(self):
        assert config_dir("darwin", HOME, {}) == HOME / "Library" / "Application Support" / "GamePulse"

    def test_unsupported_platform(self):
        with pytest.raises(ValueError):
            config_dir("linux", HOME, {})


class TestFindTool:
    def test_env_override(self, tmp_path):
        exe = tmp_path / "my-ffmpeg.exe"
        exe.write_bytes(b"")
        assert find_tool("ffmpeg", {"GP_FFMPEG": str(exe)}, bundle_dir=None, which=lambda _: None) == exe

    def test_env_override_missing_file_is_ignored(self, tmp_path):
        found = find_tool("ffmpeg", {"GP_FFMPEG": str(tmp_path / "nope")}, bundle_dir=None, which=lambda _: None)
        assert found is None

    def test_yt_dlp_env_var_name(self, tmp_path):
        exe = tmp_path / "y.exe"
        exe.write_bytes(b"")
        assert find_tool("yt-dlp", {"GP_YT_DLP": str(exe)}, bundle_dir=None, which=lambda _: None) == exe

    def test_bundle_dir_before_path(self, tmp_path):
        exe = tmp_path / "yt-dlp.exe"
        exe.write_bytes(b"")
        found = find_tool("yt-dlp", {}, bundle_dir=tmp_path, which=lambda _: "/usr/bin/yt-dlp")
        assert found == exe

    def test_bundle_dir_without_exe_suffix(self, tmp_path):
        exe = tmp_path / "ffmpeg"
        exe.write_bytes(b"")
        assert find_tool("ffmpeg", {}, bundle_dir=tmp_path, which=lambda _: None) == exe

    def test_falls_back_to_path(self):
        found = find_tool("ffmpeg", {}, bundle_dir=None, which=lambda name: f"/usr/bin/{name}")
        assert found == Path("/usr/bin/ffmpeg")

    def test_not_found(self):
        assert find_tool("ffmpeg", {}, bundle_dir=None, which=lambda _: None) is None
