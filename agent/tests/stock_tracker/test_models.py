"""Unit tests for stock tracker model helpers."""

from __future__ import annotations

import pytest

from src.stock_tracker.models import TrackerConfig, TrackerThresholds, normalize_a_share_code


def test_tracker_thresholds_dynamic_key_preserved() -> None:
    thresholds = TrackerThresholds(volume_spike=3.0, custom_param=5.0)
    assert thresholds.get("volume_spike") == 3.0
    assert thresholds.get("custom_param") == 5.0
    dumped = thresholds.model_dump()
    assert dumped["volume_spike"] == 3.0
    assert dumped["custom_param"] == 5.0


def test_tracker_thresholds_defaults() -> None:
    thresholds = TrackerThresholds()
    assert thresholds.get("volume_spike") == 2.0
    assert thresholds.get("rsi_overbought") == 70.0
    assert thresholds.get("rsi_oversold") == 30.0
    assert thresholds.get("breakout_window") == 20
    assert thresholds.get("missing", "fallback") == "fallback"


def test_tracker_config_rejects_unknown_signal() -> None:
    with pytest.raises(ValueError, match="Unknown signal"):
        TrackerConfig(signals=["not_a_signal"])


def test_tracker_config_accepts_rsi() -> None:
    config = TrackerConfig(signals=["volume_spike", "rsi"])
    assert "rsi" in config.signals
    assert "volume_spike" in config.signals


@pytest.mark.parametrize(
    ("input_code", "expected"),
    [
        ("603228", "603228.SH"),
        ("000938", "000938.SZ"),
        ("300750", "300750.SZ"),
        ("688888", "688888.SH"),
        ("000001.SZ", "000001.SZ"),
        ("600519.SH", "600519.SH"),
        # Wrong suffix should be corrected based on prefix.
        ("000938.SH", "000938.SZ"),
        ("600519.SZ", "600519.SH"),
        ("300750.SH", "300750.SZ"),
        # Whitespace and case are normalized.
        ("  603228  ", "603228.SH"),
        ("603228.sh", "603228.SH"),
    ],
)
def test_normalize_a_share_code(input_code: str, expected: str) -> None:
    assert normalize_a_share_code(input_code) == expected


@pytest.mark.parametrize(
    "input_code",
    [
        "INVALID",
        "12345",  # too short
        "1234567",  # too long
        "123456.XY",  # unknown suffix
    ],
)
def test_normalize_a_share_code_rejects_invalid(input_code: str) -> None:
    assert normalize_a_share_code(input_code) is None


def test_tracker_config_corrects_wrong_suffix() -> None:
    config = TrackerConfig(watchlist=["000938.SH"])
    assert config.watchlist == ["000938.SZ"]
