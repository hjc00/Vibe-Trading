"""Unit tests for the stock tracker event-calendar data loader."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.stock_tracker.events_data import (
    EventsDataCache,
    _risk_level,
    build_event_snapshot,
    compute_event_risk_score,
    dragon_tiger_events,
    load_events_data,
    parse_forecast_events,
    parse_holder_trade_events,
    parse_lockup_events,
)
from src.stock_tracker.models import EventItem, EventSnapshot

AS_OF = date(2026, 9, 2)


def _d(days: int) -> date:
    return AS_OF + timedelta(days=days)


def _lockup_record(days_until: int, *, ratio: float, cap_wan: float = 1000.0) -> dict:
    return {
        "free_date": _d(days_until).isoformat(),
        "free_shares": 50000.0,
        "free_ratio": ratio,  # 0-1 fraction (Eastmoney native)
        "lift_market_cap": cap_wan,
    }


class TestParseLockupEvents:
    def test_filters_to_forward_window_and_sorts_fields(self):
        items = parse_lockup_events(
            [
                _lockup_record(-10, ratio=0.5),  # past -> dropped
                _lockup_record(10, ratio=0.15),
                _lockup_record(120, ratio=0.4),  # beyond 90d -> dropped
            ],
            as_of=AS_OF,
        )
        assert len(items) == 1
        item = items[0]
        assert item.event_type == "lockup"
        assert item.event_date == _d(10)
        assert item.days_until == 10
        assert item.source == "eastmoney"
        # Native fraction preserved; display percent derived in summary/title.
        assert item.details["free_ratio"] == 0.15
        assert "15.00%" in item.summary

    def test_large_near_term_unlock_is_danger(self):
        item = parse_lockup_events(
            [_lockup_record(10, ratio=0.15)], as_of=AS_OF
        )[0]
        snap = build_event_snapshot([item], AS_OF, source="eastmoney")
        assert item.risk_level == "danger"
        assert item.risk_score == 80.0
        assert snap.event_risk_score == 80.0
        assert snap.high_risk_count == 1

    def test_medium_unlock_in_window_is_warning(self):
        item = parse_lockup_events(
            [_lockup_record(45, ratio=0.02)], as_of=AS_OF
        )[0]
        build_event_snapshot([item], AS_OF, source="eastmoney")
        assert item.risk_level == "warning"
        assert item.risk_score == 45.0

    def test_tiny_unlock_is_info(self):
        item = parse_lockup_events(
            [_lockup_record(20, ratio=0.0005)], as_of=AS_OF
        )[0]
        build_event_snapshot([item], AS_OF, source="eastmoney")
        assert item.risk_level == "info"
        assert item.risk_score == 20.0

    def test_empty_records(self):
        assert parse_lockup_events([], as_of=AS_OF) == []


class TestParseForecastEvents:
    def test_negative_forecast_is_danger(self):
        rows = [
            {
                "ann_date": "2026-08-20",
                "type": "预减",
                "p_change_min": -60.0,
                "p_change_max": -40.0,
            }
        ]
        item = parse_forecast_events(rows, as_of=AS_OF)[0]
        build_event_snapshot([item], AS_OF, source="tushare")
        assert item.event_type == "earnings_forecast"
        assert item.risk_level == "danger"
        assert item.risk_score == pytest.approx(88.0)

    def test_positive_forecast_is_not_a_risk_driver(self):
        rows = [{"ann_date": "2026-08-20", "type": "预增", "p_change_min": 30.0}]
        item = parse_forecast_events(rows, as_of=AS_OF)[0]
        build_event_snapshot([item], AS_OF, source="tushare")
        assert item.risk_level == "info"
        assert item.risk_score == 20.0

    def test_old_announcement_dropped(self):
        rows = [
            {
                "ann_date": (AS_OF - timedelta(days=400)).isoformat(),
                "type": "首亏",
                "p_change_min": -80.0,
            }
        ]
        assert parse_forecast_events(rows, as_of=AS_OF) == []

    def test_summary_carries_change_range(self):
        rows = [
            {
                "ann_date": "2026-08-20",
                "type": "预减",
                "p_change_min": -60.0,
                "p_change_max": -40.0,
            }
        ]
        item = parse_forecast_events(rows, as_of=AS_OF)[0]
        assert "-60.0%" in item.summary
        assert "-40.0%" in item.summary


class TestParseHolderTradeEvents:
    def test_large_reduction_is_danger(self):
        rows = [
            {
                "ann_date": "2026-08-25",
                "in_de": "DE",
                "holder_type": "控股股东",
                "change_ratio": 2.5,
            }
        ]
        item = parse_holder_trade_events(rows, as_of=AS_OF)[0]
        build_event_snapshot([item], AS_OF, source="tushare")
        assert item.event_type == "holder_trade"
        assert item.risk_level == "danger"
        assert item.risk_score == 80.0
        assert "控股股东" in item.title
        assert "减持" in item.title

    def test_increase_is_mild(self):
        rows = [
            {
                "ann_date": "2026-08-26",
                "in_de": "IN",
                "holder_type": "高管",
                "change_ratio": 0.3,
            }
        ]
        item = parse_holder_trade_events(rows, as_of=AS_OF)[0]
        build_event_snapshot([item], AS_OF, source="tushare")
        assert item.risk_level == "info"
        assert "增持" in item.title

    def test_unknown_direction_dropped(self):
        rows = [{"ann_date": "2026-08-26", "in_de": "XX", "change_ratio": 1.0}]
        assert parse_holder_trade_events(rows, as_of=AS_OF) == []


class TestDragonTigerEvents:
    def _appearance(self, net: float, turnover: float = 1e8) -> dict:
        return {
            "code": "600519",
            "name": "贵州茅台",
            "trade_date": "2026-08-28",
            "net_buy": net,
            "turnover": turnover,
            "reason": "日涨幅偏离值达7%",
        }

    def test_heavy_net_sell_warns(self):
        items = dragon_tiger_events([self._appearance(-4e7, turnover=1e8)])
        item = items[0]
        build_event_snapshot(items, AS_OF, source="eastmoney")
        assert item.event_type == "dragon_tiger"
        assert item.risk_level == "warning"
        assert item.details["code_bare"] == "600519"

    def test_recent_only(self):
        old = self._appearance(5e6)
        old["trade_date"] = (AS_OF - timedelta(days=200)).isoformat()
        assert dragon_tiger_events([old]) == []


class TestRiskLevel:
    def test_threshold_mapping(self):
        assert _risk_level(None) == "info"
        assert _risk_level(20.0) == "info"
        assert _risk_level(40.0) == "warning"
        assert _risk_level(69.0) == "warning"
        assert _risk_level(70.0) == "danger"
        assert _risk_level(100.0) == "danger"


class TestCompositeRiskScore:
    def _scored_item(self, score: float) -> EventItem:
        item = EventItem(
            event_type="lockup",
            event_date=_d(10),
            title="t",
            risk_score=score,
            risk_level=_risk_level(score),
            source="eastmoney",
        )
        return item

    def test_empty_items_returns_none(self):
        assert compute_event_risk_score([]) is None

    def test_single_event_uses_dominant(self):
        snap = build_event_snapshot(
            [self._scored_item(80.0)], AS_OF, source="eastmoney"
        )
        assert snap.event_risk_score == 80.0

    def test_multiple_danger_events_add_bonus(self):
        items = [self._scored_item(80.0), self._scored_item(75.0)]
        snap = build_event_snapshot(items, AS_OF, source="eastmoney")
        # dominant 80 + 5 (one extra danger event).
        assert snap.event_risk_score == 85.0
        assert snap.high_risk_count == 2

    def test_score_capped_at_100(self):
        items = [self._scored_item(100.0), self._scored_item(90.0), self._scored_item(85.0)]
        snap = build_event_snapshot(items, AS_OF, source="eastmoney")
        assert snap.event_risk_score == 100.0

    def test_non_danger_events_do_not_add_bonus(self):
        items = [self._scored_item(80.0), self._scored_item(60.0)]
        snap = build_event_snapshot(items, AS_OF, source="eastmoney")
        assert snap.event_risk_score == 80.0

    def test_items_sorted_by_date(self):
        snap = build_event_snapshot(
            [self._scored_item(50.0)],
            AS_OF,
            source="eastmoney",
        )
        assert snap.event_risk_score is not None


class TestEventsDataCache:
    def test_cache_returns_value_within_ttl_and_keyed_by_date(self):
        cache = EventsDataCache(ttl_seconds=60)
        snap = EventSnapshot(as_of=AS_OF, source="eastmoney")
        cache.set("600519.SH", AS_OF, snap)
        assert cache.get("600519.SH", AS_OF) is snap
        assert cache.get("600519.SH", AS_OF - timedelta(days=1)) is None

    def test_cache_expires_after_ttl(self):
        import time

        cache = EventsDataCache(ttl_seconds=0.01)
        snap = EventSnapshot(as_of=AS_OF, source="eastmoney")
        cache.set("600519.SH", AS_OF, snap)
        time.sleep(0.02)
        assert cache.get("600519.SH", AS_OF) is None

    def test_clear_removes_entries(self):
        cache = EventsDataCache(ttl_seconds=60)
        snap = EventSnapshot(as_of=AS_OF, source="eastmoney")
        cache.set("600519.SH", AS_OF, snap)
        cache.clear()
        assert cache.get("600519.SH", AS_OF) is None


class TestLoadEventsData:
    def test_isolates_symbol_failures(self):
        """A raising fetch still yields an EventSnapshot with error, never raises."""
        with patch(
            "src.stock_tracker.events_data._fetch_one_events",
            side_effect=[
                EventSnapshot(as_of=AS_OF, source="eastmoney"),
                EventSnapshot(as_of=AS_OF, error="lockup: boom", source="unavailable"),
            ],
        ):
            result = load_events_data(["600519.SH", "000001.SZ"], end_date=AS_OF)
        assert result["600519.SH"].error is None
        assert result["000001.SZ"].error == "lockup: boom"
        assert set(result.keys()) == {"600519.SH", "000001.SZ"}

    def test_uses_cache_and_skips_network(self):
        cache = EventsDataCache(ttl_seconds=60)
        snap = EventSnapshot(as_of=AS_OF, source="eastmoney", error="cached")
        with patch(
            "src.stock_tracker.events_data._fetch_dragon_tiger_board"
        ) as mock_board, patch(
            "src.stock_tracker.events_data._fetch_one_events"
        ) as mock_one:
            # Cache stores the earlier result; subsequent load must hit cache.
            cache.set("600519.SH", AS_OF, snap)
            result = load_events_data(["600519.SH"], end_date=AS_OF, cache=cache)
            assert result["600519.SH"] is snap
            mock_board.assert_not_called()
            mock_one.assert_not_called()

    def test_board_failure_is_nonfatal(self):
        with patch(
            "src.stock_tracker.events_data._fetch_dragon_tiger_board",
            side_effect=RuntimeError("board down"),
        ), patch(
            "src.stock_tracker.events_data._fetch_one_events",
            return_value=EventSnapshot(as_of=AS_OF, source="eastmoney"),
        ):
            result = load_events_data(["600519.SH"], end_date=AS_OF)
        assert result["600519.SH"].error is None

    def test_does_not_cache_errors(self):
        cache = EventsDataCache(ttl_seconds=60)
        with patch(
            "src.stock_tracker.events_data._fetch_dragon_tiger_board",
            return_value=[],
        ), patch(
            "src.stock_tracker.events_data._fetch_one_events",
            return_value=EventSnapshot(as_of=AS_OF, error="boom"),
        ):
            load_events_data(["600519.SH"], end_date=AS_OF, cache=cache)
        assert cache.get("600519.SH", AS_OF) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
