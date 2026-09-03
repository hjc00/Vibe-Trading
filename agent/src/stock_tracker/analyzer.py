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
    ANALYSIS_INDICATORS,
    AnalysisAction,
    AnalysisReport,
    PortfolioInsight,
    PriceZone,
    SymbolRecommendation,
    SymbolSnapshot,
    TrackerSnapshot,
)

logger = logging.getLogger(__name__)

# Every known analysis indicator block; the default enabled set when a config
# or caller does not restrict the prompt to a subset.
_ALL_INDICATORS = frozenset(ANALYSIS_INDICATORS)

_SYSTEM_PROMPT = """\
You are a quantitative analyst reviewing A-share technical, capital-flow, \
fundamental and risk signals. You work strictly from the tracker data provided \
and give research-oriented commentary, not personalized investment advice. \
Be concise and data-driven. Do not invent metrics that are not in the input. \
Price levels (entry/target/stop zones) are research estimates derived from \
support/resistance, ATR stop distance and valuation bands. Write all narrative \
fields in Chinese.
"""

# One prompt directive bullet per analysis indicator block, keyed exactly as in
# ``ANALYSIS_INDICATORS``. A disabled block is dropped from the directive so the
# model only weighs the dimensions that are actually present in the JSON.
_INDICATOR_DIRECTIVES: Dict[str, str] = {
    "period_signals": "技术面：各周期信号（放量/突破/均线排列/RSI）、周期涨跌与量能、"
    "RPS 市场与行业分位、均线/RSI/波动率等趋势指标；",
    "fund_flow": "资金面·主力资金：当日主力净流入及占比、5 日累计净流入、超大单/大单/中单/小单分布；",
    "margin": "资金面·融资融券：融资余额及日变化、融资融券总余额及变化；",
    "risk": "风险：ATR 波动、距止损参考价的距离、最大回撤、Beta；",
    "valuation": "估值与质量：PE/PB/PS 历史分位、基本面质量评分、ROE 稳定性、现金流质量；",
    "events": "事件日历：未来 90 天解禁、业绩预告、龙虎榜、股东增减持，及综合事件风险分；",
    "concept": "题材热度：所属概念板块、最热概念排名、概念热度评分、概念内涨停家数；",
    "consensus": "一致预期：机构评级分布、目标价区间、一致预期 EPS、forward PE；",
    "chip": "筹码集中度：股东户数变化与趋势、户均持股、北向/公募持仓、集中度评分；",
    "sector": "行业背景：所属行业板块、板块强弱排名与资金流、行业景气度评分（若有）；",
    "diff": "跨日变化：相对上一交易日的信号增减、涨幅与排名变化；",
    "market_sentiment": "市场情绪：全市场涨停/跌停/炸板家数及炸板率、连板高度、昨日涨停溢价；",
    "sectors": "行业强度榜：全市场行业板块涨跌/资金流/景气度排行与 watchlist 聚合；",
    "concepts": "概念热度榜：全市场概念板块涨幅/主力净流入/涨停家数排行；",
}

_ANALYSIS_TAIL = """\
不要套用一个固定模板，按每个标的实际的数据特点给出差异化判断；用 structured 的
action 与价位区间把结论表达清楚，让报告可直接被跟踪验证。
"""


def _build_analysis_directive(enabled: "set[str]") -> str:
    """Compose the analysis directive from the enabled indicator blocks."""
    lines = ["对所选标的做一次定量研究分析，综合权衡："]
    lines.extend(
        f"- {_INDICATOR_DIRECTIVES[key]}"
        for key in ANALYSIS_INDICATORS
        if key in enabled
    )
    lines.append(_ANALYSIS_TAIL)
    return "\n".join(lines)

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

_HISTORY_DIRECTIVE = """\
上面 JSON 的 history 字段按代码分组，是本模型历史分析该标的时的最近几条结论及对照：
- action/confidence/entry_zone/target_zone/stop_loss/time_horizon 是当时给出的判断；
- status 是相对当前价（current_close）的验证结果：active=仍在区间/未到目标，
  hit_target=已到目标价上沿，stopped_out=已跌破止损价，pending=暂无当前价无法验证。

请基于这些历史结论做增量研判，不要把这批标的当成全新对象重新推一遍：
- 若上次判断未被价格证伪 → 说明维持/微调的原因；
- 若价格已到目标区间/逻辑被破坏 → 给出止盈、减仓或新的目标区间；
- 若已破止损或明确证伪 → 明确反转，不再重复给同一结论。
在每只标的的 rationale 中，用一句话点明本次相对其最近一次历史结论的变化
（维持 / 修正 / 反转 及核心理由）。若 history 为空或缺失，表示无历史，正常从头分析。
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


def _serialize_symbol(
    symbol: SymbolSnapshot, enabled: "set[str]" = _ALL_INDICATORS
) -> Dict[str, Any]:
    """Serialize one symbol into a compact, JSON-safe context dict.

    ``enabled`` is the subset of ``ANALYSIS_INDICATORS`` whose blocks are fed to
    the model. Every symbol carries identity fields regardless; a block that is
    *enabled* keeps its key even when empty (``None``/``{}``) so downstream shape
    stays stable, while a block that is *disabled* is omitted entirely so the
    model never reasons about data it was not asked to weigh.
    """
    data: Dict[str, Any] = {
        "code": symbol.code,
        "name": symbol.name,
        "close": symbol.close,
        "daily_return": symbol.daily_return,
        "volume": symbol.volume,
        "avg_volume_20": symbol.avg_volume_20,
    }

    if "period_signals" in enabled:
        data["period_signals"] = {
            period: ps.model_dump(mode="json")
            for period, ps in symbol.period_signals.items()
        }

    # ``capital`` is gated by either of its sub-blocks; each sub-block keeps its
    # own provenance fields so enabling only margin does not leak fund-flow data.
    if "fund_flow" in enabled or "margin" in enabled:
        data["capital"] = None
        if symbol.capital is not None:
            capital: Dict[str, Any] = {}
            if "fund_flow" in enabled and symbol.capital.fund_flow is not None:
                ff = symbol.capital.fund_flow
                capital["fund_flow"] = {
                    "trade_date": ff.trade_date.isoformat() if ff.trade_date else None,
                    "main_net": ff.main_net,
                    "main_net_ratio": ff.main_net_ratio,
                    "main_5d_net": ff.main_5d_net,
                    "super_large_net": ff.super_large_net,
                    "large_net": ff.large_net,
                    "medium_net": ff.medium_net,
                    "small_net": ff.small_net,
                }
                capital["fund_flow_source"] = symbol.capital.fund_flow_source
                capital["fund_flow_error"] = symbol.capital.fund_flow_error
            if "margin" in enabled and symbol.capital.margin is not None:
                margin = symbol.capital.margin
                capital["margin"] = {
                    "trade_date": margin.trade_date.isoformat() if margin.trade_date else None,
                    "financing_balance": margin.financing_balance,
                    "financing_balance_change": margin.financing_balance_change,
                    "margin_total_balance": margin.margin_total_balance,
                    "margin_total_change": margin.margin_total_change,
                }
                capital["margin_source"] = symbol.capital.margin_source
                capital["margin_error"] = symbol.capital.margin_error
            if capital:
                data["capital"] = capital

    # Single-object blocks share the same symbol attribute name as their key.
    for key in ("risk", "valuation", "events", "concept", "consensus", "chip", "diff"):
        if key in enabled:
            value = getattr(symbol, key)
            data[key] = value.model_dump(mode="json") if value is not None else None

    if "sector" in enabled:
        data["sector_board"] = symbol.sector_board
        data["sector_strength_rank"] = symbol.sector_strength_rank

    return data


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


def _serialize_concept(concept: Any) -> Optional[Dict[str, Any]]:
    """Project one concept strength record into a compact context dict."""
    if concept is None:
        return None
    return {
        "board_name": concept.board_name,
        "change_pct": concept.change_pct,
        "fund_flow_net": concept.fund_flow_net,
        "limit_up_count": concept.limit_up_count,
        "market_rank": concept.market_rank,
        "members": concept.members,
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


def _resolve_indicator_set(
    snapshot: TrackerSnapshot,
    analysis_indicators: Optional[List[str]] = None,
) -> "set[str]":
    """Resolve which indicator blocks are enabled for a prompt.

    An explicit ``analysis_indicators`` (the per-run/caller override) wins;
    otherwise the snapshot config's persisted ``analysis_indicators`` selection
    applies. Unknown keys are silently dropped so a stale config cannot break
    prompt building.
    """
    if analysis_indicators is None:
        analysis_indicators = snapshot.config.analysis_indicators
    # Restrict to known keys so a stale config can never break prompt building.
    return frozenset(analysis_indicators) & _ALL_INDICATORS


def build_analysis_prompt(
    snapshot: TrackerSnapshot,
    symbols: List[SymbolSnapshot],
    user_prompt: Optional[str] = None,
    history: Optional[Dict[str, Any]] = None,
    analysis_indicators: Optional[List[str]] = None,
) -> str:
    """Build the user prompt from a snapshot and the selected symbols.

    ``history`` optionally maps each symbol code to its most recent prior
    recommendations (newest-first, as :func:`select_symbol_history` returns) so
    the model reviews its own previous calls incrementally instead of analysing
    every symbol from scratch.

    ``analysis_indicators`` optionally restricts which indicator blocks are
    serialized into the context; when omitted the selection persisted on
    ``snapshot.config`` is used.
    """
    enabled = _resolve_indicator_set(snapshot, analysis_indicators)

    context: Dict[str, Any] = {
        "trading_date": snapshot.trading_date.isoformat() if snapshot.trading_date else None,
        "periods": snapshot.config.periods,
        "signals": snapshot.config.signals,
        "thresholds": snapshot.config.thresholds.model_dump(mode="json"),
        "rankings": snapshot.rankings,
        "symbols": [_serialize_symbol(s, enabled) for s in symbols],
    }
    if "sectors" in enabled:
        context["sectors"] = [
            _serialize_sector(s) for s in snapshot.sectors if s is not None
        ]
    if "concepts" in enabled:
        context["concepts"] = [
            _serialize_concept(c) for c in snapshot.concepts if c is not None
        ]
    if "market_sentiment" in enabled:
        context["market_sentiment"] = (
            snapshot.market_sentiment.model_dump(mode="json")
            if snapshot.market_sentiment is not None
            else None
        )
    if history:
        context["history"] = history
    extra = f"\n用户补充指令：{user_prompt}" if user_prompt else ""

    text = (
        f"{_build_analysis_directive(enabled)}{extra}\n\n"
        f"追踪快照数据（JSON）：\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
    )
    if history:
        text += _HISTORY_DIRECTIVE + "\n\n"
    text += _OUTPUT_INSTRUCTIONS
    return text


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
    history: Optional[Dict[str, Any]] = None,
    analysis_indicators: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Call the configured LLM and return a normalized report dict.

    This is synchronous by design so it can be offloaded to a worker thread by
    the API route via ``asyncio.to_thread``.
    """
    prompt = build_analysis_prompt(
        snapshot,
        symbols,
        user_prompt=user_prompt,
        history=history,
        analysis_indicators=analysis_indicators,
    )
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
