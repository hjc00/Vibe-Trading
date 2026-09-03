"""Core computation engine for the A-share multi-period tracker."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from src.market_data import fetch_market_data
from src.stock_tracker.capital_data import _FUND_FLOW_LOOKBACK, CapitalDataCache, load_capital_data
from src.stock_tracker.chip_data import ChipDataCache, load_chip_data
from src.stock_tracker.concept_data import load_concept_data
from src.stock_tracker.consensus_data import (
    ConsensusDataCache,
    compute_forward_metrics,
    load_consensus_data,
)
from src.stock_tracker.events_data import EventsDataCache, load_events_data
from src.stock_tracker.models import (
    CapitalMetrics,
    ChipSnapshot,
    ConceptSnapshot,
    ConceptStrength,
    ConsensusSnapshot,
    CrossDayDiff,
    EventSnapshot,
    PeriodMetrics,
    PeriodSignals,
    RiskMetrics,
    SectorStrength,
    SignalType,
    SignalValue,
    SymbolSnapshot,
    TrackerConfig,
    TrackerSnapshot,
    ValuationSnapshot,
    VolumePoint,
)
from src.stock_tracker.names import fetch_a_share_names
from src.stock_tracker.risk import compute_atr, compute_beta, compute_max_drawdown
from src.stock_tracker.sector_data import load_sector_strength
from src.stock_tracker.sentiment_data import fetch_market_breadth, load_market_sentiment
from src.stock_tracker.signals import compute_mas, compute_rsi, get_detector, get_detector_meta
from src.stock_tracker.valuation_data import ValuationDataCache, load_valuation_data
from src.tools.sector_tool import resolve_concept_boards, resolve_industry_board

logger = logging.getLogger(__name__)

# Extra calendar days before the earliest requested period so that moving
# averages (especially 60-day) have enough history even with holidays.
_BUFFER_DAYS = 90

# A session counts as a 放量 burst when its volume reaches this multiple of the
# trailing 5-session average volume (an A-share convention, softer than the
# 2.0x ``volume_spike`` signal which fires on the single latest bar).
_VOLUME_EXPANSION_FACTOR = 1.5

# Extra sessions kept ahead of the longest period so the volume bar chart can
# classify the earliest visible bar (burst uses a 5-session trailing baseline).
_VOLUME_SERIES_CONTEXT = 6


def _as_float(value: Any) -> Optional[float]:
    """Coerce a scalar to float, mapping missing/NaN to ``None``."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def _volume_burst_series(df: pd.DataFrame) -> pd.Series:
    """Boolean per-session mask: volume >= 1.5x the trailing 5-session average.

    Shared by ``_compute_period_metrics`` (burst-day counts) and
    ``_build_volume_series`` (bar-chart burst coloring) so both stay consistent.
    """
    if "volume" not in df.columns:
        return pd.Series(index=df.index, dtype=bool)
    baseline = df["volume"].rolling(5).mean().shift(1) * _VOLUME_EXPANSION_FACTOR
    return (df["volume"] >= baseline).fillna(False)

# Historical days to fetch for capital data (fund-flow + margin-trading).
# Must cover the 30-day fund-flow lookback used by the spike detector. The
# margin-trading window is widened per-refresh to cover the longest configured
# period so the margin-expansion signal can be computed over that window.
_CAPITAL_DATA_DAYS = max(10, _FUND_FLOW_LOOKBACK)

# Market benchmark for RPS computation. The index is preferred; the ETF fallback
# is used when the index itself is unavailable.
_RPS_MARKET_BENCHMARK = "000300.SH"
_RPS_MARKET_BENCHMARK_FALLBACK = "510300.SH"


class StockTrackerEngine:
    """Fetch market data and produce a structured multi-period snapshot."""

    def __init__(self, config: TrackerConfig) -> None:
        self.config = config
        self._capital_cache = CapitalDataCache()
        self._valuation_cache = ValuationDataCache()
        self._events_cache = EventsDataCache()
        self._chip_cache = ChipDataCache()
        self._consensus_cache = ConsensusDataCache()

    def refresh(
        self,
        end_date: date | str | None = None,
        previous: TrackerSnapshot | None = None,
    ) -> TrackerSnapshot:
        """Refresh the tracker snapshot.

        Args:
            end_date: Base trading date. Defaults to today.
            previous: Optional prior snapshot for cross-day diff.

        Returns:
            A new ``TrackerSnapshot``.
        """
        if end_date is None:
            end_date = date.today()
        elif isinstance(end_date, str):
            end_date = date.fromisoformat(end_date)

        trading_date = end_date
        # Reuse same-trading-day capital/valuation from the previous snapshot so
        # a refresh does not re-request throttled/blocked Eastmoney sources.
        self._seed_caches_from_previous(previous, trading_date)
        start_date = end_date - timedelta(days=max(self.config.periods) + _BUFFER_DAYS)

        raw_data = self._fetch_data(
            self.config.watchlist,
            start_date.isoformat(),
            end_date.isoformat(),
        )

        # Resolve Chinese names once per refresh; tolerate partial failures.
        try:
            names = fetch_a_share_names(self.config.watchlist)
        except Exception:  # noqa: BLE001
            logger.exception("Name resolution failed")
            names = {}

        # Resolve sector boards, reusing the prior same-trading-day mapping so a
        # repeat refresh skips the throttled Eastmoney membership calls; only
        # symbols missing from it are resolved fresh. Tolerate partial failures.
        sector_boards = self._resolve_sector_boards(previous, trading_date)

        # Fetch market benchmark for RPS computation; tolerate failure.
        benchmark_df: Optional[pd.DataFrame] = None
        try:
            benchmark_df = self._fetch_benchmark_data(
                start_date.isoformat(),
                end_date.isoformat(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Benchmark data fetch failed")

        # Fetch margin-trading data with daily caching. Request enough history
        # to cover the longest configured period so the margin-expansion signal
        # can be computed per period; fund flow keeps its own 30-day lookback.
        capital_data: Dict[str, CapitalMetrics] = {}
        try:
            capital_data = self._fetch_capital_data(
                self.config.watchlist,
                trading_date=trading_date,
                days=max(_CAPITAL_DATA_DAYS, max(self.config.periods) + 1),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Capital data fetch failed")

        # A throttled/blocked source must not clobber an earlier successful
        # fetch: when a symbol's capital block errored this refresh but the
        # prior snapshot still holds good data, keep the good data instead of
        # blanking the card.
        capital_data = self._retain_last_good_capital(capital_data, previous)

        # Fetch valuation + quality data with daily caching; tolerate failure.
        valuation_data: Dict[str, ValuationSnapshot] = {}
        try:
            valuation_data = load_valuation_data(
                self.config.watchlist,
                end_date=trading_date,
                cache=self._valuation_cache,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Valuation data fetch failed")

        # Fetch event-calendar data (解禁/龙虎榜/业绩预告/增减持) with daily
        # caching; tolerate failure so a blocked source never breaks the refresh.
        events_data: Dict[str, EventSnapshot] = {}
        try:
            events_data = load_events_data(
                self.config.watchlist,
                end_date=trading_date,
                cache=self._events_cache,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Event data fetch failed")

        # Fetch chip-concentration data (股东户数/北向/公募) with low-frequency
        # caching; tolerate failure so a blocked source never breaks the refresh.
        chip_data: Dict[str, ChipSnapshot] = {}
        try:
            chip_data = load_chip_data(
                self.config.watchlist,
                end_date=trading_date,
                cache=self._chip_cache,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Chip data fetch failed")

        # Fetch consensus estimates (研报评级/一致预期 EPS/目标价) with
        # low-frequency caching; tolerate failure.
        consensus_data: Dict[str, ConsensusSnapshot] = {}
        try:
            consensus_data = load_consensus_data(
                self.config.watchlist,
                end_date=trading_date,
                cache=self._consensus_cache,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Consensus data fetch failed")

        # Resolve concept boards, reusing the prior same-trading-day mapping so a
        # repeat refresh skips the throttled Eastmoney membership calls.
        concept_boards = self._resolve_concept_boards(previous, trading_date)

        # Fetch the whole-market limit-up pool once and share it across market
        # sentiment and concept heat (2.16 -> 2.15 zero extra requests).
        try:
            market_breadth = fetch_market_breadth()
        except Exception:  # noqa: BLE001
            logger.exception("Market breadth fetch failed")
            market_breadth = {"source": "unavailable", "limit_up_rows": []}
        market_sentiment = load_market_sentiment(market_breadth)

        # Concept heat: reuse the prior same-day concept ranking, and the shared
        # market breadth, so a repeat refresh skips both network fetches.
        concepts: List[ConceptStrength] = []
        concept_snapshots: Dict[str, ConceptSnapshot] = {}
        try:
            concepts, concept_snapshots = load_concept_data(
                concept_boards,
                ranking=self._cached_concept_ranking(previous, trading_date),
                breadth=market_breadth,
            )
        except Exception:  # noqa: BLE001 - concept view must not break refresh
            logger.exception("Concept data computation failed")

        symbol_snapshots: List[SymbolSnapshot] = []
        unresolved: List[str] = []
        data_gaps: List[Dict[str, Any]] = []

        for code in self.config.watchlist:
            if code in raw_data.get("_unresolved", []):
                unresolved.append(code)
                data_gaps.append({"code": code, "reason": "unresolved_symbol"})
                continue

            records = raw_data.get(code)
            if records is None:
                data_gaps.append({"code": code, "reason": "no_data"})
                continue

            try:
                df = self._records_to_dataframe(records)
                if df.empty:
                    data_gaps.append({"code": code, "reason": "empty_frame"})
                    continue
                sector_board = sector_boards.get(code)
                snapshot = self._analyze_symbol(
                    code,
                    df,
                    name=names.get(code),
                    capital=capital_data.get(code),
                    sector_board=sector_board,
                    benchmark_df=benchmark_df,
                    valuation=valuation_data.get(code),
                    events=events_data.get(code),
                    chip=chip_data.get(code),
                    concept=concept_snapshots.get(code),
                    consensus=consensus_data.get(code),
                )
                # Use the latest available trading date from actual data.
                if snapshot.period_signals:
                    latest = max(
                        ps.metrics.end_date for ps in snapshot.period_signals.values() if ps.metrics.end_date
                    )
                    if latest and latest < trading_date:
                        trading_date = latest
                symbol_snapshots.append(snapshot)
            except Exception as exc:  # noqa: BLE001 — per-symbol failure must not kill the run
                logger.exception("Failed to analyze %s", code)
                data_gaps.append({"code": code, "reason": f"analysis_error: {exc}"})

        # Compute cross-sectional RPS after all symbols have their period metrics.
        self._compute_and_attach_rps(symbol_snapshots, benchmark_df)

        sectors = self._compute_sector_strength(
            symbol_snapshots, previous=previous, trading_date=trading_date
        )

        rankings = self._compute_rankings(symbol_snapshots)
        diff_map = self._compute_diff_map(symbol_snapshots, previous)

        # Attach diffs and close-derived consensus metrics (forward PE / upside)
        # once the per-symbol close is known.
        for snapshot in symbol_snapshots:
            snapshot.diff = diff_map.get(snapshot.code)
            if snapshot.consensus is not None:
                compute_forward_metrics(snapshot.consensus, snapshot.close)

        return TrackerSnapshot(
            generated_at=datetime.now().astimezone(),
            trading_date=trading_date,
            as_of_date=end_date,
            config=self.config,
            symbols=symbol_snapshots,
            rankings=rankings,
            sectors=sectors,
            concepts=concepts,
            market_sentiment=market_sentiment,
            unresolved=unresolved,
            data_gaps=data_gaps,
        )

    def _fetch_data(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """Fetch normalized OHLCV for all configured symbols."""
        try:
            return fetch_market_data(
                codes=codes,
                start_date=start_date,
                end_date=end_date,
                source="auto",
                interval="1D",
                max_rows=0,
                include_provenance=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Market data fetch failed")
            return {"_unresolved": codes, "error": str(exc)}

    @staticmethod
    def _records_to_dataframe(records: List[Dict[str, Any]]) -> pd.DataFrame:
        """Convert fetch_market_data records into a clean DataFrame."""
        if isinstance(records, dict) and "data" in records:
            records = records["data"]
        if not isinstance(records, list):
            return pd.DataFrame()

        df = pd.DataFrame(records)
        if df.empty:
            return df

        # Normalize column names to lowercase.
        df.columns = [str(c).lower() for c in df.columns]

        # Detect and parse the date column.
        date_col = None
        for candidate in ("date", "trade_date", "datetime", "time", "timestamp"):
            if candidate in df.columns:
                date_col = candidate
                break
        if date_col is None:
            return pd.DataFrame()

        df[date_col] = pd.to_datetime(df[date_col])
        df = df.sort_values(date_col).set_index(date_col)
        df.index.name = "date"

        # Rename common variants to canonical OHLCV.
        rename_map: Dict[str, str] = {}
        for canonical, aliases in {
            "open": ["open"],
            "high": ["high"],
            "low": ["low"],
            "close": ["close", "adj close", "adj_close"],
            "volume": ["volume", "vol"],
        }.items():
            for alias in aliases:
                if alias in df.columns:
                    rename_map[alias] = canonical
                    break
        df = df.rename(columns=rename_map)

        # Keep only columns we need and drop rows missing core fields.
        needed = ["open", "high", "low", "close", "volume"]
        available = [col for col in needed if col in df.columns]
        if {"close", "volume"}.difference(available):
            return pd.DataFrame()
        df = df[available].dropna(subset=["close"])
        for col in ("open", "high", "low", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.dropna()

    def _seed_caches_from_previous(
        self,
        previous: TrackerSnapshot | None,
        trading_date: date,
    ) -> None:
        """Seed capital/valuation/event caches from the prior snapshot.

        Reuses only data from a snapshot generated for the *same refresh base
        date* (``trading_date`` here is the refresh's ``end_date``); a refresh
        targeting a newer day still refetches. Seeding lets the loaders hit
        their TTL cache and skip re-requesting the throttled/blocked Eastmoney
        endpoints on every refresh within one refresh day.
        """
        if previous is None:
            return
        prev_as_of = (
            previous.as_of_date
            if previous.as_of_date is not None
            else previous.trading_date
        )
        if prev_as_of != trading_date:
            return
        for symbol in previous.symbols:
            code = symbol.code
            capital = symbol.capital
            if capital is not None:
                if capital.fund_flow_error is None:
                    self._capital_cache.set("fund_flow", code, trading_date, capital)
                if capital.margin_error is None:
                    self._capital_cache.set("margin", code, trading_date, capital)
            valuation = symbol.valuation
            if valuation is not None and valuation.error is None:
                self._valuation_cache.set(code, trading_date, valuation)
            events = symbol.events
            if events is not None and events.error is None:
                self._events_cache.set(code, trading_date, events)
            chip = symbol.chip
            if chip is not None and chip.error is None:
                self._chip_cache.set(code, trading_date, chip)
            consensus = symbol.consensus
            if consensus is not None and consensus.error is None:
                self._consensus_cache.set(code, trading_date, consensus)

    def _retain_last_good_capital(
        self,
        capital_data: Dict[str, CapitalMetrics],
        previous: TrackerSnapshot | None,
    ) -> Dict[str, CapitalMetrics]:
        """Keep an earlier good capital block when this refresh's fetch failed.

        A per-symbol fund-flow/margin error this refresh (throttled/blocked
        Eastmoney) is replaced by the same symbol's data from the prior snapshot
        when that data is error-free and non-empty, so a repeat refresh never
        blanks a card that already had values. Symbols with no prior data are
        left untouched (nothing good exists to retain).
        """
        if previous is None:
            return capital_data
        prev_capital = {
            s.code: s.capital for s in previous.symbols if s.capital is not None
        }
        for code, metrics in capital_data.items():
            prev = prev_capital.get(code)
            if prev is None:
                continue
            updates: Dict[str, Any] = {}
            if (
                metrics.fund_flow_error is not None
                and prev.fund_flow_error is None
                and prev.fund_flow.history
            ):
                updates.update(
                    fund_flow=prev.fund_flow,
                    fund_flow_source=prev.fund_flow_source,
                    fund_flow_error=None,
                )
            if (
                metrics.margin_error is not None
                and prev.margin_error is None
                and prev.margin.history
            ):
                updates.update(
                    margin=prev.margin,
                    margin_source=prev.margin_source,
                    margin_error=None,
                )
            if updates:
                capital_data[code] = metrics.model_copy(update=updates)
        return capital_data

    def _resolve_sector_boards(
        self,
        previous: TrackerSnapshot | None,
        trading_date: date,
    ) -> Dict[str, Optional[str]]:
        """Resolve each watchlist symbol's Eastmoney industry board.

        Reuses the prior snapshot's ``sector_board`` when it was captured on the
        same trading date (board membership does not change intraday), so a
        repeat refresh skips the throttled per-symbol Eastmoney membership
        requests. Only symbols absent from the prior mapping are resolved fresh;
        failures degrade to ``None`` and are retried on the next refresh.
        """
        boards: Dict[str, Optional[str]] = {}
        if previous is not None and previous.trading_date == trading_date:
            for symbol in previous.symbols:
                if symbol.sector_board:
                    boards[symbol.code] = symbol.sector_board
        for code in self.config.watchlist:
            if code in boards:
                continue
            try:
                boards[code] = resolve_industry_board(code)
            except Exception:  # noqa: BLE001 — per-symbol failure degrades to None
                logger.exception("Sector board resolution failed for %s", code)
                boards[code] = None
        return boards

    def _resolve_concept_boards(
        self,
        previous: TrackerSnapshot | None,
        trading_date: date,
    ) -> Dict[str, List[str]]:
        """Resolve each watchlist symbol's Eastmoney concept boards.

        Reuses the prior snapshot's ``concept.boards`` when it was captured on
        the same trading date, so a repeat refresh skips the throttled per-symbol
        membership requests. Only symbols absent from the prior mapping are
        resolved fresh; failures degrade to ``[]`` and are retried next refresh.
        """
        boards: Dict[str, List[str]] = {}
        if previous is not None and previous.trading_date == trading_date:
            for symbol in previous.symbols:
                if symbol.concept is not None and symbol.concept.boards:
                    boards[symbol.code] = symbol.concept.boards
        for code in self.config.watchlist:
            if code in boards:
                continue
            try:
                boards[code] = resolve_concept_boards(code)
            except Exception:  # noqa: BLE001 — per-symbol failure degrades to []
                logger.exception("Concept board resolution failed for %s", code)
                boards[code] = []
        return boards

    def _fetch_capital_data(
        self,
        codes: List[str],
        trading_date: date,
        days: int,
    ) -> Dict[str, CapitalMetrics]:
        """Fetch margin-trading data for all configured symbols."""
        return load_capital_data(
            codes,
            end_date=trading_date,
            days=days,
            cache=self._capital_cache,
        )

    def _fetch_benchmark_data(
        self,
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """Fetch CSI300 index or ETF OHLCV for RPS benchmarking.

        Tries the index first, then falls back to the ETF. Returns ``None``
        when neither resolves.
        """
        for code in (_RPS_MARKET_BENCHMARK, _RPS_MARKET_BENCHMARK_FALLBACK):
            try:
                raw = fetch_market_data(
                    codes=[code],
                    start_date=start_date,
                    end_date=end_date,
                    source="auto",
                    interval="1D",
                    max_rows=0,
                    include_provenance=False,
                )
                if code in raw.get("_unresolved", []):
                    continue
                records = raw.get(code)
                if not records:
                    continue
                df = self._records_to_dataframe(records)
                if not df.empty:
                    return df
            except Exception:  # noqa: BLE001
                logger.warning("Benchmark fetch failed for %s", code)
        return None

    def _analyze_symbol(
        self,
        code: str,
        df: pd.DataFrame,
        name: Optional[str] = None,
        capital: Optional[CapitalMetrics] = None,
        sector_board: Optional[str] = None,
        benchmark_df: Optional[pd.DataFrame] = None,
        valuation: Optional[ValuationSnapshot] = None,
        events: Optional[EventSnapshot] = None,
        chip: Optional[ChipSnapshot] = None,
        concept: Optional[ConceptSnapshot] = None,
        consensus: Optional[ConsensusSnapshot] = None,
    ) -> SymbolSnapshot:
        """Compute metrics, signals, risk measures, and summary for one symbol."""
        df = compute_mas(df)
        df["rsi"] = compute_rsi(df["close"])

        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest

        close = float(latest["close"])
        prev_close = float(prev["close"])
        daily_return = (close / prev_close - 1) if prev_close else 0.0
        volume = float(latest["volume"]) if "volume" in latest and pd.notna(latest["volume"]) else None
        avg_volume_20 = float(df["volume"].tail(20).mean()) if "volume" in df.columns else None

        # Make capital available to signal detectors via df attrs.
        if capital is not None:
            df.attrs["capital"] = capital

        risk = self._compute_risk_metrics(df, close, benchmark_df)

        period_signals: Dict[str, PeriodSignals] = {}
        burst_series = _volume_burst_series(df) if "volume" in df.columns else None
        for period in self.config.periods:
            metrics = self._compute_period_metrics(df, period, burst=burst_series)
            signals = self._compute_period_signals(df, period)
            period_signals[str(period)] = PeriodSignals(metrics=metrics, signals=signals)

        volume_series = self._build_volume_series(df, burst=burst_series)

        return SymbolSnapshot(
            code=code,
            name=name,
            close=close,
            prev_close=prev_close,
            daily_return=round(daily_return, 6),
            volume=volume,
            avg_volume_20=avg_volume_20,
            volume_series=volume_series,
            capital=capital,
            risk=risk,
            valuation=valuation,
            events=events,
            chip=chip,
            concept=concept,
            consensus=consensus,
            period_signals=period_signals,
            sector_board=sector_board,
            sector_board_source="eastmoney" if sector_board else None,
        )

    def _compute_risk_metrics(
        self,
        df: pd.DataFrame,
        close: float,
        benchmark_df: Optional[pd.DataFrame],
    ) -> Optional[RiskMetrics]:
        """Compute symbol-level ATR, max drawdown, and beta.

        Beta reuses the RPS benchmark frame (CSI 300 index with ETF fallback).
        Returns ``None`` when no metric can be computed so the frontend does
        not render an empty risk card.
        """
        atr_period = int(self.config.thresholds.get("atr_period", 14))
        dd_window = int(self.config.thresholds.get("max_drawdown_window", 60))
        beta_window = int(self.config.thresholds.get("beta_window", 60))
        stop_k = float(self.config.thresholds.get("stop_loss_atr_multiple", 2.0))

        atr = compute_atr(df, atr_period)
        atr_pct = round(atr / close, 6) if atr is not None and close else None
        max_dd = compute_max_drawdown(df, dd_window)
        beta = compute_beta(df, benchmark_df, beta_window)

        stop_price = round(close - stop_k * atr, 3) if atr is not None and close else None

        if atr is None and max_dd is None and beta is None:
            return None

        return RiskMetrics(
            atr_14=round(atr, 4) if atr is not None else None,
            atr_pct=atr_pct,
            max_drawdown_60d=max_dd,
            beta_vs_index=beta,
            beta_window=beta_window if beta is not None else None,
            benchmark_code=_RPS_MARKET_BENCHMARK if beta is not None else None,
            stop_loss_price=stop_price,
            stop_loss_atr_multiple=stop_k,
        )

    def _compute_period_metrics(
        self,
        df: pd.DataFrame,
        period: int,
        burst: Optional[pd.Series] = None,
    ) -> PeriodMetrics:
        """Return numeric metrics for the given period window."""
        window = df.tail(period)
        if window.empty:
            return PeriodMetrics(period=period)

        start_price = float(window["close"].iloc[0])
        end_price = float(window["close"].iloc[-1])
        return_pct = (end_price / start_price - 1) if start_price else 0.0

        log_returns = window["close"].pct_change().dropna()
        volatility: Optional[float] = None
        if len(log_returns) >= 2:
            volatility = float(log_returns.std() * (252 ** 0.5))

        volume_ratio: Optional[float] = None
        if "volume" in df.columns and len(df) >= period * 2:
            recent_vol = df["volume"].tail(period).mean()
            prior_vol = df["volume"].iloc[-period * 2 : -period].mean()
            if prior_vol and prior_vol > 0:
                volume_ratio = float(recent_vol / prior_vol)

        avg_volume: Optional[float] = None
        if "volume" in window.columns:
            avg_volume = float(window["volume"].mean())

        volume_expansion_days: Optional[int] = None
        volume_expansion_ratio: Optional[float] = None
        if "volume" in df.columns:
            burst = burst if burst is not None else _volume_burst_series(df)
            window_burst = burst.iloc[-period:]
            if len(window_burst):
                volume_expansion_days = int(window_burst.sum())
                volume_expansion_ratio = float(window_burst.mean())

        rsi: Optional[float] = None
        if "rsi" in df.columns and pd.notna(window["rsi"].iloc[-1]):
            rsi = float(window["rsi"].iloc[-1])

        price_vs_ma20: Optional[float] = None
        if "ma20" in window.columns and pd.notna(window["ma20"].iloc[-1]):
            price_vs_ma20 = float(end_price / window["ma20"].iloc[-1] - 1)

        latest = window.iloc[-1]
        return PeriodMetrics(
            period=period,
            start_date=window.index[0].date(),
            end_date=window.index[-1].date(),
            sessions=len(window),
            return_pct=round(return_pct, 6),
            annualized_volatility=round(volatility, 6) if volatility is not None else None,
            volume_ratio=round(volume_ratio, 3) if volume_ratio is not None else None,
            avg_volume=round(avg_volume, 2) if avg_volume is not None else None,
            volume_expansion_days=volume_expansion_days,
            volume_expansion_ratio=round(volume_expansion_ratio, 4)
            if volume_expansion_ratio is not None
            else None,
            rsi=round(rsi, 2) if rsi is not None else None,
            price_vs_ma20=round(price_vs_ma20, 5) if price_vs_ma20 is not None else None,
            ma5=float(latest["ma5"]) if "ma5" in latest and pd.notna(latest["ma5"]) else None,
            ma10=float(latest["ma10"]) if "ma10" in latest and pd.notna(latest["ma10"]) else None,
            ma20=float(latest["ma20"]) if "ma20" in latest and pd.notna(latest["ma20"]) else None,
            ma60=float(latest["ma60"]) if "ma60" in latest and pd.notna(latest["ma60"]) else None,
        )

    def _build_volume_series(
        self,
        df: pd.DataFrame,
        burst: Optional[pd.Series] = None,
    ) -> List[VolumePoint]:
        """Daily OHLCV tail for the volume card's price + volume chart.

        Keeps the longest configured period plus a small context window so the
        earliest visible bar can still be classified as a burst (a 5-session
        trailing baseline). ``burst`` is the shared mask from
        ``_volume_burst_series``; computed here when the caller did not supply it.
        OHLC is carried when the upstream records include those columns.
        """
        if "volume" not in df.columns or df.empty:
            return []
        longest = max(self.config.periods)
        tail = df.tail(longest + _VOLUME_SERIES_CONTEXT)
        if burst is None:
            burst = _volume_burst_series(df)
        points: List[VolumePoint] = []
        for idx, row in tail.iterrows():
            points.append(
                VolumePoint(
                    trade_date=idx.date(),
                    open=_as_float(row.get("open")),
                    high=_as_float(row.get("high")),
                    low=_as_float(row.get("low")),
                    close=_as_float(row.get("close")),
                    volume=_as_float(row.get("volume")),
                    is_burst=bool(burst.get(idx, False)),
                )
            )
        return points

    def _compute_period_signals(
        self,
        df: pd.DataFrame,
        period: int,
    ) -> Dict[SignalType, SignalValue]:
        """Run enabled signal detectors over the requested period window."""
        # Each detector receives the full available history so it can compute
        # its own internal windows (e.g. breakout_window), but it should only
        # evaluate the latest bar.
        results: Dict[SignalType, SignalValue] = {}
        for signal_name in self.config.signals:
            detector = get_detector(signal_name)
            results[signal_name] = detector.detect(
                code="",
                df=df,
                period=period,
                thresholds=self.config.thresholds,
            )
        return results

    def _compute_and_attach_rps(
        self,
        snapshots: List[SymbolSnapshot],
        benchmark_df: Optional[pd.DataFrame],
    ) -> None:
        """Compute cross-sectional RPS percentiles and attach them to snapshots.

        Market RPS uses the watchlist plus the CSI300 benchmark. Sector RPS
        groups symbols by their resolved Eastmoney industry board.
        """
        if not snapshots:
            return

        periods = sorted(int(p) for p in snapshots[0].period_signals.keys())

        # Pre-compute benchmark return for each period once.
        benchmark_returns: Dict[int, Optional[float]] = {}
        if benchmark_df is not None and not benchmark_df.empty:
            for period in periods:
                window = benchmark_df.tail(period)
                if len(window) >= 2:
                    start_price = float(window["close"].iloc[0])
                    end_price = float(window["close"].iloc[-1])
                    if start_price:
                        benchmark_returns[period] = round(end_price / start_price - 1, 6)

        for period in periods:
            # Collect returns and sector groups.
            returns: List[float] = []
            symbol_returns: Dict[str, float] = {}
            sector_groups: Dict[str, List[str]] = {}

            for snapshot in snapshots:
                ps = snapshot.period_signals.get(str(period))
                if ps is None or ps.metrics.return_pct is None:
                    continue
                ret = ps.metrics.return_pct
                returns.append(ret)
                symbol_returns[snapshot.code] = ret
                board = snapshot.sector_board
                if board:
                    sector_groups.setdefault(board, []).append(snapshot.code)

            # Include benchmark in the market universe if available.
            benchmark_ret = benchmark_returns.get(period)
            market_universe = returns.copy()
            if benchmark_ret is not None:
                market_universe.append(benchmark_ret)

            # Compute market RPS.
            market_rps: Dict[str, float] = {}
            if len(market_universe) >= 2:
                series = pd.Series(market_universe)
                ranks = series.rank(method="min")
                n = len(series)
                pct_values = ((ranks - 1) / (n - 1) * 100).round(2)
                # Map ranks back to symbols (the first N entries correspond to symbols).
                for idx, snapshot in enumerate(snapshots):
                    if snapshot.code not in symbol_returns:
                        continue
                    # Position in the universe equals the symbol's order in `returns`.
                    symbol_idx = list(symbol_returns.keys()).index(snapshot.code)
                    market_rps[snapshot.code] = float(pct_values.iloc[symbol_idx])

            # Compute sector RPS.
            sector_rps: Dict[str, float] = {}
            for board, codes in sector_groups.items():
                if len(codes) < 2:
                    continue
                group_returns = [symbol_returns[code] for code in codes if code in symbol_returns]
                if len(group_returns) < 2:
                    continue
                series = pd.Series(group_returns)
                ranks = series.rank(method="min")
                n = len(series)
                pct_values = ((ranks - 1) / (n - 1) * 100).round(2)
                for idx, code in enumerate(codes):
                    if code not in symbol_returns:
                        continue
                    sector_rps[code] = float(pct_values.iloc[idx])

            # Attach computed values back to each snapshot's PeriodMetrics.
            for snapshot in snapshots:
                ps = snapshot.period_signals.get(str(period))
                if ps is None:
                    continue
                ps.metrics.rps_market = market_rps.get(snapshot.code)
                ps.metrics.rps_sector = sector_rps.get(snapshot.code)
                ps.metrics.benchmark_return_pct = benchmark_ret

    def _cached_sector_ranking(
        self,
        previous: TrackerSnapshot | None,
        trading_date: date | None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Reconstruct the whole-market board ranking from the prior snapshot.

        Only the network-fetched part (the Eastmoney ``clist`` ranking) is
        frozen; watchlist aggregates are always recomputed fresh from the current
        symbols. Returns ``None`` when there is nothing reusable so the caller
        refetches the ranking.
        """
        if previous is None or previous.trading_date != trading_date:
            return None
        ranked = [
            sector
            for sector in previous.sectors
            if sector.source == "eastmoney" and sector.market_rank is not None
        ]
        if not ranked:
            return None
        return [
            {
                "board_code": sector.board_code,
                "board_name": sector.board_name,
                "change_pct": sector.change_pct,
                "fund_flow_net": sector.fund_flow_net,
                "up_count": sector.up_count,
                "down_count": sector.down_count,
                "leader": sector.leader,
            }
            for sector in sorted(ranked, key=lambda s: s.market_rank)
        ]

    def _cached_concept_ranking(
        self,
        previous: TrackerSnapshot | None,
        trading_date: date | None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Reconstruct the whole-market concept ranking from the prior snapshot.

        Only the network-fetched part (the Eastmoney ``clist`` concept ranking)
        is frozen; watchlist membership is recomputed fresh. Returns ``None``
        when there is nothing reusable so the caller refetches.
        """
        if previous is None or previous.trading_date != trading_date:
            return None
        ranked = [
            concept
            for concept in previous.concepts
            if concept.source == "eastmoney" and concept.market_rank is not None
        ]
        if not ranked:
            return None
        return [
            {
                "board_code": concept.board_code,
                "board_name": concept.board_name,
                "change_pct": concept.change_pct,
                "fund_flow_net": concept.fund_flow_net,
                "up_count": concept.up_count,
                "down_count": concept.down_count,
                "leader": concept.leader,
            }
            for concept in sorted(ranked, key=lambda c: c.market_rank)
        ]

    def _compute_sector_strength(
        self,
        snapshots: List[SymbolSnapshot],
        previous: TrackerSnapshot | None = None,
        trading_date: date | None = None,
    ) -> List[SectorStrength]:
        """Compute the sector-strength board and attach each board's rank.

        Aggregates watchlist metrics per Eastmoney industry board across every
        configured period and merges the whole-market board ranking. When a prior
        same-trading-day snapshot is supplied, its ranking is reused (frozen) so
        the throttled Eastmoney ranking fetch is skipped; aggregates are always
        recomputed from the current snapshots. Tolerates failure via
        ``load_sector_strength`` returning ``[]``.
        """
        periods = sorted(self.config.periods)

        try:
            sectors = load_sector_strength(
                snapshots,
                periods=periods,
                ranking=self._cached_sector_ranking(previous, trading_date),
            )
        except Exception:  # noqa: BLE001 - sector view must not break refresh
            logger.exception("Sector strength computation failed")
            sectors = []

        if sectors:
            rank_by_board = {
                s.board_name: s.market_rank for s in sectors if s.market_rank is not None
            }
            for snapshot in snapshots:
                board = snapshot.sector_board
                if board:
                    snapshot.sector_strength_rank = rank_by_board.get(board)
        return sectors

    @staticmethod
    def _compute_rankings(snapshots: List[SymbolSnapshot]) -> Dict[str, List[str]]:
        """Rank symbols by return, enabled signals, and total triggered count."""
        rankings: Dict[str, List[str]] = {}
        if not snapshots:
            return rankings

        # Infer available periods from the first snapshot.
        periods = sorted(int(p) for p in snapshots[0].period_signals.keys())

        def _return_for_period(snapshot: SymbolSnapshot, period: int) -> float:
            ps = snapshot.period_signals.get(str(period))
            return ps.metrics.return_pct or 0.0 if ps else 0.0

        for period in periods:
            sorted_symbols = sorted(
                snapshots,
                key=lambda s: _return_for_period(s, period),
                reverse=True,
            )
            rankings[f"return_{period}"] = [s.code for s in sorted_symbols]

        # Per-signal rankings for enabled detectors that opt in.
        baseline_period = str(periods[0]) if periods else "10"
        all_signal_names: set[str] = set()
        if snapshots and periods:
            all_signal_names = set().union(
                *(set(s.period_signals[baseline_period].signals.keys()) for s in snapshots)
            )

        for signal_name in all_signal_names:
            meta = get_detector_meta(signal_name)
            if not meta.ranking_enabled:
                continue
            extractor = meta.ranking_extractor or (lambda sv: 1.0 if sv.triggered else 0.0)

            def _signal_score(snapshot: SymbolSnapshot, name: str = signal_name, fn: Callable[[SignalValue], float] = extractor) -> float:
                score = 0.0
                for ps in snapshot.period_signals.values():
                    signal = ps.signals.get(name)
                    if signal:
                        score += fn(signal)
                return score

            rankings[signal_name] = [
                s.code for s in sorted(snapshots, key=_signal_score, reverse=True)
            ]

        # Total triggered signal count across all periods.
        def _signal_count(snapshot: SymbolSnapshot) -> int:
            count = 0
            for ps in snapshot.period_signals.values():
                for signal in ps.signals.values():
                    if signal.triggered:
                        count += 1
            return count

        rankings["signal_count"] = [
            s.code for s in sorted(snapshots, key=_signal_count, reverse=True)
        ]

        # RPS market/sector rankings per period.
        for period in periods:

            def _rps_market(snapshot: SymbolSnapshot, period: int = period) -> float:
                ps = snapshot.period_signals.get(str(period))
                return ps.metrics.rps_market or 0.0 if ps else 0.0

            rankings[f"rps_market_{period}"] = [
                s.code for s in sorted(snapshots, key=_rps_market, reverse=True)
            ]

            def _rps_sector(snapshot: SymbolSnapshot, period: int = period) -> float:
                ps = snapshot.period_signals.get(str(period))
                return ps.metrics.rps_sector or 0.0 if ps else 0.0

            rankings[f"rps_sector_{period}"] = [
                s.code
                for s in sorted(
                    [s for s in snapshots if _rps_sector(s) > 0],
                    key=_rps_sector,
                    reverse=True,
                )
            ]

        return rankings

    def _compute_diff_map(
        self,
        current: List[SymbolSnapshot],
        previous: TrackerSnapshot | None,
    ) -> Dict[str, CrossDayDiff]:
        """Compute per-symbol cross-day changes against the previous snapshot."""
        if previous is None:
            return {}

        prev_by_code = {s.code: s for s in previous.symbols}
        diff_map: Dict[str, CrossDayDiff] = {}

        for snap in current:
            prev = prev_by_code.get(snap.code)
            if prev is None:
                continue

            new_signals: List[str] = []
            cleared_signals: List[str] = []
            curr_period = snap.period_signals.get("10")
            prev_period = prev.period_signals.get("10")
            if curr_period and prev_period:
                for name in self.config.signals:
                    curr_sig = curr_period.signals.get(name)
                    prev_sig = prev_period.signals.get(name)
                    if curr_sig and curr_sig.triggered and (not prev_sig or not prev_sig.triggered):
                        new_signals.append(name)
                    if prev_sig and prev_sig.triggered and (not curr_sig or not curr_sig.triggered):
                        cleared_signals.append(name)

            diff_map[snap.code] = CrossDayDiff(
                signal_count={
                    "prev": sum(
                        1 for ps in prev.period_signals.values() for s in ps.signals.values() if s.triggered
                    ),
                    "curr": sum(
                        1 for ps in snap.period_signals.values() for s in ps.signals.values() if s.triggered
                    ),
                },
                return_pct=round((snap.daily_return or 0.0) - (prev.daily_return or 0.0), 6)
                if snap.daily_return is not None and prev.daily_return is not None
                else None,
                new_signals=new_signals,
                cleared_signals=cleared_signals,
            )

        return diff_map


__all__ = ["StockTrackerEngine"]
