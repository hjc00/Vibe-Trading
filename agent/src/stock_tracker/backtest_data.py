"""Single-symbol backtest reader with composable signal rules (单标的回测卡).

On-demand backtest that mirrors the ``financial_reports_data`` paradigm: it is
not part of the daily refresh; the frontend triggers it when the selected
symbol / rules / date range is set. The kernel reuses the real ``agent/backtest``
engine — the same ``fetch_data_map → _create_market_engine → run_backtest``
chain ``backtest/runner.main()`` uses — so results match what the Agent reports
for the same strategy, and this module never re-implements matching, fees or
the indicator ledger.

策略层是可组合的「信号原语」模型:
- 原语 (primitive) = 一个状态布尔序列 ``frame -> pd.Series[bool]``（例：收盘在
  MA 上方、DIF>DEA、RSI 高于阈值…），复用 ``indicators.py``/``signals.py`` 纯函数。
- 触发方式 (trigger): ``state``(持续满足) / ``edge_up``(刚满足=金叉/上穿) /
  ``edge_down``(刚脱离=死叉/下穿)。
- 规则 = 若干 条件(primitive+trigger+参数) 用整条 AND 或 OR 组合。
- 策略 = 买入规则 + 卖出规则；在买入条件刚满足时开多、卖出条件满足时平仓。

预设（双均线/MACD/KDJ/RSI/布林）只是若干 spec 的「一键填充」，可被用户自由增删
条件继续组合。新增原语 = 写一个 ``frame->bool`` 纯函数 + 注册一项，引擎无需改动。

设计原则:
- 永不抛异常：非法 spec / 网络 / 引擎失败降级为带 ``error`` 的 BacktestSnapshot。
- A股单标的走免费源 (tencent/eastmoney), 无需 token。
"""

from __future__ import annotations

import logging
import math
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from src.stock_tracker.indicators import (
    compute_bollinger,
    compute_kdj,
    compute_macd,
)
from src.stock_tracker.models import (
    BacktestIndicatorPanel,
    BacktestIndicatorSeries,
    BacktestPoint,
    BacktestPricePoint,
    BacktestSnapshot,
    BacktestTradePoint,
)
from src.stock_tracker.signals import compute_rsi

logger = logging.getLogger(__name__)

# Default backtest window when the caller omits dates: end = today, start ≈
# ``_DEFAULT_TRADING_DAYS`` trading sessions back. A-share weeks are 5 sessions
# plus public holidays, so ``_LOOKBACK_CALENDAR_DAYS`` estimates the calendar
# span that guarantees at least that many daily bars (slightly generous — the
# extra session(s) are simply part of the graded window).
_DEFAULT_TRADING_DAYS = 60
_LOOKBACK_CALENDAR_DAYS = int(_DEFAULT_TRADING_DAYS * 1.45) + 5
# Leading history pulled *before* the requested start so indicators (KDJ/MA/
# MACD/RSI/Bollinger) and signals are computed on a fully warmed series — the
# same values the standalone technical chart shows — while only the requested
# window is graded and displayed (the engine drops the leading bars as warm-up).
_WARMUP_CALENDAR_DAYS = 330
_INITIAL_CASH = 1_000_000
# Target-weight adjustment. ``hold`` buys on a 0→1 signal and sells on 1→0,
# letting the held weight drift with price — the right semantics for these
# single-symbol 择时 presets.
_POSITION_ADJUSTMENT = "hold"

# Trigger names a condition may use, plus their labels for the frontend.
TRIGGER_STATE = "state"
TRIGGER_EDGE_UP = "edge_up"
TRIGGER_EDGE_DOWN = "edge_down"
TRIGGER_OPTIONS: List[Dict[str, str]] = [
    {"id": TRIGGER_STATE, "label": "持续满足"},
    {"id": TRIGGER_EDGE_UP, "label": "刚满足（金叉/上穿）"},
    {"id": TRIGGER_EDGE_DOWN, "label": "刚脱离（死叉/下穿）"},
]


# ---------------------------------------------------------------------------
# Signal construction helpers
# ---------------------------------------------------------------------------


def _cross_up(series: pd.Series) -> pd.Series:
    """True on the bar where ``series`` flips False→True (or starts True)."""
    return (series & ~series.shift(1, fill_value=False)).astype(bool)


def _cross_down(series: pd.Series) -> pd.Series:
    """True on the bar where ``series`` flips True→False."""
    return (~series & series.shift(1, fill_value=False)).astype(bool)


def _combine_rule(mode: str, static_ok: Optional[bool], dynamic_ok: Optional[bool]) -> bool:
    """Combine static & entry-relative parts of one rule under its AND/OR mode.

    ``None`` means that part has no conditions → it is the operator's identity
    (True under AND, False under OR) so the surviving part decides alone. If
    *both* parts are absent there is no sell rule at all → False.
    """
    if static_ok is None and dynamic_ok is None:
        return False
    identity = mode == "and"
    static = identity if static_ok is None else static_ok
    dynamic = identity if dynamic_ok is None else dynamic_ok
    return (static and dynamic) if mode == "and" else (static or dynamic)


def _simulate_targets(
    buy: pd.Series,
    sell_static: Optional[pd.Series],
    frame: pd.DataFrame,
    take_profit_pct: Optional[float] = None,
    stop_loss_pct: Optional[float] = None,
    entry_volume_mults: Optional[List[float]] = None,
    entry_volume_mode: str = "and",
    allow_multiple_buys: bool = True,
) -> pd.Series:
    """Return a long/flat weight series from buy/sell triggers + optional exits.

    Flat → long on the first bar where ``buy`` is True (entry priced at that
    bar's open, close fallback). Long → flat when the (static) sell rule is
    True, an entry-volume sell condition fires (today's volume >= entry volume ×
    mult), the close passes ``take_profit_pct`` / ``stop_loss_pct``. A
    ``buy``+``sell`` on the same bar resolves as long. ``sell_static`` is the
    pre-evaluated stateless part of the sell rule; entry-volume conditions are
    resolved statefully inside (they need the entry bar's volume). Output is a
    float Series aligned with the input index.
    """
    buy_vals = [bool(v) for v in buy.tolist()]
    sell_vals = sell_static.tolist() if sell_static is not None else None
    closes = frame["close"].astype(float).tolist()
    opens = (
        frame["open"].astype(float).tolist()
        if "open" in frame.columns
        else list(closes)
    )
    volumes = (
        frame["volume"].astype(float).tolist()
        if "volume" in frame.columns
        else None
    )
    mults = list(entry_volume_mults or [])
    mode = entry_volume_mode if entry_volume_mode in {"and", "or"} else "and"
    out = pd.Series(0.0, index=buy.index, dtype="float64")
    state = 0
    entry: float = 0.0
    entry_volume: float = 0.0
    bought_any = False
    for i, b in enumerate(buy_vals):
        if state == 0:
            if b and (allow_multiple_buys or not bought_any):
                bought_any = True
                open_price = opens[i]
                close_price = closes[i]
                if math.isfinite(float(open_price)):
                    entry = float(open_price)
                elif math.isfinite(float(close_price)):
                    entry = float(close_price)
                else:
                    entry = 0.0
                if volumes is not None and math.isfinite(float(volumes[i])):
                    entry_volume = float(volumes[i])
                state = 1
        else:
            close_price = closes[i]
            static_ok = bool(sell_vals[i]) if sell_vals is not None else None
            dynamic_ok = None
            if mults and volumes is not None and entry_volume > 0 and math.isfinite(float(volumes[i])):
                reached = [volumes[i] >= entry_volume * m for m in mults]
                dynamic_ok = all(reached) if mode == "and" else any(reached)
            rule_exit = _combine_rule(mode, static_ok, dynamic_ok)

            exit_now = rule_exit
            if not exit_now and take_profit_pct and math.isfinite(float(close_price)):
                if close_price >= entry * (1.0 + take_profit_pct):
                    exit_now = True
            if not exit_now and stop_loss_pct and math.isfinite(float(close_price)):
                if close_price <= entry * (1.0 - stop_loss_pct):
                    exit_now = True
            if exit_now:
                state = 0
        out.iloc[i] = float(state)
    return out


class _SignalEngine:
    """Adapter turning a ``frame -> pd.Series`` function into a SignalEngine.

    Satisfies the ``backtest`` engine contract: ``generate(data_map)`` returns
    ``{code: pd.Series}`` with values in ``[-1, 1]``, index exactly aligned to
    the input frame, no NaN (warm-up bars are flat), never raises.
    """

    def __init__(self, fn: Callable[[pd.DataFrame], pd.Series]) -> None:
        self._fn = fn

    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        out: Dict[str, pd.Series] = {}
        for code, frame in data_map.items():
            try:
                weights = self._fn(frame)
            except Exception as exc:  # noqa: BLE001 - a bad strategy never kills the run
                logger.warning("strategy signal failed for %s: %s", code, exc)
                weights = pd.Series(0.0, index=frame.index)
            series = pd.Series(weights, index=frame.index)
            series = series.astype("float64").fillna(0.0).clip(-1.0, 1.0)
            out[code] = series
        return out


# ---------------------------------------------------------------------------
# Signal primitives (state conditions over a daily frame)
# ---------------------------------------------------------------------------


def _p_close_above_ma(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    close = frame["close"]
    return close > close.rolling(int(params["n"])).mean()


def _p_close_below_ma(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    close = frame["close"]
    return close < close.rolling(int(params["n"])).mean()


def _p_fast_ma_above_slow(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    close = frame["close"]
    fast = close.rolling(int(params["fast"])).mean()
    slow = close.rolling(int(params["slow"])).mean()
    return (fast > slow).fillna(False)


def _p_dif_above_dea(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    dif, dea, _ = compute_macd(
        frame["close"],
        fast=int(params["fast"]),
        slow=int(params["slow"]),
        signal=int(params["signal"]),
    )
    return (dif > dea).fillna(False)


def _p_k_above_d(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    k, d, _ = compute_kdj(frame, n=int(params["n"]), m1=int(params["m1"]), m2=int(params["m2"]))
    return (k > d).fillna(False)


def _p_kdj_j_above(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    _, _, j = compute_kdj(frame, n=int(params["n"]), m1=int(params["m1"]), m2=int(params["m2"]))
    return j > float(params["threshold"])


def _p_kdj_j_below(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    _, _, j = compute_kdj(frame, n=int(params["n"]), m1=int(params["m1"]), m2=int(params["m2"]))
    return j < float(params["threshold"])


def _p_rsi_above(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    rsi = compute_rsi(frame["close"], period=int(params["period"]))
    return rsi > float(params["threshold"])


def _p_rsi_below(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    rsi = compute_rsi(frame["close"], period=int(params["period"]))
    return rsi < float(params["threshold"])


def _p_close_above_boll_mid(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    mid, _, _, _, _ = compute_bollinger(frame["close"], n=int(params["n"]), k=float(params["k"]))
    return (frame["close"] > mid).fillna(False)


def _p_close_below_boll_lower(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    _, _, lower, _, _ = compute_bollinger(frame["close"], n=int(params["n"]), k=float(params["k"]))
    return (frame["close"] < lower).fillna(False)


def _p_close_above_boll_upper(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    _, upper, _, _, _ = compute_bollinger(frame["close"], n=int(params["n"]), k=float(params["k"]))
    return (frame["close"] > upper).fillna(False)


def _p_volume_expansion(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    """Volume spike vs the *prior* ``window`` days (excludes the current bar).

    ``baseline[i]`` is the mean volume of the ``window`` trading days strictly
    before ``i``, so a single big day never inflates its own baseline — the
    conventional meaning of 放量(量≥倍数·前N日均量).
    """
    volume = frame["volume"]
    prior = volume.shift(1)
    baseline = prior.rolling(int(params["window"]), min_periods=1).mean()
    return (volume >= float(params["mult"]) * baseline).fillna(False)


def _p_volume_vs_entry(frame: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
    """Stateless placeholder for the entry-volume sell condition.

    Real evaluation happens inside :func:`_simulate_targets`, which knows the
    entry bar's volume per position; here it contributes nothing (never True on
    its own).
    """
    return pd.Series(False, index=frame.index)


# Volume-sell condition compared against the ENTRY bar's volume (stateful —
# evaluated during simulation, not as a plain frame->bool series).
VOLUME_VS_ENTRY = "volume_vs_entry"


# Registry: primitive id -> descriptor. ``compute(frame, params)`` must return a
# boolean Series aligned with ``frame.index`` (NaNs treated as False upstream).
# KDJ shares the same K/D smoothing parameters across its primitives.
_KDJ_EXTRA_PARAMS: List[Dict[str, Any]] = [
    {"key": "m1", "label": "K 平滑(M1)", "default": 3, "min": 1, "max": 10},
    {"key": "m2", "label": "D 平滑(M2)", "default": 3, "min": 1, "max": 10},
]
PRIMITIVES: Dict[str, Dict[str, Any]] = {
    "close_above_ma": {
        "label": "收盘在 MA 上方",
        "description": "当日收盘价高于其 N 日均线",
        "params": [{"key": "n", "label": "均线周期", "default": 20, "min": 2, "max": 250}],
        "compute": _p_close_above_ma,
    },
    "close_below_ma": {
        "label": "收盘在 MA 下方",
        "description": "当日收盘价低于其 N 日均线",
        "params": [{"key": "n", "label": "均线周期", "default": 20, "min": 2, "max": 250}],
        "compute": _p_close_below_ma,
    },
    "fast_ma_above_slow": {
        "label": "快均线在慢均线上方",
        "description": "短期均线高于长期均线（多头排列状态）",
        "params": [
            {"key": "fast", "label": "短均线", "default": 5, "min": 2, "max": 60},
            {"key": "slow", "label": "长均线", "default": 20, "min": 5, "max": 250},
        ],
        "compute": _p_fast_ma_above_slow,
    },
    "dif_above_dea": {
        "label": "MACD DIF 在 DEA 上方",
        "description": "MACD 金叉后多头状态（DIF > DEA）",
        "params": [
            {"key": "fast", "label": "快线", "default": 12, "min": 2, "max": 60},
            {"key": "slow", "label": "慢线", "default": 26, "min": 5, "max": 120},
            {"key": "signal", "label": "信号线", "default": 9, "min": 2, "max": 60},
        ],
        "compute": _p_dif_above_dea,
    },
    "k_above_d": {
        "label": "KDJ K 在 D 上方",
        "description": "KDJ 金叉后多头状态（K > D）",
        "params": [
            {"key": "n", "label": "周期", "default": 9, "min": 2, "max": 60},
            *_KDJ_EXTRA_PARAMS,
        ],
        "compute": _p_k_above_d,
    },
    "kdj_j_above": {
        "label": "KDJ J 高于阈值",
        "description": "J 值高于设定阈值（强势/超买区，J=3K-2D，可超过 100）",
        "params": [
            {"key": "n", "label": "周期", "default": 9, "min": 2, "max": 60},
            *_KDJ_EXTRA_PARAMS,
            {"key": "threshold", "label": "J 阈值", "default": 100, "min": -100, "max": 200},
        ],
        "compute": _p_kdj_j_above,
    },
    "kdj_j_below": {
        "label": "KDJ J 低于阈值",
        "description": "J 值低于设定阈值（弱势/超卖区，J=3K-2D，可低于 0）",
        "params": [
            {"key": "n", "label": "周期", "default": 9, "min": 2, "max": 60},
            *_KDJ_EXTRA_PARAMS,
            {"key": "threshold", "label": "J 阈值", "default": 0, "min": -100, "max": 200},
        ],
        "compute": _p_kdj_j_below,
    },
    "rsi_above": {
        "label": "RSI 高于阈值",
        "description": "RSI 高于设定阈值（超买/强势）",
        "params": [
            {"key": "period", "label": "周期", "default": 14, "min": 2, "max": 60},
            {"key": "threshold", "label": "阈值", "default": 70, "min": 1, "max": 99},
        ],
        "compute": _p_rsi_above,
    },
    "rsi_below": {
        "label": "RSI 低于阈值",
        "description": "RSI 低于设定阈值（超卖/弱势）",
        "params": [
            {"key": "period", "label": "周期", "default": 14, "min": 2, "max": 60},
            {"key": "threshold", "label": "阈值", "default": 30, "min": 1, "max": 99},
        ],
        "compute": _p_rsi_below,
    },
    "close_above_boll_mid": {
        "label": "收盘在布林中轨上方",
        "description": "价格回到布林带中轨（均值）上方",
        "params": [
            {"key": "n", "label": "周期", "default": 20, "min": 5, "max": 120},
            {"key": "k", "label": "带宽倍数", "default": 2, "min": 1, "max": 4},
        ],
        "compute": _p_close_above_boll_mid,
    },
    "close_below_boll_lower": {
        "label": "收盘跌破布林下轨",
        "description": "价格跌破布林带下轨",
        "params": [
            {"key": "n", "label": "周期", "default": 20, "min": 5, "max": 120},
            {"key": "k", "label": "带宽倍数", "default": 2, "min": 1, "max": 4},
        ],
        "compute": _p_close_below_boll_lower,
    },
    "close_above_boll_upper": {
        "label": "收盘突破布林上轨",
        "description": "价格突破布林带上轨",
        "params": [
            {"key": "n", "label": "周期", "default": 20, "min": 5, "max": 120},
            {"key": "k", "label": "带宽倍数", "default": 2, "min": 1, "max": 4},
        ],
        "compute": _p_close_above_boll_upper,
    },
    "volume_expansion": {
        "label": "放量（量≥倍数×前N日均量）",
        "description": "当日成交量不低于其往前 window 个交易日（不含当日）的均量 × mult",
        "params": [
            {"key": "mult", "label": "倍数", "default": 1.5, "min": 1.0, "max": 5.0},
            {"key": "window", "label": "均量窗口(前N日)", "default": 20, "min": 5, "max": 60},
        ],
        "compute": _p_volume_expansion,
    },
    VOLUME_VS_ENTRY: {
        "label": "量能≥买入当日量×倍数（卖出）",
        "description": "持仓中当日成交量不低于开仓当日量的 mult 倍即卖出（需在卖出规则使用）",
        "params": [
            {"key": "mult", "label": "倍数(买入量)", "default": 3, "min": 1, "max": 100},
        ],
        "compute": _p_volume_vs_entry,
        "sell_only": True,
    },
}

# Trigger id -> transform over the primitive's state series.
_TRIGGER_FUNCS: Dict[str, Callable[[pd.Series], pd.Series]] = {
    TRIGGER_STATE: lambda s: s,
    TRIGGER_EDGE_UP: _cross_up,
    TRIGGER_EDGE_DOWN: _cross_down,
}


def _coerce_params_for(schema: List[Dict[str, Any]], raw: Any) -> Dict[str, Any]:
    """Clamp/coerce params against a param schema (defaults on missing)."""
    defaults = {p["key"]: p["default"] for p in schema}
    clean: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        raw = {}
    for p in schema:
        key = p["key"]
        try:
            value = float(raw.get(key, defaults[key]))
        except (TypeError, ValueError):
            value = float(defaults[key])
        is_int = isinstance(defaults[key], int)
        if is_int:
            value = int(round(value))
        value = max(float(p["min"]), min(float(p["max"]), value))
        clean[key] = int(value) if is_int else value
    return clean


# ---------------------------------------------------------------------------
# Rule / spec building
# ---------------------------------------------------------------------------

# Rule and condition schemas match what the frontend serializes:
#   spec = {"buy": Rule, "sell": Rule}
#   Rule = {"mode": "and"|"or", "conditions": [Condition, ...]}
#   Condition = {"primitive": <id>, "trigger": <id>, "params": {key: value}}
_RULE_MODES = {"and", "or"}


def _condition_series(cond: Dict[str, Any], frame: pd.DataFrame) -> pd.Series:
    """Evaluate one condition over a frame into a boolean series."""
    primitive_id = cond.get("primitive")
    descriptor = PRIMITIVES.get(primitive_id) if isinstance(primitive_id, str) else None
    if descriptor is None:
        return pd.Series(False, index=frame.index)
    params = _coerce_params_for(descriptor["params"], cond.get("params"))
    try:
        state = descriptor["compute"](frame, params)
    except Exception as exc:  # noqa: BLE001 - a broken condition never kills the run
        logger.warning("primitive %s failed: %s", primitive_id, exc)
        return pd.Series(False, index=frame.index)
    state = pd.Series(state, index=frame.index).fillna(False).astype(bool)
    trigger = cond.get("trigger") or TRIGGER_STATE
    transform = _TRIGGER_FUNCS.get(trigger)
    if transform is None:
        return pd.Series(False, index=frame.index)
    return transform(state)


def _rule_series(rule: Any, frame: pd.DataFrame) -> pd.Series:
    """Evaluate a rule (AND/OR of conditions) into a boolean series."""
    if not isinstance(rule, dict):
        return pd.Series(False, index=frame.index)
    conditions = rule.get("conditions") or []
    if not isinstance(conditions, list) or not conditions:
        return pd.Series(False, index=frame.index)
    mode = rule.get("mode", "and")
    result = _condition_series(conditions[0], frame)
    for other in conditions[1:]:
        other_series = _condition_series(other, frame)
        result = (result & other_series) if mode == "and" else (result | other_series)
    return result.fillna(False).astype(bool)


def _exit_pct(spec: Any, key: str) -> Optional[float]:
    """Return an optional TP/SL fraction (0 < x <= 1) from ``spec``, else None.

    Absent / zero / invalid values disable the exit band; values are clamped to
    a fraction (0.05 means 5%) so a caller sending ``5`` cannot mean 500%.
    """
    if not isinstance(spec, dict):
        return None
    raw = spec.get(key)
    if raw in (None, "", 0, False):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return min(1.0, value)


def _same_day_signal(weights: pd.Series) -> pd.Series:
    """Shift weights one bar EARLIER so the engine fills on the signal day.

    The shared backtest engine (``backtest.engines.base._align``) executes on
    the *next* bar's open after a weight change ("next-bar-open semantics").
    Pre-shifting our weight series by one bar makes the fill land on the same
    day the signal triggers (当天成交), without touching the engine's semantics
    used by Agent backtests.
    """
    shifted = weights.shift(-1, fill_value=weights.iloc[-1] if len(weights) else 0.0)
    return shifted.astype("float64")


def _spec_signal(frame: pd.DataFrame, spec: Dict[str, Any]) -> pd.Series:
    """Turn a spec's buy/sell rules (plus optional TP/SL) into weights.

    Sell conditions whose primitive is ``VOLUME_VS_ENTRY`` (成交量≥买入当日量×N)
    are resolved statefully inside :func:`_simulate_targets` because they need
    the entry bar's volume; every other sell condition is pre-evaluated as a
    static series.
    """
    buy = _rule_series(spec.get("buy"), frame)

    sell_mode = "and"
    sell_static: Optional[pd.Series] = None
    entry_volume_mults: List[float] = []
    sell_rule = spec.get("sell")
    if isinstance(sell_rule, dict):
        sell_mode = sell_rule.get("mode", "and")
        conditions = sell_rule.get("conditions") or []
        if not isinstance(conditions, list):
            conditions = []
        static_conds = [
            c for c in conditions if not (isinstance(c, dict) and c.get("primitive") == VOLUME_VS_ENTRY)
        ]
        dynamic_conds = [
            c for c in conditions if isinstance(c, dict) and c.get("primitive") == VOLUME_VS_ENTRY
        ]
        if static_conds:
            sell_static = _rule_series({"mode": sell_mode, "conditions": static_conds}, frame)
        schema = PRIMITIVES[VOLUME_VS_ENTRY]["params"]
        for condition in dynamic_conds:
            entry_volume_mults.append(
                float(_coerce_params_for(schema, condition.get("params")).get("mult", 3.0))
            )

    raw_allow = spec.get("allow_multiple_buys", True)
    allow_multiple = bool(raw_allow) if isinstance(raw_allow, bool) else str(raw_allow).lower() not in ("0", "false", "no", "")

    weights = _simulate_targets(
        buy,
        sell_static,
        frame,
        take_profit_pct=_exit_pct(spec, "take_profit_pct"),
        stop_loss_pct=_exit_pct(spec, "stop_loss_pct"),
        entry_volume_mults=entry_volume_mults,
        entry_volume_mode=sell_mode,
        allow_multiple_buys=allow_multiple,
    )
    return _same_day_signal(weights)


def build_signal_engine(spec: Dict[str, Any]) -> _SignalEngine:
    """Return a ``_SignalEngine`` whose weights come from ``spec`` rules."""
    return _SignalEngine(lambda frame: _spec_signal(frame, spec))


def _validate_spec(spec: Any) -> str:
    """Validate a spec dict; return an error message, or ``""`` when valid."""
    if not isinstance(spec, dict):
        return "spec 必须是一个对象"
    buy = spec.get("buy")
    if not isinstance(buy, dict) or not buy.get("conditions"):
        return "买入规则需至少一个条件"
    sell = spec.get("sell")
    if sell is not None and not isinstance(sell, dict):
        return "卖出规则格式错误"
    for rule_name in ("buy", "sell"):
        rule = spec.get(rule_name)
        if rule is None:
            continue
        if rule.get("mode", "and") not in _RULE_MODES:
            return f"{rule_name} 规则 mode 仅支持 and/or"
        for cond in rule.get("conditions") or []:
            if not isinstance(cond, dict):
                return f"{rule_name} 规则存在非法条件"
            primitive_id = cond.get("primitive")
            if primitive_id not in PRIMITIVES:
                return f"未知原语: {primitive_id}"
            trigger = cond.get("trigger", TRIGGER_STATE)
            if trigger not in _TRIGGER_FUNCS:
                return f"未知触发方式: {trigger}"
    return ""


# ---------------------------------------------------------------------------
# Preset templates (one-click fills; fully editable afterwards)
# ---------------------------------------------------------------------------

def _cond(primitive: str, trigger: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"primitive": primitive, "trigger": trigger, "params": params}


_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "ma_cross",
        "label": "双均线金叉",
        "spec": {
            "buy": {
                "mode": "and",
                "conditions": [_cond("fast_ma_above_slow", TRIGGER_EDGE_UP, {"fast": 5, "slow": 20})],
            },
            "sell": {
                "mode": "and",
                "conditions": [_cond("fast_ma_above_slow", TRIGGER_EDGE_DOWN, {"fast": 5, "slow": 20})],
            },
        },
    },
    {
        "id": "macd_cross",
        "label": "MACD 金叉",
        "spec": {
            "buy": {
                "mode": "and",
                "conditions": [_cond("dif_above_dea", TRIGGER_EDGE_UP, {"fast": 12, "slow": 26, "signal": 9})],
            },
            "sell": {
                "mode": "and",
                "conditions": [_cond("dif_above_dea", TRIGGER_EDGE_DOWN, {"fast": 12, "slow": 26, "signal": 9})],
            },
        },
    },
    {
        "id": "kdj_cross",
        "label": "KDJ 金叉",
        "spec": {
            "buy": {"mode": "and", "conditions": [_cond("k_above_d", TRIGGER_EDGE_UP, {"n": 9, "m1": 3, "m2": 3})]},
            "sell": {"mode": "and", "conditions": [_cond("k_above_d", TRIGGER_EDGE_DOWN, {"n": 9, "m1": 3, "m2": 3})]},
        },
    },
    {
        "id": "rsi_reversal",
        "label": "RSI 超买超卖",
        "spec": {
            "buy": {
                "mode": "and",
                "conditions": [_cond("rsi_above", TRIGGER_EDGE_UP, {"period": 14, "threshold": 30})],
            },
            "sell": {
                "mode": "and",
                "conditions": [_cond("rsi_above", TRIGGER_EDGE_UP, {"period": 14, "threshold": 70})],
            },
        },
    },
    {
        "id": "bollinger_mean_revert",
        "label": "布林均值回归",
        "spec": {
            "buy": {
                "mode": "and",
                "conditions": [_cond("close_below_boll_lower", TRIGGER_EDGE_UP, {"n": 20, "k": 2})],
            },
            "sell": {
                "mode": "and",
                "conditions": [_cond("close_above_boll_mid", TRIGGER_STATE, {"n": 20, "k": 2})],
            },
        },
    },
]


def list_primitives() -> List[Dict[str, Any]]:
    """Return the primitive registry without callables (JSON-safe)."""
    metas = []
    for primitive_id, entry in PRIMITIVES.items():
        metas.append(
            {
                "id": primitive_id,
                "label": entry["label"],
                "description": entry["description"],
                "params": entry["params"],
                "triggers": TRIGGER_OPTIONS,
                "sell_only": bool(entry.get("sell_only", False)),
            }
        )
    return metas


def list_presets() -> List[Dict[str, Any]]:
    """Return the one-click preset templates (id/label/spec)."""
    return [
        {"id": preset["id"], "label": preset["label"], "spec": preset["spec"]}
        for preset in _PRESETS
    ]


# ---------------------------------------------------------------------------
# Backtest execution
# ---------------------------------------------------------------------------


def _finite(value: Any, digits: int = 6) -> Optional[float]:
    """Round a scalar to ``digits``; None for missing/non-finite values."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _read_curve(equity_csv: Path) -> List[BacktestPoint]:
    """Read strategy/benchmark equity points from the engine's equity.csv."""
    try:
        frame = pd.read_csv(equity_csv, index_col=0, parse_dates=True)
    except Exception:  # noqa: BLE001 - a missing curve degrades to empty
        return []
    points: List[BacktestPoint] = []
    for idx, row in frame.iterrows():
        value = row.get("equity")
        if value is None or not math.isfinite(float(value)):
            continue
        points.append(
            BacktestPoint(date=pd.Timestamp(idx).strftime("%Y-%m-%d"), equity=round(float(value), 2))
        )
    return points


def _read_benchmark_curve(equity_csv: Path) -> List[BacktestPoint]:
    """Read the buy-and-hold benchmark equity points, if the column exists."""
    try:
        frame = pd.read_csv(equity_csv, index_col=0, parse_dates=True)
    except Exception:  # noqa: BLE001
        return []
    if "benchmark_equity" not in frame.columns:
        return []
    points: List[BacktestPoint] = []
    for idx, row in frame.iterrows():
        value = row.get("benchmark_equity")
        if value is None or not math.isfinite(float(value)):
            continue
        points.append(
            BacktestPoint(date=pd.Timestamp(idx).strftime("%Y-%m-%d"), equity=round(float(value), 2))
        )
    return points


def _read_trades(trades_csv: Path) -> List[BacktestTradePoint]:
    """Read executed buy/sell fills from the engine's trades.csv."""
    try:
        frame = pd.read_csv(trades_csv)
    except Exception:  # noqa: BLE001 - a missing trades file degrades to empty
        return []
    points: List[BacktestTradePoint] = []
    for _, row in frame.iterrows():
        side = str(row.get("side") or "")
        raw_ts = row.get("timestamp")
        raw_price = row.get("price")
        if not side or side not in {"buy", "sell"} or raw_ts is None or raw_price is None:
            continue
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price):
            continue
        points.append(
            BacktestTradePoint(
                date=str(raw_ts)[:10],
                side=side,
                price=round(price, 3),
            )
        )
    return points


def _read_price_points(frame: pd.DataFrame) -> List[BacktestPricePoint]:
    """Build per-day close points for the price + buy/sell overlay."""
    if frame is None or "close" not in frame.columns:
        return []
    points: List[BacktestPricePoint] = []
    for ts, value in frame["close"].items():
        if value is None or not math.isfinite(float(value)):
            continue
        points.append(
            BacktestPricePoint(
                date=pd.Timestamp(ts).strftime("%Y-%m-%d"),
                close=round(float(value), 3),
            )
        )
    return points


def _numlist(values: pd.Series) -> List[Optional[float]]:
    """Turn a float Series into a None-aware rounded list (NaN → None)."""
    out: List[Optional[float]] = []
    for raw in values.tolist():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append(None if not math.isfinite(value) else round(value, 4))
    return out


def _indicator_panels(spec: Any, frame: Optional[pd.DataFrame]) -> List[BacktestIndicatorPanel]:
    """Derive the indicators the spec's conditions actually use, for charting.

    Returns groups ready for the frontend:
    - ``"ma"`` / ``"boll"`` panels are overlaid on the price grid;
    - ``"macd"`` / ``"kdj"`` / ``"rsi"`` each get a sub-panel below the price.
    Only the primitives that appear in the buy/sell rules are computed (with
    their rule parameters), so the chart always matches the strategy.
    """
    if frame is None or frame.empty or "close" not in frame.columns:
        return []
    if not isinstance(spec, dict):
        return []
    close = frame["close"]

    conditions: List[Dict[str, Any]] = []
    for rule_key in ("buy", "sell"):
        rule = spec.get(rule_key)
        if isinstance(rule, dict):
            conditions.extend(rule.get("conditions") or [])

    # Ordered accumulator: (kind, params-fingerprint) -> panel.
    buckets: Dict[tuple[str, str], Dict[str, Any]] = {}

    def _put(
        kind: str,
        params: Dict[str, Any],
        title: str,
        series_key: str,
        series_label: str,
        values: pd.Series,
    ) -> None:
        fp = tuple(sorted((str(k), str(v)) for k, v in params.items()))
        key = (kind, fp)
        panel = buckets.get(key)
        if panel is None:
            panel = {"kind": kind, "params": params, "title": title, "series": {}}
            buckets[key] = panel
        series_map = panel["series"]
        if series_key not in series_map:
            series_map[series_key] = BacktestIndicatorSeries(
                key=series_key,
                label=series_label,
                values=_numlist(values),
            )

    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        primitive_id = cond.get("primitive")
        descriptor = PRIMITIVES.get(primitive_id) if isinstance(primitive_id, str) else None
        if descriptor is None:
            continue
        params = _coerce_params_for(descriptor["params"], cond.get("params"))

        if primitive_id in ("close_above_ma", "close_below_ma"):
            n = int(params["n"])
            _put("ma", {"n": n}, "均线", f"ma:{n}", f"MA{n}", close.rolling(n).mean())
        elif primitive_id == "fast_ma_above_slow":
            for key in ("fast", "slow"):
                n = int(params[key])
                _put("ma", {"n": n}, "均线", f"ma:{n}", f"MA{n}", close.rolling(n).mean())
        elif primitive_id in ("close_above_boll_mid", "close_below_boll_lower", "close_above_boll_upper"):
            n, k = int(params["n"]), float(params["k"])
            mid, upper, lower, _, _ = compute_bollinger(close, n=n, k=k)
            title = f"布林 (n={n}, k={k})"
            _put("boll", {"n": n, "k": k}, title, "mid", "中轨", mid)
            _put("boll", {"n": n, "k": k}, title, "upper", "上轨", upper)
            _put("boll", {"n": n, "k": k}, title, "lower", "下轨", lower)
        elif primitive_id == "dif_above_dea":
            fast, slow, signal = int(params["fast"]), int(params["slow"]), int(params["signal"])
            dif, dea, hist = compute_macd(close, fast=fast, slow=slow, signal=signal)
            title = f"MACD ({fast},{slow},{signal})"
            _put("macd", {"fast": fast, "slow": slow, "signal": signal}, title, "dif", "DIF", dif)
            _put("macd", {"fast": fast, "slow": slow, "signal": signal}, title, "dea", "DEA", dea)
            _put("macd", {"fast": fast, "slow": slow, "signal": signal}, title, "hist", "MACD", hist)
        elif primitive_id in ("k_above_d", "kdj_j_above", "kdj_j_below"):
            n, m1, m2 = int(params["n"]), int(params["m1"]), int(params["m2"])
            k, d, j = compute_kdj(frame, n=n, m1=m1, m2=m2)
            title = f"KDJ (n={n},m1={m1},m2={m2})"
            kdj_params = {"n": n, "m1": m1, "m2": m2}
            _put("kdj", kdj_params, title, "k", "K", k)
            _put("kdj", kdj_params, title, "d", "D", d)
            _put("kdj", kdj_params, title, "j", "J", j)
        elif primitive_id in ("rsi_above", "rsi_below"):
            period = int(params["period"])
            _put("rsi", {"period": period}, f"RSI ({period})", "rsi", "RSI", compute_rsi(close, period=period))

    panels: List[BacktestIndicatorPanel] = []
    for panel in buckets.values():
        series = list(panel["series"].values())  # already BacktestIndicatorSeries
        panels.append(
            BacktestIndicatorPanel(
                kind=panel["kind"],
                title=panel["title"],
                params=panel["params"],
                series=series,
            )
        )
    # Price overlays first (ma/boll), then oscillators in first-seen order.
    panels.sort(key=lambda p: 0 if p.kind in {"ma", "boll"} else 1)
    return panels


def _default_date_range() -> tuple[str, str]:
    """Return ``(start_date, end_date)`` ≈ the last 60 A股 trading sessions."""
    end = date.today()
    start = end - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
    return start.isoformat(), end.isoformat()


def run_backtest_for_symbol(
    code: str,
    spec: Any,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    label: str = "",
) -> BacktestSnapshot:
    """Run the real backtest engine for one composable rule spec. Never raises.

    Args:
        code: A-share symbol (e.g. ``"600519.SH"``).
        spec: ``{"buy": Rule, "sell": Rule}`` — see :func:`build_signal_engine`.
        start_date / end_date: ``YYYY-MM-DD`` evaluation window. Defaults to the
            last ≈60 A股 trading sessions when omitted.
        label: Human label (preset name or "自定义"), for display only.

    Returns:
        BacktestSnapshot; invalid spec / fetch / engine failures degrade to
        ``error`` instead of raising.
    """
    error = _validate_spec(spec)
    clean_spec: Dict[str, Any] = {}
    if not error and isinstance(spec, dict):
        clean_spec = {"buy": dict(spec.get("buy") or {}), "sell": dict(spec.get("sell") or {})}
        for key in ("take_profit_pct", "stop_loss_pct"):
            pct = _exit_pct(spec, key)
            if pct is not None:
                clean_spec[key] = round(pct, 4)
        if "allow_multiple_buys" in spec:
            raw_allow = spec.get("allow_multiple_buys", True)
            clean_spec["allow_multiple_buys"] = (
                bool(raw_allow)
                if isinstance(raw_allow, bool)
                else str(raw_allow).lower() not in ("0", "false", "no", "")
            )
    snapshot = BacktestSnapshot(
        code=code,
        label=label,
        spec=clean_spec,
    )
    if error:
        snapshot.error = error
        return snapshot

    start, end = _default_date_range()
    if start_date:
        start = start_date
    if end_date:
        end = end_date
    snapshot.start_date = start
    snapshot.end_date = end

    # Fetch extra leading history before ``start`` purely as indicator warm-up.
    data_start = (pd.Timestamp(start) - pd.Timedelta(days=_WARMUP_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    config = {
        "source": "auto",
        "codes": [code],
        "start_date": data_start,
        "end_date": end,
        "interval": "1D",
        "initial_cash": _INITIAL_CASH,
        "position_adjustment": _POSITION_ADJUSTMENT,
        # Grade & display only the requested window; earlier bars warm the
        # indicators but are never traded or reported.
        "evaluation_start_date": start,
    }

    signal_engine = build_signal_engine(clean_spec)
    try:
        from backtest.runner import (
            _AutoLoader,
            _create_market_engine,
            _detect_primary_source,
            fetch_data_map,
        )
        from backtest.metrics import calc_bars_per_year

        fetch_result = fetch_data_map(config)
        data_map = fetch_result.data_map
        codes = fetch_result.codes
        if not data_map:
            snapshot.error = "no data fetched"
            return snapshot
        if code not in data_map:
            snapshot.error = f"no data for {code}"
            return snapshot

        # Per-day closes power the price + buy/sell overlay. Indicators are
        # computed over the *warm* full series (leading history included) so the
        # values match the standalone technical chart, then trimmed to the
        # requested evaluation window to line up with the equity curve.
        frame = data_map.get(code)
        row_start = int(frame.index.searchsorted(pd.Timestamp(snapshot.start_date)))

        snapshot.prices = _read_price_points(frame)[row_start:]

        panels = _indicator_panels(clean_spec, frame)
        trimmed: List[BacktestIndicatorPanel] = []
        for panel in panels:
            trimmed.append(
                panel.model_copy(
                    update={
                        "series": [
                            series.model_copy(update={"values": series.values[row_start:]})
                            for series in panel.series
                        ]
                    }
                )
            )
        snapshot.indicators = trimmed

        run_config = dict(config)
        run_config["codes"] = codes
        run_config["_run_card_effective_sources"] = fetch_result.effective_sources
        effective_source = _detect_primary_source(codes, str(config.get("source", "auto")))
        engine = _create_market_engine(effective_source, run_config, codes)
        loader = _AutoLoader(data_map)
        bars_per_year = calc_bars_per_year("1D", effective_source)
    except Exception as exc:  # noqa: BLE001 - fetch/routing failure degrades
        logger.warning("backtest setup failed for %s: %s", code, exc)
        snapshot.error = f"{type(exc).__name__}: {exc}"
        return snapshot

    with tempfile.TemporaryDirectory(prefix="vibe-backtest-") as tmp:
        run_dir = Path(tmp)
        try:
            metrics = engine.run_backtest(
                run_config,
                loader,
                signal_engine,
                run_dir,
                bars_per_year=bars_per_year,
            )
        except SystemExit as exc:
            snapshot.error = f"backtest engine exited: {exc}"
            return snapshot
        except Exception as exc:  # noqa: BLE001
            logger.exception("backtest engine failed for %s", code)
            snapshot.error = f"{type(exc).__name__}: {exc}"
            return snapshot

        equity_csv = run_dir / "artifacts" / "equity.csv"
        snapshot.equity_curve = _read_curve(equity_csv)
        snapshot.benchmark_curve = _read_benchmark_curve(equity_csv)
        # B/S marks come from the engine's actual fills (S same-day, B only when
        # the buy signal fires again later) so marks & metrics always agree.
        snapshot.trades = _read_trades(run_dir / "artifacts" / "trades.csv")
        snapshot.bars = max(len(snapshot.equity_curve), 0)

        snapshot.total_return = _finite(metrics.get("total_return"))
        snapshot.annual_return = _finite(metrics.get("annual_return"))
        snapshot.max_drawdown = _finite(metrics.get("max_drawdown"))
        snapshot.sharpe = _finite(metrics.get("sharpe"))
        snapshot.sortino = _finite(metrics.get("sortino"))
        snapshot.win_rate = _finite(metrics.get("win_rate"))
        snapshot.profit_factor = _finite(metrics.get("profit_factor"))
        snapshot.trade_count = int(metrics.get("trade_count") or 0)

    return snapshot


__all__ = [
    "build_signal_engine",
    "list_primitives",
    "list_presets",
    "run_backtest_for_symbol",
]
