"""Unit tests for the tracker engine's same-day cache reuse and last-good retention.

These cover the two regressions that made 主力资金 appear only on the first
refresh of a day:

* ``_seed_caches_from_previous`` compares against the snapshot's *refresh base
  date* (``as_of_date``) instead of its lagged data ``trading_date``, so a
  repeat refresh reuses the earlier fetch instead of re-requesting Eastmoney.
* ``_retain_last_good_capital`` keeps an earlier error-free capital block when
  this refresh's fetch failed, instead of overwriting good data with an error.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.stock_tracker.engine import StockTrackerEngine
from src.stock_tracker.models import (
    CapitalMetrics,
    FundFlowHistoryItem,
    FundFlowSnapshot,
    MarginHistoryItem,
    MarginSnapshot,
    SymbolSnapshot,
    TrackerConfig,
    TrackerSnapshot,
)

_CODE = "600519.SH"


def _good_metrics() -> CapitalMetrics:
    """Error-free capital with non-empty fund-flow and margin history."""
    return CapitalMetrics(
        fund_flow=FundFlowSnapshot(
            trade_date=date(2026, 9, 1),
            main_net=1_000_000.0,
            history=[
                FundFlowHistoryItem(trade_date=date(2026, 9, 1), main_net=1_000_000.0)
            ],
        ),
        fund_flow_source="eastmoney",
        fund_flow_error=None,
        margin=MarginSnapshot(
            trade_date=date(2026, 9, 1),
            financing_balance=5_000_000.0,
            history=[
                MarginHistoryItem(trade_date=date(2026, 9, 1), financing_balance=5_000_000.0)
            ],
        ),
        margin_source="eastmoney",
        margin_error=None,
    )


def _errored_metrics() -> CapitalMetrics:
    """Capital whose fetches failed this refresh (no fund-flow/margin data)."""
    return CapitalMetrics(
        fund_flow_error="Connection aborted: RemoteDisconnected",
        fund_flow_source="unavailable",
        margin_error="Connection aborted: RemoteDisconnected",
        margin_source="unavailable",
    )


def _snapshot(
    *,
    code: str = _CODE,
    capital: CapitalMetrics | None = None,
    as_of_date: date | None = date(2026, 9, 3),
    trading_date: date | None = date(2026, 9, 2),
) -> TrackerSnapshot:
    return TrackerSnapshot(
        generated_at=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
        trading_date=trading_date,
        as_of_date=as_of_date,
        config=TrackerConfig(),
        symbols=[SymbolSnapshot(code=code, capital=capital)],
    )


# --------------------------------------------------------------------------- #
# _seed_caches_from_previous
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_seed_reuses_same_refresh_day_capital() -> None:
    """A repeat refresh with the same as_of_date must hit the seeded cache
    (no re-request of Eastmoney)."""
    engine = StockTrackerEngine(config=TrackerConfig())
    previous = _snapshot(capital=_good_metrics())

    engine._seed_caches_from_previous(previous, trading_date=date(2026, 9, 3))

    cached = engine._capital_cache.get("fund_flow", _CODE, date(2026, 9, 3))
    assert cached is not None
    assert cached.fund_flow_error is None
    assert cached.fund_flow.history


@pytest.mark.unit
def test_seed_skips_a_newer_refresh_day() -> None:
    """A refresh targeting a newer day must not reuse yesterday's capital; the
    cache stays empty so the loader issues a fresh request."""
    engine = StockTrackerEngine(config=TrackerConfig())
    previous = _snapshot(capital=_good_metrics(), as_of_date=date(2026, 9, 3))

    engine._seed_caches_from_previous(previous, trading_date=date(2026, 9, 4))

    assert engine._capital_cache.get("fund_flow", _CODE, date(2026, 9, 4)) is None


@pytest.mark.unit
def test_seed_uses_trading_date_when_as_of_date_missing() -> None:
    """Legacy snapshots (no as_of_date) fall back to trading_date, so snapshots
    written before this change still behave: same base day is reused."""
    engine = StockTrackerEngine(config=TrackerConfig())
    previous = _snapshot(capital=_good_metrics(), as_of_date=None, trading_date=date(2026, 9, 3))

    engine._seed_caches_from_previous(previous, trading_date=date(2026, 9, 3))

    assert engine._capital_cache.get("fund_flow", _CODE, date(2026, 9, 3)) is not None


# --------------------------------------------------------------------------- #
# _retain_last_good_capital
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_retain_last_good_capital_keeps_prior_good() -> None:
    """A failed fetch must not blank a card that already had good data."""
    engine = StockTrackerEngine(config=TrackerConfig())
    previous = _snapshot(capital=_good_metrics())
    current = {_CODE: _errored_metrics()}

    retained = engine._retain_last_good_capital(current, previous)

    metrics = retained[_CODE]
    assert metrics.fund_flow_error is None
    assert metrics.margin_error is None
    assert metrics.fund_flow.history
    assert metrics.margin.history
    assert metrics.fund_flow_source == "eastmoney"


@pytest.mark.unit
def test_retain_leaves_error_when_no_prior_data() -> None:
    """No prior good data exists for the symbol, so the error is kept as-is
    (there is nothing to preserve)."""
    engine = StockTrackerEngine(config=TrackerConfig())
    previous = _snapshot(capital=None)
    current = {_CODE: _errored_metrics()}

    retained = engine._retain_last_good_capital(current, previous)

    assert retained[_CODE].fund_flow_error is not None


@pytest.mark.unit
def test_retain_ignores_empty_prior_history() -> None:
    """A prior 'empty but error-free' block (history empty) is not treated as
    good data to resurrect."""
    engine = StockTrackerEngine(config=TrackerConfig())
    empty = _good_metrics()
    empty = empty.model_copy(
        update={
            "fund_flow": FundFlowSnapshot(),
            "margin": MarginSnapshot(),
        }
    )
    previous = _snapshot(capital=empty)
    current = {_CODE: _errored_metrics()}

    retained = engine._retain_last_good_capital(current, previous)

    assert retained[_CODE].fund_flow_error is not None


# --------------------------------------------------------------------------- #
# Loader boundary: a seeded refresh must not re-request Eastmoney.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_capital_fetch_is_served_from_seed_without_network() -> None:
    """After seeding from a same-refresh-day snapshot, _fetch_capital_data
    returns the cached metrics even though the underlying fetch functions are
    patched to raise (i.e. the repeat refresh never re-contacts Eastmoney)."""
    from unittest.mock import patch

    engine = StockTrackerEngine(config=TrackerConfig())
    previous = _snapshot(capital=_good_metrics())
    engine._seed_caches_from_previous(previous, trading_date=date(2026, 9, 3))

    with (
        patch(
            "src.tools.fund_flow_tool.fetch_symbol_fund_flow",
            side_effect=RuntimeError("should not be called"),
        ),
        patch(
            "src.tools.margin_trading_tool.fetch_symbol_margin_trading",
            side_effect=RuntimeError("should not be called"),
        ),
    ):
        metrics = engine._fetch_capital_data([_CODE], trading_date=date(2026, 9, 3), days=20)

    assert metrics[_CODE].fund_flow_error is None
    assert metrics[_CODE].fund_flow.history
    assert metrics[_CODE].margin.history

