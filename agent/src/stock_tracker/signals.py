"""Pluggable signal detectors for the stock tracker."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Type

import numpy as np
import pandas as pd

from src.stock_tracker.models import (
    SignalState,
    SignalType,
    SignalValue,
    TrackerThresholds,
)


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI for a price series.

    Args:
        series: Closing prices.
        period: RSI lookback in trading days.

    Returns:
        RSI series aligned with the input index.
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0)).replace(0, np.nan)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_mas(df: pd.DataFrame) -> pd.DataFrame:
    """Append common moving-average columns to a price frame in place."""
    df = df.copy()
    df["ma5"] = df["close"].rolling(window=5, min_periods=1).mean()
    df["ma10"] = df["close"].rolling(window=10, min_periods=1).mean()
    df["ma20"] = df["close"].rolling(window=20, min_periods=1).mean()
    df["ma60"] = df["close"].rolling(window=60, min_periods=1).mean()
    return df


class SignalDetector(ABC):
    """Base class for a signal detector."""

    name: SignalType = ""  # type: ignore[assignment]

    @abstractmethod
    def detect(
        self,
        code: str,
        df: pd.DataFrame,
        period: int,
        thresholds: TrackerThresholds,
    ) -> SignalValue:
        """Return the signal value for the latest bar."""


class VolumeSpikeDetector(SignalDetector):
    """Detect unusual volume expansion over a lookback window."""

    name = "volume_spike"

    def detect(
        self,
        code: str,
        df: pd.DataFrame,
        period: int,
        thresholds: TrackerThresholds,
    ) -> SignalValue:
        if len(df) < 2 or "volume" not in df.columns:
            return SignalValue(triggered=False, description="No volume data")

        latest_volume = float(df["volume"].iloc[-1])
        recent = df["volume"].iloc[-period:-1]
        if len(recent) == 0 or recent.mean() == 0 or pd.isna(recent.mean()):
            return SignalValue(triggered=False, description="Insufficient recent volume")

        avg_volume = float(recent.mean())
        ratio = latest_volume / avg_volume
        threshold = thresholds.volume_spike
        triggered = ratio >= threshold

        state = SignalState.NONE
        if triggered:
            state = SignalState.STRONG if ratio >= threshold * 1.25 else SignalState.TRIGGERED

        return SignalValue(
            triggered=triggered,
            state=state,
            value=round(ratio, 3),
            threshold=threshold,
            description=(
                f"Volume {ratio:.2f}x the {period}-day average"
                if triggered
                else f"Volume {ratio:.2f}x average, below {threshold}x threshold"
            ),
        )


class BreakoutDetector(SignalDetector):
    """Detect when price breaks the recent N-day high or low."""

    name = "breakout"

    def detect(
        self,
        code: str,
        df: pd.DataFrame,
        period: int,
        thresholds: TrackerThresholds,
    ) -> SignalValue:
        window = thresholds.breakout_window
        if len(df) < window + 1 or "close" not in df.columns or "high" not in df.columns or "low" not in df.columns:
            return SignalValue(triggered=False, description="Insufficient data for breakout")

        latest_close = float(df["close"].iloc[-1])
        recent = df.iloc[-window - 1 : -1]
        recent_high = float(recent["high"].max())
        recent_low = float(recent["low"].min())

        upper_tolerance = recent_high * 0.999
        lower_tolerance = recent_low * 1.001

        if latest_close >= upper_tolerance:
            return SignalValue(
                triggered=True,
                state=SignalState.TRIGGERED,
                value=round(latest_close / recent_high - 1, 5),
                threshold=None,
                description=f"Broke above {window}-day high {recent_high:.2f}",
            )
        if latest_close <= lower_tolerance:
            return SignalValue(
                triggered=True,
                state=SignalState.TRIGGERED,
                value=round(latest_close / recent_low - 1, 5),
                threshold=None,
                description=f"Broke below {window}-day low {recent_low:.2f}",
            )

        return SignalValue(
            triggered=False,
            state=SignalState.NONE,
            value=round((latest_close - recent_low) / (recent_high - recent_low), 3) if recent_high != recent_low else 0.0,
            threshold=None,
            description=f"Within {window}-day range",
        )


class MaAlignmentDetector(SignalDetector):
    """Detect moving-average bullish/bearish alignment."""

    name = "ma_alignment"

    def detect(
        self,
        code: str,
        df: pd.DataFrame,
        period: int,
        thresholds: TrackerThresholds,
    ) -> SignalValue:
        if len(df) < 60 or "ma5" not in df.columns:
            return SignalValue(triggered=False, description="Need 60+ bars for MA alignment")

        latest = df.iloc[-1]
        ma5 = float(latest["ma5"])
        ma10 = float(latest["ma10"])
        ma20 = float(latest["ma20"])
        ma60 = float(latest["ma60"])

        bullish = ma5 > ma10 > ma20 > ma60
        bearish = ma5 < ma10 < ma20 < ma60

        if bullish:
            return SignalValue(
                triggered=True,
                state=SignalState.TRIGGERED,
                value=round((ma5 - ma60) / ma60, 5),
                threshold=None,
                description="Bullish MA alignment (5>10>20>60)",
            )
        if bearish:
            return SignalValue(
                triggered=True,
                state=SignalState.TRIGGERED,
                value=round((ma5 - ma60) / ma60, 5),
                threshold=None,
                description="Bearish MA alignment (5<10<20<60)",
            )

        return SignalValue(
            triggered=False,
            state=SignalState.NONE,
            value=round((ma5 - ma60) / ma60, 5),
            threshold=None,
            description="MA alignment mixed",
        )


# Global detector registry. New detectors are added here and picked up automatically.
_DETECTOR_REGISTRY: Dict[SignalType, Type[SignalDetector]] = {}
_DETECTOR_INSTANCES: Dict[SignalType, SignalDetector] = {}


def _register_detector(cls: Type[SignalDetector]) -> Type[SignalDetector]:
    _DETECTOR_REGISTRY[cls.name] = cls
    return cls


_register_detector(VolumeSpikeDetector)
_register_detector(BreakoutDetector)
_register_detector(MaAlignmentDetector)


def get_detector(name: SignalType) -> SignalDetector:
    """Return a cached instance of the named detector."""
    instance = _DETECTOR_INSTANCES.get(name)
    if instance is None:
        try:
            instance = _DETECTOR_REGISTRY[name]()
        except KeyError as exc:
            raise ValueError(f"Unknown signal type: {name}") from exc
        _DETECTOR_INSTANCES[name] = instance
    return instance


def list_detectors() -> List[Type[SignalDetector]]:
    """Return all registered detector classes."""
    return list(_DETECTOR_REGISTRY.values())


__all__ = [
    "compute_rsi",
    "compute_mas",
    "SignalDetector",
    "VolumeSpikeDetector",
    "BreakoutDetector",
    "MaAlignmentDetector",
    "get_detector",
    "list_detectors",
]
