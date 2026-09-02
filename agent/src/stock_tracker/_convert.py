"""Shared scalar-conversion helpers for stock_tracker data loaders.

The Eastmoney/Tushare providers encode missing cells inconsistently (``None``,
empty strings, ``"-"``), so every loader normalizes them the same way. These
helpers are shared by :mod:`src.stock_tracker.capital_data` and
:mod:`src.stock_tracker.valuation_data` so the conversion rules stay in one
place.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional


def to_float(value: Any) -> Optional[float]:
    """Convert a provider cell to float, preserving missing values."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_float_div(value: Any) -> Optional[float]:
    """Convert to float and treat zero as None for use as a divisor."""
    v = to_float(value)
    if v is None or v == 0:
        return None
    return v


def dashed_date(value: Any) -> Optional[date]:
    """Parse a ``YYYY-MM-DD[ HH:MM:SS]`` provider cell into a ``date``."""
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


__all__ = ["dashed_date", "to_float", "to_float_div"]
