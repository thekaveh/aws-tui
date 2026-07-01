"""Small redaction helpers for durable logs and crash dumps."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(authorization|secret|password|token|credential|access[_-]?key|api[_-]?key|private[_-]?key|signature)",
    re.IGNORECASE,
)
_KEY_VALUE = re.compile(
    r'(?<![A-Za-z0-9_.-])("?)([A-Za-z0-9_.-]*'
    r"(?:secret|password|token|credential|access[_-]?key|api[_-]?key|private[_-]?key|signature)"
    r'[A-Za-z0-9_.-]*)\1(\s*[:=]\s*)("[^"]*"|[^\s,;}]+)',
    re.IGNORECASE,
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?<![A-Za-z0-9_.-])(Authorization)(\s*:\s*)([A-Za-z][A-Za-z0-9_.+-]*\s+)([^\s,;}]+)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s\"'<>]+")


def is_sensitive_key(key: str) -> bool:
    return _SENSITIVE_KEY.search(key) is not None


def redact_value(value: object, *, key: str | None = None) -> object:
    """Redact secret-like fields and URL credentials/query strings."""
    if key is not None and is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(k): redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(redact_value(v) for v in value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact_value(v) for v in value]
    return value


def redact_mapping(fields: Mapping[str, Any]) -> dict[str, object]:
    return {str(key): redact_value(value, key=str(key)) for key, value in fields.items()}


def redact_text(text: str) -> str:
    text = _URL.sub(lambda match: _redact_url(match.group(0)), text)
    text = _AUTHORIZATION_HEADER.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}{_REDACTED}",
        text,
    )
    return _KEY_VALUE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(1)}{match.group(3)}{_REDACTED}"
        ),
        text,
    )


def safe_endpoint_display(url: str | None) -> str | None:
    """Return a user-visible endpoint label without URL credentials,
    query strings, or fragments.

    The actual configured endpoint remains untouched for boto. This
    helper is only for UI/repr surfaces such as pane titles and Settings
    rows, where signed URLs or userinfo would otherwise leak into
    screenshots and crash triage.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return redact_text(url)

    host = parts.hostname
    if not host:
        return redact_text(url)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return f"{netloc}{parts.path}"


def _redact_url(raw: str) -> str:
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    netloc = parts.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = f"{_REDACTED}@{host}"
    query = _REDACTED if parts.query else ""
    return urlunsplit(SplitResult(parts.scheme, netloc, parts.path, query, parts.fragment))


__all__ = [
    "is_sensitive_key",
    "redact_mapping",
    "redact_text",
    "redact_value",
    "safe_endpoint_display",
]
