"""Market-breadth / sentiment thermometer loader for the stock tracker.

聚合全市场涨停/跌停/炸板池与连板天梯，产出 ``MarketSentimentSnapshot``
（市场情绪温度计）。同时暴露 :func:`fetch_market_breadth`，为
:mod:`src.stock_tracker.concept_data` 的概念内涨停家数提供同一份涨停池，
避免重复请求（2.15 复用 2.16 的市场涨停池）。

数据源（多源降级，永不抛异常）：
- 主源：东财 ``push2ex`` ``getTopicZTPool``（半开放、无 token），返回当日涨停池
  及每只个股的连板数 / 炸板次数 / 行业标签。
- 兜底：Tushare ``limit_list_d``（涨停/跌停/炸板池，5000 积分）+ ``limit_step``
  （连板天梯，8000 积分）。token 缺失时 ``TushareFallbackUnavailable`` →
  ``source="unavailable"``。

口径与降级：
- ``sentiment_score`` 为 0-100 温度（越高越热），权重 0.30/0.25/0.25/0.20，
  维度缺失时重归一化（与 ``compute_sector_prosperity_score`` 一致）。
- 各分位（涨停家数/连板高度/昨日涨停溢价）理想上应相对近 20 日历史；当前快照
  仅存当日，故首版用固定参考刻度近似（见 :func:`estimate_sentiment_percentiles`），
  接入近 20 日历史后仅需替换该函数、纯评分函数不变。
- ``up_count``/``down_count``/``prev_limit_up_perf`` 依赖主源额外字段，不可用时
  降级为 ``None``，评分自动重归一化。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backtest.loaders.eastmoney_client import get_json
from src.stock_tracker.models import MarketSentimentSnapshot
from src.tools import tushare_fallbacks

logger = logging.getLogger(__name__)

# Eastmoney limit-up pool endpoint (semi-open, no token). ``getTopicZTPool``
# returns the daily limit-up pool; field names are short (``c``/``n``/``lbc``/
# ``zbc``/``hybk``) and are probed defensively at parse time.
_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_POOL_UT = "7eea3edcaed734bea9cbfc24409ed989"
_POOL_PAGE_SIZE = 1000

# Sentiment score weights. Four explainable dimensions; missing dimensions are
# dropped and the remaining weights renormalized (mirrors ``_PROSPERITY_WEIGHTS``).
_SENTIMENT_WEIGHTS = {
    "limit_up": 0.30,    # 涨停家数分位
    "non_broken": 0.25,  # 1 - 炸板率
    "ladder": 0.25,      # 连板高度分位
    "prev_perf": 0.20,   # 昨日涨停溢价分位
}


def _clamp01(value: float) -> float:
    """Clamp a 0-1 fraction into range."""
    return min(max(value, 0.0), 1.0)


def _clamp100(value: float) -> float:
    """Clamp a 0-100 percentile into range."""
    return min(max(value, 0.0), 100.0)


def compute_sentiment_score(
    limit_up_pctile: Optional[float],
    broken_ratio: Optional[float],
    ladder_height_pctile: Optional[float],
    prev_perf_pctile: Optional[float],
) -> Optional[float]:
    """Score market sentiment 0-100 from breadth + limit-up temperature.

    ``limit_up_pctile`` / ``ladder_height_pctile`` / ``prev_perf_pctile`` are
    0-100 percentiles; ``broken_ratio`` is a 0-1 fraction (炸板率) converted
    internally to a ``1 - ratio`` 0-100 sub-score (fewer broken boards = hotter).
    Missing dimensions are dropped and the remaining weights renormalized;
    returns ``None`` when no dimension is available.
    """
    subs: Dict[str, float] = {}
    if limit_up_pctile is not None:
        subs["limit_up"] = _clamp100(limit_up_pctile)
    if broken_ratio is not None:
        subs["non_broken"] = round((1.0 - _clamp01(broken_ratio)) * 100, 2)
    if ladder_height_pctile is not None:
        subs["ladder"] = _clamp100(ladder_height_pctile)
    if prev_perf_pctile is not None:
        subs["prev_perf"] = _clamp100(prev_perf_pctile)
    if not subs:
        return None
    total_weight = sum(_SENTIMENT_WEIGHTS[name] for name in subs)
    if total_weight <= 0:
        return None
    score = sum(_SENTIMENT_WEIGHTS[name] * value for name, value in subs.items())
    return round(score / total_weight, 2)


def estimate_sentiment_percentiles(
    limit_up_count: Optional[int],
    max_board_height: Optional[int],
    prev_limit_up_perf: Optional[float],
) -> Dict[str, Optional[float]]:
    """Map absolute breadth values to 0-100 percentiles on fixed reference scales.

    Approximates the "relative to the trailing 20 trading days" percentile the
    plan specifies, using fixed reference scales until 20-day history is wired
    into the loader. Scales (documented, tunable):
    - 涨停家数: 0 -> 0, 150 -> 100 (A-share markets rarely exceed ~150 limit-ups).
    - 连板高度: 1 -> 0, 10 -> 100 (a 10+ 连板 is an extreme market).
    - 昨日涨停溢价: -3% -> 0, +3% -> 100.

    Returns a dict of ``{"limit_up": ..., "ladder": ..., "prev_perf": ...}``
    where missing inputs map to ``None``.
    """
    limit_up = None
    if limit_up_count is not None:
        limit_up = _clamp100(limit_up_count / 150.0 * 100)
    ladder = None
    if max_board_height is not None and max_board_height >= 1:
        ladder = _clamp100((max_board_height - 1) / 9.0 * 100)
    prev_perf = None
    if prev_limit_up_perf is not None:
        prev_perf = _clamp100((prev_limit_up_perf + 0.03) / 0.06 * 100)
    return {"limit_up": limit_up, "ladder": ladder, "prev_perf": prev_perf}


def _first(row: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first present, non-empty value among ``keys`` in ``row``."""
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _to_float(value: Any) -> Optional[float]:
    """Coerce a cell to ``float``, or ``None`` when absent/non-numeric."""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    """Coerce a cell to ``int``, or ``None`` when absent/non-numeric."""
    num = _to_float(value)
    return int(round(num)) if num is not None else None


def _parse_push2ex_pool(payload: Any) -> List[Dict[str, Any]]:
    """Extract limit-up pool rows from a ``getTopicZTPool`` payload.

    Each row is normalized to ``{code, name, board_height, broken, concepts}``.
    Field names vary by endpoint revision, so short (``c``/``n``/``lbc``) and
    long aliases are probed. Concept tags are best-effort: the pool reliably
    exposes an industry tag (``hybk``) but rarely the full concept list, so
    ``concepts`` is usually empty and the concept heat limit-up dimension
    degrades. Never raises; a bad row is skipped.
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        pool = data.get("pool")
    else:
        pool = None
    if not isinstance(pool, list):
        return []

    rows: List[Dict[str, Any]] = []
    for raw in pool:
        if not isinstance(raw, dict):
            continue
        code = _first(raw, ("c", "code", "f12", "sc"))
        name = _first(raw, ("n", "name", "f14"))
        if code is None and name is None:
            continue
        board_height = _to_int(_first(raw, ("lbc", "limit_times", "zttj")))
        broken = _to_int(_first(raw, ("zbc", "open_times", "zb")))
        concepts: List[str] = []
        for key in ("concepts", "concept", "gntk"):
            value = raw.get(key)
            if isinstance(value, list):
                concepts.extend(str(v) for v in value if v)
            elif isinstance(value, str) and value.strip():
                concepts.append(value.strip())
        rows.append(
            {
                "code": str(code).split(".", 1)[0] if code else None,
                "name": name,
                "board_height": board_height,
                "broken": broken is not None and broken > 0,
                "concepts": concepts,
            }
        )
    return rows


def _parse_push2ex_meta(payload: Any) -> Dict[str, Any]:
    """Extract optional whole-market breadth meta from a ``getTopicZTPool`` payload.

    ``up_count``/``down_count`` (涨跌家数) are returned by some endpoint
    revisions under ``data``; absent when the revision drops them. Never raises.
    """
    meta: Dict[str, Any] = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        for out_key, keys in (
            ("up_count", ("up_num", "up", "zs")),
            ("down_count", ("down_num", "down", "xd")),
        ):
            meta[out_key] = _to_int(_first(data, keys))
    return meta


def _fetch_push2ex() -> Optional[Dict[str, Any]]:
    """Fetch the Eastmoney limit-up pool, or ``None`` on failure. Never raises."""
    try:
        payload = get_json(
            _POOL_URL,
            params={
                "ut": _POOL_UT,
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": str(_POOL_PAGE_SIZE),
                "sort": "fbt:asc",
            },
        )
    except Exception as exc:  # noqa: BLE001 - degraded to tushare fallback
        logger.warning("eastmoney limit-up pool fetch failed: %s", exc)
        return None

    rows = _parse_push2ex_pool(payload)
    meta = _parse_push2ex_meta(payload)
    if not rows and not meta:
        return None
    return {"source": "eastmoney", "rows": rows, "meta": meta}


def _fetch_tushare() -> Optional[Dict[str, Any]]:
    """Fetch the limit-up pool via Tushare fallback, or ``None``. Never raises."""
    try:
        limit_list = tushare_fallbacks.fetch_limit_list(lookback_days=3)
    except tushare_fallbacks.TushareFallbackUnavailable:
        return None
    except Exception as exc:  # noqa: BLE001 - degraded
        logger.warning("tushare limit_list fetch failed: %s", exc)
        return None

    rows: List[Dict[str, Any]] = []
    for row in limit_list.get("rows", []):
        code = row.get("ts_code")
        if not code:
            continue
        limit_type = row.get("limit_type")
        rows.append(
            {
                "code": str(code).split(".", 1)[0],
                "name": row.get("name"),
                "board_height": _to_int(row.get("limit_times")),
                "broken": limit_type == "Z" or _to_int(row.get("open_times")) or 0 > 0,
                "limit_type": limit_type,
                "concepts": [],
            }
        )
    return {"source": "tushare", "rows": rows, "meta": {}}


def fetch_market_breadth() -> Dict[str, Any]:
    """Fetch the whole-market limit-up/broken-board pool as a normalized frame.

    Returns ``{source, limit_up, limit_down, broken_board, board_ladder,
    up_count, down_count, prev_limit_up_perf, limit_up_rows}``. ``limit_up`` /
    ``limit_down`` / ``broken_board`` are lists of ``{code, name}``;
    ``limit_up_rows`` carries the fuller per-stock rows (with ``board_height``
    and best-effort ``concepts``) for concept aggregation. ``source`` is
    ``"eastmoney"``, ``"tushare"``, or ``"unavailable"`` (when both fail).
    Never raises.
    """
    frame: Dict[str, Any] = {
        "source": "unavailable",
        "limit_up": [],
        "limit_down": [],
        "broken_board": [],
        "board_ladder": {},
        "up_count": None,
        "down_count": None,
        "prev_limit_up_perf": None,
        "limit_up_rows": [],
    }

    raw = _fetch_push2ex()
    if raw is None:
        raw = _fetch_tushare()
    if raw is None:
        return frame

    rows = raw.get("rows", [])
    meta = raw.get("meta", {})
    frame["source"] = raw.get("source", "unavailable")
    frame["up_count"] = meta.get("up_count")
    frame["down_count"] = meta.get("down_count")

    limit_up_rows: List[Dict[str, Any]] = []
    ladder: Dict[str, int] = {}
    for row in rows:
        code = row.get("code")
        name = row.get("name")
        entry = {"code": code, "name": name}
        limit_type = row.get("limit_type")
        broken = bool(row.get("broken"))
        if limit_type == "D":
            frame["limit_down"].append(entry)
            continue
        if limit_type == "Z" or (broken and limit_type is None):
            frame["broken_board"].append(entry)
            continue
        # Limit-up (U) or unclassified push2ex rows (default limit-up).
        frame["limit_up"].append(entry)
        limit_up_rows.append(row)
        height = row.get("board_height")
        if height is not None:
            key = str(int(height))
            ladder[key] = ladder.get(key, 0) + 1

    frame["limit_up_rows"] = limit_up_rows
    if ladder:
        frame["board_ladder"] = {
            str(k): ladder[k] for k in sorted(ladder, key=lambda x: int(x))
        }
        frame["max_board_height"] = max(int(k) for k in ladder)
    else:
        frame["max_board_height"] = None

    return frame


def load_market_sentiment(breadth: Optional[Dict[str, Any]] = None) -> MarketSentimentSnapshot:
    """Build the market-sentiment snapshot from the market breadth frame.

    ``breadth`` defaults to a fresh :func:`fetch_market_breadth` call when
    omitted. Never raises; a fully unavailable frame yields a snapshot with
    ``source="unavailable"`` and an ``error``.
    """
    if breadth is None:
        breadth = fetch_market_breadth()

    snapshot = MarketSentimentSnapshot()
    if breadth.get("source") == "unavailable":
        snapshot.error = "no market-breadth source available"
        return snapshot

    snapshot.source = breadth.get("source", "unavailable")
    snapshot.limit_up_count = len(breadth.get("limit_up", [])) or None
    snapshot.limit_down_count = len(breadth.get("limit_down", [])) or None
    snapshot.broken_board_count = len(breadth.get("broken_board", [])) or None
    snapshot.up_count = breadth.get("up_count")
    snapshot.down_count = breadth.get("down_count")
    snapshot.prev_limit_up_perf = breadth.get("prev_limit_up_perf")
    snapshot.max_board_height = breadth.get("max_board_height")
    ladder = breadth.get("board_ladder") or {}
    if ladder:
        snapshot.board_ladder = {str(k): int(v) for k, v in ladder.items()}

    limit_up = snapshot.limit_up_count or 0
    broken = snapshot.broken_board_count or 0
    total_attempts = limit_up + broken
    snapshot.broken_ratio = round(broken / total_attempts, 4) if total_attempts else None

    pctiles = estimate_sentiment_percentiles(
        snapshot.limit_up_count,
        snapshot.max_board_height,
        snapshot.prev_limit_up_perf,
    )
    snapshot.sentiment_score = compute_sentiment_score(
        pctiles["limit_up"],
        snapshot.broken_ratio,
        pctiles["ladder"],
        pctiles["prev_perf"],
    )
    return snapshot


__all__ = [
    "compute_sentiment_score",
    "estimate_sentiment_percentiles",
    "fetch_market_breadth",
    "load_market_sentiment",
]
