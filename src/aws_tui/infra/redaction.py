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
    r"(?<![A-Za-z0-9_.-])(?P<key_quote>[\"']?)(?P<key>[A-Za-z0-9_.-]*"
    r"(?:authorization|secret|password|token|credential|access[_-]?key|api[_-]?key|"
    r"private[_-]?key|signature)[A-Za-z0-9_.-]*)(?P=key_quote)"
    # Whitespace alone also separates a key from its value: botocore's
    # CredentialRetrievalError renders credential-process output as
    # ``aws_secret_access_key wJalr...`` with no delimiter, and that reaches
    # the rotating log and crash dumps. Horizontal-only, so a key at the end of
    # one line cannot swallow the next line's content as its value.
    r"(?P<separator>[^\S\r\n]*[:=][^\S\r\n]*|[^\S\r\n]+)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}]+)",
    re.IGNORECASE,
)
# Handles BOTH the schemed (``Authorization: Bearer <cred>``) and schemeless
# (``Authorization: <opaque>``) forms, and accepts ``=`` as well as ``:``.
# Horizontal-only whitespace matters: ``\s`` would span a newline, letting the
# value be read as a scheme and the NEXT header's key be eaten as the
# credential -- which leaked both secrets.
#
# The scheme is a CLOSED list rather than "any word". On one line,
# ``Authorization: OPAQUE next_token: abc`` is genuinely ambiguous, and treating
# an arbitrary leading word as a scheme resolves it the unsafe way: it preserves
# the credential and redacts the following field name instead. An unrecognized
# token is therefore treated as the credential.
_AUTHORIZATION_HEADER = re.compile(
    r"(?<![A-Za-z0-9_.-])(Authorization)([^\S\r\n]*[:=][^\S\r\n]*)"
    r"(?:(Bearer|Basic|Digest|Negotiate|NTLM|Hawk|Mutual|OAuth|AWS|AWS4-HMAC-SHA256)"
    r"([^\S\r\n]+))?"
    r"([^\s,;}]+)",
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


def _redact_authorization_header(match: re.Match[str]) -> str:
    key, separator, scheme, scheme_gap, _value = match.groups()
    prefix = f"{scheme}{scheme_gap}" if scheme else ""
    return f"{key}{separator}{prefix}{_REDACTED}"


def _redact_key_value(match: re.Match[str]) -> str:
    key_quote = match.group("key_quote")
    value = match.group("value")
    if (
        match.group("key").casefold() == "authorization"
        and not key_quote
        and not value.startswith(("'", '"'))
    ):
        # ``_AUTHORIZATION_HEADER`` has already handled every unquoted
        # ``Authorization`` value -- schemed, schemeless, ``:`` and ``=`` alike
        # -- so ``value`` here is either a preserved scheme token or the
        # redaction itself. Exempting is safe BECAUSE that pass is exhaustive;
        # do not weaken it without widening this exemption's guard. Quoted
        # forms and keys that merely contain "authorization" (``x-authorization``)
        # still fall through and are redacted below.
        return match.group(0)
    return f"{key_quote}{match.group('key')}{key_quote}{match.group('separator')}{_REDACTED}"


def redact_text(text: str) -> str:
    text = _URL.sub(lambda match: _redact_url(match.group(0)), text)
    text = _AUTHORIZATION_HEADER.sub(_redact_authorization_header, text)
    return _KEY_VALUE.sub(_redact_key_value, text)


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
    fragment = _REDACTED if parts.fragment else ""
    return urlunsplit(SplitResult(parts.scheme, netloc, parts.path, query, fragment))


__all__ = [
    "is_sensitive_key",
    "redact_mapping",
    "redact_text",
    "redact_value",
    "safe_endpoint_display",
]
