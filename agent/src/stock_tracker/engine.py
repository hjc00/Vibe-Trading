"""Core computation engine for the A-share multi-period tracker."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from src.market_data import fetch_market_data
from src.stock_tracker.capital_data import _FUND_FLOW_LOOKBACK, CapitalDataCache, load_capital_data
from src.stock_tracker.models import (
    CapitalMetrics,
    CrossDayDiff,
    PeriodMetrics,
    PeriodSignals,
    RiskMetrics,
    SignalType,
    SignalValue,
    SymbolSnapshot,
    TrackerConfig,
    TrackerSnapshot,
)
from src.stock_tracker.names import fetch_a_share_names
from src.stock_tracker.risk import compute_atr, compute_beta, compute_max_drawdown
from src.stock_tracker.signals import compute_mas, compute_rsi, get_detector, get_detector_meta
from src.tools.sector_tool import resolve_industry_board

logger = logging.getLogger(__name__)

# Extra calendar days before the earliest requested period so that moving
# averages (especially 60-day) have enough history even with holidays.
_BUFFER_DAYS = 90

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

        # Resolve sector boards once per refresh; tolerate partial failures.
        sector_boards: Dict[str, Optional[str]] = {}
        try:
            for code in self.config.watchlist:
                sector_boards[code] = resolve_industry_board(code)
        except Exception:  # noqa: BLE001
            logger.exception("Sector board resolution failed")

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

        rankings = self._compute_rankings(symbol_snapshots)
        diff_map = self._compute_diff_map(symbol_snapshots, previous)

        # Attach diffs to snapshots.
        for snapshot in symbol_snapshots:
            snapshot.diff = diff_map.get(snapshot.code)

        return TrackerSnapshot(
            generated_at=datetime.now().astimezone(),
            trading_date=trading_date,
            config=self.config,
            symbols=symbol_snapshots,
            rankings=rankings,
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
        for period in self.config.periods:
            metrics = self._compute_period_metrics(df, period)
            signals = self._compute_period_signals(df, period)
            period_signals[str(period)] = PeriodSignals(metrics=metrics, signals=signals)

        return SymbolSnapshot(
            code=code,
            name=name,
            close=close,
            prev_close=prev_close,
            daily_return=round(daily_return, 6),
            volume=volume,
            avg_volume_20=avg_volume_20,
            capital=capital,
            risk=risk,
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

    def _compute_period_metrics(self, df: pd.DataFrame, period: int) -> PeriodMetrics:
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
            rsi=round(rsi, 2) if rsi is not None else None,
            price_vs_ma20=round(price_vs_ma20, 5) if price_vs_ma20 is not None else None,
            ma5=float(latest["ma5"]) if "ma5" in latest and pd.notna(latest["ma5"]) else None,
            ma10=float(latest["ma10"]) if "ma10" in latest and pd.notna(latest["ma10"]) else None,
            ma20=float(latest["ma20"]) if "ma20" in latest and pd.notna(latest["ma20"]) else None,
            ma60=float(latest["ma60"]) if "ma60" in latest and pd.notna(latest["ma60"]) else None,
        )

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
