"""Tests for the signal detector registry and metadata."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.stock_tracker.indicators import compute_kdj, compute_macd
from src.stock_tracker.models import (
    CapitalMetrics,
    FundFlowHistoryItem,
    FundFlowSnapshot,
    MarginHistoryItem,
    MarginSnapshot,
    TrackerThresholds,
)
from src.stock_tracker.signals import (
    BollingerPctBDetector,
    BollingerSqueezeDetector,
    DivergenceDetector,
    KdjDetector,
    MacdDetector,
    MainForceInflowDetector,
    MarginExpansionDetector,
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


class TestMarginExpansionDetector:
    """The margin-expansion signal must be period-aware, not one shared scalar."""

    @staticmethod
    def _df_with_margin(
        history: list[MarginHistoryItem],
        *,
        balance: float | None = None,
        change: float | None = None,
    ) -> pd.DataFrame:
        df = pd.DataFrame({"close": [100.0, 101.0]})
        df.attrs["capital"] = CapitalMetrics(
            margin=MarginSnapshot(
                trade_date=history[0].trade_date if history else None,
                financing_balance=balance,
                financing_balance_change=change,
                history=history,
            )
        )
        return df

    def test_meta_registered(self) -> None:
        meta = get_detector_meta("margin_expansion")
        assert meta.category == "capital"
        assert meta.direction == "bullish"
        assert meta.format == "percent"
        assert meta.params["margin_expansion_threshold"]["default"] == 0.03

    def test_value_differs_across_periods(self) -> None:
        # Most-recent-first daily balances: 110 -> 105 -> 100.
        history = [
            MarginHistoryItem(trade_date=date(2026, 9, 1), financing_balance=110.0),
            MarginHistoryItem(trade_date=date(2026, 8, 28), financing_balance=105.0),
            MarginHistoryItem(trade_date=date(2026, 8, 21), financing_balance=100.0),
        ]
        df = self._df_with_margin(history)
        detector = MarginExpansionDetector()

        one_day = detector.detect("000001.SZ", df, 1, TrackerThresholds())
        two_day = detector.detect("000001.SZ", df, 2, TrackerThresholds())

        assert one_day.triggered is True  # (110-105)/105 = +4.76%
        assert two_day.triggered is True  # (110-100)/100 = +10%
        assert one_day.value != two_day.value
        assert two_day.value == pytest.approx(0.1)

    def test_below_threshold_not_triggered(self) -> None:
        history = [
            MarginHistoryItem(trade_date=date(2026, 9, 1), financing_balance=101.0),
            MarginHistoryItem(trade_date=date(2026, 8, 28), financing_balance=100.0),
        ]
        df = self._df_with_margin(history)
        detector = MarginExpansionDetector()
        # (101-100)/100 = 1% < the 3% default threshold.
        value = detector.detect("000001.SZ", df, 1, TrackerThresholds())
        assert value.triggered is False
        assert value.value == pytest.approx(0.01)

    def test_lookback_capped_at_available_history(self) -> None:
        # Only 3 rows but a 60-day window is requested: use the full window.
        history = [
            MarginHistoryItem(trade_date=date(2026, 9, 1), financing_balance=110.0),
            MarginHistoryItem(trade_date=date(2026, 8, 28), financing_balance=105.0),
            MarginHistoryItem(trade_date=date(2026, 8, 21), financing_balance=100.0),
        ]
        df = self._df_with_margin(history)
        detector = MarginExpansionDetector()
        value = detector.detect("000001.SZ", df, 60, TrackerThresholds())
        assert value.triggered is True
        assert value.value == pytest.approx(0.1)

    def test_falls_back_to_day_over_day_scalar_without_history(self) -> None:
        # No daily history: use the precomputed day-over-day change scalar.
        df = self._df_with_margin([], balance=110.0, change=10.0)
        detector = MarginExpansionDetector()
        value = detector.detect("000001.SZ", df, 20, TrackerThresholds())
        assert value.triggered is True
        assert value.value == pytest.approx(0.1)

    def test_no_margin_data_returns_no_trigger(self) -> None:
        df = pd.DataFrame({"close": [100.0, 101.0]})
        df.attrs["capital"] = CapitalMetrics()
        detector = MarginExpansionDetector()
        value = detector.detect("000001.SZ", df, 10, TrackerThresholds())
        assert value.triggered is False
        assert "margin" in value.description.lower()


def _macd_cross_frame(segments: list[np.ndarray], cross_kind: str, dif_sign: int | None = None) -> pd.DataFrame:
    """Concatenate price segments and truncate at the first qualifying MACD cross.

    ``dif_sign`` filters the cross by the sign of DIF at the cross bar (``+1``
    for above-zero, ``-1`` for below-zero) so tests can target graded states.
    """
    close = pd.Series(np.concatenate(segments))
    dif, dea, _ = compute_macd(close)
    for i in range(1, len(close)):
        crossed = (
            cross_kind == "golden"
            and dif.iloc[i] > dea.iloc[i]
            and dif.iloc[i - 1] <= dea.iloc[i - 1]
        ) or (
            cross_kind == "death"
            and dif.iloc[i] < dea.iloc[i]
            and dif.iloc[i - 1] >= dea.iloc[i - 1]
        )
        if not crossed:
            continue
        if dif_sign is not None and float(dif.iloc[i]) * dif_sign <= 0:
            continue
        return pd.DataFrame({"close": close.iloc[: i + 1]})
    raise AssertionError(f"no {cross_kind} MACD cross found")


def _kdj_cross_frame(segments: list[np.ndarray], cross_kind: str) -> pd.DataFrame:
    """Concatenate price segments and truncate at the first KDJ K/D cross."""
    close = pd.Series(np.concatenate(segments))
    df = pd.DataFrame({"close": close, "high": close + 1.0, "low": close - 1.0})
    k, d, _ = compute_kdj(df)
    for i in range(1, len(close)):
        if cross_kind == "golden" and k.iloc[i] > d.iloc[i] and k.iloc[i - 1] <= d.iloc[i - 1]:
            return df.iloc[: i + 1]
        if cross_kind == "death" and k.iloc[i] < d.iloc[i] and k.iloc[i - 1] >= d.iloc[i - 1]:
            return df.iloc[: i + 1]
    raise AssertionError(f"no {cross_kind} KDJ cross found")


class TestMacdDetector:
    def test_meta_registered(self) -> None:
        meta = get_detector_meta("macd")
        assert meta.category == "trend"
        assert meta.direction == "both"
        assert meta.format == "raw"
        assert meta.params["macd_fast"]["default"] == 12.0

    def test_golden_cross_above_zero_is_strong(self) -> None:
        df = _macd_cross_frame(
            [np.linspace(100, 98, 5), np.linspace(98, 130, 30), np.linspace(130, 122, 10), np.linspace(122, 140, 20)],
            "golden",
            dif_sign=1,
        )
        value = MacdDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value == "strong"
        assert value.direction == "bullish"
        assert "above zero" in value.description

    def test_golden_cross_below_zero_is_triggered(self) -> None:
        df = _macd_cross_frame([np.linspace(20, 12, 40), np.linspace(12, 24, 40)], "golden", dif_sign=-1)
        value = MacdDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value == "triggered"
        assert value.direction == "bullish"
        assert "below zero" in value.description

    def test_death_cross_is_bearish(self) -> None:
        df = _macd_cross_frame([np.linspace(12, 20, 40), np.linspace(20, 10, 40)], "death")
        value = MacdDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value == "triggered"
        assert value.direction == "bearish"
        assert "death cross" in value.description

    def test_no_cross_returns_no_trigger(self) -> None:
        df = pd.DataFrame({"close": np.linspace(100, 200, 60)})
        value = MacdDetector().detect("000001.SZ", df, 20, TrackerThresholds())
        assert value.triggered is False


class TestKdjDetector:
    def test_meta_registered(self) -> None:
        meta = get_detector_meta("kdj")
        assert meta.category == "momentum"
        assert meta.direction == "both"
        assert meta.format == "raw"
        assert meta.params["kdj_n"]["default"] == 9.0

    def test_golden_cross_is_bullish(self) -> None:
        df = _kdj_cross_frame([np.linspace(20, 12, 40), np.linspace(12, 24, 40)], "golden")
        value = KdjDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value in ("triggered", "strong")
        assert value.direction == "bullish"
        assert "golden cross" in value.description

    def test_death_cross_is_bearish(self) -> None:
        df = _kdj_cross_frame([np.linspace(12, 20, 40), np.linspace(20, 10,40)], "death")
        value = KdjDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.direction == "bearish"
        assert "death cross" in value.description

    def test_no_cross_returns_no_trigger(self) -> None:
        df = pd.DataFrame(
            {"close": np.linspace(100, 200, 60), "high": np.linspace(100, 200, 60) + 1.0, "low": np.linspace(100, 200, 60) - 1.0}
        )
        value = KdjDetector().detect("000001.SZ", df, 20, TrackerThresholds())
        assert value.triggered is False


class TestBollingerPctBDetector:
    def test_meta_registered(self) -> None:
        meta = get_detector_meta("bollinger_pct_b")
        assert meta.category == "momentum"
        assert meta.direction == "both"
        assert meta.params["bb_n"]["default"] == 20.0

    def test_close_above_upper_band_is_strong_bullish(self) -> None:
        df = pd.DataFrame({"close": [10.0] * 30 + [20.0]})
        value = BollingerPctBDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value == "strong"
        assert value.direction == "bullish"
        assert value.value is not None and value.value > 1.0

    def test_close_below_lower_band_is_bearish(self) -> None:
        df = pd.DataFrame({"close": [10.0] * 30 + [0.0]})
        value = BollingerPctBDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value == "triggered"
        assert value.direction == "bearish"
        assert value.value is not None and value.value < 0.0

    def test_within_band_returns_no_trigger(self) -> None:
        df = pd.DataFrame({"close": np.linspace(100, 110, 30)})
        value = BollingerPctBDetector().detect("000001.SZ", df, 20, TrackerThresholds())
        assert value.triggered is False


class TestBollingerSqueezeDetector:
    def test_meta_registered(self) -> None:
        meta = get_detector_meta("bollinger_squeeze")
        assert meta.category == "volatility"
        assert meta.direction == "neutral"
        assert meta.ranking_enabled is False

    def test_squeeze_is_neutral_strong(self) -> None:
        # Long high-volatility run then a sharp collapse to flat -> bandwidth at
        # a historically low percentile.
        oscillating = 100 + np.sin(np.linspace(0, 20 * np.pi, 100)) * 10
        df = pd.DataFrame({"close": list(oscillating) + [100.0] * 20})
        value = BollingerSqueezeDetector().detect("000001.SZ", df, 20, TrackerThresholds())

        assert value.triggered is True
        assert value.state.value == "strong"
        assert value.direction == "neutral"
        assert value.value is not None and value.value < 0.05

    def test_normal_bandwidth_returns_no_trigger(self) -> None:
        oscillating = 100 + np.sin(np.linspace(0, 20 * np.pi, 100)) * 10
        df = pd.DataFrame({"close": list(oscillating)})
        value = BollingerSqueezeDetector().detect("000001.SZ", df, 20, TrackerThresholds())
        assert value.triggered is False


def _top_divergence_frame() -> pd.DataFrame:
    close = pd.Series(
        list(np.linspace(100, 150, 30))
        + list(np.linspace(150, 110, 15))
        + list(np.linspace(110, 155, 20))
        + list(np.linspace(155, 150, 4))
    )
    return pd.DataFrame({"close": close, "high": close + 0.5, "low": close - 0.5})


def _bottom_divergence_frame() -> pd.DataFrame:
    close = pd.Series(
        list(np.linspace(100, 50, 30))
        + list(np.linspace(50, 90, 15))
        + list(np.linspace(90, 45, 20))
        + list(np.linspace(45, 50, 4))
    )
    return pd.DataFrame({"close": close, "high": close + 0.5, "low": close - 0.5})


class TestDivergenceDetector:
    def test_meta_registered(self) -> None:
        meta = get_detector_meta("divergence")
        assert meta.category == "momentum"
        assert meta.direction == "both"
        assert meta.params["divergence_pivot"]["default"] == 3.0

    def test_top_divergence_is_bearish(self) -> None:
        # Pass a large enough period so the lookback covers both swing highs.
        value = DivergenceDetector().detect("000001.SZ", _top_divergence_frame(), 100, TrackerThresholds())

        assert value.triggered is True
        assert value.direction == "bearish"
        assert value.value is not None and value.value < 0
        assert "top divergence" in value.description.lower()

    def test_bottom_divergence_is_bullish(self) -> None:
        value = DivergenceDetector().detect("000001.SZ", _bottom_divergence_frame(), 100, TrackerThresholds())

        assert value.triggered is True
        assert value.direction == "bullish"
        assert value.value is not None and value.value > 0
        assert "bottom divergence" in value.description.lower()

    def test_no_divergence_returns_no_trigger(self) -> None:
        close = np.linspace(100, 200, 60)
        df = pd.DataFrame({"close": close, "high": close + 0.5, "low": close - 0.5})
        value = DivergenceDetector().detect("000001.SZ", df, 100, TrackerThresholds())
        assert value.triggered is False
