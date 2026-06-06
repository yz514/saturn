import pytest

from saturn.ingestion.errors import SourceFailure
from saturn.ingestion import http


def test_http_get_wraps_transport_errors_as_source_failure(monkeypatch):
    def boom(req, timeout):  # signature of urllib.request.urlopen(req, timeout=...)
        raise OSError("connection refused")

    monkeypatch.setattr(http.request, "urlopen", boom)
    with pytest.raises(SourceFailure):
        http.http_get("https://example.com/x", user_agent="Saturn test@example.com")


def test_http_get_returns_body_and_sets_user_agent(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": 1}'

    def fake_urlopen(req, timeout):
        captured["ua"] = req.get_header("User-agent")
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(http.request, "urlopen", fake_urlopen)
    body = http.http_get("https://example.com/x", user_agent="Saturn test@example.com")
    assert body == b'{"ok": 1}'
    assert captured["ua"] == "Saturn test@example.com"
    assert captured["url"] == "https://example.com/x"
