"""HTTP API the GamePulse site talks to (bound to 127.0.0.1 only)."""

import base64
import binascii
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gp_agent import __version__
from gp_agent.exporter import ClipRequest, CompileRequest, Overlay
from gp_agent.jobs import JobQueue
from gp_agent.overlay import png_size
from gp_agent.pairing import CODE_TTL_S, Pairing, PairingLocked, TokenStore
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
    game: str = Field(max_length=MAX_TEXT)
    category: str = Field(max_length=MAX_TEXT)
    index: int = Field(ge=1, le=999)


class CompileIn(_CamelModel):
    game: str = Field(max_length=MAX_TEXT)
    title: str = Field(max_length=MAX_TEXT)
    clips: list[ClipPartIn] = Field(min_length=1, max_length=MAX_CLIPS)


class PairIn(BaseModel):
    code: str = Field(max_length=16)


class BadInput(ValueError):
    """Request is well-formed JSON but its content can't be used."""


def _decode_overlay(ov: OverlayIn) -> Overlay:
    try:
        data = base64.b64decode(ov.png, validate=True)
        png_size(data)
    except (binascii.Error, ValueError):
        raise BadInput("Imagem do desenho inválida") from None
    return Overlay(timestamp_ms=ov.timestamp_ms, png=data)


def _to_request(part: ClipPartIn, game: str, category: str, index: int) -> ClipRequest:
    try:
        classify_source(part.video_url)
    except ValueError:
        raise BadInput("Fonte de vídeo não suportada") from None
    return ClipRequest(
        video_url=part.video_url, timestamp_ms=part.timestamp_ms, pre_ms=part.pre_ms, post_ms=part.post_ms,
        game=game, category=category, index=index,
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
        job = queue.submit(_to_request(body, body.game, body.category, body.index))
        return JSONResponse({"jobId": job.id}, status_code=202)

    @app.post("/compile", status_code=202, dependencies=[Depends(require_token)])
    def compile_clips(body: CompileIn) -> JSONResponse:
        clips = tuple(_to_request(part, body.game, body.title, i) for i, part in enumerate(body.clips, start=1))
        job = queue.submit(CompileRequest(game=body.game, title=body.title, clips=clips))
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
        }

    return app
