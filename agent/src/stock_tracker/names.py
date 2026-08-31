"""Resolve A-share symbol names from Tencent's free quote API."""

from __future__ import annotations

import logging
import re
import ssl
import urllib.request
from typing import Dict, List, Optional

import certifi

logger = logging.getLogger(__name__)

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={query}"
# Response lines look like:
#   v_sh603228="1~景旺电子~603228~...";
# The second tilde-separated field is the Chinese name.
_QUOTE_LINE_RE = re.compile(r'v_(sh|sz|bj)(\d{6})="([^"]*)";')


def _to_tencent_code(code: str) -> Optional[str]:
    """Convert ``603228.SH`` to ``sh603228`` for the Tencent API."""
    code = code.strip().upper()
    if not code.endswith((".SH", ".SZ", ".BJ")):
        return None
    suffix = code[-2:].lower()
    return f"{suffix}{code[:-3]}"


def fetch_a_share_names(codes: List[str]) -> Dict[str, str]:
    """Return a mapping from normalized code to Chinese name.

    Missing or unresolvable codes are omitted. The function tolerates
    individual network failures and returns whatever it could resolve.
    """
    query_codes = []
    for code in codes:
        tencent_code = _to_tencent_code(code)
        if tencent_code is not None:
            query_codes.append(tencent_code)

    if not query_codes:
        return {}

    try:
        url = _TENCENT_QUOTE_URL.format(query=",".join(query_codes))
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://qt.gtimg.cn/",
            },
        )
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch A-share names from Tencent: %s", exc)
        return {}

    names: Dict[str, str] = {}
    for match in _QUOTE_LINE_RE.finditer(raw):
        suffix = match.group(1).upper()
        numeric = match.group(2)
        payload = match.group(3)
        normalized = f"{numeric}.{suffix}"
        parts = payload.split("~")
        if len(parts) >= 2 and parts[1].strip():
            names[normalized] = parts[1].strip()
    return names


__all__ = ["fetch_a_share_names"]
