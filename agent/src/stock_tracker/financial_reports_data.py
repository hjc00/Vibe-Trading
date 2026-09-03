"""Financial-report reader (财报速读) — on-demand, multi-period.

拉取单只 A 股最近若干报告期（年报/中报/季报混合口径）的核心指标序列，产出
``FinancialReportSnapshot``：横向多期对比由前端负责，这里只负责把干净期数
转成模型、基于最新期标红旗、并把最新实际 EPS 与一致预期 EPS 对比出 beat/miss。

数据源：东财 F10 主指标表（``RPT_F10_FINANCE_MAINFINADATA``），通过
:func:`src.tools.financial_statements_tool.fetch_financial_indicators` 复用
既有抓取与字段归一化，本模块不重复实现 provider 逻辑。

设计原则：
- 纯函数与编排分离：``build_financial_report`` 可独立单测；``load_financial_report``
  是唯一的网络入口。
- 不进日频 refresh：由前端在选中标的时触发读取（后端缓存命中即秒回），
  手动"刷新"走 ``force`` 强制重拉。
- 持久化缓存：财报指标只在报告期披露后变化，按 code 把东财返回落盘到
  ``data/stock_tracker/financial_reports/<code>.json``（进程重启后仍命中），
  默认 24h TTL，命中优先返回缓存、不再打网络；拉取失败时回退展示上一份缓存。
- 永不抛异常，缺数据/失败降级为带 ``error`` 的空报告。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.stock_tracker.models import FinancialPeriod, FinancialReportSnapshot
from src.stock_tracker.store import atomic_write_json, tracker_data_root
from src.tools.financial_statements_tool import fetch_financial_indicators

logger = logging.getLogger(__name__)

# Beat/miss 判定阈值：实际 EPS 相对一致预期 ±5% 内视为 inline。
_BEAT_TOLERANCE = 0.05

# 财报指标按报告期披露（季度级），日内不变；24h TTL 让同一自然日内重复点击
# 直接命中缓存，又不会把盘后新披露的报告期冻结太久。
_CACHE_TTL_SECONDS = 24 * 60 * 60
# 落盘缓存的子目录（tracker 数据根下），与 snapshots/analyses 同级。
_CACHE_DIRNAME = "financial_reports"
_CACHE_SCHEMA_VERSION = 1


def _beat_miss(actual_eps: Optional[float], consensus_eps: Optional[float]) -> Optional[str]:
    """Compare latest actual EPS against the consensus estimate."""
    if actual_eps is None or consensus_eps is None or consensus_eps <= 0:
        return None
    ratio = actual_eps / consensus_eps
    if ratio > 1 + _BEAT_TOLERANCE:
        return "beat"
    if ratio < 1 - _BEAT_TOLERANCE:
        return "miss"
    return "inline"


def _flag(latest: FinancialPeriod, prior: Optional[FinancialPeriod]) -> List[str]:
    """Derive human-readable red flags from the latest (and prior) period."""
    flags: List[str] = []
    ocf = latest.operating_cashflow_to_net_profit
    if ocf is not None and ocf < 0.5:
        flags.append("经营现金流/净利 < 0.5，利润含金量偏低")
    if latest.net_profit_yoy is not None and latest.net_profit_yoy < 0:
        flags.append("归母净利润同比下滑")
    if latest.revenue_yoy is not None and latest.revenue_yoy < 0:
        flags.append("营业收入同比下滑")
    if latest.debt_to_assets is not None and latest.debt_to_assets > 70:
        flags.append("资产负债率 > 70%，杠杆偏高")
    if latest.gross_margin is not None and prior is not None:
        if prior.gross_margin is not None and latest.gross_margin < prior.gross_margin:
            flags.append("毛利率连续下滑（较上期下降）")
    if latest.net_profit_yoy is not None and latest.revenue_yoy is not None:
        if latest.net_profit_yoy < latest.revenue_yoy - 20:
            flags.append("增收不增利：净利同比明显低于营收同比")
    return flags


def build_financial_report(
    code: str,
    indicators: List[dict],
    *,
    consensus_eps: Optional[float] = None,
) -> FinancialReportSnapshot:
    """Build a :class:`FinancialReportSnapshot` from clean period dicts.

    ``indicators`` are newest-first dicts as returned by
    :func:`src.tools.financial_statements_tool.fetch_financial_indicators`
    (``end_date`` / ``report_type`` / indicator fields). Pure function (no
    network), safe to unit test with synthetic input.

    Returns:
        Snapshot with newest-first ``periods``; an empty data source degrades to
        ``error="no financial report data"`` instead of raising.
    """
    periods = [
        FinancialPeriod(**{k: v for k, v in item.items() if k in FinancialPeriod.model_fields})
        for item in indicators
    ]
    snapshot = FinancialReportSnapshot(code=code, periods=periods)
    if not periods:
        snapshot.error = "no financial report data"
        return snapshot
    latest, prior = periods[0], (periods[1] if len(periods) > 1 else None)
    snapshot.red_flags = _flag(latest, prior)
    snapshot.beat_miss = _beat_miss(latest.eps, consensus_eps)
    snapshot.consensus_eps = consensus_eps
    snapshot.source = "eastmoney"
    return snapshot


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_cache_root() -> Path:
    """Tracker-local folder holding the per-symbol report cache files."""
    return tracker_data_root() / _CACHE_DIRNAME


def _safe_filename(code: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in code)


class _CachedReportEntry:
    __slots__ = ("periods", "expires_at")

    def __init__(self, periods: List[dict], expires_at: datetime) -> None:
        self.periods = periods
        self.expires_at = expires_at


class FinancialReportCache:
    """Disk-persistent TTL cache of one symbol's clean financial indicators.

    Keyed by code; each entry is a JSON file under
    ``data/stock_tracker/financial_reports/<code>.json`` so cache hits survive
    process restarts (the tracker commits ``data/stock_tracker``). An in-memory
    mirror serves repeat clicks without touching disk; expiry is wall-clock so a
    restart cannot accidentally resurrect a stale entry as fresh.

    Caches the network payload (``fetch_financial_indicators`` newest-first
    dicts) rather than the derived snapshot: red flags are cheap to recompute,
    and the beat/miss depends on the latest consensus EPS which may have moved
    since the entry was stored. Rebuilding per request keeps beat/miss fresh.

    Writes are atomic (temp file + ``os.replace``) so a crash never leaves a
    half-written report.
    """

    def __init__(
        self,
        ttl_seconds: float = _CACHE_TTL_SECONDS,
        root_dir: Path | str | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self.root = Path(root_dir) if root_dir else _default_cache_root()
        self._mem: Dict[str, _CachedReportEntry] = {}
        self._lock = threading.Lock()

    def get(self, code: str) -> Optional[List[dict]]:
        """Return fresh cached indicators for ``code``, or ``None`` when stale/missing."""
        now = _utcnow()
        with self._lock:
            entry = self._mem.get(code)
            if entry is not None and entry.expires_at > now:
                return entry.periods
            self._mem.pop(code, None)
        loaded = self._read(code)
        if loaded is None or loaded.expires_at <= now:
            return None
        with self._lock:
            self._mem[code] = loaded
        return loaded.periods

    def get_stale(self, code: str) -> Optional[List[dict]]:
        """Return the last known indicators for ``code`` even past the TTL.

        Used as a fallback so a failed refresh still shows the previous report
        instead of an error card. Returns the in-memory value when present
        (fresh or not) and otherwise reads the persisted file, expiry ignored.
        """
        with self._lock:
            entry = self._mem.get(code)
            if entry is not None:
                return entry.periods
        loaded = self._read(code)
        return loaded.periods if loaded is not None else None

    def set(self, code: str, periods: List[dict]) -> None:
        """Persist ``periods`` for ``code`` for the configured TTL."""
        if not periods:
            return
        expires_at = _utcnow() + timedelta(seconds=self._ttl_seconds)
        envelope = {
            "schema": _CACHE_SCHEMA_VERSION,
            "code": code,
            "fetched_at": _utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
            "periods": periods,
        }
        self._write(code, envelope)
        with self._lock:
            self._mem[code] = _CachedReportEntry(periods, expires_at)

    def clear(self) -> None:
        """Drop in-memory entries and delete all persisted report files."""
        with self._lock:
            self._mem.clear()
        if self.root.exists():
            for path in self.root.glob("*.json"):
                try:
                    path.unlink()
                except OSError:  # noqa: BLE001 - best-effort cleanup
                    logger.warning("Failed to remove report cache %s", path)

    # -- persistence helpers -------------------------------------------------

    def _path(self, code: str) -> Path:
        return self.root / f"{_safe_filename(code)}.json"

    def _read(self, code: str) -> Optional[_CachedReportEntry]:
        path = self._path(code)
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(raw, dict) or raw.get("schema") != _CACHE_SCHEMA_VERSION:
            return None
        periods = raw.get("periods")
        if not isinstance(periods, list):
            return None
        try:
            expires_at = datetime.fromisoformat(str(raw["expires_at"]))
        except (KeyError, TypeError, ValueError):
            return None
        return _CachedReportEntry(periods, expires_at)

    def _write(self, code: str, envelope: dict) -> None:
        atomic_write_json(self._path(code), envelope)


_DEFAULT_CACHE = FinancialReportCache()


def load_financial_report(
    code: str,
    *,
    consensus_eps: Optional[float] = None,
    max_periods: int = 12,
    cache: Optional[FinancialReportCache] = None,
    force: bool = False,
) -> FinancialReportSnapshot:
    """Fetch (or serve from the persisted cache) a financial report. Never raises.

    Args:
        code: A-share symbol (e.g. ``"600519.SH"``).
        consensus_eps: Optional latest-year consensus EPS for beat/miss.
        max_periods: Most recent report periods to keep.
        cache: Cache to consult; defaults to the process-wide module cache,
            persisted under ``data/stock_tracker/financial_reports``.
        force: Skip the cache read and hit the network (e.g. a manual Refresh
            click); a successful fetch still refreshes the cache.

    Returns:
        Financial report snapshot; unresolvable / fetch failure degrades to
        ``error="no financial report data"`` (unless a previously cached report
        exists, which is then served as a last-good fallback).
    """
    cache = cache or _DEFAULT_CACHE
    if not force:
        cached = cache.get(code)
        if cached is not None:
            return build_financial_report(code, cached, consensus_eps=consensus_eps)
    try:
        indicators = fetch_financial_indicators(code, max_periods=max_periods)
    except Exception:  # noqa: BLE001 - never raise out of the loader
        indicators = []
    if indicators:
        cache.set(code, indicators)
        return build_financial_report(code, indicators, consensus_eps=consensus_eps)
    stale = cache.get_stale(code)
    if stale is not None:
        return build_financial_report(code, stale, consensus_eps=consensus_eps)
    return build_financial_report(code, [], consensus_eps=consensus_eps)


__all__ = [
    "FinancialReportCache",
    "build_financial_report",
    "load_financial_report",
]
