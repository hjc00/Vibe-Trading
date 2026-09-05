"""Unit tests for the composable stock-tracker backtest reader.

Covers the pure building blocks: primitives, edge/trigger transforms, rule
AND/OR evaluation, preset specs, the generated signal contract, and the
never-raises invalid-spec path. The real engine execution hits the network and
is exercised by the ``integration``-marked tests separately.
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd
import pytest

from src.stock_tracker.backtest_data import (
    _condition_series,
    _coerce_params_for,
    _cross_down,
    _cross_up,
    _indicator_panels,
    _kdj_divergence_state,
    _kdj_pin_state,
    _read_price_points,
    _read_trades,
    _rule_series,
    _same_day_signal,
    _simulate_targets,
    _validate_spec,
    PRIMITIVE_CATEGORIES,
    PRIMITIVES,
    TRIGGER_EDGE_DOWN,
    TRIGGER_EDGE_UP,
    TRIGGER_STATE,
    build_signal_engine,
    list_presets,
    list_primitive_categories,
    list_primitives,
    run_backtest_for_symbol,
)
from src.stock_tracker.models import BacktestSnapshot


def _ohlcv_frame(n: int = 400, seed: int = 7) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, len(idx)))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1e5, 1e6, len(idx)).astype(float),
        },
        index=idx,
    )


@pytest.mark.unit
def test_list_primitives_returns_registry_without_callables() -> None:
    metas = {m["id"]: m for m in list_primitives()}
    assert set(metas) == set(PRIMITIVES)
    category_ids = {c["id"] for c in PRIMITIVE_CATEGORIES}
    for meta in metas.values():
        assert meta["label"]
        assert meta["description"]
        assert meta["triggers"]
        assert "compute" not in meta
        assert set(meta["params"][0]) >= {"key", "label", "default", "min", "max"}
        # Every primitive belongs to a declared category.
        assert meta["category"] in category_ids
    # Every declared category is populated by at least one primitive.
    used = {meta["category"] for meta in metas.values()}
    assert used == category_ids


@pytest.mark.unit
def test_list_primitive_categories_returns_ordered_catalog() -> None:
    cats = list_primitive_categories()
    assert cats == PRIMITIVE_CATEGORIES
    assert [c["id"] for c in cats] == ["ma", "macd", "kdj", "rsi", "boll", "volume"]
    assert len({c["id"] for c in cats}) == len(cats)
    for cat in cats:
        assert cat["label"]


@pytest.mark.unit
def test_list_presets_returns_seven_filled_templates() -> None:
    presets = list_presets()
    assert len(presets) == 7
    for preset in presets:
        assert preset["id"] and preset["label"]
        spec = preset["spec"]
        assert spec["buy"]["conditions"] and spec["sell"]["conditions"]
    # KDJ 底背离预设：买=底背离事件，卖=对称的顶背离事件。
    kdj = next(p for p in presets if p["id"] == "kdj_divergence")
    assert kdj["label"]
    assert kdj["spec"]["buy"]["conditions"][0]["primitive"] == "kdj_bottom_divergence"
    assert kdj["spec"]["sell"]["conditions"][0]["primitive"] == "kdj_top_divergence"
    # KDJ 超卖钝化预设：买=超卖钝化，卖=对称的超买钝化。
    stag = next(p for p in presets if p["id"] == "kdj_stagnation")
    assert stag["spec"]["buy"]["conditions"][0]["primitive"] == "kdj_oversold_stagnation"
    assert stag["spec"]["sell"]["conditions"][0]["primitive"] == "kdj_overbought_stagnation"


@pytest.mark.unit
def test_edge_detection() -> None:
    series = pd.Series([True, True, False, False, True], index=range(5))
    assert _cross_up(series).tolist() == [True, False, False, False, True]
    assert _cross_down(series).tolist() == [False, False, True, False, False]
    assert _cross_up(pd.Series([False] * 4)).tolist() == [False] * 4


@pytest.mark.unit
def test_condition_trigger_transforms() -> None:
    frame = _ohlcv_frame(n=120)
    # state: identity on the primitive's boolean series
    state = _condition_series(
        {"primitive": "close_above_ma", "trigger": TRIGGER_STATE, "params": {"n": 5}},
        frame,
    )
    # edge_up must be a strict subset of bars true where state is true, and the
    # first bar true counts as an edge.
    edge = _condition_series(
        {"primitive": "close_above_ma", "trigger": TRIGGER_EDGE_UP, "params": {"n": 5}},
        frame,
    )
    assert edge.dtype == bool and state.dtype == bool
    assert (edge <= state).all()
    assert int(state.sum()) > 0 and int(edge.sum()) > 0

    # Unknown primitive / trigger degrade to all-False without raising.
    assert not _condition_series({"primitive": "nope"}, frame).any()
    assert not _condition_series(
        {"primitive": "close_above_ma", "trigger": "bogus"}, frame
    ).any()


@pytest.mark.unit
def test_rule_series_and_or_empty() -> None:
    frame = _ohlcv_frame(n=120)
    above = {"primitive": "close_above_ma", "trigger": TRIGGER_STATE, "params": {"n": 60}}
    below = {"primitive": "close_below_ma", "trigger": TRIGGER_STATE, "params": {"n": 20}}
    both_and = _rule_series({"mode": "and", "conditions": [above, below]}, frame)
    single = _rule_series({"mode": "or", "conditions": [above]}, frame)
    a = _rule_series({"mode": "and", "conditions": [above]}, frame)
    # AND of two conditions is tighter than either alone; a lone OR rule equals it.
    assert a.equals(single)
    assert both_and.le(a).all()
    # Empty/malformed rules are all-False.
    assert not _rule_series({"mode": "and", "conditions": []}, frame).any()
    assert not _rule_series("junk", frame).any()


@pytest.mark.unit
@pytest.mark.parametrize("preset", list_presets())
def test_preset_signal_contract(preset) -> None:
    """Each preset spec generates an aligned [-1,1] series with real trades."""
    engine = build_signal_engine(preset["spec"])
    data_map = {"600519.SH": _ohlcv_frame()}
    series = engine.generate(data_map)["600519.SH"]

    assert list(series.index) == list(data_map["600519.SH"].index)
    assert -1.0 <= float(series.min()) <= series.max() <= 1.0
    assert not series.isna().any()
    assert set(series.unique()).issubset({0.0, 1.0})
    assert (series > 0).any(), f"preset {preset['id']} never goes long"


@pytest.mark.unit
def test_signal_engine_survives_short_history() -> None:
    preset = list_presets()[0]
    engine = build_signal_engine(preset["spec"])
    short = _ohlcv_frame(n=3)
    series = engine.generate({"600519.SH": short})["600519.SH"]
    assert not series.isna().any()
    assert (series == 0.0).all()


@pytest.mark.unit
def test_coerce_params_clamps_by_schema() -> None:
    schema = PRIMITIVES["fast_ma_above_slow"]["params"]
    clean = _coerce_params_for(schema, {"fast": 999, "slow": 2, "junk": 1})
    assert clean == {"fast": 60, "slow": 5}
    float_schema = PRIMITIVES["volume_expansion"]["params"]
    assert _coerce_params_for(float_schema, {"mult": 9}) == {"mult": 5.0, "window": 20}


@pytest.mark.unit
@pytest.mark.parametrize(
    "spec",
    [
        None,
        {},
        {"buy": {"mode": "and", "conditions": []}, "sell": {"conditions": []}},
        {"buy": {"mode": "and", "conditions": [{"primitive": "nope"}]}, "sell": {"conditions": []}},
        {"buy": {"mode": "nand", "conditions": [{"primitive": "close_above_ma"}]}, "sell": {"conditions": []}},
    ],
)
def test_validate_spec_rejects_invalid(spec) -> None:
    assert _validate_spec(spec) != ""


@pytest.mark.unit
def test_validate_spec_accepts_valid() -> None:
    assert _validate_spec(list_presets()[0]["spec"]) == ""


@pytest.mark.unit
def test_run_invalid_spec_returns_error_snapshot() -> None:
    snap = run_backtest_for_symbol("600519.SH", {"buy": {"mode": "and", "conditions": []}})
    assert isinstance(snap, BacktestSnapshot)
    assert snap.error is not None
    assert snap.label == ""


@pytest.mark.unit
def test_backtest_snapshot_roundtrips() -> None:
    snap = BacktestSnapshot(
        code="600519.SH",
        label="双均线金叉",
        spec={"buy": {"mode": "and", "conditions": []}},
        total_return=-0.27,
        trade_count=3,
    )
    data = snap.model_dump(mode="json")
    assert data["code"] == "600519.SH"
    assert data["label"] == "双均线金叉"
    assert data["spec"]["buy"]["mode"] == "and"
    assert data["trade_count"] == 3
    assert data["equity_curve"] == []
    assert data["prices"] == []
    assert data["trades"] == []
    assert data["indicators"] == []


@pytest.mark.unit
def test_same_day_shift_maps_fill_to_trigger_day() -> None:
    """Engine executes next-open (pos[t]=sig[t-1]); pre-shift makes it same-day.

    A weight flipping to 1 at index 5 (trigger day) must surface as a signal at
    index 4 so the engine's +1 shift fills on bar 5.
    """
    weights = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    signal = _same_day_signal(weights)
    assert signal.iloc[4] == 1.0  # engine pos[5] = signal[4]
    assert signal.iloc[5] == 0.0  # already flat the bar after the trigger


@pytest.mark.unit
def test_entry_volume_exit_triggers() -> None:
    """卖出规则「量能≥买入当日量×倍数」：持仓中触发即平仓。"""
    idx = pd.date_range("2026-08-01", periods=12, freq="B")
    frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": [100.0] * 12,
        },
        index=idx,
    )
    frame.iloc[7, frame.columns.get_loc("volume")] = 300.0  # 3× the 100 entry volume
    buy = pd.Series([True] + [False] * 11, index=idx)

    weights = _simulate_targets(
        buy,
        None,
        frame,
        entry_volume_mults=[3.0],
        entry_volume_mode="and",
    )
    # Bought bar 0; exit on the volume-spike bar (index 7); flat from then on.
    assert float(weights.iloc[:7].min()) == 1.0
    assert float(weights.iloc[7:].sum()) == 0.0

    hold = _simulate_targets(buy, None, frame)  # no entry-volume condition
    assert float(hold.sum()) == 12.0


@pytest.mark.unit
def test_entry_volume_condition_reaches_engine() -> None:
    """The volume_vs_entry primitive registered & evaluated via spec/engine."""
    from src.stock_tracker.backtest_data import build_signal_engine

    spec = {
        "buy": {
            "mode": "and",
            "conditions": [
                {"primitive": "rsi_above", "trigger": TRIGGER_EDGE_UP, "params": {"period": 14, "threshold": 0}},
            ],
        },
        "sell": {
            "mode": "and",
            "conditions": [
                {"primitive": "volume_vs_entry", "trigger": TRIGGER_STATE, "params": {"mult": 3}},
            ],
        },
    }
    engine = build_signal_engine(spec)
    frame = _rising_frame()
    frame.loc[frame.index[9], "volume"] = 4_000_000.0  # 4× the entry-day volume
    series = engine.generate({"600519.SH": frame})["600519.SH"]
    assert (series >= 0.0).all()
    assert not (series == 0.0).all()  # goes long at some point
    assert not (series == 1.0).all()  # the volume condition eventually exits


@pytest.mark.unit
def test_allow_multiple_buys_toggle() -> None:
    """多笔开关：False=整段只买一次；True=平仓后再次满足买入可再开仓。"""
    idx = pd.date_range("2026-08-01", periods=8, freq="B")
    frame = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 101.0, "volume": [100.0] * 8},
        index=idx,
    )
    buy = pd.Series([True, False, False, True, False, False, False, False], index=idx)
    sell = pd.Series([False, False, True, False, False, False, False, False], index=idx)

    multi = _simulate_targets(buy, sell, frame, allow_multiple_buys=True)
    single = _simulate_targets(buy, sell, frame, allow_multiple_buys=False)
    # Multi: enter bar0, exit bar2, re-enter bar3.
    assert float(multi.iloc[0]) == 1.0
    assert float(multi.iloc[2]) == 0.0
    assert float(multi.iloc[3]) == 1.0
    assert float(multi.iloc[-1]) == 1.0
    # Single: after the first exit it stays flat — the bar-3 signal is ignored.
    assert float(single.iloc[0]) == 1.0
    assert float(single.iloc[2]) == 0.0
    assert float(single.iloc[3:].sum()) == 0.0


@pytest.mark.unit
def test_single_buy_ignores_warmup_signals() -> None:
    """Pre-evaluation warm-up bars must never consume a single-buy entry.

    A single-buy spec (``allow_multiple_buys=False``) is permitted one entry
    per run. The backtest fetches extra leading history to warm indicators, and
    that history must not be tradable — otherwise the one allowed buy can fire
    on an invisible warm-up bar, then every real in-window signal is ignored and
    the graded window reports zero trades. Passing ``eval_start`` to the signal
    engine masks pre-boundary buys so the single entry comes from the window.
    """
    close = [10.0, 12.0, 14.0, 12.0, 11.0, 15.0, 16.0, 17.0]
    idx = pd.date_range("2026-08-01", periods=8, freq="B")
    frame = pd.DataFrame(
        {
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [1e6] * 8,
        },
        index=idx,
    )
    # close>MA2 edges at bars 1 & 5; close<MA2 edge at bar 3 (exits the bar-1 buy).
    buy_rule = {
        "mode": "and",
        "conditions": [
            {"primitive": "close_above_ma", "trigger": TRIGGER_EDGE_UP, "params": {"n": 2}}
        ],
    }
    sell_rule = {
        "mode": "and",
        "conditions": [
            {"primitive": "close_below_ma", "trigger": TRIGGER_EDGE_UP, "params": {"n": 2}}
        ],
    }
    spec = {"buy": buy_rule, "sell": sell_rule, "allow_multiple_buys": False}
    data_map = {"600519.SH": frame}

    # Without eval_start the bar-1 (warm-up) signal eats the single buy, so the
    # window (bars 4..7) stays flat even though bar 5 is a fresh in-window signal.
    stale = build_signal_engine(spec).generate(data_map)["600519.SH"]
    assert float(stale.iloc[4:].sum()) == 0.0

    # With eval_start the pre-boundary buy is masked: bar 5 becomes the one
    # entry and the window holds long from there.
    fixed = build_signal_engine(spec, eval_start=idx[4]).generate(data_map)["600519.SH"]
    assert float(fixed.iloc[0:4].sum()) == 0.0
    assert float(fixed.iloc[4:].sum()) == 4.0


@pytest.mark.unit
def test_read_trades_parses_and_filters(tmp_path) -> None:
    import pandas as pd

    path = tmp_path / "trades.csv"
    pd.DataFrame(
        [
            {"timestamp": "2024-02-08", "side": "buy", "price": 1587.257},
            {"timestamp": "2024-03-01", "side": "sell", "price": 1550.0},
            {"timestamp": "2024-03-02", "side": "buy", "price": "nan"},
        ]
    ).to_csv(path, index=False)

    trades = _read_trades(path)
    assert [(t.side, t.price) for t in trades] == [("buy", 1587.257), ("sell", 1550.0)]
    assert trades[0].date == "2024-02-08"


@pytest.mark.unit
def test_read_price_points_skips_nan() -> None:
    frame = _ohlcv_frame(n=5)
    frame.loc[frame.index[2], "close"] = float("nan")
    points = _read_price_points(frame)
    assert len(points) == 4
    assert all(p.date and p.close > 0 for p in points)
    assert _read_price_points(pd.DataFrame({"x": [1, 2]})) == []


def _kdj_frame(n: int = 60) -> pd.DataFrame:
    """OHLCV where close/high/low move together — lets tests control extremes."""
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


@pytest.mark.unit
def test_kdj_divergence_primitives_registered() -> None:
    metas = {meta["id"]: meta for meta in list_primitives()}
    assert {"kdj_bottom_divergence", "kdj_top_divergence"} <= set(metas)
    for pid in ("kdj_bottom_divergence", "kdj_top_divergence"):
        defaults = {p["key"]: p["default"] for p in metas[pid]["params"]}
        assert defaults == {"n": 9, "m1": 3, "m2": 3, "pivot": 3}
    # 顶背离只宜作为卖出方向，避免被加进买入规则。
    assert metas["kdj_top_divergence"]["sell_only"] is True
    assert metas["kdj_bottom_divergence"].get("sell_only") is not True


@pytest.mark.unit
def test_kdj_stagnation_primitives_registered() -> None:
    metas = {meta["id"]: meta for meta in list_primitives()}
    assert {"kdj_oversold_stagnation", "kdj_overbought_stagnation"} <= set(metas)
    os_defaults = {p["key"]: p["default"] for p in metas["kdj_oversold_stagnation"]["params"]}
    ob_defaults = {p["key"]: p["default"] for p in metas["kdj_overbought_stagnation"]["params"]}
    assert os_defaults == {"n": 9, "m1": 3, "m2": 3, "threshold": 0, "lookback": 5}
    assert ob_defaults == {"n": 9, "m1": 3, "m2": 3, "threshold": 100, "lookback": 5}
    # 超买钝化只宜作为卖出方向；超卖钝化可作买入方向。
    assert metas["kdj_overbought_stagnation"]["sell_only"] is True
    assert metas["kdj_oversold_stagnation"].get("sell_only") is not True


def _pin_frame(lows: List[float], highs: List[float]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(lows), freq="B")
    return pd.DataFrame(
        {"open": lows, "high": highs, "low": lows, "close": lows, "volume": [1e6] * len(lows)},
        index=idx,
    )


@pytest.mark.unit
def test_kdj_oversold_stagnation_fires_when_price_holds_floor() -> None:
    """超卖钝化 = J 跌破阈值 且 价格未破前 N 日低点；一旦破位则不应触发。

    Price steps down once (bar 20) then holds its floor; J only dips sub-zero
    after the breakdown is over — so the earlier breakdown bars (fresh lows)
    must NOT fire even though J is deeply oversold, only the floor-holding bars.
    """
    lows = [100.0] * 25 + [90.0] * 15
    frame = _pin_frame(lows, [h * 1.01 for h in lows])
    j = pd.Series([50.0] * 25 + [-10.0] * 15, index=frame.index)  # oversold from bar 25
    out = _kdj_pin_state(frame, j, oversold=True, threshold=0.0, lookback=5)
    assert out.dtype == bool
    # Bar 25 broke the 100 floor (J is already oversold there) -> no fire;
    # bar 26+ holds 90 with J oversold -> fires.
    assert not out.iloc[:26].any()
    assert bool(out.iloc[26]) is True
    assert int(out.sum()) > 0
    # No fire when J never leaves the normal zone, or when the floor keeps breaking.
    j_neutral = pd.Series([50.0] * 40, index=frame.index)
    assert not _kdj_pin_state(frame, j_neutral, oversold=True, threshold=0.0, lookback=5).any()
    falling = [100.0 - i for i in range(40)]
    frame2 = _pin_frame(falling, [h * 1.01 for h in falling])
    j2 = pd.Series([-5.0] * 40, index=frame2.index)  # deep oversold the whole way
    assert not _kdj_pin_state(frame2, j2, oversold=True, threshold=0.0, lookback=5).any()


@pytest.mark.unit
def test_kdj_overbought_stagnation_is_top_mirror() -> None:
    """超买钝化 = J 突破阈值 且 价格未突破前 N 日高点（镜像于超卖版）。"""
    highs = [100.0] * 25 + [110.0] * 15
    frame = _pin_frame([h * 0.99 for h in highs], highs)
    j = pd.Series([50.0] * 25 + [150.0] * 15, index=frame.index)
    out = _kdj_pin_state(frame, j, oversold=False, threshold=100.0, lookback=5)
    assert out.dtype == bool
    assert not out.iloc[:26].any()  # bar 25 broke the 100 ceiling (J already hot)
    assert bool(out.iloc[26]) is True
    # Missing price column degrades to all-False.
    assert not _kdj_pin_state(pd.DataFrame({"close": [1.0] * 20}), j.iloc[:20], oversold=True, threshold=0.0, lookback=5).any()


@pytest.mark.unit
def test_kdj_stagnation_primitives_survive_via_rule_engine() -> None:
    """Stagnation primitives are usable as ordinary AND/OR conditions."""
    frame = _ohlcv_frame(n=400)
    buy = _condition_series(
        {"primitive": "kdj_oversold_stagnation", "trigger": TRIGGER_STATE, "params": {"n": 9, "threshold": 0, "lookback": 5}},
        frame,
    )
    sell = _condition_series(
        {"primitive": "kdj_overbought_stagnation", "trigger": TRIGGER_STATE, "params": {"n": 9, "threshold": 100, "lookback": 5}},
        frame,
    )
    assert buy.dtype == bool and sell.dtype == bool
    assert int(buy.sum()) + int(sell.sum()) > 0  # a wandering frame visits both tails


@pytest.mark.unit
def test_kdj_divergence_state_fires_on_confirmable_bar() -> None:
    """底背离 = 价格更低的第二个摆动低点、其 K 值更高，在摆动确认 bar 触发。

    A deeper swing low at bar 35 must not fire until bar 35+pivot (its ``pivot``-
    bar neighbourhood to the right is complete) — the divergence never signals on
    the still-unconfirmed low itself.
    """
    frame = _kdj_frame()
    # Uniform 100 baseline with exactly two isolated troughs: bar 15 (price 90)
    # then a deeper bar 35 (price 88). Flat plateaus never form swings (a swing
    # needs a strictly lower bar before it), so only the two troughs qualify.
    low = np.full(len(frame), 100.0)
    low[15] = 90.0
    low[35] = 88.0
    frame["low"] = low
    k = pd.Series(np.full(len(frame), 60.0), index=frame.index)
    k.iloc[15] = 20.0  # K at the first (higher) low
    k.iloc[35] = 42.0  # K at the deeper low is higher -> bottom divergence
    bottom = _kdj_divergence_state(frame, k, which="bottom", pivot=2)
    assert bottom.dtype == bool
    assert bottom.sum() == 1
    assert bool(bottom.iloc[37]) is True  # 35 + pivot(2): first confirmable bar
    assert not bottom.iloc[:37].any()

    # Without the higher K (indicator confirms the drop) nothing fires.
    k_flat = pd.Series(np.full(len(frame), 60.0), index=frame.index)
    assert not _kdj_divergence_state(frame, k_flat, which="bottom", pivot=2).any()

    # Top is the mirror: higher swing high whose K is lower fires on its confirm bar.
    high = np.full(len(frame), 100.0)
    high[15] = 110.0
    high[35] = 115.0
    frame["high"] = high
    k2 = pd.Series(np.full(len(frame), 40.0), index=frame.index)
    k2.iloc[15] = 80.0
    k2.iloc[35] = 62.0
    top = _kdj_divergence_state(frame, k2, which="top", pivot=2)
    assert top.sum() == 1
    assert bool(top.iloc[37]) is True

    # Too-short history and missing price columns degrade to all-False.
    assert not _kdj_divergence_state(_kdj_frame(n=6), k, which="bottom", pivot=2).any()
    assert not _kdj_divergence_state(pd.DataFrame({"close": [1.0] * 20}), k, which="bottom", pivot=2).any()


@pytest.mark.unit
def test_kdj_divergence_primitives_survive_via_rule_engine() -> None:
    """Divergence primitives are usable as ordinary AND/OR conditions."""
    frame = _ohlcv_frame(n=400)
    buy = _condition_series(
        {"primitive": "kdj_bottom_divergence", "trigger": TRIGGER_STATE, "params": {"n": 9, "m1": 3, "m2": 3, "pivot": 3}},
        frame,
    )
    sell = _condition_series(
        {"primitive": "kdj_top_divergence", "trigger": TRIGGER_STATE, "params": {"n": 9, "m1": 3, "m2": 3, "pivot": 3}},
        frame,
    )
    assert buy.dtype == bool and sell.dtype == bool
    assert int(buy.sum()) > 0  # a wandering frame contains real bottom divergences
    # Event pulses are isolated (never adjacent) by the pivot confirmation.
    indices = buy[buy].index
    assert len(indices) >= 2
    assert all((indices[i + 1] - indices[i]).days >= 1 for i in range(len(indices) - 1))


def _rising_frame(n: int = 60, start: float = 100.0, step: float = 2.0) -> pd.DataFrame:
    """Strictly rising OHLCV: any 'long once' rule trends up monotonically."""
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = start + step * np.arange(n)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


@pytest.mark.unit
def test_kdj_j_primitives_registered() -> None:
    metas = {meta["id"]: meta for meta in list_primitives()}
    assert {"kdj_j_above", "kdj_j_below"} <= set(metas)
    # Standard KDJ(9,3,3): N window + M1/M2 K-D smoothing, plus the J threshold.
    for pid in ("k_above_d", "kdj_j_above", "kdj_j_below"):
        defaults = {p["key"]: p["default"] for p in metas[pid]["params"]}
        assert defaults["n"] == 9 and defaults["m1"] == 3 and defaults["m2"] == 3
    frame = _ohlcv_frame(n=120)
    below = _condition_series(
        {"primitive": "kdj_j_below", "trigger": TRIGGER_STATE, "params": {"n": 9, "threshold": 20}},
        frame,
    )
    above = _condition_series(
        {"primitive": "kdj_j_above", "trigger": TRIGGER_STATE, "params": {"n": 9, "threshold": 100}},
        frame,
    )
    assert below.dtype == bool and above.dtype == bool
    assert int(below.sum()) > 0  # a wandering frame dips below J=20 at some point


@pytest.mark.unit
def test_take_profit_limits_a_long() -> None:
    """With a one-shot buy on a rising stock, TP closes the position and never re-enters."""
    frame = _rising_frame()
    spec = {
        "buy": {
            "mode": "and",
            "conditions": [{"primitive": "rsi_above", "trigger": TRIGGER_EDGE_UP, "params": {"period": 14, "threshold": 0}}],
        },
        "sell": {"mode": "and", "conditions": []},
        "take_profit_pct": 0.05,
    }
    series = build_signal_engine(spec).generate({"600519.SH": frame})["600519.SH"]
    ones = (series == 1.0).tolist()
    # Bought on the first bar, then flat from the TP bar onward (never re-enters).
    assert ones[0] is True
    assert sum(ones) < len(frame)
    first_flat = ones.index(False) if False in ones else len(ones)
    assert sum(ones[first_flat:]) == 0  # no long position after the take-profit exit
    assert sum(ones) <= 10


@pytest.mark.unit
def test_sell_disabled_holds_to_end() -> None:
    """Empty/omitted sell rule (禁止卖出) → buy once and hold through the window."""
    frame = _rising_frame()
    spec = {
        "buy": {
            "mode": "and",
            "conditions": [{"primitive": "rsi_above", "trigger": TRIGGER_EDGE_UP, "params": {"period": 14, "threshold": 0}}],
        },
    }
    series = build_signal_engine(spec).generate({"600519.SH": frame})["600519.SH"]
    assert (series == 1.0).all()


@pytest.mark.unit
def test_indicator_panels_derive_only_used_primitives() -> None:
    frame = _ohlcv_frame(n=90)
    spec = {
        "buy": {
            "mode": "and",
            "conditions": [
                {"primitive": "fast_ma_above_slow", "trigger": TRIGGER_EDGE_UP, "params": {"fast": 5, "slow": 20}},
                {"primitive": "dif_above_dea", "trigger": TRIGGER_STATE, "params": {"fast": 12, "slow": 26, "signal": 9}},
                {"primitive": "kdj_j_below", "trigger": TRIGGER_STATE, "params": {"n": 9, "threshold": 20}},
            ],
        },
        "sell": {
            "mode": "and",
            "conditions": [
                {"primitive": "close_above_boll_mid", "trigger": TRIGGER_STATE, "params": {"n": 20, "k": 2}},
                {"primitive": "rsi_above", "trigger": TRIGGER_STATE, "params": {"period": 14, "threshold": 70}},
            ],
        },
    }
    panels = _indicator_panels(spec, frame)
    kinds = {p.kind for p in panels}
    assert {"ma", "boll", "macd", "kdj", "rsi"} <= kinds
    for panel in panels:
        assert panel.series
        assert len(panel.series[0].values) == len(frame)  # aligned to price dates
        for series in panel.series:
            assert len(series.values) == len(frame)
    # Same primitive appearing in buy and sell must not duplicate the panel.
    repeated = {
        "buy": {"mode": "and", "conditions": [{"primitive": "dif_above_dea", "trigger": TRIGGER_EDGE_UP, "params": {"fast": 12, "slow": 26, "signal": 9}}]},
        "sell": {"mode": "and", "conditions": [{"primitive": "dif_above_dea", "trigger": TRIGGER_EDGE_DOWN, "params": {"fast": 12, "slow": 26, "signal": 9}}]},
    }
    macd_panels = [p for p in _indicator_panels(repeated, frame) if p.kind == "macd"]
    assert len(macd_panels) == 1
    assert _indicator_panels({}, frame) == []
    assert _indicator_panels(None, frame) == []


