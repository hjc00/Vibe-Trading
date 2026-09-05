"""Unit tests for the composable stock-tracker backtest reader.

Covers the pure building blocks: primitives, edge/trigger transforms, rule
AND/OR evaluation, preset specs, the generated signal contract, and the
never-raises invalid-spec path. The real engine execution hits the network and
is exercised by the ``integration``-marked tests separately.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.stock_tracker.backtest_data import (
    _condition_series,
    _coerce_params_for,
    _cross_down,
    _cross_up,
    _indicator_panels,
    _read_price_points,
    _read_trades,
    _rule_series,
    _same_day_signal,
    _validate_spec,
    PRIMITIVES,
    TRIGGER_EDGE_DOWN,
    TRIGGER_EDGE_UP,
    TRIGGER_STATE,
    build_signal_engine,
    list_presets,
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
    for meta in metas.values():
        assert meta["label"]
        assert meta["description"]
        assert meta["triggers"]
        assert "compute" not in meta
        assert set(meta["params"][0]) >= {"key", "label", "default", "min", "max"}


@pytest.mark.unit
def test_list_presets_returns_five_filled_templates() -> None:
    presets = list_presets()
    assert len(presets) == 5
    for preset in presets:
        assert preset["id"] and preset["label"]
        spec = preset["spec"]
        assert spec["buy"]["conditions"] and spec["sell"]["conditions"]


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


