"""Read-only tool: sell-side research reports + consensus EPS for A-shares.

Two free, no-auth disclosure feeds are stitched into one envelope:

* **Eastmoney reportapi** publishes the rolling list of broker research reports
  for a mainland A-share: report title, issuing brokerage, analyst, publish
  date, the broker's rating label, and that broker's per-year EPS / PE
  forecasts. This is the primary feed and drives the ``reports`` block.
* **Eastmoney datacenter** (``datacenter-web``) publishes a market *consensus*
  EPS forecast (the mean of analyst estimates) per forward fiscal year via the
  ``RPT_WEB_RESPREDICT`` report. The consensus feed is best-effort: a datacenter
  failure degrades the ``consensus_eps`` block to an empty list and never
  aborts the report fetch.

Both feeds cover mainland A-shares only (``.SH`` / ``.SZ`` / ``.BJ``); any other
market returns an error envelope. Every outbound GET goes through the project's
throttled clients so the tool never hits a host un-throttled and never
re-implements provider plumbing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from backtest.loaders.eastmoney_client import get_json, resolve_secid
from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)

# Eastmoney research-report list endpoint. qType=0 selects individual-stock
# reports; the response carries a ``data`` array of one row per report.
_REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list"

# Eastmoney datacenter consensus report. RPT_WEB_RESPREDICT serves one row per
# symbol carrying up to four fiscal years of mean analyst EPS (YEARn/EPSn), each
# marked actual (A) or estimate (E).
_EM_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_CONSENSUS_REPORT = "RPT_WEB_RESPREDICT"

# A-share exchange suffixes these disclosures cover.
_A_SHARE_SUFFIXES = ("SH", "SZ", "BJ")

# Hard caps so a long history cannot bloat the payload.
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


class ResearchReportsTool(BaseTool):
    """Fetch A-share sell-side research reports plus market consensus EPS."""

    name = "get_research_reports"
    description = (
        "Fetch mainland A-share sell-side research coverage: recent broker "
        "research reports (title, brokerage, analyst, publish date, rating) with "
        "each broker's per-year EPS and PE forecasts from Eastmoney, plus the "
        "market consensus (mean) EPS forecast per forward fiscal year from "
        "Eastmoney's datacenter. "
        "Markets: China A-shares only (.SH / .SZ / .BJ). "
        "Reports are filtered to the [beginTime, endTime] window (both optional, "
        "defaulting to the trailing two years). "
        'Example: {"code": "600519.SH", "limit": 10, '
        '"beginTime": "20240101", "endTime": "20261231"}.'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "A-share symbol in <code>.<exchange> form, exchange suffix one "
                    "of SH / SZ / BJ (e.g. '600519.SH', '000001.SZ', '830799.BJ')."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Maximum number of most-recent research reports to return "
                    f"(1-{_MAX_LIMIT}). Defaults to {_DEFAULT_LIMIT}."
                ),
                "default": _DEFAULT_LIMIT,
            },
            "beginTime": {
                "type": "string",
                "description": (
                    "Earliest report publish date (inclusive) to include, as "
                    "'YYYYMMDD' (e.g. '20240101'). Optional; defaults to the "
                    "beginning of the trailing two-year window. Must not be "
                    "later than 'endTime'."
                ),
            },
            "endTime": {
                "type": "string",
                "description": (
                    "Latest report publish date (inclusive) to include, as "
                    "'YYYYMMDD' (e.g. '20261231'). Optional; defaults to today."
                ),
            },
        },
        "required": ["code"],
    }

    def execute(self, **kwargs: Any) -> str:
        """Resolve the symbol, fetch reports + consensus, return a JSON envelope.

        Args:
            **kwargs: ``code`` (required A-share symbol); optional ``limit``
                (report count cap); optional ``beginTime`` / ``endTime`` as
                'YYYYMMDD' strings bounding the report publish-date window
                (defaults to the trailing two years).

        Returns:
            A JSON string envelope. On success:
            ``{"ok": true, "market": "CN", "source": "eastmoney",
            "data": {"code", "reports": [...], "consensus_eps": [...]}}``.
            On failure: ``{"ok": false, "error": str}``.
        """
        code = kwargs.get("code")
        if not isinstance(code, str) or not code.strip():
            return _error("'code' is required and must be a non-empty A-share symbol")
        code = code.strip().upper()

        suffix = code.rpartition(".")[2]
        if suffix not in _A_SHARE_SUFFIXES:
            return _error(
                f"research reports are China A-share only (.SH/.SZ/.BJ); got '{code}'"
            )
        if resolve_secid(code) is None:
            return _error(f"could not resolve A-share symbol '{code}'")

        limit = _clamp_limit(kwargs.get("limit", _DEFAULT_LIMIT))
        now = datetime.now()
        default_end = now
        default_begin = now - timedelta(days=730)
        begin_time = _parse_date_param(kwargs.get("beginTime"), default_begin)
        end_time = _parse_date_param(kwargs.get("endTime"), default_end)
        if begin_time is None or end_time is None:
            return _error(
                "'beginTime'/'endTime' must be valid 'YYYYMMDD' strings"
                + " (e.g. '20240101') when provided"
            )
        # A reversed window is answered by Eastmoney with HTTP 200 and zero hits,
        # which this tool would then report as "no research coverage" — a false
        # statement about the company rather than about the request.
        if begin_time > end_time:
            return _error(
                f"'beginTime' ({begin_time:%Y%m%d}) must not be later than 'endTime' "
                f"({end_time:%Y%m%d}); a reversed window returns zero reports and "
                "would be indistinguishable from genuinely missing coverage"
            )

        data = fetch_research_reports_data(
            code,
            limit=limit,
            begin_time=begin_time,
            end_time=end_time,
        )
        reports = data["reports"]
        consensus_eps = data["consensus_eps"]

        if not reports and not consensus_eps:
            return _error(f"no research coverage found for '{code}'")

        return json.dumps(
            {
                "ok": True,
                "market": "CN",
                "source": "eastmoney",
                "data": {
                    "code": code,
                    "reports": reports[:limit],
                    "consensus_eps": consensus_eps,
                },
            },
            ensure_ascii=False,
        )


def _bare_code(code: str) -> str:
    """Return the numeric stock code without its exchange suffix."""
    return code.rpartition(".")[0]


def _parse_date_param(value: Any, default: datetime) -> datetime | None:
    """Parse an optional 'YYYYMMDD' date param, falling back to ``default``.

    Args:
        value: A ``None`` (use ``default``) or a 'YYYYMMDD' string.
        default: The ``datetime`` to return when ``value`` is ``None``.

    Returns:
        A ``datetime`` for the given value, ``default`` when ``value`` is
        ``None``, or ``None`` when a provided value is not a valid 'YYYYMMDD'.
    """
    if value is None:
        return default
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y%m%d")
    except ValueError:
        return None


def _clamp_limit(value: Any) -> int:
    """Coerce a requested report count into the supported ``1.._MAX_LIMIT`` range."""
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_LIMIT
    return max(1, min(n, _MAX_LIMIT))


def fetch_research_reports_data(
    code: str,
    limit: int = _DEFAULT_LIMIT,
    begin_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Fetch sell-side reports + consensus EPS for one A-share as plain dicts.

    Code-level counterpart to :class:`ResearchReportsTool` for programmatic
    consumers (e.g. the stock-tracker consensus loader). Assumes a valid A-share
    symbol and returns ``{"reports": [...], "consensus_eps": [...]}`` (empty
    lists, never raises) on any failure. The Eastmoney report list is the
    primary feed; the Eastmoney datacenter consensus EPS is best-effort.

    Args:
        code: A-share symbol (e.g. ``"600519.SH"``).
        limit: Maximum number of most-recent reports to keep.
        begin_time: Optional lower bound on report publish date (defaults to the
            trailing two years).
        end_time: Optional upper bound on report publish date (defaults to now).

    Returns:
        ``{"reports": [...], "consensus_eps": [...]}``; both lists empty when
        the symbol is unresolvable or the request fails.
    """
    code = code.strip().upper()
    if code.rpartition(".")[2] not in _A_SHARE_SUFFIXES:
        return {"reports": [], "consensus_eps": []}
    if resolve_secid(code) is None:
        return {"reports": [], "consensus_eps": []}

    limit = _clamp_limit(limit)
    now = datetime.now()
    if end_time is None:
        end_time = now
    if begin_time is None:
        begin_time = now - timedelta(days=730)

    try:
        # Eastmoney's reportapi expects the bare numeric ``code`` together
        # with a mandatory [beginTime, endTime] window in %Y%m%d form;
        # omitting the window yields HTTP 400.
        payload = get_json(
            _REPORT_LIST_URL,
            params={
                "code": _bare_code(code),
                "beginTime": begin_time.strftime("%Y%m%d"),
                "endTime": end_time.strftime("%Y%m%d"),
                "qType": "0",
                "pageSize": str(limit),
                "pageNo": "1",
            },
        )
    except Exception as exc:  # noqa: BLE001 - degraded to empty lists
        logger.warning("东财研报列表抓取失败 %s: %s", code, exc)
        return {"reports": [], "consensus_eps": []}

    reports = _parse_reports(payload)
    consensus_eps = _fetch_consensus_eps(code)
    return {"reports": reports[:limit], "consensus_eps": consensus_eps}


def _parse_reports(payload: Any) -> list[dict]:
    """Extract per-report records from an Eastmoney reportapi payload.

    Args:
        payload: Decoded reportapi JSON; rows live under the ``data`` array.

    Returns:
        A list of normalized report dicts (newest first as served), empty when
        the payload carries no usable rows.
    """
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []

    reports: list[dict] = []
    for row in rows:
        record = _normalize_report(row)
        if record is not None:
            reports.append(record)
    return reports


def _normalize_report(row: Any) -> dict | None:
    """Map one raw reportapi row to our report record, or ``None`` if unusable.

    A row carrying neither a title nor a publish date holds no signal and is
    dropped; a single bad row never aborts the batch.

    Args:
        row: One element of the reportapi ``data`` array.

    Returns:
        ``{title, brokerage, analyst, publish_date, rating, eps_forecast,
        pe_forecast}`` or ``None``.
    """
    if not isinstance(row, dict):
        return None
    title = _clean_text(row.get("title"))
    publish_date = _clean_date(row.get("publishDate"))
    if title is None and publish_date is None:
        return None
    return {
        "title": title,
        "brokerage": _clean_text(row.get("orgSName")) or _clean_text(row.get("orgName")),
        "analyst": _clean_text(row.get("researcher")),
        "publish_date": publish_date,
        "rating": _clean_text(row.get("emRatingName")) or _clean_text(row.get("sRatingName")),
        "eps_forecast": {
            "this_year": _to_number(row.get("predictThisYearEps")),
            "next_year": _to_number(row.get("predictNextYearEps")),
        },
        "pe_forecast": {
            "this_year": _to_number(row.get("predictThisYearPe")),
            "next_year": _to_number(row.get("predictNextYearPe")),
        },
    }


def _fetch_consensus_eps(code: str) -> list[dict]:
    """Fetch Eastmoney consensus (mean) EPS forecast per forward fiscal year.

    Best-effort: any network/parse failure is logged and degraded to an empty
    list so the primary report fetch is never aborted by a datacenter outage.
    Reads the Eastmoney datacenter ``RPT_WEB_RESPREDICT`` report, which serves
    one row per symbol carrying up to four fiscal years of mean analyst EPS.

    Args:
        code: A-share symbol such as ``"600519.SH"``.

    Returns:
        A list of ``{fiscal_year, consensus_eps}`` dicts for the estimate
        (forward) years, empty when Eastmoney returns nothing usable or the
        request fails.
    """
    try:
        payload = get_json(
            _EM_DATACENTER_URL,
            params={
                "reportName": _EM_CONSENSUS_REPORT,
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{_bare_code(code)}")',
                "pageNumber": "1",
                "pageSize": "1",
                "source": "WEB",
                "client": "WEB",
            },
        )
    except Exception as exc:  # noqa: BLE001 - consensus is best-effort
        logger.warning("东财一致预期 EPS 抓取失败 %s: %s", code, exc)
        return []
    return _parse_consensus_eps(payload)


def _parse_consensus_eps(payload: Any) -> list[dict]:
    """Extract per-year consensus EPS rows from an Eastmoney datacenter payload.

    The ``RPT_WEB_RESPREDICT`` payload wraps its single per-symbol row under
    ``result.data``. Each row carries up to four ``YEARn`` / ``EPSn`` pairs with
    a matching ``YEAR_MARKn`` (``A`` = reported actual, ``E`` = analyst
    estimate); only the estimate years are forward-looking consensus.

    Args:
        payload: Decoded Eastmoney datacenter JSON.

    Returns:
        A list of ``{fiscal_year, consensus_eps}`` dicts, empty when no usable
        row is present.
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list) or not data:
        return []
    row = data[0]
    if not isinstance(row, dict):
        return []

    out: list[dict] = []
    for index in range(1, 5):
        year_raw = row.get(f"YEAR{index}")
        eps = _to_number(row.get(f"EPS{index}"))
        mark = row.get(f"YEAR_MARK{index}")
        if year_raw is None or eps is None:
            continue
        if not isinstance(mark, str) or mark.strip().upper() != "E":
            continue
        out.append({"fiscal_year": str(year_raw).strip(), "consensus_eps": eps})
    return out


def _clean_text(value: Any) -> str | None:
    """Trim a string cell, or ``None`` when absent/blank/non-string."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _clean_date(value: Any) -> str | None:
    """Trim a timestamp cell to its ``YYYY-MM-DD`` date, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().split(" ", 1)[0]


def _to_number(value: Any) -> float | None:
    """Coerce a cell to ``float``, or ``None`` when absent/non-numeric."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _error(message: str) -> str:
    """Render a failure envelope as a JSON string."""
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
