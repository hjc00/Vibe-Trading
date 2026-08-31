"""Unit tests for stock tracker model helpers."""

from __future__ import annotations

import pytest

from src.stock_tracker.models import TrackerConfig, normalize_a_share_code


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
