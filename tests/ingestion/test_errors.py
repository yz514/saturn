from saturn.ingestion.errors import DataUnavailable, IngestionError, SourceFailure


def test_subclasses_of_ingestion_error():
    assert issubclass(DataUnavailable, IngestionError)
    assert issubclass(SourceFailure, IngestionError)


def test_prices_reexports_same_class():
    from saturn.ingestion.prices import IngestionError as PricesIngestionError

    assert PricesIngestionError is IngestionError
