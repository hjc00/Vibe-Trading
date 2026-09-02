"""Optional Tushare fallback adapters for China-market flow tools.

The public Eastmoney endpoints are free and remain the primary source for these
tools.  When they are unavailable, a configured Tushare token can recover the
same research workflow through a separate provider with a compatible envelope.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.config.accessor import get_env_config

_TUSHARE_TOKEN_PLACEHOLDERS = {"", "your-tushare-token"}


class TushareFallbackUnavailable(RuntimeError):
    """Raised when the optional Tushare fallback cannot be used."""


def _pro_api() -> Any:
    token = get_env_config().data.tushare_token.strip()
    if token in _TUSHARE_TOKEN_PLACEHOLDERS:
        raise TushareFallbackUnavailable("TUSHARE_TOKEN is not configured")
    try:
        import tushare as ts
    except Exception as exc:  # noqa: BLE001 - import errors vary by install
        raise TushareFallbackUnavailable(f"tushare import failed: {exc}") from exc
    return ts.pro_api(token)


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if bool(getattr(frame, "empty", False)):
        return []
    if hasattr(frame, "to_dict"):
        rows = frame.to_dict("records")
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, dict)]
    return []


def _compact_date(value: str) -> str:
    digits = str(value).strip().replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise TushareFallbackUnavailable(f"invalid date for tushare fallback: {value!r}")
    return digits


def _dashed_date(value: Any) -> str | None:
    if value is None:
        return None
    digits = str(value).strip().replace("-", "")
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(value)[:10] if value else None


def _date_window(days: int) -> tuple[str, str]:
    end = date.today()
    # Market holidays/weekends mean calendar days need slack to recover the
    # requested number of trading rows.
    start = end - timedelta(days=max(days * 3, 10))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _net_amount(row: dict[str, Any], buy_key: str, sell_key: str) -> float | None:
    buy = _to_float(row.get(buy_key))
    sell = _to_float(row.get(sell_key))
    if buy is None and sell is None:
        return None
    # Tushare moneyflow amount fields are in 10k CNY; the Eastmoney tool emits
    # CNY, so convert to keep the existing bucket units.
    return ((buy or 0.0) - (sell or 0.0)) * 10_000


def _ts_code(code: str) -> str:
    token = code.strip().upper()
    if "." in token:
        bare, suffix = token.split(".", 1)
        if suffix in {"SH", "SZ", "BJ"} and len(bare) == 6 and bare.isdigit():
            return f"{bare}.{suffix}"
        raise TushareFallbackUnavailable(f"unsupported Tushare symbol: {code}")
    for prefix in ("SH", "SZ", "BJ"):
        if token.startswith(prefix):
            token = token[len(prefix) :]
            break
    if len(token) != 6 or not token.isdigit():
        raise TushareFallbackUnavailable(f"unsupported Tushare symbol: {code}")
    if token.startswith(("5", "6", "9")):
        suffix = "SH"
    elif token.startswith(("0", "2", "3")):
        suffix = "SZ"
    elif token.startswith(("4", "8")):
        suffix = "BJ"
    else:
        raise TushareFallbackUnavailable(f"unsupported Tushare symbol: {code}")
    return f"{token}.{suffix}"


def fetch_fund_flow(symbol: str, *, days: int) -> dict[str, Any]:
    ts_code = _ts_code(symbol)
    start_date, end_date = _date_window(days)
    rows = _records(
        _pro_api().moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
    )
    parsed: list[dict[str, Any]] = []
    for row in rows:
        parsed.append(
            {
                "timestamp": _dashed_date(row.get("trade_date")),
                "main": (_to_float(row.get("net_mf_amount")) or 0.0) * 10_000,
                "small": _net_amount(row, "buy_sm_amount", "sell_sm_amount"),
                "medium": _net_amount(row, "buy_md_amount", "sell_md_amount"),
                "large": _net_amount(row, "buy_lg_amount", "sell_lg_amount"),
                "super_large": _net_amount(row, "buy_elg_amount", "sell_elg_amount"),
            }
        )
    parsed.sort(key=lambda item: item.get("timestamp") or "")
    parsed = parsed[-days:]
    return {"symbol": symbol, "ts_code": ts_code, "source": "tushare", "rows": parsed}


def fetch_dragon_tiger(trade_date: str, code: str | None) -> dict[str, Any]:
    compact = _compact_date(trade_date)
    ts_code = _ts_code(code) if code else None
    pro = _pro_api()
    kwargs: dict[str, str] = {"trade_date": compact}
    if ts_code:
        kwargs["ts_code"] = ts_code
    appearances_raw = _records(pro.top_list(**kwargs))
    appearances = [
        {
            "code": str(row.get("ts_code", "")).split(".", 1)[0] or None,
            "name": row.get("name"),
            "close": row.get("close"),
            "change_pct": row.get("pct_change"),
            "net_buy": row.get("net_amount"),
            "buy_amount": row.get("l_buy"),
            "sell_amount": row.get("l_sell"),
            "turnover": row.get("amount"),
            "reason": row.get("reason"),
        }
        for row in appearances_raw
    ]

    data: dict[str, Any] = {
        "date": _dashed_date(compact),
        "count": len(appearances_raw),
        "appearances": appearances,
    }
    if ts_code:
        seats_raw = _records(pro.top_inst(trade_date=compact, ts_code=ts_code))
        data["code"] = ts_code.split(".", 1)[0]
        data["seats"] = [
            {
                "seat": row.get("exalter"),
                "side": row.get("side"),
                "buy": row.get("buy"),
                "sell": row.get("sell"),
                "net": row.get("net_buy"),
                "rank": None,
            }
            for row in seats_raw
        ]
    return data


def fetch_northbound_flow(*, lookback_days: int) -> dict[str, Any]:
    start_date, end_date = _date_window(lookback_days)
    rows = _records(_pro_api().moneyflow_hsgt(start_date=start_date, end_date=end_date))
    history: list[dict[str, Any]] = []
    for row in rows:
        shanghai = _to_float(row.get("hgt"))
        shenzhen = _to_float(row.get("sgt"))
        total = _to_float(row.get("north_money"))
        history.append(
            {
                "trade_date": _dashed_date(row.get("trade_date")),
                "shanghai_connect": shanghai * 100 if shanghai is not None else None,
                "shenzhen_connect": shenzhen * 100 if shenzhen is not None else None,
                "total": total * 100 if total is not None else None,
            }
        )
    history.sort(key=lambda item: item.get("trade_date") or "")
    history = history[-lookback_days:]
    latest = history[-1] if history else {}
    return {
        "unit": "10k CNY",
        "lookback_days": lookback_days,
        "realtime": {
            "shanghai_connect": latest.get("shanghai_connect"),
            "shenzhen_connect": latest.get("shenzhen_connect"),
            "total": latest.get("total"),
        },
        "history": history,
    }


def fetch_margin_trading(code: str, *, days: int) -> dict[str, Any]:
    ts_code = _ts_code(code)
    start_date, end_date = _date_window(days)
    rows = _records(
        _pro_api().margin_detail(ts_code=ts_code, start_date=start_date, end_date=end_date)
    )
    normalized = [
        {
            "trade_date": _dashed_date(row.get("trade_date")),
            "financing_balance": _to_float(row.get("rzye")),
            "financing_buy": _to_float(row.get("rzmre")),
            "financing_repay": _to_float(row.get("rzche")),
            "short_balance": _to_float(row.get("rqye")),
            "short_volume": _to_float(row.get("rqyl")),
            "margin_total_balance": _to_float(row.get("rzrqye")),
        }
        for row in rows
    ]
    normalized.sort(key=lambda item: item.get("trade_date") or "", reverse=True)
    return {"code": ts_code.split(".", 1)[0], "ts_code": ts_code, "rows": normalized[:days]}


def fetch_daily_basic(code: str, *, days: int) -> dict[str, Any]:
    """Fetch per-day valuation multiples (``daily_basic``) for one symbol.

    Tushare ``daily_basic`` returns the authoritative PE_TTM / PB / PS_TTM /
    dividend-yield fields that the Eastmoney valuation report lacks. Amounts
    are normalized to the repo's CNY unit (``total_mv`` is 10k CNY -> CNY).
    """
    ts_code = _ts_code(code)
    start_date, end_date = _date_window(days)
    rows = _records(
        _pro_api().daily_basic(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,dv_ratio,total_mv",
        )
    )
    parsed = [
        {
            "trade_date": _dashed_date(row.get("trade_date")),
            "close": _to_float(row.get("close")),
            "pe_ttm": _to_float(row.get("pe_ttm")),
            "pb": _to_float(row.get("pb")),
            "ps_ttm": _to_float(row.get("ps_ttm")),
            "dividend_yield": _to_float(row.get("dv_ratio")),
            "total_market_cap": (_to_float(row.get("total_mv")) or 0.0) * 10_000,
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("trade_date") or "")
    parsed = parsed[-days:]
    return {"code": ts_code.split(".", 1)[0], "ts_code": ts_code, "source": "tushare", "rows": parsed}


def fetch_fina_indicator(code: str, *, periods: int = 40) -> dict[str, Any]:
    """Fetch per-report-period quality indicators (``fina_indicator``).

    Covers ROE, margins, growth rates, leverage, and cash-flow quality. The
    cash-flow-to-net-profit proxy uses ``ocf_to_profit`` (经营现金流/营业利润),
    matching the Eastmoney F10 ratio computed in the valuation loader.
    """
    ts_code = _ts_code(code)
    rows = _records(_pro_api().fina_indicator(ts_code=ts_code, limit=max(periods, 2)))
    parsed = [
        {
            "end_date": _dashed_date(row.get("end_date")),
            "roe": _to_float(row.get("roe")),
            "gross_margin": _to_float(row.get("grossprofit_margin")),
            "net_margin": _to_float(row.get("netprofit_margin")),
            "net_profit_yoy": _to_float(row.get("yoynet_profit")),
            "revenue_yoy": _to_float(row.get("yoy_or")),
            "debt_to_assets": _to_float(row.get("debt_to_assets")),
            "operating_cashflow_to_net_profit": _to_float(row.get("ocf_to_profit")),
            "eps": _to_float(row.get("eps")),
            "bps": _to_float(row.get("bps")),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("end_date") or "")
    return {"code": ts_code.split(".", 1)[0], "ts_code": ts_code, "source": "tushare", "rows": parsed}


def fetch_forecast(code: str, *, periods: int = 2) -> dict[str, Any]:
    """Fetch a stock's most recent earnings-forecast (业绩预告) announcements.

    Tushare ``forecast`` carries one row per report period, each with a Chinese
    ``type`` label (预增/略增/续盈/扭亏/略减/预减/首亏/续亏/减亏) plus the expected
    net-profit change range. Only the latest ``periods`` announcements are kept,
    newest announcement first, so the tracker can flag a fresh 预减/首亏/续亏.

    Raises:
        TushareFallbackUnavailable: No usable token / Tushare not importable.
    """
    ts_code = _ts_code(code)
    rows = _records(_pro_api().forecast(ts_code=ts_code, limit=max(periods, 2)))
    parsed = [
        {
            "ann_date": _dashed_date(row.get("ann_date")),
            "end_date": _dashed_date(row.get("end_date")),
            "type": row.get("type"),
            "p_change_min": _to_float(row.get("p_change_min")),
            "p_change_max": _to_float(row.get("p_change_max")),
            "net_profit_min": _to_float(row.get("net_profit_min")),
            "net_profit_max": _to_float(row.get("net_profit_max")),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("ann_date") or "", reverse=True)
    return {
        "code": ts_code.split(".", 1)[0],
        "ts_code": ts_code,
        "source": "tushare",
        "rows": parsed[:periods],
    }


def fetch_holder_trade(code: str, *, days: int = 120) -> dict[str, Any]:
    """Fetch a stock's recent shareholder increase/decrease (股东增减持) trades.

    Tushare ``stk_holdertrade`` carries per-holder trades with a direction
    (``in_de``: IN=增持 / DE=减持), the traded share count and its ratio to the
    company's current total shares (``change_ratio``, as a percentage number),
    the holder type and the announcement date.

    Raises:
        TushareFallbackUnavailable: No usable token / Tushare not importable.
    """
    ts_code = _ts_code(code)
    start_date, end_date = _date_window(days)
    rows = _records(
        _pro_api().stk_holdertrade(ts_code=ts_code, start_date=start_date, end_date=end_date)
    )
    parsed = [
        {
            "ann_date": _dashed_date(row.get("ann_date")),
            "holder_name": row.get("holder_name"),
            "holder_type": row.get("holder_type"),
            "in_de": row.get("in_de"),
            "change_vol": _to_float(row.get("change_vol")),
            "change_ratio": _to_float(row.get("change_ratio")),
            "avg_price": _to_float(row.get("avg_price")),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("ann_date") or "")
    return {
        "code": ts_code.split(".", 1)[0],
        "ts_code": ts_code,
        "source": "tushare",
        "rows": parsed,
    }


def fetch_limit_list(trade_date: str | None = None, *, lookback_days: int = 5) -> dict[str, Any]:
    """Fetch A-share daily limit-up/limit-down/broken-board pool (``limit_list_d``).

    ``limit_list_d`` returns one row per limit-up (U) / limit-down (D) / broken
    board (Z) stock with its consecutive-board count (``limit_times``) and
    open-times (``open_times``). Requires 5000+ Tushare points.

    Raises:
        TushareFallbackUnavailable: No usable token / Tushare not importable.
    """
    pro = _pro_api()
    if trade_date is not None:
        rows = _records(pro.limit_list_d(trade_date=_compact_date(trade_date)))
    else:
        start_date, end_date = _date_window(lookback_days)
        rows = _records(
            pro.limit_list_d(start_date=start_date, end_date=end_date)
        )
    parsed = [
        {
            "trade_date": _dashed_date(row.get("trade_date")),
            "ts_code": row.get("ts_code"),
            "name": row.get("name"),
            "industry": row.get("industry"),
            "close": _to_float(row.get("close")),
            "pct_chg": _to_float(row.get("pct_chg")),
            "limit_type": row.get("limit"),  # U/D/Z
            "open_times": _to_float(row.get("open_times")),
            "limit_times": _to_float(row.get("limit_times")),
            "up_stat": row.get("up_stat"),
            "fd_amount": _to_float(row.get("fd_amount")),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("trade_date") or "")
    return {"source": "tushare", "rows": parsed}


def fetch_limit_step(trade_date: str | None = None, *, lookback_days: int = 5) -> dict[str, Any]:
    """Fetch the daily consecutive-board ladder (``limit_step``, 连板天梯).

    One row per stock reaching ``nums`` consecutive limit-ups. Requires 8000+
    Tushare points.

    Raises:
        TushareFallbackUnavailable: No usable token / Tushare not importable.
    """
    pro = _pro_api()
    if trade_date is not None:
        rows = _records(pro.limit_step(trade_date=_compact_date(trade_date)))
    else:
        start_date, end_date = _date_window(lookback_days)
        rows = _records(pro.limit_step(start_date=start_date, end_date=end_date))
    parsed = [
        {
            "trade_date": _dashed_date(row.get("trade_date")),
            "ts_code": row.get("ts_code"),
            "name": row.get("name"),
            "nums": _to_float(row.get("nums")),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("trade_date") or "")
    return {"source": "tushare", "rows": parsed}


def fetch_report_rc(code: str, *, lookback_days: int = 400) -> dict[str, Any]:
    """Fetch sell-side earnings forecasts (``report_rc``) for one symbol.

    ``report_rc`` carries per-broker EPS/PE forecasts, a rating label, and
    target-price bounds (``max_price``/``min_price``). 120 points trial (10
    calls/day); 8000+ for full access.

    Raises:
        TushareFallbackUnavailable: No usable token / Tushare not importable.
    """
    ts_code = _ts_code(code)
    start_date, end_date = _date_window(lookback_days)
    rows = _records(
        _pro_api().report_rc(ts_code=ts_code, start_date=start_date, end_date=end_date)
    )
    parsed = [
        {
            "report_date": _dashed_date(row.get("report_date")),
            "org_name": row.get("org_name"),
            "author_name": row.get("author_name"),
            "quarter": row.get("quarter"),
            "rating": row.get("rating"),
            "eps": _to_float(row.get("eps")),
            "pe": _to_float(row.get("pe")),
            "roe": _to_float(row.get("roe")),
            "net_profit": _to_float(row.get("np")),
            "max_price": _to_float(row.get("max_price")),
            "min_price": _to_float(row.get("min_price")),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("report_date") or "", reverse=True)
    return {
        "code": ts_code.split(".", 1)[0],
        "ts_code": ts_code,
        "source": "tushare",
        "rows": parsed,
    }


def fetch_hk_hold(code: str, *, lookback_days: int = 400) -> dict[str, Any]:
    """Fetch northbound (Stock Connect) holding ratio (``hk_hold``) for one symbol.

    ``ratio`` is the held-share ratio (%), sourced from HKEX. Since 2024-08 the
    exchange stopped daily northbound disclosure and moved to quarterly, so this
    is naturally low-frequency. 120 points trial.

    Raises:
        TushareFallbackUnavailable: No usable token / Tushare not importable.
    """
    ts_code = _ts_code(code)
    start_date, end_date = _date_window(lookback_days)
    rows = _records(
        _pro_api().hk_hold(ts_code=ts_code, start_date=start_date, end_date=end_date)
    )
    parsed = [
        {
            "trade_date": _dashed_date(row.get("trade_date")),
            "vol": _to_float(row.get("vol")),
            "ratio": _to_float(row.get("ratio")),
            "exchange": row.get("exchange"),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("trade_date") or "")
    return {
        "code": ts_code.split(".", 1)[0],
        "ts_code": ts_code,
        "source": "tushare",
        "rows": parsed,
    }


def fetch_fund_portfolio(code: str, *, lookback_days: int = 400) -> dict[str, Any]:
    """Fetch mutual-fund holdings (``fund_portfolio``) for one stock, quarterly.

    ``stk_mkv_ratio`` is the held value as a share of the stock's market cap
    (%). 5000+ Tushare points.

    Raises:
        TushareFallbackUnavailable: No usable token / Tushare not importable.
    """
    ts_code = _ts_code(code)
    start_date, end_date = _date_window(lookback_days)
    rows = _records(
        _pro_api().fund_portfolio(symbol=ts_code, start_date=start_date, end_date=end_date)
    )
    parsed = [
        {
            "end_date": _dashed_date(row.get("end_date")),
            "ann_date": _dashed_date(row.get("ann_date")),
            "fund_code": row.get("ts_code"),
            "mkv": _to_float(row.get("mkv")),
            "amount": _to_float(row.get("amount")),
            "stk_mkv_ratio": _to_float(row.get("stk_mkv_ratio")),
            "stk_float_ratio": _to_float(row.get("stk_float_ratio")),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    parsed.sort(key=lambda item: item.get("end_date") or "")
    return {
        "code": ts_code.split(".", 1)[0],
        "ts_code": ts_code,
        "source": "tushare",
        "rows": parsed,
    }
