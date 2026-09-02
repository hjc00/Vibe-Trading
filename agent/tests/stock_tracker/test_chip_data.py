"""Unit tests for the stock tracker chip-concentration loader."""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.stock_tracker.chip_data import (
    ChipDataCache,
    compute_chip_concentration_score,
    compute_holder_trend,
    load_chip_data,
)
from src.stock_tracker.models import ChipSnapshot


# ---------------------------------------------------------------------------
# compute_chip_concentration_score
# ---------------------------------------------------------------------------


def test_chip_score_weighted():
    # holder -15% -> 100 (w 0.4); avg_hold 0% -> 0 (w 0.3); northbound 0% -> 0
    # (w 0.3). score = 0.4*100 = 40.0.
    assert compute_chip_concentration_score(-15.0, 0.0, 0.0) == pytest.approx(40.0)


def test_chip_score_single_dimension_renormalized():
    assert compute_chip_concentration_score(-15.0, None, None) == pytest.approx(100.0)
    assert compute_chip_concentration_score(None, 30.0, None) == pytest.approx(100.0)
    assert compute_chip_concentration_score(None, None, 20.0) == pytest.approx(100.0)


def test_chip_score_clamps_bounds():
    # More negative holder change (more accumulation) clamps at 100.
    assert compute_chip_concentration_score(-30.0, None, None) == pytest.approx(100.0)
    # Positive holder change (dispersion) clamps at 0.
    assert compute_chip_concentration_score(5.0, None, None) == pytest.approx(0.0)
    assert compute_chip_concentration_score(None, -10.0, None) == pytest.approx(0.0)
    assert compute_chip_concentration_score(None, None, -5.0) == pytest.approx(0.0)


def test_chip_score_all_none():
    assert compute_chip_concentration_score(None, None, None) is None


# ---------------------------------------------------------------------------
# compute_holder_trend
# ---------------------------------------------------------------------------


def test_holder_trend_accumulating():
    assert compute_holder_trend([100.0, 90.0, 80.0]) == "accumulating"


def test_holder_trend_distributing():
    assert compute_holder_trend([80.0, 90.0, 100.0]) == "distributing"


def test_holder_trend_mixed_returns_none():
    assert compute_holder_trend([100.0, 90.0, 95.0]) is None


def test_holder_trend_needs_three_points():
    assert compute_holder_trend([100.0, 90.0]) is None
    assert compute_holder_trend([]) is None


def test_holder_trend_ignores_none():
    assert compute_holder_trend([None, 100.0, 90.0, 80.0]) == "accumulating"


# ---------------------------------------------------------------------------
# ChipDataCache
# ---------------------------------------------------------------------------


class TestChipDataCache:
    def test_cache_returns_value_within_ttl(self):
        cache = ChipDataCache(ttl_seconds=60)
        snapshot = ChipSnapshot(holder_count=12345, source="eastmoney")
        cache.set("600519.SH", date.today(), snapshot)
        assert cache.get("600519.SH", date.today()) is snapshot

    def test_cache_expires_after_ttl(self):
        cache = ChipDataCache(ttl_seconds=0.01)
        cache.set("600519.SH", date.today(), ChipSnapshot())
        time.sleep(0.02)
        assert cache.get("600519.SH", date.today()) is None

    def test_cache_key_includes_trading_date(self):
        cache = ChipDataCache(ttl_seconds=60)
        today = date.today()
        cache.set("600519.SH", today, ChipSnapshot(holder_count=12345))
        assert cache.get("600519.SH", today - timedelta(days=1)) is None
        assert cache.get("600519.SH", today) is not None


# ---------------------------------------------------------------------------
# load_chip_data
# ---------------------------------------------------------------------------


class TestLoadChipData:
    def test_isolates_symbol_errors(self):
        def _fake(code):
            if code == "600519.SH":
                return ChipSnapshot(holder_count=12345, source="eastmoney")
            return ChipSnapshot(error="no shareholder-count disclosure")

        with patch("src.stock_tracker.chip_data._fetch_one_chip", side_effect=_fake):
            result = load_chip_data(["600519.SH", "000001.SZ"], end_date=date(2026, 8, 31))

        assert result["600519.SH"].holder_count == 12345
        assert result["000001.SZ"].error is not None

    def test_caches_successes_only(self):
        cache = ChipDataCache(ttl_seconds=60)
        snapshot = ChipSnapshot(holder_count=12345, source="eastmoney")

        with patch("src.stock_tracker.chip_data._fetch_one_chip", return_value=snapshot) as mock:
            load_chip_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert cache.get("600519.SH", date(2026, 8, 31)) is not None

            mock.reset_mock()
            result = load_chip_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert result["600519.SH"].holder_count == 12345
            mock.assert_not_called()

    def test_does_not_cache_errors(self):
        cache = ChipDataCache(ttl_seconds=60)
        failed = ChipSnapshot(error="no shareholder-count disclosure")

        with patch("src.stock_tracker.chip_data._fetch_one_chip", return_value=failed) as mock:
            load_chip_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert cache.get("600519.SH", date(2026, 8, 31)) is None
            assert mock.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
