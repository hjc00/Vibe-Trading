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
_ANALYSES_DIRNAME = "analyses"
_SCHEMA_VERSION = 1


def _default_root() -> Path:
    """Return the tracker data root.

    Prefers a project-local ``data/stock_tracker`` directory so the tracker's
    state (watchlist, snapshots, analysis) travels with the project folder and
    can be shared across devices. Falls back to the ``VIBE_TRADING_HOME``
    override, or ``~/.vibe-trading`` when run outside the repo (e.g. an
    installed package).
    """
    env_root = os.environ.get("VIBE_TRADING_HOME", "").strip()
    if env_root:
        return Path(env_root).expanduser() / "stock_tracker"
    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "pyproject.toml").exists():
        return repo_root / "data" / "stock_tracker"
    return get_runtime_root() / "stock_tracker"


class TrackerStore:
    """JSON file store for tracker configuration and daily snapshots.

    All writes are atomic (temp file + ``os.replace`` + fsync) and land under
    the project-local data directory (or the runtime root when run outside the
    repo) so they never pollute the working tree.
    """

    def __init__(self, root_dir: Path | str | None = None) -> None:
        self.root = Path(root_dir) if root_dir else _default_root()
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
    # LLM analysis
    # ------------------------------------------------------------------

    def save_analysis(
        self,
        report: Dict[str, Any],
        trading_date: Optional[date] = None,
        generated_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Persist a new LLM analysis report and return its envelope."""
        gen = generated_at or datetime.now().astimezone()
        analysis_id = gen.strftime("%Y%m%dT%H%M%S%f")
        envelope = {
            "id": analysis_id,
            "trading_date": trading_date.isoformat() if trading_date else None,
            "generated_at": gen.isoformat(),
            "report": report,
        }
        self._write_json(self._analysis_path(analysis_id), envelope)
        return envelope

    def get_analysis(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        """Load one analysis envelope by id."""
        path = self._analysis_path(analysis_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load analysis %s: %s", analysis_id, exc)
            return None

    def get_latest_analysis(self) -> Optional[Dict[str, Any]]:
        """Load the most recently persisted analysis envelope, if any."""
        ids = self._list_analysis_ids()
        return self.get_analysis(ids[-1]) if ids else None

    def list_analyses(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return newest-first analysis summaries for the picker."""
        items: List[Dict[str, Any]] = []
        for analysis_id in self._list_analysis_ids():
            envelope = self.get_analysis(analysis_id)
            if envelope is None:
                continue
            report = envelope.get("report")
            summary = str(report.get("summary") or "") if isinstance(report, dict) else ""
            items.append(
                {
                    "id": analysis_id,
                    "trading_date": envelope.get("trading_date"),
                    "generated_at": envelope.get("generated_at"),
                    "summary": summary,
                }
            )
        items.sort(key=lambda item: item.get("generated_at") or "", reverse=True)
        return items[: max(1, min(limit, 200))]

    def _analysis_path(self, analysis_id: str) -> Path:
        return self.root / _ANALYSES_DIRNAME / f"{analysis_id}.json"

    def _list_analysis_ids(self) -> List[str]:
        """Return analysis ids sorted oldest-first by filename."""
        directory = self.root / _ANALYSES_DIRNAME
        if not directory.exists():
            return []
        ids = [
            entry.stem
            for entry in directory.iterdir()
            if entry.is_file() and entry.suffix == ".json"
        ]
        return sorted(ids)

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
