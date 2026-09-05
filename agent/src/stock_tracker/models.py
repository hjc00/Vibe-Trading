"""Data models for the A-share multi-period stock tracker."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SignalType = str
DEFAULT_SIGNALS: List[SignalType] = [
    "volume_spike",
    "breakout",
    "ma_alignment",
    "rsi",
    "margin_expansion",
    "macd",
    "divergence",
    "kdj",
    "bollinger_pct_b",
]
DEFAULT_PERIODS: List[int] = [10, 20, 60]
DEFAULT_WATCHLIST: List[str] = [
    "510300.SH",
    "600519.SH",
    "000001.SZ",
    "300750.SZ",
    "600036.SH",
]

# Indicator blocks that may be injected into an LLM analysis prompt. Each key
# maps 1:1 to a serialization block in the analyzer (symbol-level slices plus
# the snapshot-level sectors/concepts/market_sentiment context). Order defines
# both the config canonical order and the prompt directive bullet order.
ANALYSIS_INDICATORS: List[str] = [
    "period_signals",
    "technical_indicators",
    "fund_flow",
    "margin",
    "risk",
    "valuation",
    "events",
    "concept",
    "consensus",
    "chip",
    "sector",
    "diff",
    "market_sentiment",
    "sectors",
    "concepts",
]

# Analysis emphasis presets. ``balanced`` weighs every enabled block equally;
# ``technical`` instructs the model to treat price action + technical signals as
# the primary evidence and other dimensions as secondary context. Stored on
# ``TrackerConfig.analysis_focus`` and overridable per analyze request.
ANALYSIS_FOCUS_BALANCED = "balanced"
ANALYSIS_FOCUS_TECHNICAL = "technical"
ANALYSIS_FOCUS_OPTIONS: List[str] = [
    ANALYSIS_FOCUS_BALANCED,
    ANALYSIS_FOCUS_TECHNICAL,
]

# Cards on the stock-tracker dashboard that can be hidden via the card's hide
# button. ``TrackerConfig.card_visibility`` stores the visible/hidden state; a
# missing key means visible. Order mirrors the dashboard's top-to-bottom
# reading order and doubles as the canonical order for validation.
HIDEABLE_CARDS: List[str] = [
    "market_sentiment",
    "volume",
    "volume_chart",
    "margin",
    "fund_flow",
    "rps",
    "risk",
    "valuation",
    "events",
    "concept",
    "consensus",
    "chip",
    "financial_report",
    "backtest",
    "sector",
]

# Which refresh-time data dimensions each card consumes. The engine unions the
# dimensions of the currently visible cards and only fetches those; a card whose
# data comes from the core OHLCV fetch or an on-demand route consumes nothing
# here (hiding it only hides the UI). ``industry_ranking`` feeds only the sector
# board; ``concept`` feeds the per-symbol concept card AND the sector board's
# concept tab, so either card alone keeps that dimension alive.
CARD_DATA_DIMENSIONS: Dict[str, frozenset[str]] = {
    "market_sentiment": frozenset({"market_sentiment"}),
    "volume": frozenset(),
    "volume_chart": frozenset(),
    "margin": frozenset({"margin"}),
    "fund_flow": frozenset({"fund_flow"}),
    "rps": frozenset(),
    "risk": frozenset(),
    "valuation": frozenset({"valuation"}),
    "events": frozenset({"events"}),
    "concept": frozenset({"concept"}),
    "consensus": frozenset({"consensus"}),
    "chip": frozenset({"chip"}),
    "financial_report": frozenset(),
    "backtest": frozenset(),
    "sector": frozenset({"industry_ranking", "concept"}),
}


class TrackerThresholds(BaseModel):
    """User-overridable thresholds for signal detection.

    Known thresholds are declared as typed fields so they appear in docs and
    get range validation. Additional per-signal parameters are accepted via
    ``ConfigDict(extra="allow")`` and flattened into the serialized output so
    consumers see a single flat threshold map.
    """

    model_config = ConfigDict(extra="allow")

    volume_spike: float = Field(default=2.0, ge=1.0, description="Volume vs avg ratio to trigger a spike.")
    rsi_overbought: float = Field(default=70.0, ge=50.0, le=100.0)
    rsi_oversold: float = Field(default=30.0, ge=0.0, le=50.0)
    breakout_window: int = Field(default=20, ge=5, le=250)
    # Risk-metric windows and stop-loss configuration (2.3).
    atr_period: int = Field(default=14, ge=2, le=60)
    max_drawdown_window: int = Field(default=60, ge=20, le=250)
    beta_window: int = Field(default=60, ge=20, le=250)
    stop_loss_atr_multiple: float = Field(default=2.0, ge=0.5, le=5.0)

    def get(self, name: str, default: Any = None) -> Any:
        """Return a threshold by name, including dynamically allowed extras."""
        return getattr(self, name, default)

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        """Flatten known fields and extra fields into a single dict."""
        data = super().model_dump(**kwargs)
        known = set(type(self).model_fields.keys())
        for key, value in self.__dict__.items():
            if key not in known and not key.startswith("_"):
                data[key] = value
        return data


class TrackerConfig(BaseModel):
    """Runtime configuration for one tracker instance."""

    watchlist: List[str] = Field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    periods: List[int] = Field(default_factory=lambda: list(DEFAULT_PERIODS))
    signals: List[SignalType] = Field(default_factory=lambda: list(DEFAULT_SIGNALS))
    thresholds: TrackerThresholds = Field(default_factory=TrackerThresholds)
    refresh_interval_seconds: int = Field(
        default=10,
        ge=5,
        description="Auto quote refresh interval in seconds.",
    )
    # Number of detail cards to render in the middle row. At most three fit per
    # row; the rest wrap onto the next row. Defaults to the full card set so no
    # card is hidden unless the user lowers the count.
    detail_card_count: int = Field(
        default=11,
        ge=1,
        description="Number of detail cards to show (max three per row; extras wrap).",
    )
    # Indicator blocks injected into LLM analysis prompts. Defaults to every
    # known block so existing reports are unchanged; persisted with the rest of
    # the config so the selection survives restarts.
    analysis_indicators: List[str] = Field(
        default_factory=lambda: list(ANALYSIS_INDICATORS),
        description="Analysis indicator blocks fed to the LLM (subset of ANALYSIS_INDICATORS).",
    )
    # Analysis emphasis preset. ``balanced`` (default) weighs all enabled blocks
    # equally; ``technical`` biases the prompt toward price action/technical
    # signals. Stored with the config and overridable per analyze request.
    analysis_focus: str = Field(
        default=ANALYSIS_FOCUS_BALANCED,
        description="Analysis emphasis preset (balanced|technical).",
    )
    # Per-card visibility for the dashboard cards listed in HIDEABLE_CARDS. A
    # missing key means the card is visible; ``{id: false}`` hides it from the
    # UI and stops a refresh from fetching its data dimensions.
    card_visibility: Dict[str, bool] = Field(
        default_factory=dict,
        description="Per-card visibility map (key: HIDEABLE_CARDS id, value: visible).",
    )
    # Per-symbol user break-even (保本) price, keyed by normalized code. A code
    # without an entry (or whose entry was cleared) has no break-even set. Fed to
    # LLM analysis as the user's cost basis so the model can weigh gain/loss.
    break_even_prices: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-symbol user break-even price, keyed by normalized code.",
    )

    @field_validator("watchlist")
    @classmethod
    def _validate_watchlist(cls, value: List[str]) -> List[str]:
        cleaned = []
        for code in value:
            stripped = code.strip().upper()
            if not stripped:
                continue
            normalized = normalize_a_share_code(stripped)
            if normalized is None:
                raise ValueError(f"Invalid A-share code: {code!r}")
            cleaned.append(normalized)
        if not cleaned:
            raise ValueError("watchlist must contain at least one A-share code")
        return cleaned

    @field_validator("periods")
    @classmethod
    def _validate_periods(cls, value: List[int]) -> List[int]:
        unique = sorted({int(p) for p in value})
        if not unique:
            raise ValueError("periods must contain at least one positive integer")
        if any(p < 1 or p > 250 for p in unique):
            raise ValueError("periods must be between 1 and 250 trading days")
        return unique

    @field_validator("signals")
    @classmethod
    def _validate_signals(cls, value: List[str]) -> List[SignalType]:
        """Validate signal names against the detector registry at runtime."""
        from src.stock_tracker.signals import list_detector_names

        known = set(list_detector_names())
        unique: List[SignalType] = []
        seen: set[str] = set()
        for signal in value:
            if signal not in known:
                raise ValueError(f"Unknown signal: {signal}")
            if signal not in seen:
                seen.add(signal)
                unique.append(signal)
        if not unique:
            raise ValueError("signals must contain at least one signal type")
        return unique

    @field_validator("analysis_indicators")
    @classmethod
    def _validate_analysis_indicators(cls, value: List[str]) -> List[str]:
        """Reject unknown analysis indicator keys and keep at least one."""
        known = set(ANALYSIS_INDICATORS)
        unique: List[str] = []
        seen: set[str] = set()
        for key in value:
            if key not in known:
                raise ValueError(f"Unknown analysis indicator: {key}")
            if key not in seen:
                seen.add(key)
                unique.append(key)
        if not unique:
            raise ValueError("analysis_indicators must contain at least one indicator")
        return unique

    @field_validator("analysis_focus")
    @classmethod
    def _validate_analysis_focus(cls, value: Any) -> str:
        """Restrict analysis emphasis to the known presets."""
        focus = value or ANALYSIS_FOCUS_BALANCED
        if focus not in ANALYSIS_FOCUS_OPTIONS:
            raise ValueError(
                f"Unknown analysis_focus {focus!r}; expected one of {ANALYSIS_FOCUS_OPTIONS}"
            )
        return focus

    @field_validator("card_visibility")
    @classmethod
    def _validate_card_visibility(cls, value: Dict[str, bool]) -> Dict[str, bool]:
        """Reject unknown card ids and coerce values to strict booleans."""
        known = set(HIDEABLE_CARDS)
        for key in value:
            if key not in known:
                raise ValueError(f"Unknown hideable card: {key}")
        return {key: bool(visible) for key, visible in value.items()}

    @field_validator("break_even_prices", mode="before")
    @classmethod
    def _validate_break_even_prices(
        cls, value: Any
    ) -> Dict[str, float]:
        """Normalize codes and keep only finite, positive break-even prices.

        Runs before type coercion so raw API input (``None`` clears, strings)
        is cleaned first; a ``Dict[str, float]`` field would otherwise reject
        those before this validator could drop them. Entries with an unknown
        code or a non-finite / non-positive price are dropped rather than
        rejected so user edits can never wedge the config.
        """
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("break_even_prices must be a dict")
        cleaned: Dict[str, float] = {}
        for raw_code, raw_price in value.items():
            code = normalize_a_share_code(raw_code)
            if code is None:
                continue
            if raw_price is None:
                continue
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(price) or price <= 0:
                continue
            cleaned[code] = price
        return cleaned

    def model_dump_json_safe(self) -> Dict[str, Any]:
        """Return a plain JSON-serializable dict."""
        return self.model_dump(mode="json")


class SignalState(str, Enum):
    """Semantic state of a single signal."""

    NONE = "none"
    TRIGGERED = "triggered"
    STRONG = "strong"


class SignalValue(BaseModel):
    """One detected signal for one symbol/period.

    ``direction`` is an explicit bullish/bearish/neutral override so the
    frontend badge can color ``direction="both"`` signals correctly without
    relying on keyword heuristics over ``description``. Detectors that know
    their sign at detect time should set it.
    """

    triggered: bool = False
    state: SignalState = SignalState.NONE
    value: Optional[float] = None
    threshold: Optional[float] = None
    description: str = ""
    direction: Optional[Literal["bullish", "bearish", "neutral"]] = None


class PeriodMetrics(BaseModel):
    """Numeric metrics for a single symbol over one period."""

    period: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    sessions: int = 0
    return_pct: Optional[float] = None
    annualized_volatility: Optional[float] = None
    # Volume energy over the window. ``volume_ratio`` compares the window's
    # mean daily volume against the equal-length preceding window; the
    # expansion fields count sessions whose volume was >= 1.5x the trailing
    # 5-session average (a conventional 放量 burst).
    volume_ratio: Optional[float] = None
    avg_volume: Optional[float] = None
    volume_expansion_days: Optional[int] = None
    volume_expansion_ratio: Optional[float] = None
    rsi: Optional[float] = None
    price_vs_ma20: Optional[float] = None
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    # Relative strength percentiles (0-100) within the watchlist universe.
    rps_market: Optional[float] = None
    rps_sector: Optional[float] = None
    benchmark_return_pct: Optional[float] = None


class PeriodSignals(BaseModel):
    """All signals for one symbol over one period."""

    metrics: PeriodMetrics
    signals: Dict[SignalType, SignalValue] = Field(default_factory=dict)


class MarginHistoryItem(BaseModel):
    """One daily margin-trading balance observation for charting."""

    trade_date: Optional[date] = None
    financing_balance: Optional[float] = None
    margin_total_balance: Optional[float] = None


class MarginSnapshot(BaseModel):
    """Daily margin-trading balance snapshot for one symbol."""

    trade_date: Optional[date] = None
    financing_balance: Optional[float] = None
    financing_balance_change: Optional[float] = None
    margin_total_balance: Optional[float] = None
    margin_total_change: Optional[float] = None
    history: List[MarginHistoryItem] = Field(default_factory=list)


class FundFlowHistoryItem(BaseModel):
    """One daily fund-flow observation by order bucket."""

    trade_date: Optional[date] = None
    main_net: Optional[float] = None
    super_large_net: Optional[float] = None
    large_net: Optional[float] = None
    medium_net: Optional[float] = None
    small_net: Optional[float] = None


class FundFlowSnapshot(BaseModel):
    """Daily main-force/net-inflow snapshot for one symbol."""

    trade_date: Optional[date] = None
    main_net: Optional[float] = None
    main_net_ratio: Optional[float] = None  # main_net / turnover, display only
    main_5d_net: Optional[float] = None  # sum of main_net over the latest 5 days
    super_large_net: Optional[float] = None
    large_net: Optional[float] = None
    medium_net: Optional[float] = None
    small_net: Optional[float] = None
    history: List[FundFlowHistoryItem] = Field(default_factory=list)


class CapitalMetrics(BaseModel):
    """Capital metrics for one symbol (fund-flow + margin-trading)."""

    fund_flow: FundFlowSnapshot = Field(default_factory=FundFlowSnapshot)
    margin: MarginSnapshot = Field(default_factory=MarginSnapshot)
    fund_flow_source: str = "unavailable"
    margin_source: str = "unavailable"
    fund_flow_error: Optional[str] = None
    margin_error: Optional[str] = None


class RiskMetrics(BaseModel):
    """Symbol-level risk measures for stop-loss and position sizing.

    Unlike ``PeriodMetrics`` these are computed once per symbol, not per period.
    Beta is measured against the CSI 300 index (with the CSI 300 ETF fallback),
    reusing the same benchmark frame that powers RPS.
    """

    atr_14: Optional[float] = None  # 14-day ATR in price units
    atr_pct: Optional[float] = None  # atr_14 / close, stop-loss distance as a fraction
    max_drawdown_60d: Optional[float] = None  # max drawdown over 60 sessions, negative fraction
    beta_vs_index: Optional[float] = None  # OLS slope of stock returns on benchmark returns
    beta_window: Optional[int] = None  # trading days actually used for the beta regression
    benchmark_code: Optional[str] = None  # benchmark code used for beta (index or ETF)
    stop_loss_price: Optional[float] = None  # suggested stop price = close - k * ATR
    stop_loss_atr_multiple: Optional[float] = None  # the k actually used


class ValuationSnapshot(BaseModel):
    """Valuation multiples, historical percentiles, and quality fundamentals.

    Populated by :mod:`src.stock_tracker.valuation_data` from the Eastmoney
    datacenter ``RPT_VALUEANALYSIS_DET`` (daily valuation series) and
    ``RPT_F10_FINANCE_MAINFINADATA`` (per-period fundamentals). Fields degrade
    to ``None`` when a source is unavailable; ``error`` carries the reason.
    """

    trade_date: Optional[date] = None
    # Valuation multiples (from the daily valuation series).
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    ps_ttm: Optional[float] = None
    pcf_ocf_ttm: Optional[float] = None
    peg: Optional[float] = None
    dividend_yield: Optional[float] = None
    total_market_cap: Optional[float] = None
    # Percentile of the current multiple within its own history (0-100; a
    # lower percentile means cheaper relative to the stock's own past). Computed
    # over a trailing 3y (primary) and 1y (secondary) window at fetch time from
    # the raw daily valuation series, which is not persisted.
    pe_percentile_3y: Optional[float] = None
    pe_percentile_1y: Optional[float] = None
    pb_percentile_3y: Optional[float] = None
    pb_percentile_1y: Optional[float] = None
    # Fundamental quality inputs (from the F10 indicator report).
    roe: Optional[float] = None
    roe_mean_5y: Optional[float] = None
    roe_std_5y: Optional[float] = None
    gross_margin: Optional[float] = None
    gross_margin_std_5y: Optional[float] = None
    net_margin: Optional[float] = None
    net_profit_yoy: Optional[float] = None
    revenue_yoy: Optional[float] = None
    operating_cashflow_to_net_profit: Optional[float] = None
    debt_to_assets: Optional[float] = None
    # Composite fundamental quality score (0-100; None when no inputs).
    fundamental_quality_score: Optional[float] = None
    # Provenance and per-symbol error isolation.
    source: str = "unavailable"
    error: Optional[str] = None


class CrossDayDiff(BaseModel):
    """Change between today and the previous trading day for one symbol."""

    signal_count: Optional[Dict[str, int]] = None
    return_pct: Optional[float] = None
    rank_return_10: Optional[int] = None
    rank_change_10: Optional[int] = None
    new_signals: List[str] = Field(default_factory=list)
    cleared_signals: List[str] = Field(default_factory=list)


class SectorPeriodMetric(BaseModel):
    """Per-period watchlist aggregate for one industry board."""

    period: int
    avg_return_pct: Optional[float] = None
    avg_rps_market: Optional[float] = None
    avg_rps_sector: Optional[float] = None


class SectorStrength(BaseModel):
    """Strength snapshot for one Eastmoney industry board (行业板块).

    Combines the whole-market board ranking (percent change, main-force net
    inflow, up/down constituent counts, leading stock) with watchlist-internal
    aggregates (member returns per period, prosperity score). The aggregates
    and ``prosperity_score`` stay ``None`` when the board has no watchlist
    members. Populated by :mod:`src.stock_tracker.sector_data`.
    """

    # Whole-market board ranking (Eastmoney clist, sorted by change_pct desc).
    board_code: Optional[str] = None
    board_name: str
    change_pct: Optional[float] = None
    fund_flow_net: Optional[float] = None  # main-force net inflow, CNY
    up_count: Optional[float] = None
    down_count: Optional[float] = None
    leader: Optional[str] = None
    market_rank: Optional[int] = None  # 1-based rank by change_pct
    # Watchlist-internal aggregates. ``period_metrics`` holds the per-period
    # return / RPS trend across all configured periods (ascending by period).
    member_count: int = 0
    members: List[str] = Field(default_factory=list)
    period_metrics: List[SectorPeriodMetric] = Field(default_factory=list)
    total_main_net: Optional[float] = None
    # Prosperity (fundamental-based scoring).
    prosperity_score: Optional[float] = None
    avg_roe: Optional[float] = None
    avg_gross_margin: Optional[float] = None
    avg_revenue_yoy: Optional[float] = None
    # Provenance: "eastmoney" (from ranking), "watchlist" (aggregate-only),
    # or "unavailable".
    source: str = "unavailable"
    error: Optional[str] = None


class EventItem(BaseModel):
    """One upcoming/recent corporate event for a symbol.

    ``risk_level`` mirrors the conventional tone vocabulary used across the
    tracker UI (``danger`` / ``warning`` / ``info``); ``risk_score`` is the
    event's own 0-100 sub-score and drives that level. ``days_until`` counts
    natural days to ``event_date`` (negative for historical events).
    """

    event_type: str = ""  # lockup | earnings_forecast | dragon_tiger | holder_trade
    event_date: Optional[date] = None
    title: str = ""  # 中文短标题，如「解禁 0.5 亿股」「中报业绩预减」
    summary: str = ""  # 一句话说明
    risk_level: str = "info"  # info | warning | danger（前端直接映射色调）
    risk_score: Optional[float] = None  # 该事件自身的 0–100 风险分
    days_until: Optional[int] = None  # 距事件日的自然日数（历史事件可为负）
    source: str = "unavailable"  # eastmoney | tushare
    details: Dict[str, Any] = Field(default_factory=dict)  # 事件特有字段


class EventSnapshot(BaseModel):
    """Event calendar + composite risk for one symbol.

    Populated by :mod:`src.stock_tracker.events_data` from the lockup /
    dragon-tiger Eastmoney reports plus the Tushare ``forecast`` /
    ``stk_holdertrade`` fallbacks. Items are sorted by ``event_date`` ascending;
    ``event_risk_score`` is the composite 0-100 risk and ``high_risk_count`` the
    number of ``danger`` items. Degrades with ``error`` when a source fails so a
    bad symbol never blocks the refresh.
    """

    as_of: Optional[date] = None
    items: List[EventItem] = Field(default_factory=list)
    event_risk_score: Optional[float] = None  # 0–100 综合事件风险
    high_risk_count: int = 0  # risk_level == "danger" 的事件数
    source: str = "unavailable"
    error: Optional[str] = None


class ConceptSnapshot(BaseModel):
    """Concept / thematic-board heat for one symbol (题材/概念热度).

    Populated by :mod:`src.stock_tracker.concept_data` from the Eastmoney
    concept-board taxonomy (``clist`` ``fs=m:90+t:3`` ranking plus ``slist``
    ``spt=3`` membership). ``boards`` lists every concept board the stock
    belongs to; ``hottest_concept`` / ``hottest_concept_rank`` point at its
    most-heated board on the whole-market ranking; ``concept_heat_score`` is the
    0-100 composite. Degrades with ``error`` so a blocked source never breaks
    the refresh.
    """

    boards: List[str] = Field(default_factory=list)
    hottest_concept: Optional[str] = None
    hottest_concept_rank: Optional[int] = None  # 1-based rank on the concept board ranking
    concept_heat_score: Optional[float] = None  # 0-100
    limit_up_count: Optional[int] = None  # 最热概念内涨停家数（复用 2.16 市场涨停池）
    source: str = "unavailable"
    error: Optional[str] = None


class ConceptStrength(BaseModel):
    """Strength snapshot for one Eastmoney concept board (概念板块).

    Mirrors :class:`SectorStrength` but trades the fundamental ``prosperity_score``
    for ``limit_up_count`` (the number of limit-up stocks inside the concept,
    aggregated from the market limit-up pool). Populated by
    :mod:`src.stock_tracker.concept_data`.
    """

    board_code: Optional[str] = None
    board_name: str
    change_pct: Optional[float] = None
    fund_flow_net: Optional[float] = None  # main-force net inflow, CNY
    up_count: Optional[float] = None
    down_count: Optional[float] = None
    leader: Optional[str] = None
    limit_up_count: Optional[int] = None
    market_rank: Optional[int] = None  # 1-based rank by change_pct
    member_count: int = 0
    members: List[str] = Field(default_factory=list)
    source: str = "unavailable"
    error: Optional[str] = None


class MarketSentimentSnapshot(BaseModel):
    """Whole-market breadth and limit-up temperature (市场情绪温度计).

    Populated by :mod:`src.stock_tracker.sentiment_data` from the Eastmoney
    limit-up pool (``push2ex getTopicZTPool``) with a Tushare ``limit_list_d`` /
    ``limit_step`` fallback. ``sentiment_score`` is the composite 0-100
    temperature (higher = hotter); ``board_ladder`` maps each consecutive-board
    height (连板次数) to its count. Degrades with ``error``.
    """

    limit_up_count: Optional[int] = None
    limit_down_count: Optional[int] = None
    broken_board_count: Optional[int] = None  # 炸板家数
    broken_ratio: Optional[float] = None  # 炸板率 0-1
    max_board_height: Optional[int] = None  # 最高连板
    board_ladder: Dict[str, int] = Field(default_factory=dict)  # 连板高度 -> 家数
    up_count: Optional[int] = None  # 全市场上涨家数
    down_count: Optional[int] = None  # 全市场下跌家数
    prev_limit_up_perf: Optional[float] = None  # 昨日涨停股今日平均表现（溢价）
    sentiment_score: Optional[float] = None  # 0-100
    source: str = "unavailable"
    error: Optional[str] = None


class ConsensusSnapshot(BaseModel):
    """Sell-side consensus estimates for one symbol (盈利预期/一致预期).

    Populated by :mod:`src.stock_tracker.consensus_data` from the Eastmoney
    research-report feed (``reportapi``) and the Eastmoney datacenter
    per-year consensus EPS, with a Tushare ``report_rc`` fallback for target
    prices and EPS revision. ``forward_pe`` is derived from the next-year
    consensus EPS and the latest close. Degrades with ``error``; the fallback
    feeds may be ``None`` when no token / insufficient Tushare points.
    """

    analyst_count: Optional[int] = None
    consensus_eps_cur: Optional[float] = None
    consensus_eps_next: Optional[float] = None
    forward_pe: Optional[float] = None
    target_price_avg: Optional[float] = None
    target_price_low: Optional[float] = None
    target_price_high: Optional[float] = None
    upside_pct: Optional[float] = None  # target_price_avg / close - 1, fraction
    rating_distribution: Dict[str, int] = Field(default_factory=dict)  # 评级 -> 家数
    rating_score: Optional[float] = None  # 0-100 评级综合分
    eps_revision_pct: Optional[float] = None  # EPS 上/下修幅度（分率）
    source: str = "unavailable"
    error: Optional[str] = None


class ChipHolderItem(BaseModel):
    """One quarterly shareholder-count observation for chip charting."""

    end_date: Optional[date] = None
    holder_count: Optional[float] = None
    holder_count_change_pct: Optional[float] = None
    avg_hold_amount: Optional[float] = None


class ChipSnapshot(BaseModel):
    """Chip concentration / institutional movement for one symbol (筹码集中度).

    Populated by :mod:`src.stock_tracker.chip_data` from the Eastmoney
    shareholder-count report (``RPT_HOLDERNUMLATEST``) plus Tushare ``hk_hold``
    (northbound) and ``fund_portfolio`` (mutual-fund) fallbacks.
    ``holder_trend`` is ``"accumulating"`` when the holder count falls for two
    consecutive periods (吸筹), ``"distributing"`` when it rises (派发);
    ``chip_concentration_score`` is the 0-100 composite. Degrades with ``error``.
    """

    holder_count: Optional[float] = None
    holder_count_change_pct: Optional[float] = None  # 环比%，负=户数下降
    holder_trend: Optional[str] = None  # accumulating | distributing
    avg_hold_amount: Optional[float] = None  # 户均持股市值
    northbound_holding_ratio: Optional[float] = None  # 北向持股占比%
    fund_holding_ratio: Optional[float] = None  # 公募持股占比%
    chip_concentration_score: Optional[float] = None  # 0-100
    holder_history: List[ChipHolderItem] = Field(default_factory=list)
    source: str = "unavailable"
    error: Optional[str] = None


class FinancialPeriod(BaseModel):
    """One report period's key indicators (报告期, 年报/中报/季报).

    ``report_type`` is derived from the report end date (年报 / 中报 / 一季报 /
    三季报). The YoY fields (``revenue_yoy`` / ``net_profit_yoy``) are already
    cumulative-period comparisons; level fields such as ``roe`` / ``eps`` are the
    reported cumulative figures for that period (interim vs annual are not
    directly comparable). Populated by
    :func:`src.tools.financial_statements_tool.fetch_financial_indicators`.
    """

    end_date: str  # REPORT_DATE[:10]，如 "2026-06-30"
    report_type: str  # 年报 | 中报 | 一季报 | 三季报 | 报告
    roe: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    net_profit_yoy: Optional[float] = None
    revenue_yoy: Optional[float] = None
    debt_to_assets: Optional[float] = None
    eps: Optional[float] = None
    operating_cashflow_to_net_profit: Optional[float] = None


class FinancialReportSnapshot(BaseModel):
    """Financial-report reading for one symbol (财报速读, on-demand).

    Produced on demand (not part of the daily refresh) by
    :mod:`src.stock_tracker.financial_reports_data` and the
    ``financial-report`` route. ``periods`` are newest-first; ``red_flags`` and
    ``beat_miss`` are derived from the latest period. Standalone (not attached to
    ``SymbolSnapshot``) because it is a per-click fetch, not a refresh dimension.
    """

    code: str
    periods: List[FinancialPeriod] = Field(default_factory=list)  # 倒序，最新在前
    red_flags: List[str] = Field(default_factory=list)  # 基于 periods[0]
    beat_miss: Optional[str] = None  # beat | miss | inline（缺一致预期则为 None）
    consensus_eps: Optional[float] = None
    source: str = "unavailable"
    error: Optional[str] = None


class BacktestPoint(BaseModel):
    """One equity-curve point (strategy or benchmark) for the backtest chart."""

    date: str  # "YYYY-MM-DD"
    equity: float


class BacktestPricePoint(BaseModel):
    """One daily close of the backtested symbol (for the buy/sell overlay)."""

    date: str  # "YYYY-MM-DD"
    close: float


class BacktestTradePoint(BaseModel):
    """One executed buy/sell fill of a backtest run.

    ``side`` is the fill side reported by the engine (``"buy"`` opens a long,
    ``"sell"`` closes it); A 股 ChinaAEngine is long-only so these map directly
    to 买入点 / 卖出点 on the price overlay.
    """

    date: str  # "YYYY-MM-DD"
    side: str  # buy | sell
    price: float


class BacktestIndicatorSeries(BaseModel):
    """One indicator series aligned to the backtest window's price dates.

    ``values`` is a list the same length as ``BacktestSnapshot.prices``; ``None``
    marks warm-up bars where the indicator is not yet defined.
    """

    key: str
    label: str
    values: List[Optional[float]] = Field(default_factory=list)


class BacktestIndicatorPanel(BaseModel):
    """One indicator group drawn on (or below) the backtest price chart.

    ``kind`` is ``"ma"`` / ``"boll"`` (overlay on the price grid) or
    ``"macd"`` / ``"kdj"`` / ``"rsi"`` (each gets its own sub-panel below).
    ``params`` records the strategy parameters that produced it.
    """

    kind: str
    title: str
    params: Dict[str, Any] = Field(default_factory=dict)
    series: List[BacktestIndicatorSeries] = Field(default_factory=list)


class BacktestSnapshot(BaseModel):
    """Single-symbol strategy backtest result (单标的回测, on-demand).

    Produced on demand (not part of the daily refresh) by
    :mod:`src.stock_tracker.backtest_data`, which runs the real ``agent/backtest``
    engine over an on-demand long daily bar range. Standalone (not attached to
    ``SymbolSnapshot``) because it is a per-click computation, not a refresh
    dimension.

    Return/drawdown/win-rate fields are decimals (fractions), matching the
    engine's own metric semantics; ``total_return=0.23`` means +23%.
    """

    code: str
    label: str = ""
    spec: Dict[str, Any] = Field(default_factory=dict)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    bars: int = 0  # evaluated trading bars
    # Fractions: total/annual return (0.23 = +23%), max_drawdown (negative
    # fraction), win_rate (0-1). ``annual_return`` may be None when the window
    # is too short to annualize.
    total_return: Optional[float] = None
    annual_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    win_rate: Optional[float] = None
    profit_factor: Optional[float] = None
    trade_count: int = 0
    equity_curve: List[BacktestPoint] = Field(default_factory=list)
    benchmark_curve: List[BacktestPoint] = Field(default_factory=list)
    prices: List[BacktestPricePoint] = Field(default_factory=list)
    trades: List[BacktestTradePoint] = Field(default_factory=list)
    indicators: List[BacktestIndicatorPanel] = Field(default_factory=list)
    source: str = "backtest"
    error: Optional[str] = None


class VolumePoint(BaseModel):
    """One daily OHLCV observation for charting within the tracker.

    ``open/high/low/close`` are the daily candles (available when the upstream
    source returns full OHLC); ``is_burst`` marks volume >= 1.5x the trailing
    5-session average.
    """

    trade_date: Optional[date] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    is_burst: bool = False


class IndicatorBar(BaseModel):
    """One trailing daily bar plus its precomputed technical indicators.

    Populated by :mod:`src.stock_tracker.engine` from the same pure functions
    the signal detectors use, so plotted values match badge values. Warm-up
    bars carry ``None`` for indicators that need more history.
    """

    trade_date: Optional[date] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    dif: Optional[float] = None
    dea: Optional[float] = None
    macd_hist: Optional[float] = None
    k: Optional[float] = None
    d: Optional[float] = None
    j: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_mid: Optional[float] = None
    bb_lower: Optional[float] = None
    pct_b: Optional[float] = None
    bandwidth: Optional[float] = None


class DivergenceMark(BaseModel):
    """Coordinates of one top/bottom divergence for chart annotation.

    Indices are ``iloc`` positions within the ``bars`` list of the enclosing
    :class:`IndicatorSeries`.
    """

    kind: str = "top"  # top | bottom
    price_hi_idx: Optional[int] = None
    price_lo_idx: Optional[int] = None
    dif_hi_idx: Optional[int] = None
    dif_lo_idx: Optional[int] = None


class IndicatorSeries(BaseModel):
    """Trailing technical-indicator series for one symbol's chart.

    ``bars`` holds the last ``_PRICE_BARS`` daily bars in chronological order;
    ``divergence_marks`` holds any MACD-DIF divergence annotations detected over
    that same window.
    """

    bars: List[IndicatorBar] = Field(default_factory=list)
    divergence_marks: List[DivergenceMark] = Field(default_factory=list)


class SymbolSnapshot(BaseModel):
    """One symbol's slice of a tracker snapshot."""

    code: str
    name: Optional[str] = None
    market: str = "a_share"
    close: Optional[float] = None
    prev_close: Optional[float] = None
    daily_return: Optional[float] = None
    volume: Optional[float] = None
    avg_volume_20: Optional[float] = None
    volume_series: List[VolumePoint] = Field(default_factory=list)
    currency: str = "CNY"
    period_signals: Dict[str, PeriodSignals] = Field(default_factory=dict)
    capital: Optional[CapitalMetrics] = None
    risk: Optional[RiskMetrics] = None
    valuation: Optional[ValuationSnapshot] = None
    events: Optional[EventSnapshot] = None
    concept: Optional[ConceptSnapshot] = None
    consensus: Optional[ConsensusSnapshot] = None
    chip: Optional[ChipSnapshot] = None
    diff: Optional[CrossDayDiff] = None
    indicators: Optional[IndicatorSeries] = None
    sector_board: Optional[str] = None
    sector_board_source: Optional[str] = None
    sector_strength_rank: Optional[int] = None  # board's market rank (1-based)
    error: Optional[str] = None


class TrackerSnapshot(BaseModel):
    """A complete daily snapshot produced by the tracker engine."""

    generated_at: datetime
    trading_date: Optional[date] = None
    # Refresh base date (the engine's ``end_date``); ``trading_date`` may lag it
    # when data vendors have not finalized the latest session. Same-day refresh
    # reuse and last-good retention key off this field, not ``trading_date``.
    as_of_date: Optional[date] = None
    config: TrackerConfig
    symbols: List[SymbolSnapshot] = Field(default_factory=list)
    rankings: Dict[str, List[str]] = Field(default_factory=dict)
    sectors: List[SectorStrength] = Field(default_factory=list)
    concepts: List[ConceptStrength] = Field(default_factory=list)
    market_sentiment: Optional[MarketSentimentSnapshot] = None
    unresolved: List[str] = Field(default_factory=list)
    data_gaps: List[Dict[str, Any]] = Field(default_factory=list)

    def model_dump_json_safe(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict with dates as ISO strings."""
        return self.model_dump(mode="json")


class AnalysisAction(str, Enum):
    """Structured analyst action for one symbol recommendation."""

    BUY = "buy"
    HOLD = "hold"
    REDUCE = "reduce"
    AVOID = "avoid"


class PriceZone(BaseModel):
    """A low/high price band used for entry or target recommendations."""

    low: Optional[float] = None
    high: Optional[float] = None


class EvidenceItem(BaseModel):
    """One concrete indicator reading cited as evidence for a recommendation.

    ``indicator`` names the metric (e.g. "MACD DIF/DEA", "KDJ J", "RPS_10"),
    ``value`` is the exact value copied from the input snapshot (never invented),
    and ``read`` is the short interpretation ("金叉偏多/超买/放量 1.6x …").
    """

    indicator: str
    value: Optional[Any] = None
    read: Optional[str] = None


class SymbolRecommendation(BaseModel):
    """Structured per-symbol recommendation produced by the LLM analyzer."""

    code: str
    name: Optional[str] = None
    action: AnalysisAction = AnalysisAction.HOLD
    confidence: Optional[float] = Field(default=None, ge=0, le=100)  # 0-100
    rationale: str = ""
    entry_zone: Optional[PriceZone] = None  # 合理买入区间
    target_zone: Optional[PriceZone] = None  # 目标价区间
    stop_loss: Optional[float] = None  # 止损参考价
    reduce_trigger: Optional[str] = None  # 减仓/止损触发条件（叙述）
    track_metrics: List[str] = Field(default_factory=list)  # 关键跟踪指标名
    time_horizon: Optional[str] = None
    risks: List[str] = Field(default_factory=list)
    # Snapshot value chips (rsi/volume_ratio/...) preserved for the frontend.
    key_metrics: Dict[str, Any] = Field(default_factory=dict)
    # Concrete indicator -> reading -> judgment list the model must fill.
    basis: List[EvidenceItem] = Field(default_factory=list)


class PortfolioInsight(BaseModel):
    """Portfolio-level view inside an analysis report."""

    theme: str = ""
    top_pick: Optional[str] = None
    cautions: List[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    """A normalized LLM analysis report over selected symbols."""

    summary: str = ""
    symbols: List[SymbolRecommendation] = Field(default_factory=list)
    portfolio: PortfolioInsight = Field(default_factory=PortfolioInsight)
    caveats: List[str] = Field(default_factory=list)


class TrackRecordItem(BaseModel):
    """A persisted, verifiable prediction for one symbol recommendation."""

    analysis_id: str
    trading_date: Optional[date] = None
    code: str
    name: Optional[str] = None
    action: str = "hold"
    confidence: Optional[float] = None
    entry_zone: Optional[PriceZone] = None
    target_zone: Optional[PriceZone] = None
    stop_loss: Optional[float] = None
    time_horizon: Optional[str] = None
    current_close: Optional[float] = None
    # pending / active / hit_target / stopped_out
    status: str = "active"


class TrackerSettings(BaseModel):
    """Persisted tracker settings plus optional metadata."""

    config: TrackerConfig = Field(default_factory=TrackerConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


def _infer_a_share_exchange(numeric: str) -> Optional[str]:
    """Infer the exchange suffix from the first two digits of a 6-digit code."""
    prefix = numeric[:2]
    if prefix in ("60", "68", "69"):
        return "SH"
    if prefix in ("00", "30"):
        return "SZ"
    if prefix in (
        "80", "81", "82", "83", "84", "85", "86", "87", "88", "89", "40", "41", "42", "43"
    ):
        return "BJ"
    return None


def _is_a_share_code(code: str) -> bool:
    """Return True for codes like 000001.SZ, 600519.SH, 000001.BJ."""
    import re

    if not re.fullmatch(r"^\d{6}\.(SH|SZ|BJ)$", code):
        return False
    return _infer_a_share_exchange(code[:6]) is not None


def normalize_a_share_code(code: str) -> Optional[str]:
    r"""Normalize an A-share code to ``6-digit.EXCHANGE`` form.

    If the code already has an exchange suffix, the prefix is still trusted
    more than the suffix: ``000938.SH`` is corrected to ``000938.SZ`` because
    ``00`` prefixes are Shenzhen. This lets users paste codes from sources that
    occasionally use the wrong venue suffix.

    If the code is a bare 6-digit number, infer the exchange from the prefix:

      - 60/68/69 -> .SH
      - 00/30    -> .SZ
      - 8/4      -> .BJ

    Returns ``None`` for unrecognized formats.
    """
    import re

    code = code.strip().upper()
    match = re.fullmatch(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", code)
    if not match:
        return None
    numeric = match.group(1)
    inferred = _infer_a_share_exchange(numeric)
    if inferred:
        return f"{numeric}.{inferred}"
    # Unknown prefix but a suffix is present; keep it for forward compatibility.
    suffix = match.group(2)
    if suffix:
        return f"{numeric}.{suffix}"
    return None


__all__ = [
    "DEFAULT_PERIODS",
    "DEFAULT_SIGNALS",
    "DEFAULT_WATCHLIST",
    "ANALYSIS_INDICATORS",
    "HIDEABLE_CARDS",
    "CARD_DATA_DIMENSIONS",
    "SignalType",
    "SignalState",
    "SignalValue",
    "TrackerThresholds",
    "TrackerConfig",
    "PeriodMetrics",
    "PeriodSignals",
    "MarginHistoryItem",
    "MarginSnapshot",
    "FundFlowHistoryItem",
    "FundFlowSnapshot",
    "CapitalMetrics",
    "RiskMetrics",
    "ValuationSnapshot",
    "EventItem",
    "EventSnapshot",
    "SectorPeriodMetric",
    "SectorStrength",
    "ConceptSnapshot",
    "ConceptStrength",
    "MarketSentimentSnapshot",
    "ConsensusSnapshot",
    "ChipHolderItem",
    "ChipSnapshot",
    "CrossDayDiff",
    "DivergenceMark",
    "IndicatorBar",
    "IndicatorSeries",
    "SymbolSnapshot",
    "TrackerSnapshot",
    "BacktestPoint",
    "BacktestPricePoint",
    "BacktestTradePoint",
    "BacktestIndicatorSeries",
    "BacktestIndicatorPanel",
    "BacktestSnapshot",
    "AnalysisAction",
    "PriceZone",
    "EvidenceItem",
    "SymbolRecommendation",
    "PortfolioInsight",
    "AnalysisReport",
    "TrackRecordItem",
    "TrackerSettings",
    "normalize_a_share_code",
]
