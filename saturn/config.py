"""Application settings, loaded from environment and an optional .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None
    fred_api_key: str | None = None
    sec_user_agent: str | None = None
    default_model: str = "claude-sonnet-4-6"
    reports_dir: Path = Path("reports")
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Return a freshly-loaded Settings instance."""
    return Settings()
