import json
from datetime import date
from pathlib import Path

from saturn.ingestion.fred import FRED_SERIES, _parse_observations

FIX = Path(__file__).parent.parent / "fixtures" / "fred"


def _raw():
    return json.loads((FIX / "observations_FEDFUNDS.json").read_text(encoding="utf-8"))


def test_parse_skips_missing_values_and_sorts_ascending():
    obs = _parse_observations(_raw())
    # the "." value on 2026-02-01 is dropped
    assert (date(2026, 2, 1), 0.0) not in obs
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
