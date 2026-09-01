"""Unit tests for the stock tracker risk-metric helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.stock_tracker.risk import compute_atr, compute_beta, compute_max_drawdown


def _ohlcv(
    closes: list[float],
    *,
    spread: float = 1.0,
) -> pd.DataFrame:
    """Build an OHLCV frame from close prices with a constant high-low spread."""
    dates = pd.date_range(end="2026-08-31", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "open": [c - spread / 2 for c in closes],
            "high": [c + spread / 2 for c in closes],
            "low": [c - spread / 2 for c in closes],
            "close": closes,
            "volume": [10000] * len(closes),
        },
        index=dates,
    )


def _returns_frame(
    start_price: float,
    daily_returns: list[float],
) -> pd.DataFrame:
    """Build a close-price frame whose daily returns follow ``daily_returns``."""
    closes = [start_price]
    for r in daily_returns:
        closes.append(closes[-1] * (1 + r))
    return _ohlcv(closes)


def test_compute_atr_constant_range():
    closes = [100.0 + i for i in range(30)]
    df = _ohlcv(closes, spread=2.0)
    # True range is dominated by high-low for every bar (midpoint closes), so
    # the Wilder ATR converges to the constant spread of 2.0.
    atr = compute_atr(df, period=14)
    assert atr == pytest.approx(2.0, abs=1e-6)


def test_compute_atr_insufficient_data():
    df = _ohlcv([100.0 + i for i in range(10)])
    assert compute_atr(df, period=14) is None


def test_compute_atr_missing_columns():
    df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
    assert compute_atr(df, period=14) is None


def test_compute_max_drawdown_known():
    # Peak at 120, trough at 80 -> (80 / 120 - 1) = -1/3.
    df = _ohlcv([100.0, 120.0, 110.0, 80.0, 90.0])
    dd = compute_max_drawdown(df, window=60)
    assert dd == pytest.approx(-1 / 3, abs=1e-6)


def test_compute_max_drawdown_insufficient_data():
    df = _ohlcv([100.0])
    assert compute_max_drawdown(df, window=60) is None


def test_compute_beta_known_slope():
    # Deterministic benchmark returns; stock is exactly 1.5x leveraged.
    bench_returns = [0.5 * np.sin(i / 4.0) / 100 for i in range(80)]
    bench = _returns_frame(100.0, bench_returns)
    stock = _returns_frame(100.0, [1.5 * r for r in bench_returns])

    beta = compute_beta(stock, bench, window=60)
    assert beta == pytest.approx(1.5, abs=1e-4)


def test_compute_beta_none_without_benchmark():
    df = _ohlcv([100.0 + i for i in range(60)])
    assert compute_beta(df, None, window=60) is None


def test_compute_beta_none_on_insufficient_overlap():
    stock_dates = pd.date_range(end="2026-08-31", periods=60, freq="B")
    bench_dates = pd.date_range(end="2026-07-15", periods=10, freq="B")
    stock = pd.DataFrame({"close": np.linspace(100, 160, 60)}, index=stock_dates)
    bench = pd.DataFrame({"close": np.linspace(100, 160, 10)}, index=bench_dates)
    assert compute_beta(stock, bench, window=60) is None


def test_compute_beta_none_on_zero_benchmark_variance():
    bench = _ohlcv([100.0] * 60)  # flat -> zero variance
    stock = _ohlcv([100.0 + i for i in range(60)])
    assert compute_beta(stock, bench, window=60) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
