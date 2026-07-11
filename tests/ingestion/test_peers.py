from saturn.ingestion import peers
from saturn.models import IndustryContext, PeerSummary


def test_peers_for_matches_semiconductor_industry():
    got = peers._peers_for("Semiconductors")
    assert ("NVDA", "demand") in got and ("AMAT", "supply") in got


def test_peers_for_unmapped_industry_is_empty():
    assert peers._peers_for("Restaurants") == []


def test_peers_for_ticker_fallback_when_industry_missing():
    # industry not populated (identity absent) -> known semi ticker still maps to the chain
    assert ("NVDA", "demand") in peers._peers_for(None, "MU")
    assert peers._peers_for(None, "AAPL") == []   # not a semi ticker


def test_fetch_industry_context_excludes_self_and_skips_failures(monkeypatch):
    # stub the per-peer summary: NVDA succeeds, AMD returns None (skipped), MU is the target
    def fake_summary(ticker, role):
        return None if ticker == "AMD" else PeerSummary(ticker=ticker, role=role, revenue_growth_yoy=0.5,
                                                        provenance=peers.Provenance(source="SEC EDGAR"))
    monkeypatch.setattr(peers, "_peer_summary", fake_summary)
    ic = peers.fetch_industry_context("MU", "Semiconductors")
    tickers = {p.ticker for p in ic.peers}
    assert "MU" not in tickers          # self excluded
    assert "AMD" not in tickers         # None skipped
    assert "NVDA" in tickers and isinstance(ic, IndustryContext) and ic.note


def test_fetch_industry_context_no_peers_raises(monkeypatch):
    from saturn.ingestion.errors import DataUnavailable
    import pytest
    monkeypatch.setattr(peers, "_peer_summary", lambda t, r: None)
    with pytest.raises(DataUnavailable):
        peers.fetch_industry_context("MU", "Semiconductors")
