"""Financial-report reader (财报速读) — on-demand, multi-period.

拉取单只 A 股最近若干报告期（年报/中报/季报混合口径）的核心指标序列，产出
``FinancialReportSnapshot``：横向多期对比由前端负责，这里只负责把干净期数
转成模型、基于最新期标红旗、并把最新实际 EPS 与一致预期 EPS 对比出 beat/miss。

数据源：东财 F10 主指标表（``RPT_F10_FINANCE_MAINFINADATA``），通过
:func:`src.tools.financial_statements_tool.fetch_financial_indicators` 复用
既有抓取与字段归一化，本模块不重复实现 provider 逻辑。

设计原则：
- 纯函数与编排分离：``build_financial_report`` 可独立单测；``load_financial_report``
  是唯一的网络入口。
- 手动按需：不进日频 refresh，无缓存（每次点击实时拉取，低频）。
- 永不抛异常，缺数据/失败降级为带 ``error`` 的空报告。
"""

from __future__ import annotations

from typing import List, Optional

from src.stock_tracker.models import FinancialPeriod, FinancialReportSnapshot
from src.tools.financial_statements_tool import fetch_financial_indicators

# Beat/miss 判定阈值：实际 EPS 相对一致预期 ±5% 内视为 inline。
_BEAT_TOLERANCE = 0.05


def _beat_miss(actual_eps: Optional[float], consensus_eps: Optional[float]) -> Optional[str]:
    """Compare latest actual EPS against the consensus estimate."""
    if actual_eps is None or consensus_eps is None or consensus_eps <= 0:
        return None
    ratio = actual_eps / consensus_eps
    if ratio > 1 + _BEAT_TOLERANCE:
        return "beat"
    if ratio < 1 - _BEAT_TOLERANCE:
        return "miss"
    return "inline"


def _flag(latest: FinancialPeriod, prior: Optional[FinancialPeriod]) -> List[str]:
    """Derive human-readable red flags from the latest (and prior) period."""
    flags: List[str] = []
    ocf = latest.operating_cashflow_to_net_profit
    if ocf is not None and ocf < 0.5:
        flags.append("经营现金流/净利 < 0.5，利润含金量偏低")
    if latest.net_profit_yoy is not None and latest.net_profit_yoy < 0:
        flags.append("归母净利润同比下滑")
    if latest.revenue_yoy is not None and latest.revenue_yoy < 0:
        flags.append("营业收入同比下滑")
    if latest.debt_to_assets is not None and latest.debt_to_assets > 70:
        flags.append("资产负债率 > 70%，杠杆偏高")
    if latest.gross_margin is not None and prior is not None:
        if prior.gross_margin is not None and latest.gross_margin < prior.gross_margin:
            flags.append("毛利率连续下滑（较上期下降）")
    if latest.net_profit_yoy is not None and latest.revenue_yoy is not None:
        if latest.net_profit_yoy < latest.revenue_yoy - 20:
            flags.append("增收不增利：净利同比明显低于营收同比")
    return flags


def build_financial_report(
    code: str,
    indicators: List[dict],
    *,
    consensus_eps: Optional[float] = None,
) -> FinancialReportSnapshot:
    """Build a :class:`FinancialReportSnapshot` from clean period dicts.

    ``indicators`` are newest-first dicts as returned by
    :func:`src.tools.financial_statements_tool.fetch_financial_indicators`
    (``end_date`` / ``report_type`` / indicator fields). Pure function (no
    network), safe to unit test with synthetic input.

    Returns:
        Snapshot with newest-first ``periods``; an empty data source degrades to
        ``error="no financial report data"`` instead of raising.
    """
    periods = [
        FinancialPeriod(**{k: v for k, v in item.items() if k in FinancialPeriod.model_fields})
        for item in indicators
    ]
    snapshot = FinancialReportSnapshot(code=code, periods=periods)
    if not periods:
        snapshot.error = "no financial report data"
        return snapshot
    latest, prior = periods[0], (periods[1] if len(periods) > 1 else None)
    snapshot.red_flags = _flag(latest, prior)
    snapshot.beat_miss = _beat_miss(latest.eps, consensus_eps)
    snapshot.consensus_eps = consensus_eps
    snapshot.source = "eastmoney"
    return snapshot


def load_financial_report(
    code: str,
    *,
    consensus_eps: Optional[float] = None,
    max_periods: int = 12,
) -> FinancialReportSnapshot:
    """Fetch and build a financial report for one symbol. Never raises.

    Args:
        code: A-share symbol (e.g. ``"600519.SH"``).
        consensus_eps: Optional latest-year consensus EPS for beat/miss.
        max_periods: Most recent report periods to keep.

    Returns:
        Financial report snapshot; unresolvable / fetch failure degrades to
        ``error="no financial report data"``.
    """
    try:
        indicators = fetch_financial_indicators(code, max_periods=max_periods)
    except Exception:  # noqa: BLE001 - never raise out of the loader
        return FinancialReportSnapshot(code=code, error="no financial report data")
    return build_financial_report(code, indicators, consensus_eps=consensus_eps)


__all__ = ["build_financial_report", "load_financial_report"]
