"""Pure indicator math shared by signal detectors and chart serialization.

Each function here is a deterministic transform over a price frame and returns
pandas ``Series`` aligned with the input index. Detectors import these to decide
a trigger on the latest bar; ``StockTrackerEngine._analyze_symbol`` imports the
same functions to serialize the trailing bars for the frontend chart, so the
badge value and the plotted value can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Return ``(dif, dea, histogram)`` for the standard Chinese MACD.

    ``DIF = EMA(close, fast) - EMA(close, slow)``, ``DEA = EMA(DIF, signal)``,
    and ``histogram = 2 * (DIF - DEA)``. Warm-up bars are ``NaN``.
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    histogram = 2.0 * (dif - dea)
    return dif, dea, histogram


def compute_kdj(df: pd.DataFrame, n: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Return ``(k, d, j)`` for KDJ with the conventional 1/3 smoothing.

    ``RSV = (close - LLV_n) / (HHV_n - LLV_n) * 100``; flat windows (HHV == LLV)
    map to ``RSV = 50`` to avoid division by zero. ``K``/``D`` are seeded at 50
    and smoothed recursively.
    """
    close = df["close"]
    hhv = df["high"].rolling(n, min_periods=1).max()
    llv = df["low"].rolling(n, min_periods=1).min()
    denom = (hhv - llv).replace(0.0, np.nan)
    rsv = (close - llv) / denom * 100.0
    rsv = rsv.fillna(50.0)

    k = pd.Series(np.nan, index=df.index, dtype="float64")
    d = pd.Series(np.nan, index=df.index, dtype="float64")
    k_val = 50.0
    d_val = 50.0
    for i, r in enumerate(rsv.to_numpy()):
        k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * r
        d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
        k.iloc[i] = k_val
        d.iloc[i] = d_val
    j = 3.0 * k - 2.0 * d
    return k, d, j


def compute_bollinger(
    close: pd.Series, n: int = 20, k: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Return ``(mid, upper, lower, pct_b, bandwidth)`` for Bollinger bands.

    ``%B = (close - lower) / (upper - lower)`` and
    ``bandwidth = (upper - lower) / mid``. ``%B``/``bandwidth`` are ``NaN`` on
    the warm-up bars and whenever the band is degenerate.
    """
    mid = close.rolling(n, min_periods=1).mean()
    std = close.rolling(n, min_periods=1).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    band_width = upper - lower
    pct_b = (close - lower) / band_width.replace(0.0, np.nan)
    bandwidth = band_width / mid.replace(0.0, np.nan)
    return mid, upper, lower, pct_b, bandwidth


def find_swing_points(series: pd.Series, pivot: int) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Return ``(highs, lows)`` as ``(iloc_index, value)`` for confirmed swings.

    A bar is a swing high when it is the max of its ``pivot``-bar neighborhood
    and strictly higher than the previous bar; swing lows are symmetric. Pairs
    are ordered left-to-right by index.
    """
    values = series.to_numpy(dtype="float64")
    n = len(values)
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    for i in range(pivot, n - pivot):
        window = values[i - pivot : i + pivot + 1]
        if values[i] == window.max() and values[i] > values[i - 1] and values[i] >= values[i + 1]:
            highs.append((i, float(values[i])))
        if values[i] == window.min() and values[i] < values[i - 1] and values[i] <= values[i + 1]:
            lows.append((i, float(values[i])))
    return highs, lows


@dataclass
class DivergenceResult:
    """Outcome of a MACD-DIF divergence scan over one frame.

    Indices are ``iloc`` positions relative to the frame passed in, so callers
    that pass the trailing serialized frame can use them directly as chart
    marks. ``strength`` is signed: negative for top divergence, positive for
    bottom divergence.
    """

    triggered: bool
    kind: Optional[str]  # "top" | "bottom" | None
    strength: float
    price_hi_idx: Optional[int]
    price_lo_idx: Optional[int]
    dif_hi_idx: Optional[int]
    dif_lo_idx: Optional[int]
    description: str


def _divergence_strength(p_recent: float, p_prev: float, d_recent: float, d_prev: float) -> float:
    """Relative price move minus relative DIF move, both positive on divergence."""
    price_delta = (p_recent - p_prev) / p_prev if p_prev else 0.0
    dif_base = abs(d_prev) if d_prev else 1e-9
    dif_delta = (d_recent - d_prev) / dif_base
    return price_delta - dif_delta


def find_divergence(
    df: pd.DataFrame,
    dif: pd.Series,
    lookback: int,
    pivot: int,
    tolerance: float,
) -> DivergenceResult:
    """Detect MACD-DIF top/bottom divergence over the trailing ``lookback`` bars.

    Top divergence: the most recent swing high is higher than the prior one (by
    ``tolerance``) but its DIF is lower. Bottom divergence is the mirror on swing
    lows. Returns a non-triggered result when fewer than two swings exist.
    """
    insufficient = DivergenceResult(
        triggered=False, kind=None, strength=0.0,
        price_hi_idx=None, price_lo_idx=None, dif_hi_idx=None, dif_lo_idx=None,
        description="insufficient swing points",
    )
    window = df.iloc[-lookback:]
    dif_window = dif.iloc[-lookback:]
    if len(window) < 2 * pivot + 2:
        return insufficient

    highs, _ = find_swing_points(window["high"], pivot)
    _, lows = find_swing_points(window["low"], pivot)

    # Top divergence — compare the two most recent swing highs.
    if len(highs) >= 2:
        (i_recent, p_recent) = highs[-1]
        (i_prev, p_prev) = highs[-2]
        if p_prev and p_recent > p_prev * (1.0 + tolerance):
            d_recent = float(dif_window.iloc[i_recent])
            d_prev = float(dif_window.iloc[i_prev])
            if pd.notna(d_recent) and pd.notna(d_prev) and d_recent < d_prev:
                strength = -_divergence_strength(p_recent, p_prev, d_recent, d_prev)
                return DivergenceResult(
                    triggered=True, kind="top", strength=round(strength, 4),
                    price_hi_idx=i_recent, price_lo_idx=i_prev, dif_hi_idx=i_recent, dif_lo_idx=i_prev,
                    description="Top divergence: higher price high, lower DIF high",
                )

    # Bottom divergence — compare the two most recent swing lows.
    if len(lows) >= 2:
        (i_recent, p_recent) = lows[-1]
        (i_prev, p_prev) = lows[-2]
        if p_prev and p_recent < p_prev * (1.0 - tolerance):
            d_recent = float(dif_window.iloc[i_recent])
            d_prev = float(dif_window.iloc[i_prev])
            if pd.notna(d_recent) and pd.notna(d_prev) and d_recent > d_prev:
                strength = _divergence_strength(p_prev, p_recent, d_prev, d_recent)
                return DivergenceResult(
                    triggered=True, kind="bottom", strength=round(strength, 4),
                    price_hi_idx=i_recent, price_lo_idx=i_prev, dif_hi_idx=i_recent, dif_lo_idx=i_prev,
                    description="Bottom divergence: lower price low, higher DIF low",
                )

    return DivergenceResult(
        triggered=False, kind=None, strength=0.0,
        price_hi_idx=None, price_lo_idx=None, dif_hi_idx=None, dif_lo_idx=None,
        description="no divergence",
    )


__all__ = [
    "compute_bollinger",
    "compute_kdj",
    "compute_macd",
    "find_divergence",
    "find_swing_points",
    "DivergenceResult",
]
