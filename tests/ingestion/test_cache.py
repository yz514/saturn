from datetime import date

import pytest

from saturn.ingestion.cache import read_cache, write_cache


def test_write_then_read_roundtrip(tmp_path):
    payload = {"hello": "world", "n": 1}
    write_cache("edgar", "NVDA", payload, root=tmp_path, today=date(2026, 6, 6))
    got = read_cache(
        "edgar", "NVDA", ttl_days=30, root=tmp_path, today=date(2026, 6, 6)
    )
    assert got == payload


def test_miss_returns_none(tmp_path):
    got = read_cache("edgar", "MSFT", ttl_days=30, root=tmp_path, today=date(2026, 6, 6))
    assert got is None


def test_expired_entry_is_a_miss(tmp_path):
    write_cache("fred", "MACRO", {"x": 1}, root=tmp_path, today=date(2026, 6, 1))
    # 5 days later with a 1-day TTL -> expired.
    got = read_cache(
        "fred", "MACRO", ttl_days=1, root=tmp_path, today=date(2026, 6, 6)
    )
    assert got is None


def test_fresh_entry_within_ttl_hits(tmp_path):
    write_cache("fred", "MACRO", {"x": 1}, root=tmp_path, today=date(2026, 6, 6))
    got = read_cache(
        "fred", "MACRO", ttl_days=1, root=tmp_path, today=date(2026, 6, 6)
    )
    assert got == {"x": 1}


def test_freshest_in_window_wins(tmp_path):
    write_cache("edgar", "NVDA", {"v": "old"}, root=tmp_path, today=date(2026, 6, 2))
    write_cache("edgar", "NVDA", {"v": "new"}, root=tmp_path, today=date(2026, 6, 5))
    got = read_cache("edgar", "NVDA", ttl_days=30, root=tmp_path, today=date(2026, 6, 6))
    assert got == {"v": "new"}


def test_future_dated_file_is_a_miss(tmp_path):
    # Written "in the future" relative to the read date -> age < 0 -> miss.
    write_cache("fred", "MACRO", {"x": 1}, root=tmp_path, today=date(2026, 6, 10))
    got = read_cache("fred", "MACRO", ttl_days=30, root=tmp_path, today=date(2026, 6, 6))
    assert got is None


def test_negative_ttl_raises(tmp_path):
    with pytest.raises(ValueError):
        read_cache("fred", "MACRO", ttl_days=-1, root=tmp_path, today=date(2026, 6, 6))
