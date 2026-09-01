"""Data models for the A-share multi-period stock tracker."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SignalType = str
DEFAULT_SIGNALS: List[SignalType] = ["volume_spike", "breakout", "ma_alignment", "rsi"]
DEFAULT_PERIODS: List[int] = [10, 20, 60]
DEFAULT_WATCHLIST: List[str] = [
    "510300.SH",
    "600519.SH",
    "000001.SZ",
    "300750.SZ",
    "600036.SH",
]


class TrackerThresholds(BaseModel):
    """User-overridable thresholds for signal detection.

    Known thresholds are declared as typed fields so they appear in docs and
    get range validation. Additional per-signal parameters are accepted via
    ``ConfigDict(extra="allow")`` and flattened into the serialized output so
    consumers see a single flat threshold map.
    """

    model_config = ConfigDict(extra="allow")

    volume_spike: float = Field(default=2.0, ge=1.0, description="Volume vs avg ratio to trigger a spike.")
    rsi_overbought: float = Field(default=70.0, ge=50.0, le=100.0)
    rsi_oversold: float = Field(default=30.0, ge=0.0, le=50.0)
    breakout_window: int = Field(default=20, ge=5, le=250)

    def get(self, name: str, default: Any = None) -> Any:
        """Return a threshold by name, including dynamically allowed extras."""
        return getattr(self, name, default)

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        """Flatten known fields and extra fields into a single dict."""
        data = super().model_dump(**kwargs)
        known = set(type(self).model_fields.keys())
        for key, value in self.__dict__.items():
            if key not in known and not key.startswith("_"):
                data[key] = value
        return data


class TrackerConfig(BaseModel):
    """Runtime configuration for one tracker instance."""

    watchlist: List[str] = Field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    periods: List[int] = Field(default_factory=lambda: list(DEFAULT_PERIODS))
    signals: List[SignalType] = Field(default_factory=lambda: list(DEFAULT_SIGNALS))
    thresholds: TrackerThresholds = Field(default_factory=TrackerThresholds)

    @field_validator("watchlist")
    @classmethod
    def _validate_watchlist(cls, value: List[str]) -> List[str]:
        cleaned = []
        for code in value:
            stripped = code.strip().upper()
            if not stripped:
                continue
            normalized = normalize_a_share_code(stripped)
            if normalized is None:
                raise ValueError(f"Invalid A-share code: {code!r}")
            cleaned.append(normalized)
        if not cleaned:
            raise ValueError("watchlist must contain at least one A-share code")
        return cleaned

    @field_validator("periods")
    @classmethod
    def _validate_periods(cls, value: List[int]) -> List[int]:
        unique = sorted({int(p) for p in value})
        if not unique:
            raise ValueError("periods must contain at least one positive integer")
        if any(p < 1 or p > 250 for p in unique):
            raise ValueError("periods must be between 1 and 250 trading days")
        return unique

    @field_validator("signals")
    @classmethod
    def _validate_signals(cls, value: List[str]) -> List[SignalType]:
        """Validate signal names against the detector registry at runtime."""
        from src.stock_tracker.signals import list_detector_names

        known = set(list_detector_names())
        unique: List[SignalType] = []
        seen: set[str] = set()
        for signal in value:
            if signal not in known:
                raise ValueError(f"Unknown signal: {signal}")
            if signal not in seen:
                seen.add(signal)
                unique.append(signal)
        if not unique:
            raise ValueError("signals must contain at least one signal type")
        return unique

    def model_dump_json_safe(self) -> Dict[str, Any]:
        """Return a plain JSON-serializable dict."""
        return self.model_dump(mode="json")


class SignalState(str, Enum):
    """Semantic state of a single signal."""

    NONE = "none"
    TRIGGERED = "triggered"
    STRONG = "strong"


class SignalValue(BaseModel):
    """One detected signal for one symbol/period."""

    triggered: bool = False
    state: SignalState = SignalState.NONE
    value: Optional[float] = None
    threshold: Optional[float] = None
    description: str = ""


class PeriodMetrics(BaseModel):
    """Numeric metrics for a single symbol over one period."""

    period: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    sessions: int = 0
    return_pct: Optional[float] = None
    annualized_volatility: Optional[float] = None
    volume_ratio: Optional[float] = None
    rsi: Optional[float] = None
    price_vs_ma20: Optional[float] = None
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None


class PeriodSignals(BaseModel):
    """All signals for one symbol over one period."""

    metrics: PeriodMetrics
    signals: Dict[SignalType, SignalValue] = Field(default_factory=dict)


class CrossDayDiff(BaseModel):
    """Change between today and the previous trading day for one symbol."""

    signal_count: Optional[Dict[str, int]] = None
    return_pct: Optional[float] = None
    rank_return_10: Optional[int] = None
    rank_change_10: Optional[int] = None
    new_signals: List[str] = Field(default_factory=list)
    cleared_signals: List[str] = Field(default_factory=list)


class SymbolSnapshot(BaseModel):
    """One symbol's slice of a tracker snapshot."""

    code: str
    name: Optional[str] = None
    market: str = "a_share"
    close: Optional[float] = None
    prev_close: Optional[float] = None
    daily_return: Optional[float] = None
    volume: Optional[float] = None
    avg_volume_20: Optional[float] = None
    currency: str = "CNY"
    period_signals: Dict[str, PeriodSignals] = Field(default_factory=dict)
    diff: Optional[CrossDayDiff] = None
    error: Optional[str] = None


class TrackerSnapshot(BaseModel):
    """A complete daily snapshot produced by the tracker engine."""

    generated_at: datetime
    trading_date: Optional[date] = None
    config: TrackerConfig
    symbols: List[SymbolSnapshot] = Field(default_factory=list)
    rankings: Dict[str, List[str]] = Field(default_factory=dict)
    unresolved: List[str] = Field(default_factory=list)
    data_gaps: List[Dict[str, Any]] = Field(default_factory=list)

    def model_dump_json_safe(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict with dates as ISO strings."""
        return self.model_dump(mode="json")


class TrackerSettings(BaseModel):
    """Persisted tracker settings plus optional metadata."""

    config: TrackerConfig = Field(default_factory=TrackerConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


def _infer_a_share_exchange(numeric: str) -> Optional[str]:
    """Infer the exchange suffix from the first two digits of a 6-digit code."""
    prefix = numeric[:2]
    if prefix in ("60", "68", "69"):
        return "SH"
    if prefix in ("00", "30"):
        return "SZ"
    if prefix in (
        "80", "81", "82", "83", "84", "85", "86", "87", "88", "89", "40", "41", "42", "43"
    ):
        return "BJ"
    return None


def _is_a_share_code(code: str) -> bool:
    """Return True for codes like 000001.SZ, 600519.SH, 000001.BJ."""
    import re

    if not re.fullmatch(r"^\d{6}\.(SH|SZ|BJ)$", code):
        return False
    return _infer_a_share_exchange(code[:6]) is not None


def normalize_a_share_code(code: str) -> Optional[str]:
    r"""Normalize an A-share code to ``6-digit.EXCHANGE`` form.

    If the code already has an exchange suffix, the prefix is still trusted
    more than the suffix: ``000938.SH`` is corrected to ``000938.SZ`` because
    ``00`` prefixes are Shenzhen. This lets users paste codes from sources that
    occasionally use the wrong venue suffix.

    If the code is a bare 6-digit number, infer the exchange from the prefix:

      - 60/68/69 -> .SH
      - 00/30    -> .SZ
      - 8/4      -> .BJ

    Returns ``None`` for unrecognized formats.
    """
    import re

    code = code.strip().upper()
    match = re.fullmatch(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", code)
    if not match:
        return None
    numeric = match.group(1)
    inferred = _infer_a_share_exchange(numeric)
    if inferred:
        return f"{numeric}.{inferred}"
    # Unknown prefix but a suffix is present; keep it for forward compatibility.
    suffix = match.group(2)
    if suffix:
        return f"{numeric}.{suffix}"
    return None


__all__ = [
    "DEFAULT_PERIODS",
    "DEFAULT_SIGNALS",
    "DEFAULT_WATCHLIST",
    "SignalType",
    "SignalState",
    "SignalValue",
    "TrackerThresholds",
    "TrackerConfig",
    "PeriodMetrics",
    "PeriodSignals",
    "CrossDayDiff",
    "SymbolSnapshot",
    "TrackerSnapshot",
    "TrackerSettings",
    "normalize_a_share_code",
]
