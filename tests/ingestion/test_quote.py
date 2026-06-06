from saturn.ingestion.prices import _mock_quote, fetch_quote
from saturn.models import Quote


def test_mock_quote_shape():
    q = _mock_quote("NVDA")
    assert isinstance(q, Quote)
    assert q.price is not None
    assert q.provenance.source == "yfinance (mock)"


def test_fetch_quote_mock_path():
    q = fetch_quote("ANYTHING", mock=True)
    assert isinstance(q, Quote)
    assert q.currency == "USD"
    assert q.provenance.source == "yfinance (mock)"
