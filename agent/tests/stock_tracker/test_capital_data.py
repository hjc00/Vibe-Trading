"""Tests for the stock tracker capital data loader."""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.stock_tracker.capital_data import (
    CapitalDataCache,
    _parse_fund_flow_rows,
    _parse_margin_rows,
    fetch_fund_flow_batch,
    fetch_margin_trading_batch,
    load_capital_data,
)
from src.stock_tracker.models import (
    CapitalMetrics,
    FundFlowHistoryItem,
    MarginHistoryItem,
)


def _make_fund_flow_rows(start: date, days: int, base_main: float = 1_000_000.0):
    """Build deterministic daily fund-flow rows for mocking."""
    rows = []
    for i in range(days):
        trade_date = start + timedelta(days=i)
        rows.append(
            {
                "timestamp": trade_date.isoformat(),
                "main": base_main + (i % 7) * 100_000.0 - 300_000.0,
                "super_large": 100_000.0 + i * 10_000.0,
                "large": 50_000.0,
                "medium": -20_000.0,
                "small": -10_000.0,
                "turnover": 1_000_000_000.0,
            }
        )
    return rows


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


class TestFundFlowParseRows:
    def test_parse_fund_flow_rows_computes_5d_net(self):
        base = date(2026, 8, 1)
        rows = _make_fund_flow_rows(base, 10)
        snapshot = _parse_fund_flow_rows(rows)

        assert snapshot.trade_date == base + timedelta(days=9)
        assert snapshot.main_net is not None
        expected_5d = sum(
            row["main"] for row in rows[-5:]
        )
        assert snapshot.main_5d_net == pytest.approx(expected_5d)
        assert len(snapshot.history) == 10
        assert snapshot.history[0] == FundFlowHistoryItem(
            trade_date=base,
            main_net=rows[0]["main"],
            super_large_net=rows[0]["super_large"],
            large_net=rows[0]["large"],
            medium_net=rows[0]["medium"],
            small_net=rows[0]["small"],
        )

    def test_parse_fund_flow_rows_empty(self):
        snapshot = _parse_fund_flow_rows([])
        assert snapshot.trade_date is None
        assert snapshot.history == []

    def test_parse_fund_flow_rows_sorts_descending_input(self):
        base = date(2026, 8, 1)
        rows = _make_fund_flow_rows(base, 10)
        rows.reverse()
        snapshot = _parse_fund_flow_rows(rows)
        assert snapshot.trade_date == base + timedelta(days=9)
        assert snapshot.history[0].trade_date == base


class TestFundFlowBatchFetch:
    def test_fetch_fund_flow_batch_uses_cache(self):
        cache = CapitalDataCache(ttl_seconds=60)
        cached = CapitalMetrics(fund_flow_source="cached")
        cache.set("fund_flow", "600519.SH", date.today(), cached)

        with patch("src.stock_tracker.capital_data.fetch_symbol_fund_flow") as mock_fetch:
            result = fetch_fund_flow_batch(["600519.SH"], trading_date=date.today(), cache=cache)
            assert result["600519.SH"] is cached
            mock_fetch.assert_not_called()

    def test_fetch_fund_flow_batch_isolates_errors(self):
        def _fake_fetch(code, *, period, days):
            if code == "600519.SH":
                return {"rows": _make_fund_flow_rows(date(2026, 8, 1), 5)}
            return {"error": "failed"}

        with patch("src.stock_tracker.capital_data.fetch_symbol_fund_flow", side_effect=_fake_fetch):
            result = fetch_fund_flow_batch(["600519.SH", "000001.SZ"], trading_date=date.today())
            assert result["600519.SH"].fund_flow.main_net is not None
            assert result["000001.SZ"].fund_flow_error is not None

    def test_fetch_fund_flow_batch_caches_success(self):
        cache = CapitalDataCache(ttl_seconds=60)
        rows = _make_fund_flow_rows(date(2026, 8, 1), 5)

        with patch(
            "src.stock_tracker.capital_data.fetch_symbol_fund_flow",
            return_value={"rows": rows},
        ) as mock_fetch:
            fetch_fund_flow_batch(["600519.SH"], trading_date=date.today(), cache=cache)
            assert cache.get("fund_flow", "600519.SH", date.today()) is not None

            # Second call should hit cache and not call fetch again.
            mock_fetch.reset_mock()
            result = fetch_fund_flow_batch(["600519.SH"], trading_date=date.today(), cache=cache)
            assert result["600519.SH"].fund_flow.main_net is not None
            mock_fetch.assert_not_called()


class TestLoadCapitalDataMerge:
    def test_load_capital_data_merges_fund_flow_and_margin(self):
        fund_rows = _make_fund_flow_rows(date(2026, 8, 1), 10)
        margin_rows = [
            {"trade_date": "2026-08-29", "financing_balance": 1100000.0, "margin_total_balance": 2100000.0},
            {"trade_date": "2026-08-28", "financing_balance": 1000000.0, "margin_total_balance": 2000000.0},
        ]

        with patch(
            "src.stock_tracker.capital_data.fetch_symbol_fund_flow",
            return_value={"rows": fund_rows, "source": "eastmoney"},
        ), patch(
            "src.stock_tracker.capital_data.fetch_symbol_margin_trading",
            return_value={"rows": margin_rows, "source": "eastmoney"},
        ):
            result = load_capital_data(["600519.SH"], end_date=date(2026, 8, 29))
            metrics = result["600519.SH"]
            assert metrics.fund_flow.main_net is not None
            assert metrics.fund_flow_source == "eastmoney"
            assert metrics.margin.financing_balance == 1100000.0
            assert metrics.margin_source == "eastmoney"
            assert metrics.fund_flow_error is None
            assert metrics.margin_error is None

    def test_load_capital_data_isolates_fund_flow_error(self):
        margin_rows = [
            {"trade_date": "2026-08-29", "financing_balance": 1100000.0, "margin_total_balance": 2100000.0},
        ]

        with patch(
            "src.stock_tracker.capital_data.fetch_symbol_fund_flow",
            return_value={"error": "blocked"},
        ), patch(
            "src.stock_tracker.capital_data.fetch_symbol_margin_trading",
            return_value={"rows": margin_rows, "source": "eastmoney"},
        ):
            result = load_capital_data(["600519.SH"], end_date=date(2026, 8, 29))
            metrics = result["600519.SH"]
            assert metrics.fund_flow_error is not None
            assert metrics.margin.financing_balance == 1100000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
