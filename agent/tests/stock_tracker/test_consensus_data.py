"""Unit tests for the stock tracker consensus loader."""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.stock_tracker.consensus_data import (
    ConsensusDataCache,
    _compute_eps_revision,
    compute_forward_metrics,
    compute_rating_score,
    load_consensus_data,
)
from src.stock_tracker.models import ConsensusSnapshot


# ---------------------------------------------------------------------------
# compute_rating_score
# ---------------------------------------------------------------------------


def test_rating_score_weighted_mix():
    # 买入 x2 (100) + 中性 x1 (50) -> 250/3 = 83.33.
    assert compute_rating_score({"买入": 2, "中性": 1}) == pytest.approx(83.33, abs=0.01)


def test_rating_score_english_labels():
    assert compute_rating_score({"Buy": 1, "Sell": 1}) == pytest.approx(50.0)


def test_rating_score_empty_or_nonpositive():
    assert compute_rating_score({}) is None
    assert compute_rating_score({"买入": 0}) is None


def test_rating_score_ignores_invalid_counts():
    assert compute_rating_score({"买入": "x", "增持": 1}) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# compute_forward_metrics
# ---------------------------------------------------------------------------


def test_forward_metrics_computes_pe_and_upside():
    snap = ConsensusSnapshot(consensus_eps_next=10.0, target_price_avg=150.0)
    compute_forward_metrics(snap, close=100.0)
    assert snap.forward_pe == pytest.approx(10.0)
    assert snap.upside_pct == pytest.approx(0.5)


def test_forward_metrics_no_close_is_noop():
    snap = ConsensusSnapshot(consensus_eps_next=10.0, target_price_avg=150.0)
    compute_forward_metrics(snap, None)
    assert snap.forward_pe is None
    assert snap.upside_pct is None


def test_forward_metrics_missing_fields_is_noop():
    snap = ConsensusSnapshot()
    compute_forward_metrics(snap, 100.0)
    assert snap.forward_pe is None
    assert snap.upside_pct is None


# ---------------------------------------------------------------------------
# _compute_eps_revision
# ---------------------------------------------------------------------------


def test_eps_revision_averages_broker_change():
    rows = [
        {"org_name": "中信", "report_date": "2026-01-01", "eps": 1.0},
        {"org_name": "中信", "report_date": "2026-02-01", "eps": 1.1},
        {"org_name": "华泰", "report_date": "2026-01-01", "eps": 2.0},
        {"org_name": "华泰", "report_date": "2026-02-01", "eps": 2.4},
    ]
    # 中信: (1.1-1.0)/1.0 = 10%; 华泰: (2.4-2.0)/2.0 = 20%; mean 15%.
    assert _compute_eps_revision(rows) == pytest.approx(15.0)


def test_eps_revision_needs_two_forecasts():
    rows = [{"org_name": "中信", "report_date": "2026-01-01", "eps": 1.0}]
    assert _compute_eps_revision(rows) is None


# ---------------------------------------------------------------------------
# ConsensusDataCache
# ---------------------------------------------------------------------------


class TestConsensusDataCache:
    def test_cache_returns_value_within_ttl(self):
        cache = ConsensusDataCache(ttl_seconds=60)
        snapshot = ConsensusSnapshot(analyst_count=5, source="eastmoney+ths")
        cache.set("600519.SH", date.today(), snapshot)
        assert cache.get("600519.SH", date.today()) is snapshot

    def test_cache_expires_after_ttl(self):
        cache = ConsensusDataCache(ttl_seconds=0.01)
        cache.set("600519.SH", date.today(), ConsensusSnapshot())
        time.sleep(0.02)
        assert cache.get("600519.SH", date.today()) is None

    def test_cache_key_includes_trading_date(self):
        cache = ConsensusDataCache(ttl_seconds=60)
        today = date.today()
        cache.set("600519.SH", today, ConsensusSnapshot(analyst_count=5))
        assert cache.get("600519.SH", today - timedelta(days=1)) is None
        assert cache.get("600519.SH", today) is not None


# ---------------------------------------------------------------------------
# load_consensus_data
# ---------------------------------------------------------------------------


class TestLoadConsensusData:
    def test_isolates_symbol_errors(self):
        def _fake(code):
            if code == "600519.SH":
                return ConsensusSnapshot(analyst_count=5, source="eastmoney+ths")
            return ConsensusSnapshot(error="no analyst coverage")

        with patch("src.stock_tracker.consensus_data._fetch_one_consensus", side_effect=_fake):
            result = load_consensus_data(["600519.SH", "000001.SZ"], end_date=date(2026, 8, 31))

        assert result["600519.SH"].analyst_count == 5
        assert result["000001.SZ"].error == "no analyst coverage"

    def test_caches_successes_only(self):
        cache = ConsensusDataCache(ttl_seconds=60)
        snapshot = ConsensusSnapshot(analyst_count=5, source="eastmoney+ths")

        with patch("src.stock_tracker.consensus_data._fetch_one_consensus", return_value=snapshot) as mock:
            load_consensus_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert cache.get("600519.SH", date(2026, 8, 31)) is not None

            mock.reset_mock()
            result = load_consensus_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert result["600519.SH"].analyst_count == 5
            mock.assert_not_called()

    def test_does_not_cache_errors(self):
        cache = ConsensusDataCache(ttl_seconds=60)
        failed = ConsensusSnapshot(error="no analyst coverage")

        with patch("src.stock_tracker.consensus_data._fetch_one_consensus", return_value=failed) as mock:
            load_consensus_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert cache.get("600519.SH", date(2026, 8, 31)) is None
            assert mock.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
