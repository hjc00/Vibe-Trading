"""Unit tests for optional Tushare fallback adapters.

The Tushare client is replaced by small in-memory fakes, so these tests never
touch the network or require a real token.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.tools import tushare_fallbacks as tf


def test_fund_flow_maps_moneyflow_buckets_to_existing_schema() -> None:
    pro = SimpleNamespace(
        moneyflow=lambda **_: [
            {
                "trade_date": "20240103",
                "net_mf_amount": 12.5,
                "buy_sm_amount": 3.0,
                "sell_sm_amount": 1.0,
                "buy_md_amount": 5.0,
                "sell_md_amount": 8.0,
                "buy_lg_amount": 20.0,
                "sell_lg_amount": 7.0,
                "buy_elg_amount": 30.0,
                "sell_elg_amount": 10.0,
            }
        ]
    )
    with patch.object(tf, "_pro_api", return_value=pro), patch.object(
        tf, "_date_window", return_value=("20240101", "20240103")
    ):
        result = tf.fetch_fund_flow("600519.SH", days=5)

    row = result["rows"][0]
    assert result["source"] == "tushare"
    assert row["timestamp"] == "2024-01-03"
    assert row["main"] == 125000.0
    assert row["small"] == 20000.0
    assert row["medium"] == -30000.0
    assert row["large"] == 130000.0
    assert row["super_large"] == 200000.0


def test_dragon_tiger_maps_top_list_and_top_inst() -> None:
    pro = SimpleNamespace(
        top_list=lambda **_: [
            {
                "ts_code": "600519.SH",
                "name": "Kweichow Moutai",
                "close": 1700.0,
                "pct_change": 5.2,
                "net_amount": 1.2e8,
                "l_buy": 3.0e8,
                "l_sell": 1.8e8,
                "amount": 9.0e8,
                "reason": "daily move",
            }
        ],
        top_inst=lambda **_: [
            {"exalter": "Institution", "side": "0", "buy": 2.0e8, "sell": 0.0, "net_buy": 2.0e8}
        ],
    )
    with patch.object(tf, "_pro_api", return_value=pro):
        data = tf.fetch_dragon_tiger("2024-01-02", "600519")

    assert data["date"] == "2024-01-02"
    assert data["appearances"][0]["code"] == "600519"
    assert data["appearances"][0]["net_buy"] == 1.2e8
    assert data["seats"][0]["seat"] == "Institution"
    assert data["seats"][0]["net"] == 2.0e8


def test_northbound_converts_tushare_million_yuan_to_10k_cny() -> None:
    pro = SimpleNamespace(
        moneyflow_hsgt=lambda **_: [
            {"trade_date": "20240102", "hgt": 12.0, "sgt": -2.0, "north_money": 10.0},
            {"trade_date": "20240103", "hgt": 3.5, "sgt": 1.0, "north_money": 4.5},
        ]
    )
    with patch.object(tf, "_pro_api", return_value=pro), patch.object(
        tf, "_date_window", return_value=("20240101", "20240103")
    ):
        data = tf.fetch_northbound_flow(lookback_days=2)

    assert data["unit"] == "10k CNY"
    assert data["history"][0]["shanghai_connect"] == 1200.0
    assert data["history"][0]["total"] == 1000.0
    assert data["realtime"]["total"] == 450.0


def test_margin_trading_maps_and_sorts_most_recent_first() -> None:
    pro = SimpleNamespace(
        margin_detail=lambda **_: [
            {"trade_date": "20240102", "rzye": 1.0, "rzmre": 2.0, "rzche": 3.0, "rqye": 4.0, "rqyl": 5.0, "rzrqye": 6.0},
            {"trade_date": "20240103", "rzye": 7.0, "rzmre": 8.0, "rzche": 9.0, "rqye": 10.0, "rqyl": 11.0, "rzrqye": 12.0},
        ]
    )
    with patch.object(tf, "_pro_api", return_value=pro), patch.object(
        tf, "_date_window", return_value=("20240101", "20240103")
    ):
        data = tf.fetch_margin_trading("600519.SH", days=5)

    assert data["ts_code"] == "600519.SH"
    assert data["rows"][0]["trade_date"] == "2024-01-03"
    assert data["rows"][0]["financing_balance"] == 7.0
    assert data["rows"][1]["margin_total_balance"] == 6.0


def test_forecast_maps_announcements_newest_first() -> None:
    pro = SimpleNamespace(
        forecast=lambda **_: [
            {"ts_code": "600519.SH", "ann_date": "20240110", "end_date": "20231231",
             "type": "预增", "p_change_min": 20.0, "p_change_max": 30.0,
             "net_profit_min": 1000.0, "net_profit_max": 1100.0},
            {"ts_code": "600519.SH", "ann_date": "20231010", "end_date": "20230930",
             "type": "略增", "p_change_min": 5.0, "p_change_max": 10.0,
             "net_profit_min": 900.0, "net_profit_max": 950.0},
        ]
    )
    with patch.object(tf, "_pro_api", return_value=pro):
        data = tf.fetch_forecast("600519.SH")

    assert data["ts_code"] == "600519.SH"
    assert data["source"] == "tushare"
    # Newest announcement first.
    assert data["rows"][0]["ann_date"] == "2024-01-10"
    assert data["rows"][0]["type"] == "预增"
    assert data["rows"][0]["p_change_min"] == 20.0
    assert data["rows"][1]["ann_date"] == "2023-10-10"


def test_holder_trade_maps_in_de_and_ratio() -> None:
    pro = SimpleNamespace(
        stk_holdertrade=lambda **_: [
            {"ts_code": "600519.SH", "ann_date": "20240108", "holder_name": "大股东",
             "holder_type": "控股股东", "in_de": "DE", "change_vol": 500.0,
             "change_ratio": 1.5, "avg_price": 1500.0},
            {"ts_code": "600519.SH", "ann_date": "20240115", "holder_name": "高管",
             "holder_type": "高管", "in_de": "IN", "change_vol": 20.0,
             "change_ratio": 0.05, "avg_price": 1520.0},
        ]
    )
    with patch.object(tf, "_pro_api", return_value=pro), patch.object(
        tf, "_date_window", return_value=("20231001", "20240115")
    ):
        data = tf.fetch_holder_trade("600519.SH")

    assert data["source"] == "tushare"
    # Sorted by announcement date ascending.
    assert [r["in_de"] for r in data["rows"]] == ["DE", "IN"]
    assert data["rows"][0]["change_ratio"] == 1.5
    assert data["rows"][1]["holder_type"] == "高管"


def test_forecast_raises_when_token_missing() -> None:
    with patch.object(
        tf, "_pro_api", side_effect=tf.TushareFallbackUnavailable("TUSHARE_TOKEN is not configured")
    ):
        import pytest

        with pytest.raises(tf.TushareFallbackUnavailable):
            tf.fetch_forecast("600519.SH")
