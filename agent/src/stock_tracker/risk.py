"""Pure risk-metric helpers for the stock tracker (ATR, drawdown, beta).

Each function is deterministic and network-free so it can be unit tested with
constructed DataFrames. They mirror the style of ``compute_rsi`` /
``compute_mas`` in ``signals.py`` but live in their own module because they
feed symbol-level ``RiskMetrics`` rather than per-period signals.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def compute_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Return the latest 14-day Average True Range using Wilder smoothing.

    True range is the max of (high - low, |high - prev_close|, |low -
    prev_close|). The last True Range depends on the prior close, so at least
    ``period + 1`` bars are required for a well-defined value.

    Args:
        df: OHLCV frame with ``high``, ``low`` and ``close`` columns.
        period: ATR smoothing period in trading days.

    Returns:
        Latest ATR in price units, or ``None`` when the frame is too short or
        the relevant columns are missing.
    """
    required = {"high", "low", "close"}
    if not required.issubset(df.columns):
        return None
    if len(df) < period + 1:
        return None

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing is an EMA with alpha = 1 / period, matching the RSI
    # implementation in signals.py.
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    latest = atr.iloc[-1]
    return float(latest) if pd.notna(latest) else None


def compute_max_drawdown(df: pd.DataFrame, window: int = 60) -> Optional[float]:
    """Return the worst peak-to-trough decline over the trailing ``window`` bars.

    Args:
        df: OHLCV frame with a ``close`` column.
        window: Lookback window in trading days.

    Returns:
        Negative fraction (e.g. ``-0.1823``) or ``None`` when fewer than two
        closes are available.
    """
    if "close" not in df.columns:
        return None
    closes = df["close"].tail(window)
    if len(closes) < 2:
        return None
    drawdown = closes / closes.cummax() - 1.0
    return round(float(drawdown.min()), 6)


def compute_beta(
    stock_df: pd.DataFrame,
    benchmark_df: Optional[pd.DataFrame],
    window: int = 60,
    min_overlap: int = 30,
) -> Optional[float]:
    """Return the OLS slope of stock daily returns on benchmark returns.

    Both return series are aligned on their shared dates, so trading calendars
    do not need to match exactly. Beta is only returned when at least
    ``min_overlap`` overlapping observations remain after alignment.

    Args:
        stock_df: OHLCV frame with a ``close`` column.
        benchmark_df: Benchmark (CSI 300 index or ETF) frame with a ``close``
            column; ``None`` means no benchmark is available.
        window: Maximum trailing bars to include for each series.
        min_overlap: Minimum overlapping observations required.

    Returns:
        Beta (float) or ``None`` when the benchmark is missing, the overlap is
        too small, or the benchmark return has zero variance.
    """
    if benchmark_df is None or benchmark_df.empty or "close" not in benchmark_df.columns:
        return None
    if "close" not in stock_df.columns:
        return None

    stock_ret = stock_df["close"].pct_change().dropna().tail(window)
    bench_ret = benchmark_df["close"].pct_change().dropna().tail(window)
    if stock_ret.empty or bench_ret.empty:
        return None

    aligned = pd.concat(
        [stock_ret.rename("stock"), bench_ret.rename("bench")],
        axis=1,
        join="inner",
    ).dropna()
    if len(aligned) < min_overlap:
        return None

    bench_var = float(aligned["bench"].var())
    if not bench_var or bench_var <= 0:
        return None

    beta = float(aligned["stock"].cov(aligned["bench"]) / bench_var)
    return round(beta, 4)


__all__ = ["compute_atr", "compute_max_drawdown", "compute_beta"]
