from saturn.ingestion.dossier import _mock_dossier, build_dossier
from saturn.models import CompanyDossier


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
        edgar_fn=None,   # not wired yet
        fred_fn=None,    # not wired yet
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
    from saturn.ingestion.edgar import fetch_edgar  # noqa: F401 (used to assert default)
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
    assert "fred" in {g.source for g in d.gaps}  # fred still unwired here
