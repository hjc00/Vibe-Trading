"""Margin-trading data loader for the stock tracker.

This module批量拉取个股融资融券数据，提供按交易日缓存和
per-symbol 错误隔离，返回结构化的 ``CapitalMetrics``。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

from src.stock_tracker.models import CapitalMetrics, MarginHistoryItem, MarginSnapshot
from src.tools.margin_trading_tool import fetch_symbol_margin_trading

logger = logging.getLogger(__name__)

_DEFAULT_DAYS = 60
_CACHE_TTL_SECONDS = 30 * 60


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


def _dashed_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    for code in pending:
        item = _fetch_one_margin(code, max(days, 2))
        metrics = item["metrics"]
        results[code] = metrics
        if metrics.margin_error is None:
            cache.set("margin", code, trading_date, metrics)

    return results


def load_capital_data(
    codes: List[str],
    *,
    end_date: Optional[date] = None,
    days: int = _DEFAULT_DAYS,
    cache: Optional[CapitalDataCache] = None,
) -> Dict[str, CapitalMetrics]:
    """Load combined fund-flow and margin-trading metrics for ``codes``.

    Args:
        codes: A-share symbols like ``["600519.SH", "000001.SZ"]``.
        end_date: Trading date used for cache keys. Defaults to today.
        days: Number of historical days to fetch.
        cache: Shared cache instance; a fresh one is created if omitted.

    Returns:
        Dict mapping symbol to ``CapitalMetrics``. Missing or failed symbols
        are still present with error fields populated.
    """
    if end_date is None:
        end_date = date.today()
    if cache is None:
        cache = CapitalDataCache()

    fund_results = fetch_fund_flow_batch(codes, days=days, cache=cache, trading_date=end_date)
    margin_results = fetch_margin_trading_batch(
        codes,
        days=max(days, _FUND_FLOW_LOOKBACK),
        cache=cache,
        trading_date=end_date,
    )

    combined: Dict[str, CapitalMetrics] = {}
    for code in codes:
        fund = fund_results.get(
            code, CapitalMetrics(fund_flow_error="not fetched", fund_flow_source="unavailable")
        )
        margin = margin_results.get(
            code, CapitalMetrics(margin_error="not fetched", margin_source="unavailable")
        )
        combined[code] = _merge_metrics(fund, margin)

    return combined


__all__ = [
    "CapitalDataCache",
    "fetch_fund_flow_batch",
    "fetch_margin_trading_batch",
    "load_capital_data",
]
