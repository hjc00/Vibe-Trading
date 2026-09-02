"""Unit tests for the stock tracker market-sentiment loader."""

from __future__ import annotations

import pytest

from src.stock_tracker.sentiment_data import (
    compute_sentiment_score,
    estimate_sentiment_percentiles,
    fetch_market_breadth,
    load_market_sentiment,
)


# ---------------------------------------------------------------------------
# compute_sentiment_score
# ---------------------------------------------------------------------------


def test_sentiment_score_weighted():
    # limit_up 50 (w 0.30); broken_ratio 0.3 -> 70 (w 0.25); ladder 50 (w 0.25);
    # prev_perf 50 (w 0.20). score = 15 + 17.5 + 12.5 + 10 = 55.0.
    assert compute_sentiment_score(50.0, 0.30, 50.0, 50.0) == pytest.approx(55.0)


def test_sentiment_score_single_dimension_renormalized():
    assert compute_sentiment_score(50.0, None, None, None) == pytest.approx(50.0)
    assert compute_sentiment_score(None, 0.30, None, None) == pytest.approx(70.0)
    assert compute_sentiment_score(None, None, 50.0, None) == pytest.approx(50.0)
    assert compute_sentiment_score(None, None, None, 50.0) == pytest.approx(50.0)


def test_sentiment_score_broken_ratio_inverts():
    # broken_ratio 0 -> 100; 1 -> 0.
    assert compute_sentiment_score(None, 0.0, None, None) == pytest.approx(100.0)
    assert compute_sentiment_score(None, 1.0, None, None) == pytest.approx(0.0)


def test_sentiment_score_clamps_percentiles():
    assert compute_sentiment_score(200.0, None, None, None) == pytest.approx(100.0)
    assert compute_sentiment_score(-50.0, None, None, None) == pytest.approx(0.0)


def test_sentiment_score_all_none():
    assert compute_sentiment_score(None, None, None, None) is None


# ---------------------------------------------------------------------------
# estimate_sentiment_percentiles
# ---------------------------------------------------------------------------


def test_percentiles_high():
    pct = estimate_sentiment_percentiles(150, 10, 0.03)
    assert pct["limit_up"] == pytest.approx(100.0)
    assert pct["ladder"] == pytest.approx(100.0)
    assert pct["prev_perf"] == pytest.approx(100.0)


def test_percentiles_low():
    pct = estimate_sentiment_percentiles(0, 1, -0.03)
    assert pct["limit_up"] == pytest.approx(0.0)
    assert pct["ladder"] == pytest.approx(0.0)
    assert pct["prev_perf"] == pytest.approx(0.0)


def test_percentiles_missing():
    pct = estimate_sentiment_percentiles(None, None, None)
    assert pct == {"limit_up": None, "ladder": None, "prev_perf": None}


def test_percentiles_ladder_skips_below_one():
    pct = estimate_sentiment_percentiles(150, 0, None)
    assert pct["ladder"] is None


# ---------------------------------------------------------------------------
# load_market_sentiment
# ---------------------------------------------------------------------------


def _breadth(**overrides):
    frame = {
        "source": "eastmoney",
        "limit_up": [{"code": "600519", "name": "贵州茅台"}],
        "limit_down": [],
        "broken_board": [{"code": "000001", "name": "平安银行"}],
        "board_ladder": {"2": 3, "3": 1},
        "max_board_height": 3,
        "up_count": 3000,
        "down_count": 2000,
        "prev_limit_up_perf": 0.01,
        "limit_up_rows": [],
    }
    frame.update(overrides)
    return frame


def test_load_sentiment_builds_snapshot():
    snap = load_market_sentiment(_breadth())
    assert snap.source == "eastmoney"
    assert snap.limit_up_count == 1
    assert snap.broken_board_count == 1
    # 1 limit-up + 1 broken -> broken_ratio = 0.5.
    assert snap.broken_ratio == pytest.approx(0.5)
    assert snap.max_board_height == 3
    assert snap.board_ladder == {"2": 3, "3": 1}
    assert snap.up_count == 3000
    assert snap.down_count == 2000
    assert snap.sentiment_score is not None


def test_load_sentiment_no_broken_boards():
    snap = load_market_sentiment(_breadth(broken_board=[], limit_up=[{"code": "600519", "name": "x"}]))
    # No broken boards among 1 limit-up -> a 0% broken rate, not None.
    assert snap.broken_ratio == pytest.approx(0.0)
    assert snap.sentiment_score is not None


def test_load_sentiment_empty_market_ratio_is_none():
    snap = load_market_sentiment(_breadth(limit_up=[], broken_board=[]))
    assert snap.limit_up_count is None
    assert snap.broken_ratio is None  # 0 attempts -> undefined ratio


def test_load_sentiment_unavailable():
    snap = load_market_sentiment({"source": "unavailable"})
    assert snap.error is not None
    assert snap.sentiment_score is None


def test_fetch_market_breadth_unavailable_when_sources_fail():
    from unittest.mock import patch

    with patch(
        "src.stock_tracker.sentiment_data._fetch_push2ex", return_value=None
    ), patch("src.stock_tracker.sentiment_data._fetch_tushare", return_value=None):
        frame = fetch_market_breadth()

    assert frame["source"] == "unavailable"
    assert frame["limit_up"] == []
    assert frame["limit_up_rows"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
