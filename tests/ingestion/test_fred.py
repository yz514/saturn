import json
from datetime import date
from pathlib import Path

import pytest

from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.fred import FRED_SERIES, _parse_observations, fetch_fred
from saturn.models import MacroSnapshot

FIX = Path(__file__).parent.parent / "fixtures" / "fred"


def _raw():
    return json.loads((FIX / "observations_FEDFUNDS.json").read_text(encoding="utf-8"))


def test_parse_skips_missing_values_and_sorts_ascending():
    obs = _parse_observations(_raw())
    # the "." value on 2026-02-01 is dropped entirely
    assert not any(d == date(2026, 2, 1) for d, _ in obs)
    assert len(obs) == 3  # 4 input rows minus the one "." row
    assert obs == sorted(obs, key=lambda t: t[0])
    assert obs[-1] == (date(2026, 4, 1), 4.25)


def test_parse_returns_date_float_tuples():
    obs = _parse_observations(_raw())
    d, v = obs[0]
    assert isinstance(d, date)
    assert isinstance(v, float)


def test_registry_includes_core_series():
    ids = {s[0] for s in FRED_SERIES}
    assert {"FEDFUNDS", "CPIAUCSL", "DGS10", "DGS2", "UNRATE", "M2SL"} <= ids


def test_parse_handles_empty_and_missing_observations():
    assert _parse_observations({"observations": []}) == []
    assert _parse_observations({}) == []


def test_fetch_fred_builds_snapshot_with_provenance(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "testkey")

    def fake_fetch(series_id, api_key):
        return {"observations": [{"date": "2026-04-01", "value": "1.5"}]}

    snap = fetch_fred("NVDA", fetch=fake_fetch)
    assert isinstance(snap, MacroSnapshot)
    assert len(snap.series) == len(FRED_SERIES)
    s0 = snap.series[0]
    assert s0.observations[-1][1] == 1.5
    assert s0.provenance.source == "FRED"
    assert s0.title  # human title from the registry


def test_fetch_fred_ignores_ticker(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "testkey")
    snap = fetch_fred("ANYTHING", fetch=lambda sid, api_key: {"observations": []})
    assert isinstance(snap, MacroSnapshot)


def test_fetch_fred_without_key_raises_data_unavailable(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(DataUnavailable):
        fetch_fred("NVDA", fetch=lambda sid, api_key: {"observations": []})
