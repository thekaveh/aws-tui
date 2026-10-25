"""Private, short-lived EMR Serverless dashboard links."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from aws_tui.domain.filesystem import ValidationError

_DNS_LABEL = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", re.ASCII)
_INVALID_ESCAPE = re.compile(r"%(?![a-fA-F0-9]{2})")
_HTTPS_AUTHORITY = re.compile(r"https://([^/?#]+)", re.IGNORECASE | re.ASCII)


def _valid_dashboard_url(value: str) -> bool:
    if not 1 <= len(value) <= 2048:
        return False
    if any(
        c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs"} or c == "\\" for c in value
    ):
        return False
    if _INVALID_ESCAPE.search(value):
        return False
    # urllib.parse caches complete URL arguments globally. Extract only the
    # authority locally so signed path/query/fragment values are never retained.
    match = _HTTPS_AUTHORITY.match(value)
    if match is None:
        return False
    authority = match.group(1)
    hostname, separator, port = authority.partition(":")
    if separator and port != "443":
        return False
    if not hostname.isascii() or len(hostname) > 253:
        return False
    if not all(_DNS_LABEL.fullmatch(label) for label in hostname.split(".")):
        return False
    return hostname.lower().endswith((".amazonaws.com", ".amazonaws.com.cn"))


def validate_dashboard_url(value: object) -> str:
    """Return the exact URL after admission, without exposing it in failures."""
    if type(value) is str and _valid_dashboard_url(value):
        return value
    raise ValidationError("Invalid EMR Serverless dashboard URL") from None


@dataclass(frozen=True, slots=True)
class DashboardLink:
    """An ephemeral link whose URL must never enter diagnostics or snapshots."""

    url: str = field(repr=False)

    def __post_init__(self) -> None:
        validate_dashboard_url(self.url)
