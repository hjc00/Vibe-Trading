"""LLM analysis for A-share tracker snapshots.

Wraps the existing ``ChatLLM`` into a focused helper that serializes a subset
of a ``TrackerSnapshot`` (selected symbols plus sector strength) into a prompt,
calls the configured model, and normalizes the reply into a structured
``AnalysisReport`` with actionable per-symbol recommendations (action, price
zones, stop-loss, tracking metrics) the API can hand straight to the frontend.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.providers.chat import ChatLLM
from src.stock_tracker.models import (
    AnalysisAction,
    AnalysisReport,
    PortfolioInsight,
    PriceZone,
    SymbolRecommendation,
    SymbolSnapshot,
    TrackerSnapshot,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a quantitative analyst reviewing A-share technical, capital-flow, \
fundamental and risk signals. You work strictly from the tracker data provided \
and give research-oriented commentary, not personalized investment advice. \
Be concise and data-driven. Do not invent metrics that are not in the input. \
Price levels (entry/target/stop zones) are research estimates derived from \
support/resistance, ATR stop distance and valuation bands. Write all narrative \
fields in Chinese.
"""

_ANALYSIS_DIRECTIVE = """\
对所选标的做一次全维度的定量研究分析，综合权衡：
- 技术面：各周期信号（放量/突破/均线排列/RSI）、RPS 市场与行业分位、周期间趋势；
- 资金面：主力资金流向（当日与 5 日累计）、融资融券余额变化；
- 估值与质量：PE/PB 的 3 年分位（近 1 年分位为辅）、基本面质量评分、ROE 稳定性、现金流质量；
- 风险：ATR 波动、最大回撤、Beta、距止损参考价的距离；
- 行业背景：所属行业景气度评分、板块强弱排名与资金流。

不要套用一个固定模板，按每个标的实际的数据特点给出差异化判断；用 structured 的
action 与价位区间把结论表达清楚，让报告可直接被跟踪验证。
"""

_OUTPUT_INSTRUCTIONS = """\
Return ONLY a single valid JSON object (no markdown fences, no commentary \
outside the JSON) with exactly this shape:

{
  "summary": "一段中文综述",
  "symbols": [
    {
      "code": "600519.SH",
      "name": "贵州茅台",
      "action": "buy",
      "confidence": 75,
      "rationale": "基于信号的简要中文判断",
      "entry_zone": {"low": 1400.0, "high": 1450.0},
      "target_zone": {"low": 1600.0, "high": 1700.0},
      "stop_loss": 1350.0,
      "reduce_trigger": "跌破 20 日均线且放量，或主力资金连续 3 日净流出",
      "track_metrics": ["rsi", "volume_ratio", "roe"],
      "time_horizon": "2-4 周",
      "risks": ["风险1", "风险2"],
      "key_metrics": {"rsi": 55.0, "volume_ratio": 1.5}
    }
  ],
  "portfolio": {
    "theme": "组合层面的一句话主题",
    "top_pick": "600519.SH",
    "cautions": ["组合层面提示"]
  },
  "caveats": ["数据或结论的局限性说明"]
}

Rules:
- "action" MUST be one of: buy, hold, reduce, avoid.
- "confidence" is an integer 0-100 (your conviction in the action).
- "entry_zone"/"target_zone" are price bands; use null when not applicable.
- "stop_loss" is a single reference price below the entry zone; null when N/A.
- "reduce_trigger" states the concrete condition that would invalidate the call.
- Include one entry in "symbols" for every input symbol.
"""

# Legacy/loose action vocabularies mapped onto the structured 4-value action.
_ACTION_SYNONYMS: Dict[str, AnalysisAction] = {
    "buy": AnalysisAction.BUY,
    "strong_buy": AnalysisAction.BUY,
    "top_pick": AnalysisAction.BUY,
    "hold": AnalysisAction.HOLD,
    "watch": AnalysisAction.HOLD,
    "reduce": AnalysisAction.REDUCE,
    "reduce_position": AnalysisAction.REDUCE,
    "sell": AnalysisAction.REDUCE,
    "caution": AnalysisAction.REDUCE,
    "avoid": AnalysisAction.AVOID,
    "avoid_position": AnalysisAction.AVOID,
}


def _num(value: Any) -> Optional[float]:
    """Best-effort float coercion; returns None for non-numeric input."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def _coerce_action(value: Any, legacy: Any = None) -> AnalysisAction:
    """Map an action token (or a legacy recommendation) to a valid action.

    An unrecognized explicit ``action`` degrades to ``avoid`` (do not mislabel),
    while a missing action falls back to the legacy ``recommendation`` and then
    to the neutral ``hold``.
    """
    if isinstance(value, str):
        hit = _ACTION_SYNONYMS.get(value.strip().lower())
        if hit:
            return hit
        return AnalysisAction.AVOID
    if isinstance(legacy, str):
        hit = _ACTION_SYNONYMS.get(legacy.strip().lower())
        if hit:
            return hit
    return AnalysisAction.HOLD


def _clamp_conf(value: Any) -> Optional[float]:
    """Coerce a confidence value into 0-100, mapping legacy word levels."""
    if value is None:
        return None
    if isinstance(value, str):
        mapping = {"high": 80.0, "medium": 50.0, "low": 30.0}
        hit = mapping.get(value.strip().lower())
        if hit is not None:
            return hit
        value = _num(value)
    if value is None:
        return None
    return max(0.0, min(100.0, value))


def _parse_price_zone(value: Any) -> Optional[PriceZone]:
    """Parse a price band from {low,high}, [low, high] or a single number."""
    if value is None or isinstance(value, PriceZone):
        return value
    low = high = None
    if isinstance(value, dict):
        low = _num(value.get("low"))
        high = _num(value.get("high"))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        low, high = _num(value[0]), _num(value[1])
    elif isinstance(value, (int, float)):
        low = high = float(value)
    if low is None and high is None:
        return None
    if low is not None and high is not None and low > high:
        low, high = high, low
    return PriceZone(low=low, high=high)


def _coerce_str_list(value: Any) -> List[str]:
    """Coerce a comma-separated string or a list into a trimmed string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _serialize_symbol(symbol: SymbolSnapshot) -> Dict[str, Any]:
    """Serialize a single symbol into a compact, JSON-safe context dict."""
    capital = None
    if symbol.capital is not None:
        ff = symbol.capital.fund_flow
        margin = symbol.capital.margin
        capital = {
            "fund_flow": {
                "trade_date": ff.trade_date.isoformat() if ff.trade_date else None,
                "main_net": ff.main_net,
                "main_net_ratio": ff.main_net_ratio,
                "main_5d_net": ff.main_5d_net,
                "super_large_net": ff.super_large_net,
                "large_net": ff.large_net,
                "medium_net": ff.medium_net,
                "small_net": ff.small_net,
            },
            "margin": {
                "trade_date": margin.trade_date.isoformat() if margin.trade_date else None,
                "financing_balance": margin.financing_balance,
                "financing_balance_change": margin.financing_balance_change,
                "margin_total_balance": margin.margin_total_balance,
                "margin_total_change": margin.margin_total_change,
            },
            "fund_flow_source": symbol.capital.fund_flow_source,
            "margin_source": symbol.capital.margin_source,
            "fund_flow_error": symbol.capital.fund_flow_error,
            "margin_error": symbol.capital.margin_error,
        }

    def _dump(obj: Any) -> Any:
        return obj.model_dump(mode="json") if obj is not None else None

    return {
        "code": symbol.code,
        "name": symbol.name,
        "close": symbol.close,
        "daily_return": symbol.daily_return,
        "volume": symbol.volume,
        "avg_volume_20": symbol.avg_volume_20,
        "period_signals": {
            period: ps.model_dump(mode="json")
            for period, ps in symbol.period_signals.items()
        },
        "capital": capital,
        "risk": _dump(symbol.risk),
        "valuation": _dump(symbol.valuation),
        "sector_board": symbol.sector_board,
        "sector_strength_rank": symbol.sector_strength_rank,
        "diff": _dump(symbol.diff),
    }


def _serialize_sector(sector: Any) -> Optional[Dict[str, Any]]:
    """Project one sector strength record into a compact context dict."""
    if sector is None:
        return None
    return {
        "board_name": sector.board_name,
        "change_pct": sector.change_pct,
        "fund_flow_net": sector.fund_flow_net,
        "market_rank": sector.market_rank,
        "prosperity_score": sector.prosperity_score,
        "avg_roe": sector.avg_roe,
        "avg_gross_margin": sector.avg_gross_margin,
        "avg_revenue_yoy": sector.avg_revenue_yoy,
        "members": sector.members,
    }


def _normalize_symbol(item: Any) -> Optional[SymbolRecommendation]:
    """Coerce one parsed symbol dict into a valid recommendation or drop it."""
    if not isinstance(item, dict):
        return None
    action = _coerce_action(item.get("action"), item.get("recommendation"))
    try:
        rec = SymbolRecommendation(
            code=str(item.get("code") or "").strip(),
            name=str(item["name"]).strip() if item.get("name") is not None else None,
            action=action,
            confidence=_clamp_conf(item.get("confidence")),
            rationale=str(item.get("rationale") or "").strip(),
            entry_zone=_parse_price_zone(item.get("entry_zone") or item.get("entry")),
            target_zone=_parse_price_zone(item.get("target_zone") or item.get("target")),
            stop_loss=_num(item.get("stop_loss")),
            reduce_trigger=(
                str(item.get("reduce_trigger") or "").strip() or None
            ),
            track_metrics=_coerce_str_list(item.get("track_metrics")),
            time_horizon=(
                str(item.get("time_horizon") or "").strip() or None
            ),
            risks=_coerce_str_list(item.get("risks")),
            key_metrics=item.get("key_metrics")
            if isinstance(item.get("key_metrics"), dict)
            else {},
        )
    except Exception:  # noqa: BLE001
        logger.debug("Dropping malformed analyzer symbol entry: %s", item)
        return None
    if not rec.code:
        return None
    return rec


def build_analysis_prompt(
    snapshot: TrackerSnapshot,
    symbols: List[SymbolSnapshot],
    user_prompt: Optional[str] = None,
) -> str:
    """Build the user prompt from a snapshot and the selected symbols."""
    context = {
        "trading_date": snapshot.trading_date.isoformat() if snapshot.trading_date else None,
        "periods": snapshot.config.periods,
        "signals": snapshot.config.signals,
        "thresholds": snapshot.config.thresholds.model_dump(mode="json"),
        "rankings": snapshot.rankings,
        "sectors": [
            _serialize_sector(s) for s in snapshot.sectors if s is not None
        ],
        "symbols": [_serialize_symbol(s) for s in symbols],
    }
    extra = f"\n用户补充指令：{user_prompt}" if user_prompt else ""

    return (
        f"{_ANALYSIS_DIRECTIVE}{extra}\n\n"
        f"追踪快照数据（JSON）：\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"{_OUTPUT_INSTRUCTIONS}"
    )


def _extract_json(text: str) -> Dict[str, Any]:
    """Best-effort parse of a JSON object embedded in an LLM reply."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences, then fall back to the first balanced object.
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_report(parsed: Dict[str, Any], raw_text: str) -> AnalysisReport:
    """Coerce a parsed LLM reply into a structured :class:`AnalysisReport`."""
    raw_symbols = parsed.get("symbols", [])
    if not isinstance(raw_symbols, list):
        raw_symbols = []
    symbols = [
        rec for rec in (_normalize_symbol(s) for s in raw_symbols) if rec is not None
    ]

    portfolio = parsed.get("portfolio", {})
    if not isinstance(portfolio, dict):
        portfolio = {}

    return AnalysisReport(
        summary=str(parsed.get("summary") or "").strip() or raw_text.strip(),
        symbols=symbols,
        portfolio=PortfolioInsight(
            theme=str(portfolio.get("theme") or "").strip(),
            top_pick=portfolio.get("top_pick"),
            cautions=_coerce_str_list(portfolio.get("cautions")),
        ),
        caveats=_coerce_str_list(parsed.get("caveats")),
    )


def run_analysis(
    snapshot: TrackerSnapshot,
    symbols: List[SymbolSnapshot],
    user_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Call the configured LLM and return a normalized report dict.

    This is synchronous by design so it can be offloaded to a worker thread by
    the API route via ``asyncio.to_thread``.
    """
    prompt = build_analysis_prompt(snapshot, symbols, user_prompt=user_prompt)
    llm = ChatLLM()
    logger.info(
        "Stock-tracker LLM analysis start: %d symbol(s), provider=%s, model=%s",
        len(symbols),
        llm.runtime_snapshot.provider,
        llm.model_name,
    )
    try:
        response = llm.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
    finally:
        llm.close()

    raw_text = (response.content or "").strip()
    parsed = _extract_json(raw_text)
    report = _normalize_report(parsed, raw_text)
    return report.model_dump(mode="json")


__all__ = [
    "build_analysis_prompt",
    "run_analysis",
]
