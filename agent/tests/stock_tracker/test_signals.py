"""Tests for the signal detector registry and metadata."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.stock_tracker.models import CapitalMetrics, FundFlowHistoryItem, FundFlowSnapshot, TrackerThresholds
from src.stock_tracker.signals import (
    MainForceInflowDetector,
    NetInflowSpikeDetector,
    RSIDetector,
    get_detector,
    get_detector_meta,
    list_detector_meta,
    list_detector_names,
)


def test_registry_contains_builtin_detectors() -> None:
    names = list_detector_names()
    assert "volume_spike" in names
    assert "breakout" in names
    assert "ma_alignment" in names
    assert "rsi" in names


def test_list_detector_meta_is_serializable() -> None:
    metas = list_detector_meta()
    assert len(metas) >= 4
    for meta in metas:
        dumped = meta.model_dump()
        assert dumped["name"]
        assert "params" in dumped
        assert "ranking_enabled" in dumped


def test_get_detector_meta_returns_expected_fields() -> None:
    meta = get_detector_meta("volume_spike")
    assert meta.name == "volume_spike"
    assert meta.format == "multiple"
    assert "volume_spike" in meta.params
    assert meta.ranking_enabled is True


def test_ma_alignment_is_global_not_table() -> None:
    meta = get_detector_meta("ma_alignment")
    assert meta.is_global is True
    assert meta.show_in_table is False
    assert meta.ranking_enabled is False


def test_rsi_meta_declares_params() -> None:
    meta = get_detector_meta("rsi")
    assert meta.params["rsi_overbought"]["default"] == 70.0
    assert meta.params["rsi_oversold"]["default"] == 30.0


def test_get_detector_caches_instance() -> None:
    first = get_detector("rsi")
    second = get_detector("rsi")
    assert first is second
    assert isinstance(first, RSIDetector)


def test_registry_contains_margin_detector() -> None:
    names = list_detector_names()
    assert "margin_expansion" in names


def test_margin_expansion_meta_declares_params() -> None:
    meta = get_detector_meta("margin_expansion")
    assert meta.category == "capital"
    assert meta.direction == "bullish"
    assert "margin_expansion_threshold" in meta.params
    assert meta.params["margin_expansion_threshold"]["default"] == 0.03


def test_get_detector_caches_margin_instance() -> None:
    from src.stock_tracker.signals import MarginExpansionDetector

    first = get_detector("margin_expansion")
    second = get_detector("margin_expansion")
    assert first is second
    assert isinstance(first, MarginExpansionDetector)


def test_unknown_detector_raises() -> None:
    with pytest.raises(ValueError, match="Unknown signal type"):
        get_detector_meta("not_a_signal")
    with pytest.raises(ValueError, match="Unknown signal type"):
        get_detector("not_a_signal")


def _make_fund_flow_history(start: date, days: int, main_values: list[float] | None = None):
    """Build a ``FundFlowSnapshot`` history of ``days`` items ending on ``start``.

    ``main_values`` are applied in order; remaining days use a neutral value.
    """
    history: list[FundFlowHistoryItem] = []
    for i in range(days):
        trade_date = start - timedelta(days=days - 1 - i)
        main_net = main_values[i] if main_values and i < len(main_values) else 100_000.0
        history.append(
            FundFlowHistoryItem(
                trade_date=trade_date,
                main_net=main_net,
                super_large_net=main_net * 0.5,
                large_net=main_net * 0.3,
                medium_net=-main_net * 0.1,
                small_net=-main_net * 0.1,
            )
        )
    latest = history[-1]
    return FundFlowSnapshot(
        trade_date=latest.trade_date,
        main_net=latest.main_net,
        history=history,
    )


def _df_with_capital(snapshot: FundFlowSnapshot) -> pd.DataFrame:
    """Create a minimal DataFrame with the given capital snapshot in attrs."""
    df = pd.DataFrame({"close": [100.0, 101.0], "volume": [10000, 11000]})
    df.attrs["capital"] = CapitalMetrics(fund_flow=snapshot)
    return df


class TestNetInflowSpikeDetector:
    def test_meta_registered(self) -> None:
        meta = get_detector_meta("net_inflow_spike")
        assert meta.category == "capital"
        assert meta.direction == "both"
        assert meta.format == "multiple"
        assert meta.params["net_inflow_spike_multiple"]["default"] == 2.0

    def test_spike_triggered_when_today_exceeds_threshold(self) -> None:
        base = date(2026, 8, 31)
        # 20 days of small absolute values, today is a large positive spike.
        main_values = [100_000.0] * 20 + [10_000_000.0]
        snapshot = _make_fund_flow_history(base, 21, main_values)
        df = _df_with_capital(snapshot)

        detector = NetInflowSpikeDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value in ("triggered", "strong")
        assert value.value is not None
        assert value.value >= 2.0
        assert "inflow" in value.description.lower()

    def test_spike_not_triggered_below_threshold(self) -> None:
        base = date(2026, 8, 31)
        main_values = [100_000.0] * 21
        snapshot = _make_fund_flow_history(base, 21, main_values)
        df = _df_with_capital(snapshot)

        detector = NetInflowSpikeDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())

        assert value.triggered is False

    def test_spike_outflow_triggered(self) -> None:
        base = date(2026, 8, 31)
        main_values = [100_000.0] * 20 + [-10_000_000.0]
        snapshot = _make_fund_flow_history(base, 21, main_values)
        df = _df_with_capital(snapshot)

        detector = NetInflowSpikeDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())

        assert value.triggered is True
        assert "outflow" in value.description.lower()

    def test_spike_no_history_returns_no_trigger(self) -> None:
        snapshot = FundFlowSnapshot(history=[])
        df = _df_with_capital(snapshot)
        detector = NetInflowSpikeDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())
        assert value.triggered is False


class TestMainForceInflowDetector:
    def test_meta_registered(self) -> None:
        meta = get_detector_meta("main_force_inflow")
        assert meta.category == "capital"
        assert meta.direction == "bullish"
        assert meta.format == "raw"
        assert meta.params["main_force_inflow_days"]["default"] == 3.0

    def test_triggered_after_three_consecutive_positive_days(self) -> None:
        base = date(2026, 8, 31)
        # 5 negative days, then 3 positive days.
        main_values = [-100_000.0] * 5 + [100_000.0, 200_000.0, 300_000.0]
        snapshot = _make_fund_flow_history(base, 8, main_values)
        df = _df_with_capital(snapshot)

        detector = MainForceInflowDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())

        assert value.triggered is True
        assert value.value == 3
        assert value.state.value in ("triggered", "strong")

    def test_not_triggered_with_only_two_positive_days(self) -> None:
        base = date(2026, 8, 31)
        main_values = [-100_000.0] * 6 + [100_000.0, 200_000.0]
        snapshot = _make_fund_flow_history(base, 8, main_values)
        df = _df_with_capital(snapshot)

        detector = MainForceInflowDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())

        assert value.triggered is False
        assert value.value == 2

    def test_insufficient_history_returns_no_trigger(self) -> None:
        base = date(2026, 8, 31)
        main_values = [100_000.0, 200_000.0]
        snapshot = _make_fund_flow_history(base, 2, main_values)
        df = _df_with_capital(snapshot)

        detector = MainForceInflowDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())

        assert value.triggered is False
        assert "history" in value.description.lower()
