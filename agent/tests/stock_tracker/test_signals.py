"""Tests for the signal detector registry and metadata."""

from __future__ import annotations

import pytest

from src.stock_tracker.signals import (
    RSIDetector,
    get_detector,
    get_detector_meta,
    list_detector_meta,
    list_detector_names,
)


def test_registry_contains_builtin_detectors() -> None:
    names = list_detector_names()
    assert "volume_spike" in names
    assert "breakout" in names
    assert "ma_alignment" in names
    assert "rsi" in names


def test_list_detector_meta_is_serializable() -> None:
    metas = list_detector_meta()
    assert len(metas) >= 4
    for meta in metas:
        dumped = meta.model_dump()
        assert dumped["name"]
        assert "params" in dumped
        assert "ranking_enabled" in dumped


def test_get_detector_meta_returns_expected_fields() -> None:
    meta = get_detector_meta("volume_spike")
    assert meta.name == "volume_spike"
    assert meta.format == "multiple"
    assert "volume_spike" in meta.params
    assert meta.ranking_enabled is True


def test_ma_alignment_is_global_not_table() -> None:
    meta = get_detector_meta("ma_alignment")
    assert meta.is_global is True
    assert meta.show_in_table is False
    assert meta.ranking_enabled is False


def test_rsi_meta_declares_params() -> None:
    meta = get_detector_meta("rsi")
    assert meta.params["rsi_overbought"]["default"] == 70.0
    assert meta.params["rsi_oversold"]["default"] == 30.0


def test_get_detector_caches_instance() -> None:
    first = get_detector("rsi")
    second = get_detector("rsi")
    assert first is second
    assert isinstance(first, RSIDetector)


def test_registry_contains_margin_detector() -> None:
    names = list_detector_names()
    assert "margin_expansion" in names


def test_margin_expansion_meta_declares_params() -> None:
    meta = get_detector_meta("margin_expansion")
    assert meta.category == "capital"
    assert meta.direction == "bullish"
    assert "margin_expansion_threshold" in meta.params
    assert meta.params["margin_expansion_threshold"]["default"] == 0.03


def test_get_detector_caches_margin_instance() -> None:
    from src.stock_tracker.signals import MarginExpansionDetector

    first = get_detector("margin_expansion")
    second = get_detector("margin_expansion")
    assert first is second
    assert isinstance(first, MarginExpansionDetector)


def test_unknown_detector_raises() -> None:
    with pytest.raises(ValueError, match="Unknown signal type"):
        get_detector_meta("not_a_signal")
    with pytest.raises(ValueError, match="Unknown signal type"):
        get_detector("not_a_signal")
