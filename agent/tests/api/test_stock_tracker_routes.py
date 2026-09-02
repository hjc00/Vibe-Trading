"""Integration tests for stock tracker API routes."""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
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
    assert data["config"]["detail_card_count"] == 5


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
    fake_records = {
        "600519.SH": [
            {
                "code": "600519.SH",
                "name": "贵州茅台",
                "close": 1480.0,
                "volume": 8000,
            },
            {
                "code": "600519.SH",
                "name": "贵州茅台",
                "close": 1500.0,
                "volume": 10000,
            },
        ],
        "_unresolved": [],
    }
    with patch("src.api.stock_tracker_routes.fetch_market_data", return_value=fake_records):
        response = client.get("/api/stock-tracker/quotes")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert len(data["quotes"]) == 1
    quote = data["quotes"][0]
    assert quote["code"] == "600519.SH"
    assert quote["close"] == 1500.0
    assert quote["prev_close"] == 1480.0
    assert quote["change_amount"] == 20.0
    assert round(quote["daily_return"], 4) == round(20.0 / 1480.0, 4)
    assert quote["error"] is None
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
        json={"symbols": ["600519.SH"], "focus": "rank_opportunities"},
    )
    assert response.status_code == 404


def test_analyze_invalid_symbol_returns_422(client, isolated_tracker_store):
    _saved_snapshot(isolated_tracker_store)
    response = client.post(
        "/api/stock-tracker/analyze",
        json={"symbols": ["INVALID"], "focus": "rank_opportunities"},
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
            json={"symbols": ["600519.SH"], "focus": "rank_opportunities"},
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
            json={"symbols": ["600519.SH"], "focus": "rank_opportunities"},
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
