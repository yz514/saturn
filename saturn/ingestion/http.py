"""Tiny HTTP helper for ingestion adapters (stdlib urllib, typed errors).

Centralizes the User-Agent header (SEC requires a contact UA) and converts any
transport error into a typed SourceFailure. Kept dependency-free on purpose.
"""

from __future__ import annotations

import logging
import re
from urllib import request

from saturn.ingestion.errors import SourceFailure

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


def _redact(url: str) -> str:
    """Mask secret query params (e.g. api_key, apikey, token) in a URL for logs/errors."""
    return re.sub(r"(?i)\b(api_key|apikey|token)=[^&\s]*", r"\1=***", url)


def http_get(url: str, *, user_agent: str, accept: str = "*/*", timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """GET `url` with the given User-Agent; return the raw body bytes.

    Raises SourceFailure on any transport/HTTP error.
    """
    req = request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:  # noqa: BLE001 - all transport failures are SourceFailure
        safe_url = _redact(url)
        logger.warning("http_get failed for %s: %s", safe_url, exc)
        raise SourceFailure(f"HTTP GET failed for {safe_url}: {exc}") from exc
