"""Unit tests for the pure technical-indicator math shared by detectors and charts."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.stock_tracker.indicators import (
    compute_bollinger,
    compute_kdj,
    compute_macd,
    find_divergence,
    find_swing_points,
)


def test_compute_macd_histogram_is_double_diff() -> None:
    close = pd.Series(np.linspace(20, 10, 60))
    dif, dea, hist = compute_macd(close)

    assert len(dif) == len(close)
    assert len(dea) == len(close)
    assert len(hist) == len(close)
    # histogram is 2 * (DIF - DEA).
    expected = 2.0 * (dif - dea)
    assert np.allclose(hist.to_numpy(), expected.to_numpy(), equal_nan=True)


def test_compute_macd_matches_manual_emas() -> None:
    close = pd.Series(np.linspace(20, 10, 60))
    dif, dea, _ = compute_macd(close, fast=12, slow=26, signal=9)

    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    expected_dif = ema_fast - ema_slow
    expected_dea = expected_dif.ewm(span=9, adjust=False).mean()

    assert np.allclose(dif.to_numpy(), expected_dif.to_numpy())
    assert np.allclose(dea.to_numpy(), expected_dea.to_numpy())


def test_compute_macd_respects_custom_periods() -> None:
    close = pd.Series(np.linspace(20, 10, 60))
    default_dif, _, _ = compute_macd(close)
    custom_dif, _, _ = compute_macd(close, fast=5, slow=13, signal=4)
    assert not np.allclose(default_dif.to_numpy(), custom_dif.to_numpy())


def test_compute_kdj_seeds_at_50_on_flat_series() -> None:
    flat = pd.Series([10.0] * 10)
    df = pd.DataFrame({"close": flat, "high": flat, "low": flat})
    k, d, j = compute_kdj(df)
    # Flat windows map RSV to 50, so K/D/J all stay at 50.
    assert np.allclose(k.to_numpy(), 50.0)
    assert np.allclose(d.to_numpy(), 50.0)
    assert np.allclose(j.to_numpy(), 50.0)


def test_compute_kdj_j_is_three_k_minus_two_d() -> None:
    close = pd.Series(np.linspace(20, 10, 40) + np.linspace(10, 22, 40))
    df = pd.DataFrame({"close": close, "high": close + 1.0, "low": close - 1.0})
    k, d, j = compute_kdj(df)

    assert np.allclose(j.to_numpy(), 3.0 * k.to_numpy() - 2.0 * d.to_numpy())


def test_compute_kdj_values_within_bounds() -> None:
    close = pd.Series(np.linspace(20, 10, 40) + np.linspace(10, 22, 40))
    df = pd.DataFrame({"close": close, "high": close + 1.0, "low": close - 1.0})
    k, d, j = compute_kdj(df)

    # K and D are weighted averages of an [0,100] RSV and are bounded there.
    assert k.between(0.0, 100.0).all()
    assert d.between(0.0, 100.0).all()
    # J = 3K - 2D can exceed the bounds, but must stay finite.
    assert not j.isna().any()


def test_compute_bollinger_mid_is_rolling_mean() -> None:
    close = pd.Series(np.linspace(20, 10, 60))
    mid, upper, lower, _, _ = compute_bollinger(close, n=20, k=2.0)

    expected_mid = close.rolling(20, min_periods=1).mean()
    expected_std = close.rolling(20, min_periods=1).std(ddof=0)
    assert np.allclose(mid.to_numpy(), expected_mid.to_numpy())
    assert np.allclose(upper.to_numpy(), (expected_mid + 2.0 * expected_std).to_numpy())
    assert np.allclose(lower.to_numpy(), (expected_mid - 2.0 * expected_std).to_numpy())


def test_compute_bollinger_pct_b_formula() -> None:
    close = pd.Series(np.linspace(20, 10, 60))
    _, upper, lower, pct_b, _ = compute_bollinger(close, n=20, k=2.0)

    expected = (close - lower) / (upper - lower)
    assert np.allclose(pct_b.to_numpy(), expected.to_numpy(), equal_nan=True)


def test_compute_bollinger_bandwidth_formula() -> None:
    close = pd.Series(np.linspace(20, 10, 60))
    mid, upper, lower, _, bandwidth = compute_bollinger(close, n=20, k=2.0)

    expected = (upper - lower) / mid
    assert np.allclose(bandwidth.to_numpy(), expected.to_numpy(), equal_nan=True)


def test_find_swing_points_detects_peak_and_trough() -> None:
    # A single clear peak then a single clear trough.
    values = np.array([1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4, 3, 2, 1], dtype="float64")
    series = pd.Series(values)
    highs, lows = find_swing_points(series, pivot=2)

    assert (4, 5.0) in highs  # peak at index 4
    assert (8, 1.0) in lows  # trough at index 8


def _top_divergence_frame() -> pd.DataFrame:
    """Steep rally -> pullback -> higher high with lower DIF -> decline to confirm."""
    close = pd.Series(
        list(np.linspace(100, 150, 30))
        + list(np.linspace(150, 110, 15))
        + list(np.linspace(110, 155, 20))
        + list(np.linspace(155, 150, 4))
    )
    return pd.DataFrame({"close": close, "high": close + 0.5, "low": close - 0.5})


def _bottom_divergence_frame() -> pd.DataFrame:
    """Steep decline -> rally -> lower low with higher DIF -> rally to confirm."""
    close = pd.Series(
        list(np.linspace(100, 50, 30))
        + list(np.linspace(50, 90, 15))
        + list(np.linspace(90, 45, 20))
        + list(np.linspace(45, 50, 4))
    )
    return pd.DataFrame({"close": close, "high": close + 0.5, "low": close - 0.5})


def test_find_divergence_top() -> None:
    df = _top_divergence_frame()
    dif, _, _ = compute_macd(df["close"])
    result = find_divergence(df, dif, lookback=len(df), pivot=2, tolerance=0.002)

    assert result.triggered is True
    assert result.kind == "top"
    assert result.strength < 0
    assert result.price_hi_idx is not None
    assert result.dif_hi_idx is not None


def test_find_divergence_bottom() -> None:
    df = _bottom_divergence_frame()
    dif, _, _ = compute_macd(df["close"])
    result = find_divergence(df, dif, lookback=len(df), pivot=2, tolerance=0.002)

    assert result.triggered is True
    assert result.kind == "bottom"
    assert result.strength > 0
    assert result.price_lo_idx is not None
    assert result.dif_lo_idx is not None


def test_find_divergence_insufficient_data() -> None:
    # Too few bars to confirm any swing points.
    df = pd.DataFrame({"close": [100.0, 101.0, 102.0], "high": [100.5, 101.5, 102.5], "low": [99.5, 100.5, 101.5]})
    dif, _, _ = compute_macd(df["close"])
    result = find_divergence(df, dif, lookback=3, pivot=2, tolerance=0.002)

    assert result.triggered is False
    assert result.kind is None
    assert result.strength == 0.0
