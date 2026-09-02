"""Unit tests for the concept-board fetchers added to the A-share sector tool."""

from __future__ import annotations

from unittest.mock import patch

from src.tools.sector_tool import fetch_concept_board_ranking, resolve_concept_boards
from src.tools.shareholder_count_tool import fetch_shareholder_count


def _clist_payload(rows: list[dict]) -> dict:
    return {"data": {"diff": rows}}


def _slist_payload(rows: list[dict]) -> dict:
    return {"data": {"diff": rows}}


def test_resolve_concept_boards_subtracts_industry_board():
    """Concept boards = membership boards minus the single industry board."""
    boards = [
        {"board_name": "白酒Ⅱ", "board_code": "BK0477"},
        {"board_name": "白酒概念", "board_code": "BK0800"},
        {"board_name": "消费", "board_code": "BK0900"},
    ]
    with patch(
        "src.tools.sector_tool.resolve_industry_board", return_value="白酒Ⅱ"
    ), patch(
        "src.tools.sector_tool._fetch_membership_boards", return_value=boards
    ):
        names = resolve_concept_boards("600519.SH")

    assert names == ["白酒概念", "消费"]


def test_resolve_concept_boards_keeps_all_when_industry_unknown():
    boards = [
        {"board_name": "白酒概念", "board_code": "BK0800"},
        {"board_name": "消费", "board_code": "BK0900"},
    ]
    with patch(
        "src.tools.sector_tool.resolve_industry_board", return_value=None
    ), patch(
        "src.tools.sector_tool._fetch_membership_boards", return_value=boards
    ):
        names = resolve_concept_boards("600519.SH")

    assert names == ["白酒概念", "消费"]


def test_fetch_concept_board_ranking_uses_concept_universe():
    captured: dict = {}

    def _fake_get_json(url, *, params):
        captured["fs"] = params.get("fs")
        return _clist_payload(
            [
                {"f12": "BK0800", "f14": "AI", "f3": 3.0, "f104": 50, "f105": 30, "f62": 1e8},
                {"f12": "BK0900", "f14": "消费", "f3": 1.0},
            ]
        )

    with patch("src.tools.sector_tool.get_json", side_effect=_fake_get_json):
        ranking = fetch_concept_board_ranking(limit=20)

    assert captured["fs"] == "m:90+t:3"
    assert len(ranking) == 2
    assert ranking[0]["board_name"] == "AI"
    assert ranking[0]["change_pct"] == 3.0
    assert ranking[0]["fund_flow_net"] == 1e8


def test_fetch_shareholder_count_parses_periods():
    payload = {
        "result": {
            "data": [
                {
                    "END_DATE": "2026-06-30 00:00:00",
                    "HOLDER_NUM": 100000,
                    "HOLDER_NUM_CHANGE": -5000,
                    "HOLDER_NUM_RATIO": -4.76,
                    "AVG_HOLD_NUM": 5000,
                    "AVG_HOLD_AMT": 500000,
                    "TOTAL_MARKET_CAP": 5e10,
                }
            ]
        }
    }

    with patch(
        "src.tools.shareholder_count_tool.get_json", return_value=payload
    ), patch(
        "src.tools.shareholder_count_tool.resolve_secid", return_value="1.600519"
    ):
        periods = fetch_shareholder_count("600519.SH", max_periods=24)

    assert len(periods) == 1
    rec = periods[0]
    assert rec["end_date"] == "2026-06-30"
    assert rec["holder_count"] == 100000.0
    assert rec["holder_count_change_pct"] == -4.76


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
