"""Unit tests for building a track record from persisted analysis reports."""

from __future__ import annotations

from src.stock_tracker.track_record import build_track_record


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
