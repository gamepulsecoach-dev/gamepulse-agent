"""Start the agent: python -m gp_agent  (GP_AGENT_DEV=1 also accepts DEV origins)."""

import logging
import os
import socket
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from gp_agent.ffmpeg_cmd import pick_encoder
from gp_agent.files import FileTickets
from gp_agent.jobs import JobQueue
from gp_agent.pairing import Pairing, TokenStore
from gp_agent.paths import cache_dir, config_dir, export_dir, find_tool, windows_documents
from gp_agent.exporter import export
from gp_agent.runner import Runner, Tools
from gp_agent.server import DEFAULT_PORT, Settings, create_app
from gp_agent.tray import Tray, load_backend

log = logging.getLogger("gp_agent")

LOG_MAX_BYTES = 1_000_000
SHUTDOWN_TIMEOUT_S = 10


def _bundle_dir() -> Path | None:
    # PyInstaller unpacks shipped binaries here
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _console_code(code: str) -> None:
    log.warning("Código de emparelhamento: %s (válido 5 min)", code)


def _setup_logging(log_dir: Path) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    if sys.stderr is not None:
        logging.basicConfig(level=logging.INFO, format=fmt)
        return
    # Windowed build: no console, keep a small log next to the token
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "agent.log", maxBytes=LOG_MAX_BYTES, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[handler])


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _stop(server_box: list[uvicorn.Server]) -> None:
    for server in server_box:
        server.should_exit = True


def main() -> int:
    env, home, platform = os.environ, Path.home(), sys.platform
    conf_dir = config_dir(platform, home, env)
    _setup_logging(conf_dir)

    ytdlp = find_tool("yt-dlp", env, _bundle_dir())
    ffmpeg = find_tool("ffmpeg", env, _bundle_dir())
    if ytdlp is None or ffmpeg is None:
        log.error("yt-dlp ou ffmpeg não encontrados (definir GP_YT_DLP / GP_FFMPEG)")
        return 1
    deno = find_tool("deno", env, _bundle_dir())
    if deno is None:
        log.warning("deno não encontrado (GP_DENO) — downloads do YouTube podem falhar")

    settings = Settings(
        export_dir=export_dir(platform, home, env, windows_documents(platform)),
        allow_dev=env.get("GP_AGENT_DEV") == "1",
        port=int(env.get("GP_AGENT_PORT", DEFAULT_PORT)),  # override only for local testing
    )
    if not _port_free(settings.port):
        log.error("Porta %d ocupada — o agente já está a correr?", settings.port)
        return 1

    server_box: list[uvicorn.Server] = []
    backend = None if env.get("GP_AGENT_NO_TRAY") == "1" else load_backend()
    tray = Tray(backend, settings.export_dir, on_quit=lambda: _stop(server_box)) if backend else None

    runner = Runner(Tools(ytdlp=ytdlp, ffmpeg=ffmpeg, encoder=pick_encoder(platform), deno=deno), cache_dir(platform, home, env))
    app = create_app(
        settings,
        queue=JobQueue(lambda req, ctx: export(req, runner, settings.export_dir, ctx)),
        pairing=Pairing(),
        tokens=TokenStore(conf_dir / "token"),
        show_code=tray.show_code if tray else _console_code,
        tickets=FileTickets(settings.export_dir),
        probe=runner.probe,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port, log_level="warning", log_config=None))
    server_box.append(server)
    log.info("Clips em %s | dev=%s | tray=%s", settings.export_dir, settings.allow_dev, bool(tray))

    if tray is None:
        server.run()
        return 0
    # The tray owns the main thread (required on macOS); HTTP runs beside it
    thread = threading.Thread(target=server.run, name="gp-agent-http", daemon=True)
    thread.start()
    tray.run()
    thread.join(timeout=SHUTDOWN_TIMEOUT_S)
    return 0


if __name__ == "__main__":
    sys.exit(main())
