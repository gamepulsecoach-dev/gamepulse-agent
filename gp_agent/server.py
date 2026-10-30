"""HTTP API the GamePulse site talks to (bound to 127.0.0.1 only)."""

import base64
import binascii
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gp_agent import __version__
from gp_agent.edit_cmd import Loupe, OverlaySegment, Pause, Rect, ZoomCrop
from gp_agent.exporter import ClipRequest, CompileRequest, EditRequest, Overlay
from gp_agent.ffmpeg_cmd import Probe
from gp_agent.files import FileTickets, resolve_export_file
from gp_agent.jobs import JobQueue
from gp_agent.overlay import png_size
from gp_agent.pairing import CODE_TTL_S, Pairing, PairingLocked, TokenStore
from gp_agent.runner import RunnerError
from gp_agent.security import is_allowed_origin, token_matches
from gp_agent.sources import classify_source

DEFAULT_PORT = 7777
MAX_TIMESTAMP_MS = 6 * 3600 * 1000
MAX_PADDING_MS = 120_000
MAX_TEXT = 200
MAX_URL = 2048
MAX_OVERLAYS = 5
MAX_CLIPS = 100
MAX_PNG_B64 = 12_000_000
MAX_BODY_BYTES = 64 * 1024 * 1024
# Telestrator edit (same limits as the site's edit-timeline.js / edit-export.js)
PAUSE_MIN_MS = 500
PAUSE_MAX_MS = 10_000
MAX_PAUSES = 50
MAX_EDIT_IMAGES = 1500
MAX_ZOOM_CROPS = 2000  # site: camera-zoom.js MAX_SAMPLES
MAX_LOUPES = 10  # site: loupe.js MAX_LOUPES
ZOOM_MIN_SIZE = 0.25  # crop of a 4x zoom
UV_EPS = 1e-6
MAX_SEGMENTS = 20_000

# Paths a browser may open without an Origin header (e.g. typing the url)
_NO_ORIGIN_OK = frozenset({"/health"})

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    export_dir: Path
    allow_dev: bool = False
    port: int = DEFAULT_PORT


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class OverlayIn(_CamelModel):
    timestamp_ms: int = Field(alias="timestampMs", ge=0, le=MAX_TIMESTAMP_MS)
    png: str = Field(min_length=1, max_length=MAX_PNG_B64)  # base64, no data: prefix


class ClipPartIn(_CamelModel):
    video_url: str = Field(alias="videoUrl", min_length=1, max_length=MAX_URL)
    timestamp_ms: int = Field(alias="timestampMs", ge=0, le=MAX_TIMESTAMP_MS)
    pre_ms: int = Field(alias="preMs", ge=0, le=MAX_PADDING_MS)
    post_ms: int = Field(alias="postMs", ge=0, le=MAX_PADDING_MS)
    overlays: list[OverlayIn] = Field(default_factory=list, max_length=MAX_OVERLAYS)


class ClipIn(ClipPartIn):
    team: str = Field(default="", max_length=MAX_TEXT)
    game: str = Field(max_length=MAX_TEXT)
    category: str = Field(max_length=MAX_TEXT)
    index: int = Field(ge=1, le=999)


class CompileIn(_CamelModel):
    team: str = Field(default="", max_length=MAX_TEXT)
    game: str = Field(max_length=MAX_TEXT)
    title: str = Field(max_length=MAX_TEXT)
    clips: list[ClipPartIn] = Field(min_length=1, max_length=MAX_CLIPS)


class PauseIn(_CamelModel):
    at_ms: int = Field(alias="atMs", ge=0, le=MAX_TIMESTAMP_MS)
    dur_ms: int = Field(alias="durMs", ge=PAUSE_MIN_MS, le=PAUSE_MAX_MS)


class SegmentIn(_CamelModel):
    image: int = Field(ge=0, lt=MAX_EDIT_IMAGES)
    dur_ms: int = Field(alias="durMs", ge=1, le=MAX_TIMESTAMP_MS)


def _uv(**kw) -> float:
    return Field(allow_inf_nan=False, **kw)


class RectIn(_CamelModel):
    x: float = _uv(ge=0, le=1)
    y: float = _uv(ge=0, le=1)
    w: float = _uv(gt=0, le=1)
    h: float = _uv(gt=0, le=1)


class ZoomCropIn(_CamelModel):
    at_ms: int = Field(alias="atMs", ge=0, le=MAX_TIMESTAMP_MS)
    x: float = _uv(ge=0, le=1)
    y: float = _uv(ge=0, le=1)
    w: float = _uv(ge=ZOOM_MIN_SIZE - UV_EPS, le=1)
    h: float = _uv(ge=ZOOM_MIN_SIZE - UV_EPS, le=1)


class LoupeIn(_CamelModel):
    start_ms: int = Field(alias="startMs", ge=0, le=MAX_TIMESTAMP_MS)
    end_ms: int = Field(alias="endMs", ge=0, le=MAX_TIMESTAMP_MS)
    src: RectIn
    dst: RectIn
    shape: Literal["circle", "rect"]


class EditIn(_CamelModel):
    path: str = Field(min_length=1, max_length=MAX_TEXT * 3)
    pauses: list[PauseIn] = Field(default_factory=list, max_length=MAX_PAUSES)
    segments: list[SegmentIn] = Field(min_length=1, max_length=MAX_SEGMENTS)
    images: list[str] = Field(min_length=1, max_length=MAX_EDIT_IMAGES)
    zoom: list[ZoomCropIn] = Field(default_factory=list, max_length=MAX_ZOOM_CROPS)
    loupes: list[LoupeIn] = Field(default_factory=list, max_length=MAX_LOUPES)


class PairIn(BaseModel):
    code: str = Field(max_length=16)


class FileTicketIn(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_TEXT * 3)


class BadInput(ValueError):
    """Request is well-formed JSON but its content can't be used."""


def _decode_overlay(ov: OverlayIn) -> Overlay:
    return Overlay(timestamp_ms=ov.timestamp_ms, png=_decode_png(ov.png))


def _decode_png(b64: str) -> bytes:
    try:
        data = base64.b64decode(b64, validate=True)
        png_size(data)
    except (binascii.Error, ValueError):
        raise BadInput("Imagem do desenho inválida") from None
    return data


def _inside(r: RectIn | ZoomCropIn) -> bool:
    return r.x + r.w <= 1 + UV_EPS and r.y + r.h <= 1 + UV_EPS


def _rect(r: RectIn) -> Rect:
    return Rect(r.x, r.y, r.w, r.h)


def _zoom(crops: list[ZoomCropIn]) -> tuple[ZoomCrop, ...]:
    if any(b.at_ms <= a.at_ms for a, b in zip(crops, crops[1:])):
        raise BadInput("Zoom com tempos fora de ordem")
    if not all(_inside(c) for c in crops):
        raise BadInput("Zoom fora do vídeo")
    return tuple(ZoomCrop(c.at_ms, c.x, c.y, c.w, c.h) for c in crops)


def _loupes(items: list[LoupeIn]) -> tuple[Loupe, ...]:
    if any(lp.end_ms <= lp.start_ms for lp in items):
        raise BadInput("Lupa sem duração")
    if not all(_inside(lp.src) and _inside(lp.dst) for lp in items):
        raise BadInput("Lupa fora do vídeo")
    return tuple(Loupe(lp.start_ms, lp.end_ms, _rect(lp.src), _rect(lp.dst), lp.shape) for lp in items)


def _to_edit(body: EditIn, export_dir: Path) -> EditRequest:
    if any(seg.image >= len(body.images) for seg in body.segments):
        raise BadInput("Segmento do desenho sem imagem")
    zoom, loupes = _zoom(body.zoom), _loupes(body.loupes)
    try:
        src = resolve_export_file(export_dir, body.path)
    except ValueError:
        raise BadInput("Caminho do clip inválido") from None
    return EditRequest(
        src=src,
        pauses=tuple(Pause(p.at_ms, p.dur_ms) for p in body.pauses),
        segments=tuple(OverlaySegment(s.image, s.dur_ms) for s in body.segments),
        images=tuple(_decode_png(img) for img in body.images),
        zoom=zoom,
        loupes=loupes,
    )


def _to_request(part: ClipPartIn, game: str, category: str, index: int, team: str = "") -> ClipRequest:
    try:
        classify_source(part.video_url)
    except ValueError:
        raise BadInput("Fonte de vídeo não suportada") from None
    return ClipRequest(
        video_url=part.video_url, timestamp_ms=part.timestamp_ms, pre_ms=part.pre_ms, post_ms=part.post_ms,
        game=game, category=category, index=index, team=team,
        overlays=tuple(_decode_overlay(ov) for ov in part.overlays),
    )


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-GP-Token",
        "Access-Control-Max-Age": "600",
        # Chrome/Edge Private (Local) Network Access preflight
        "Access-Control-Allow-Private-Network": "true",
        "Access-Control-Allow-Local-Network": "true",
        "Vary": "Origin",
    }


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _relative_file(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    return str(path.relative_to(base)) if path.is_relative_to(base) else path.name


def _body_too_big(request: Request) -> bool:
    length = request.headers.get("content-length")
    return length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES)


def create_app(
    settings: Settings,
    queue: JobQueue,
    pairing: Pairing,
    tokens: TokenStore,
    show_code: Callable[[str], None],
    tickets: FileTickets,
    probe: Callable[[Path], Probe],
) -> FastAPI:
    app = FastAPI(title="GamePulse Agent", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    allowed_hosts = {f"localhost:{settings.port}", f"127.0.0.1:{settings.port}"}

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        # Same {"error": ...} shape as every other failure the site sees
        return _error(exc.status_code, str(exc.detail))

    @app.exception_handler(BadInput)
    async def bad_input(_request: Request, exc: BadInput) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_body(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo the input back: NaN/Infinity would break the JSON response
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return _error(422, f"Pedido inválido: {where}" if where else "Pedido inválido")

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # A foreign Host means DNS rebinding: some site resolved its own name to 127.0.0.1
        if request.headers.get("host") not in allowed_hosts:
            return _error(400, "Host inválido")
        origin = request.headers.get("origin")
        if origin is None:
            if request.method == "GET" and request.url.path in _NO_ORIGIN_OK:
                return await call_next(request)
            return _error(403, "Origem em falta")
        if not is_allowed_origin(origin, settings.allow_dev):
            return _error(403, "Origem não autorizada")
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers(origin))
        if _body_too_big(request):
            response: Response = _error(413, "Pedido demasiado grande")
        else:
            response = await call_next(request)
        response.headers.update(_cors_headers(origin))
        return response

    def require_token(x_gp_token: Annotated[str | None, Header()] = None) -> None:
        if not token_matches(tokens.load() or "", x_gp_token):
            raise HTTPException(status_code=401, detail="Agente não emparelhado com este browser")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "agent": "gamepulse-agent", "version": __version__, "paired": tokens.load() is not None}

    @app.post("/pair/start")
    def pair_start() -> dict:
        show_code(pairing.start())
        return {"ok": True, "expiresIn": CODE_TTL_S}

    @app.post("/pair")
    def pair(body: PairIn) -> JSONResponse:
        try:
            ok = pairing.confirm(body.code)
        except PairingLocked:
            return _error(429, "Demasiadas tentativas. Pede um código novo.")
        if not ok:
            return _error(403, "Código inválido ou expirado")
        return JSONResponse({"token": tokens.load_or_create()})

    @app.post("/clip", status_code=202, dependencies=[Depends(require_token)])
    def clip(body: ClipIn) -> JSONResponse:
        job = queue.submit(_to_request(body, body.game, body.category, body.index, body.team))
        return JSONResponse({"jobId": job.id}, status_code=202)

    @app.post("/compile", status_code=202, dependencies=[Depends(require_token)])
    def compile_clips(body: CompileIn) -> JSONResponse:
        clips = tuple(_to_request(part, body.game, body.title, i, body.team) for i, part in enumerate(body.clips, start=1))
        job = queue.submit(CompileRequest(game=body.game, title=body.title, clips=clips, team=body.team))
        return JSONResponse({"jobId": job.id}, status_code=202)

    @app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
    def job_status(job_id: str) -> dict:
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Pedido não encontrado")
        return {
            "id": job.id,
            "status": job.status.value,
            "file": _relative_file(job.file, settings.export_dir),
            "error": job.error,
            "progress": job.progress,
        }

    @app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
    def job_cancel(job_id: str) -> JSONResponse:
        if queue.get(job_id) is None:
            return _error(404, "Pedido não encontrado")
        if not queue.cancel(job_id):
            return _error(409, "O pedido já terminou")
        return JSONResponse({"ok": True})

    @app.post("/edit", status_code=202, dependencies=[Depends(require_token)])
    def edit(body: EditIn) -> JSONResponse:
        try:
            req = _to_edit(body, settings.export_dir)
        except FileNotFoundError:
            return _error(404, "Clip não encontrado neste PC")
        job = queue.submit(req)
        return JSONResponse({"jobId": job.id}, status_code=202)

    @app.post("/files/ticket", dependencies=[Depends(require_token)])
    def file_ticket(body: FileTicketIn) -> JSONResponse:
        try:
            ticket = tickets.issue(body.path)
        except FileNotFoundError:
            return _error(404, "Clip não encontrado neste PC")
        except ValueError:
            return _error(400, "Caminho do clip inválido")
        try:
            info = probe(tickets.resolve(ticket))
        except (RunnerError, ValueError):
            return _error(422, "Não foi possível ler o clip")
        return JSONResponse({
            "url": f"/files/{ticket}",
            "width": info.width, "height": info.height,
            "fps": info.fps, "durationMs": info.duration_ms,
        })

    @app.get("/files/{ticket}")
    def serve_file(ticket: str) -> Response:
        path = tickets.resolve(ticket)
        if path is None or not path.is_file():
            return _error(404, "Ligação expirada ou clip removido")
        # FileResponse answers Range requests (206), which <video> needs to seek
        return FileResponse(path, media_type="video/mp4")

    return app
