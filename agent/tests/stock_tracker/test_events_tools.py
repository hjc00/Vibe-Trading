"""Unit tests for the event-feed public fetchers added to the A-share tools."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from src.tools.dragon_tiger_tool import fetch_recent_board
from src.tools.lockup_expiry_tool import fetch_lockup_records


def _lockup_payload(rows: list[dict]) -> dict:
    return {"result": {"data": rows}}


def test_fetch_lockup_records_shapes_rich_record():
    """fetch_lockup_records must read ALL columns and keep the ratio fields."""
    rows = [
        {
            "SECURITY_CODE": "600519",
            "SECURITY_NAME_ABBR": "贵州茅台",
            "FREE_DATE": "2026-09-15 00:00:00",
            "FREE_SHARES": 50000.0,
            "ADD_LISTING_SHARES": 5000.0,
            "ADD_LISTING_CAP": 120000.0,
            "ADD_LISTSHARES_RATIO": 0.125,
            "FREE_SHARES_TYPE": "首发原股东限售股份",
        }
    ]

    captured: dict = {}

    def _fake_get_json(url, *, params, headers=None, proxies=None):
        captured["columns"] = params.get("columns")
        captured["filter"] = params.get("filter")
        return _lockup_payload(rows)

    with patch(
        "src.tools.lockup_expiry_tool.eastmoney_client.get_json",
        side_effect=_fake_get_json,
    ):
        records = fetch_lockup_records("600519.SH", horizon_days=90)

    assert captured["columns"] == "ALL"
    assert 'SECURITY_CODE="600519"' in captured["filter"]
    assert len(records) == 1
    rec = records[0]
    assert rec["code"] == "600519"
    assert rec["free_date"] == "2026-09-15"
    assert rec["free_ratio"] == 0.125  # native fraction preserved
    assert rec["free_shares"] == 50000.0


def test_fetch_lockup_records_rejects_bad_code():
    import pytest

    with pytest.raises(ValueError):
        fetch_lockup_records("NOT-A-CODE")


def test_fetch_recent_board_walks_days_and_stops_at_target():
    """fetch_recent_board collects `days` boards, skipping empty (non-trading) days."""

    rows_by_date = {
        "2026-08-28": [  # Friday
            {"SECURITY_CODE": "600519", "TRADE_DATE": "2026-08-28 00:00:00", "BILLBOARD_NET_AMT": -1e7}
        ],
        "2026-08-27": [  # Thursday
            {"SECURITY_CODE": "000001", "TRADE_DATE": "2026-08-27 00:00:00", "BILLBOARD_NET_AMT": 5e6}
        ],
    }

    def _fake_report(report_name, *, filter_expr, sort_columns, sort_types):
        # Extract the queried trade date from the single-date filter literal.
        trade_date = filter_expr.split("'")[1]
        return rows_by_date.get(trade_date, [])

    with patch(
        "datetime.date",
    ) as mock_date, patch(
        "src.tools.dragon_tiger_tool._fetch_report",
        side_effect=_fake_report,
    ):
        mock_date.today.return_value = date(2026, 8, 28)
        appearances = fetch_recent_board(days=2)

    assert len(appearances) == 2
    assert appearances[0]["code"] == "000001"
    assert appearances[0]["trade_date"] == "2026-08-27"
    assert appearances[1]["code"] == "600519"
    assert appearances[1]["trade_date"] == "2026-08-28"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
