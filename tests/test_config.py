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


def test_new_keys_default_none(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    s = get_settings()
    assert s.fred_api_key is None
    assert s.sec_user_agent is None


def test_new_keys_read_from_env(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "abc123")
    monkeypatch.setenv("SEC_USER_AGENT", "Saturn test@example.com")
    s = get_settings()
    assert s.fred_api_key == "abc123"
    assert s.sec_user_agent == "Saturn test@example.com"
