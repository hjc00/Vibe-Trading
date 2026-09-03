"""Tests for get_research_reports: success + error envelopes, HTTP mocked.

Both the report list and the consensus EPS come from Eastmoney now: reportapi
drives ``get_json`` for the reports block, and the datacenter consensus report
(``RPT_WEB_RESPREDICT``) is read through the same ``get_json``. Tests mock
``get_json`` with a two-call side effect (report payload first, consensus
payload second), so no test reaches a live endpoint.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

from src.tools import research_reports_tool as rrt
from src.tools.research_reports_tool import ResearchReportsTool

_REPORT_PAYLOAD = {
    "data": [
        {
            "title": "Q1 beat, raise target",
            "orgSName": "Broker A",
            "researcher": "Analyst One",
            "publishDate": "2024-04-30 08:00:00",
            "emRatingName": "Buy",
            "predictThisYearEps": "12.34",
            "predictNextYearEps": "15.00",
            "predictThisYearPe": "20.1",
            "predictNextYearPe": "16.5",
        },
        {
            "title": "Margins stable",
            "orgSName": "Broker B",
            "researcher": "Analyst Two",
            "publishDate": "2024-03-15 09:30:00",
            "emRatingName": "Hold",
            "predictThisYearEps": "11.80",
            "predictNextYearEps": "13.20",
            "predictThisYearPe": "21.0",
            "predictNextYearPe": "18.8",
        },
    ]
}

# One RPT_WEB_RESPREDICT row: actual (A) for 2024, estimates (E) for 2025/2026.
_CONSENSUS_PAYLOAD = {
    "result": {
        "data": [
            {
                "SECURITY_CODE": "600519",
                "YEAR1": 2024,
                "YEAR_MARK1": "A",
                "EPS1": 12.1,
                "YEAR2": 2025,
                "YEAR_MARK2": "E",
                "EPS2": 14.5,
                "YEAR3": 2026,
                "YEAR_MARK3": "E",
                "EPS3": 16.9,
            }
        ]
    }
}

_CONSENSUS_ROWS = [
    {"fiscal_year": "2025", "consensus_eps": 14.5},
    {"fiscal_year": "2026", "consensus_eps": 16.9},
]


class _FrozenDatetime(datetime):
    """``datetime`` with a pinned ``now()`` so default-window tests are exact.

    Subclassing keeps ``strptime`` intact, which ``_parse_date_param`` needs.
    """

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - see class docstring
        return datetime(2026, 8, 13, 12, 0, 0)


def _patch_payloads(*payloads):
    """Return a context manager patching ``get_json`` with a two-call side effect."""
    return patch.object(rrt, "get_json", side_effect=list(payloads))


def test_success_envelope_merges_reports_and_consensus():
    with _patch_payloads(_REPORT_PAYLOAD, _CONSENSUS_PAYLOAD) as mock_get_json:
        out = ResearchReportsTool().execute(code="600519.SH", limit=10)

    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["market"] == "CN"
    assert payload["source"] == "eastmoney"
    assert payload["data"]["code"] == "600519.SH"

    reports = payload["data"]["reports"]
    assert len(reports) == 2
    assert reports[0] == {
        "title": "Q1 beat, raise target",
        "brokerage": "Broker A",
        "analyst": "Analyst One",
        "publish_date": "2024-04-30",
        "rating": "Buy",
        "eps_forecast": {"this_year": 12.34, "next_year": 15.0},
        "pe_forecast": {"this_year": 20.1, "next_year": 16.5},
    }

    assert payload["data"]["consensus_eps"] == _CONSENSUS_ROWS

    # Report call first, consensus datacenter call second; both on Eastmoney.
    calls = mock_get_json.call_args_list
    report_params = calls[0].kwargs["params"]
    consensus_params = calls[1].kwargs["params"]
    assert report_params["code"] == "600519"
    assert consensus_params["reportName"] == "RPT_WEB_RESPREDICT"
    assert consensus_params["filter"] == '(SECURITY_CODE="600519")'
    assert consensus_params["pageSize"] == "1"


def test_limit_caps_returned_reports():
    with _patch_payloads(_REPORT_PAYLOAD, _CONSENSUS_PAYLOAD):
        out = ResearchReportsTool().execute(code="600519.SH", limit=1)
    payload = json.loads(out)
    assert len(payload["data"]["reports"]) == 1


def test_consensus_failure_degrades_consensus_but_keeps_reports():
    with _patch_payloads(_REPORT_PAYLOAD, RuntimeError("eastmoney datacenter 503")):
        out = ResearchReportsTool().execute(code="600519.SH")
    payload = json.loads(out)
    assert payload["ok"] is True
    assert len(payload["data"]["reports"]) == 2
    assert payload["data"]["consensus_eps"] == []


def test_consensus_bad_payload_degrades_consensus():
    with _patch_payloads(_REPORT_PAYLOAD, {"result": {}}):
        out = ResearchReportsTool().execute(code="600519.SH")
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["data"]["consensus_eps"] == []


def test_consensus_actual_years_are_not_reported_as_forecasts():
    # Only mark-E (estimate) years are forward-looking consensus; the reported
    # actual (mark A) year must not be served as a consensus forecast.
    payload = {
        "result": {
            "data": [
                {
                    "YEAR1": 2024,
                    "YEAR_MARK1": "A",
                    "EPS1": 12.1,
                    "YEAR2": 2025,
                    "YEAR_MARK2": "E",
                    "EPS2": 14.5,
                }
            ]
        }
    }
    with _patch_payloads(_REPORT_PAYLOAD, payload):
        out = ResearchReportsTool().execute(code="600519.SH")
    payload = json.loads(out)
    assert payload["data"]["consensus_eps"] == [
        {"fiscal_year": "2025", "consensus_eps": 14.5}
    ]


def test_non_a_share_returns_error_without_http():
    with patch.object(rrt, "get_json") as mock_get_json:
        out = ResearchReportsTool().execute(code="AAPL.US")
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "A-share" in payload["error"]
    mock_get_json.assert_not_called()


def test_missing_code_returns_error_envelope():
    out = ResearchReportsTool().execute()
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "required" in payload["error"]


def test_report_request_failure_is_caught_as_error_envelope():
    # The report-list fetch owns the try/except: an outage is degraded to empty
    # lists, so the tool answers an error envelope rather than raising.
    with patch.object(rrt, "get_json", side_effect=RuntimeError("HTTP 429")):
        out = ResearchReportsTool().execute(code="600519.SH")
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "no research coverage" in payload["error"]


def test_empty_coverage_returns_error_envelope():
    with _patch_payloads({"data": []}, {"result": {"data": []}}):
        out = ResearchReportsTool().execute(code="600519.SH")
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "no research coverage" in payload["error"]


def test_default_window_is_the_trailing_two_years():
    # Eastmoney rejects a request with no window (HTTP 400), so both bounds must
    # always be sent even when the caller supplies neither.
    with patch.object(rrt, "datetime", _FrozenDatetime), _patch_payloads(
        _REPORT_PAYLOAD, _CONSENSUS_PAYLOAD
    ) as mock_get_json:
        out = ResearchReportsTool().execute(code="600519.SH")

    assert json.loads(out)["ok"] is True
    params = mock_get_json.call_args_list[0].kwargs["params"]
    assert params["beginTime"] == "20240813"
    assert params["endTime"] == "20260813"


def test_explicit_window_is_forwarded_verbatim():
    with _patch_payloads(_REPORT_PAYLOAD, _CONSENSUS_PAYLOAD) as mock_get_json:
        out = ResearchReportsTool().execute(
            code="600519.SH", beginTime="20240101", endTime="20261231"
        )

    assert json.loads(out)["ok"] is True
    params = mock_get_json.call_args_list[0].kwargs["params"]
    assert params["beginTime"] == "20240101"
    assert params["endTime"] == "20261231"


def test_malformed_date_returns_error_without_http():
    with patch.object(rrt, "get_json") as mock_get_json:
        out = ResearchReportsTool().execute(code="600519.SH", beginTime="2024-01-01")
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "YYYYMMDD" in payload["error"]
    mock_get_json.assert_not_called()


def test_reversed_window_is_rejected_not_reported_as_missing_coverage():
    # Eastmoney answers a reversed window with HTTP 200 and zero hits. Without
    # this guard the empty result becomes "no research coverage found", which is
    # a false claim about the company instead of an error about the request.
    with patch.object(rrt, "get_json") as mock_get_json:
        out = ResearchReportsTool().execute(
            code="600519.SH", beginTime="20261231", endTime="20240101"
        )
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "must not be later than" in payload["error"]
    assert "no research coverage" not in payload["error"]
    mock_get_json.assert_not_called()
