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
_AUTHORIZATION_SCHEMES = (
    r"Bearer|Basic|Digest|Negotiate|NTLM|Hawk|Mutual|OAuth|Token|AWS4-HMAC-SHA256|AWS"
)
_CREDENTIAL_SCHEMES = frozenset(
    scheme.casefold()
    for scheme in (
        "Bearer",
        "Basic",
        "Digest",
        "Negotiate",
        "NTLM",
        "Hawk",
        "Mutual",
        "OAuth",
        "Token",
        "AWS4-HMAC-SHA256",
        "AWS",
    )
)
# Without a delimiter, the header is matched only when a known scheme follows,
# so prose such as "Authorization header missing" is left alone.
_AUTHORIZATION_HEADER = re.compile(
    r"(?<![A-Za-z0-9_.-])(Authorization)"
    rf"([^\S\r\n]*[:=][^\S\r\n]*|[^\S\r\n]+(?=(?:{_AUTHORIZATION_SCHEMES})[^\S\r\n]))"
    rf"(?:({_AUTHORIZATION_SCHEMES})"
    r"([^\S\r\n]+))?"
    r"([^\s,;}]+)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


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


def _is_key_name(token: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_.-]+", token) is not None and is_sensitive_key(token)


_NEXT_TOKEN = re.compile(r"[^\S\r\n]+(?P<token>[^\s,;}]+)")


def _redact_key_values(text: str) -> str:
    """Redact each sensitive key's value, scanning left to right.

    A value that is itself a sensitive key name is ambiguous. It may be a
    credential that contains a keyword (``password Password123``), or the next
    key of an adjacent pair (``password aws_secret_access_key wJalr...``). Only
    redacting the name would leave the other reading exposed, so the name and
    the token after it are both redacted, following the rule that an
    unrecognized token is the credential. The exception is a token that starts
    its own key/value pair: it is scanned as a key, so its value is redacted.
    """
    pieces: list[str] = []
    pos = 0
    while (match := _KEY_VALUE.search(text, pos)) is not None:
        value = match.group("value")
        value_start, value_end = match.span("value")
        following = None if value.startswith(("'", '"')) else _NEXT_TOKEN.match(text, value_end)
        # A value followed by an already-redacted token was handled by an earlier
        # pass (the Authorization header), so it is left as it is.
        already_redacted = following is not None and following.group("token") == _REDACTED
        scheme_credential = following is not None and value.casefold() in _CREDENTIAL_SCHEMES
        if (
            not value.startswith(("'", '"'))
            and not already_redacted
            and (_is_key_name(value.rstrip(":=")) or scheme_credential)
        ):
            pieces.append(text[pos:value_start])
            pair = (
                _KEY_VALUE.search(text, following.start("token")) if following is not None else None
            )
            if following is None:
                pieces.append(_REDACTED)
                pos = value_end
            elif pair is not None and pair.start() < following.end("token"):
                # A key/value pair starts inside the next token, for example
                # ``payload={'secret_access_key':``. Scan on so it is redacted.
                pieces.append(_REDACTED)
                pos = value_end
            else:
                gap = text[value_end : following.start("token")]
                pieces.append(f"{_REDACTED}{gap}{_REDACTED}")
                pos = following.end("token")
            continue
        pieces.append(text[pos : match.start()])
        pieces.append(_redact_key_value(match))
        pos = match.end()
    pieces.append(text[pos:])
    return "".join(pieces)


def redact_text(text: str) -> str:
    text = _URL.sub(lambda match: _redact_url(match.group(0)), text)
    text = _AUTHORIZATION_HEADER.sub(_redact_authorization_header, text)
    return _redact_key_values(text)


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
        # Failed parsing cannot establish safe URL component boundaries.
        # Keep harmless malformed addresses useful for diagnosis.
        return _REDACTED if any(marker in raw for marker in ("@", "?", "#")) else raw
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
