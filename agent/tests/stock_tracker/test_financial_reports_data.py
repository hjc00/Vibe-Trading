"""Tests for the stock tracker financial-report reader (财报速读)."""

from __future__ import annotations

import pytest

from src.stock_tracker import financial_reports_data as frd
from src.stock_tracker.financial_reports_data import (
    FinancialReportCache,
    _beat_miss,
    _flag,
    build_financial_report,
    load_financial_report,
)
from src.stock_tracker.models import FinancialPeriod, FinancialReportSnapshot

pytestmark = pytest.mark.unit


def _indicator(**overrides):
    """Build one clean indicator dict (newest-first) with sensible defaults."""
    base = {
        "end_date": "2026-06-30",
        "report_type": "中报",
        "roe": 15.2,
        "gross_margin": 91.8,
        "net_margin": 52.1,
        "net_profit_yoy": 15.1,
        "revenue_yoy": 12.3,
        "debt_to_assets": 24.5,
        "eps": 32.1,
        "operating_cashflow_to_net_profit": 1.12,
    }
    base.update(overrides)
    return base


class TestBuildFinancialReport:
    def test_empty_source_degrades_with_error(self):
        report = build_financial_report("600519.SH", [])
        assert isinstance(report, FinancialReportSnapshot)
        assert report.code == "600519.SH"
        assert report.error == "no financial report data"
        assert report.periods == []
        assert report.red_flags == []
        assert report.beat_miss is None

    def test_multiple_periods_fill_newest_first(self):
        indicators = [
            _indicator(end_date="2026-06-30", report_type="中报"),
            _indicator(end_date="2026-03-31", report_type="一季报"),
            _indicator(end_date="2025-12-31", report_type="年报"),
        ]
        report = build_financial_report("600519.SH", indicators)
        assert report.error is None
        assert report.source == "eastmoney"
        assert len(report.periods) == 3
        assert report.periods[0].end_date == "2026-06-30"
        assert report.periods[2].report_type == "年报"
        assert report.periods[0].gross_margin == 91.8

    def test_beat_when_eps_above_consensus(self):
        report = build_financial_report(
            "600519.SH", [_indicator(eps=32.1)], consensus_eps=30.0
        )
        assert report.beat_miss == "beat"
        assert report.consensus_eps == 30.0

    def test_miss_when_eps_below_consensus(self):
        report = build_financial_report(
            "600519.SH", [_indicator(eps=28.0)], consensus_eps=31.0
        )
        assert report.beat_miss == "miss"

    def test_inline_within_tolerance(self):
        report = build_financial_report(
            "600519.SH", [_indicator(eps=31.0)], consensus_eps=31.0
        )
        assert report.beat_miss == "inline"

    def test_beat_miss_none_when_consensus_missing(self):
        report = build_financial_report("600519.SH", [_indicator(eps=32.1)])
        assert report.beat_miss is None
        assert report.consensus_eps is None


class TestRedFlags:
    def test_no_flags_on_healthy(self):
        report = build_financial_report("600519.SH", [_indicator()])
        assert report.red_flags == []

    def test_weak_cash_flow_flag(self):
        report = build_financial_report(
            "600519.SH", [_indicator(operating_cashflow_to_net_profit=0.3)]
        )
        assert any("现金流" in f for f in report.red_flags)

    def test_declining_profit_and_revenue_flags(self):
        report = build_financial_report(
            "600519.SH", [_indicator(net_profit_yoy=-5.0, revenue_yoy=-2.0)]
        )
        assert any("净利" in f and "同比下滑" in f for f in report.red_flags)
        assert any("营业收入" in f and "同比下滑" in f for f in report.red_flags)

    def test_high_leverage_flag(self):
        report = build_financial_report("600519.SH", [_indicator(debt_to_assets=80.0)])
        assert any("负债率" in f for f in report.red_flags)

    def test_gross_margin_two_period_decline(self):
        indicators = [
            _indicator(end_date="2026-06-30", gross_margin=90.0),
            _indicator(end_date="2026-03-31", gross_margin=91.0),
        ]
        report = build_financial_report("600519.SH", indicators)
        assert any("毛利率" in f for f in report.red_flags)

    def test_gross_margin_no_flag_when_up(self):
        indicators = [
            _indicator(end_date="2026-06-30", gross_margin=92.0),
            _indicator(end_date="2026-03-31", gross_margin=91.0),
        ]
        report = build_financial_report("600519.SH", indicators)
        assert report.red_flags == []

    def test_growing_revenue_but_flat_profit_flag(self):
        # 增收不增利: net profit YoY well below revenue YoY.
        report = build_financial_report(
            "600519.SH",
            [_indicator(revenue_yoy=40.0, net_profit_yoy=5.0)],
        )
        assert any("增收不增利" in f for f in report.red_flags)

    def test_flag_tolerates_missing_cells(self):
        sparse = _indicator()
        sparse.pop("operating_cashflow_to_net_profit")
        sparse.pop("roe")
        report = build_financial_report("600519.SH", [sparse])
        # No KeyError; only structural flags (e.g. margin decline needs a prior
        # period, which is absent) may fire.
        assert isinstance(report.red_flags, list)


class TestFinancialReportCache:
    def test_miss_then_hit_roundtrip(self, tmp_path):
        cache = FinancialReportCache(ttl_seconds=60, root_dir=tmp_path)
        assert cache.get("600519.SH") is None
        indicators = [_indicator(eps=32.1)]
        cache.set("600519.SH", indicators)
        assert cache.get("600519.SH") == indicators

    def test_expired_entry_is_a_miss_but_stale_readable(self, tmp_path):
        cache = FinancialReportCache(ttl_seconds=-1.0, root_dir=tmp_path)
        cache.set("600519.SH", [_indicator(eps=32.1)])
        # Expired: normal get is a miss, but the persisted file stays as last-good.
        assert cache.get("600519.SH") is None
        assert cache.get_stale("600519.SH") is not None

    def test_persists_across_instances(self, tmp_path):
        FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path).set(
            "600519.SH", [_indicator(eps=32.1)]
        )
        fresh = FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)
        assert fresh.get("600519.SH") == [_indicator(eps=32.1)]

    def test_clear_removes_files(self, tmp_path):
        cache = FinancialReportCache(ttl_seconds=60, root_dir=tmp_path)
        cache.set("600519.SH", [_indicator()])
        cache.clear()
        assert cache.get("600519.SH") is None
        assert cache.get_stale("600519.SH") is None


class TestLoadFinancialReportCaching:
    def test_second_call_served_from_cache_without_refetch(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def fake_fetch(code, max_periods=12):  # noqa: ARG001
            calls["n"] += 1
            return [_indicator(eps=32.1)]

        monkeypatch.setattr(frd, "fetch_financial_indicators", fake_fetch)
        cache = FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)

        first = load_financial_report("600519.SH", consensus_eps=30.0, cache=cache)
        assert calls["n"] == 1
        assert first.beat_miss == "beat"

        # Cache hit: no refetch, but beat/miss is rebuilt against the fresh consensus.
        second = load_financial_report("600519.SH", consensus_eps=34.0, cache=cache)
        assert calls["n"] == 1
        assert second.periods[0].eps == 32.1
        assert second.beat_miss == "miss"

    def test_restart_serves_persisted_cache_without_network(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def fake_fetch(code, max_periods=12):  # noqa: ARG001
            calls["n"] += 1
            return [_indicator(eps=32.1)]

        monkeypatch.setattr(frd, "fetch_financial_indicators", fake_fetch)

        first = load_financial_report(
            "600519.SH", cache=FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)
        )
        assert first.periods[0].eps == 32.1
        assert calls["n"] == 1

        # A fresh cache instance over the same directory == server restart.
        second = load_financial_report(
            "600519.SH", cache=FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)
        )
        assert calls["n"] == 1
        assert second.periods[0].eps == 32.1

    def test_fetch_failure_falls_back_to_last_persisted_report(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def flaky(code, max_periods=12):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] == 1:
                return [_indicator(eps=32.1)]
            raise RuntimeError("boom")

        monkeypatch.setattr(frd, "fetch_financial_indicators", flaky)
        # ttl<0 makes the entry expire immediately, so the second load must refetch.
        cache = FinancialReportCache(ttl_seconds=-1.0, root_dir=tmp_path)

        first = load_financial_report("600519.SH", cache=cache)
        assert calls["n"] == 1
        assert first.periods[0].eps == 32.1

        second = load_financial_report("600519.SH", cache=cache)
        assert calls["n"] == 2  # refresh attempted after expiry
        assert second.error is None  # but the persisted last-good is served
        assert second.periods[0].eps == 32.1

    def test_force_bypasses_cache(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def fake_fetch(code, max_periods=12):  # noqa: ARG001
            calls["n"] += 1
            return [_indicator(eps=32.1)]

        monkeypatch.setattr(frd, "fetch_financial_indicators", fake_fetch)
        cache = FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)

        load_financial_report("600519.SH", consensus_eps=30.0, cache=cache)
        load_financial_report("600519.SH", consensus_eps=30.0, cache=cache)
        assert calls["n"] == 1  # second read served from cache

        forced = load_financial_report("600519.SH", consensus_eps=34.0, cache=cache, force=True)
        assert calls["n"] == 2  # force ignores the cache and re-fetches
        assert forced.periods[0].eps == 32.1
        assert forced.beat_miss == "miss"  # rebuilt against the fresh consensus

    def test_fetch_failure_with_no_cache_is_not_cached(self, monkeypatch, tmp_path):
        def fail(code, max_periods=12):  # noqa: ARG001
            raise RuntimeError("boom")

        monkeypatch.setattr(frd, "fetch_financial_indicators", fail)
        cache = FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)
        report = load_financial_report("600519.SH", cache=cache)
        assert report.error == "no financial report data"
        assert cache.get("600519.SH") is None
        assert cache.get_stale("600519.SH") is None

    def test_empty_result_not_cached(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            frd, "fetch_financial_indicators", lambda code, max_periods=12: []  # noqa: ARG005
        )
        cache = FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)
        report = load_financial_report("600519.SH", cache=cache)
        assert report.error == "no financial report data"
        assert report.periods == []
        assert cache.get("600519.SH") is None

    def test_transient_failure_retries_next_call(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def flaky(code, max_periods=12):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return [_indicator()]

        monkeypatch.setattr(frd, "fetch_financial_indicators", flaky)
        cache = FinancialReportCache(ttl_seconds=3600, root_dir=tmp_path)
        assert (
            load_financial_report("600519.SH", cache=cache).error
            == "no financial report data"
        )
        ok = load_financial_report("600519.SH", cache=cache)
        assert ok.error is None
        assert cache.get("600519.SH") is not None


class TestBeatMissHelper:
    def test_thresholds(self):
        assert _beat_miss(1.0, None) is None
        assert _beat_miss(None, 1.0) is None
        assert _beat_miss(1.05, 1.0) == "inline"  # exactly at tolerance
        assert _beat_miss(1.06, 1.0) == "beat"
        assert _beat_miss(0.95, 1.0) == "inline"
        assert _beat_miss(0.94, 1.0) == "miss"


class TestFlagHelper:
    def test_requires_financial_periods(self):
        # Unit-testable through the pydantic layer it consumes.
        latest = FinancialPeriod(end_date="2026-06-30", report_type="中报")
        assert _flag(latest, None) == []

    def test_margin_decline_needs_both(self):
        latest = FinancialPeriod(end_date="2026-06-30", report_type="中报", gross_margin=90.0)
        prior = FinancialPeriod(end_date="2026-03-31", report_type="一季报", gross_margin=91.0)
        assert any("毛利率" in f for f in _flag(latest, prior))
