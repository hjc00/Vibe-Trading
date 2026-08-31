"""Authenticated Web API for the A-share multi-period stock tracker."""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.stock_tracker.analyzer import run_analysis
from src.stock_tracker.engine import StockTrackerEngine
from src.stock_tracker.models import TrackerConfig, normalize_a_share_code
from src.stock_tracker.store import TrackerStore

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Any]

_MAX_ANALYZE_SYMBOLS = 20

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


class TrackerAnalyzeRequest(BaseModel):
    """Request body for LLM analysis over selected symbols."""

    symbols: List[str]
    focus: Literal["rank_opportunities", "risk_check", "custom"] = "rank_opportunities"
    user_prompt: Optional[str] = None


class TrackerAnalyzeResponse(BaseModel):
    """Envelope returned by the analysis endpoint."""

    status: str
    report: Dict[str, Any]
    id: Optional[str] = None
    generated_at: Optional[str] = None
    trading_date: Optional[str] = None


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


@dataclass
class _SelectedSymbols:
    """Symbols to analyze plus any filtering caveats."""

    symbols: List[Any] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)


def _select_symbols(snapshot: Any, requested: List[str]) -> _SelectedSymbols:
    """Normalize and filter the requested codes against the latest snapshot."""
    normalized: List[str] = []
    for code in requested:
        norm = normalize_a_share_code(code)
        if norm and norm not in normalized:
            normalized.append(norm)

    if len(normalized) > _MAX_ANALYZE_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail=f"At most {_MAX_ANALYZE_SYMBOLS} symbols can be analyzed at once.",
        )

    available = {s.code: s for s in snapshot.symbols}
    selected = _SelectedSymbols()
    for code in normalized:
        symbol = available.get(code)
        if symbol is None:
            selected.caveats.append(f"{code}: not in snapshot")
            continue
        if code in snapshot.unresolved:
            selected.caveats.append(f"{code}: unresolved")
            continue
        if symbol.error:
            selected.caveats.append(f"{code}: {symbol.error}")
            continue
        selected.symbols.append(symbol)

    return selected


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
                result = await asyncio.to_thread(_refresh_snapshot_sync)
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

    @app.post("/api/stock-tracker/analyze", response_model=TrackerAnalyzeResponse)
    async def analyze_snapshot(
        request: TrackerAnalyzeRequest,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> TrackerAnalyzeResponse:
        store = _get_store()
        snapshot = store.get_latest_snapshot()
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No snapshot available. Refresh first.")

        selected = _select_symbols(snapshot, request.symbols)
        if not selected.symbols:
            raise HTTPException(status_code=422, detail="No valid symbols selected for analysis.")

        report = await asyncio.to_thread(
            run_analysis,
            snapshot,
            selected.symbols,
            request.focus,
            request.user_prompt,
        )
        report["caveats"] = selected.caveats + report.get("caveats", [])

        envelope = store.save_analysis(report, trading_date=snapshot.trading_date)

        return TrackerAnalyzeResponse(
            status="ok",
            report=report,
            id=envelope.get("id"),
            generated_at=envelope.get("generated_at"),
            trading_date=envelope.get("trading_date"),
        )

    @app.get("/api/stock-tracker/analyze/history")
    async def get_analysis_history(
        limit: int = 50,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> Dict[str, Any]:
        items = _get_store().list_analyses(limit=limit)
        return {"status": "ok", "items": items}

    @app.get("/api/stock-tracker/analyze/{analysis_id}")
    async def get_analysis_by_id(
        analysis_id: str,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> Dict[str, Any]:
        analysis = _get_store().get_analysis(analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="Analysis not found.")
        return {
            "status": "ok",
            "report": analysis.get("report"),
            "id": analysis.get("id"),
            "trading_date": analysis.get("trading_date"),
            "generated_at": analysis.get("generated_at"),
        }

    @app.get("/api/stock-tracker/analyze")
    async def get_latest_analysis(principal=Depends(require_auth)) -> Dict[str, Any]:  # noqa: ARG001
        analysis = _get_store().get_latest_analysis()
        if analysis is None:
            return {
                "status": "empty",
                "report": None,
                "id": None,
                "trading_date": None,
                "generated_at": None,
            }
        return {
            "status": "ok",
            "report": analysis.get("report"),
            "id": analysis.get("id"),
            "trading_date": analysis.get("trading_date"),
            "generated_at": analysis.get("generated_at"),
        }


def _refresh_snapshot() -> Dict[str, Any]:
    """Return a thread-safe copy of the current refresh state."""
    with _REFRESH_LOCK:
        return deepcopy(_REFRESH_STATE)


__all__ = ["register_stock_tracker_routes"]
