"""Unit tests for the stock tracker sector-strength helpers."""

from __future__ import annotations

import pytest

from src.stock_tracker.models import (
    CapitalMetrics,
    FundFlowSnapshot,
    PeriodMetrics,
    PeriodSignals,
    SymbolSnapshot,
    ValuationSnapshot,
)
from src.stock_tracker.sector_data import (
    aggregate_sector_metrics,
    build_sector_strength,
    compute_sector_prosperity_score,
)


# ---------------------------------------------------------------------------
# compute_sector_prosperity_score
# ---------------------------------------------------------------------------


def test_prosperity_score_weighted():
    # roe=15 -> 50.0 (w 0.4); margin=30 -> (30-5)/55*100 = 45.45 (w 0.2);
    # yoy=15 -> 50.0 (w 0.4). score = 0.4*50 + 0.2*45.45 + 0.4*50 = 49.09.
    score = compute_sector_prosperity_score(15.0, 30.0, 15.0)
    assert score == pytest.approx(49.09, abs=0.01)


def test_prosperity_score_single_dimension_renormalized():
    # Only ROE present: sub-score 50, weight 0.4 -> 50.0 after renormalization.
    assert compute_sector_prosperity_score(15.0, None, None) == pytest.approx(50.0)
    assert compute_sector_prosperity_score(None, 30.0, None) == pytest.approx(45.45, abs=0.01)
    assert compute_sector_prosperity_score(None, None, 15.0) == pytest.approx(50.0)


def test_prosperity_score_clamps_bounds():
    assert compute_sector_prosperity_score(30.0, None, None) == pytest.approx(100.0)
    assert compute_sector_prosperity_score(0.0, None, None) == pytest.approx(0.0)
    assert compute_sector_prosperity_score(-5.0, None, None) == pytest.approx(0.0)
    assert compute_sector_prosperity_score(None, 60.0, None) == pytest.approx(100.0)
    assert compute_sector_prosperity_score(None, 5.0, None) == pytest.approx(0.0)
    assert compute_sector_prosperity_score(None, 100.0, None) == pytest.approx(100.0)
    assert compute_sector_prosperity_score(None, None, 50.0) == pytest.approx(100.0)
    assert compute_sector_prosperity_score(None, None, -20.0) == pytest.approx(0.0)
    assert compute_sector_prosperity_score(None, None, -30.0) == pytest.approx(0.0)


def test_prosperity_score_all_none():
    assert compute_sector_prosperity_score(None, None, None) is None


# ---------------------------------------------------------------------------
# aggregate_sector_metrics
# ---------------------------------------------------------------------------


def _snapshot(
    code: str,
    board: str | None,
    *,
    ret: float | None = None,
    rps_m: float | None = None,
    rps_s: float | None = None,
    main_net: float | None = None,
    roe: float | None = None,
    margin: float | None = None,
    yoy: float | None = None,
) -> SymbolSnapshot:
    """Build a minimal symbol snapshot carrying optional period/capital/fundamentals."""
    period_signals = {}
    if ret is not None or rps_m is not None or rps_s is not None:
        period_signals["10"] = PeriodSignals(
            metrics=PeriodMetrics(
                period=10,
                return_pct=ret,
                rps_market=rps_m,
                rps_sector=rps_s,
            )
        )
    capital = CapitalMetrics(fund_flow=FundFlowSnapshot(main_net=main_net)) if main_net is not None else None
    valuation = None
    if any(v is not None for v in (roe, margin, yoy)):
        valuation = ValuationSnapshot(roe=roe, gross_margin=margin, revenue_yoy=yoy)
    return SymbolSnapshot(
        code=code,
        sector_board=board,
        period_signals=period_signals,
        capital=capital,
        valuation=valuation,
    )


def test_aggregate_groups_and_averages():
    snaps = [
        _snapshot("600519.SH", "白酒", ret=0.10, rps_m=80.0, rps_s=90.0, main_net=1e8, roe=25.0, margin=60.0, yoy=15.0),
        _snapshot("000858.SZ", "白酒", ret=0.05, rps_m=70.0, rps_s=80.0, main_net=2e8, roe=15.0, margin=40.0, yoy=10.0),
        _snapshot("300750.SZ", "电池", ret=0.20, rps_m=95.0, rps_s=99.0),
    ]
    agg = aggregate_sector_metrics(snaps, periods=[10])
    assert set(agg.keys()) == {"白酒", "电池"}

    baijiu = agg["白酒"]
    assert baijiu["member_count"] == 2
    assert baijiu["members"] == ["600519.SH", "000858.SZ"]
    pm10 = baijiu["period_metrics"][0]
    assert pm10["period"] == 10
    assert pm10["avg_return_pct"] == pytest.approx(0.075)
    assert pm10["avg_rps_market"] == pytest.approx(75.0)
    assert pm10["avg_rps_sector"] == pytest.approx(85.0)
    assert baijiu["total_main_net"] == pytest.approx(3e8)
    assert baijiu["avg_roe"] == pytest.approx(20.0)
    assert baijiu["avg_gross_margin"] == pytest.approx(50.0)
    assert baijiu["avg_revenue_yoy"] == pytest.approx(12.5)
    assert baijiu["prosperity_score"] is not None

    # 电池 has no fundamentals -> prosperity None, no fund-flow data.
    dianchi = agg["电池"]
    assert dianchi["member_count"] == 1
    assert dianchi["period_metrics"][0]["avg_return_pct"] == pytest.approx(0.20)
    assert dianchi["total_main_net"] is None
    assert dianchi["prosperity_score"] is None


def test_aggregate_skips_missing_values():
    snaps = [
        _snapshot("600519.SH", "白酒", ret=0.10, roe=20.0),
        _snapshot("000858.SZ", "白酒", ret=None, roe=None),
    ]
    agg = aggregate_sector_metrics(snaps, periods=[10])
    baijiu = agg["白酒"]
    assert baijiu["member_count"] == 2
    # Only the present values are averaged; missing period metrics contribute nothing.
    pm10 = baijiu["period_metrics"][0]
    assert pm10["avg_return_pct"] == pytest.approx(0.10)
    assert pm10["avg_rps_market"] is None
    assert baijiu["avg_roe"] == pytest.approx(20.0)
    assert baijiu["total_main_net"] is None


def test_aggregate_skips_unresolved_board():
    snaps = [
        _snapshot("600519.SH", "白酒", ret=0.10),
        _snapshot("000001.SZ", None, ret=0.20),
    ]
    agg = aggregate_sector_metrics(snaps, periods=[10])
    assert set(agg.keys()) == {"白酒"}


def test_aggregate_multiple_periods_trend():
    period_signals = {
        "10": PeriodSignals(metrics=PeriodMetrics(period=10, return_pct=0.05, rps_market=70.0)),
        "20": PeriodSignals(metrics=PeriodMetrics(period=20, return_pct=0.10)),
        "60": PeriodSignals(metrics=PeriodMetrics(period=60, return_pct=0.20)),
    }
    snap = SymbolSnapshot(code="600519.SH", sector_board="白酒", period_signals=period_signals)

    agg = aggregate_sector_metrics([snap], periods=[10, 20, 60])
    pm = agg["白酒"]["period_metrics"]
    assert [m["period"] for m in pm] == [10, 20, 60]
    assert [m["avg_return_pct"] for m in pm] == [pytest.approx(0.05), pytest.approx(0.10), pytest.approx(0.20)]

    # A configured period with no snapshot data stays None.
    agg2 = aggregate_sector_metrics([snap], periods=[10, 30])
    pm2 = {m["period"]: m for m in agg2["白酒"]["period_metrics"]}
    assert pm2[10]["avg_return_pct"] == pytest.approx(0.05)
    assert pm2[30]["avg_return_pct"] is None


# ---------------------------------------------------------------------------
# build_sector_strength
# ---------------------------------------------------------------------------


def test_build_strength_merges_and_ranks():
    ranking = [
        {"board_name": "白酒", "board_code": "BK0477", "change_pct": 2.5, "fund_flow_net": 1e8, "leader": "600519.SH"},
        {"board_name": "半导体", "board_code": "BK1036", "change_pct": -1.2},
    ]
    watchlist_agg = {
        "白酒": {
            "member_count": 2,
            "members": ["600519.SH", "000858.SZ"],
            "prosperity_score": 60.0,
            "period_metrics": [{"period": 10, "avg_return_pct": 0.075}],
        },
        "电池": {
            "member_count": 1,
            "members": ["300750.SZ"],
            "prosperity_score": 70.0,
            "period_metrics": [],
        },
    }
    strengths = build_sector_strength(ranking, watchlist_agg)

    assert len(strengths) == 3
    # Ranking order preserved; watchlist-only board appends at the end.
    assert [s.board_name for s in strengths] == ["白酒", "半导体", "电池"]
    assert strengths[0].market_rank == 1
    assert strengths[1].market_rank == 2
    assert strengths[2].market_rank is None

    # Matched board carries ranking + aggregates.
    assert strengths[0].board_code == "BK0477"
    assert strengths[0].change_pct == 2.5
    assert strengths[0].fund_flow_net == 1e8
    assert strengths[0].member_count == 2
    assert strengths[0].prosperity_score == 60.0
    assert strengths[0].source == "eastmoney"
    assert len(strengths[0].period_metrics) == 1
    assert strengths[0].period_metrics[0].period == 10
    assert strengths[0].period_metrics[0].avg_return_pct == pytest.approx(0.075)

    # Ranking-only board keeps ranking fields, no aggregates.
    assert strengths[1].member_count == 0
    assert strengths[1].members == []
    assert strengths[1].prosperity_score is None
    assert strengths[1].period_metrics == []

    # Watchlist-only board marks provenance.
    assert strengths[2].source == "watchlist"
    assert strengths[2].prosperity_score == 70.0


def test_build_strength_empty_ranking_keeps_watchlist():
    strengths = build_sector_strength([], {"白酒": {"member_count": 1, "members": ["600519.SH"]}})
    assert len(strengths) == 1
    assert strengths[0].board_name == "白酒"
    assert strengths[0].market_rank is None
    assert strengths[0].source == "watchlist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
