"""Provider-agnostic LLM interface."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Minimal text-completion interface implemented by all providers."""

    def complete(self, system: str, prompt: str, *, model: str | None = None) -> str:
        """Return the model's text response to `prompt` under `system`."""
        ...
