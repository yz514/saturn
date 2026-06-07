"""Live dependency checks for `saturn doctor`.

Each check wraps one real adapter/credential and returns a CheckResult; a check
never raises. The live network calls here are intentional (this is the one
non-offline command). Unit tests monkeypatch the adapters to stay offline.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from saturn.llm.anthropic_client import AnthropicClient

logger = logging.getLogger(__name__)

_PING_MODEL = "claude-haiku-4-5"  # cheapest model — this only proves the key works


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str


def check_anthropic(settings) -> CheckResult:
    """Verify the Anthropic key by a tiny live ping on the cheapest model."""
    if not settings.anthropic_api_key:
        return CheckResult(name="Anthropic", ok=False, detail="ANTHROPIC_API_KEY not set")
    try:
        client = AnthropicClient(settings.anthropic_api_key, _PING_MODEL)
        reply = client.complete("You are a health check.", "Reply with the single word: OK")
        if reply and reply.strip():
            return CheckResult(name="Anthropic", ok=True, detail=f"key works ({_PING_MODEL} responded)")
        return CheckResult(name="Anthropic", ok=False, detail="empty response from model")
    except Exception as exc:  # noqa: BLE001 - a check never raises
        return CheckResult(name="Anthropic", ok=False, detail=str(exc))
