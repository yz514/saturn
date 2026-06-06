from saturn.ingestion.dispatch import route_to_source
from saturn.ingestion.errors import DataUnavailable, SourceFailure
from saturn.models import SourceGap


def test_success_returns_value_and_no_gap():
    result, gap = route_to_source("edgar", lambda: {"ok": 1})
    assert result == {"ok": 1}
    assert gap is None


def test_data_unavailable_becomes_gap():
    def boom():
        raise DataUnavailable("no CIK for ZZZZ")

    result, gap = route_to_source("edgar", boom)
    assert result is None
    assert isinstance(gap, SourceGap)
    assert gap.source == "edgar"
    assert "no CIK" in gap.reason


def test_source_failure_becomes_gap():
    def boom():
        raise SourceFailure("connection reset")

    result, gap = route_to_source("fred", boom)
    assert result is None
    assert gap.source == "fred"
    assert "connection reset" in gap.reason


def test_unexpected_error_also_becomes_gap():
    def boom():
        raise ValueError("surprise")

    result, gap = route_to_source("fred", boom)
    assert result is None
    assert gap.source == "fred"
    assert "surprise" in gap.reason
