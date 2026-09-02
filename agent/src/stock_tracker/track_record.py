"""Build a verifiable track record from persisted analysis reports.

Each persisted analysis stores per-symbol structured recommendations. Any
recommendation that carries a price anchor (entry zone, target zone or a
stop-loss) is a *prediction* that can be verified against the latest close
price: whether it reached its target or was stopped out.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.stock_tracker.models import PriceZone, TrackRecordItem

__all__ = ["build_track_record"]

_ACTION_VOCAB = {"buy", "hold", "reduce", "avoid"}


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def _zone(value: Any) -> Optional[PriceZone]:
    """Parse a stored entry/target band into a PriceZone, tolerantly."""
    if value is None:
        return None
    try:
        return PriceZone.model_validate(value)
    except Exception:  # noqa: BLE001
        return None


def _action(value: Any) -> str:
    """Normalize a stored action token into one of the four action values."""
    if isinstance(value, str) and value.strip().lower() in _ACTION_VOCAB:
        return value.strip().lower()
    return "hold"


def _trading_date(envelope: Dict[str, Any]) -> Any:
    return envelope.get("trading_date")


def _items_for_analysis(envelope: Dict[str, Any]) -> List[TrackRecordItem]:
    """Turn one analysis envelope into its verifiable prediction items."""
    report = envelope.get("report")
    if not isinstance(report, dict):
        return []
    raw_symbols = report.get("symbols", [])
    if not isinstance(raw_symbols, list):
        return []

    analysis_id = str(envelope.get("id") or "")
    trading_date = _trading_date(envelope)
    items: List[TrackRecordItem] = []
    for symbol in raw_symbols:
        if not isinstance(symbol, dict):
            continue
        code = str(symbol.get("code") or "").strip()
        if not code:
            continue
        entry = _zone(symbol.get("entry_zone") or symbol.get("entry"))
        target = _zone(symbol.get("target_zone") or symbol.get("target"))
        stop = _num(symbol.get("stop_loss"))
        # Only recommendations carrying a price anchor count as predictions.
        if entry is None and target is None and stop is None:
            continue
        items.append(
            TrackRecordItem(
                analysis_id=analysis_id,
                trading_date=trading_date,
                code=code,
                name=symbol.get("name"),
                action=_action(symbol.get("action") or symbol.get("recommendation")),
                confidence=_num(symbol.get("confidence")),
                entry_zone=entry,
                target_zone=target,
                stop_loss=stop,
                time_horizon=symbol.get("time_horizon"),
            )
        )
    return items


def _status(item: TrackRecordItem, close: Optional[float]) -> str:
    """Classify the prediction against the latest close price."""
    if close is None:
        return "pending"
    if item.stop_loss is not None and close <= item.stop_loss:
        return "stopped_out"
    high = item.target_zone.high if item.target_zone else None
    if high is not None and close >= high:
        return "hit_target"
    return "active"


def build_track_record(
    analyses: List[Dict[str, Any]],
    close_by_code: Dict[str, float],
) -> List[TrackRecordItem]:
    """Build the ordered track record from persisted analysis envelopes.

    Args:
        analyses: Analysis envelopes (each has an ``id``, ``trading_date`` and a
            ``report`` dict). Newest-first order is preserved in the output.
        close_by_code: Current close price per symbol code.

    Returns:
        A list of :class:`TrackRecordItem`, one per persisted prediction.
    """
    items: List[TrackRecordItem] = []
    for envelope in analyses:
        for item in _items_for_analysis(envelope):
            item.current_close = close_by_code.get(item.code)
            item.status = _status(item, item.current_close)
            items.append(item)
    return items
