"""Buy-signal alert watcher: de-dupe, persist, and list signal notifications.

Decoupled from the engine (which only computes *hits* — the raw ``code/name/
price/signal_date`` tuples a strategy currently triggers on). This module owns
the notification bookkeeping: a compact ``state.json`` de-dupes each
``(code, signal_date)`` pair so a repeat scan never re-notifies the same signal,
and each alert is persisted as ``alerts/<id>.json`` for the frontend to poll.

The state also stores a fingerprint of the strategy spec; when the user edits the
strategy, the fingerprint changes and the de-dupe map is reset so the new rules
are re-evaluated from scratch instead of silently inheriting the old ones.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from src.stock_tracker.store import atomic_write_json, tracker_data_root

logger = logging.getLogger(__name__)


class BuySignalAlert(BaseModel):
    """One persisted buy-signal notification."""

    id: str
    code: str
    name: Optional[str] = None
    price: Optional[float] = None
    signal_date: str
    strategy_label: str = "自定义策略"
    source: str = "intraday"  # "intraday" | "close_confirm"
    triggered_at: str
    acknowledged: bool = False


def spec_fingerprint(spec: Any) -> str:
    """Return a stable hash of a strategy spec for de-dupe invalidation."""
    try:
        blob = json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = str(spec)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class AlertStore:
    """JSON-file store for buy-signal alerts plus de-dupe state."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else tracker_data_root() / "alerts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"

    # ------------------------------------------------------------------
    # De-dupe state
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"spec_fingerprint": None, "last_signal_date": {}}
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return {"spec_fingerprint": None, "last_signal_date": {}}
            last = raw.get("last_signal_date") or {}
            return {
                "spec_fingerprint": raw.get("spec_fingerprint"),
                "last_signal_date": last if isinstance(last, dict) else {},
            }
        except Exception as exc:  # noqa: BLE001 - corrupt state degrades to empty
            logger.warning("Failed to load alert state: %s", exc)
            return {"spec_fingerprint": None, "last_signal_date": {}}

    def _save_state(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.state_path, state)

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _alert_path(self, alert_id: str) -> Path:
        return self.root / f"{alert_id}.json"

    def emit(
        self,
        hits: List[Dict[str, Any]],
        spec: Any,
        source: str = "intraday",
        strategy_label: Optional[str] = None,
    ) -> List[BuySignalAlert]:
        """De-dupe and persist alerts; return the newly emitted ones.

        A hit is a dict with ``code``, and optionally ``name``/``price``/
        ``signal_date``. Missing ``signal_date`` falls back to today. De-dupe is
        per ``(code, signal_date)``; changing the strategy (fingerprint) resets
        the de-dupe map so new rules are evaluated fresh.
        """
        label = strategy_label or "自定义策略"
        fingerprint = spec_fingerprint(spec)
        state = self._load_state()
        if state.get("spec_fingerprint") != fingerprint:
            state = {"spec_fingerprint": fingerprint, "last_signal_date": {}}

        emitted: List[BuySignalAlert] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            code = hit.get("code")
            if not code:
                continue
            signal_date = str(hit.get("signal_date") or datetime.now().astimezone().date())
            if state["last_signal_date"].get(code) == signal_date:
                continue
            now = datetime.now().astimezone()
            alert = BuySignalAlert(
                id=now.strftime("%Y%m%dT%H%M%S%f"),
                code=code,
                name=hit.get("name"),
                price=hit.get("price"),
                signal_date=signal_date,
                strategy_label=label,
                source=source,
                triggered_at=now.isoformat(),
            )
            self._write_alert(alert)
            state["last_signal_date"][code] = signal_date
            emitted.append(alert)
        if emitted:
            self._save_state(state)
        return emitted

    def _write_alert(self, alert: BuySignalAlert) -> None:
        atomic_write_json(self._alert_path(alert.id), alert.model_dump(mode="json"))

    def list(self, limit: int = 50, unread_only: bool = False) -> List[BuySignalAlert]:
        """Return persisted alerts newest-first."""
        alerts: List[BuySignalAlert] = []
        try:
            entries = [
                p
                for p in self.root.iterdir()
                if p.is_file() and p.suffix == ".json" and p.name != "state.json"
            ]
        except OSError:
            return alerts
        for path in entries:
            try:
                with path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    alerts.append(BuySignalAlert.model_validate(raw))
            except Exception:  # noqa: BLE001 - a bad file never blocks listing
                continue
        alerts.sort(key=lambda a: a.id, reverse=True)
        if unread_only:
            alerts = [a for a in alerts if not a.acknowledged]
        return alerts[: max(0, min(limit, 500))]

    def acknowledge(self, ids: Optional[List[str]] = None) -> int:
        """Mark alerts read; ``ids=None`` marks all. Returns the count updated."""
        alerts = self.list(limit=500)
        targets = set(ids) if ids is not None else None
        updated = 0
        for alert in alerts:
            if targets is not None and alert.id not in targets:
                continue
            if alert.acknowledged:
                continue
            alert.acknowledged = True
            self._write_alert(alert)
            updated += 1
        return updated


__all__ = ["BuySignalAlert", "AlertStore", "spec_fingerprint"]
