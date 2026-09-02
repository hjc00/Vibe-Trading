"""Unit tests for the stock tracker concept-heat loader."""

from __future__ import annotations

import pytest

from src.stock_tracker.concept_data import (
    _percentile,
    aggregate_concept_membership,
    aggregate_limit_up_by_concept,
    build_concept_snapshots,
    build_concept_strength,
    compute_concept_heat_score,
    compute_concept_heat_scores,
)


# ---------------------------------------------------------------------------
# compute_concept_heat_score
# ---------------------------------------------------------------------------


def test_concept_heat_score_weighted():
    # All dimensions at 50 -> 50.
    assert compute_concept_heat_score(50.0, 50.0, 50.0) == pytest.approx(50.0)


def test_concept_heat_score_single_dimension_renormalized():
    assert compute_concept_heat_score(50.0, None, None) == pytest.approx(50.0)
    assert compute_concept_heat_score(None, 50.0, None) == pytest.approx(50.0)
    assert compute_concept_heat_score(None, None, 50.0) == pytest.approx(50.0)


def test_concept_heat_score_clamps():
    assert compute_concept_heat_score(200.0, None, None) == pytest.approx(100.0)
    assert compute_concept_heat_score(-50.0, None, None) == pytest.approx(0.0)


def test_concept_heat_score_all_none():
    assert compute_concept_heat_score(None, None, None) is None


# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------


def test_percentile_midpoint_rank():
    values = [0.0, 1.0, 2.0, 3.0]
    assert _percentile(values, 3.0) == pytest.approx(87.5)  # (3 + 0.5*1)/4
    assert _percentile(values, 0.0) == pytest.approx(12.5)
    assert _percentile(values, None) is None
    assert _percentile([], 1.0) is None
    assert _percentile([None, None], 1.0) is None


# ---------------------------------------------------------------------------
# aggregate_limit_up_by_concept
# ---------------------------------------------------------------------------


def test_aggregate_limit_up_counts():
    rows = [
        {"concepts": ["AI", "机器人"]},
        {"concepts": ["AI"]},
        {"concepts": []},
        {},
    ]
    counts = aggregate_limit_up_by_concept(rows)
    assert counts == {"AI": 2, "机器人": 1}


# ---------------------------------------------------------------------------
# aggregate_concept_membership
# ---------------------------------------------------------------------------


def test_aggregate_concept_membership():
    boards_map = {
        "600519.SH": ["白酒", "消费"],
        "000858.SZ": ["白酒"],
    }
    agg = aggregate_concept_membership(boards_map)
    assert agg["白酒"] == {"members": ["600519.SH", "000858.SZ"], "member_count": 2}
    assert agg["消费"]["members"] == ["600519.SH"]


# ---------------------------------------------------------------------------
# build_concept_strength
# ---------------------------------------------------------------------------


def test_build_concept_strength_merges_and_ranks():
    ranking = [
        {"board_name": "AI", "board_code": "BK0800", "change_pct": 3.0, "fund_flow_net": 1e8, "leader": "002230.SZ"},
        {"board_name": "白酒", "board_code": "BK0477", "change_pct": 1.0},
    ]
    membership = {"白酒": {"members": ["600519.SH"], "member_count": 1}}
    limit_up = {"AI": 5}
    strengths = build_concept_strength(ranking, membership, limit_up)

    assert len(strengths) == 2
    assert [s.board_name for s in strengths] == ["AI", "白酒"]
    assert strengths[0].market_rank == 1
    assert strengths[0].limit_up_count == 5
    assert strengths[0].member_count == 0
    assert strengths[1].member_count == 1
    assert strengths[1].limit_up_count is None


def test_build_concept_strength_watchlist_only_appends():
    strengths = build_concept_strength(
        [], {"白酒": {"members": ["600519.SH"], "member_count": 1}}, {}
    )
    assert len(strengths) == 1
    assert strengths[0].board_name == "白酒"
    assert strengths[0].market_rank is None
    assert strengths[0].source == "watchlist"


# ---------------------------------------------------------------------------
# build_concept_snapshots
# ---------------------------------------------------------------------------


def test_build_concept_snapshots_attaches_hottest():
    boards_map = {"600519.SH": ["白酒", "AI"]}
    ranking = [
        {"board_name": "AI", "change_pct": 3.0},
        {"board_name": "白酒", "change_pct": 1.0},
    ]
    heat_scores = {"AI": 90.0, "白酒": 60.0}
    limit_up = {"AI": 5}
    snaps = build_concept_snapshots(boards_map, ranking, heat_scores, limit_up)

    snap = snaps["600519.SH"]
    assert snap.boards == ["白酒", "AI"]
    # AI has the best (lowest) rank -> hottest.
    assert snap.hottest_concept == "AI"
    assert snap.hottest_concept_rank == 1
    assert snap.concept_heat_score == 90.0
    assert snap.limit_up_count == 5
    assert snap.source == "eastmoney"


def test_build_concept_snapshots_no_boards_marks_error():
    snaps = build_concept_snapshots({"600519.SH": []}, [], {}, {})
    assert snaps["600519.SH"].error == "no concept board membership"


def test_build_concept_snapshots_unranked_boards_keep_membership():
    boards_map = {"600519.SH": ["白酒"]}
    snaps = build_concept_snapshots(boards_map, [], {}, {})
    snap = snaps["600519.SH"]
    assert snap.boards == ["白酒"]
    assert snap.hottest_concept is None


# ---------------------------------------------------------------------------
# compute_concept_heat_scores
# ---------------------------------------------------------------------------


def test_compute_concept_heat_scores_cross_sectional():
    ranking = [
        {"board_name": "A", "change_pct": 3.0, "fund_flow_net": 1e8},
        {"board_name": "B", "change_pct": -1.0, "fund_flow_net": -1e8},
    ]
    # No limit-up pool -> that dimension is missing and renormalized.
    scores = compute_concept_heat_scores(ranking, {})
    assert set(scores) == {"A", "B"}
    assert scores["A"] > scores["B"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
