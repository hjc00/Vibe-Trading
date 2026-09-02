"""Valuation data loader for the stock tracker.

批量拉取个股估值与基本面质量数据，提供按交易日缓存和 per-symbol 错误隔离，
返回结构化的 ``ValuationSnapshot``。

数据源（均走 :func:`backtest.loaders.eastmoney_client.get_json` 的东财节流）：
- ``RPT_VALUEANALYSIS_DET`` —— 每日估值序列（PE_TTM / PB / PS_TTM / PCF /
  PEG / 总市值）。一次请求返回约 8 年历史，既给当前值也用于 3/5/10 年分位。
- ``RPT_F10_FINANCE_MAINFINADATA`` —— 分报告期财务指标（ROE / 毛利率 /
  净利率 / 增速 / 每股经营现金流 / 资产负债率），用于质量评分。
- 可选 Tushare 兜底（``daily_basic`` / ``fina_indicator``），需要配置
  ``TUSHARE_TOKEN``；token 缺失或主源不可用时按 symbol 记录 error，不影响
  主流程。所有请求共享东财节流，批量请求间有固定间隔。

字段口径说明：
- ROE / 毛利率 / 净利率 / 增速 / 资产负债率均为百分数（如 16.75 表示 16.75%）。
- ``operating_cashflow_to_net_profit`` = 每股经营现金流 / 每股收益（F10 口径）；
  Tushare 兜底用 ``ocf_to_profit``（经营现金流/营业利润）作为近似。
- 分位窗口：3y≈750 交易日、5y≈1250、10y≈2500；序列不足
  :data:`_MIN_PERCENTILE_SESSIONS` 个交易日时返回 ``None``。
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from backtest.loaders.eastmoney_client import get_json
from src.stock_tracker._convert import dashed_date, to_float
from src.stock_tracker.models import ValuationHistoryItem, ValuationSnapshot
from src.tools import tushare_fallbacks

logger = logging.getLogger(__name__)

# Trading-day history requested from the valuation report. Covers the full 10y
# window (plus slack); the Eastmoney report returns what it has (~8.5y).
_DEFAULT_DAYS = 2500
_CACHE_TTL_SECONDS = 30 * 60
# Delay between per-symbol HTTP requests in batch loaders. Mirrors the capital
# data loader to keep bursty short-lived HTTPS requests under the proxy limit.
_REQUEST_DELAY_SECONDS = 0.15

# Eastmoney datacenter report API (same host as the F10 financial-statements
# tool). ``sortTypes=-1`` returns newest-first rows.
_DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_VALUATION_REPORT = "RPT_VALUEANALYSIS_DET"
_FUNDAMENTAL_REPORT = "RPT_F10_FINANCE_MAINFINADATA"
# Newest-first sort column differs per report.
_REPORT_SORT_COLUMN = {
    _VALUATION_REPORT: "TRADE_DATE",
    _FUNDAMENTAL_REPORT: "REPORT_DATE",
}

# Minimum history points required before a valuation percentile is meaningful.
_MIN_PERCENTILE_SESSIONS = 60
# Report periods to fetch for quality scoring (~10 years of quarterly reports).
_FUNDAMENTAL_PERIODS = 40
# Number of trailing report periods used for 5-year stability aggregates.
_STABILITY_PERIODS = 20

# Percentile windows by label, in trading days.
_PERCENTILE_WINDOWS = {"3y": 750, "5y": 1250, "10y": 2500}

# Fundamental quality score weights. Centralized here so the scoring rule is
# explainable and tunable in one place; sub-scores are each 0-100.
_QUALITY_WEIGHTS = {
    "roe": 0.30,
    "cashflow": 0.20,
    "growth": 0.25,
    "gross_margin": 0.15,
    "leverage": 0.10,
}


class _CachedValuationEntry:
    __slots__ = ("snapshot", "expires_at")

    def __init__(self, snapshot: ValuationSnapshot, expires_at: float) -> None:
        self.snapshot = snapshot
        self.expires_at = expires_at


class ValuationDataCache:
    """TTL cache for per-symbol valuation data keyed by ``(code, trading_date)``."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, _CachedValuationEntry] = {}
        self._lock = threading.Lock()

    def get(
        self,
        code: str,
        trading_date: date,
    ) -> Optional[ValuationSnapshot]:
        key = f"{code}:{trading_date.isoformat()}"
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or entry.expires_at < now:
                self._cache.pop(key, None)
                return None
            return entry.snapshot

    def set(self, code: str, trading_date: date, snapshot: ValuationSnapshot) -> None:
        key = f"{code}:{trading_date.isoformat()}"
        expires_at = time.monotonic() + self._ttl_seconds
        with self._lock:
            self._cache[key] = _CachedValuationEntry(snapshot, expires_at)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def _fetch_report(report_name: str, secucode: str, *, page_size: int) -> List[Dict[str, Any]]:
    """Fetch newest-first rows for one datacenter report; never raises."""
    try:
        payload = get_json(
            _DATACENTER_URL,
            params={
                "reportName": report_name,
                "columns": "ALL",
                "filter": f'(SECUCODE="{secucode}")',
                "pageSize": str(page_size),
                "sortColumns": _REPORT_SORT_COLUMN[report_name],
                "sortTypes": "-1",
            },
        )
    except Exception as exc:  # noqa: BLE001 - report failure is non-fatal
        logger.warning("datacenter report %s failed for %s: %s", report_name, secucode, exc)
        return []
    result = payload.get("result") if isinstance(payload, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _parse_valuation_history(rows: List[Dict[str, Any]]) -> List[ValuationHistoryItem]:
    """Build an ascending daily valuation history from newest-first report rows."""
    parsed: List[ValuationHistoryItem] = []
    for row in rows:
        trade_date = dashed_date(row.get("TRADE_DATE"))
        if trade_date is None:
            continue
        parsed.append(
            ValuationHistoryItem(
                trade_date=trade_date,
                close=to_float(row.get("CLOSE_PRICE")),
                pe_ttm=to_float(row.get("PE_TTM")),
                pb=to_float(row.get("PB_MRQ")),
                ps_ttm=to_float(row.get("PS_TTM")),
            )
        )
    parsed.sort(key=lambda item: item.trade_date)
    return parsed


def _window_percentile(values: List[float], window_days: int) -> Optional[float]:
    """Percentile (0-100) of the latest value within the trailing window."""
    if len(values) < _MIN_PERCENTILE_SESSIONS:
        return None
    window = values[-window_days:]
    if len(window) < _MIN_PERCENTILE_SESSIONS:
        return None
    series = pd.Series(window)
    return round(float(series.rank(pct=True).iloc[-1] * 100), 2)


def _build_valuation_snapshot(rows: List[Dict[str, Any]]) -> ValuationSnapshot:
    """Build a ``ValuationSnapshot`` from ``RPT_VALUEANALYSIS_DET`` rows."""
    if not rows:
        return ValuationSnapshot(error="no valuation data")
    latest = rows[0]
    history = _parse_valuation_history(rows)
    pe_series = [item.pe_ttm for item in history if item.pe_ttm is not None]
    pb_series = [item.pb for item in history if item.pb is not None]

    peg = to_float(latest.get("PEG_CAR"))
    snapshot = ValuationSnapshot(
        trade_date=dashed_date(latest.get("TRADE_DATE")),
        pe_ttm=to_float(latest.get("PE_TTM")),
        pb=to_float(latest.get("PB_MRQ")),
        ps_ttm=to_float(latest.get("PS_TTM")),
        pcf_ocf_ttm=to_float(latest.get("PCF_OCF_TTM")),
        # PEG is only meaningful for positive growth; Eastmoney emits a negative
        # "PEG_CAR" when net profit is falling, which we suppress.
        peg=peg if peg is not None and peg > 0 else None,
        total_market_cap=to_float(latest.get("TOTAL_MARKET_CAP")),
        source="eastmoney",
        history=history,
    )
    for label, window_days in _PERCENTILE_WINDOWS.items():
        setattr(snapshot, f"pe_percentile_{label}", _window_percentile(pe_series, window_days))
        setattr(snapshot, f"pb_percentile_{label}", _window_percentile(pb_series, window_days))
    return snapshot


def _tushare_valuation_snapshot(code: str) -> ValuationSnapshot:
    """Build a snapshot from the Tushare ``daily_basic`` fallback; never raises."""
    try:
        result = tushare_fallbacks.fetch_daily_basic(code, days=_DEFAULT_DAYS)
    except Exception as exc:  # noqa: BLE001 - fallback availability varies by token
        return ValuationSnapshot(error=str(exc), source="unavailable")
    rows = result.get("rows", [])
    if not rows:
        return ValuationSnapshot(error="no valuation data", source="unavailable")

    latest = rows[-1]
    history = [
        ValuationHistoryItem(
            trade_date=dashed_date(item.get("trade_date")),
            close=to_float(item.get("close")),
            pe_ttm=to_float(item.get("pe_ttm")),
            pb=to_float(item.get("pb")),
            ps_ttm=to_float(item.get("ps_ttm")),
        )
        for item in rows
    ]
    snapshot = ValuationSnapshot(
        trade_date=dashed_date(latest.get("trade_date")),
        pe_ttm=to_float(latest.get("pe_ttm")),
        pb=to_float(latest.get("pb")),
        ps_ttm=to_float(latest.get("ps_ttm")),
        dividend_yield=to_float(latest.get("dividend_yield")),
        total_market_cap=to_float(latest.get("total_market_cap")),
        source="tushare",
        history=history,
    )
    pe_series = [item.pe_ttm for item in history if item.pe_ttm is not None]
    pb_series = [item.pb for item in history if item.pb is not None]
    for label, window_days in _PERCENTILE_WINDOWS.items():
        setattr(snapshot, f"pe_percentile_{label}", _window_percentile(pe_series, window_days))
        setattr(snapshot, f"pb_percentile_{label}", _window_percentile(pb_series, window_days))
    return snapshot


# ---------------------------------------------------------------------------
# Fundamental quality
# ---------------------------------------------------------------------------


def _parse_fundamental_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize ``RPT_F10_FINANCE_MAINFINADATA`` rows into ascending periods.

    Cash-flow quality is derived from per-share figures so it reflects the
    actual 经营现金流/净利润 ratio rather than a margin proxy.
    """
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        end_date = dashed_date(row.get("REPORT_DATE"))
        if end_date is None:
            continue
        eps = to_float(row.get("EPSJB"))
        per_share_ocf = to_float(row.get("MGJYXJJE"))
        ocf_to_net_profit: Optional[float] = None
        if eps is not None and eps > 0 and per_share_ocf is not None:
            ocf_to_net_profit = round(per_share_ocf / eps, 3)
        parsed.append(
            {
                "end_date": end_date,
                "roe": to_float(row.get("ROEJQ")),
                "gross_margin": to_float(row.get("XSMLL")),
                "net_margin": to_float(row.get("XSJLL")),
                "net_profit_yoy": to_float(row.get("PARENTNETPROFITTZ")),
                "revenue_yoy": to_float(row.get("TOTALOPERATEREVETZ")),
                "debt_to_assets": to_float(row.get("ZCFZL")),
                "operating_cashflow_to_net_profit": ocf_to_net_profit,
            }
        )
    parsed.sort(key=lambda item: item["end_date"])
    return parsed


def _aggregate_fundamentals(periods: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge the latest period with trailing 5-year stability aggregates."""
    if not periods:
        return {}
    latest = dict(periods[-1])
    window = periods[-_STABILITY_PERIODS:]
    roes = [p["roe"] for p in window if p.get("roe") is not None]
    margins = [p["gross_margin"] for p in window if p.get("gross_margin") is not None]
    if len(roes) >= 4:
        latest["roe_mean_5y"] = round(statistics.mean(roes), 2)
        latest["roe_std_5y"] = round(statistics.pstdev(roes), 2)
    if len(margins) >= 4:
        latest["gross_margin_std_5y"] = round(statistics.pstdev(margins), 2)
    return latest


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _linear(score: float, lo: float, hi: float) -> float:
    """Map a raw value in ``[lo, hi]`` linearly to 0-100, clamped outside."""
    if hi == lo:
        return 100.0 if score >= hi else 0.0
    return _clamp((score - lo) / (hi - lo) * 100.0, 0.0, 100.0)


def _sub_roe(latest: Dict[str, Any]) -> Optional[float]:
    roe = latest.get("roe")
    if roe is None:
        return None
    level = _linear(roe, 0.0, 20.0)  # ROE 20%+ scores full
    stability = 1.0
    mean = latest.get("roe_mean_5y")
    std = latest.get("roe_std_5y")
    if mean and std is not None:
        stability = 1.0 - _clamp(abs(std) / abs(mean), 0.0, 0.5)
    return level * stability


def _sub_cashflow(latest: Dict[str, Any]) -> Optional[float]:
    ratio = latest.get("operating_cashflow_to_net_profit")
    if ratio is None:
        return None
    if ratio >= 1.0:
        return 100.0
    if ratio <= 0.0:
        return 0.0
    return _linear(ratio, 0.0, 1.0)


def _sub_growth(latest: Dict[str, Any]) -> Optional[float]:
    yoy = latest.get("net_profit_yoy")
    if yoy is None:
        return None
    # 20%+ growth scores full, -20%+ decline scores zero, 0% is neutral.
    return _linear(yoy, -20.0, 20.0)


def _sub_gross_margin(latest: Dict[str, Any]) -> Optional[float]:
    margin = latest.get("gross_margin")
    if margin is None:
        return None
    level = _linear(margin, 0.0, 40.0)  # 40%+ gross margin scores full
    stability = 1.0
    std = latest.get("gross_margin_std_5y")
    if std is not None and margin != 0:
        stability = 1.0 - _clamp(abs(std) / abs(margin), 0.0, 0.5)
    return level * stability


def _sub_leverage(latest: Dict[str, Any]) -> Optional[float]:
    dta = latest.get("debt_to_assets")
    if dta is None:
        return None
    # <=30% leverage scores full, >=70% scores zero.
    return 100.0 - _linear(dta, 30.0, 70.0)


_SUB_SCORES = {
    "roe": _sub_roe,
    "cashflow": _sub_cashflow,
    "growth": _sub_growth,
    "gross_margin": _sub_gross_margin,
    "leverage": _sub_leverage,
}


def compute_quality_score(
    fundamentals: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> Optional[float]:
    """Score fundamental quality 0-100 from current-period plus aggregates.

    ``fundamentals`` carries at least one of: ``roe``, ``roe_mean_5y``,
    ``roe_std_5y``, ``gross_margin``, ``gross_margin_std_5y``,
    ``net_profit_yoy``, ``operating_cashflow_to_net_profit``,
    ``debt_to_assets``. Missing sub-scores are dropped and the remaining
    weights renormalized; returns ``None`` when no sub-score is available.
    """
    if not weights:
        weights = _QUALITY_WEIGHTS
    available: Dict[str, float] = {}
    for name, fn in _SUB_SCORES.items():
        value = fn(fundamentals)
        if value is not None:
            available[name] = value
    if not available:
        return None
    total_weight = sum(weights.get(name, 0.0) for name in available)
    if total_weight <= 0:
        return None
    score = sum(weights.get(name, 0.0) * value for name, value in available.items())
    return round(score / total_weight, 2)


# ---------------------------------------------------------------------------
# Batch loaders
# ---------------------------------------------------------------------------


def _fetch_one_valuation(code: str) -> ValuationSnapshot:
    """Fetch valuation + fundamentals for one symbol; never raises."""
    snapshot = ValuationSnapshot()
    try:
        rows = _fetch_report(_VALUATION_REPORT, code, page_size=_DEFAULT_DAYS)
        if rows:
            snapshot = _build_valuation_snapshot(rows)
        else:
            snapshot = _tushare_valuation_snapshot(code)
    except Exception as exc:  # noqa: BLE001 - per-symbol isolation
        logger.warning("Valuation fetch failed for %s: %s", code, exc)
        snapshot = _tushare_valuation_snapshot(code)
        if snapshot.error is None:
            snapshot.error = str(exc)

    # Fundamentals + quality score; isolated so a bad symbol never kills the batch.
    try:
        fund_rows = _fetch_report(_FUNDAMENTAL_REPORT, code, page_size=_FUNDAMENTAL_PERIODS)
        if fund_rows:
            _apply_fundamentals(snapshot, _parse_fundamental_rows(fund_rows))
        else:
            _apply_fundamentals_tushare(snapshot, code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fundamental fetch failed for %s: %s", code, exc)
        detail = f"fundamentals: {exc}"
        snapshot.error = f"{snapshot.error}; {detail}" if snapshot.error else detail

    if snapshot.error is None and snapshot.pe_ttm is None and snapshot.roe is None:
        snapshot.error = "no valuation or fundamental data"
    return snapshot


def _apply_fundamentals(snapshot: ValuationSnapshot, periods: List[Dict[str, Any]]) -> None:
    """Attach fundamentals + quality score from normalized report periods."""
    aggregated = _aggregate_fundamentals(periods)
    if not aggregated:
        return
    snapshot.roe = aggregated.get("roe")
    snapshot.roe_mean_5y = aggregated.get("roe_mean_5y")
    snapshot.roe_std_5y = aggregated.get("roe_std_5y")
    snapshot.gross_margin = aggregated.get("gross_margin")
    snapshot.gross_margin_std_5y = aggregated.get("gross_margin_std_5y")
    snapshot.net_margin = aggregated.get("net_margin")
    snapshot.net_profit_yoy = aggregated.get("net_profit_yoy")
    snapshot.revenue_yoy = aggregated.get("revenue_yoy")
    snapshot.operating_cashflow_to_net_profit = aggregated.get("operating_cashflow_to_net_profit")
    snapshot.debt_to_assets = aggregated.get("debt_to_assets")
    snapshot.fundamental_quality_score = compute_quality_score(aggregated)
    if snapshot.source == "unavailable":
        snapshot.source = "eastmoney"


def _apply_fundamentals_tushare(snapshot: ValuationSnapshot, code: str) -> None:
    """Attach fundamentals from the Tushare ``fina_indicator`` fallback."""
    try:
        result = tushare_fallbacks.fetch_fina_indicator(code, periods=_FUNDAMENTAL_PERIODS)
    except Exception:  # noqa: BLE001 - fallback availability varies by token
        return
    periods = result.get("rows", [])
    if not periods:
        return
    # ``fetch_fina_indicator`` already returns normalized dicts in ascending order.
    aggregated = _aggregate_fundamentals(periods)
    if not aggregated:
        return
    snapshot.roe = aggregated.get("roe")
    snapshot.roe_mean_5y = aggregated.get("roe_mean_5y")
    snapshot.roe_std_5y = aggregated.get("roe_std_5y")
    snapshot.gross_margin = aggregated.get("gross_margin")
    snapshot.gross_margin_std_5y = aggregated.get("gross_margin_std_5y")
    snapshot.net_margin = aggregated.get("net_margin")
    snapshot.net_profit_yoy = aggregated.get("net_profit_yoy")
    snapshot.revenue_yoy = aggregated.get("revenue_yoy")
    snapshot.operating_cashflow_to_net_profit = aggregated.get("operating_cashflow_to_net_profit")
    snapshot.debt_to_assets = aggregated.get("debt_to_assets")
    snapshot.fundamental_quality_score = compute_quality_score(aggregated)
    if snapshot.source == "unavailable":
        snapshot.source = "tushare"


def load_valuation_data(
    codes: List[str],
    *,
    end_date: Optional[date] = None,
    cache: Optional[ValuationDataCache] = None,
) -> Dict[str, ValuationSnapshot]:
    """Load valuation + quality metrics for ``codes`` with caching and isolation.

    Returns a dict mapping each code to a ``ValuationSnapshot``. Missing or
    failed symbols are still present with ``error`` populated.
    """
    if end_date is None:
        end_date = date.today()
    cache = cache or ValuationDataCache()
    results: Dict[str, ValuationSnapshot] = {}
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
        snapshot = _fetch_one_valuation(code)
        results[code] = snapshot
        if snapshot.error is None:
            cache.set(code, end_date, snapshot)

    return results


__all__ = [
    "ValuationDataCache",
    "compute_quality_score",
    "load_valuation_data",
]
