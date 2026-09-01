"""Pluggable signal detectors for the stock tracker."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Type

import numpy as np
import pandas as pd

from src.stock_tracker.models import (
    SignalState,
    SignalType,
    SignalValue,
    TrackerThresholds,
)

SignalDirection = Literal["bullish", "bearish", "neutral", "both"]
SignalFormat = Literal["percent", "multiple", "raw", "price"]


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
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(pd.notna(rsi), 100.0)
    return rsi


def compute_mas(df: pd.DataFrame) -> pd.DataFrame:
    """Append common moving-average columns to a price frame in place."""
    df = df.copy()
    df["ma5"] = df["close"].rolling(window=5, min_periods=1).mean()
    df["ma10"] = df["close"].rolling(window=10, min_periods=1).mean()
    df["ma20"] = df["close"].rolling(window=20, min_periods=1).mean()
    df["ma60"] = df["close"].rolling(window=60, min_periods=1).mean()
    return df


@dataclass
class SignalMeta:
    """Self-describing metadata for a signal detector.

    This is what allows the frontend and engine to handle a new signal without
    hard-coding its name, formatting, or ranking behaviour.
    """

    name: str
    category: str
    direction: SignalDirection
    label: str
    description: str
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    default_params: Dict[str, float] = field(default_factory=dict)
    format: SignalFormat = "raw"
    ranking_enabled: bool = True
    ranking_extractor: Optional[Callable[[SignalValue], float]] = None
    show_in_table: bool = True
    is_global: bool = False

    def model_dump(self) -> Dict[str, Any]:
        """Return a JSON-safe dict (callables are omitted)."""
        return {
            "name": self.name,
            "category": self.category,
            "direction": self.direction,
            "label": self.label,
            "description": self.description,
            "params": self.params,
            "default_params": self.default_params,
            "format": self.format,
            "ranking_enabled": self.ranking_enabled,
            "show_in_table": self.show_in_table,
            "is_global": self.is_global,
        }


class SignalDetector(ABC):
    """Base class for a signal detector."""

    name: SignalType = ""  # type: ignore[assignment]
    meta: SignalMeta = SignalMeta(
        name="",
        category="custom",
        direction="neutral",
        label="",
        description="",
    )

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
    meta = SignalMeta(
        name="volume_spike",
        category="volume",
        direction="neutral",
        label="Volume spike",
        description="Latest volume is unusually large versus the recent average.",
        params={
            "volume_spike": {
                "type": "float",
                "min": 1.0,
                "default": 2.0,
                "description": "Volume ratio versus recent average required to trigger.",
            }
        },
        default_params={"volume_spike": 2.0},
        format="multiple",
        ranking_enabled=True,
        ranking_extractor=lambda sv: sv.value or 0.0,
    )

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
        threshold = thresholds.get("volume_spike", 2.0)
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
    meta = SignalMeta(
        name="breakout",
        category="momentum",
        direction="both",
        label="Breakout",
        description="Price closes above the recent high or below the recent low.",
        params={
            "breakout_window": {
                "type": "int",
                "min": 5,
                "max": 250,
                "default": 20,
                "description": "Number of days used to define the recent high/low range.",
            }
        },
        default_params={"breakout_window": 20.0},
        format="percent",
        ranking_enabled=True,
        ranking_extractor=lambda sv: abs(sv.value or 0.0),
    )

    def detect(
        self,
        code: str,
        df: pd.DataFrame,
        period: int,
        thresholds: TrackerThresholds,
    ) -> SignalValue:
        window = int(thresholds.get("breakout_window", 20))
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
    meta = SignalMeta(
        name="ma_alignment",
        category="trend",
        direction="both",
        label="MA alignment",
        description="Moving averages are aligned bullishly or bearishly.",
        params={},
        default_params={},
        format="percent",
        ranking_enabled=False,
        show_in_table=False,
        is_global=True,
    )

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


class RSIDetector(SignalDetector):
    """Detect RSI overbought/oversold extremes within the active period window."""

    name = "rsi"
    meta = SignalMeta(
        name="rsi",
        category="momentum",
        direction="both",
        label="RSI",
        description="RSI reaches overbought or oversold levels using a configurable lookback.",
        params={
            "rsi_period": {
                "type": "int",
                "min": 2,
                "max": 250,
                "default": 14,
                "description": "RSI lookback period in trading days.",
            },
            "rsi_overbought": {
                "type": "float",
                "min": 50.0,
                "max": 100.0,
                "default": 70.0,
                "description": "RSI level considered overbought.",
            },
            "rsi_oversold": {
                "type": "float",
                "min": 0.0,
                "max": 50.0,
                "default": 30.0,
                "description": "RSI level considered oversold.",
            },
        },
        default_params={"rsi_period": 14.0, "rsi_overbought": 70.0, "rsi_oversold": 30.0},
        format="raw",
        ranking_enabled=True,
        ranking_extractor=lambda sv: abs((sv.value or 50.0) - 50.0),
    )

    def detect(
        self,
        code: str,
        df: pd.DataFrame,
        period: int,
        thresholds: TrackerThresholds,
    ) -> SignalValue:
        # Use a configurable RSI lookback (default 14) across all period columns
        # instead of tying the lookback to the tracker period.
        lookback = int(thresholds.get("rsi_period", 14))
        lookback = max(2, min(lookback, 250))
        window = df.tail(lookback)
        if len(window) < lookback or "close" not in window.columns:
            return SignalValue(triggered=False, description=f"Need {lookback}+ bars for RSI")

        rsi_series = compute_rsi(window["close"], period=lookback)
        rsi = float(rsi_series.iloc[-1])
        if pd.isna(rsi):
            return SignalValue(triggered=False, description=f"Need {lookback}+ bars for RSI")

        overbought = float(thresholds.get("rsi_overbought", 70.0))
        oversold = float(thresholds.get("rsi_oversold", 30.0))

        if rsi >= overbought:
            return SignalValue(
                triggered=True,
                state=SignalState.STRONG,
                value=round(rsi, 2),
                threshold=overbought,
                description=f"RSI overbought {rsi:.1f} (>= {overbought})",
            )
        if rsi <= oversold:
            return SignalValue(
                triggered=True,
                state=SignalState.TRIGGERED,
                value=round(rsi, 2),
                threshold=oversold,
                description=f"RSI oversold {rsi:.1f} (<= {oversold})",
            )

        return SignalValue(
            triggered=False,
            state=SignalState.NONE,
            value=round(rsi, 2),
            threshold=None,
            description=f"RSI {rsi:.1f}",
        )


# Global detector registry. New detectors are added here and picked up automatically.
_DETECTOR_REGISTRY: Dict[SignalType, Type[SignalDetector]] = {}
_DETECTOR_META: Dict[SignalType, SignalMeta] = {}


def register_detector(cls: Type[SignalDetector]) -> Type[SignalDetector]:
    """Register a detector class and its metadata."""
    _DETECTOR_REGISTRY[cls.name] = cls
    _DETECTOR_META[cls.name] = cls.meta
    return cls


class MarginExpansionDetector(SignalDetector):
    """Detect financing-balance expansion versus the previous trading day."""

    name = "margin_expansion"
    meta = SignalMeta(
        name="margin_expansion",
        category="capital",
        direction="bullish",
        label="Margin expansion",
        description="Outstanding financing balance expanded versus the prior trading day.",
        params={
            "margin_expansion_threshold": {
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.03,
                "description": "Financing balance day-over-day change rate required to trigger.",
            }
        },
        default_params={"margin_expansion_threshold": 0.03},
        format="percent",
        ranking_enabled=True,
        ranking_extractor=lambda sv: sv.value or 0.0,
    )

    def detect(
        self,
        code: str,
        df: pd.DataFrame,
        period: int,
        thresholds: TrackerThresholds,
    ) -> SignalValue:
        capital = df.attrs.get("capital")
        if capital is None or capital.margin.financing_balance_change is None:
            return SignalValue(triggered=False, description="No margin change data")

        change = capital.margin.financing_balance_change
        balance = capital.margin.financing_balance
        prior = balance - change if balance is not None else None
        if prior is None or prior <= 0:
            return SignalValue(triggered=False, description="Cannot compute margin change rate")

        change_pct = change / prior
        threshold = float(thresholds.get("margin_expansion_threshold", 0.03))

        if change_pct >= threshold:
            state = SignalState.STRONG if change_pct >= threshold * 1.5 else SignalState.TRIGGERED
            return SignalValue(
                triggered=True,
                state=state,
                value=round(change_pct, 5),
                threshold=threshold,
                description=f"Financing balance +{change_pct * 100:.2f}% (>= {threshold * 100:.2f}%)",
            )

        return SignalValue(
            triggered=False,
            state=SignalState.NONE,
            value=round(change_pct, 5),
            threshold=threshold,
            description=f"Financing balance {change_pct * 100:+.2f}%",
        )


register_detector(VolumeSpikeDetector)
register_detector(BreakoutDetector)
register_detector(MaAlignmentDetector)
register_detector(RSIDetector)
register_detector(MarginExpansionDetector)


def list_detector_names() -> List[SignalType]:
    """Return all registered detector names."""
    return list(_DETECTOR_REGISTRY.keys())


def list_detector_meta() -> List[SignalMeta]:
    """Return metadata for all registered detectors."""
    return list(_DETECTOR_META.values())


def get_detector_meta(name: SignalType) -> SignalMeta:
    """Return metadata for a single detector."""
    try:
        return _DETECTOR_META[name]
    except KeyError as exc:
        raise ValueError(f"Unknown signal type: {name}") from exc


_DETECTOR_INSTANCES: Dict[SignalType, SignalDetector] = {}


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


__all__ = [
    "compute_rsi",
    "compute_mas",
    "SignalMeta",
    "SignalDetector",
    "VolumeSpikeDetector",
    "BreakoutDetector",
    "MaAlignmentDetector",
    "RSIDetector",
    "MarginExpansionDetector",
    "get_detector",
    "get_detector_meta",
    "list_detector_meta",
    "list_detector_names",
    "register_detector",
]
