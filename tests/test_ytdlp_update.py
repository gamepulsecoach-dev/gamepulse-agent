import subprocess
import threading
from pathlib import Path

from gp_agent.runner import build_download_args, download_error_message
from gp_agent.ytdlp_update import YtDlpManager

YT = "https://www.youtube.com/watch?v=abc123"


class FakeRun:
    def __init__(self, returncode=0, exc=None):
        self.calls, self.returncode, self.exc = [], returncode, exc

    def __call__(self, args, **kw):
        self.calls.append(args)
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.returncode, "", "")


def _bundled(tmp_path, content=b"v1"):
    src = tmp_path / "bundle" / "yt-dlp.exe"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(content)
    return src


class TestPrepare:
    def test_copies_bundled_to_user_dir(self, tmp_path):
        src = _bundled(tmp_path)
        mgr = YtDlpManager(src, tmp_path / "bin", run=FakeRun())
        path = mgr.prepare()
        assert path == tmp_path / "bin" / "yt-dlp.exe"
        assert path.read_bytes() == b"v1"

    def test_keeps_updated_copy_when_bundle_unchanged(self, tmp_path):
        src = _bundled(tmp_path)
        mgr = YtDlpManager(src, tmp_path / "bin", run=FakeRun())
        path = mgr.prepare()
        path.write_bytes(b"updated")  # yt-dlp -U replaced the user copy
        assert YtDlpManager(src, tmp_path / "bin", run=FakeRun()).prepare().read_bytes() == b"updated"

    def test_recopies_when_studio_ships_new_bundle(self, tmp_path):
        src = _bundled(tmp_path)
        YtDlpManager(src, tmp_path / "bin", run=FakeRun()).prepare().write_bytes(b"updated")
        src.write_bytes(b"v2-newer-bundle")  # Studio upgrade
        assert YtDlpManager(src, tmp_path / "bin", run=FakeRun()).prepare().read_bytes() == b"v2-newer-bundle"

    def test_falls_back_to_bundled_when_copy_fails(self, tmp_path):
        src = _bundled(tmp_path)
        blocker = tmp_path / "bin"
        blocker.write_text("not a dir")
        assert YtDlpManager(src, blocker, run=FakeRun()).prepare() == src


class TestUpdate:
    def test_runs_self_update_on_user_copy(self, tmp_path):
        run = FakeRun()
        mgr = YtDlpManager(_bundled(tmp_path), tmp_path / "bin", run=run)
        path = mgr.prepare()
        assert mgr.update() is True
        assert run.calls[-1][:2] == [str(path), "-U"]

    def test_never_updates_the_bundled_file(self, tmp_path):
        src = _bundled(tmp_path)
        blocker = tmp_path / "bin"
        blocker.write_text("x")
        run = FakeRun()
        mgr = YtDlpManager(src, blocker, run=run)
        mgr.prepare()
        assert mgr.update() is False
        assert run.calls == []

    def test_failure_is_swallowed(self, tmp_path):
        mgr = YtDlpManager(_bundled(tmp_path), tmp_path / "bin", run=FakeRun(exc=subprocess.TimeoutExpired("yt", 1)))
        mgr.prepare()
        assert mgr.update() is False
        mgr2 = YtDlpManager(_bundled(tmp_path), tmp_path / "bin2", run=FakeRun(returncode=1))
        mgr2.prepare()
        assert mgr2.update() is False

    def test_update_holds_lock(self, tmp_path):
        lock = threading.Lock()
        seen = []

        def run(args, **kw):
            seen.append(lock.locked())
            return subprocess.CompletedProcess(args, 0, "", "")

        mgr = YtDlpManager(_bundled(tmp_path), tmp_path / "bin", run=run, lock=lock)
        mgr.prepare()
        mgr.update()
        assert seen == [True]


class TestDownloadArgs:
    def test_remote_solver_components(self, tmp_path):
        args = build_download_args(Path("yt"), Path("ff"), YT, tmp_path / "k", deno=Path("d"))
        assert args[args.index("--remote-components") + 1] == "ejs:github"
        assert args.index("--remote-components") < args.index("--")

    def test_no_remote_components_without_deno(self, tmp_path):
        assert "--remote-components" not in build_download_args(Path("yt"), Path("ff"), YT, tmp_path / "k")


class TestDownloadErrorMessage:
    def test_youtube_block_gets_clear_message(self):
        tail = ("e. For more details, refer to https://github.com/yt-dlp/yt-dlp/wiki/EJS\n"
                "WARNING: [youtube] x: SABR\nERROR: [youtube] T_Rps: This video is not available")
        msg = download_error_message(tail)
        assert msg.startswith("O YouTube recusou o download")
        assert "ERROR: [youtube] T_Rps: This video is not available" in msg
        assert "WARNING" not in msg

    def test_other_errors_keep_raw_tail(self):
        assert download_error_message("ERROR: Unable to download webpage") == "Download do vídeo falhou: ERROR: Unable to download webpage"

    def test_empty(self):
        assert download_error_message("") == "Download do vídeo falhou"
