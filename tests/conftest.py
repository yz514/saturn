"""Shared test fixtures.

Keeps the suite fully offline regardless of a developer's local machine state:
no test should ever load a real ANTHROPIC_API_KEY (from the shell environment
or a local .env file) and accidentally construct a real LLM client or hit the
network.
"""

from __future__ import annotations

import pytest

from saturn.config import Settings


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    # Ignore any real .env file during tests, and drop a shell-exported key.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
