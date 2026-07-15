from datetime import date as _date

from saturn.ingestion.consensus import RawConsensus, validate_consensus, _ntm_weight, _blend_ntm
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


def test_forward_eps_passes_for_real_hypergrowth_via_runrate():
    # MU-like: latest quarter EPS 24.67 -> run-rate ~98.7. A forward of 150 is only
    # +52% above the current run-rate -> PASS. (Against the stale FY2025 EPS of 7.59
    # it would be +1876%, which the old baseline wrongly rejected.)
    fund = _fund({"FY2025": 7.59, "Q3 FY2026": 24.67, "Q2 FY2026": 12.2,
                  "Q1 FY2026": 4.6, "Q3 FY2025": 1.7})
    raw = RawConsensus(forward_eps=150.0, forward_pe=1154.0 / 150.0, n_analysts=40)
    c = validate_consensus(raw, fund, _quote(1154.0))
    assert c.forward_eps == 150.0
    assert not any("forward_eps" in r for r in c.rejected)


def test_forward_eps_rejected_for_split_contamination_via_runrate():
    # AVGO-like: latest quarter EPS 1.91 -> run-rate ~7.64. A forward of 19.40 is
    # +154% above the current run-rate -> still REJECT (the split scrambled it).
    fund = _fund({"FY2025": 4.77, "Q2 FY2026": 1.91, "Q1 FY2026": 1.7,
                  "Q4 FY2025": 1.6, "Q3 FY2025": 1.5})
    raw = RawConsensus(forward_eps=19.40, forward_pe=380.0 / 19.40, n_analysts=45)
    c = validate_consensus(raw, fund, _quote(380.0))
    assert c.forward_eps is None
    assert any("forward_eps" in r for r in c.rejected)


def test_nonpositive_price_rejects_forward_eps():
    raw = RawConsensus(forward_eps=9.0, forward_pe=11.0, target_mean=110.0,
                       target_high=120.0, target_low=100.0, n_analysts=10)
    c = validate_consensus(raw, _fund({"FY2024": 8.0}), _quote(0.0))
    assert c.forward_eps is None and c.forward_pe is None
    assert any("forward_eps" in r for r in c.rejected)


def test_zero_estimate_surprise_rejected_with_reason():
    raw = RawConsensus(last_actual_eps=2.0, last_estimate_eps=0.0)
    c = validate_consensus(raw, _fund({"FY2024": 5.0}), _quote(100.0))
    assert c.last_eps_surprise_pct is None
    assert any("eps_surprise" in r and "zero" in r for r in c.rejected)


def test_fetch_consensus_maps_info_fields(monkeypatch):
    import saturn.ingestion.consensus as cons

    class _FakeTicker:
        def __init__(self, t): pass
        @property
        def info(self):
            return {"forwardEps": 9.6, "forwardPE": 30.6, "pegRatio": 2.4,
                    "targetMeanPrice": 314.0, "targetHighPrice": 360.0, "targetLowPrice": 250.0,
                    "recommendationKey": "buy", "numberOfAnalystOpinions": 42}
        @property
        def earnings_history(self):
            return None

    import types
    monkeypatch.setattr(cons, "yf", types.SimpleNamespace(Ticker=_FakeTicker), raising=False)
    raw = cons.fetch_consensus("AAPL")
    assert raw.forward_eps == 9.6 and raw.forward_pe == 30.6 and raw.peg == 2.4
    assert raw.target_mean == 314.0 and raw.rating == "buy" and raw.n_analysts == 42


def test_fetch_consensus_reads_both_years_and_fiscal_year_end(monkeypatch):
    import pandas as pd
    from saturn.ingestion import consensus as C
    rev_df = pd.DataFrame({"avg": [70e9, 84e9]}, index=["0y", "+1y"])
    eps_df = pd.DataFrame({"avg": [5.5, 6.6]}, index=["0y", "+1y"])

    class _T:
        info = {"forwardEps": 6.6, "nextFiscalYearEnd": 1798675200}   # 2026-12-31 UTC
        earnings_history = None
        revenue_estimate = rev_df
        earnings_estimate = eps_df

    monkeypatch.setattr(C, "yf", type("YF", (), {"Ticker": staticmethod(lambda t: _T())}))
    raw = C.fetch_consensus("X")
    assert raw.rev_fy0 == 70e9 and raw.rev_fy1 == 84e9
    assert raw.eps_fy0 == 5.5 and raw.eps_fy1 == 6.6
    assert raw.fy0_end is not None and raw.fy0_end.year == 2026


def test_fetch_consensus_estimate_sources_defensive(monkeypatch):
    from saturn.ingestion import consensus as C

    class _T:
        info = {}
        earnings_history = None

        @property
        def revenue_estimate(self):
            raise RuntimeError("analysis endpoint down")

    monkeypatch.setattr(C, "yf", type("YF", (), {"Ticker": staticmethod(lambda t: _T())}))
    raw = C.fetch_consensus("X")          # must not raise
    assert raw.rev_fy0 is None and raw.rev_fy1 is None
    assert raw.eps_fy0 is None and raw.eps_fy1 is None
    assert raw.fy0_end is None


def _rev_fund(eps=4.5, rev=90e9, shares=10e9):
    return Fundamentals(facts=[
        FinancialFact(concept="EarningsPerShareDiluted", value=eps, unit="USD/shares", fiscal_period="FY2024", provenance=PROV),
        FinancialFact(concept="Revenues", value=rev, unit="USD", fiscal_period="FY2024", provenance=PROV),
        FinancialFact(concept="WeightedAverageSharesDiluted", value=shares, unit="shares", fiscal_period="FY2024", provenance=PROV)])


def test_forward_revenue_accepted_when_consistent():
    # NTM EPS 5.0 x 10e9 shares / 100e9 rev = 0.5 margin (ok); 100/90-1 = +11% growth (ok).
    # The revenue + current-FY EPS are accepted together as a horizon-matched pair.
    raw = RawConsensus(forward_eps=6.2, forward_eps_ntm=5.0, forward_revenue=100e9)
    c = validate_consensus(raw, _rev_fund(), _quote(50.0))
    assert c.forward_revenue == 100e9 and c.forward_eps_ntm == 5.0
    assert not any("forward_revenue" in r for r in c.rejected)


def test_forward_revenue_rejected_when_implausible():
    # fr 40e9 -> implied margin 50e9/40e9 = 1.25 (>0.6) -> rejected (both fields left None)
    raw = RawConsensus(forward_eps=6.2, forward_eps_ntm=5.0, forward_revenue=40e9)
    c = validate_consensus(raw, _rev_fund(), _quote(50.0))
    assert c.forward_revenue is None and c.forward_eps_ntm is None
    assert any("forward_revenue" in r for r in c.rejected)


def test_forward_revenue_no_baseline_rejected():
    # no Revenues/shares facts -> cannot validate
    raw = RawConsensus(forward_eps=6.2, forward_eps_ntm=5.0, forward_revenue=100e9)
    fund = Fundamentals(facts=[FinancialFact(
        concept="EarningsPerShareDiluted", value=4.5, unit="USD/shares", fiscal_period="FY2024", provenance=PROV)])
    c = validate_consensus(raw, fund, _quote(50.0))
    assert c.forward_revenue is None
    assert any("no baseline" in r for r in c.rejected)


def test_forward_revenue_needs_ntm_eps_not_anchor_eps():
    # The revenue gate is horizon-matched: it validates against the current-FY (NTM) EPS, not the
    # forward (FY+1) anchor EPS. A valid anchor forward_eps alone does not admit the revenue.
    raw = RawConsensus(forward_eps=5.0, forward_revenue=100e9)  # no forward_eps_ntm
    c = validate_consensus(raw, _rev_fund(), _quote(50.0))
    assert c.forward_eps == 5.0             # anchor EPS still validated for the forward P/E
    assert c.forward_revenue is None        # revenue not accepted without an NTM EPS baseline
    assert c.forward_eps_ntm is None
    assert any("no baseline" in r for r in c.rejected)


def test_ntm_weight_zero_when_fiscal_year_already_ended():
    # MSFT case: FY0 ended 2026-06-30, today 2026-07-15 -> nothing of FY0 remains
    assert _ntm_weight(_date(2026, 6, 30), _date(2026, 7, 15)) == 0.0


def test_ntm_weight_mid_year():
    # AMZN case: FY0 ends 2026-12-31, today 2026-07-15 -> ~5.6 months left -> ~0.46
    w = _ntm_weight(_date(2026, 12, 31), _date(2026, 7, 15))
    assert 0.45 < w < 0.48


def test_ntm_weight_clamps_to_one_beyond_twelve_months():
    assert _ntm_weight(_date(2028, 1, 1), _date(2026, 7, 15)) == 1.0


def test_ntm_weight_none_without_fiscal_year_end():
    assert _ntm_weight(None, _date(2026, 7, 15)) is None


def test_blend_ntm_endpoints_and_midpoint():
    assert _blend_ntm(0.0, 8.66, 9.88) == 9.88          # FY0 elapsed -> pure FY1
    assert _blend_ntm(1.0, 8.66, 9.88) == 8.66          # FY0 entirely ahead -> pure FY0
    assert abs(_blend_ntm(0.4627, 8.66, 9.88) - 9.32) < 0.01


def test_blend_ntm_none_when_any_input_missing():
    assert _blend_ntm(None, 8.66, 9.88) is None
    assert _blend_ntm(0.5, None, 9.88) is None
    assert _blend_ntm(0.5, 8.66, None) is None
