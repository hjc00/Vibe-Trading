"""Integration tests for stock tracker API routes."""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api_server
from src.stock_tracker.models import TrackerConfig, TrackerSnapshot
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


def test_get_settings_defaults(client):
    response = client.get("/api/stock-tracker/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["watchlist"] == TrackerConfig().watchlist
    assert data["config"]["periods"] == [10, 20, 60]


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
