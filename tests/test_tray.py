from pathlib import Path
from types import SimpleNamespace

from gp_agent import tray
from gp_agent.tray import TITLE, Tray, open_folder, pairing_message


class FakeIcon:
    def __init__(self, name, image, title, menu):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self.notified, self.stopped, self.ran = [], False, False

    def notify(self, message, title):
        self.notified.append((message, title))

    def stop(self):
        self.stopped = True

    def run(self):
        self.ran = True


def fake_backend():
    return SimpleNamespace(
        Icon=FakeIcon,
        Menu=lambda *items: list(items),
        MenuItem=lambda text, action: SimpleNamespace(text=text, action=action),
    )


def make_tray(tmp_path, opened=None, quits=None):
    opened = [] if opened is None else opened
    quits = [] if quits is None else quits
    return Tray(fake_backend(), tmp_path, on_quit=lambda: quits.append(1), open_dir=opened.append)


class TestPairingMessage:
    def test_contains_code_and_validity(self):
        msg = pairing_message("123456")
        assert "123456" in msg and "5 min" in msg


class TestTray:
    def test_icon_has_title_and_two_items(self, tmp_path):
        t = make_tray(tmp_path)
        assert t._icon.title == TITLE
        assert [i.text for i in t._icon.menu] == ["Abrir pasta dos clips", "Sair"]

    def test_show_code_notifies(self, tmp_path):
        t = make_tray(tmp_path)
        t.show_code("654321")
        assert "654321" in t._icon.notified[0][0]

    def test_open_folder_item(self, tmp_path):
        opened = []
        t = make_tray(tmp_path, opened=opened)
        t._icon.menu[0].action(None, None)
        assert opened == [tmp_path]

    def test_quit_item_stops_server_and_icon(self, tmp_path):
        quits = []
        t = make_tray(tmp_path, quits=quits)
        t._icon.menu[1].action(None, None)
        assert quits == [1] and t._icon.stopped

    def test_run_blocks_on_icon(self, tmp_path):
        t = make_tray(tmp_path)
        t.run()
        assert t._icon.ran


class TestOpenFolder:
    def test_windows_uses_startfile(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(tray.os, "startfile", calls.append, raising=False)
        target = tmp_path / "clips"
        open_folder(target, "win32")
        assert calls == [target] and target.is_dir()

    def test_macos_uses_open(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(tray.subprocess, "Popen", calls.append)
        open_folder(tmp_path, "darwin")
        assert calls == [["open", str(tmp_path)]]


class TestLoadBackend:
    def test_returns_module_when_installed(self):
        assert tray.load_backend() is not None

    def test_none_when_import_fails(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fail(name, *a, **k):
            if name == "pystray":
                raise ImportError("no desktop")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fail)
        assert tray.load_backend() is None


def test_icon_image_is_square():
    assert tray._icon_image().size == (tray.ICON_SIZE, tray.ICON_SIZE)


def test_export_dir_is_path(tmp_path):
    assert isinstance(make_tray(tmp_path)._export_dir, Path)
