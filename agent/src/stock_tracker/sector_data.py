"""Sector / industry-board strength loader for the stock tracker.

聚合全市场行业板块排行（东财 clist 涨跌幅 + 主力净流入）与 watchlist 内个股
指标，产出 ``SectorStrength`` 列表，供「行业/板块强度看板」使用。

设计原则（与 :mod:`src.stock_tracker.valuation_data` 一致）：
- 纯函数（无网络）与编排函数分离：``compute_sector_prosperity_score`` /
  ``aggregate_sector_metrics`` / ``build_sector_strength`` 可独立单测。
- 网络仅经 :func:`src.tools.sector_tool.fetch_industry_board_ranking`
  （单一数据源，共享东财节流）。
- ``load_sector_strength`` 永不抛异常：网络失败降级为仅 watchlist 聚合视图，
  聚合失败降级为仅全市场排行视图，两者皆失败返回空列表。

口径说明：
- ROE / 毛利率 / 营收增速均为百分数（如 16.75 表示 16.75%），与
  ``ValuationSnapshot`` 的字段口径一致。
"""

from __future__ import annotations

import logging
from statistics import mean
from typing import Any, Dict, List, Optional

from src.stock_tracker.models import SectorPeriodMetric, SectorStrength, SymbolSnapshot
from src.tools.sector_tool import fetch_industry_board_ranking

logger = logging.getLogger(__name__)

# Prosperity score weights. Three explainable dimensions; missing dimensions are
# dropped and the remaining weights renormalized (mirrors ``_QUALITY_WEIGHTS``
# in valuation_data.py).
_PROSPERITY_WEIGHTS = {
    "revenue_yoy": 0.40,
    "roe": 0.40,
    "gross_margin": 0.20,
}


def _clamp01(value: float) -> float:
    """Clamp a 0-1 fraction into range."""
    return min(max(value, 0.0), 1.0)


def _sub_roe(roe: float) -> float:
    """ROE sub-score 0-100: linear over 0-30%."""
    return round(_clamp01(roe / 30.0) * 100, 2)


def _sub_gross_margin(margin: float) -> float:
    """Gross-margin sub-score 0-100: linear over 5-60%."""
    return round(_clamp01((margin - 5.0) / 55.0) * 100, 2)


def _sub_revenue_yoy(yoy: float) -> float:
    """Revenue-growth sub-score 0-100: linear over -20%..+50%."""
    return round(_clamp01((yoy + 20.0) / 70.0) * 100, 2)


def compute_sector_prosperity_score(
    roe: Optional[float],
    gross_margin: Optional[float],
    revenue_yoy: Optional[float],
) -> Optional[float]:
    """Score sector prosperity 0-100 from ROE, gross margin, revenue growth.

    Sub-scores are linearly clamped: ROE 0-30%, gross margin 5-60%, revenue
    YoY -20%..+50%. Missing dimensions are dropped and the remaining weights
    renormalized; returns ``None`` when no dimension is available.
    """
    subs: Dict[str, float] = {}
    if roe is not None:
        subs["roe"] = _sub_roe(roe)
    if gross_margin is not None:
        subs["gross_margin"] = _sub_gross_margin(gross_margin)
    if revenue_yoy is not None:
        subs["revenue_yoy"] = _sub_revenue_yoy(revenue_yoy)
    if not subs:
        return None
    total_weight = sum(_PROSPERITY_WEIGHTS[name] for name in subs)
    if total_weight <= 0:
        return None
    score = sum(_PROSPERITY_WEIGHTS[name] * value for name, value in subs.items())
    return round(score / total_weight, 2)


def _mean_non_none(values: List[Optional[float]]) -> Optional[float]:
    """Mean of present (non-None) values, or ``None`` when all are missing."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(float(mean(present)), 4)


def _sum_non_none(values: List[Optional[float]]) -> Optional[float]:
    """Sum of present (non-None) values, or ``None`` when all are missing."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(float(sum(present)), 2)


def aggregate_sector_metrics(
    snapshots: List[SymbolSnapshot],
    periods: List[int],
) -> Dict[str, Dict[str, Any]]:
    """Aggregate per-board watchlist metrics keyed by board name.

    Groups ``snapshots`` by ``sector_board`` and computes member lists, average
    period return / RPS per configured period, total main-force net inflow,
    average fundamentals, and a prosperity score per board. Snapshots without a
    resolved board are skipped. Pure function — no network.

    Args:
        snapshots: Symbol snapshots to aggregate.
        periods: Trading-day windows used for return / RPS aggregation, in
            ascending order.

    Returns:
        ``{board_name: {...}}`` with keys ``members``, ``member_count``,
        ``total_main_net``, ``avg_roe``, ``avg_gross_margin``,
        ``avg_revenue_yoy``, ``prosperity_score``, and ``period_metrics`` — a
        list of ``{period, avg_return_pct, avg_rps_market, avg_rps_sector}``
        with one entry per configured period.
    """
    groups: Dict[str, List[SymbolSnapshot]] = {}
    for snap in snapshots:
        board = snap.sector_board
        if board:
            groups.setdefault(board, []).append(snap)

    result: Dict[str, Dict[str, Any]] = {}
    for board, members in groups.items():
        main_nets: List[Optional[float]] = []
        roes: List[Optional[float]] = []
        margins: List[Optional[float]] = []
        revenue_yoys: List[Optional[float]] = []
        period_accum: Dict[int, Dict[str, List[Optional[float]]]] = {
            p: {"returns": [], "rps_market": [], "rps_sector": []} for p in periods
        }
        for snap in members:
            for p in periods:
                ps = snap.period_signals.get(str(p))
                if ps is None:
                    continue
                acc = period_accum[p]
                acc["returns"].append(ps.metrics.return_pct)
                acc["rps_market"].append(ps.metrics.rps_market)
                acc["rps_sector"].append(ps.metrics.rps_sector)
            capital = snap.capital
            if capital is not None:
                main_nets.append(capital.fund_flow.main_net)
            valuation = snap.valuation
            if valuation is not None:
                roes.append(valuation.roe)
                margins.append(valuation.gross_margin)
                revenue_yoys.append(valuation.revenue_yoy)

        agg = {
            "members": [s.code for s in members],
            "member_count": len(members),
            "total_main_net": _sum_non_none(main_nets),
            "avg_roe": _mean_non_none(roes),
            "avg_gross_margin": _mean_non_none(margins),
            "avg_revenue_yoy": _mean_non_none(revenue_yoys),
            "period_metrics": [
                {
                    "period": p,
                    "avg_return_pct": _mean_non_none(acc["returns"]),
                    "avg_rps_market": _mean_non_none(acc["rps_market"]),
                    "avg_rps_sector": _mean_non_none(acc["rps_sector"]),
                }
                for p, acc in period_accum.items()
            ],
        }
        agg["prosperity_score"] = compute_sector_prosperity_score(
            agg["avg_roe"],
            agg["avg_gross_margin"],
            agg["avg_revenue_yoy"],
        )
        result[board] = agg
    return result


def _agg_period_metrics(agg: Dict[str, Any]) -> List[SectorPeriodMetric]:
    """Convert raw per-period aggregates into model instances, ordered by period."""
    return [
        SectorPeriodMetric(
            period=int(pm["period"]),
            avg_return_pct=pm.get("avg_return_pct"),
            avg_rps_market=pm.get("avg_rps_market"),
            avg_rps_sector=pm.get("avg_rps_sector"),
        )
        for pm in agg.get("period_metrics", [])
    ]


def build_sector_strength(
    board_ranking: List[Dict[str, Any]],
    watchlist_agg: Dict[str, Dict[str, Any]],
) -> List[SectorStrength]:
    """Merge whole-market board ranking with watchlist aggregates.

    Boards are matched by ``board_name``. Ranking boards keep their change-pct
    order with a 1-based ``market_rank``; watchlist-only boards (missing from
    the ranking, e.g. after a fetch failure) append at the end with
    ``market_rank`` ``None``. Pure function — no network.
    """
    strengths: List[SectorStrength] = []
    seen: set[str] = set()

    for rank, row in enumerate(board_ranking, start=1):
        name = row.get("board_name")
        if not name:
            continue
        name = str(name)
        seen.add(name)
        agg = watchlist_agg.get(name, {})
        strengths.append(
            SectorStrength(
                board_code=row.get("board_code"),
                board_name=name,
                change_pct=row.get("change_pct"),
                fund_flow_net=row.get("fund_flow_net"),
                up_count=row.get("up_count"),
                down_count=row.get("down_count"),
                leader=row.get("leader"),
                market_rank=rank,
                member_count=agg.get("member_count", 0),
                members=agg.get("members", []),
                period_metrics=_agg_period_metrics(agg),
                total_main_net=agg.get("total_main_net"),
                prosperity_score=agg.get("prosperity_score"),
                avg_roe=agg.get("avg_roe"),
                avg_gross_margin=agg.get("avg_gross_margin"),
                avg_revenue_yoy=agg.get("avg_revenue_yoy"),
                source="eastmoney",
            )
        )

    for name, agg in watchlist_agg.items():
        if name in seen:
            continue
        strengths.append(
            SectorStrength(
                board_name=name,
                member_count=agg.get("member_count", 0),
                members=agg.get("members", []),
                period_metrics=_agg_period_metrics(agg),
                total_main_net=agg.get("total_main_net"),
                prosperity_score=agg.get("prosperity_score"),
                avg_roe=agg.get("avg_roe"),
                avg_gross_margin=agg.get("avg_gross_margin"),
                avg_revenue_yoy=agg.get("avg_revenue_yoy"),
                source="watchlist",
            )
        )
    return strengths


def load_sector_strength(
    snapshots: List[SymbolSnapshot],
    *,
    periods: List[int],
    limit: int = 50,
    ranking: Optional[List[Dict[str, Any]]] = None,
) -> List[SectorStrength]:
    """Load the combined sector-strength list for the tracker dashboard.

    Fetches the whole-market board ranking (Eastmoney) and merges watchlist
    aggregates across every configured period. Never raises: network or
    aggregation failures degrade to the watchlist-only or ranking-only view
    (``[]`` when both fail).

    Args:
        snapshots: Current symbol snapshots to aggregate per board.
        periods: Trading-day windows for the per-period trend comparison.
        limit: Max whole-market boards to fetch. Only used when ``ranking`` is
            not supplied.
        ranking: Optional pre-fetched whole-market board ranking rows (same shape
            as :func:`src.tools.sector_tool.fetch_industry_board_ranking`). When
            supplied the Eastmoney ranking fetch is skipped; pass ``None`` to
            fetch fresh.

    Returns:
        Boards sorted by ``change_pct`` descending (``market_rank`` 1-based);
        watchlist boards missing from the ranking append at the end.
    """
    if ranking is None:
        try:
            ranking = fetch_industry_board_ranking(limit)
        except Exception as exc:  # noqa: BLE001 - degraded to watchlist-only view
            logger.warning("Sector board ranking fetch failed: %s", exc)
            ranking = []

    watchlist_agg: Dict[str, Dict[str, Any]] = {}
    try:
        watchlist_agg = aggregate_sector_metrics(snapshots, periods)
    except Exception as exc:  # noqa: BLE001 - degraded to ranking-only view
        logger.warning("Sector aggregation failed: %s", exc)

    return build_sector_strength(ranking, watchlist_agg)


__all__ = [
    "aggregate_sector_metrics",
    "build_sector_strength",
    "compute_sector_prosperity_score",
    "load_sector_strength",
]
