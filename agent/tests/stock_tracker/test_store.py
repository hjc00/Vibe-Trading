"""Unit tests for the stock tracker store."""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.stock_tracker.models import (
    TrackerConfig,
    TrackerSettings,
    TrackerSnapshot,
)
from src.stock_tracker.store import TrackerStore


def test_settings_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = TrackerStore(root_dir=tmp)
        settings = TrackerSettings(
            config=TrackerConfig(watchlist=["000001.SZ"]),
            created_at=datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc),
        )
        store.save_settings(settings)

        loaded = store.get_settings()
        assert loaded.config.watchlist == ["000001.SZ"]
        assert loaded.updated_at is not None


def test_settings_returns_defaults_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        store = TrackerStore(root_dir=tmp)
        loaded = store.get_settings()
        assert loaded.config.watchlist == TrackerConfig().watchlist


def test_snapshot_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = TrackerStore(root_dir=tmp)
        snapshot = TrackerSnapshot(
            generated_at=datetime(2026, 8, 31, 16, 0, 0, tzinfo=timezone.utc),
            trading_date=date(2026, 8, 31),
            config=TrackerConfig(),
            symbols=[],
        )
        store.save_snapshot(snapshot)

        loaded = store.get_snapshot(date(2026, 8, 31))
        assert loaded is not None
        assert loaded.trading_date == date(2026, 8, 31)


def test_list_snapshots_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        store = TrackerStore(root_dir=tmp)
        for day in [28, 29, 30, 31]:
            store.save_snapshot(
                TrackerSnapshot(
                    generated_at=datetime(2026, 8, day, 16, 0, 0, tzinfo=timezone.utc),
                    trading_date=date(2026, 8, day),
                    config=TrackerConfig(),
                    symbols=[],
                )
            )

        dates = store.list_snapshot_dates()
        assert [d.day for d in dates] == [31, 30, 29, 28]


def test_atomic_write_survives_interruption():
    with tempfile.TemporaryDirectory() as tmp:
        store = TrackerStore(root_dir=tmp)
        target = Path(tmp) / "test.json"

        # Write initial valid content.
        store._write_json(target, {"version": 1})

        # Simulate an interrupted write by creating a temp file but not replacing.
        temp_path = target.parent / ".test.json.interrupted.tmp"
        temp_path.write_text("corrupted", encoding="utf-8")

        # A subsequent write should still succeed and leave valid content.
        store._write_json(target, {"version": 2})
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["version"] == 2


def test_delete_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        store = TrackerStore(root_dir=tmp)
        store.save_snapshot(
            TrackerSnapshot(
                generated_at=datetime(2026, 8, 31, 16, 0, 0, tzinfo=timezone.utc),
                trading_date=date(2026, 8, 31),
                config=TrackerConfig(),
                symbols=[],
            )
        )
        assert store.delete_snapshot(date(2026, 8, 31)) is True
        assert store.get_snapshot(date(2026, 8, 31)) is None
        assert store.delete_snapshot(date(2026, 8, 31)) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
