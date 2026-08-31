"""Persistent storage for tracker settings and daily snapshots."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.paths import get_runtime_root
from src.stock_tracker.models import TrackerSettings, TrackerSnapshot

logger = logging.getLogger(__name__)

_SETTINGS_FILENAME = "settings.json"
_SNAPSHOTS_DIRNAME = "snapshots"
_SCHEMA_VERSION = 1


class TrackerStore:
    """JSON file store for tracker configuration and daily snapshots.

    All writes are atomic (temp file + ``os.replace`` + fsync) and land under
    the runtime root so they never pollute the working tree.
    """

    def __init__(self, root_dir: Path | str | None = None) -> None:
        self.root = Path(root_dir) if root_dir else get_runtime_root() / "stock_tracker"
        self.root.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.root / _SNAPSHOTS_DIRNAME
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_settings(self) -> TrackerSettings:
        """Load persisted settings or return defaults."""
        path = self.root / _SETTINGS_FILENAME
        if not path.exists():
            return TrackerSettings()
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return TrackerSettings()
            return TrackerSettings.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load tracker settings from %s: %s", path, exc)
            return TrackerSettings()

    def save_settings(self, settings: TrackerSettings) -> None:
        """Persist settings atomically."""
        settings.updated_at = datetime.now().astimezone()
        self._write_json(self.root / _SETTINGS_FILENAME, settings.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def save_snapshot(self, snapshot: TrackerSnapshot) -> None:
        """Persist one daily snapshot."""
        if snapshot.trading_date is None:
            snapshot.trading_date = date.today()
        path = self._snapshot_path(snapshot.trading_date)
        envelope = {
            "schema_version": _SCHEMA_VERSION,
            "snapshot": snapshot.model_dump(mode="json"),
        }
        self._write_json(path, envelope)

    def get_snapshot(self, trading_date: date) -> Optional[TrackerSnapshot]:
        """Load a snapshot for a specific trading date."""
        path = self._snapshot_path(trading_date)
        return self._load_snapshot_file(path)

    def get_latest_snapshot(self) -> Optional[TrackerSnapshot]:
        """Load the most recently generated snapshot (by ``generated_at``)."""
        latest: Optional[TrackerSnapshot] = None
        for snap in self.list_snapshots(limit=100):
            if latest is None or snap.generated_at > latest.generated_at:
                latest = snap
        return latest

    def list_snapshot_dates(self, limit: int = 100) -> List[date]:
        """Return trading dates for stored snapshots, newest first."""
        dates: List[date] = []
        if not self.snapshots_dir.exists():
            return dates
        for entry in self.snapshots_dir.iterdir():
            if not entry.is_file() or not entry.suffix == ".json":
                continue
            try:
                dates.append(date.fromisoformat(entry.stem))
            except ValueError:
                continue
        dates.sort(reverse=True)
        return dates[:limit]

    def list_snapshots(self, limit: int = 30) -> List[TrackerSnapshot]:
        """Return the most recent snapshots deserialized."""
        result: List[TrackerSnapshot] = []
        for d in self.list_snapshot_dates(limit=limit):
            snap = self.get_snapshot(d)
            if snap is not None:
                result.append(snap)
        return result

    def delete_snapshot(self, trading_date: date) -> bool:
        """Remove a snapshot file. Returns True if the file existed."""
        path = self._snapshot_path(trading_date)
        if path.exists():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _snapshot_path(self, trading_date: date) -> Path:
        return self.snapshots_dir / f"{trading_date.isoformat()}.json"

    def _load_snapshot_file(self, path: Path) -> Optional[TrackerSnapshot]:
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "snapshot" in raw:
                data = raw["snapshot"]
            else:
                data = raw
            return TrackerSnapshot.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load tracker snapshot from %s: %s", path, exc)
            return None

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        """Atomically write a JSON object to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encoded)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
            TrackerStore._fsync_dir(path.parent)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """Ensure the directory entry for a new file is durable."""
        try:
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


__all__ = ["TrackerStore"]
