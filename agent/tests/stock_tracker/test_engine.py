"""Unit tests for the stock tracker engine."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.stock_tracker.engine import StockTrackerEngine
from src.stock_tracker.models import CapitalMetrics, MarginSnapshot, TrackerConfig, TrackerThresholds


def _make_df(rows: int = 80, volume_spike_idx: int | None = None) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame for testing."""
    dates = pd.date_range(end="2026-08-31", periods=rows, freq="B")
    base = pd.DataFrame(
        {
            "open": [100.0 + i * 0.1 for i in range(rows)],
            "high": [100.5 + i * 0.1 for i in range(rows)],
            "low": [99.5 + i * 0.1 for i in range(rows)],
            "close": [100.0 + i * 0.1 for i in range(rows)],
            "volume": [10000 + (i % 5) * 1000 for i in range(rows)],
        },
        index=dates,
    )
    if volume_spike_idx is not None:
        base.iloc[volume_spike_idx, base.columns.get_loc("volume")] *= 3
    return base


def test_volume_spike_triggered():
    df = _make_df(volume_spike_idx=-1)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", df)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["volume_spike"]
    assert signal.triggered is True
    assert signal.value >= 2.0


def test_volume_spike_not_triggered():
    df = _make_df()
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", df)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["volume_spike"]
    assert signal.triggered is False


def test_breakout_above_recent_high():
    df = _make_df()
    # Make the latest close clearly above the previous 20-day high.
    df.iloc[-1, df.columns.get_loc("close")] = df.iloc[-21:-1]["high"].max() * 1.05
    df.iloc[-1, df.columns.get_loc("high")] = df.iloc[-1, df.columns.get_loc("close")]

    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", df)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["breakout"]
    assert signal.triggered is True
    assert "above" in signal.description.lower()


def test_ma_alignment_bullish():
    df = _make_df()
    # Force a strong uptrend so MAs are ordered 5 > 10 > 20 > 60.
    for i in range(len(df)):
        df.iloc[i, df.columns.get_loc("close")] = 100.0 + i * 1.0
        df.iloc[i, df.columns.get_loc("high")] = df.iloc[i, df.columns.get_loc("close")] + 0.5
        df.iloc[i, df.columns.get_loc("low")] = df.iloc[i, df.columns.get_loc("close")] - 0.5
        df.iloc[i, df.columns.get_loc("open")] = df.iloc[i, df.columns.get_loc("close")] - 0.1

    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", df)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["ma_alignment"]
    assert signal.triggered is True
    assert "bullish" in signal.description.lower()


def test_records_to_dataframe_parses_date_column():
    records = [
        {"date": "2026-08-27", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10000},
        {"date": "2026-08-28", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 11000},
        {"date": "2026-08-29", "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 12000},
    ]
    df = StockTrackerEngine._records_to_dataframe(records)

    assert not df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3
    assert df.index[0].date().isoformat() == "2026-08-27"


def _make_oscillating_df(rows: int = 80, final_close_multiplier: float = 1.0) -> pd.DataFrame:
    """Build a DataFrame with up/down closes so RSI is well-defined."""
    dates = pd.date_range(end="2026-08-31", periods=rows, freq="B")
    close = 100.0
    closes = []
    for i in range(rows):
        # Alternate small up/down days to keep RSI in a neutral, computable range.
        change = 0.2 if i % 2 == 0 else -0.1
        close += change
        closes.append(close)
    closes[-1] *= final_close_multiplier

    base = pd.DataFrame(
        {
            "open": [c - 0.1 for c in closes],
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.3 for c in closes],
            "close": closes,
            "volume": [10000 + (i % 5) * 1000 for i in range(rows)],
        },
        index=dates,
    )
    return base


def test_rsi_overbought_triggered():
    df = _make_oscillating_df(rows=80, final_close_multiplier=1.5)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", df)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["rsi"]
    assert signal.triggered is True
    assert "overbought" in signal.description.lower()


def test_rsi_not_triggered_in_neutral_zone():
    df = _make_oscillating_df(rows=80)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", df)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["rsi"]
    assert signal.triggered is False
    assert signal.value is not None


def test_compute_diff_detects_new_rsi_signal():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    df_neutral = _make_oscillating_df(rows=80)
    df_overbought = _make_oscillating_df(rows=80, final_close_multiplier=1.5)

    previous = engine._analyze_symbol("000001.SZ", df_neutral)
    current = engine._analyze_symbol("000001.SZ", df_overbought)

    from datetime import datetime, timezone
    from src.stock_tracker.models import TrackerSnapshot

    previous_snapshot = TrackerSnapshot(
        generated_at=datetime.now(timezone.utc),
        trading_date=None,
        config=config,
        symbols=[previous],
    )
    diff_map = engine._compute_diff_map([current], previous_snapshot)
    diff = diff_map["000001.SZ"]
    assert "rsi" in diff.new_signals


def test_compute_rankings_by_return():
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snap_a = engine._analyze_symbol("000001.SZ", _make_df(rows=80))
    snap_b = engine._analyze_symbol("000002.SZ", _make_df(rows=80))
    # Artificially boost B's return by inflating recent closes.
    for ps in snap_b.period_signals.values():
        ps.metrics.return_pct = 0.5
    for ps in snap_a.period_signals.values():
        ps.metrics.return_pct = 0.1

    rankings = StockTrackerEngine._compute_rankings([snap_a, snap_b])
    assert rankings["return_10"][0] == "000002.SZ"
    assert rankings["return_10"][1] == "000001.SZ"


def test_compute_rankings_includes_enabled_signals():
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snap_a = engine._analyze_symbol("000001.SZ", _make_df(rows=80))
    snap_b = engine._analyze_symbol("000002.SZ", _make_df(rows=80))

    rankings = StockTrackerEngine._compute_rankings([snap_a, snap_b])
    assert "return_10" in rankings
    assert "volume_spike" in rankings
    assert "rsi" in rankings
    assert "signal_count" in rankings
    # ma_alignment opts out of ranking.
    assert "ma_alignment" not in rankings


def test_compute_diff_detects_new_signal():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    df_no_spike = _make_df()
    df_spike = _make_df(volume_spike_idx=-1)

    current = engine._analyze_symbol("000001.SZ", df_spike)
    previous = engine._analyze_symbol("000001.SZ", df_no_spike)

    diff_map = engine._compute_diff_map([current], None)
    assert "000001.SZ" not in diff_map  # no previous snapshot

    # Build a minimal previous snapshot.
    from datetime import datetime, timezone
    from src.stock_tracker.models import TrackerSnapshot
    previous_snapshot = TrackerSnapshot(
        generated_at=datetime.now(timezone.utc),
        trading_date=None,
        config=config,
        symbols=[previous],
    )
    diff_map = engine._compute_diff_map([current], previous_snapshot)
    diff = diff_map["000001.SZ"]
    assert "volume_spike" in diff.new_signals


def test_config_threshold_override():
    config = TrackerConfig(
        watchlist=["000001.SZ"],
        periods=[10],
        thresholds=TrackerThresholds(volume_spike=5.0),
    )
    df = _make_df(volume_spike_idx=-1)
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", df)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["volume_spike"]
    # 3x spike is below the 5x threshold.
    assert signal.triggered is False


def test_capital_metrics_attached():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    df = _make_df(rows=80)

    capital = CapitalMetrics(
        margin=MarginSnapshot(
            trade_date=date.fromisoformat("2026-08-31"),
            financing_balance=100_000_000.0,
            financing_balance_change=5_000_000.0,
        ),
    )
    snapshot = engine._analyze_symbol("000001.SZ", df, capital=capital)

    assert snapshot.capital is not None
    assert snapshot.capital.margin.financing_balance == 100_000_000.0


def test_margin_expansion_signal_triggered():
    config = TrackerConfig(
        watchlist=["000001.SZ"],
        periods=[10],
        signals=["margin_expansion"],
    )
    engine = StockTrackerEngine(config)
    df = _make_df(rows=80)

    # 10% increase in financing balance vs prior day.
    capital = CapitalMetrics(
        margin=MarginSnapshot(
            financing_balance=110_000_000.0,
            financing_balance_change=10_000_000.0,
        ),
    )
    snapshot = engine._analyze_symbol("000001.SZ", df, capital=capital)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["margin_expansion"]
    assert signal.triggered is True
    assert signal.value is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
