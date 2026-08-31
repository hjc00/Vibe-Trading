"""LLM analysis for A-share tracker snapshots.

Wraps the existing ``ChatLLM`` into a focused helper that serializes a subset
of a ``TrackerSnapshot`` (selected symbols only) into a prompt, calls the
configured model, and normalizes the reply into a structured JSON report the
API can hand straight to the frontend.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.providers.chat import ChatLLM
from src.stock_tracker.models import SymbolSnapshot, TrackerSnapshot

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a quantitative analyst reviewing A-share technical signals. \
You work strictly from the tracker data provided and give research-oriented \
commentary, not personalized investment advice. Be concise and data-driven. \
Do not invent metrics that are not in the input. Do not give buy/sell price \
targets. Write all narrative fields in Chinese.
"""

_FOCUS_DIRECTIVES = {
    "rank_opportunities": (
        "按短期技术 setup 强度对标的排序比较，说明每个标的相对强弱。"
    ),
    "risk_check": (
        "重点排查下行风险：高波动、量价背离、RSI 超买/超卖、信号转弱或消失。"
    ),
    "custom": "按用户额外指令分析。",
}

_OUTPUT_INSTRUCTIONS = """\
Return ONLY a single valid JSON object (no markdown fences, no commentary \
outside the JSON) with exactly this shape:

{
  "summary": "一段中文综述",
  "symbols": [
    {
      "code": "600519.SH",
      "name": "贵州茅台",
      "recommendation": "watch",
      "confidence": "high",
      "rationale": "基于信号的简要中文判断",
      "key_metrics": {"rsi": 60.0, "volume_ratio": 1.5},
      "risks": ["风险1", "风险2"],
      "time_horizon": "2-4 周"
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
- "recommendation" MUST be one of: watch, hold, avoid, caution, top_pick.
- "confidence" MUST be one of: high, medium, low.
- Include one entry in "symbols" for every input symbol.
"""


def _serialize_symbol(symbol: SymbolSnapshot) -> Dict[str, Any]:
    """Serialize a single symbol into a compact, JSON-safe context dict."""
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
        "diff": symbol.diff.model_dump(mode="json") if symbol.diff else None,
    }


def build_analysis_prompt(
    snapshot: TrackerSnapshot,
    symbols: List[SymbolSnapshot],
    focus: str = "rank_opportunities",
    user_prompt: Optional[str] = None,
) -> str:
    """Build the user prompt from a snapshot and the selected symbols."""
    context = {
        "trading_date": snapshot.trading_date.isoformat() if snapshot.trading_date else None,
        "periods": snapshot.config.periods,
        "signals": snapshot.config.signals,
        "thresholds": snapshot.config.thresholds.model_dump(mode="json"),
        "rankings": snapshot.rankings,
        "symbols": [_serialize_symbol(s) for s in symbols],
    }
    directive = _FOCUS_DIRECTIVES.get(focus, _FOCUS_DIRECTIVES["rank_opportunities"])
    if focus == "custom" and user_prompt:
        directive = f"{directive}\n用户额外指令：{user_prompt}"

    return (
        f"{directive}\n\n"
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


def _normalize_report(parsed: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    """Coerce a parsed LLM reply into the report shape the API exposes."""
    symbols = parsed.get("symbols", [])
    if not isinstance(symbols, list):
        symbols = []
    portfolio = parsed.get("portfolio", {})
    if not isinstance(portfolio, dict):
        portfolio = {}
    caveats = parsed.get("caveats", [])
    if not isinstance(caveats, list):
        caveats = []

    return {
        "summary": str(parsed.get("summary") or "").strip() or raw_text.strip(),
        "symbols": [s for s in symbols if isinstance(s, dict)],
        "portfolio": {
            "theme": str(portfolio.get("theme") or ""),
            "top_pick": portfolio.get("top_pick"),
            "cautions": [str(c) for c in portfolio.get("cautions", [])]
            if isinstance(portfolio.get("cautions", []), list)
            else [],
        },
        "caveats": [str(c) for c in caveats],
    }


def run_analysis(
    snapshot: TrackerSnapshot,
    symbols: List[SymbolSnapshot],
    focus: str = "rank_opportunities",
    user_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Call the configured LLM and return a normalized report dict.

    This is synchronous by design so it can be offloaded to a worker thread by
    the API route via ``asyncio.to_thread``.
    """
    prompt = build_analysis_prompt(snapshot, symbols, focus, user_prompt)
    llm = ChatLLM()
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
    return _normalize_report(parsed, raw_text)


__all__ = [
    "build_analysis_prompt",
    "run_analysis",
]
