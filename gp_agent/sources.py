"""Which video URLs the agent accepts, and how to read them."""

import ipaddress
import re
from enum import Enum
from urllib.parse import parse_qs, urlsplit

YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
GDRIVE_HOSTS = frozenset({"drive.google.com"})

_GDRIVE_PATH_ID = re.compile(r"/file/d/([A-Za-z0-9_-]+)")
_GDRIVE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class SourceKind(Enum):
    YOUTUBE = "youtube"
    GDRIVE = "gdrive"
    DIRECT = "direct"


def _is_local_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def classify_source(url: str | None) -> SourceKind:
    """Return the source kind, or raise ValueError for anything unsupported or unsafe."""
    if not isinstance(url, str) or not url:
        raise ValueError("empty url")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise ValueError("only https urls are supported")
    if parts.username or parts.password:
        raise ValueError("credentials in url are not allowed")
    if _is_local_host(host):
        raise ValueError("local network addresses are not allowed")
    if host in YOUTUBE_HOSTS:
        return SourceKind.YOUTUBE
    if host in GDRIVE_HOSTS:
        return SourceKind.GDRIVE
    if parts.path.lower().endswith(".mp4"):
        return SourceKind.DIRECT
    raise ValueError("unsupported video source")


def gdrive_file_id(url: str) -> str:
    parts = urlsplit(url)
    match = _GDRIVE_PATH_ID.search(parts.path)
    file_id = match.group(1) if match else (parse_qs(parts.query).get("id") or [""])[0]
    if not file_id or not _GDRIVE_ID.match(file_id):
        raise ValueError("google drive file id not found")
    return file_id
