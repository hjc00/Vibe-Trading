"""Unit tests for the A-share name resolver."""

from __future__ import annotations

import urllib.request
from io import BytesIO
from unittest.mock import patch

from src.stock_tracker.names import fetch_a_share_names


def _mock_response(payload: bytes) -> urllib.request.addinfourl:
    """Wrap bytes in the minimal addinfourl interface."""
    resp = urllib.request.addinfourl(
        BytesIO(payload),
        {"Content-Type": "text/plain"},
        "https://qt.gtimg.cn/q=test",
    )
    resp.code = 200  # type: ignore[attr-defined]
    return resp


def test_fetch_a_share_names_parses_gbk_response():
    # GBK-encoded names: 景旺电子 for sh603228, 平安银行 for sz000001.
    raw = (
        b'v_sh603228="1~\xbe\xb0\xcd\xfa\xb5\xe7\xd7\xd3~603228~96.30~...";\n'
        b'v_sz000001="51~\xc6\xbd\xb0\xb2\xd2\xf8\xd0\xd0~000001~11.60~...";'
    )

    with patch("urllib.request.urlopen", return_value=_mock_response(raw)):
        names = fetch_a_share_names(["603228.SH", "000001.SZ"])

    assert names == {"603228.SH": "景旺电子", "000001.SZ": "平安银行"}


def test_fetch_a_share_names_ignores_missing_codes():
    raw = b'v_sh603228="1~\xbe\xb0\xcd\xfa\xb5\xe7\xd7\xd3~603228~...";'

    with patch("urllib.request.urlopen", return_value=_mock_response(raw)):
        names = fetch_a_share_names(["603228.SH", "999999.SH"])

    assert names == {"603228.SH": "景旺电子"}


def test_fetch_a_share_names_returns_empty_for_non_a_share():
    assert fetch_a_share_names(["AAPL.US"]) == {}


def test_fetch_a_share_names_tolerates_network_errors():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        names = fetch_a_share_names(["603228.SH"])
    assert names == {}
