"""Unit tests for building a track record from persisted analysis reports."""

from __future__ import annotations

from src.stock_tracker.track_record import build_track_record, select_symbol_history


def _envelope(analysis_id: str, symbols: list[dict]) -> dict:
    return {
        "id": analysis_id,
        "trading_date": "2026-08-31",
        "generated_at": "2026-08-31T10:00:00+00:00",
        "report": {"summary": "s", "symbols": symbols},
    }


def _symbol(code: str, **kwargs) -> dict:
    base = {"code": code, "action": "buy", "confidence": 70}
    base.update(kwargs)
    return base


def test_hit_target_when_close_reaches_target_high():
    analyses = [
        _envelope(
            "a",
            [_symbol("600519.SH", target_zone={"low": 1600.0, "high": 1700.0}, stop_loss=1420.0)],
        )
    ]
    items = build_track_record(analyses, {"600519.SH": 1720.0})
    assert len(items) == 1
    item = items[0]
    assert item.status == "hit_target"
    assert item.action == "buy"
    assert item.analysis_id == "a"
    assert item.current_close == 1720.0


def test_stopped_out_when_close_below_stop():
    analyses = [
        _envelope("a", [_symbol("600519.SH", stop_loss=1420.0, target_zone={"low": 1600.0, "high": 1700.0})])
    ]
    items = build_track_record(analyses, {"600519.SH": 1400.0})
    assert items[0].status == "stopped_out"


def test_active_when_close_between_stop_and_target():
    analyses = [
        _envelope("a", [_symbol("600519.SH", stop_loss=1420.0, target_zone={"low": 1600.0, "high": 1700.0})])
    ]
    items = build_track_record(analyses, {"600519.SH": 1500.0})
    assert items[0].status == "active"


def test_pending_when_no_current_close():
    analyses = [
        _envelope("a", [_symbol("600519.SH", target_zone={"low": 1600.0, "high": 1700.0})])
    ]
    items = build_track_record(analyses, {})
    assert items[0].status == "pending"
    assert items[0].current_close is None


def test_ignores_recommendations_without_price_anchors():
    analyses = [_envelope("a", [_symbol("600519.SH", action="hold")])]
    items = build_track_record(analyses, {"600519.SH": 1500.0})
    assert items == []


def test_defaults_hold_action_and_ignores_word_confidence():
    # action mapping of synonyms (top_pick/watch/...) happens upstream in the
    # analyzer; stored predictions carry the normalized 4-value action already.
    analyses = [
        _envelope(
            "a",
            [
                {
                    "code": "600519.SH",
                    "recommendation": "top_pick",
                    "confidence": "high",
                    "target_zone": {"low": 1600.0, "high": 1700.0},
                }
            ],
        )
    ]
    items = build_track_record(analyses, {"600519.SH": 1650.0})
    assert items[0].action == "hold"  # no structured action -> neutral default
    assert items[0].confidence is None
    assert items[0].status == "active"


def test_latest_analysis_first_order_is_preserved():
    analyses = [
        _envelope("old", [_symbol("A", target_zone={"low": 10.0, "high": 12.0})]),
        _envelope("new", [_symbol("B", target_zone={"low": 20.0, "high": 22.0})]),
    ]
    items = build_track_record(analyses, {"A": 11.0, "B": 21.0})
    assert [item.analysis_id for item in items] == ["old", "new"]


def test_select_symbol_history_keeps_newest_limit_per_code():
    # analyses are newest-first; stop_loss marks each record's identity.
    analyses = [
        _envelope(aid, [_symbol("600519.SH", stop_loss=stop)])
        for aid, stop in [
            ("r6", 1460.0),
            ("r5", 1450.0),
            ("r4", 1440.0),
            ("r3", 1430.0),
            ("r2", 1420.0),
            ("r1", 1410.0),
        ]
    ]
    history = select_symbol_history(analyses, {"600519.SH": 1500.0}, limit=5)
    records = history["600519.SH"]
    assert len(records) == 5  # newest 5 kept, the oldest (r1) dropped
    assert [record["stop_loss"] for record in records] == [
        1460.0, 1450.0, 1440.0, 1430.0, 1420.0,
    ]
    assert [record["status"] for record in records] == ["active"] * 5
    # identity fields are dropped; the code is the mapping key.
    assert "code" not in records[0]
    assert "analysis_id" not in records[0]
    assert "name" not in records[0]


def test_select_symbol_history_uses_all_when_fewer_than_limit():
    analyses = [
        _envelope("r3", [_symbol("600519.SH", stop_loss=1430.0)]),
        _envelope("r2", [_symbol("600519.SH", stop_loss=1420.0)]),
        _envelope("r1", [_symbol("600519.SH", stop_loss=1410.0)]),
    ]
    history = select_symbol_history(analyses, {"600519.SH": 1500.0})
    assert [record["stop_loss"] for record in history["600519.SH"]] == [
        1430.0, 1420.0, 1410.0,
    ]


def test_select_symbol_history_filters_by_requested_codes():
    analyses = [
        _envelope("r2", [_symbol("600519.SH", stop_loss=1420.0)]),
        _envelope("r1", [_symbol("000001.SZ", stop_loss=10.0)]),
    ]
    history = select_symbol_history(
        analyses,
        {"600519.SH": 1500.0, "000001.SZ": 11.0},
        codes=["600519.SH"],
    )
    assert list(history) == ["600519.SH"]
    assert history["600519.SH"][0]["stop_loss"] == 1420.0


def test_select_symbol_history_counts_duplicate_code_once_per_analysis():
    analyses = [
        _envelope(
            "r2",
            [
                _symbol("600519.SH", stop_loss=1420.0),
                _symbol("600519.SH", stop_loss=1421.0),
            ],
        ),
        _envelope("r1", [_symbol("600519.SH", stop_loss=1410.0)]),
    ]
    history = select_symbol_history(analyses, {"600519.SH": 1500.0})
    assert len(history["600519.SH"]) == 2  # r2 counts once, plus r1


def test_select_symbol_history_empty_when_no_history():
    assert select_symbol_history([], {"600519.SH": 1500.0}) == {}
