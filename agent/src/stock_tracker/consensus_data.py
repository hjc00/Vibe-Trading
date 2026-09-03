"""Sell-side consensus / earnings-expectation loader (盈利预期/一致预期).

聚合东财研报（``reportapi``，零 token）与东财 datacenter 一致预期 EPS，产出
``ConsensusSnapshot``，供「盈利预期/一致预期」卡片使用。目标价与 EPS 上/下修
走 Tushare ``report_rc`` 兜底（120 积分试用、每天 10 次），token 缺失或积分不足
时静默降级。

设计原则（与 :mod:`src.stock_tracker.valuation_data` 一致）：
- 纯函数（无网络）与编排分离：``compute_rating_score`` / ``compute_forward_metrics``
  可独立单测。
- 主源东财研报（零 token）保证 ``rating_distribution`` / ``analyst_count`` /
  ``consensus_eps_cur/next`` 可用；目标价 / EPS 修正为可选兜底。
- ``forward_pe`` / ``upside_pct`` 依赖最新收盘价，由
  :func:`compute_forward_metrics` 在 engine 拿到 ``close`` 后回填，避免 loader
  额外拉行情。
- ``load_consensus_data`` 永不抛异常，逐 symbol 隔离 error。
- 低频缓存：``ConsensusDataCache`` TTL 1 天，与日频 ``ValuationDataCache``
  （30 min）分离。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from statistics import mean
from typing import Any, Dict, List, Optional

from src.stock_tracker.models import ConsensusSnapshot
from src.tools import tushare_fallbacks
from src.tools.research_reports_tool import fetch_research_reports_data

logger = logging.getLogger(__name__)

# Consensus is daily-to-weekly; a 1-day TTL avoids re-hitting the throttled
# Eastmoney report feed and the points-gated Tushare report_rc endpoint.
_CACHE_TTL_SECONDS = 24 * 60 * 60
# Delay between per-symbol HTTP requests in the batch loader.
_REQUEST_DELAY_SECONDS = 0.15
# Cap on reports used for analyst-count / rating distribution.
_REPORT_LIMIT = 50


def _clamp100(value: float) -> float:
    """Clamp a 0-100 value into range."""
    return min(max(value, 0.0), 100.0)


def _to_float(value: Any) -> Optional[float]:
    """Coerce a cell to ``float``, or ``None`` when absent/non-numeric."""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean_non_none(values: List[Optional[float]]) -> Optional[float]:
    """Mean of present (non-None) values, or ``None`` when all are missing."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(float(mean(present)), 4)


def _rating_to_score(label: Any) -> float:
    """Map a rating label to a 0-100 tone score (100 = most bullish)."""
    text = str(label).strip().lower()
    bullish = ("买入", "增持", "推荐", "强烈", "buy", "overweight", "outperform", "跑赢", "优于")
    bearish = ("减持", "卖出", "回避", "sell", "underweight", "underperform", "跑输", "低于")
    neutral = ("中性", "持有", "观望", "neutral", "hold", "equalweight", "持平")
    if any(k in text for k in bullish):
        return 100.0
    if any(k in text for k in bearish):
        return 0.0
    if any(k in text for k in neutral):
        return 50.0
    return 50.0  # unknown -> neutral


def compute_rating_score(rating_distribution: Dict[str, int]) -> Optional[float]:
    """Score the rating mix 0-100 (weighted mean of per-rating tones).

    Returns ``None`` when the distribution is empty or all counts are <= 0.
    """
    if not rating_distribution:
        return None
    weighted = 0.0
    total = 0
    for rating, count in rating_distribution.items():
        try:
            c = int(count)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        weighted += _rating_to_score(rating) * c
        total += c
    if not total:
        return None
    return round(weighted / total, 2)


def compute_forward_metrics(consensus: ConsensusSnapshot, close: Optional[float]) -> None:
    """Fill ``forward_pe`` / ``upside_pct`` from the latest close, in place.

    ``forward_pe = close / consensus_eps_next``; ``upside_pct =
    target_price_avg / close - 1`` (fraction). No-ops when ``close`` or the
    required consensus field is missing.
    """
    if not close:
        return
    if consensus.consensus_eps_next:
        consensus.forward_pe = round(close / consensus.consensus_eps_next, 2)
    if consensus.target_price_avg:
        consensus.upside_pct = round(consensus.target_price_avg / close - 1, 4)


class _CachedConsensusEntry:
    __slots__ = ("snapshot", "expires_at")

    def __init__(self, snapshot: ConsensusSnapshot, expires_at: float) -> None:
        self.snapshot = snapshot
        self.expires_at = expires_at


class ConsensusDataCache:
    """TTL cache for per-symbol consensus keyed by ``(code, trading_date)``."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, _CachedConsensusEntry] = {}
        self._lock = threading.Lock()

    def get(self, code: str, trading_date: date) -> Optional[ConsensusSnapshot]:
        key = f"{code}:{trading_date.isoformat()}"
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or entry.expires_at < now:
                self._cache.pop(key, None)
                return None
            return entry.snapshot

    def set(self, code: str, trading_date: date, snapshot: ConsensusSnapshot) -> None:
        key = f"{code}:{trading_date.isoformat()}"
        expires_at = time.monotonic() + self._ttl_seconds
        with self._lock:
            self._cache[key] = _CachedConsensusEntry(snapshot, expires_at)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def _fetch_report_rc(code: str) -> List[Dict[str, Any]]:
    """Best-effort Tushare ``report_rc`` rows for target price / EPS revision."""
    try:
        data = tushare_fallbacks.fetch_report_rc(code, lookback_days=400)
    except tushare_fallbacks.TushareFallbackUnavailable:
        return []
    except Exception as exc:  # noqa: BLE001 - optional dimension
        logger.warning("report_rc fetch failed for %s: %s", code, exc)
        return []
    return data.get("rows", [])


def _apply_report_rc(snapshot: ConsensusSnapshot, rows: List[Dict[str, Any]]) -> None:
    """Fill target-price bounds / EPS revision from ``report_rc`` rows."""
    highs = [r["max_price"] for r in rows if _to_float(r.get("max_price")) is not None]
    lows = [r["min_price"] for r in rows if _to_float(r.get("min_price")) is not None]
    mids = [
        (_to_float(r.get("max_price")) + _to_float(r.get("min_price"))) / 2
        for r in rows
        if _to_float(r.get("max_price")) is not None and _to_float(r.get("min_price")) is not None
    ]
    if highs:
        snapshot.target_price_high = round(max(highs), 2)
    if lows:
        snapshot.target_price_low = round(min(lows), 2)
    if mids:
        snapshot.target_price_avg = round(float(mean(mids)), 2)
    snapshot.eps_revision_pct = _compute_eps_revision(rows)


def _compute_eps_revision(rows: List[Dict[str, Any]]) -> Optional[float]:
    """Mean latest-vs-prior EPS revision (%) across brokers with >= 2 forecasts.

    Groups ``report_rc`` rows by broker, takes the two most-recent EPS forecasts,
    and averages their percent change. Returns ``None`` when no broker has two.
    """
    by_org: Dict[str, List[tuple]] = {}
    for row in rows:
        org = row.get("org_name")
        eps = _to_float(row.get("eps"))
        if not org or eps is None:
            continue
        by_org.setdefault(str(org), []).append((row.get("report_date"), eps))

    changes: List[float] = []
    for entries in by_org.values():
        entries.sort(key=lambda item: item[0] or "")
        if len(entries) < 2:
            continue
        prev_eps = entries[-2][1]
        latest_eps = entries[-1][1]
        if prev_eps:
            changes.append((latest_eps - prev_eps) / prev_eps)
    if not changes:
        return None
    return round(float(mean(changes)) * 100, 4)


def _fetch_one_consensus(code: str) -> ConsensusSnapshot:
    """Build one symbol's consensus snapshot, isolating every source failure."""
    snapshot = ConsensusSnapshot()

    data = fetch_research_reports_data(code, limit=_REPORT_LIMIT)
    reports = data.get("reports", [])
    consensus_eps = data.get("consensus_eps", [])

    if not reports and not consensus_eps:
        snapshot.error = "no analyst coverage"
        return snapshot

    # Rating distribution + analyst count from the Eastmoney report feed.
    rating_dist: Dict[str, int] = {}
    for report in reports:
        rating = report.get("rating")
        if rating:
            rating_dist[str(rating)] = rating_dist.get(str(rating), 0) + 1
    snapshot.rating_distribution = rating_dist
    snapshot.rating_score = compute_rating_score(rating_dist)
    snapshot.analyst_count = len(reports) or None

    # Consensus EPS: prefer the brokers' own this/next-year forecasts, then the
    # Eastmoney datacenter per-year consensus fallback.
    this_year = _mean_non_none(
        [report.get("eps_forecast", {}).get("this_year") for report in reports]
    )
    next_year = _mean_non_none(
        [report.get("eps_forecast", {}).get("next_year") for report in reports]
    )
    if this_year is None or next_year is None:
        eps_by_year = [
            (e.get("fiscal_year"), e.get("consensus_eps"))
            for e in consensus_eps
            if _to_float(e.get("consensus_eps")) is not None
        ]
        eps_by_year.sort(key=lambda item: str(item[0]) or "")
        em_values = [e[1] for e in eps_by_year]
        if this_year is None and em_values:
            this_year = em_values[0]
        if next_year is None and len(em_values) >= 2:
            next_year = em_values[1]
    snapshot.consensus_eps_cur = this_year
    snapshot.consensus_eps_next = next_year
    snapshot.source = "eastmoney"

    # Optional Tushare report_rc fallback for target prices / EPS revision.
    rc_rows = _fetch_report_rc(code)
    if rc_rows:
        _apply_report_rc(snapshot, rc_rows)
    return snapshot


def load_consensus_data(
    codes: List[str],
    *,
    end_date: Optional[date] = None,
    cache: Optional[ConsensusDataCache] = None,
) -> Dict[str, ConsensusSnapshot]:
    """Load consensus estimates for ``codes`` with caching and isolation.

    Returns a dict mapping each code to a ``ConsensusSnapshot``. Missing or
    failed symbols are still present with ``error`` populated. Never raises.
    """
    if end_date is None:
        end_date = date.today()
    cache = cache or ConsensusDataCache()
    results: Dict[str, ConsensusSnapshot] = {}
    pending: List[str] = []

    for code in codes:
        cached = cache.get(code, end_date)
        if cached is not None:
            results[code] = cached
        else:
            pending.append(code)

    for index, code in enumerate(pending):
        if index > 0:
            time.sleep(_REQUEST_DELAY_SECONDS)
        snapshot = _fetch_one_consensus(code)
        results[code] = snapshot
        if snapshot.error is None:
            cache.set(code, end_date, snapshot)

    return results


__all__ = [
    "ConsensusDataCache",
    "compute_forward_metrics",
    "compute_rating_score",
    "load_consensus_data",
]
