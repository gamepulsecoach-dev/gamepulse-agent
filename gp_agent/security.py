"""Who may talk to the agent: closed origin list + paired token."""

import hmac
import re
import secrets

PROD_ORIGINS = frozenset({"https://gamepulsecoach.com", "https://www.gamepulsecoach.com"})

_DEV_ORIGINS = (
    re.compile(r"^https://gamepulse-deploy-[a-z0-9-]+\.vercel\.app$"),
    re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?$"),
)


def is_allowed_origin(origin: str | None, allow_dev: bool) -> bool:
    if not origin:
        return False
    if origin in PROD_ORIGINS:
        return True
    return allow_dev and any(p.match(origin) for p in _DEV_ORIGINS)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_matches(expected: str, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode(), provided.encode())
