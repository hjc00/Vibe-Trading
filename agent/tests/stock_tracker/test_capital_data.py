"""Tests for the stock tracker margin-trading data loader."""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.stock_tracker.capital_data import (
    CapitalDataCache,
    _parse_margin_rows,
    fetch_margin_trading_batch,
    load_capital_data,
)
from src.stock_tracker.models import CapitalMetrics, MarginHistoryItem


class TestCapitalDataCache:
    def test_cache_returns_value_within_ttl(self):
        cache = CapitalDataCache(ttl_seconds=60)
        metrics = CapitalMetrics()
        trading_date = date.today()
        cache.set("margin", "600519.SH", trading_date, metrics)
        assert cache.get("margin", "600519.SH", trading_date) is metrics

    def test_cache_expires_after_ttl(self):
        cache = CapitalDataCache(ttl_seconds=0.01)
        metrics = CapitalMetrics()
        trading_date = date.today()
        cache.set("margin", "600519.SH", trading_date, metrics)
        time.sleep(0.02)
        assert cache.get("margin", "600519.SH", trading_date) is None

    def test_cache_key_includes_trading_date(self):
        cache = CapitalDataCache(ttl_seconds=60)
        metrics = CapitalMetrics()
        today = date.today()
        yesterday = today - timedelta(days=1)
        cache.set("margin", "600519.SH", today, metrics)
        assert cache.get("margin", "600519.SH", yesterday) is None
        assert cache.get("margin", "600519.SH", today) is metrics

    def test_cache_clear(self):
        cache = CapitalDataCache()
        cache.set("margin", "600519.SH", date.today(), CapitalMetrics())
        cache.clear()
        assert cache.get("margin", "600519.SH", date.today()) is None


class TestParseRows:
    def test_parse_margin_rows_computes_change(self):
        rows = [
            {"trade_date": "2026-08-29", "financing_balance": 1100000.0, "margin_total_balance": 2100000.0},
            {"trade_date": "2026-08-28", "financing_balance": 1000000.0, "margin_total_balance": 2000000.0},
        ]
        snapshot = _parse_margin_rows(rows)
        assert snapshot.trade_date == date(2026, 8, 29)
        assert snapshot.financing_balance == 1100000.0
        assert snapshot.financing_balance_change == 100000.0
        assert snapshot.margin_total_change == 100000.0
        assert len(snapshot.history) == 2
        assert snapshot.history[0] == MarginHistoryItem(
            trade_date=date(2026, 8, 29),
            financing_balance=1100000.0,
            margin_total_balance=2100000.0,
        )

    def test_parse_margin_rows_empty(self):
        snapshot = _parse_margin_rows([])
        assert snapshot.financing_balance is None
        assert snapshot.history == []


class TestBatchFetch:
    def test_fetch_margin_trading_batch_uses_cache(self):
        cache = CapitalDataCache(ttl_seconds=60)
        cached = CapitalMetrics(margin_source="cached")
        cache.set("margin", "600519.SH", date.today(), cached)

        with patch("src.stock_tracker.capital_data.fetch_symbol_margin_trading") as mock_fetch:
            result = fetch_margin_trading_batch(["600519.SH"], trading_date=date.today(), cache=cache)
            assert result["600519.SH"] is cached
            mock_fetch.assert_not_called()

    def test_fetch_margin_trading_batch_isolates_errors(self):
        def _fake_fetch(code, days):
            if code == "600519.SH":
                return {"rows": [{"trade_date": "2026-08-29", "financing_balance": 1000000.0}]}
            return {"error": "failed"}

        with patch("src.stock_tracker.capital_data.fetch_symbol_margin_trading", side_effect=_fake_fetch):
            result = fetch_margin_trading_batch(["600519.SH", "000001.SZ"], trading_date=date.today())
            assert result["600519.SH"].margin.financing_balance == 1000000.0
            assert result["000001.SZ"].margin_error is not None


class TestLoadCapitalData:
    def test_load_capital_data_fetches_margin(self):
        def _fake_margin(code, days):
            return {
                "rows": [
                    {"trade_date": "2026-08-29", "financing_balance": 1100000.0, "margin_total_balance": 2100000.0},
                    {"trade_date": "2026-08-28", "financing_balance": 1000000.0, "margin_total_balance": 2000000.0},
                ],
                "source": "eastmoney",
            }

        with patch("src.stock_tracker.capital_data.fetch_symbol_margin_trading", side_effect=_fake_margin):
            result = load_capital_data(["600519.SH"], end_date=date(2026, 8, 29))
            metrics = result["600519.SH"]
            assert metrics.margin.financing_balance == 1100000.0
            assert metrics.margin.financing_balance_change == 100000.0
            assert metrics.margin_source == "eastmoney"
            assert len(metrics.margin.history) == 2

    def test_load_capital_data_missing_symbol_has_error(self):
        with patch("src.stock_tracker.capital_data.fetch_symbol_margin_trading", return_value={"error": "fail"}):
            result = load_capital_data(["600519.SH"], end_date=date(2026, 8, 29))
            assert result["600519.SH"].margin_error is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
