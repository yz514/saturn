from types import SimpleNamespace

from saturn.diagnostics import CheckResult, check_anthropic


class _FakeClient:
    def __init__(self, api_key, default_model):
        self.default_model = default_model

    def complete(self, system, prompt, *, model=None):
        return "OK"


def test_check_anthropic_missing_key():
    r = check_anthropic(SimpleNamespace(anthropic_api_key=None))
    assert isinstance(r, CheckResult)
    assert r.name == "Anthropic"
    assert r.ok is False
    assert "ANTHROPIC_API_KEY not set" in r.detail


def test_check_anthropic_ping_ok(monkeypatch):
    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _FakeClient)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is True
    assert "claude-haiku-4-5" in r.detail


def test_check_anthropic_error_is_caught(monkeypatch):
    class _Boom:
        def __init__(self, *a):
            raise RuntimeError("bad key")

    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _Boom)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is False
    assert "bad key" in r.detail


def test_check_anthropic_empty_response(monkeypatch):
    class _EmptyClient:
        def __init__(self, api_key, default_model):
            pass

        def complete(self, system, prompt, *, model=None):
            return "   "

    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _EmptyClient)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is False
    assert "empty response" in r.detail
