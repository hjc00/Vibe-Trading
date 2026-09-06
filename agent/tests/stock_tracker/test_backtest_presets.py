"""Unit tests for user-saved backtest rule presets persistence."""

from __future__ import annotations

import pytest

from src.stock_tracker.backtest_data import BacktestPresetStore, list_presets

VALID_SPEC = {
    "buy": {
        "mode": "and",
        "conditions": [
            {"primitive": "fast_ma_above_slow", "trigger": "edge_up", "params": {"fast": 5, "slow": 20}}
        ],
    },
    "sell": {
        "mode": "and",
        "conditions": [
            {"primitive": "fast_ma_above_slow", "trigger": "edge_down", "params": {"fast": 5, "slow": 20}}
        ],
    },
}


@pytest.mark.unit
def test_list_empty_when_no_file(tmp_path) -> None:
    assert BacktestPresetStore(root=tmp_path).list() == []


@pytest.mark.unit
def test_save_returns_preset_and_persists(tmp_path) -> None:
    preset = BacktestPresetStore(root=tmp_path).save("双均线金叉", VALID_SPEC)
    assert preset["id"].startswith("custom_")
    assert preset["label"] == "双均线金叉"
    assert preset["spec"] == VALID_SPEC

    # A fresh store over the same root sees the persisted preset.
    loaded = BacktestPresetStore(root=tmp_path).list()
    assert len(loaded) == 1
    assert loaded[0]["id"] == preset["id"]
    assert loaded[0]["label"] == "双均线金叉"


@pytest.mark.unit
def test_save_strips_and_caps_label(tmp_path) -> None:
    store = BacktestPresetStore(root=tmp_path)
    long_label = "长" * 100
    preset = store.save(f"  {long_label}  ", VALID_SPEC)
    assert preset["label"] == long_label[:64]
    assert store.list()[0]["label"] == long_label[:64]


@pytest.mark.unit
def test_save_rejects_empty_label(tmp_path) -> None:
    store = BacktestPresetStore(root=tmp_path)
    with pytest.raises(ValueError):
        store.save("   ", VALID_SPEC)
    assert store.list() == []


@pytest.mark.unit
def test_save_rejects_invalid_spec(tmp_path) -> None:
    store = BacktestPresetStore(root=tmp_path)
    invalid = {"buy": {"mode": "and", "conditions": []}, "sell": {"conditions": []}}
    with pytest.raises(ValueError):
        store.save("bad", invalid)
    assert store.list() == []


@pytest.mark.unit
def test_delete_removes_preset(tmp_path) -> None:
    store = BacktestPresetStore(root=tmp_path)
    preset = store.save("macd", VALID_SPEC)
    assert store.delete(preset["id"]) is True
    assert store.list() == []
    # Deleting again (or a missing id) is a no-op.
    assert store.delete(preset["id"]) is False


@pytest.mark.unit
def test_list_ignores_corrupt_file(tmp_path) -> None:
    path = tmp_path / "backtest_presets.json"
    path.write_text("{ not json", encoding="utf-8")
    assert BacktestPresetStore(root=tmp_path).list() == []


@pytest.mark.unit
def test_builtin_presets_are_not_custom() -> None:
    presets = list_presets()
    assert presets
    assert all(preset.get("custom") is False for preset in presets)
