"""Unit tests for the stock tracker LLM analyzer."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from src.stock_tracker.analyzer import (
    _extract_json,
    _normalize_report,
    _serialize_sector,
    _serialize_symbol,
    build_analysis_prompt,
    run_analysis,
)
from src.stock_tracker.models import (
    AnalysisAction,
    PeriodMetrics,
    PeriodSignals,
    RiskMetrics,
    SectorStrength,
    SignalState,
    SignalValue,
    SymbolSnapshot,
    TrackerConfig,
    TrackerSnapshot,
)


def _snapshot() -> TrackerSnapshot:
    symbol = SymbolSnapshot(
        code="600519.SH",
        name="贵州茅台",
        close=1500.0,
        daily_return=0.5,
        period_signals={
            "10": PeriodSignals(
                metrics=PeriodMetrics(period=10, rsi=55.0, volume_ratio=1.6),
                signals={
                    "volume_spike": SignalValue(
                        triggered=True, state=SignalState.TRIGGERED, value=1.6, threshold=2.0
                    )
                },
            )
        },
    )
    return TrackerSnapshot(
        generated_at=datetime.now(timezone.utc),
        trading_date=date(2026, 8, 31),
        config=TrackerConfig(),
        symbols=[symbol],
    )


def test_build_analysis_prompt_includes_symbol_data():
    snapshot = _snapshot()
    prompt = build_analysis_prompt(snapshot, snapshot.symbols)
    assert "600519.SH" in prompt
    assert "贵州茅台" in prompt
    assert "buy" in prompt  # structured action vocabulary is present
    assert "avoid" in prompt


def test_build_analysis_prompt_appends_user_prompt():
    snapshot = _snapshot()
    prompt = build_analysis_prompt(snapshot, snapshot.symbols, "重点看均线多头排列")
    assert "重点看均线多头排列" in prompt


def test_build_analysis_prompt_omits_user_prompt_when_empty():
    snapshot = _snapshot()
    prompt = build_analysis_prompt(snapshot, snapshot.symbols)
    assert "用户补充指令" not in prompt


def test_serialize_symbol_carries_capital_risk_and_sector():
    symbol = _snapshot().symbols[0]
    symbol.risk = RiskMetrics(atr_14=8.0, stop_loss_price=1490.0)
    data = _serialize_symbol(symbol)
    assert data["code"] == "600519.SH"
    assert data["capital"] is None
    assert data["risk"] == {
        "atr_14": 8.0,
        "atr_pct": None,
        "max_drawdown_60d": None,
        "beta_vs_index": None,
        "beta_window": None,
        "benchmark_code": None,
        "stop_loss_price": 1490.0,
        "stop_loss_atr_multiple": None,
    }
    assert data["sector_board"] is None
    assert data["sector_strength_rank"] is None
    assert "period_signals" in data


def test_serialize_symbol_with_capital_is_json_safe():
    import json

    from src.stock_tracker.models import (
        CapitalMetrics,
        FundFlowSnapshot,
        MarginSnapshot,
    )

    symbol = _snapshot().symbols[0]
    symbol.capital = CapitalMetrics(
        fund_flow=FundFlowSnapshot(
            trade_date=date(2026, 8, 31),
            main_net=1_000_000.0,
            main_5d_net=500_000.0,
        ),
        margin=MarginSnapshot(
            trade_date=date(2026, 8, 31),
            financing_balance=20_000_000.0,
        ),
        fund_flow_source="eastmoney",
        margin_source="eastmoney",
    )
    data = _serialize_symbol(symbol)
    # date fields must serialize to ISO strings (not raw date objects).
    assert data["capital"]["fund_flow"]["trade_date"] == "2026-08-31"
    assert data["capital"]["margin"]["trade_date"] == "2026-08-31"
    assert data["capital"]["fund_flow"]["main_net"] == 1_000_000.0
    json.dumps(data, ensure_ascii=False)  # must not raise TypeError


def test_serialize_symbol_includes_events():
    from src.stock_tracker.models import EventItem, EventSnapshot

    symbol = _snapshot().symbols[0]
    symbol.events = EventSnapshot(
        as_of=date(2026, 8, 31),
        source="eastmoney",
        event_risk_score=88.0,
        high_risk_count=1,
        items=[
            EventItem(
                event_type="earnings_forecast",
                event_date=date(2026, 8, 20),
                title="业绩预减",
                risk_level="danger",
                risk_score=88.0,
                source="tushare",
                details={"forecast_type": "预减"},
            )
        ],
    )
    data = _serialize_symbol(symbol)
    assert data["events"] is not None
    assert data["events"]["event_risk_score"] == 88.0
    assert data["events"]["as_of"] == "2026-08-31"
    assert data["events"]["items"][0]["title"] == "业绩预减"
    assert data["events"]["items"][0]["risk_level"] == "danger"


def test_build_analysis_prompt_includes_event_risk_directive():
    snapshot = _snapshot()
    prompt = build_analysis_prompt(snapshot, snapshot.symbols)
    assert "事件日历" in prompt
    assert "综合事件风险分" in prompt


def test_serialize_sector_is_compact():
    sector = SectorStrength(
        board_name="白酒",
        change_pct=1.2,
        fund_flow_net=1e8,
        market_rank=3,
        prosperity_score=72.0,
        members=["600519.SH"],
    )
    data = _serialize_sector(sector)
    assert data["board_name"] == "白酒"
    assert data["change_pct"] == 1.2
    assert data["prosperity_score"] == 72.0
    assert data["members"] == ["600519.SH"]
    # history / per-period series are intentionally dropped for prompt size.
    assert "period_metrics" not in data


def test_extract_json_plain():
    assert _extract_json('{"summary": "hi"}') == {"summary": "hi"}


def test_extract_json_strips_markdown_fences():
    text = '```json\n{"summary": "hi"}\n```'
    assert _extract_json(text) == {"summary": "hi"}


def test_extract_json_invalid_returns_empty():
    assert _extract_json("not json at all") == {}


def test_normalize_report_fills_defaults():
    parsed = {"summary": "s", "symbols": [{"code": "x"}], "portfolio": {}}
    report = _normalize_report(parsed, "raw")
    assert report.summary == "s"
    assert len(report.symbols) == 1
    symbol = report.symbols[0]
    assert symbol.code == "x"
    assert symbol.action == AnalysisAction.HOLD
    assert symbol.confidence is None
    assert symbol.entry_zone is None
    assert report.portfolio.theme == ""
    assert report.portfolio.cautions == []


def test_normalize_report_maps_legacy_recommendation_to_action():
    cases = {
        "top_pick": AnalysisAction.BUY,
        "watch": AnalysisAction.HOLD,
        "hold": AnalysisAction.HOLD,
        "caution": AnalysisAction.REDUCE,
        "avoid": AnalysisAction.AVOID,
    }
    for legacy, expected in cases.items():
        parsed = {"symbols": [{"code": "x", "recommendation": legacy}]}
        report = _normalize_report(parsed, "")
        assert report.symbols[0].action == expected, legacy


def test_normalize_report_degrades_unknown_action_to_avoid():
    parsed = {"symbols": [{"code": "x", "action": "moon"}]}
    report = _normalize_report(parsed, "")
    assert report.symbols[0].action == AnalysisAction.AVOID


def test_normalize_report_clamps_confidence():
    parsed = {
        "symbols": [
            {"code": "a", "confidence": "high"},
            {"code": "b", "confidence": 120},
            {"code": "c", "confidence": -5},
            {"code": "d", "confidence": 75},
        ]
    }
    report = _normalize_report(parsed, "")
    conf = {s.code: s.confidence for s in report.symbols}
    assert conf["a"] == 80.0
    assert conf["b"] == 100.0
    assert conf["c"] == 0.0
    assert conf["d"] == 75.0


def test_normalize_report_parses_price_zones_leniently():
    parsed = {
        "symbols": [
            {"code": "a", "entry_zone": {"low": 10.0, "high": 12.0}},
            {"code": "b", "entry_zone": [10.0, 12.0]},
            {"code": "c", "entry_zone": {"high": 12.0, "low": 8.0}},
            {"code": "d", "target": 20.0},
        ]
    }
    report = _normalize_report(parsed, "")
    by_code = {s.code: s for s in report.symbols}
    assert (by_code["a"].entry_zone.low, by_code["a"].entry_zone.high) == (10.0, 12.0)
    assert (by_code["b"].entry_zone.low, by_code["b"].entry_zone.high) == (10.0, 12.0)
    # Out-of-order low/high gets swapped.
    assert (by_code["c"].entry_zone.low, by_code["c"].entry_zone.high) == (8.0, 12.0)
    assert (by_code["d"].target_zone.low, by_code["d"].target_zone.high) == (20.0, 20.0)


def test_normalize_report_drops_non_dict_symbols():
    parsed = {"symbols": ["junk", {"code": "x"}]}
    report = _normalize_report(parsed, "")
    assert [s.code for s in report.symbols] == ["x"]


def test_run_analysis_returns_normalized_report():
    snapshot = _snapshot()
    with patch("src.stock_tracker.analyzer.ChatLLM") as mock_cls:
        instance = mock_cls.return_value
        instance.chat.return_value.content = (
            '{"summary": "ok", "symbols": [], "portfolio": {}, "caveats": []}'
        )
        report = run_analysis(snapshot, snapshot.symbols)
    assert report["summary"] == "ok"
    assert report["symbols"] == []
    instance.close.assert_called_once()


def test_run_analysis_falls_back_to_raw_text():
    snapshot = _snapshot()
    with patch("src.stock_tracker.analyzer.ChatLLM") as mock_cls:
        instance = mock_cls.return_value
        instance.chat.return_value.content = "无法分析"
        report = run_analysis(snapshot, snapshot.symbols)
    assert report["summary"] == "无法分析"
    assert report["symbols"] == []
