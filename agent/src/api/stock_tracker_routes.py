"""Authenticated Web API for the A-share multi-period stock tracker."""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.market_data import fetch_market_data
from src.stock_tracker.analyzer import run_analysis
from src.stock_tracker.engine import StockTrackerEngine
from src.stock_tracker.models import (
    AnalysisReport,
    TrackerConfig,
    normalize_a_share_code,
)
from src.stock_tracker.signals import list_detector_meta
from src.stock_tracker.store import TrackerStore
from src.stock_tracker.track_record import build_track_record, select_symbol_history

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Any]

_MAX_ANALYZE_SYMBOLS = 20

# How many of the model's most recent per-symbol analysis records are fed back
# by default when the analyze request does not specify ``history_limit``.
_DEFAULT_HISTORY_LIMIT = 5

# Calendar days of finalized history fetched alongside the live single-day bar so
# a quote can report the true 昨收. Tencent's daily kline only serves the current
# day when asked for that exact day, so a bare single-day window never contains
# the prior session's close.
_QUOTE_PREV_LOOKBACK_DAYS = 15

_REFRESH_LOCK = threading.Lock()
_REFRESH_OPERATION_LOCK = threading.Lock()
# Set when a ``force`` refresh arrives while another run is in flight; the active
# background worker performs one extra run to honour it rather than a second
# concurrent worker doing double network work.
_REFRESH_RERUN = False
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


# (message keywords, friendly hint). Keyword matching is case-insensitive and
# provider-agnostic so a swap of LANGCHAIN_PROVIDER keeps error mapping working.
_PROVIDER_ERROR_HINTS: List[tuple[tuple[str, ...], str]] = [
    (
        ("usage limit", "weekly", "7-day", "quota", "insufficient_quota", "balance", "余额", "额度"),
        "模型服务配额已用完：请稍后再试、购买/重置额度，或切换其他模型"
        "（agent/.env 的 LANGCHAIN_PROVIDER / LANGCHAIN_MODEL_NAME）。",
    ),
    (
        ("rate limit", "too many requests", "请求过于频繁", "429"),
        "模型服务请求过于频繁，请稍后再试。",
    ),
    (
        ("permission denied", "invalid api key", "unauthorized", "authentication", "鉴权", "密钥", "401", "403"),
        "模型服务鉴权失败：请检查 agent/.env 中对应模型的 API Key 配置。",
    ),
    (
        ("model not found", "no such model", "does not exist", "invalid model", "模型不存在", "404"),
        "找不到配置的模型：请检查 agent/.env 的 LANGCHAIN_MODEL_NAME。",
    ),
    (
        ("context length", "maximum context", "too many tokens", "上下文", "输入过长", "超长"),
        "输入超过模型上下文上限：请减少一次分析的标的数量后重试。",
    ),
    (
        ("connection", "timed out", "timeout", "connect", "网络", "连接失败"),
        "模型服务网络异常或超时，请稍后再试。",
    ),
]


def _provider_error_messages(exc: BaseException) -> List[str]:
    """Collect messages across an exception chain so nested provider errors surface."""
    messages: List[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    return messages


def _friendly_provider_error(exc: BaseException) -> Optional[str]:
    """Map a provider/LLM exception to a friendly Chinese hint, or None."""
    blob = "\n".join(_provider_error_messages(exc)).lower()
    for keywords, hint in _PROVIDER_ERROR_HINTS:
        if any(keyword in blob for keyword in keywords):
            return hint
    return None


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class TrackerSettingsRequest(BaseModel):
    """Update payload for tracker configuration."""

    watchlist: Optional[List[str]] = None
    periods: Optional[List[int]] = None
    signals: Optional[List[str]] = None
    thresholds: Optional[Dict[str, float]] = None
    refresh_interval_seconds: Optional[int] = Field(
        default=None,
        ge=5,
        description="Auto quote refresh interval in seconds.",
    )
    detail_card_count: Optional[int] = Field(
        default=None,
        ge=1,
        description="Number of detail cards to show (max three per row; extras wrap).",
    )


class TrackerConfigResponse(BaseModel):
    """Current tracker configuration."""

    watchlist: List[str]
    periods: List[int]
    signals: List[str]
    thresholds: Dict[str, float]
    refresh_interval_seconds: int
    detail_card_count: int


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
    user_prompt: Optional[str] = None
    # How many of the model's most recent per-symbol records to reference;
    # 0 disables history, None keeps the server default (``_DEFAULT_HISTORY_LIMIT``).
    history_limit: Optional[int] = Field(default=None, ge=0, le=30)


class TrackerAnalyzeResponse(BaseModel):
    """Envelope returned by the analysis endpoint."""

    status: str
    report: AnalysisReport
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
        # Merge as plain dicts so the reconstruct below re-runs Pydantic
        # coercion. ``model_copy(update=...)`` would skip validators and let
        # integral floats (e.g. atr_period=14.0 from the JSON request) leak
        # into int fields, producing serialization warnings later.
        kwargs["thresholds"] = {**current.thresholds.model_dump(), **request.thresholds}
    if request.refresh_interval_seconds is not None:
        kwargs["refresh_interval_seconds"] = request.refresh_interval_seconds
    if request.detail_card_count is not None:
        kwargs["detail_card_count"] = request.detail_card_count

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


class _QuoteItem(BaseModel):
    """One real-time quote for a single symbol."""

    code: str
    name: Optional[str] = None
    close: Optional[float] = None
    prev_close: Optional[float] = None
    daily_return: Optional[float] = None
    change_amount: Optional[float] = None
    date: Optional[str] = None  # bar trading date of ``close`` (YYYY-MM-DD)
    updated_at: Optional[str] = None
    error: Optional[str] = None


class _QuotesResponse(BaseModel):
    """Response envelope for the lightweight quotes endpoint."""

    status: str
    quotes: List[_QuoteItem]
    data_gaps: List[Dict[str, Any]]


def _quote_row_date(row: Dict[str, Any]) -> Optional[str]:
    """Extract a bar's trading date (``YYYY-MM-DD``) from a fetched row."""
    value = row.get("trade_date") or row.get("date") or row.get("datetime")
    if value is None:
        return None
    return str(value)[:10]


def _merge_quote_rows(
    history_rows: List[Dict[str, Any]],
    live_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge finalized history with live single-day bars, newest-last.

    The live bar wins when both sources cover the same trading date (it may be a
    fresher intraday quote). Rows without a parseable date are appended in order
    so unusual loaders/fixtures still behave like a plain record list.
    """
    by_date: Dict[str, Dict[str, Any]] = {}
    undated: List[Dict[str, Any]] = []
    for row in history_rows:
        key = _quote_row_date(row)
        if key is None:
            undated.append(row)
        else:
            by_date.setdefault(key, row)
    for row in live_rows:
        key = _quote_row_date(row)
        if key is None:
            undated.append(row)
        else:
            by_date[key] = row
    ordered = [by_date[key] for key in sorted(by_date)]
    ordered.extend(undated)
    return ordered


def _fetch_quotes(codes: List[str]) -> Dict[str, Any]:
    """Fetch the latest available price for each code via fetch_market_data.

    A quote must pair ``close`` with the previous session's close (昨收) of the
    *same* trading date. Tencent's daily kline only serves today's bar when the
    window is exactly that single day, so the live window alone never contains
    the prior session. A short finalized window is fetched alongside and the two
    are merged, letting the last two bars be read as (昨收, close). Per-symbol
    failures are recorded in ``data_gaps`` rather than raising.
    """
    today = date.today()
    today_str = today.isoformat()
    history_start = (today - timedelta(days=_QUOTE_PREV_LOOKBACK_DAYS)).isoformat()
    try:
        live = fetch_market_data(
            codes=codes,
            start_date=today_str,
            end_date=today_str,
            interval="1D",
            max_rows=2,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch tracker quotes")
        return {
            "status": "error",
            "quotes": [],
            "data_gaps": [{"code": "__batch__", "reason": f"fetch_failed: {exc}"}],
        }

    # Best-effort finalized series for the 昨收 basis; degrades to the live
    # window alone (the pre-fix behavior) when unavailable.
    try:
        history = fetch_market_data(
            codes=codes,
            start_date=history_start,
            end_date=today_str,
            interval="1D",
            max_rows=0,
        )
    except Exception:  # noqa: BLE001 — a failed history fetch must not drop live quotes
        logger.exception("Failed to fetch quote history; using live bars only")
        history = {}

    quotes: List[Dict[str, Any]] = []
    data_gaps: List[Dict[str, Any]] = []
    unresolved = set(live.get("_unresolved", [])) | set(history.get("_unresolved", []))
    updated_at = datetime.now().astimezone().isoformat()

    for code in codes:
        if code in unresolved:
            data_gaps.append({"code": code, "reason": "unresolved_symbol"})
            continue
        live_rows = live.get(code) or []
        if not isinstance(live_rows, list):
            live_rows = []
        history_rows = history.get(code) or []
        if not isinstance(history_rows, list):
            history_rows = []
        rows = _merge_quote_rows(history_rows, live_rows)
        if not rows:
            data_gaps.append({"code": code, "reason": "no_data"})
            continue

        try:
            latest = rows[-1]
            prev = rows[-2] if len(rows) > 1 else None
            close = float(latest["close"]) if latest.get("close") is not None else None
            prev_close = float(prev["close"]) if prev and prev.get("close") is not None else None
            daily_return = None
            change_amount = None
            if close is not None and prev_close is not None and prev_close != 0:
                change_amount = close - prev_close
                daily_return = change_amount / prev_close
            quotes.append(
                {
                    "code": code,
                    "name": latest.get("name"),
                    "close": close,
                    "prev_close": prev_close,
                    "daily_return": daily_return,
                    "change_amount": change_amount,
                    "date": _quote_row_date(latest),
                    "updated_at": updated_at,
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to parse quote for %s", code)
            data_gaps.append({"code": code, "reason": f"parse_error: {exc}"})

    return {"status": "ok", "quotes": quotes, "data_gaps": data_gaps}


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

    @app.get("/api/stock-tracker/signals")
    async def list_signals(principal=Depends(require_auth)) -> Dict[str, Any]:  # noqa: ARG001
        """Return metadata for all registered signal detectors."""
        return {
            "status": "ok",
            "signals": [meta.model_dump() for meta in list_detector_meta()],
        }

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

    @app.get("/api/stock-tracker/quotes", response_model=_QuotesResponse)
    async def get_quotes(principal=Depends(require_auth)) -> _QuotesResponse:  # noqa: ARG001
        """Return lightweight real-time quotes for the current watchlist."""
        settings = _get_store().get_settings()
        result = await asyncio.to_thread(_fetch_quotes, settings.config.watchlist)
        return _QuotesResponse(**result)

    @app.post("/api/stock-tracker/refresh")
    async def refresh_snapshot(
        request: RefreshRequest | None = None,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> Dict[str, Any]:
        """Kick off a snapshot refresh in the background and return immediately.

        The actual refresh runs on a daemon worker thread; clients poll
        ``/api/stock-tracker/refresh-status`` until ``running`` flips back to
        false. A second request while one is in flight answers ``refreshing``
        (or, with ``force``, queues a single re-run once the current one ends)
        instead of starting a duplicate refresh.
        """
        global _REFRESH_RERUN
        request = request or RefreshRequest()

        start_worker = False
        with _REFRESH_OPERATION_LOCK:
            if _REFRESH_STATE["running"]:
                if not request.force:
                    return {
                        "status": "refreshing",
                        "message": "A refresh is already in progress.",
                        "refresh": _refresh_snapshot(),
                    }
                # force: run again once the in-flight refresh finishes.
                _REFRESH_RERUN = True
            else:
                with _REFRESH_LOCK:
                    _REFRESH_STATE.update(
                        {
                            "running": True,
                            "current": None,
                            "symbols": {},
                            "error": None,
                        }
                    )
                start_worker = True

        if start_worker:
            threading.Thread(
                target=_run_refresh_background,
                name="stock-tracker-refresh",
                daemon=True,
            ).start()

        return {"status": "started", "refresh": _refresh_snapshot()}

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

        logger.info(
            "Analyze request: %d/%d symbol(s) selected, trading_date=%s",
            len(selected.symbols),
            len(request.symbols),
            snapshot.trading_date,
        )
        # Feed the model its recent conclusions per symbol (count is
        # user-configurable via history_limit; 0 opts out) so each run is an
        # incremental review, not a memory-less from-scratch re-analysis.
        history_limit = request.history_limit
        if history_limit is None:
            history_limit = _DEFAULT_HISTORY_LIMIT
        history = {}
        if history_limit > 0:
            history = select_symbol_history(
                store.list_analysis_envelopes(limit=200),
                {symbol.code: symbol.close for symbol in snapshot.symbols},
                codes=[symbol.code for symbol in selected.symbols],
                limit=history_limit,
            )
        try:
            report = await asyncio.to_thread(
                run_analysis,
                snapshot,
                selected.symbols,
                request.user_prompt,
                history,
            )
        except Exception as exc:  # surface provider/LLM failures as readable errors
            hint = _friendly_provider_error(exc)
            if hint is None:
                raise
            logger.warning("Analysis failed with provider error: %s", exc)
            raise HTTPException(status_code=502, detail=hint) from exc
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

    @app.get("/api/stock-tracker/analyze/track-record")
    async def get_track_record(principal=Depends(require_auth)) -> Dict[str, Any]:  # noqa: ARG001
        store = _get_store()
        analyses = store.list_analysis_envelopes(limit=200)
        snapshot = store.get_latest_snapshot()
        close_by_code = (
            {symbol.code: symbol.close for symbol in snapshot.symbols}
            if snapshot is not None
            else {}
        )
        items = build_track_record(analyses, close_by_code)
        return {
            "status": "ok",
            "items": [item.model_dump(mode="json") for item in items],
        }

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

    @app.delete("/api/stock-tracker/analyze/{analysis_id}")
    async def delete_analysis(
        analysis_id: str,
        principal=Depends(require_auth),  # noqa: ARG001
    ) -> Dict[str, Any]:
        deleted = _get_store().delete_analysis(analysis_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Analysis not found.")
        return {"status": "ok", "deleted": analysis_id}

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


def _run_refresh_background() -> None:
    """Run the snapshot refresh, honouring a queued forced re-run.

    Executes in a daemon worker thread so the HTTP handler never blocks the
    event loop on the multi-second Eastmoney fetches. The refresh is
    single-flight: a ``force`` request arriving mid-run is consumed here as one
    additional run rather than spawning a concurrent worker (which would double
    the network load). Failures are recorded into ``_REFRESH_STATE["error"]``
    and surfaced by ``refresh-status``.
    """
    global _REFRESH_RERUN
    while True:
        try:
            _refresh_snapshot_sync()
        except Exception as exc:  # noqa: BLE001 - surfaced via refresh-status
            logger.exception("Tracker refresh failed")
            with _REFRESH_LOCK:
                _REFRESH_STATE["error"] = f"{type(exc).__name__}: {exc}"
        with _REFRESH_OPERATION_LOCK:
            if not _REFRESH_RERUN:
                with _REFRESH_LOCK:
                    _REFRESH_STATE["running"] = False
                    _REFRESH_STATE["current"] = None
                return
            _REFRESH_RERUN = False
            # Keep ``running`` True and loop once more for the forced re-run.


def _refresh_snapshot() -> Dict[str, Any]:
    """Return a thread-safe copy of the current refresh state."""
    with _REFRESH_LOCK:
        return deepcopy(_REFRESH_STATE)


__all__ = ["register_stock_tracker_routes"]
