import json
from pathlib import Path

import pytest

from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.identifiers import _parse_company_tickers, ticker_to_cik

FIXTURE = Path(__file__).parent.parent / "fixtures" / "edgar" / "company_tickers.json"


def _raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_maps_ticker_to_padded_cik():
    mapping = _parse_company_tickers(_raw())
    assert mapping["NVDA"] == "0001045810"   # zero-padded to 10 digits
    assert mapping["AAPL"] == "0000320193"


def test_parse_normalizes_lowercase_source_ticker():
    mapping = _parse_company_tickers({"0": {"cik_str": 789019, "ticker": "msft"}})
    assert mapping["MSFT"] == "0000789019"


def test_ticker_to_cik_uses_injected_fetcher():
    cik = ticker_to_cik("nvda", fetch=lambda: _raw())
    assert cik == "0001045810"


def test_ticker_to_cik_unknown_raises_data_unavailable():
    with pytest.raises(DataUnavailable):
        ticker_to_cik("ZZZZ", fetch=lambda: _raw())
