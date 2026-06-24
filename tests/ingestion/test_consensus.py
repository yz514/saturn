from saturn.ingestion.consensus import RawConsensus, validate_consensus
from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

PROV = Provenance(source="SEC EDGAR")


def _fund(eps_by_fy):
    return Fundamentals(facts=[
        FinancialFact(concept="EarningsPerShareDiluted", value=v, unit="USD/shares",
                      fiscal_period=p, provenance=PROV)
        for p, v in eps_by_fy.items()
    ])


def _quote(price):
    return Quote(price=price, market_cap=None, currency="USD", provenance=Provenance(source="yfinance"))


def test_clean_case_all_fields_pass():
    raw = RawConsensus(forward_eps=9.60, forward_pe=30.66, peg=2.4,
                       target_mean=314.0, target_high=360.0, target_low=250.0, rating="buy",
                       n_analysts=42, last_actual_eps=2.40, last_estimate_eps=2.35)
    c = validate_consensus(raw, _fund({"FY2024": 8.27}), _quote(294.3))
    assert c.forward_eps == 9.60 and abs(c.forward_pe - 30.66) < 1e-6
    assert abs(c.target_upside_pct - (314.0 / 294.3 - 1)) < 1e-9
    assert c.rating == "buy" and c.n_analysts == 42
    assert c.last_eps_surprise_pct is not None
    assert c.rejected == []
    assert c.provenance.source == "yfinance (estimate)"


def test_avgo_split_case_rejects_eps_keeps_targets():
    # forward EPS 19.35 vs verified trailing 4.80 -> +303% -> EPS trio rejected;
    # price targets are sane and survive (per-field granularity).
    raw = RawConsensus(forward_eps=19.35, forward_pe=19.7, peg=0.7,
                       target_mean=522.0, target_high=650.0, target_low=216.0,
                       rating="strong_buy", n_analysts=45)
    c = validate_consensus(raw, _fund({"FY2025": 4.80}), _quote(380.0))
    assert c.forward_eps is None and c.forward_pe is None and c.peg is None
    assert any("forward_eps" in r for r in c.rejected)
    assert c.target_mean == 522.0 and c.rating == "strong_buy"


def test_internal_inconsistency_rejects_eps_trio():
    # forward_pe disagrees with price/forward_eps -> reject
    raw = RawConsensus(forward_eps=10.0, forward_pe=50.0, target_mean=None, n_analysts=10)
    c = validate_consensus(raw, _fund({"FY2024": 9.0}), _quote(100.0))  # implied pe = 10
    assert c.forward_pe is None
    assert any("forward_pe" in r or "forward_eps" in r for r in c.rejected)


def test_target_out_of_band_dropped():
    raw = RawConsensus(target_mean=5000.0, target_high=6000.0, target_low=4000.0, n_analysts=10)
    c = validate_consensus(raw, _fund({"FY2024": 5.0}), _quote(100.0))  # 5000 > 5x*100
    assert c.target_mean is None and any("price_target" in r for r in c.rejected)


def test_too_few_analysts_drops_targets_and_rating():
    raw = RawConsensus(target_mean=110.0, target_high=120.0, target_low=100.0,
                       rating="buy", n_analysts=2)
    c = validate_consensus(raw, _fund({"FY2024": 5.0}), _quote(100.0))
    assert c.target_mean is None and c.rating is None


def test_absurd_surprise_dropped():
    raw = RawConsensus(last_actual_eps=10.0, last_estimate_eps=1.0)  # +900%
    c = validate_consensus(raw, _fund({"FY2024": 5.0}), _quote(100.0))
    assert c.last_eps_surprise_pct is None and any("surprise" in r for r in c.rejected)


def test_missing_baseline_rejects_eps_but_keeps_targets():
    raw = RawConsensus(forward_eps=9.0, forward_pe=11.0,
                       target_mean=110.0, target_high=120.0, target_low=100.0, n_analysts=10)
    c = validate_consensus(raw, _fund({}), _quote(100.0))  # no EPS fact
    assert c.forward_eps is None and any("forward_eps" in r for r in c.rejected)
    assert c.target_mean == 110.0


def test_negative_trailing_eps_rejects_forward():
    raw = RawConsensus(forward_eps=2.0, forward_pe=50.0)
    c = validate_consensus(raw, _fund({"FY2024": -1.0}), _quote(100.0))
    assert c.forward_eps is None and any("forward_eps" in r for r in c.rejected)
