"""Capital data loader for the stock tracker.

This module批量拉取个股资金流向与融资融券数据，提供按交易日缓存和
per-symbol 错误隔离，返回结构化的 ``CapitalMetrics``。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

from src.stock_tracker._convert import (
    dashed_date as _dashed_date,
    to_float as _to_float,
    to_float_div as _to_float_div,
)
from src.stock_tracker.models import (
    CapitalMetrics,
    FundFlowHistoryItem,
    FundFlowSnapshot,
    MarginHistoryItem,
    MarginSnapshot,
)
from src.tools.fund_flow_tool import fetch_symbol_fund_flow
from src.tools.margin_trading_tool import fetch_symbol_margin_trading

logger = logging.getLogger(__name__)

_DEFAULT_DAYS = 60
_FUND_FLOW_LOOKBACK = 30
_CACHE_TTL_SECONDS = 30 * 60

# Delay between per-symbol HTTP requests in batch loaders. A small positive
# value reduces the chance that a proxy/edge gateway closes connections under
# bursty concurrent short-lived HTTPS requests.
_REQUEST_DELAY_SECONDS = 0.15


class _CachedCapitalEntry:
    __slots__ = ("metrics", "expires_at")

    def __init__(self, metrics: CapitalMetrics, expires_at: float) -> None:
        self.metrics = metrics
        self.expires_at = expires_at


class CapitalDataCache:
    """TTL cache for per-symbol capital data keyed by ``(code, trading_date)``."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, _CachedCapitalEntry] = {}
        self._lock = threading.Lock()

    def _key(self, namespace: str, code: str, trading_date: date) -> str:
        return f"{namespace}:{code}:{trading_date.isoformat()}"

    def get(
        self,
        namespace: str,
        code: str,
        trading_date: date,
    ) -> Optional[CapitalMetrics]:
        """Return cached metrics for a namespace if present and not expired."""
        key = self._key(namespace, code, trading_date)
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or entry.expires_at < now:
                self._cache.pop(key, None)
                return None
            return entry.metrics

    def set(
        self,
        namespace: str,
        code: str,
        trading_date: date,
        metrics: CapitalMetrics,
    ) -> None:
        """Store metrics for a namespace with TTL."""
        key = self._key(namespace, code, trading_date)
        expires_at = time.monotonic() + self._ttl_seconds
        with self._lock:
            self._cache[key] = _CachedCapitalEntry(metrics, expires_at)

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()


def _parse_fund_flow_rows(rows: List[Dict[str, Any]]) -> FundFlowSnapshot:
    """Build a ``FundFlowSnapshot`` from Eastmoney/Tushare daily fund-flow rows.

    The input rows are normalised by ``fetch_symbol_fund_flow`` and contain at
    least ``timestamp``, ``main``, ``super_large``, ``large``, ``medium`` and
    ``small`` net-inflow buckets. They are sorted ascending by date before the
    latest snapshot and 5-day cumulative main-net values are computed.
    """
    if not rows:
        return FundFlowSnapshot()

    def _row_date(row: Dict[str, Any]) -> Optional[date]:
        ts = row.get("timestamp")
        if not isinstance(ts, str) or not ts:
            return None
        # Accept "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS".
        return _dashed_date(ts[:10])

    parsed: List[FundFlowHistoryItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade_date = _row_date(row)
        parsed.append(
            FundFlowHistoryItem(
                trade_date=trade_date,
                main_net=_to_float(row.get("main")),
                super_large_net=_to_float(row.get("super_large")),
                large_net=_to_float(row.get("large")),
                medium_net=_to_float(row.get("medium")),
                small_net=_to_float(row.get("small")),
            )
        )

    # Keep only rows with a parseable date and sort ascending.
    parsed = [item for item in parsed if item.trade_date is not None]
    parsed.sort(key=lambda item: item.trade_date)

    if not parsed:
        return FundFlowSnapshot(history=parsed)

    latest = parsed[-1]
    main_5d_net: Optional[float] = None
    if len(parsed) >= 5:
        main_values = [item.main_net for item in parsed[-5:]]
        if all(v is not None for v in main_values):
            main_5d_net = sum(main_values)  # type: ignore[arg-type]

    main_net_ratio: Optional[float] = None
    if latest.main_net is not None and latest.trade_date is not None:
        latest_row: Optional[Dict[str, Any]] = None
        for row in rows:
            if isinstance(row, dict) and _row_date(row) == latest.trade_date:
                latest_row = row
                break
        if latest_row is not None:
            turnover = _to_float_div(latest_row.get("turnover"))
            if turnover is not None:
                main_net_ratio = latest.main_net / turnover

    return FundFlowSnapshot(
        trade_date=latest.trade_date,
        main_net=latest.main_net,
        main_net_ratio=main_net_ratio,
        main_5d_net=main_5d_net,
        super_large_net=latest.super_large_net,
        large_net=latest.large_net,
        medium_net=latest.medium_net,
        small_net=latest.small_net,
        history=parsed,
    )


def _fetch_one_fund_flow(code: str, days: int) -> Dict[str, Any]:
    """Fetch fund-flow data for one symbol; never raises."""
    try:
        result = fetch_symbol_fund_flow(code, period="daily", days=days)
        if "error" in result:
            return {
                "code": code,
                "metrics": CapitalMetrics(
                    fund_flow_error=str(result["error"]),
                    fund_flow_source=result.get("source", "unavailable"),
                ),
            }
        rows = result.get("rows", [])
        source = result.get("source", "eastmoney")
        return {
            "code": code,
            "metrics": CapitalMetrics(
                fund_flow=_parse_fund_flow_rows(rows),
                fund_flow_source=source,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fund flow fetch failed for %s: %s", code, exc)
        return {
            "code": code,
            "metrics": CapitalMetrics(
                fund_flow_error=str(exc),
                fund_flow_source="unavailable",
            ),
        }


def fetch_fund_flow_batch(
    codes: List[str],
    *,
    days: int = _FUND_FLOW_LOOKBACK,
    cache: Optional[CapitalDataCache] = None,
    trading_date: Optional[date] = None,
) -> Dict[str, CapitalMetrics]:
    """Fetch fund-flow metrics for ``codes`` with caching and per-symbol isolation.

    Returns a dict mapping each code to ``CapitalMetrics`` (possibly with
    ``fund_flow_error`` populated).
    """
    if trading_date is None:
        trading_date = date.today()
    cache = cache or CapitalDataCache()
    results: Dict[str, CapitalMetrics] = {}
    pending: List[str] = []

    for code in codes:
        cached = cache.get("fund_flow", code, trading_date)
        if cached is not None:
            results[code] = cached
        else:
            pending.append(code)

    for index, code in enumerate(pending):
        if index > 0:
            time.sleep(_REQUEST_DELAY_SECONDS)
        item = _fetch_one_fund_flow(code, max(days, 2))
        metrics = item["metrics"]
        results[code] = metrics
        if metrics.fund_flow_error is None:
            cache.set("fund_flow", code, trading_date, metrics)

    return results


def _parse_margin_rows(rows: List[Dict[str, Any]]) -> MarginSnapshot:
    """Build a ``MarginSnapshot`` from most-recent-first margin rows."""
    if not rows:
        return MarginSnapshot()

    history = [
        MarginHistoryItem(
            trade_date=_dashed_date(row.get("trade_date")),
            financing_balance=_to_float(row.get("financing_balance")),
            margin_total_balance=_to_float(row.get("margin_total_balance")),
        )
        for row in rows
        if isinstance(row, dict)
    ]

    latest = rows[0]
    trade_date = _dashed_date(latest.get("trade_date"))
    financing_balance = _to_float(latest.get("financing_balance"))
    margin_total_balance = _to_float(latest.get("margin_total_balance"))

    financing_change: Optional[float] = None
    margin_total_change: Optional[float] = None
    if len(rows) >= 2:
        prev = rows[1]
        prev_financing = _to_float(prev.get("financing_balance"))
        prev_margin_total = _to_float(prev.get("margin_total_balance"))
        if financing_balance is not None and prev_financing is not None:
            financing_change = financing_balance - prev_financing
        if margin_total_balance is not None and prev_margin_total is not None:
            margin_total_change = margin_total_balance - prev_margin_total

    return MarginSnapshot(
        trade_date=trade_date,
        financing_balance=financing_balance,
        financing_balance_change=financing_change,
        margin_total_balance=margin_total_balance,
        margin_total_change=margin_total_change,
        history=history,
    )


def _fetch_one_margin(code: str, days: int) -> Dict[str, Any]:
    """Fetch margin trading for one symbol; never raises."""
    try:
        result = fetch_symbol_margin_trading(code, days=days)
        if "error" in result:
            return {
                "code": code,
                "metrics": CapitalMetrics(
                    margin_error=str(result["error"]),
                    margin_source=result.get("source", "unavailable"),
                ),
            }
        rows = result.get("rows", [])
        source = result.get("source", "eastmoney")
        return {
            "code": code,
            "metrics": CapitalMetrics(
                margin=_parse_margin_rows(rows),
                margin_source=source,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Margin trading fetch failed for %s: %s", code, exc)
        return {
            "code": code,
            "metrics": CapitalMetrics(
                margin_error=str(exc),
                margin_source="unavailable",
            ),
        }


def fetch_margin_trading_batch(
    codes: List[str],
    *,
    days: int = _DEFAULT_DAYS,
    cache: Optional[CapitalDataCache] = None,
    trading_date: Optional[date] = None,
) -> Dict[str, CapitalMetrics]:
    """Fetch margin-trading metrics for ``codes`` with caching and per-symbol isolation.

    Returns a dict mapping each code to ``CapitalMetrics`` (possibly with
    ``margin_error`` populated).
    """
    if trading_date is None:
        trading_date = date.today()
    cache = cache or CapitalDataCache()
    results: Dict[str, CapitalMetrics] = {}
    pending: List[str] = []

    for code in codes:
        cached = cache.get("margin", code, trading_date)
        if cached is not None:
            results[code] = cached
        else:
            pending.append(code)

    for index, code in enumerate(pending):
        if index > 0:
            time.sleep(_REQUEST_DELAY_SECONDS)
        item = _fetch_one_margin(code, max(days, 2))
        metrics = item["metrics"]
        results[code] = metrics
        if metrics.margin_error is None:
            cache.set("margin", code, trading_date, metrics)

    return results


def _merge_capital_metrics(
    fund_metrics: CapitalMetrics,
    margin_metrics: CapitalMetrics,
) -> CapitalMetrics:
    """Merge fund-flow and margin-trading metrics into one ``CapitalMetrics``.

    Errors and source fields from both inputs are preserved. If one side failed,
    the other's snapshot still appears in the result.
    """
    return CapitalMetrics(
        fund_flow=fund_metrics.fund_flow,
        margin=margin_metrics.margin,
        fund_flow_source=fund_metrics.fund_flow_source,
        margin_source=margin_metrics.margin_source,
        fund_flow_error=fund_metrics.fund_flow_error,
        margin_error=margin_metrics.margin_error,
    )


def load_capital_data(
    codes: List[str],
    *,
    end_date: Optional[date] = None,
    days: int = _DEFAULT_DAYS,
    cache: Optional[CapitalDataCache] = None,
) -> Dict[str, CapitalMetrics]:
    """Load fund-flow and margin-trading metrics for ``codes``.

    Args:
        codes: A-share symbols like ``["600519.SH", "000001.SZ"]``.
        end_date: Trading date used for cache keys. Defaults to today.
        days: Number of historical days to fetch for margin data. Fund flow
            always uses its own lookback to satisfy the 20-day spike detector.
        cache: Shared cache instance; a fresh one is created if omitted.

    Returns:
        Dict mapping symbol to ``CapitalMetrics``. Missing or failed symbols
        are still present with the relevant ``*_error`` populated.
    """
    if end_date is None:
        end_date = date.today()
    cache = cache or CapitalDataCache()

    fund_results = fetch_fund_flow_batch(
        codes,
        days=max(_FUND_FLOW_LOOKBACK, 2),
        cache=cache,
        trading_date=end_date,
    )
    margin_results = fetch_margin_trading_batch(
        codes,
        days=max(days, 2),
        cache=cache,
        trading_date=end_date,
    )

    merged: Dict[str, CapitalMetrics] = {}
    for code in codes:
        fund_metrics = fund_results.get(code)
        if fund_metrics is None:
            fund_metrics = CapitalMetrics(fund_flow_error="missing from batch")
        margin_metrics = margin_results.get(code)
        if margin_metrics is None:
            margin_metrics = CapitalMetrics(margin_error="missing from batch")
        merged[code] = _merge_capital_metrics(fund_metrics, margin_metrics)

    return merged


__all__ = [
    "CapitalDataCache",
    "fetch_fund_flow_batch",
    "fetch_margin_trading_batch",
    "load_capital_data",
]
