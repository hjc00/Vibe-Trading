"""Event-calendar data loader for the stock tracker.

聚合限售解禁 / 业绩预告 / 龙虎榜 / 股东增减持四类事件，产出结构化的
``EventSnapshot``（含 0–100 综合事件风险分 ``event_risk_score``），供前端事件
时间线卡与 LLM 分析 prompt 使用。范式对齐 :mod:`src.stock_tracker.valuation_data`：
纯函数 + TTL 缓存 + per-symbol 隔离 + 永不抛异常。

数据源与降级策略：
- 限售解禁 —— 复用东财 ``RPT_LIFT_STOCK``（:func:`src.tools.lockup_expiry_tool
  .fetch_lockup_records`，个股未来 90 天窗口）。
- 龙虎榜 —— 复用东财 ``RPT_DAILYBILLBOARD_DETAILS``（:func:`src.tools
  .dragon_tiger_tool.fetch_recent_board`，全市场近几个交易日一次拉取后按 code
  过滤，请求数与 watchlist 大小无关）。
- 业绩预告 / 股东增减持 —— Tushare ``forecast`` / ``stk_holdertrade`` 兜底
  （best-effort，需 2000 积分；token 缺失或权限不足时按 symbol 记 error，
  不阻塞主流程）。

风险规则集中在 :func:`compute_event_risk_score` 与各 ``_score_*``：每个事件先算
子分（0–100），综合分 = 主导风险子分 + 每多一个 danger 事件 +5，封顶 100；
无事件返回 ``None``。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from src.stock_tracker.models import EventItem, EventSnapshot
from src.tools import tushare_fallbacks
from src.tools.dragon_tiger_tool import fetch_recent_board
from src.tools.lockup_expiry_tool import fetch_lockup_records

logger = logging.getLogger(__name__)

# Event type slugs.
_EVENT_LOCKUP = "lockup"
_EVENT_FORECAST = "earnings_forecast"
_EVENT_DRAGON_TIGER = "dragon_tiger"
_EVENT_HOLDER_TRADE = "holder_trade"

# Future event window (calendar days) — aligned with the lockup tool default.
_DEFAULT_HORIZON_DAYS = 90
# Dragon-tiger rows are recent (they cannot be in the future); keep the last
# few weeks so a watchlist holding a recently-appearing name still shows it.
_DRAGON_TIGER_LOOKBACK_DAYS = 30
# Recent disclosures (forecast / holder-trade announcements) shown as context
# rather than forward calendar events.
_DISCLOSURE_LOOKBACK_DAYS = 60
_CACHE_TTL_SECONDS = 30 * 60
# Delay between per-symbol HTTP requests (mirrors the capital/valuation loaders).
_REQUEST_DELAY_SECONDS = 0.15

# Tushare forecast ``type`` labels.
_FORECAST_POSITIVE = {"预增", "略增", "续盈", "扭亏"}
_FORECAST_NEGATIVE = {"略减", "预减", "首亏", "续亏"}
# Risk thresholds mapping a sub-score to a tone level.
_DANGER_THRESHOLD = 70.0
_WARNING_THRESHOLD = 40.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _fmt_shares(shares_wan: Optional[float]) -> str:
    """Format an Eastmoney unlock amount (万-share units) as 亿/万股."""
    if shares_wan is None:
        return ""
    shares = shares_wan * 10_000.0
    if shares >= 1e8:
        return f"{shares / 1e8:.2f} 亿股"
    if shares >= 1e4:
        return f"{shares / 1e4:.1f} 万股"
    return f"{shares:.0f} 股"


def _fmt_money_wan(cap_wan: Optional[float]) -> str:
    """Format an Eastmoney market-cap cell (万-CNY units) as 亿元."""
    if cap_wan is None:
        return ""
    value = cap_wan * 10_000.0
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿元"
    return f"{value / 1e4:.0f} 万元"


# ---------------------------------------------------------------------------
# Risk sub-scores. Each reads its parser's ``details`` dict, which keeps the
# raw upstream values under stable keys, so the rule stays in one place.
# ---------------------------------------------------------------------------


def _days_until(event_date: Optional[date], as_of: date) -> Optional[int]:
    if event_date is None:
        return None
    return (event_date - as_of).days


def _risk_level(score: Optional[float]) -> str:
    """Map an event sub-score to the tone vocabulary used by the frontend."""
    if score is None:
        return "info"
    if score >= _DANGER_THRESHOLD:
        return "danger"
    if score >= _WARNING_THRESHOLD:
        return "warning"
    return "info"


def _score_lockup(details: Dict[str, Any]) -> Optional[float]:
    """Lockup supply-pressure sub-score: ratio (unlock/total) + proximity.

    ``details["free_ratio"]`` is Eastmoney's native 0-1 fraction of total shares
    unlocking (e.g. 0.05762 == 5.76%); ``details["free_date"]`` the unlock day.
    A large unlock very soon is the highest supply-pressure event.
    """
    ratio = details.get("free_ratio")
    free_date = details.get("free_date")
    if ratio is None or not free_date:
        return None
    try:
        unlock_day = date.fromisoformat(str(free_date)[:10])
    except ValueError:
        return None
    days_until = _days_until(unlock_day, date.today())
    if days_until is None or days_until < 0:
        return None  # already unlocked — not a forward risk
    ratio_pct = float(ratio) * 100.0
    if ratio_pct >= 5.0 and days_until <= 30:
        return 80.0
    if ratio_pct >= 1.0 and days_until <= 30:
        return 60.0
    if ratio_pct >= 5.0:
        return 55.0
    if ratio_pct >= 1.0:
        return 45.0
    return 20.0  # small unlock — informational


_FORECAST_RISK: Dict[str, float] = {
    "预增": 20.0,
    "略增": 25.0,
    "续盈": 30.0,
    "扭亏": 35.0,
    "略减": 55.0,
    "预减": 80.0,
    "首亏": 88.0,
    "续亏": 85.0,
    "减亏": 45.0,
}


def _score_forecast(details: Dict[str, Any]) -> Optional[float]:
    base = _FORECAST_RISK.get(str(details.get("forecast_type") or ""))
    if base is None:
        return None
    # A very wide expected decline reads worse; nudge by the p_change floor.
    p_min = details.get("p_change_min")
    if base >= _DANGER_THRESHOLD:
        if p_min is not None and p_min < -50.0:
            return min(100.0, base + 8.0)
        if p_min is not None and p_min > -10.0:
            return base - 5.0
    return base


def _score_holder_trade(details: Dict[str, Any]) -> Optional[float]:
    direction = str(details.get("in_de") or "").upper()
    change_ratio = details.get("change_ratio")
    if direction == "DE":
        # change_ratio is a percentage number (1.0 == 1% of total shares).
        if change_ratio is not None and float(change_ratio) >= 5.0:
            return 88.0
        if change_ratio is not None and float(change_ratio) >= 1.0:
            return 80.0
        return 45.0
    if direction == "IN":
        return 25.0  # 增持 — mildly positive, never a risk driver
    return None


def _score_dragon_tiger(details: Dict[str, Any]) -> Optional[float]:
    """龙虎榜 sub-score from the net buy/sell footprint.

    A heavy net *sell* board appearance suggests distribution; a net buy is
    informational. Uses the share of turnover the net amount represents so a
    large absolute amount on a huge-turnover day does not over-scare.
    """
    net = details.get("net_buy")
    if net is None or net == 0:
        return None
    turnover = details.get("turnover")
    turnover = float(turnover) if isinstance(turnover, (int, float)) else None
    if net < 0:
        if turnover and abs(net) / turnover >= 0.30:
            return 82.0
        if turnover and abs(net) / turnover >= 0.15:
            return 62.0
        return 50.0
    return 22.0  # 净买入 — informational


def _score_event(event: EventItem) -> Optional[float]:
    """Dispatch one event to its sub-score from the stored ``details``."""
    dispatch = {
        _EVENT_LOCKUP: _score_lockup,
        _EVENT_FORECAST: _score_forecast,
        _EVENT_HOLDER_TRADE: _score_holder_trade,
        _EVENT_DRAGON_TIGER: _score_dragon_tiger,
    }
    fn = dispatch.get(event.event_type)
    return fn(event.details) if fn is not None else None


# ---------------------------------------------------------------------------
# Pure parsers (records -> events). Parser output carries ``details`` with the
# raw inputs; ``days_until`` and the risk score/level are filled by
# :func:`build_event_snapshot` using the caller-provided ``as_of``.
# ---------------------------------------------------------------------------


def parse_lockup_events(
    records: List[Dict[str, Any]],
    as_of: Optional[date] = None,
    horizon_days: int = _DEFAULT_HORIZON_DAYS,
) -> List[EventItem]:
    """Normalize upcoming lockup-expiry records into ``EventItem``s.

    Records come from :func:`fetch_lockup_records` and carry ``free_date`` /
    ``free_shares`` / ``free_ratio`` / ``lift_market_cap``. Only events inside
    the forward ``horizon_days`` window (relative to ``as_of``) are kept.
    """
    if as_of is None:
        as_of = date.today()
    cutoff = as_of + timedelta(days=horizon_days)
    items: List[EventItem] = []
    for record in records:
        free_date = record.get("free_date")
        if not free_date:
            continue
        try:
            event_date = date.fromisoformat(str(free_date)[:10])
        except ValueError:
            continue
        if event_date < as_of or event_date > cutoff:
            continue
        shares_wan = record.get("free_shares")
        cap_wan = record.get("lift_market_cap")
        ratio = record.get("free_ratio")  # 0-1 fraction (native report scale)
        share_label = _fmt_shares(shares_wan)
        title = f"限售解禁 {share_label}".strip()
        summary_bits = []
        if cap_wan is not None:
            summary_bits.append(f"解禁市值 {_fmt_money_wan(cap_wan)}")
        if ratio is not None:
            summary_bits.append(f"占总股本 {float(ratio) * 100.0:.2f}%")
        summary = "；".join(summary_bits)
        items.append(
            EventItem(
                event_type=_EVENT_LOCKUP,
                event_date=event_date,
                title=title,
                summary=summary,
                days_until=_days_until(event_date, as_of),
                source="eastmoney",
                details={
                    "free_ratio": ratio,  # keep the native 0-1 fraction
                    "free_date": str(free_date)[:10],
                    "free_shares": shares_wan,  # 万-share units
                    "lift_market_cap": cap_wan,  # 万-CNY units
                },
            )
        )
    return items


_FORECAST_LABEL = {
    "预增": "业绩预增",
    "略增": "业绩略增",
    "续盈": "业绩续盈",
    "扭亏": "业绩扭亏",
    "减亏": "业绩减亏",
    "略减": "业绩略减",
    "预减": "业绩预减",
    "首亏": "业绩首亏",
    "续亏": "业绩续亏",
}


def parse_forecast_events(
    rows: List[Dict[str, Any]],
    as_of: Optional[date] = None,
) -> List[EventItem]:
    """Normalize Tushare ``forecast`` rows into ``EventItem``s.

    Each row's announcement date (``ann_date``) is the event date; only
    announcements from the recent ``_DISCLOSURE_LOOKBACK_DAYS`` are kept. Rows
    arrive newest-first from :func:`fetch_forecast`.
    """
    if as_of is None:
        as_of = date.today()
    cutoff = as_of - timedelta(days=_DISCLOSURE_LOOKBACK_DAYS)
    items: List[EventItem] = []
    for row in rows:
        ann_date = row.get("ann_date")
        if not ann_date:
            continue
        try:
            event_date = date.fromisoformat(str(ann_date)[:10])
        except ValueError:
            continue
        if event_date < cutoff or event_date > as_of:
            continue  # too old to matter for a current view
        ftype = str(row.get("type") or "")
        label = _FORECAST_LABEL.get(ftype, ftype or "业绩预告")
        p_min = row.get("p_change_min")
        p_max = row.get("p_change_max")
        if p_min is not None and p_max is not None and abs(p_min - p_max) < 1e-9:
            p_max = None
        range_text = ""
        if p_min is not None and p_max is not None:
            range_text = f"净利润同比 {p_min:+.1f}% ~ {p_max:+.1f}%"
        elif p_min is not None:
            range_text = f"净利润同比约 {p_min:+.1f}%"
        elif p_max is not None:
            range_text = f"净利润同比约 {p_max:+.1f}%"
        items.append(
            EventItem(
                event_type=_EVENT_FORECAST,
                event_date=event_date,
                title=label,
                summary=range_text,
                days_until=_days_until(event_date, as_of),
                source="tushare",
                details={
                    "forecast_type": ftype,
                    "p_change_min": p_min,
                    "p_change_max": p_max,
                    "net_profit_min": row.get("net_profit_min"),
                    "net_profit_max": row.get("net_profit_max"),
                    "report_end_date": str(row.get("end_date") or "")[:10],
                },
            )
        )
    return items


def parse_holder_trade_events(
    rows: List[Dict[str, Any]],
    as_of: Optional[date] = None,
) -> List[EventItem]:
    """Normalize Tushare ``stk_holdertrade`` rows into ``EventItem``s.

    One event per announcement within the recent lookback; ``details`` carry the
    direction, holder type and the trade's ratio to total shares.
    """
    if as_of is None:
        as_of = date.today()
    cutoff = as_of - timedelta(days=_DISCLOSURE_LOOKBACK_DAYS)
    items: List[EventItem] = []
    for row in rows:
        ann_date = row.get("ann_date")
        if not ann_date:
            continue
        try:
            event_date = date.fromisoformat(str(ann_date)[:10])
        except ValueError:
            continue
        if event_date < cutoff or event_date > as_of:
            continue
        direction = str(row.get("in_de") or "").upper()
        if direction not in ("IN", "DE"):
            continue  # direction unknown — nothing to flag
        holder_type = str(row.get("holder_type") or "") or "股东"
        change_ratio = row.get("change_ratio")
        action_label = "减持" if direction == "DE" else "增持"
        ratio_text = f"{change_ratio}%" if change_ratio is not None else ""
        items.append(
            EventItem(
                event_type=_EVENT_HOLDER_TRADE,
                event_date=event_date,
                title=f"{holder_type}{action_label} {ratio_text}".strip(),
                summary="",
                days_until=_days_until(event_date, as_of),
                source="tushare",
                details={
                    "in_de": direction,
                    "holder_type": holder_type,
                    "holder_name": row.get("holder_name"),
                    "change_ratio": change_ratio,
                },
            )
        )
    return items


def dragon_tiger_events(
    appearances: List[Dict[str, Any]],
    lookback_days: int = _DRAGON_TIGER_LOOKBACK_DAYS,
) -> List[EventItem]:
    """Normalize recent dragon-tiger appearance rows into ``EventItem``s.

    Appearances come from :func:`fetch_recent_board` and each carries a
    ``trade_date`` plus the market-wide ``code``. ``code_bare`` is stored so
    :func:`load_events_data` can filter the shared board per watchlist code.
    """
    as_of = date.today()
    cutoff = as_of - timedelta(days=lookback_days)
    items: List[EventItem] = []
    for row in appearances:
        code = str(row.get("code") or "")
        trade_date = row.get("trade_date")
        if not code or not trade_date:
            continue
        try:
            event_date = date.fromisoformat(str(trade_date)[:10])
        except ValueError:
            continue
        if event_date < cutoff or event_date > as_of:
            continue
        net = row.get("net_buy")
        reason = str(row.get("reason") or "")
        name = str(row.get("name") or "")
        direction = ""
        if isinstance(net, (int, float)) and net < 0:
            direction = "净卖出"
        elif isinstance(net, (int, float)) and net > 0:
            direction = "净买入"
        items.append(
            EventItem(
                event_type=_EVENT_DRAGON_TIGER,
                event_date=event_date,
                title=direction or "登榜",
                summary=f"{name} 龙虎榜{reason}",
                days_until=_days_until(event_date, as_of),
                source="eastmoney",
                details={
                    "code_bare": code,
                    "net_buy": net,
                    "reason": reason,
                    "change_pct": row.get("change_pct"),
                },
            )
        )
    return items


# ---------------------------------------------------------------------------
# Composite scoring + snapshot building
# ---------------------------------------------------------------------------


def compute_event_risk_score(items: List[EventItem]) -> Optional[float]:
    """Score an event set 0-100, or ``None`` when no item carries a score.

    Composite = dominant (max) sub-score, plus ``+5`` for every additional
    ``danger`` event beyond the one driving the max. Capped at 100. The rule is
    centralized here (like ``compute_quality_score``) so it stays explainable.
    """
    scored = [i.risk_score for i in items if i.risk_score is not None]
    if not scored:
        return None
    dominant = max(scored)
    danger = [i for i in items if i.risk_level == "danger"]
    extra = max(0, len(danger) - 1) * 5.0
    return round(_clamp(dominant + extra), 2)


def build_event_snapshot(
    items: List[EventItem],
    as_of: date,
    *,
    source: str = "unavailable",
    error: Optional[str] = None,
) -> EventSnapshot:
    """Assemble a sorted ``EventSnapshot`` from parsed event items.

    Parser-built items carry their raw ``details`` without a risk score; this is
    where ``days_until``, per-event risk scores/levels and the composite risk
    are derived from ``as_of``. Items that already carry a risk (e.g. built by
    tests / callers) are left untouched.
    """
    for item in items:
        if item.risk_score is None:
            item.risk_score = _score_event(item)
            item.risk_level = _risk_level(item.risk_score)
        else:
            item.risk_level = _risk_level(item.risk_score)
        item.days_until = _days_until(item.event_date, as_of)
    items = sorted(items, key=lambda i: (i.event_date is None, i.event_date or date.min))
    snapshot = EventSnapshot(
        as_of=as_of,
        items=items,
        source=source,
        error=error,
    )
    snapshot.event_risk_score = compute_event_risk_score(items)
    snapshot.high_risk_count = sum(1 for i in items if i.risk_level == "danger")
    return snapshot


# ---------------------------------------------------------------------------
# Orchestration (network, never raises)
# ---------------------------------------------------------------------------


class _CachedEventsEntry:
    __slots__ = ("snapshot", "expires_at")

    def __init__(self, snapshot: EventSnapshot, expires_at: float) -> None:
        self.snapshot = snapshot
        self.expires_at = expires_at


class EventsDataCache:
    """TTL cache for per-symbol event data keyed by ``(code, trading_date)``."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, _CachedEventsEntry] = {}
        self._lock = threading.Lock()

    def get(self, code: str, trading_date: date) -> Optional[EventSnapshot]:
        key = f"{code}:{trading_date.isoformat()}"
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or entry.expires_at < now:
                self._cache.pop(key, None)
                return None
            return entry.snapshot

    def set(self, code: str, trading_date: date, snapshot: EventSnapshot) -> None:
        key = f"{code}:{trading_date.isoformat()}"
        expires_at = time.monotonic() + self._ttl_seconds
        with self._lock:
            self._cache[key] = _CachedEventsEntry(snapshot, expires_at)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def _fetch_one_lockup(code: str, as_of: date, horizon_days: int) -> List[EventItem]:
    """Fetch one symbol's upcoming lockup events (Eastmoney); may raise."""
    return parse_lockup_events(
        fetch_lockup_records(code, horizon_days=horizon_days),
        as_of=as_of,
        horizon_days=horizon_days,
    )


def _fetch_one_forecast(code: str) -> List[EventItem]:
    """Fetch one symbol's recent earnings-forecast events (Tushare)."""
    return parse_forecast_events(tushare_fallbacks.fetch_forecast(code).get("rows", []))


def _fetch_one_holder_trade(code: str) -> List[EventItem]:
    """Fetch one symbol's recent shareholder trade events (Tushare)."""
    return parse_holder_trade_events(
        tushare_fallbacks.fetch_holder_trade(code).get("rows", [])
    )


def _fetch_dragon_tiger_board() -> List[EventItem]:
    """Fetch the whole-market recent dragon-tiger board once and project it."""
    return dragon_tiger_events(fetch_recent_board())


def _fetch_one_events(
    code: str,
    board_items: List[EventItem],
    as_of: date,
    horizon_days: int,
) -> EventSnapshot:
    """Assemble a single symbol's event snapshot; never raises.

    Missing sources degrade gracefully: a hard network/points failure is
    recorded in ``error`` (so the snapshot is not cached and the next refresh
    retries), while a merely-unavailable optional source (no Tushare token) is
    noted only when it leaves the snapshot with no data at all.
    """
    all_items: List[EventItem] = []
    errors: List[str] = []
    sources: List[str] = []
    tushare_unavailable = False

    def _add(source: str, items: List[EventItem]) -> None:
        all_items.extend(items)
        if items:
            sources.append(source)

    # Eastmoney source (per-symbol lockup).
    try:
        _add("eastmoney", _fetch_one_lockup(code, as_of, horizon_days))
    except Exception as exc:  # noqa: BLE001 - per-symbol isolation
        logger.warning("Lockup fetch failed for %s: %s", code, exc)
        errors.append(f"lockup: {exc}")

    # Tushare best-effort sources — token/points gaps degrade per symbol.
    try:
        _add("tushare", _fetch_one_forecast(code))
    except tushare_fallbacks.TushareFallbackUnavailable:
        tushare_unavailable = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Earnings-forecast fetch failed for %s: %s", code, exc)
        errors.append(f"forecast: {exc}")
    try:
        _add("tushare", _fetch_one_holder_trade(code))
    except tushare_fallbacks.TushareFallbackUnavailable:
        tushare_unavailable = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Holder-trade fetch failed for %s: %s", code, exc)
        errors.append(f"holder_trade: {exc}")

    # Dragon-tiger is market-wide and already fetched once per load.
    code_bare = code.split(".", 1)[0]
    dragon_items = [i for i in board_items if i.details.get("code_bare") == code_bare]
    all_items.extend(dragon_items)
    if dragon_items:
        sources.append("eastmoney")

    source = "eastmoney" if "eastmoney" in sources else "tushare" if sources else "unavailable"
    error: Optional[str] = "; ".join(errors) if errors else None
    if error is None and tushare_unavailable and not all_items:
        error = "事件源不可用（业绩预告/增减持未获取，且无解禁/龙虎榜数据）"
    return build_event_snapshot(all_items, as_of, source=source, error=error)


def load_events_data(
    codes: List[str],
    *,
    end_date: Optional[date] = None,
    cache: Optional[EventsDataCache] = None,
    horizon_days: int = _DEFAULT_HORIZON_DAYS,
) -> Dict[str, EventSnapshot]:
    """Load the event calendar for ``codes`` with caching and isolation.

    Returns a dict mapping each code to an ``EventSnapshot``. Missing or failed
    symbols are still present with ``error`` populated; the function never
    raises.
    """
    if end_date is None:
        end_date = date.today()
    cache = cache or EventsDataCache()
    results: Dict[str, EventSnapshot] = {}
    pending: List[str] = []

    for code in codes:
        cached = cache.get(code, end_date)
        if cached is not None:
            results[code] = cached
        else:
            pending.append(code)
    if not pending:
        return results

    # Whole-market dragon-tiger board: fetched once per load, filtered per code.
    board_items: List[EventItem] = []
    try:
        board_items = _fetch_dragon_tiger_board()
    except Exception:  # noqa: BLE001 - board is an enhancement, not required
        logger.warning("Dragon-tiger board fetch failed; skipping board events")

    for index, code in enumerate(pending):
        if index > 0:
            time.sleep(_REQUEST_DELAY_SECONDS)
        snapshot = _fetch_one_events(code, board_items, end_date, horizon_days)
        results[code] = snapshot
        if snapshot.error is None:
            cache.set(code, end_date, snapshot)

    return results


__all__ = [
    "EventsDataCache",
    "build_event_snapshot",
    "compute_event_risk_score",
    "dragon_tiger_events",
    "load_events_data",
    "parse_forecast_events",
    "parse_holder_trade_events",
    "parse_lockup_events",
]
