"""Unit tests for the stock tracker LLM analyzer."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from src.stock_tracker.analyzer import (
    _extract_json,
    _normalize_report,
    build_analysis_prompt,
    run_analysis,
)
from src.stock_tracker.models import (
    PeriodMetrics,
    PeriodSignals,
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
    prompt = build_analysis_prompt(snapshot, snapshot.symbols, "rank_opportunities")
    assert "600519.SH" in prompt
    assert "贵州茅台" in prompt
    assert "rank_opportunities" not in prompt  # directive is Chinese, not the enum key
    assert "watch" in prompt  # output vocabulary is present


def test_build_analysis_prompt_appends_custom_prompt():
    snapshot = _snapshot()
    prompt = build_analysis_prompt(snapshot, snapshot.symbols, "custom", "重点看均线")
    assert "重点看均线" in prompt


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
    assert report["summary"] == "s"
    assert report["symbols"] == [{"code": "x"}]
    assert report["portfolio"] == {"theme": "", "top_pick": None, "cautions": []}
    assert report["caveats"] == []


def test_run_analysis_returns_normalized_report():
    snapshot = _snapshot()
    with patch("src.stock_tracker.analyzer.ChatLLM") as mock_cls:
        instance = mock_cls.return_value
        instance.chat.return_value.content = '{"summary": "ok", "symbols": [], "portfolio": {}}'
        report = run_analysis(snapshot, snapshot.symbols, "rank_opportunities")
    assert report["summary"] == "ok"
    assert report["symbols"] == []
    instance.close.assert_called_once()


def test_run_analysis_falls_back_to_raw_text():
    snapshot = _snapshot()
    with patch("src.stock_tracker.analyzer.ChatLLM") as mock_cls:
        instance = mock_cls.return_value
        instance.chat.return_value.content = "无法分析"
        report = run_analysis(snapshot, snapshot.symbols, "risk_check")
    assert report["summary"] == "无法分析"
    assert report["symbols"] == []
