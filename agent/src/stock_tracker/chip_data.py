"""Chip-concentration / institutional-movement loader for the stock tracker.

聚合个股「股东户数」变化（东财 ``RPT_HOLDERNUMLATEST``）、北向持股占比
（Tushare ``hk_hold``，季度口径）与公募持仓占比（Tushare ``fund_portfolio``，
季度），产出 ``ChipSnapshot``，供「筹码集中度」卡片使用。

设计原则（与 :mod:`src.stock_tracker.valuation_data` 一致）：
- 纯函数（无网络）与编排函数分离：``compute_chip_concentration_score`` /
  ``compute_holder_trend`` 可独立单测。
- 股东户数为主源（东财，零 token）；北向/公募为可选 Tushare 兜底，token
  缺失或积分不足时静默降级（``TushareFallbackUnavailable`` → ``None``）。
- ``load_chip_data`` 永不抛异常：逐 symbol 隔离 error。
- 低频缓存：``ChipDataCache`` TTL 7 天，与日频 ``ValuationDataCache``
  （30 min）分离，避免撞东财/Tushare 节流与积分墙。

字段口径：
- ``holder_count_change_pct`` 为股东户数环比百分数（负=户数下降=吸筹）。
- ``avg_hold_amount`` 为户均持股市值（元）。
- 北向/公募占比均为百分数（如 1.57 表示 1.57%）。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

from src.stock_tracker._convert import dashed_date, to_float
from src.stock_tracker.models import ChipHolderItem, ChipSnapshot
from src.tools import tushare_fallbacks
from src.tools.shareholder_count_tool import fetch_shareholder_count

logger = logging.getLogger(__name__)

# Low-frequency cache TTL: shareholder count / fund holdings are quarterly, and
# northbound is quarterly since 2024-08, so a 7-day TTL avoids re-hitting the
# throttled/points-gated Tushare endpoints while staying fresh enough.
_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
# Delay between per-symbol HTTP requests in the batch loader.
_REQUEST_DELAY_SECONDS = 0.15

# Chip concentration score weights. Three explainable dimensions; missing
# dimensions are dropped and the remaining weights renormalized (mirrors
# ``_QUALITY_WEIGHTS`` in valuation_data.py).
_CHIP_WEIGHTS = {
    "holder": 0.40,          # 股东户数下降（吸筹）
    "avg_hold": 0.30,        # 户均持股上升（集中）
    "northbound_fund": 0.30,  # 北向/公募增持
}


def _clamp01(value: float) -> float:
    """Clamp a 0-1 fraction into range."""
    return min(max(value, 0.0), 1.0)


def _sub_holder(change_pct: float) -> float:
    """Holder-count change sub-score 0-100: linear over -15%..0% (下降=吸筹)."""
    return round(_clamp01(-change_pct / 15.0) * 100, 2)


def _sub_avg_hold(change_pct: float) -> float:
    """Avg-holding change sub-score 0-100: linear over 0%..+30%."""
    return round(_clamp01(change_pct / 30.0) * 100, 2)


def _sub_northbound_fund(change_pct: float) -> float:
    """Northbound/fund change sub-score 0-100: linear over 0%..+20%."""
    return round(_clamp01(change_pct / 20.0) * 100, 2)


def compute_chip_concentration_score(
    holder_change_pct: Optional[float],
    avg_hold_change_pct: Optional[float],
    northbound_fund_change_pct: Optional[float],
) -> Optional[float]:
    """Score chip concentration 0-100 from holder/avg-holding/institution changes.

    ``holder_change_pct`` is the holder-count QoQ percent (negative = fewer
    holders = accumulation = good); ``avg_hold_change_pct`` and
    ``northbound_fund_change_pct`` are percent changes where positive is good.
    Missing dimensions are dropped and the remaining weights renormalized;
    returns ``None`` when no dimension is available.
    """
    subs: Dict[str, float] = {}
    if holder_change_pct is not None:
        subs["holder"] = _sub_holder(holder_change_pct)
    if avg_hold_change_pct is not None:
        subs["avg_hold"] = _sub_avg_hold(avg_hold_change_pct)
    if northbound_fund_change_pct is not None:
        subs["northbound_fund"] = _sub_northbound_fund(northbound_fund_change_pct)
    if not subs:
        return None
    total_weight = sum(_CHIP_WEIGHTS[name] for name in subs)
    if total_weight <= 0:
        return None
    score = sum(_CHIP_WEIGHTS[name] * value for name, value in subs.items())
    return round(score / total_weight, 2)


def compute_holder_trend(holder_counts: List[Optional[float]]) -> Optional[str]:
    """Classify holder-count trend over the trailing periods.

    Two consecutive period-over-period declines mark ``"accumulating"`` (吸筹),
    two consecutive rises mark ``"distributing"`` (派发); otherwise ``None``.

    Args:
        holder_counts: Holder counts in chronological order (oldest first).

    Returns:
        ``"accumulating"``, ``"distributing"``, or ``None`` when fewer than
        three usable observations or no two-period trend.
    """
    counts = [c for c in holder_counts if c is not None]
    if len(counts) < 3:
        return None
    recent = counts[-1] - counts[-2]
    prior = counts[-2] - counts[-3]
    if recent < 0 and prior < 0:
        return "accumulating"
    if recent > 0 and prior > 0:
        return "distributing"
    return None


class _CachedChipEntry:
    __slots__ = ("snapshot", "expires_at")

    def __init__(self, snapshot: ChipSnapshot, expires_at: float) -> None:
        self.snapshot = snapshot
        self.expires_at = expires_at


class ChipDataCache:
    """TTL cache for per-symbol chip data keyed by ``(code, trading_date)``."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, _CachedChipEntry] = {}
        self._lock = threading.Lock()

    def get(self, code: str, trading_date: date) -> Optional[ChipSnapshot]:
        key = f"{code}:{trading_date.isoformat()}"
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or entry.expires_at < now:
                self._cache.pop(key, None)
                return None
            return entry.snapshot

    def set(self, code: str, trading_date: date, snapshot: ChipSnapshot) -> None:
        key = f"{code}:{trading_date.isoformat()}"
        expires_at = time.monotonic() + self._ttl_seconds
        with self._lock:
            self._cache[key] = _CachedChipEntry(snapshot, expires_at)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def _fetch_northbound(code: str) -> Optional[Dict[str, Any]]:
    """Best-effort northbound holding ratio + QoQ change via Tushare ``hk_hold``."""
    try:
        data = tushare_fallbacks.fetch_hk_hold(code, lookback_days=400)
    except tushare_fallbacks.TushareFallbackUnavailable:
        return None
    except Exception as exc:  # noqa: BLE001 - optional dimension
        logger.warning("hk_hold fetch failed for %s: %s", code, exc)
        return None
    rows = data.get("rows", [])
    if not rows:
        return None
    latest = rows[-1]
    ratio = to_float(latest.get("ratio"))
    change_pct = None
    if len(rows) >= 2:
        prev = to_float(rows[-2].get("ratio"))
        if ratio is not None and prev:
            change_pct = round((ratio / prev - 1) * 100, 4)
    return {"ratio": ratio, "change_pct": change_pct}


def _fetch_fund(code: str) -> Optional[Dict[str, Any]]:
    """Best-effort mutual-fund holding ratio + change via Tushare ``fund_portfolio``.

    Aggregates ``stk_mkv_ratio`` across funds for the latest report period and
    compares it to the prior period.
    """
    try:
        data = tushare_fallbacks.fetch_fund_portfolio(code, lookback_days=400)
    except tushare_fallbacks.TushareFallbackUnavailable:
        return None
    except Exception as exc:  # noqa: BLE001 - optional dimension
        logger.warning("fund_portfolio fetch failed for %s: %s", code, exc)
        return None
    rows = data.get("rows", [])
    if not rows:
        return None

    ends = sorted({r.get("end_date") for r in rows if r.get("end_date")})
    if not ends:
        return None

    def _sum_ratio(end: str) -> Optional[float]:
        values = [
            to_float(r.get("stk_mkv_ratio"))
            for r in rows
            if r.get("end_date") == end and to_float(r.get("stk_mkv_ratio")) is not None
        ]
        return round(sum(values), 4) if values else None

    latest = ends[-1]
    ratio = _sum_ratio(latest)
    change_pct = None
    if len(ends) >= 2:
        prev = _sum_ratio(ends[-2])
        if ratio is not None and prev:
            change_pct = round((ratio / prev - 1) * 100, 4)
    return {"ratio": ratio, "change_pct": change_pct}


def _fetch_one_chip(code: str) -> ChipSnapshot:
    """Build one symbol's chip snapshot, isolating every source failure."""
    snapshot = ChipSnapshot()

    periods = fetch_shareholder_count(code)  # newest-first
    if not periods:
        snapshot.error = "no shareholder-count disclosure"
        return snapshot

    # Reverse to chronological (oldest first) for history and trend.
    history: List[ChipHolderItem] = []
    for p in reversed(periods):
        history.append(
            ChipHolderItem(
                end_date=dashed_date(p.get("end_date")),
                holder_count=to_float(p.get("holder_count")),
                holder_count_change_pct=to_float(p.get("holder_count_change_pct")),
                avg_hold_amount=to_float(p.get("avg_hold_amount")),
            )
        )
    snapshot.holder_history = history
    snapshot.holder_count = history[-1].holder_count if history else None
    snapshot.holder_count_change_pct = (
        history[-1].holder_count_change_pct if history else None
    )
    snapshot.avg_hold_amount = history[-1].avg_hold_amount if history else None
    snapshot.source = "eastmoney"

    # Derived avg-holding QoQ percent change between the two latest periods.
    avg_hold_change_pct = None
    if len(history) >= 2:
        a0 = history[-1].avg_hold_amount
        a1 = history[-2].avg_hold_amount
        if a0 is not None and a1:
            avg_hold_change_pct = round((a0 / a1 - 1) * 100, 4)

    northbound = _fetch_northbound(code)
    fund = _fetch_fund(code)
    if northbound and northbound.get("ratio") is not None:
        snapshot.northbound_holding_ratio = northbound["ratio"]
    if fund and fund.get("ratio") is not None:
        snapshot.fund_holding_ratio = fund["ratio"]

    # Combined institutional-change input: prefer northbound, fall back to fund.
    northbound_fund_change_pct = None
    if northbound and northbound.get("change_pct") is not None:
        northbound_fund_change_pct = northbound["change_pct"]
    elif fund and fund.get("change_pct") is not None:
        northbound_fund_change_pct = fund["change_pct"]

    snapshot.chip_concentration_score = compute_chip_concentration_score(
        snapshot.holder_count_change_pct,
        avg_hold_change_pct,
        northbound_fund_change_pct,
    )
    snapshot.holder_trend = compute_holder_trend([h.holder_count for h in history])
    return snapshot


def load_chip_data(
    codes: List[str],
    *,
    end_date: Optional[date] = None,
    cache: Optional[ChipDataCache] = None,
) -> Dict[str, ChipSnapshot]:
    """Load chip-concentration data for ``codes`` with caching and isolation.

    Returns a dict mapping each code to a ``ChipSnapshot``. Missing or failed
    symbols are still present with ``error`` populated. Never raises.
    """
    if end_date is None:
        end_date = date.today()
    cache = cache or ChipDataCache()
    results: Dict[str, ChipSnapshot] = {}
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
        snapshot = _fetch_one_chip(code)
        results[code] = snapshot
        if snapshot.error is None:
            cache.set(code, end_date, snapshot)

    return results


__all__ = [
    "ChipDataCache",
    "compute_chip_concentration_score",
    "compute_holder_trend",
    "load_chip_data",
]
