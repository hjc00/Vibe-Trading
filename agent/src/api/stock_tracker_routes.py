"""Authenticated Web API for the A-share multi-period stock tracker."""

from __future__ import annotations

import logging
import sys as _sys
import threading
from copy import deepcopy
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.stock_tracker.engine import StockTrackerEngine
from src.stock_tracker.models import TrackerConfig, TrackerSettings
from src.stock_tracker.store import TrackerStore

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Any]

_REFRESH_LOCK = threading.Lock()
_REFRESH_OPERATION_LOCK = threading.Lock()
_REFRESH_STATE: Dict[str, Any] = {
    "running": False,
    "current": None,
    "symbols": {},
    "error": None,
}

_store: Optional[TrackerStore] = None


def _get_store() -> TrackerStore:
    """Return the singleton tracker store."""
    global _store
    if _store is None:
        _store = TrackerStore()
    return _store


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class TrackerSettingsRequest(BaseModel):
    """Update payload for tracker configuration."""

    watchlist: Optional[List[str]] = None
    periods: Optional[List[int]] = None
    signals: Optional[List[str]] = None
    thresholds: Optional[Dict[str, float]] = None


class TrackerConfigResponse(BaseModel):
    """Current tracker configuration."""

    watchlist: List[str]
    periods: List[int]
    signals: List[str]
    thresholds: Dict[str, float]


class TrackerSettingsResponse(BaseModel):
    """Persisted tracker settings envelope."""

    config: TrackerConfigResponse
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RefreshRequest(BaseModel):
    """Optional refresh parameters."""

    force: bool = Field(default=False, description="Ignore an in-flight refresh and start a new one.")


class SnapshotSummary(BaseModel):
    """Lightweight snapshot list item."""

    trading_date: str
    symbol_count: int
    signal_count: int
    generated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_from_request(request: TrackerSettingsRequest) -> TrackerConfig:
    """Build a validated config from an update request."""
    current = _get_store().get_settings().config
    kwargs: Dict[str, Any] = {}
    if request.watchlist is not None:
        kwargs["watchlist"] = request.watchlist
    if request.periods is not None:
        kwargs["periods"] = request.periods
    if request.signals is not None:
        kwargs["signals"] = request.signals
    if request.thresholds is not None:
        kwargs["thresholds"] = current.thresholds.model_copy(update=request.thresholds)

    # Merge with current config so omitted fields keep their defaults, then
    # reconstruct to re-run Pydantic validators (model_copy skips them).
    base = current.model_dump()
    base.update(kwargs)
    return TrackerConfig.model_validate(base)


def _refresh_snapshot_sync(end_date: Optional[date] = None) -> Dict[str, Any]:
    """Run the tracker refresh synchronously inside the API worker thread."""
    store = _get_store()
    settings = store.get_settings()
    previous = store.get_latest_snapshot()

    engine = StockTrackerEngine(config=settings.config)
    snapshot = engine.refresh(end_date=end_date, previous=previous)
    store.save_snapshot(snapshot)

    return {
        "status": "ok",
        "snapshot": snapshot.model_dump(mode="json"),
    }


def _set_refresh_progress(code: str, status: str, error: Optional[str] = None) -> None:
    """Record per-symbol progress for the polling endpoint."""
    with _REFRESH_LOCK:
        _REFRESH_STATE["current"] = code if status == "refreshing" else _REFRESH_STATE.get("current")
        _REFRESH_STATE["symbols"][code] = {"status": status, "error": error}
        if status == "ok":
            _REFRESH_STATE["current"] = None


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_stock_tracker_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount stock tracker routes onto ``app``."""
    if require_auth is None:
        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError(
                "register_stock_tracker_routes: api_server module not in sys.modules; "
                "ensure api_server is imported before calling this function"
            )
        require_auth = host.require_auth

    @app.get("/api/stock-tracker/settings", response_model=TrackerSettingsResponse)
    async def get_settings(principal=Depends(require_auth)) -> TrackerSettingsResponse:  # noqa: ARG001
        settings = _get_store().get_settings()
        return TrackerSettingsResponse(
            config=TrackerConfigResponse(**settings.config.model_dump(mode="json")),
            created_at=settings.created_at.isoformat() if settings.created_at else None,
            updated_at=settings.updated_at.isoformat() if settings.updated_at else None,
        )

    @app.put("/api/stock-tracker/settings", response_model=TrackerSettingsResponse)
    async def update_settings(
        request: TrackerSettingsRequest,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> TrackerSettingsResponse:
        try:
            new_config = _config_from_request(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store = _get_store()
        settings = store.get_settings()
        settings.config = new_config
        store.save_settings(settings)

        return TrackerSettingsResponse(
            config=TrackerConfigResponse(**settings.config.model_dump(mode="json")),
            created_at=settings.created_at.isoformat() if settings.created_at else None,
            updated_at=settings.updated_at.isoformat() if settings.updated_at else None,
        )

    @app.get("/api/stock-tracker")
    async def get_latest_snapshot(principal=Depends(require_auth)) -> Dict[str, Any]:  # noqa: ARG001
        store = _get_store()
        snapshot = store.get_latest_snapshot()
        if snapshot is None:
            return {
                "status": "empty",
                "snapshot": None,
                "message": "No snapshot available. Trigger a refresh to create one.",
            }
        return {"status": "ok", "snapshot": snapshot.model_dump(mode="json")}

    @app.get("/api/stock-tracker/history")
    async def get_history(
        limit: int = 30,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> Dict[str, Any]:
        snapshots = _get_store().list_snapshots(limit=max(1, min(limit, 365)))
        return {
            "status": "ok",
            "snapshots": [s.model_dump(mode="json") for s in snapshots],
        }

    @app.post("/api/stock-tracker/refresh")
    async def refresh_snapshot(
        request: RefreshRequest | None = None,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> Dict[str, Any]:
        request = request or RefreshRequest()

        with _REFRESH_OPERATION_LOCK:
            if _REFRESH_STATE["running"] and not request.force:
                return {
                    "status": "refreshing",
                    "message": "A refresh is already in progress.",
                    "refresh": _refresh_snapshot(),
                }

            with _REFRESH_LOCK:
                _REFRESH_STATE.update(
                    {
                        "running": True,
                        "current": None,
                        "symbols": {},
                        "error": None,
                    }
                )

            try:
                result = _refresh_snapshot_sync()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Tracker refresh failed")
                with _REFRESH_LOCK:
                    _REFRESH_STATE["error"] = f"{type(exc).__name__}: {exc}"
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            finally:
                with _REFRESH_LOCK:
                    _REFRESH_STATE["running"] = False
                    _REFRESH_STATE["current"] = None

        return result

    @app.get("/api/stock-tracker/refresh-status")
    async def refresh_status(principal=Depends(require_auth)) -> Dict[str, Any]:  # noqa: ARG001
        with _REFRESH_LOCK:
            return {"status": "ok", "refresh": deepcopy(_REFRESH_STATE)}


def _refresh_snapshot() -> Dict[str, Any]:
    """Return a thread-safe copy of the current refresh state."""
    with _REFRESH_LOCK:
        return deepcopy(_REFRESH_STATE)


__all__ = ["register_stock_tracker_routes"]
