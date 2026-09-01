"""A-share multi-period stock tracker toolkit."""

from __future__ import annotations

from src.stock_tracker.engine import StockTrackerEngine
from src.stock_tracker.models import (
    CrossDayDiff,
    PeriodMetrics,
    PeriodSignals,
    SignalState,
    SignalType,
    SignalValue,
    SymbolSnapshot,
    TrackerConfig,
    TrackerSettings,
    TrackerSnapshot,
    TrackerThresholds,
)
from src.stock_tracker.signals import (
    BreakoutDetector,
    MaAlignmentDetector,
    RSIDetector,
    VolumeSpikeDetector,
    compute_mas,
    compute_rsi,
    get_detector,
    get_detector_meta,
    list_detector_meta,
    list_detector_names,
    register_detector,
)
from src.stock_tracker.store import TrackerStore

__all__ = [
    "BreakoutDetector",
    "CrossDayDiff",
    "MaAlignmentDetector",
    "PeriodMetrics",
    "PeriodSignals",
    "RSIDetector",
    "SignalState",
    "SignalType",
    "SignalValue",
    "StockTrackerEngine",
    "SymbolSnapshot",
    "TrackerConfig",
    "TrackerSettings",
    "TrackerSnapshot",
    "TrackerStore",
    "TrackerThresholds",
    "VolumeSpikeDetector",
    "compute_mas",
    "compute_rsi",
    "get_detector",
    "get_detector_meta",
    "list_detector_meta",
    "list_detector_names",
    "register_detector",
]
