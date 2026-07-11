from saturn.ingestion.dossier import _mock_dossier, build_dossier
from saturn.models import CompanyDossier


# ---------------------------------------------------------------------------
# Task 3 tests: industry_context wired into build_dossier + mock
# ---------------------------------------------------------------------------

def test_mock_dossier_has_industry_context():
    """_mock_dossier must set a non-None industry_context with at least one peer."""
    from saturn.models import IndustryContext
    d = _mock_dossier("NVDA")
    assert d.industry_context is not None
    assert isinstance(d.industry_context, IndustryContext)
    assert len(d.industry_context.peers) >= 1


def test_build_dossier_mock_path_has_industry_context():
    """build_dossier(mock=True) must also return a dossier with industry_context."""
    from saturn.models import IndustryContext
    d = build_dossier("MU", mock=True)
    assert d.industry_context is not None
    assert isinstance(d.industry_context, IndustryContext)


def test_build_dossier_real_path_attaches_industry_context(monkeypatch):
    """Real path: monkeypatched fetch_industry_context returns canned IndustryContext."""
    from saturn.models import IndustryContext, PeerSummary, Provenance, Quote
    import saturn.ingestion.dossier as dossier_mod

    canned_ic = IndustryContext(
        peers=[PeerSummary(ticker="NVDA", role="demand", revenue_growth_yoy=0.6,
                           provenance=Provenance(source="SEC EDGAR"))],
        note="test note",
        provenance=Provenance(source="SEC EDGAR"),
    )
    monkeypatch.setattr(dossier_mod, "fetch_industry_context", lambda ticker, industry: canned_ic)

    d = build_dossier(
        "MU",
        mock=False,
        quote_fn=lambda t, *, mock: Quote(price=1.0, currency="USD", provenance=Provenance(source="yfinance")),
        edgar_fn=None,
        fred_fn=None,
        identity={"industry": "Semiconductors"},
    )
    assert d.industry_context is not None
    assert d.industry_context is canned_ic


def test_mock_dossier_is_rich():
    d = _mock_dossier("NVDA")
    assert isinstance(d, CompanyDossier)
    assert d.quote is not None and d.quote.price is not None
    assert d.fundamentals is not None and len(d.fundamentals.facts) >= 1
    assert d.macro is not None and len(d.macro.series) >= 1
    assert d.filing_sections and d.filing_sections[0].name
    # every datum is provenance-tagged
    assert d.quote.provenance.source
    assert d.fundamentals.facts[0].provenance.source
    assert d.macro.series[0].provenance.source


def test_build_dossier_mock_path_returns_mock():
    d = build_dossier("NVDA", mock=True)
    assert d.ticker == "NVDA"
    assert d.quote is not None


def test_build_dossier_real_path_quote_only_records_gaps():
    # Real path with quote stubbed to succeed and edgar/fred unavailable.
    from saturn.models import Provenance, Quote

    def fake_quote(ticker, *, mock):
        return Quote(price=1.0, currency="USD", provenance=Provenance(source="yfinance"))

    d = build_dossier(
        "NVDA",
        mock=False,
        quote_fn=fake_quote,
        edgar_fn=None,   # explicitly None -> recorded as a gap
        fred_fn=None,    # explicitly None -> recorded as a gap
        identity={"name": "NVIDIA Corporation"},
    )
    assert d.quote.price == 1.0
    assert d.fundamentals is None
    gap_sources = {g.source for g in d.gaps}
    assert "edgar" in gap_sources and "fred" in gap_sources


def test_build_dossier_records_gap_when_quote_fails():
    from saturn.ingestion.errors import SourceFailure

    def failing_quote(ticker, *, mock):
        raise SourceFailure("yahoo down")

    d = build_dossier(
        "NVDA", mock=False, quote_fn=failing_quote, edgar_fn=None, fred_fn=None
    )
    assert d.quote is None
    assert "quote" in {g.source for g in d.gaps}


def test_build_dossier_default_edgar_is_wired():
    from saturn.ingestion.edgar import fetch_edgar
    # The default edgar_fn IS the real fetch_edgar (verifies the wiring, not just the merge).
    assert build_dossier.__kwdefaults__["edgar_fn"] is fetch_edgar
    from saturn.models import Fundamentals, FinancialFact, Provenance, Quote

    def fake_edgar(ticker):
        return {
            "fundamentals": Fundamentals(
                facts=[FinancialFact(concept="Revenues", value=1.0, provenance=Provenance(source="SEC EDGAR"))]
            ),
            "filing_sections": [],
            "name": "NVIDIA CORP",
            "cik": "0001045810",
        }

    d = build_dossier(
        "NVDA",
        mock=False,
        quote_fn=lambda t, *, mock: Quote(price=1.0, provenance=Provenance(source="yfinance")),
        edgar_fn=fake_edgar,
        fred_fn=None,
    )
    assert d.name == "NVIDIA CORP"        # merged from edgar result
    assert d.cik == "0001045810"
    assert d.fundamentals.facts[0].concept == "Revenues"
    assert "fred" in {g.source for g in d.gaps}  # fred explicitly None here so it records a gap


def test_build_dossier_default_fred_is_wired():
    from saturn.ingestion.fred import fetch_fred
    from saturn.models import MacroSnapshot, MacroSeries, Provenance, Quote
    from datetime import date

    # The default fred_fn IS the real fetch_fred (verifies the wiring itself).
    assert build_dossier.__kwdefaults__["fred_fn"] is fetch_fred

    def fake_fred(ticker):
        return MacroSnapshot(
            series=[
                MacroSeries(
                    series_id="FEDFUNDS",
                    title="Federal Funds Effective Rate",
                    observations=[(date(2026, 4, 1), 4.25)],
                    provenance=Provenance(source="FRED"),
                )
            ]
        )

    d = build_dossier(
        "NVDA",
        mock=False,
        quote_fn=lambda t, *, mock: Quote(price=1.0, provenance=Provenance(source="yfinance")),
        edgar_fn=None,           # keep edgar a gap for this test
        fred_fn=fake_fred,
    )
    assert d.macro is not None
    assert d.macro.series[0].series_id == "FEDFUNDS"
    assert "edgar" in {g.source for g in d.gaps}


def test_build_dossier_unpacks_material_events():
    from datetime import date
    from saturn.models import MaterialEvent, Provenance, Quote

    def fake_edgar(ticker):
        return {
            "fundamentals": None,
            "filing_sections": [],
            "material_events": [
                MaterialEvent(filing_date=date(2024, 5, 22), item_codes=["2.02"], provenance=Provenance(source="SEC EDGAR"))
            ],
            "name": "NVIDIA CORP",
            "cik": "0001045810",
        }

    d = build_dossier(
        "NVDA", mock=False,
        quote_fn=lambda t, *, mock: Quote(price=1.0, provenance=Provenance(source="yfinance")),
        edgar_fn=fake_edgar, fred_fn=None,
    )
    assert len(d.material_events) == 1
    assert d.material_events[0].item_codes == ["2.02"]


def test_mock_dossier_has_quarterly_and_event():
    from saturn.ingestion.dossier import _mock_dossier
    d = _mock_dossier("NVDA")
    assert any(f.fiscal_period.startswith("Q") for f in d.fundamentals.facts)
    assert d.material_events and d.material_events[0].item_codes


def test_mock_dossier_has_derived_metrics():
    d = _mock_dossier("NVDA")
    names = {m.name for m in d.derived_metrics}
    assert "net_margin" in names                       # computed from mock fundamentals
    assert all(m.provenance.source == "Saturn (derived)" for m in d.derived_metrics)


def test_build_dossier_attaches_metrics():
    from saturn.ingestion.dossier import build_dossier
    from saturn.models import Fundamentals, FinancialFact, Provenance, Quote

    prov = Provenance(source="SEC EDGAR")
    fund = Fundamentals(facts=[
        FinancialFact(concept="Revenues", value=1000.0, unit="USD", fiscal_period="FY2025", provenance=prov),
        FinancialFact(concept="NetIncomeLoss", value=200.0, unit="USD", fiscal_period="FY2025", provenance=prov),
    ])
    quote = Quote(price=10.0, market_cap=5000.0, currency="USD", provenance=Provenance(source="yfinance"))
    d = build_dossier(
        "X",
        quote_fn=lambda t, *, mock: quote,
        edgar_fn=lambda t: {"fundamentals": fund, "filing_sections": [], "material_events": [], "name": "X", "cik": "1"},
        fred_fn=lambda t: None,
    )
    assert any(m.name == "net_margin" and m.fiscal_period == "FY2025" for m in d.derived_metrics)


def test_build_dossier_validates_and_attaches_consensus(monkeypatch):
    from saturn.models import Fundamentals, FinancialFact, Provenance, Quote
    from saturn.ingestion.consensus import RawConsensus

    prov = Provenance(source="SEC EDGAR")
    fund = Fundamentals(facts=[
        FinancialFact(concept="EarningsPerShareDiluted", value=8.27, unit="USD/shares",
                      fiscal_period="FY2024", provenance=prov),
    ])
    quote = Quote(price=294.3, market_cap=1.0, currency="USD", provenance=Provenance(source="yfinance"))
    raw = RawConsensus(forward_eps=9.6, forward_pe=30.66, target_mean=314.0,
                       target_high=360.0, target_low=250.0, rating="buy", n_analysts=42)
    monkeypatch.setattr("saturn.ingestion.dossier.fetch_consensus", lambda t: raw)

    d = build_dossier(
        "X",
        quote_fn=lambda t, *, mock: quote,
        edgar_fn=lambda t: {"fundamentals": fund, "filing_sections": [], "material_events": [], "name": "X", "cik": "1"},
        fred_fn=lambda t: None,
    )
    assert d.consensus is not None
    assert d.consensus.forward_eps == 9.6 and d.consensus.rating == "buy"
    assert d.consensus.provenance.source == "yfinance (estimate)"


def test_mock_dossier_has_consensus():
    d = _mock_dossier("NVDA")
    assert d.consensus is not None and d.consensus.rating is not None


def test_build_dossier_attaches_forward_model_metrics(monkeypatch):
    from saturn.models import Fundamentals, FinancialFact, Provenance, Quote

    prov = Provenance(source="SEC EDGAR")
    rows = []
    for i, fy in enumerate(["FY2022", "FY2023", "FY2024", "FY2025"]):
        rows += [("OperatingCashFlow", fy, 500.0 + 50.0 * i), ("CapitalExpenditures", fy, 50.0),
                 ("WeightedAverageSharesDiluted", fy, 100.0)]
    fund = Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=prov)
        for (c, p, v) in rows
    ])
    quote = Quote(price=10.0, market_cap=5000.0, currency="USD", provenance=Provenance(source="yfinance"))
    monkeypatch.setattr("saturn.ingestion.dossier.fetch_fred", lambda t: None)

    d = build_dossier(
        "X",
        quote_fn=lambda t, *, mock: quote,
        edgar_fn=lambda t: {"fundamentals": fund, "filing_sections": [], "material_events": [], "name": "X", "cik": "1"},
        fred_fn=lambda t: None,
    )
    model = [m for m in d.derived_metrics if m.provenance.source == "Saturn (model)"]
    assert any(m.name == "implied_fcf_growth" for m in model)
