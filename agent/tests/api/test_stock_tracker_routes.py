"""Integration tests for stock tracker API routes."""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api_server
from src.stock_tracker.models import SymbolSnapshot, TrackerConfig, TrackerSnapshot
from src.stock_tracker.store import TrackerStore


@pytest.fixture
def client():
    return TestClient(api_server.app)


@pytest.fixture(autouse=True)
def isolated_tracker_store():
    """Use a temporary directory for tracker state during tests."""
    with tempfile.TemporaryDirectory() as tmp:
        store = TrackerStore(root_dir=tmp)
        with patch("src.api.stock_tracker_routes._store", store):
            yield store


def test_list_signals_endpoint(client):
    response = client.get("/api/stock-tracker/signals")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    names = {s["name"] for s in data["signals"]}
    assert "volume_spike" in names
    assert "breakout" in names
    assert "ma_alignment" in names
    assert "rsi" in names
    for signal in data["signals"]:
        assert "params" in signal
        assert "format" in signal
        assert "show_in_table" in signal
        assert "is_global" in signal


def test_get_settings_default(client):
    response = client.get("/api/stock-tracker/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["watchlist"] == TrackerConfig().watchlist
    assert data["config"]["periods"] == [10, 20, 60]
    assert data["config"]["refresh_interval_seconds"] == 10
    assert data["config"]["detail_card_count"] == 9


def test_update_settings_validation(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"watchlist": ["INVALID"]},
    )
    assert response.status_code == 422


def test_update_settings_auto_normalizes_bare_code(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"watchlist": ["603228"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["watchlist"] == ["603228.SH"]


def test_update_settings_rejects_empty_periods(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"periods": []},
    )
    assert response.status_code == 422


def test_update_settings_success(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"watchlist": ["000001.SZ"], "periods": [5, 10]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["watchlist"] == ["000001.SZ"]
    assert data["config"]["periods"] == [5, 10]


def test_update_settings_refresh_interval(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"refresh_interval_seconds": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["refresh_interval_seconds"] == 5


def test_update_settings_refresh_interval_validation(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"refresh_interval_seconds": 3},
    )
    assert response.status_code == 422


def test_update_settings_detail_card_count(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"detail_card_count": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["detail_card_count"] == 5


def test_update_settings_detail_card_count_validation(client):
    response = client.put(
        "/api/stock-tracker/settings",
        json={"detail_card_count": 0},
    )
    assert response.status_code == 422


def test_quotes_endpoint(client, isolated_tracker_store):
    isolated_tracker_store.save_settings(
        isolated_tracker_store.get_settings().model_copy(
            update={"config": TrackerConfig(watchlist=["600519.SH"])}
        )
    )
    # Tencent serves today's bar only for a single-day window, so the live
    # quote arrives without a predecessor; 昨收 must come from the merged
    # finalized history (the previous session's close).
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    def _bar(trade_date, close):
        return {
            "code": "600519.SH",
            "name": "贵州茅台",
            "trade_date": trade_date,
            "close": close,
            "volume": 10000,
        }

    def fake_fetch_market_data(**kwargs):
        if kwargs["start_date"] == kwargs["end_date"]:
            return {"600519.SH": [_bar(today, 1500.0)], "_unresolved": []}
        return {"600519.SH": [_bar(yesterday, 1480.0)], "_unresolved": []}

    with patch(
        "src.api.stock_tracker_routes.fetch_market_data",
        side_effect=fake_fetch_market_data,
    ):
        response = client.get("/api/stock-tracker/quotes")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert len(data["quotes"]) == 1
    quote = data["quotes"][0]
    assert quote["code"] == "600519.SH"
    assert quote["close"] == 1500.0
    assert quote["prev_close"] == 1480.0
    assert quote["date"] == today
    assert quote["change_amount"] == 20.0
    assert round(quote["daily_return"], 4) == round(20.0 / 1480.0, 4)
    assert quote["error"] is None
    assert data["data_gaps"] == []


def test_quotes_endpoint_history_unavailable_degrades(client, isolated_tracker_store):
    """When the finalized-history fetch fails, quotes still resolve from the
    live single-day window (no false data_gaps)."""
    isolated_tracker_store.save_settings(
        isolated_tracker_store.get_settings().model_copy(
            update={"config": TrackerConfig(watchlist=["600519.SH"])}
        )
    )
    today = date.today().isoformat()

    def _bar(close):
        return {
            "code": "600519.SH",
            "name": "贵州茅台",
            "trade_date": today,
            "close": close,
            "volume": 10000,
        }

    calls = {"n": 0}

    def fake_fetch_market_data(**kwargs):
        calls["n"] += 1
        if kwargs["start_date"] == kwargs["end_date"]:
            return {"600519.SH": [_bar(1500.0)], "_unresolved": []}
        raise RuntimeError("history unavailable")

    with patch(
        "src.api.stock_tracker_routes.fetch_market_data",
        side_effect=fake_fetch_market_data,
    ):
        response = client.get("/api/stock-tracker/quotes")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert calls["n"] == 2
    quote = data["quotes"][0]
    assert quote["close"] == 1500.0
    assert quote["prev_close"] is None
    assert data["data_gaps"] == []


def test_get_latest_snapshot_empty(client):
    response = client.get("/api/stock-tracker")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "empty"
    assert data["snapshot"] is None


def test_get_latest_snapshot_returns_data(client, isolated_tracker_store):
    isolated_tracker_store.save_snapshot(
        TrackerSnapshot(
            generated_at=datetime.now(timezone.utc),
            trading_date=date(2026, 8, 31),
            config=TrackerConfig(),
            symbols=[],
        )
    )
    response = client.get("/api/stock-tracker")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["snapshot"]["trading_date"] == "2026-08-31"


def test_history_endpoint(client, isolated_tracker_store):
    isolated_tracker_store.save_snapshot(
        TrackerSnapshot(
            generated_at=datetime.now(timezone.utc),
            trading_date=date(2026, 8, 31),
            config=TrackerConfig(),
            symbols=[],
        )
    )
    response = client.get("/api/stock-tracker/history?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert len(data["snapshots"]) == 1


def test_refresh_status_endpoint(client):
    response = client.get("/api/stock-tracker/refresh-status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "running" in data["refresh"]


def _wait_refresh_done(client, timeout: float = 10.0) -> dict:
    """Poll refresh-status until the background refresh completes."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        state = client.get("/api/stock-tracker/refresh-status").json()["refresh"]
        if not state["running"]:
            return state
        _time.sleep(0.02)
    raise AssertionError("background refresh did not finish")


def test_refresh_post_starts_background_and_persists(client, isolated_tracker_store):
    from datetime import datetime, timezone as tz

    def _fake_sync():
        isolated_tracker_store.save_snapshot(
            TrackerSnapshot(
                generated_at=datetime.now(tz.utc),
                trading_date=date(2026, 8, 31),
                config=TrackerConfig(),
                symbols=[SymbolSnapshot(code="600519.SH", name="贵州茅台", close=1500.0)],
            )
        )
        return {"status": "ok"}

    with patch("src.api.stock_tracker_routes._refresh_snapshot_sync", side_effect=_fake_sync):
        response = client.post("/api/stock-tracker/refresh")
        assert response.status_code == 200
        # The POST answers immediately; the refresh finishes on a worker thread.
        assert response.json()["status"] == "started"
        state = _wait_refresh_done(client)
    assert state["running"] is False
    assert state["error"] is None

    snapshot = client.get("/api/stock-tracker").json()
    assert snapshot["status"] == "ok"
    assert snapshot["snapshot"]["symbols"][0]["code"] == "600519.SH"


def test_refresh_background_error_surfaced_in_status(client, isolated_tracker_store):
    def _boom():
        raise RuntimeError("provider down")

    with patch("src.api.stock_tracker_routes._refresh_snapshot_sync", side_effect=_boom):
        response = client.post("/api/stock-tracker/refresh")
        assert response.json()["status"] == "started"
        state = _wait_refresh_done(client)
    assert state["running"] is False
    assert state["error"] is not None
    assert "provider down" in state["error"]


def test_refresh_post_singleflight_returns_refreshing(client, isolated_tracker_store):
    import threading

    started = threading.Event()
    release = threading.Event()

    def _slow_sync():
        started.set()
        if not release.wait(5.0):
            raise RuntimeError("timed out")
        return {"status": "ok"}

    with patch("src.api.stock_tracker_routes._refresh_snapshot_sync", side_effect=_slow_sync):
        first = client.post("/api/stock-tracker/refresh")
        assert first.json()["status"] == "started"
        assert started.wait(5.0)

        # A second (non-force) request while one is running answers "refreshing"
        # instead of spawning a duplicate refresh.
        second = client.post("/api/stock-tracker/refresh")
        assert second.json()["status"] == "refreshing"

        release.set()
        state = _wait_refresh_done(client)
    assert state["running"] is False


def _saved_snapshot(store: TrackerStore) -> None:
    store.save_snapshot(
        TrackerSnapshot(
            generated_at=datetime.now(timezone.utc),
            trading_date=date(2026, 8, 31),
            config=TrackerConfig(),
            symbols=[SymbolSnapshot(code="600519.SH", name="贵州茅台", close=1500.0)],
        )
    )


def test_analyze_no_snapshot_returns_404(client):
    response = client.post(
        "/api/stock-tracker/analyze",
        json={"symbols": ["600519.SH"]},
    )
    assert response.status_code == 404


def test_analyze_invalid_symbol_returns_422(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    response = client.post(
        "/api/stock-tracker/analyze",
        json={"symbols": ["INVALID"]},
    )
    assert response.status_code == 422


def test_analyze_returns_report(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    fake_report = {
        "summary": "综述",
        "symbols": [],
        "portfolio": {"theme": "", "top_pick": None, "cautions": []},
        "caveats": [],
    }
    with patch("src.api.stock_tracker_routes.run_analysis", return_value=fake_report):
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"]},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["report"]["summary"] == "综述"


def test_get_analysis_empty(client):
    response = client.get("/api/stock-tracker/analyze")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "empty"
    assert data["report"] is None


def test_analyze_persists_and_get_returns_report(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    fake_report = {
        "summary": "综述",
        "symbols": [],
        "portfolio": {"theme": "", "top_pick": None, "cautions": []},
        "caveats": [],
    }
    with patch("src.api.stock_tracker_routes.run_analysis", return_value=fake_report):
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"]},
        )
    assert response.status_code == 200

    get_response = client.get("/api/stock-tracker/analyze")
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["status"] == "ok"
    assert data["report"]["summary"] == "综述"


def test_analysis_history_endpoint(client, isolated_tracker_store):
    isolated_tracker_store.save_analysis(
        {"summary": "a"},
        generated_at=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
    )
    isolated_tracker_store.save_analysis(
        {"summary": "b"},
        generated_at=datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc),
    )

    response = client.get("/api/stock-tracker/analyze/history")
    assert response.status_code == 200
    data = response.json()
    assert [item["summary"] for item in data["items"]] == ["b", "a"]


def test_get_analysis_by_id_endpoint(client, isolated_tracker_store):
    envelope = isolated_tracker_store.save_analysis({"summary": "x"})
    response = client.get(f"/api/stock-tracker/analyze/{envelope['id']}")
    assert response.status_code == 200
    assert response.json()["report"]["summary"] == "x"


def test_get_analysis_by_id_404(client):
    response = client.get("/api/stock-tracker/analyze/nonexistent")
    assert response.status_code == 404


def test_delete_analysis_removes_report_and_history(client, isolated_tracker_store):
    envelope = isolated_tracker_store.save_analysis({"summary": "a"})
    delete_response = client.delete(f"/api/stock-tracker/analyze/{envelope['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] == envelope["id"]

    history = client.get("/api/stock-tracker/analyze/history").json()
    assert history["items"] == []
    by_id = client.get(f"/api/stock-tracker/analyze/{envelope['id']}")
    assert by_id.status_code == 404


def test_delete_analysis_missing_returns_404(client, isolated_tracker_store):
    response = client.delete("/api/stock-tracker/analyze/nonexistent")
    assert response.status_code == 404


def test_analyze_passes_user_prompt_through(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    fake_report = {
        "summary": "综述",
        "symbols": [],
        "portfolio": {"theme": "", "top_pick": None, "cautions": []},
        "caveats": [],
    }
    with patch(
        "src.api.stock_tracker_routes.run_analysis", return_value=fake_report
    ) as mocked:
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"], "user_prompt": "重点看均线多头排列"},
        )
    assert response.status_code == 200
    args, _kwargs = mocked.call_args
    assert args[2] == "重点看均线多头排列"


def test_analyze_feeds_recent_history_to_llm(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)  # 600519.SH close = 1500.0
    # Persist three past analyses (oldest first); the newest is saved last.
    for day, stop in [(26, 1400.0), (27, 1410.0), (28, 1420.0)]:
        isolated_tracker_store.save_analysis(
            {
                "summary": "综述",
                "symbols": [
                    {
                        "code": "600519.SH",
                        "action": "buy",
                        "confidence": 70,
                        "entry_zone": {"low": 1450.0, "high": 1480.0},
                        "target_zone": {"low": 1600.0, "high": 1700.0},
                        "stop_loss": stop,
                    }
                ],
                "portfolio": {"theme": "", "top_pick": None, "cautions": []},
                "caveats": [],
            },
            trading_date=date(2026, 8, day),
            generated_at=datetime(2026, 8, day, 9, 0, tzinfo=timezone.utc),
        )

    fake_report = {
        "summary": "综述",
        "symbols": [],
        "portfolio": {"theme": "", "top_pick": None, "cautions": []},
        "caveats": [],
    }
    with patch(
        "src.api.stock_tracker_routes.run_analysis", return_value=fake_report
    ) as mocked:
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"]},
        )
    assert response.status_code == 200
    args, _kwargs = mocked.call_args
    history = args[3]
    assert set(history) == {"600519.SH"}
    assert [r["stop_loss"] for r in history["600519.SH"]] == [1420.0, 1410.0, 1400.0]
    assert all("code" not in r for r in history["600519.SH"])


def _save_history_batch(store: TrackerStore, stops: list[float]) -> None:
    """Persist one past analysis per stop value, oldest first."""
    for day, stop in enumerate(stops, start=24):
        store.save_analysis(
            {
                "summary": "综述",
                "symbols": [
                    {
                        "code": "600519.SH",
                        "action": "buy",
                        "confidence": 70,
                        "entry_zone": {"low": 1450.0, "high": 1480.0},
                        "target_zone": {"low": 1600.0, "high": 1700.0},
                        "stop_loss": stop,
                    }
                ],
                "portfolio": {"theme": "", "top_pick": None, "cautions": []},
                "caveats": [],
            },
            trading_date=date(2026, 8, day),
            generated_at=datetime(2026, 8, day, 9, 0, tzinfo=timezone.utc),
        )


def test_analyze_honours_custom_history_limit(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    _save_history_batch(isolated_tracker_store, [1400.0, 1410.0, 1420.0, 1430.0, 1440.0, 1450.0, 1460.0])
    fake_report = {
        "summary": "综述",
        "symbols": [],
        "portfolio": {"theme": "", "top_pick": None, "cautions": []},
        "caveats": [],
    }
    with patch(
        "src.api.stock_tracker_routes.run_analysis", return_value=fake_report
    ) as mocked:
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"], "history_limit": 2},
        )
    assert response.status_code == 200
    args, _kwargs = mocked.call_args
    history = args[3]
    assert [r["stop_loss"] for r in history["600519.SH"]] == [1460.0, 1450.0]


def test_analyze_history_limit_zero_disables_history(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    _save_history_batch(isolated_tracker_store, [1400.0, 1410.0])
    fake_report = {
        "summary": "综述",
        "symbols": [],
        "portfolio": {"theme": "", "top_pick": None, "cautions": []},
        "caveats": [],
    }
    with patch(
        "src.api.stock_tracker_routes.run_analysis", return_value=fake_report
    ) as mocked:
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"], "history_limit": 0},
        )
    assert response.status_code == 200
    args, _kwargs = mocked.call_args
    assert args[3] == {}


def test_analyze_ignores_unknown_body_fields(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    fake_report = {
        "summary": "综述",
        "symbols": [],
        "portfolio": {"theme": "", "top_pick": None, "cautions": []},
        "caveats": [],
    }
    with patch(
        "src.api.stock_tracker_routes.run_analysis", return_value=fake_report
    ) as mocked:
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"], "focus": "rank_opportunities"},
        )
    assert response.status_code == 200
    args, _kwargs = mocked.call_args
    assert args[2] is None  # removed focus must not leak into user_prompt


def test_analyze_maps_quota_error_to_friendly_502(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    with patch(
        "src.api.stock_tracker_routes.run_analysis",
        side_effect=Exception(
            "OpenAIPermissionDeniedError: Error code: 403 - "
            "You've reached your weekly (7-day) usage limit."
        ),
    ):
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"]},
        )
    assert response.status_code == 502
    assert "配额" in response.json()["detail"]


def test_analyze_maps_auth_error_to_friendly_502(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    with patch(
        "src.api.stock_tracker_routes.run_analysis",
        side_effect=Exception("Invalid API key provided"),
    ):
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"]},
        )
    assert response.status_code == 502
    assert "鉴权" in response.json()["detail"]


def test_analyze_maps_rate_limit_error_to_friendly_502(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    with patch(
        "src.api.stock_tracker_routes.run_analysis",
        side_effect=Exception("Error code: 429 - Rate limit reached"),
    ):
        response = client.post(
            "/api/stock-tracker/analyze",
            json={"symbols": ["600519.SH"]},
        )
    assert response.status_code == 502
    assert "过于频繁" in response.json()["detail"]


def test_track_record_endpoint(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)  # 600519.SH close = 1500.0
    envelope = isolated_tracker_store.save_analysis(
        {
            "summary": "综述",
            "symbols": [
                {
                    "code": "600519.SH",
                    "action": "buy",
                    "confidence": 70,
                    "entry_zone": {"low": 1450.0, "high": 1480.0},
                    "target_zone": {"low": 1600.0, "high": 1700.0},
                    "stop_loss": 1420.0,
                }
            ],
            "portfolio": {"theme": "", "top_pick": None, "cautions": []},
            "caveats": [],
        }
    )
    response = client.get("/api/stock-tracker/analyze/track-record")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["analysis_id"] == envelope["id"]
    assert item["code"] == "600519.SH"
    assert item["action"] == "buy"
    assert item["status"] == "active"  # 1500 below target 1600, above stop 1420


def test_track_record_endpoint_ignores_reports_without_price_anchors(client, isolated_tracker_store):
    isolated_tracker_store.save_analysis(
        {
            "summary": "综述",
            "symbols": [{"code": "600519.SH", "action": "hold", "rationale": "无价位锚点"}],
            "portfolio": {"theme": "", "top_pick": None, "cautions": []},
            "caveats": [],
        }
    )
    response = client.get("/api/stock-tracker/analyze/track-record")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_config_from_request_coerces_int_threshold_fields(client, isolated_tracker_store):
    from src.api.stock_tracker_routes import TrackerSettingsRequest, _config_from_request

    config = _config_from_request(
        TrackerSettingsRequest(thresholds={"atr_period": 14, "beta_window": 60, "volume_spike": 2.5})
    )
    # Integral floats from the JSON request must be coerced back to int so the
    # model serializes cleanly (regression: model_copy bypassed validation).
    assert isinstance(config.thresholds.atr_period, int)
    assert config.thresholds.atr_period == 14
    assert isinstance(config.thresholds.beta_window, int)
    assert config.thresholds.beta_window == 60
    # Non-integral thresholds keep their float type.
    assert config.thresholds.volume_spike == 2.5


def test_update_settings_serializes_without_int_float_warning(client, isolated_tracker_store):
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        response = client.put(
            "/api/stock-tracker/settings",
            json={"thresholds": {"atr_period": 14, "max_drawdown_window": 60}},
        )
    assert response.status_code == 200
    pydantic_warnings = [w for w in caught if "PydanticSerializationUnexpectedValue" in str(w.message)]
    assert pydantic_warnings == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
