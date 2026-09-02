"""Tests for the stock tracker valuation data loader."""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.stock_tracker.models import ValuationSnapshot
from src.stock_tracker.valuation_data import (
    ValuationDataCache,
    _aggregate_fundamentals,
    _build_valuation_snapshot,
    _fetch_one_valuation,
    _parse_fundamental_rows,
    _tushare_valuation_snapshot,
    _window_percentile,
    compute_quality_score,
    load_valuation_data,
)


def _make_valuation_rows(days: int = 200) -> list[dict]:
    """Build newest-first ``RPT_VALUEANALYSIS_DET`` rows with deterministic PE/PB.

    PE rises over time so the latest value is the historical max; PB falls so
    the latest value is the historical min. This makes percentiles exact.
    """
    start = date(2026, 9, 1)
    rows = []
    for i in range(days):
        idx = days - i  # oldest=1, newest=days
        rows.append(
            {
                "SECUCODE": "600519.SH",
                "TRADE_DATE": f"{(start - timedelta(days=i)):%Y-%m-%d} 00:00:00",
                "CLOSE_PRICE": 100.0 + idx,
                "PE_TTM": 10.0 + idx * 0.01,
                "PB_MRQ": 5.0 - idx * 0.005,
                "PS_TTM": 8.0 + idx * 0.01,
                "PCF_OCF_TTM": 3.0,
                "PEG_CAR": 1.5,
                "TOTAL_MARKET_CAP": 1.6e12,
            }
        )
    return rows


def _make_fundamental_rows(periods: int = 8) -> list[dict]:
    """Build newest-first ``RPT_F10_FINANCE_MAINFINADATA`` rows."""
    base = date(2026, 6, 30)
    rows = []
    for i in range(periods):
        rows.append(
            {
                "REPORT_DATE": f"{(base - timedelta(days=91 * i)):%Y-%m-%d} 00:00:00",
                "ROEJQ": 16.0 + i * 0.5,
                "XSMLL": 88.0 + i,
                "XSJLL": 50.0,
                "PARENTNETPROFITTZ": 10.0 + i,
                "TOTALOPERATEREVETZ": 5.0,
                "ZCFZL": 15.0,
                "EPSJB": 30.0,
                "MGJYXJJE": 45.0,
            }
        )
    return rows


class TestValuationDataCache:
    def test_cache_returns_value_within_ttl(self):
        cache = ValuationDataCache(ttl_seconds=60)
        snapshot = ValuationSnapshot(pe_ttm=19.9, source="eastmoney")
        cache.set("600519.SH", date.today(), snapshot)
        assert cache.get("600519.SH", date.today()) is snapshot

    def test_cache_expires_after_ttl(self):
        cache = ValuationDataCache(ttl_seconds=0.01)
        cache.set("600519.SH", date.today(), ValuationSnapshot())
        time.sleep(0.02)
        assert cache.get("600519.SH", date.today()) is None

    def test_cache_key_includes_trading_date(self):
        cache = ValuationDataCache(ttl_seconds=60)
        today = date.today()
        cache.set("600519.SH", today, ValuationSnapshot(pe_ttm=19.9))
        assert cache.get("600519.SH", today - timedelta(days=1)) is None
        assert cache.get("600519.SH", today) is not None


class TestBuildValuationSnapshot:
    def test_build_snapshot_from_rows(self):
        snapshot = _build_valuation_snapshot(_make_valuation_rows(days=200))

        assert snapshot.trade_date == date(2026, 9, 1)
        assert snapshot.pe_ttm == pytest.approx(10.0 + 200 * 0.01)
        assert snapshot.pb == pytest.approx(5.0 - 200 * 0.005)
        assert snapshot.ps_ttm == pytest.approx(8.0 + 200 * 0.01)
        assert snapshot.pcf_ocf_ttm == 3.0
        assert snapshot.peg == 1.5
        assert snapshot.total_market_cap == 1.6e12
        assert snapshot.source == "eastmoney"
        assert snapshot.error is None
        assert len(snapshot.history) == 200

    def test_history_is_ascending(self):
        snapshot = _build_valuation_snapshot(_make_valuation_rows(days=50))
        dates = [item.trade_date for item in snapshot.history]
        assert dates == sorted(dates)
        # Newest row from the report is the last history item.
        assert dates[-1] == date(2026, 9, 1)

    def test_percentiles_use_latest_value_position(self):
        snapshot = _build_valuation_snapshot(_make_valuation_rows(days=200))
        # PE is at its historical max -> ~100th percentile; PB at its min -> ~0.
        assert snapshot.pe_percentile_3y == pytest.approx(100.0, abs=1.0)
        assert snapshot.pb_percentile_3y == pytest.approx(0.0, abs=1.0)

    def test_empty_rows_produce_error_snapshot(self):
        snapshot = _build_valuation_snapshot([])
        assert snapshot.error == "no valuation data"
        assert snapshot.source == "unavailable"


class TestWindowPercentile:
    def test_short_series_returns_none(self):
        assert _window_percentile([float(i) for i in range(50)], 750) is None

    def test_window_returns_percentile(self):
        values = [float(i) for i in range(100)]  # latest is max
        assert _window_percentile(values, 750) == pytest.approx(100.0, abs=1.0)

    def test_window_shorter_than_requested_uses_available(self):
        values = [float(i) for i in range(100)]
        # Window of 2500 but only 100 points -> uses all 100.
        assert _window_percentile(values, 2500) == pytest.approx(100.0, abs=1.0)


class TestFundamentals:
    def test_parse_fundamental_rows_computes_cashflow_ratio(self):
        rows = _make_fundamental_rows(periods=2)
        parsed = _parse_fundamental_rows(rows)
        assert len(parsed) == 2
        # MGJYXJJE=45.0 / EPSJB=30.0
        assert parsed[-1]["operating_cashflow_to_net_profit"] == pytest.approx(1.5)
        assert parsed[-1]["roe"] == pytest.approx(16.0)

    def test_parse_fundamental_rows_skips_missing_report_date(self):
        rows = [{"ROEJQ": 16.0}]
        assert _parse_fundamental_rows(rows) == []

    def test_aggregate_fundamentals_computes_5y_stats(self):
        rows = _make_fundamental_rows(periods=8)
        parsed = _parse_fundamental_rows(rows)
        aggregated = _aggregate_fundamentals(parsed)
        assert aggregated["roe_mean_5y"] is not None
        assert aggregated["roe_std_5y"] is not None
        assert aggregated["gross_margin_std_5y"] is not None
        # Latest period values pass through.
        assert aggregated["roe"] == pytest.approx(16.0)

    def test_aggregate_fundamentals_empty(self):
        assert _aggregate_fundamentals([]) == {}


class TestQualityScore:
    def test_strong_fundamentals_score_high(self):
        score = compute_quality_score(
            {
                "roe": 25.0,
                "roe_mean_5y": 20.0,
                "roe_std_5y": 1.0,
                "gross_margin": 50.0,
                "gross_margin_std_5y": 1.0,
                "net_profit_yoy": 30.0,
                "operating_cashflow_to_net_profit": 1.5,
                "debt_to_assets": 20.0,
            }
        )
        assert score is not None
        assert score > 90

    def test_weak_fundamentals_score_low(self):
        score = compute_quality_score(
            {
                "roe": 2.0,
                "roe_mean_5y": 5.0,
                "roe_std_5y": 4.0,
                "gross_margin": 8.0,
                "gross_margin_std_5y": 6.0,
                "net_profit_yoy": -30.0,
                "operating_cashflow_to_net_profit": -0.5,
                "debt_to_assets": 80.0,
            }
        )
        assert score is not None
        assert score < 5

    def test_missing_subscore_renormalizes(self):
        score = compute_quality_score({"roe": 20.0, "net_profit_yoy": 20.0})
        assert score is not None
        # Both subscores max out; renormalized weights keep it ~100.
        assert score > 90

    def test_no_inputs_returns_none(self):
        assert compute_quality_score({}) is None

    def test_custom_weights_apply(self):
        weights = {"roe": 1.0}
        score = compute_quality_score({"roe": 10.0}, weights=weights)
        assert score == pytest.approx(_linear_for(10.0, 0.0, 20.0))


def _linear_for(value: float, lo: float, hi: float) -> float:
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


class TestTushareFallback:
    def test_tushare_valuation_snapshot(self):
        rows = [
            {
                "trade_date": "2026-08-28",
                "close": 100.0,
                "pe_ttm": 18.0,
                "pb": 5.0,
                "ps_ttm": 9.0,
                "dividend_yield": 1.2,
                "total_market_cap": 1.5e12,
            },
            {
                "trade_date": "2026-08-31",
                "close": 101.0,
                "pe_ttm": 19.0,
                "pb": 5.1,
                "ps_ttm": 9.1,
                "dividend_yield": 1.2,
                "total_market_cap": 1.6e12,
            },
        ]
        with patch(
            "src.stock_tracker.valuation_data.tushare_fallbacks.fetch_daily_basic",
            return_value={"rows": rows, "source": "tushare"},
        ):
            snapshot = _tushare_valuation_snapshot("600519.SH")

        assert snapshot.source == "tushare"
        assert snapshot.pe_ttm == 19.0
        assert snapshot.pb == 5.1
        assert snapshot.dividend_yield == 1.2
        assert snapshot.pe_percentile_3y is None  # too few points
        assert snapshot.history[0].trade_date == date(2026, 8, 28)

    def test_tushare_fallback_unavailable(self):
        with patch(
            "src.stock_tracker.valuation_data.tushare_fallbacks.fetch_daily_basic",
            side_effect=RuntimeError("TUSHARE_TOKEN is not configured"),
        ):
            snapshot = _tushare_valuation_snapshot("600519.SH")
        assert snapshot.source == "unavailable"
        assert snapshot.error is not None


class TestFetchOneValuation:
    def test_combines_valuation_and_fundamentals(self):
        val_rows = _make_valuation_rows(days=200)
        fund_rows = _make_fundamental_rows(periods=8)

        def _fake_report(report_name, secucode, *, page_size):
            if report_name == "RPT_VALUEANALYSIS_DET":
                return val_rows
            return fund_rows

        with patch("src.stock_tracker.valuation_data._fetch_report", side_effect=_fake_report):
            snapshot = _fetch_one_valuation("600519.SH")

        assert snapshot.error is None
        assert snapshot.pe_ttm is not None
        assert snapshot.roe == pytest.approx(16.0)
        assert snapshot.roe_mean_5y is not None
        assert snapshot.fundamental_quality_score is not None
        assert snapshot.source == "eastmoney"

    def test_isolates_fundamental_failure(self):
        val_rows = _make_valuation_rows(days=200)

        def _fake_report(report_name, secucode, *, page_size):
            if report_name == "RPT_VALUEANALYSIS_DET":
                return val_rows
            raise RuntimeError("datacenter down")

        with patch("src.stock_tracker.valuation_data._fetch_report", side_effect=_fake_report):
            snapshot = _fetch_one_valuation("600519.SH")

        assert snapshot.pe_ttm is not None
        assert snapshot.error is not None
        assert "fundamentals" in snapshot.error


class TestLoadValuationData:
    def test_isolates_symbol_errors(self):
        def _fake(code):
            if code == "600519.SH":
                return ValuationSnapshot(pe_ttm=19.9, source="eastmoney")
            return ValuationSnapshot(error="boom", source="unavailable")

        with patch("src.stock_tracker.valuation_data._fetch_one_valuation", side_effect=_fake):
            result = load_valuation_data(["600519.SH", "000001.SZ"], end_date=date(2026, 8, 31))

        assert result["600519.SH"].pe_ttm == 19.9
        assert result["000001.SZ"].error == "boom"

    def test_caches_successes(self):
        cache = ValuationDataCache(ttl_seconds=60)
        snapshot = ValuationSnapshot(pe_ttm=19.9, source="eastmoney")

        with patch(
            "src.stock_tracker.valuation_data._fetch_one_valuation",
            return_value=snapshot,
        ) as mock_fetch:
            load_valuation_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert cache.get("600519.SH", date(2026, 8, 31)) is not None

            mock_fetch.reset_mock()
            result = load_valuation_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert result["600519.SH"].pe_ttm == 19.9
            mock_fetch.assert_not_called()

    def test_does_not_cache_errors(self):
        cache = ValuationDataCache(ttl_seconds=60)
        failed = ValuationSnapshot(error="boom", source="unavailable")

        with patch(
            "src.stock_tracker.valuation_data._fetch_one_valuation",
            return_value=failed,
        ) as mock_fetch:
            load_valuation_data(["600519.SH"], end_date=date(2026, 8, 31), cache=cache)
            assert cache.get("600519.SH", date(2026, 8, 31)) is None
            assert mock_fetch.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
