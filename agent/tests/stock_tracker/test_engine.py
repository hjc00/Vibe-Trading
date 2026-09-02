"""Unit tests for the stock tracker engine."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.stock_tracker.engine import StockTrackerEngine
from src.stock_tracker.models import (
    CapitalMetrics,
    EventSnapshot,
    FundFlowHistoryItem,
    FundFlowSnapshot,
    MarginSnapshot,
    SymbolSnapshot,
    TrackerConfig,
    TrackerSnapshot,
    TrackerThresholds,
    ValuationSnapshot,
)


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


def _make_fund_flow_capital(days: int = 30, *, spike_main: float | None = None) -> CapitalMetrics:
    """Build ``CapitalMetrics`` with a fund-flow history ending today."""
    base_date = date(2026, 8, 31)
    history: list[FundFlowHistoryItem] = []
    for i in range(days):
        trade_date = base_date - timedelta(days=days - 1 - i)
        if i == days - 1 and spike_main is not None:
            main_net = spike_main
        else:
            # Mostly positive so both signals can be exercised.
            main_net = 100_000.0 + i * 10_000.0
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

    snapshot = FundFlowSnapshot(
        trade_date=base_date,
        main_net=history[-1].main_net,
        history=history,
    )
    return CapitalMetrics(fund_flow=snapshot)


def test_net_inflow_spike_signal_triggered():
    config = TrackerConfig(
        watchlist=["000001.SZ"],
        periods=[10],
        signals=["net_inflow_spike"],
    )
    engine = StockTrackerEngine(config)
    df = _make_df(rows=80)
    capital = _make_fund_flow_capital(30, spike_main=10_000_000.0)

    snapshot = engine._analyze_symbol("000001.SZ", df, capital=capital)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["net_inflow_spike"]
    assert signal.triggered is True
    assert signal.value is not None


def test_main_force_inflow_signal_triggered():
    config = TrackerConfig(
        watchlist=["000001.SZ"],
        periods=[10],
        signals=["main_force_inflow"],
    )
    engine = StockTrackerEngine(config)
    df = _make_df(rows=80)
    capital = _make_fund_flow_capital(30)

    snapshot = engine._analyze_symbol("000001.SZ", df, capital=capital)

    ps = snapshot.period_signals["10"]
    signal = ps.signals["main_force_inflow"]
    assert signal.triggered is True
    assert signal.value is not None


def test_compute_rankings_includes_new_capital_signals():
    config = TrackerConfig(
        watchlist=["000001.SZ", "000002.SZ"],
        periods=[10],
        signals=["net_inflow_spike", "main_force_inflow"],
    )
    engine = StockTrackerEngine(config)

    df_a = _make_df(rows=80)
    df_b = _make_df(rows=80)
    snap_a = engine._analyze_symbol("000001.SZ", df_a, capital=_make_fund_flow_capital(30, spike_main=10_000_000.0))
    snap_b = engine._analyze_symbol("000002.SZ", df_b, capital=_make_fund_flow_capital(30))

    rankings = StockTrackerEngine._compute_rankings([snap_a, snap_b])
    assert "net_inflow_spike" in rankings
    assert "main_force_inflow" in rankings
    # The spike should rank higher than the steady inflow.
    assert rankings["net_inflow_spike"][0] == "000001.SZ"


def _make_df_with_return(rows: int = 80, *, return_pct: float = 0.0) -> pd.DataFrame:
    """Build a DataFrame whose last 10-bar return equals ``return_pct``."""
    dates = pd.date_range(end="2026-08-31", periods=rows, freq="B")
    start_price = 100.0
    end_price = start_price * (1 + return_pct)
    # Flat path until the last 10 bars, then linear ramp to the target end price.
    closes: list[float] = []
    ramp_start = rows - 10
    for i in range(rows):
        if i < ramp_start:
            closes.append(start_price)
        else:
            closes.append(start_price + (end_price - start_price) * (i - ramp_start) / 9)
    return pd.DataFrame(
        {
            "open": [c - 0.1 for c in closes],
            "high": [c + 0.1 for c in closes],
            "low": [c - 0.1 for c in closes],
            "close": closes,
            "volume": [10000 + (i % 5) * 1000 for i in range(rows)],
        },
        index=dates,
    )


def test_rps_market_computed_with_benchmark():
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ", "000003.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    # Symbol returns: -5%, 0%, +5%.
    snapshots = [
        engine._analyze_symbol("000001.SZ", _make_df_with_return(return_pct=-0.05)),
        engine._analyze_symbol("000002.SZ", _make_df_with_return(return_pct=0.0)),
        engine._analyze_symbol("000003.SZ", _make_df_with_return(return_pct=0.05)),
    ]

    # Benchmark return 2%.
    benchmark_df = _make_df_with_return(return_pct=0.02)

    engine._compute_and_attach_rps(snapshots, benchmark_df)

    market_rps = {s.code: s.period_signals["10"].metrics.rps_market for s in snapshots}
    # Universe: -5%, 0%, +5%, +2%. Sorted: -5% < 0% < +2% < +5%.
    # rank(min): 1, 2, 3, 4. pct = (rank - 1) / (4 - 1) * 100.
    assert market_rps["000001.SZ"] == 0.0
    assert market_rps["000002.SZ"] == pytest.approx(33.33, abs=0.01)
    assert market_rps["000003.SZ"] == 100.0

    for s in snapshots:
        assert s.period_signals["10"].metrics.benchmark_return_pct == pytest.approx(0.02, abs=1e-6)


def test_rps_sector_groups_by_board():
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ", "000003.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snapshots = [
        engine._analyze_symbol("000001.SZ", _make_df_with_return(return_pct=-0.05), sector_board="Bank"),
        engine._analyze_symbol("000002.SZ", _make_df_with_return(return_pct=0.0), sector_board="Bank"),
        engine._analyze_symbol("000003.SZ", _make_df_with_return(return_pct=0.05), sector_board="Tech"),
    ]

    engine._compute_and_attach_rps(snapshots, None)

    sector_rps = {s.code: s.period_signals["10"].metrics.rps_sector for s in snapshots}
    # Bank group: -5%, 0%. rank(min): 1, 2. pct = (rank - 1) / (2 - 1) * 100.
    assert sector_rps["000001.SZ"] == 0.0
    assert sector_rps["000002.SZ"] == 100.0
    # Tech group has only one member.
    assert sector_rps["000003.SZ"] is None


def test_rps_rankings_added():
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snapshots = [
        engine._analyze_symbol("000001.SZ", _make_df_with_return(return_pct=-0.05)),
        engine._analyze_symbol("000002.SZ", _make_df_with_return(return_pct=0.05)),
    ]
    engine._compute_and_attach_rps(snapshots, None)

    rankings = StockTrackerEngine._compute_rankings(snapshots)
    assert "rps_market_10" in rankings
    assert "rps_sector_10" in rankings
    assert rankings["rps_market_10"][0] == "000002.SZ"


def test_rps_fallback_when_benchmark_missing():
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snapshots = [
        engine._analyze_symbol("000001.SZ", _make_df_with_return(return_pct=-0.05)),
        engine._analyze_symbol("000002.SZ", _make_df_with_return(return_pct=0.05)),
    ]

    engine._compute_and_attach_rps(snapshots, None)

    market_rps = {s.code: s.period_signals["10"].metrics.rps_market for s in snapshots}
    # Watchlist only: -5%, +5%. rank(min): 1, 2. pct = (rank - 1) / (2 - 1) * 100.
    assert market_rps["000001.SZ"] == 0.0
    assert market_rps["000002.SZ"] == 100.0
    for s in snapshots:
        assert s.period_signals["10"].metrics.benchmark_return_pct is None


def test_rps_sector_none_when_single_peer():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snapshot = engine._analyze_symbol("000001.SZ", _make_df_with_return(return_pct=0.05), sector_board="Bank")
    engine._compute_and_attach_rps([snapshot], None)

    assert snapshot.period_signals["10"].metrics.rps_sector is None


def test_risk_metrics_attached_with_benchmark():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    df = _make_df(rows=80)
    benchmark_df = _make_df_with_return(return_pct=0.02)

    snapshot = engine._analyze_symbol("000001.SZ", df, benchmark_df=benchmark_df)

    assert snapshot.risk is not None
    # _make_df has a constant high-low spread of 1.0 and midpoint closes.
    assert snapshot.risk.atr_14 == pytest.approx(1.0, abs=1e-6)
    assert snapshot.risk.atr_pct == pytest.approx(1.0 / snapshot.close, abs=1e-6)
    assert snapshot.risk.max_drawdown_60d is not None
    assert snapshot.risk.max_drawdown_60d <= 0.0
    assert snapshot.risk.beta_vs_index is not None
    assert snapshot.risk.beta_window == 60
    # stop loss = close - 2 * ATR (close = 100 + 0.1 * 79 = 107.9).
    assert snapshot.risk.stop_loss_price == pytest.approx(107.9 - 2 * 1.0, abs=1e-6)


def test_risk_beta_none_when_benchmark_missing():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snapshot = engine._analyze_symbol("000001.SZ", _make_df(rows=80), benchmark_df=None)

    assert snapshot.risk is not None
    assert snapshot.risk.beta_vs_index is None
    assert snapshot.risk.beta_window is None
    assert snapshot.risk.atr_14 is not None
    assert snapshot.risk.max_drawdown_60d is not None
    assert snapshot.risk.stop_loss_price is not None


def test_valuation_metrics_attached():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    df = _make_df(rows=80)

    valuation = ValuationSnapshot(
        pe_ttm=19.9,
        pb=6.4,
        fundamental_quality_score=88.0,
        source="eastmoney",
    )
    snapshot = engine._analyze_symbol("000001.SZ", df, valuation=valuation)

    assert snapshot.valuation is not None
    assert snapshot.valuation.pe_ttm == 19.9
    assert snapshot.valuation.pb == 6.4
    assert snapshot.valuation.fundamental_quality_score == 88.0


def test_events_metrics_attached():
    from datetime import date as _date, timedelta as _td

    from src.stock_tracker.models import EventItem, EventSnapshot

    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    df = _make_df(rows=80)

    as_of = _date(2026, 8, 31)
    events = EventSnapshot(
        as_of=as_of,
        source="eastmoney",
        event_risk_score=80.0,
        high_risk_count=1,
        items=[
            EventItem(
                event_type="lockup",
                event_date=as_of + _td(days=10),
                title="限售解禁 1.00 亿股",
                risk_level="danger",
                risk_score=80.0,
                days_until=10,
                source="eastmoney",
                details={"free_ratio": 0.15},
            )
        ],
    )
    snapshot = engine._analyze_symbol("000001.SZ", df, events=events)

    assert snapshot.events is not None
    assert snapshot.events.source == "eastmoney"
    assert snapshot.events.event_risk_score == 80.0
    assert snapshot.events.items[0].risk_level == "danger"


def test_events_none_when_not_provided():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", _make_df(rows=80))
    assert snapshot.events is None


def test_valuation_none_when_not_provided():
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snapshot = engine._analyze_symbol("000001.SZ", _make_df(rows=80))
    assert snapshot.valuation is None


def test_risk_stop_loss_multiple_override():
    config = TrackerConfig(
        watchlist=["000001.SZ"],
        periods=[10],
        thresholds=TrackerThresholds(stop_loss_atr_multiple=3.0),
    )
    engine = StockTrackerEngine(config)

    snapshot = engine._analyze_symbol("000001.SZ", _make_df(rows=80), benchmark_df=None)

    assert snapshot.risk is not None
    assert snapshot.risk.stop_loss_atr_multiple == 3.0
    assert snapshot.risk.stop_loss_price == pytest.approx(107.9 - 3 * 1.0, abs=1e-6)


def _make_previous_snapshot(
    config: TrackerConfig,
    trading_date: date,
    *,
    capital: CapitalMetrics | None,
    valuation: ValuationSnapshot | None,
    events: EventSnapshot | None = None,
) -> TrackerSnapshot:
    """Build a minimal previous snapshot carrying capital/valuation/events."""
    from datetime import datetime, timezone

    symbol = SymbolSnapshot(
        code="000001.SZ",
        name="Test",
        close=10.0,
        capital=capital,
        valuation=valuation,
        events=events,
    )
    return TrackerSnapshot(
        generated_at=datetime.now(timezone.utc),
        trading_date=trading_date,
        config=config,
        symbols=[symbol],
    )


def test_seed_caches_from_previous_same_day():
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    capital = CapitalMetrics(
        fund_flow=FundFlowSnapshot(trade_date=trading_date, main_net=100_000.0),
        margin=MarginSnapshot(trade_date=trading_date, financing_balance=1_000_000.0),
        fund_flow_source="eastmoney",
        margin_source="eastmoney",
    )
    valuation = ValuationSnapshot(trade_date=trading_date, pe_ttm=19.9, source="eastmoney")

    previous = _make_previous_snapshot(config, trading_date, capital=capital, valuation=valuation)
    engine._seed_caches_from_previous(previous, trading_date)

    assert engine._capital_cache.get("fund_flow", "000001.SZ", trading_date) is capital
    assert engine._capital_cache.get("margin", "000001.SZ", trading_date) is capital
    assert engine._valuation_cache.get("000001.SZ", trading_date) is valuation


def test_seed_caches_skipped_for_different_day():
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    capital = CapitalMetrics(fund_flow=FundFlowSnapshot(trade_date=trading_date, main_net=100_000.0))
    previous = _make_previous_snapshot(config, trading_date, capital=capital, valuation=None)

    # A new trading day must not reuse the previous day's data.
    engine._seed_caches_from_previous(previous, date(2026, 9, 1))
    assert engine._capital_cache.get("fund_flow", "000001.SZ", date(2026, 9, 1)) is None
    assert engine._valuation_cache.get("000001.SZ", date(2026, 9, 1)) is None


def test_seed_skips_capital_with_errors():
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    capital = CapitalMetrics(fund_flow_error="eastmoney blocked", margin_error="eastmoney blocked")
    previous = _make_previous_snapshot(config, trading_date, capital=capital, valuation=None)
    engine._seed_caches_from_previous(previous, trading_date)

    assert engine._capital_cache.get("fund_flow", "000001.SZ", trading_date) is None
    assert engine._capital_cache.get("margin", "000001.SZ", trading_date) is None


def test_seed_caches_events_from_previous_same_day():
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    events = EventSnapshot(as_of=trading_date, source="eastmoney", event_risk_score=72.0)
    previous = _make_previous_snapshot(config, trading_date, capital=None, valuation=None, events=events)
    engine._seed_caches_from_previous(previous, trading_date)

    assert engine._events_cache.get("000001.SZ", trading_date) is events

    # A different trading date must not reuse the previous day's events.
    engine._events_cache.clear()
    engine._seed_caches_from_previous(previous, date(2026, 9, 1))
    assert engine._events_cache.get("000001.SZ", date(2026, 9, 1)) is None


def test_seed_skips_events_with_errors():
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    events = EventSnapshot(as_of=trading_date, source="unavailable", error="lockup: boom")
    previous = _make_previous_snapshot(config, trading_date, capital=None, valuation=None, events=events)
    engine._seed_caches_from_previous(previous, trading_date)

    assert engine._events_cache.get("000001.SZ", trading_date) is None


def test_compute_sector_strength_attaches_rank(monkeypatch):
    from src.stock_tracker.models import SectorStrength

    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snap_a = engine._analyze_symbol("000001.SZ", _make_df(rows=80), sector_board="Bank")
    snap_b = engine._analyze_symbol("000002.SZ", _make_df(rows=80), sector_board="Bank")

    sectors = [
        SectorStrength(board_name="Bank", change_pct=1.5, market_rank=1, member_count=2),
        SectorStrength(board_name="Tech", change_pct=-0.5, market_rank=2),
    ]
    monkeypatch.setattr("src.stock_tracker.engine.load_sector_strength", lambda *a, **k: sectors)

    result = engine._compute_sector_strength([snap_a, snap_b])

    assert result == sectors
    assert snap_a.sector_strength_rank == 1
    assert snap_b.sector_strength_rank == 1


def test_compute_sector_strength_none_when_board_unranked(monkeypatch):
    from src.stock_tracker.models import SectorStrength

    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snap = engine._analyze_symbol("000001.SZ", _make_df(rows=80), sector_board="Bank")
    # Bank appears only in the watchlist aggregation, not in the ranking.
    sectors = [SectorStrength(board_name="Tech", change_pct=2.0, market_rank=1)]
    monkeypatch.setattr("src.stock_tracker.engine.load_sector_strength", lambda *a, **k: sectors)

    result = engine._compute_sector_strength([snap])

    assert len(result) == 1
    assert snap.sector_strength_rank is None


def test_compute_sector_strength_degrades_on_failure(monkeypatch):
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)

    snap = engine._analyze_symbol("000001.SZ", _make_df(rows=80), sector_board="Bank")

    def _boom(*args, **kwargs):
        raise RuntimeError("sector data unavailable")

    monkeypatch.setattr("src.stock_tracker.engine.load_sector_strength", _boom)

    result = engine._compute_sector_strength([snap])

    assert result == []
    assert snap.sector_strength_rank is None


# ---------------------------------------------------------------------------
# Same-trading-day sector reuse (改动1: skip throttled Eastmoney board calls)
# ---------------------------------------------------------------------------


def _snapshot_with_boards_and_sectors(
    trading_date: date,
    board_map: dict,
    sectors: list,
) -> "TrackerSnapshot":
    """Build a prior snapshot carrying per-symbol sector boards plus a sector list."""
    from datetime import datetime, timezone

    symbols = [SymbolSnapshot(code=code, sector_board=board) for code, board in board_map.items()]
    return TrackerSnapshot(
        generated_at=datetime.now(timezone.utc),
        trading_date=trading_date,
        config=TrackerConfig(),
        symbols=symbols,
        sectors=sectors,
    )


def test_resolve_sector_boards_reuses_previous_same_day(monkeypatch):
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    previous = _snapshot_with_boards_and_sectors(
        trading_date, {"000001.SZ": "Bank", "000002.SZ": "Tech"}, []
    )

    calls = {"n": 0}

    def _fake_resolve(code):
        calls["n"] += 1
        return "FakeBoard"

    monkeypatch.setattr("src.stock_tracker.engine.resolve_industry_board", _fake_resolve)

    boards = engine._resolve_sector_boards(previous, trading_date)

    assert boards == {"000001.SZ": "Bank", "000002.SZ": "Tech"}
    assert calls["n"] == 0


def test_resolve_sector_boards_fills_only_missing(monkeypatch):
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ", "000003.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    # Previous snapshot only resolved the first symbol.
    previous = _snapshot_with_boards_and_sectors(trading_date, {"000001.SZ": "Bank"}, [])

    resolved: list[str] = []

    def _fake_resolve(code):
        resolved.append(code)
        return {"000002.SZ": "Tech", "000003.SZ": "Bank"}[code]

    monkeypatch.setattr("src.stock_tracker.engine.resolve_industry_board", _fake_resolve)

    boards = engine._resolve_sector_boards(previous, trading_date)

    assert boards["000001.SZ"] == "Bank"
    assert sorted(resolved) == ["000002.SZ", "000003.SZ"]
    assert boards["000002.SZ"] == "Tech"
    assert boards["000003.SZ"] == "Bank"


def test_resolve_sector_boards_retries_unresolved_previous_board(monkeypatch):
    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ", "000002.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    # First symbol had no board last time (resolution failed) -> must be retried.
    previous = _snapshot_with_boards_and_sectors(trading_date, {"000001.SZ": None, "000002.SZ": "Tech"}, [])

    resolved: list[str] = []

    def _fake_resolve(code):
        resolved.append(code)
        return "Bank"

    monkeypatch.setattr("src.stock_tracker.engine.resolve_industry_board", _fake_resolve)

    boards = engine._resolve_sector_boards(previous, trading_date)

    assert resolved == ["000001.SZ"]
    assert boards["000001.SZ"] == "Bank"
    assert boards["000002.SZ"] == "Tech"


def test_resolve_sector_boards_refetches_on_new_trading_day(monkeypatch):
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    previous = _snapshot_with_boards_and_sectors(date(2026, 8, 31), {"000001.SZ": "Bank"}, [])

    resolved: list[str] = []

    def _fake_resolve(code):
        resolved.append(code)
        return "NewBoard"

    monkeypatch.setattr("src.stock_tracker.engine.resolve_industry_board", _fake_resolve)

    boards = engine._resolve_sector_boards(previous, date(2026, 9, 1))

    assert resolved == ["000001.SZ"]
    assert boards["000001.SZ"] == "NewBoard"


def test_cached_sector_ranking_reconstructs_same_day_sorted():
    from src.stock_tracker.models import SectorStrength

    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    sectors = [
        SectorStrength(board_name="Bank", board_code="BK01", change_pct=2.0, market_rank=2, source="eastmoney"),
        SectorStrength(board_name="Tech", board_code="BK02", change_pct=3.0, market_rank=1, source="eastmoney"),
        # Watchlist-only board (no whole-market rank) is not part of the ranking.
        SectorStrength(board_name="Standalone", source="watchlist"),
    ]
    previous = _snapshot_with_boards_and_sectors(trading_date, {}, sectors)

    ranking = engine._cached_sector_ranking(previous, trading_date)

    assert ranking is not None
    assert [r["board_name"] for r in ranking] == ["Tech", "Bank"]
    assert ranking[0]["board_code"] == "BK02"
    assert ranking[0]["change_pct"] == 3.0
    assert ranking[1]["leader"] is None


def test_cached_sector_ranking_none_for_new_day_or_empty():
    from src.stock_tracker.models import SectorStrength

    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    sectors = [SectorStrength(board_name="Bank", change_pct=2.0, market_rank=2, source="eastmoney")]

    previous = _snapshot_with_boards_and_sectors(trading_date, {}, sectors)
    assert engine._cached_sector_ranking(previous, date(2026, 9, 1)) is None

    empty_previous = _snapshot_with_boards_and_sectors(trading_date, {}, [])
    assert engine._cached_sector_ranking(empty_previous, trading_date) is None


def test_compute_sector_strength_reuses_previous_ranking(monkeypatch):
    from src.stock_tracker.models import SectorStrength

    trading_date = date(2026, 8, 31)
    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snap = engine._analyze_symbol("000001.SZ", _make_df(rows=80), sector_board="Bank")

    sectors = [SectorStrength(board_name="Bank", change_pct=1.0, market_rank=1, source="eastmoney")]
    previous = _snapshot_with_boards_and_sectors(trading_date, {"000001.SZ": "Bank"}, sectors)

    captured: dict = {}

    def _fake_load(snapshots, **kwargs):
        captured.update(kwargs)
        return sectors

    monkeypatch.setattr("src.stock_tracker.engine.load_sector_strength", _fake_load)

    result = engine._compute_sector_strength([snap], previous=previous, trading_date=trading_date)

    assert captured["ranking"] is not None
    assert captured["ranking"][0]["board_name"] == "Bank"
    assert result == sectors
    assert snap.sector_strength_rank == 1


def test_compute_sector_strength_fetches_without_previous(monkeypatch):
    from src.stock_tracker.models import SectorStrength

    config = TrackerConfig(watchlist=["000001.SZ"], periods=[10])
    engine = StockTrackerEngine(config)
    snap = engine._analyze_symbol("000001.SZ", _make_df(rows=80), sector_board="Bank")

    captured: dict = {}
    sectors = [SectorStrength(board_name="Bank", change_pct=1.0, market_rank=1, source="eastmoney")]

    def _fake_load(snapshots, **kwargs):
        captured.update(kwargs)
        return sectors

    monkeypatch.setattr("src.stock_tracker.engine.load_sector_strength", _fake_load)

    engine._compute_sector_strength([snap])

    assert captured["ranking"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
