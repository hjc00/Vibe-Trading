"""Concept / thematic-board heat loader for the stock tracker (题材/概念热度).

聚合全市场概念板块排行（东财 ``clist`` ``fs=m:90+t:3``）与 watchlist 内个股的
概念归属（东财 ``slist`` ``spt=3``），产出：
- ``ConceptStrength`` 列表（板块级，镜像 :mod:`sector_data` 的行业看板，但以
  涨停家数替代基本面景气度）。
- 每只个股的 ``ConceptSnapshot``（所属概念、最热概念、热度分、涨停家数）。

设计原则（与 :mod:`src.stock_tracker.sector_data` 一致）：
- 纯函数（无网络）与编排函数分离：``compute_concept_heat_score`` /
  ``compute_concept_heat_scores`` / ``build_concept_strength`` /
  ``build_concept_snapshots`` 可独立单测。
- 概念热度分 = 0.40×概念涨幅分位 + 0.30×主力净流入分位 + 0.30×概念内涨停家数分位，
  分位由全市场概念榜横截面算得；维度缺失时重归一化。
- 概念内涨停家数复用 2.16 的市场涨停池（:func:`src.stock_tracker.sentiment_data.fetch_market_breadth`），
  零额外请求。涨停池不含概念标签时该维缺省、评分重归一化。
- 网络失败降级：排行不可用 → 仅 watchlist 聚合视图；两者皆不可用 → 空。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.stock_tracker.models import ConceptSnapshot, ConceptStrength
from src.stock_tracker.sentiment_data import fetch_market_breadth
from src.tools.sector_tool import fetch_concept_board_ranking

logger = logging.getLogger(__name__)

# Concept heat score weights. Three explainable dimensions; missing dimensions
# are dropped and the remaining weights renormalized (mirrors
# ``_PROSPERITY_WEIGHTS`` in sector_data.py).
_CONCEPT_WEIGHTS = {
    "change": 0.40,     # 概念涨幅分位
    "fund_flow": 0.30,  # 主力净流入分位
    "limit_up": 0.30,   # 概念内涨停家数分位
}


def _clamp100(value: float) -> float:
    """Clamp a 0-100 percentile into range."""
    return min(max(value, 0.0), 100.0)


def compute_concept_heat_score(
    change_pct_pctile: Optional[float],
    fund_flow_pctile: Optional[float],
    limit_up_pctile: Optional[float],
) -> Optional[float]:
    """Score concept heat 0-100 from cross-sectional percentiles.

    Each input is already a 0-100 percentile of the concept's value within the
    whole-market concept ranking (higher = hotter). Missing dimensions are
    dropped and the remaining weights renormalized; returns ``None`` when no
    dimension is available.
    """
    subs: Dict[str, float] = {}
    if change_pct_pctile is not None:
        subs["change"] = _clamp100(change_pct_pctile)
    if fund_flow_pctile is not None:
        subs["fund_flow"] = _clamp100(fund_flow_pctile)
    if limit_up_pctile is not None:
        subs["limit_up"] = _clamp100(limit_up_pctile)
    if not subs:
        return None
    total_weight = sum(_CONCEPT_WEIGHTS[name] for name in subs)
    if total_weight <= 0:
        return None
    score = sum(_CONCEPT_WEIGHTS[name] * value for name, value in subs.items())
    return round(score / total_weight, 2)


def _percentile(values: List[Optional[float]], value: Optional[float]) -> Optional[float]:
    """Midpoint-rank percentile (0-100) of ``value`` within ``values``.

    Returns ``None`` when ``value`` is ``None`` or there are no present values.
    """
    if value is None:
        return None
    present = sorted(v for v in values if v is not None)
    if not present:
        return None
    below = sum(1 for v in present if v < value)
    equal = sum(1 for v in present if v == value)
    return round((below + 0.5 * equal) / len(present) * 100, 2)


def aggregate_limit_up_by_concept(limit_up_rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count limit-up stocks per concept from the market limit-up pool.

    Each ``limit_up_rows`` entry carries a best-effort ``concepts`` tag list
    (empty when the source does not expose concept membership). Rows with no
    concept tags contribute nothing.
    """
    counts: Dict[str, int] = {}
    for row in limit_up_rows:
        for concept in row.get("concepts") or []:
            name = str(concept).strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


def compute_concept_heat_scores(
    ranking: List[Dict[str, Any]],
    limit_up_by_concept: Dict[str, int],
) -> Dict[str, float]:
    """Compute a cross-sectional heat score per concept board.

    Each board's change-pct / fund-flow / limit-up-count percentile is measured
    against the whole ranking. When the limit-up pool carries no concept tags,
    that dimension is treated as missing (not uniformly zero) so the score
    renormalizes over the remaining dimensions.
    """
    change_pcts = [r.get("change_pct") for r in ranking]
    fund_flows = [r.get("fund_flow_net") for r in ranking]
    has_limit_up = bool(limit_up_by_concept)
    limit_ups = [
        (limit_up_by_concept.get(r.get("board_name"), 0) if has_limit_up else None)
        for r in ranking
    ]

    scores: Dict[str, float] = {}
    for index, row in enumerate(ranking):
        name = row.get("board_name")
        if not name:
            continue
        score = compute_concept_heat_score(
            _percentile(change_pcts, change_pcts[index]),
            _percentile(fund_flows, fund_flows[index]),
            _percentile(limit_ups, limit_ups[index]),
        )
        if score is not None:
            scores[str(name)] = score
    return scores


def aggregate_concept_membership(
    boards_map: Dict[str, List[str]],
) -> Dict[str, Dict[str, Any]]:
    """Group watchlist symbols by their concept boards.

    ``boards_map`` maps each code to its resolved concept-board names. Returns
    ``{concept_name: {"members": [...], "member_count": int}}``.
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for code, boards in boards_map.items():
        for board in boards:
            entry = agg.setdefault(board, {"members": [], "member_count": 0})
            entry["members"].append(code)
    for entry in agg.values():
        entry["member_count"] = len(entry["members"])
    return agg


def build_concept_strength(
    ranking: List[Dict[str, Any]],
    membership_agg: Dict[str, Dict[str, Any]],
    limit_up_by_concept: Dict[str, int],
) -> List[ConceptStrength]:
    """Merge the whole-market concept ranking with watchlist membership.

    Ranking boards keep their change-pct order with a 1-based ``market_rank``;
    watchlist-only concepts (missing from the ranking) append at the end with
    ``market_rank`` ``None``. Pure function — no network.
    """
    strengths: List[ConceptStrength] = []
    seen: set[str] = set()

    for rank, row in enumerate(ranking, start=1):
        name = row.get("board_name")
        if not name:
            continue
        name = str(name)
        seen.add(name)
        agg = membership_agg.get(name, {})
        strengths.append(
            ConceptStrength(
                board_code=row.get("board_code"),
                board_name=name,
                change_pct=row.get("change_pct"),
                fund_flow_net=row.get("fund_flow_net"),
                up_count=row.get("up_count"),
                down_count=row.get("down_count"),
                leader=row.get("leader"),
                limit_up_count=limit_up_by_concept.get(name),
                market_rank=rank,
                member_count=agg.get("member_count", 0),
                members=agg.get("members", []),
                source="eastmoney",
            )
        )

    for name, agg in membership_agg.items():
        if name in seen:
            continue
        strengths.append(
            ConceptStrength(
                board_name=name,
                member_count=agg.get("member_count", 0),
                members=agg.get("members", []),
                source="watchlist",
            )
        )
    return strengths


def build_concept_snapshots(
    boards_map: Dict[str, List[str]],
    ranking: List[Dict[str, Any]],
    heat_scores: Dict[str, float],
    limit_up_by_concept: Dict[str, int],
) -> Dict[str, ConceptSnapshot]:
    """Build per-symbol concept snapshots from resolved boards + ranking.

    The symbol's hottest concept is its board with the lowest (best) market rank
    among those present in the whole-market ranking; its heat score and limit-up
    count are attached. Symbols whose concepts fall outside the ranking keep
    ``boards`` but leave the hottest/heat fields ``None``.
    """
    rank_by_name: Dict[str, int] = {}
    for index, row in enumerate(ranking, start=1):
        name = row.get("board_name")
        if name:
            rank_by_name[str(name)] = index

    snapshots: Dict[str, ConceptSnapshot] = {}
    for code, boards in boards_map.items():
        snapshot = ConceptSnapshot()
        snapshot.boards = list(boards)
        if not boards:
            snapshot.error = "no concept board membership"
            snapshots[code] = snapshot
            continue

        best_name: Optional[str] = None
        best_rank: Optional[int] = None
        for board in boards:
            rank = rank_by_name.get(board)
            if rank is not None and (best_rank is None or rank < best_rank):
                best_rank = rank
                best_name = board

        if best_name is None:
            snapshot.source = "eastmoney" if ranking else "unavailable"
            snapshots[code] = snapshot
            continue

        snapshot.hottest_concept = best_name
        snapshot.hottest_concept_rank = best_rank
        snapshot.concept_heat_score = heat_scores.get(best_name)
        snapshot.limit_up_count = limit_up_by_concept.get(best_name)
        snapshot.source = "eastmoney"
        snapshots[code] = snapshot
    return snapshots


def load_concept_data(
    boards_map: Dict[str, List[str]],
    *,
    limit: int = 50,
    ranking: Optional[List[Dict[str, Any]]] = None,
    breadth: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ConceptStrength], Dict[str, ConceptSnapshot]]:
    """Load concept heat: board list + per-symbol snapshots.

    Fetches the whole-market concept ranking and the market limit-up pool when
    not supplied, then computes cross-sectional heat scores and merges watchlist
    membership. Never raises: network failures degrade to a watchlist-only or
    empty view.

    Args:
        boards_map: ``{code: [concept_name, ...]}`` resolved per symbol.
        limit: Max whole-market concept boards to fetch (only used when
            ``ranking`` is not supplied).
        ranking: Optional pre-fetched concept ranking (same shape as
            :func:`src.tools.sector_tool.fetch_concept_board_ranking`).
        breadth: Optional pre-fetched market breadth frame (from
            :func:`src.stock_tracker.sentiment_data.fetch_market_breadth`), so a
            single refresh shares one limit-up-pool fetch across sentiment +
            concept.

    Returns:
        ``(concepts, snapshots)`` where ``concepts`` is the ``ConceptStrength``
        list and ``snapshots`` maps each code to its ``ConceptSnapshot``.
    """
    if ranking is None:
        try:
            ranking = fetch_concept_board_ranking(limit)
        except Exception as exc:  # noqa: BLE001 - degraded to watchlist-only view
            logger.warning("Concept board ranking fetch failed: %s", exc)
            ranking = []
    if breadth is None:
        try:
            breadth = fetch_market_breadth()
        except Exception as exc:  # noqa: BLE001 - degraded to no limit-up counts
            logger.warning("Market breadth fetch failed: %s", exc)
            breadth = {"source": "unavailable", "limit_up_rows": []}

    limit_up_by_concept = aggregate_limit_up_by_concept(
        breadth.get("limit_up_rows", [])
    )
    heat_scores = compute_concept_heat_scores(ranking, limit_up_by_concept)
    membership_agg = aggregate_concept_membership(boards_map)

    strengths = build_concept_strength(ranking, membership_agg, limit_up_by_concept)
    snapshots = build_concept_snapshots(
        boards_map, ranking, heat_scores, limit_up_by_concept
    )
    return strengths, snapshots


__all__ = [
    "aggregate_concept_membership",
    "aggregate_limit_up_by_concept",
    "build_concept_snapshots",
    "build_concept_strength",
    "compute_concept_heat_score",
    "compute_concept_heat_scores",
    "load_concept_data",
]
