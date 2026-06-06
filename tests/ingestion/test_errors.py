import pytest

from saturn.ingestion.errors import DataUnavailable, IngestionError, SourceFailure


def test_subclasses_of_ingestion_error():
    assert issubclass(DataUnavailable, IngestionError)
    assert issubclass(SourceFailure, IngestionError)


def test_prices_reexports_same_class():
    from saturn.ingestion.prices import IngestionError as PricesIngestionError

    assert PricesIngestionError is IngestionError


def test_exceptions_raise_and_carry_message():
    for exc_type in (IngestionError, DataUnavailable, SourceFailure):
        with pytest.raises(IngestionError) as info:
            raise exc_type("boom")
        assert "boom" in str(info.value)
