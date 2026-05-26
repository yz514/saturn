from saturn.ingestion.prices import IngestionError, fetch_company_data
from saturn.models import CompanyData


def test_fetch_mock_returns_nvidia_fixture():
    c = fetch_company_data("NVDA", mock=True)
    assert isinstance(c, CompanyData)
    assert c.ticker == "NVDA"
    assert c.name == "NVIDIA Corporation"
    assert "Data Center" in c.segments
    assert c.price is not None
    assert c.news and c.news[0].title.startswith("[MOCK]")


def test_fetch_mock_preserves_ticker_case():
    c = fetch_company_data("msft", mock=True)
    assert c.ticker == "msft"


def test_ingestion_error_is_runtime_error():
    assert issubclass(IngestionError, RuntimeError)
