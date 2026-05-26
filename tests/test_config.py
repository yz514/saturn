from saturn.config import Settings, get_settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    s = Settings(_env_file=None)
    assert s.default_model == "claude-sonnet-4-6"
    assert s.anthropic_api_key is None
    assert s.log_level == "INFO"


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DEFAULT_MODEL", "claude-opus-4-7")
    s = Settings(_env_file=None)
    assert s.anthropic_api_key == "test-key"
    assert s.default_model == "claude-opus-4-7"


def test_get_settings_returns_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert get_settings().anthropic_api_key == "k"
