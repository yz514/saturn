"""Anthropic-backed LLMClient with prompt caching on the system prompt."""

from __future__ import annotations


def _build_params(
    system: str, prompt: str, model: str, *, max_tokens: int = 2000
) -> dict:
    """Build the kwargs for Anthropic messages.create (pure, testable)."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": prompt}],
    }


class AnthropicClient:
    """LLMClient implementation backed by the Anthropic SDK."""

    def __init__(self, api_key: str, default_model: str) -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self._default_model = default_model

    def complete(self, system: str, prompt: str, *, model: str | None = None) -> str:
        params = _build_params(system, prompt, model or self._default_model)
        response = self._client.messages.create(**params)
        return "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
