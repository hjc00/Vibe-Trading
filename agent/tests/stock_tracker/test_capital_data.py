"""Tests for the stock tracker capital data loader."""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.stock_tracker.capital_data import (
    CapitalDataCache,
    _merge_metrics,
    _parse_fund_flow_rows,
    _parse_margin_rows,
    fetch_fund_flow_batch,
    fetch_margin_trading_batch,
    load_capital_data,
)
from src.stock_tracker.models import CapitalMetrics, FundFlowSnapshot, MarginSnapshot


class TestCapitalDataCache:
    def test_cache_returns_value_within_ttl(self):
        cache = CapitalDataCache(ttl_seconds=60)
        metrics = CapitalMetrics()
        trading_date = date.today()
        cache.set("fund", "600519.SH", trading_date, metrics)
        assert cache.get("fund", "600519.SH", trading_date) is metrics

    def test_cache_expires_after_ttl(self):
        cache = CapitalDataCache(ttl_seconds=0.01)
        metrics = CapitalMetrics()
        trading_date = date.today()
        cache.set("fund", "600519.SH", trading_date, metrics)
        time.sleep(0.02)
        assert cache.get("fund", "600519.SH", trading_date) is None

    def test_cache_key_includes_trading_date(self):
        cache = CapitalDataCache(ttl_seconds=60)
        metrics = CapitalMetrics()
        today = date.today()
        yesterday = today - timedelta(days=1)
        cache.set("fund", "600519.SH", today, metrics)
        assert cache.get("fund", "600519.SH", yesterday) is None
        assert cache.get("fund", "600519.SH", today) is metrics

    def test_cache_clear(self):
        cache = CapitalDataCache()
        cache.set("fund", "600519.SH", date.today(), CapitalMetrics())
        cache.clear()
        assert cache.get("fund", "600519.SH", date.today()) is None


class TestParseRows:
    def test_parse_fund_flow_rows_computes_5d_sum(self):
        rows = [
            {"timestamp": "2026-08-25", "main": 1000000.0},
            {"timestamp": "2026-08-26", "main": 2000000.0},
            {"timestamp": "2026-08-27", "main": 3000000.0},
            {"timestamp": "2026-08-28", "main": 4000000.0},
            {"timestamp": "2026-08-29", "main": 5000000.0},
        ]
        snapshot = _parse_fund_flow_rows(rows)
        assert snapshot.trade_date == date(2026, 8, 29)
        assert snapshot.main_net == 5000000.0
        assert snapshot.main_5d_net == 15000000.0

    def test_parse_fund_flow_rows_empty(self):
        snapshot = _parse_fund_flow_rows([])
        assert snapshot.main_net is None
        assert snapshot.main_5d_net is None

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

    def test_parse_margin_rows_empty(self):
        snapshot = _parse_margin_rows([])
        assert snapshot.financing_balance is None


class TestMergeMetrics:
    def test_merge_prefers_side_without_error(self):
        fund = CapitalMetrics(
            fund_flow=FundFlowSnapshot(main_net=1_000_000.0),
            fund_flow_source="eastmoney",
            fund_flow_error=None,
            margin_error="not fetched",
            margin_source="unavailable",
        )
        margin = CapitalMetrics(
            margin=MarginSnapshot(financing_balance=100_000_000.0),
            margin_source="eastmoney",
            margin_error=None,
            fund_flow_error="not fetched",
            fund_flow_source="unavailable",
        )
        merged = _merge_metrics(fund, margin)
        assert merged.fund_flow_source == "eastmoney"
        assert merged.fund_flow.main_net == 1_000_000.0
        assert merged.margin_source == "eastmoney"
        assert merged.margin.financing_balance == 100_000_000.0
        assert merged.fund_flow_error is None
        assert merged.margin_error is None

    def test_merge_falls_back_when_error(self):
        fund_with_error = CapitalMetrics(
            fund_flow_source="eastmoney",
            fund_flow_error="timeout",
            margin_error="not fetched",
            margin_source="unavailable",
        )
        fallback = CapitalMetrics(
            fund_flow=FundFlowSnapshot(main_net=500_000.0),
            fund_flow_source="tushare",
            fund_flow_error=None,
            margin_error="not fetched",
            margin_source="unavailable",
        )
        merged = _merge_metrics(fund_with_error, fallback)
        assert merged.fund_flow_source == "tushare"
        assert merged.fund_flow.main_net == 500_000.0
        assert merged.fund_flow_error == "timeout"


class TestBatchFetch:
    def test_fetch_fund_flow_batch_uses_cache(self):
        cache = CapitalDataCache(ttl_seconds=60)
        cached = CapitalMetrics(fund_flow_source="cached")
        cache.set("fund", "600519.SH", date.today(), cached)

        with patch("src.stock_tracker.capital_data.fetch_symbol_fund_flow") as mock_fetch:
            result = fetch_fund_flow_batch(["600519.SH"], trading_date=date.today(), cache=cache)
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
    def test_load_capital_data_combines_sources(self):
        def _fake_fund_flow(code, period, days):
            return {
                "rows": [{"timestamp": "2026-08-29", "main": 1000000.0}],
                "source": "eastmoney",
            }

        def _fake_margin(code, days):
            return {
                "rows": [
                    {"trade_date": "2026-08-29", "financing_balance": 1100000.0, "margin_total_balance": 2100000.0},
                    {"trade_date": "2026-08-28", "financing_balance": 1000000.0, "margin_total_balance": 2000000.0},
                ],
                "source": "eastmoney",
            }

        with (
            patch("src.stock_tracker.capital_data.fetch_symbol_fund_flow", side_effect=_fake_fund_flow),
            patch("src.stock_tracker.capital_data.fetch_symbol_margin_trading", side_effect=_fake_margin),
        ):
            result = load_capital_data(["600519.SH"], end_date=date(2026, 8, 29))
            metrics = result["600519.SH"]
            assert metrics.fund_flow.main_net == 1000000.0
            assert metrics.margin.financing_balance_change == 100000.0
            assert metrics.fund_flow_source == "eastmoney"
            assert metrics.margin_source == "eastmoney"

    def test_load_capital_data_missing_symbol_has_error(self):
        with (
            patch("src.stock_tracker.capital_data.fetch_symbol_fund_flow", return_value={"error": "fail"}),
            patch("src.stock_tracker.capital_data.fetch_symbol_margin_trading", return_value={"error": "fail"}),
        ):
            result = load_capital_data(["600519.SH"], end_date=date(2026, 8, 29))
            assert result["600519.SH"].fund_flow_error is not None
            assert result["600519.SH"].margin_error is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
