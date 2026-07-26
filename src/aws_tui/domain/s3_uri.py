"""Structured, non-throwing validation for S3 locations."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_IPV4_SHAPED = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}\Z")
_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_RESERVED_PREFIXES = ("xn--", "sthree-", "amzn-s3-demo-")
_RESERVED_SUFFIXES = ("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3")


@dataclass(frozen=True, slots=True)
class S3Uri:
    """Validated S3 URI parts whose repr does not expose the location."""

    bucket: str = field(repr=False)
    path: str = field(repr=False)


def parse_s3_uri(uri: str | None) -> S3Uri | None:
    """Return validated S3 URI parts, or ``None`` for any malformed input."""
    if not isinstance(uri, str) or not uri or _contains_unsafe_char(uri):
        return None
    if _BAD_PERCENT_ESCAPE.search(uri) or "?" in uri or "#" in uri:
        return None
    try:
        decoded = unquote(uri, errors="strict")
        parsed = urlsplit(uri)
    except (UnicodeDecodeError, ValueError):
        return None
    bucket = parsed.netloc
    if (
        parsed.scheme.casefold() != "s3"
        or parsed.query
        or parsed.fragment
        or _contains_unsafe_char(decoded)
        or not _is_general_purpose_bucket(bucket)
    ):
        return None
    return S3Uri(bucket, parsed.path)


def _contains_unsafe_char(value: str) -> bool:
    return any(char.isspace() or unicodedata.category(char) == "Cc" for char in value)


def _is_general_purpose_bucket(bucket: str) -> bool:
    return (
        _BUCKET_NAME.fullmatch(bucket) is not None
        and ".." not in bucket
        and _IPV4_SHAPED.fullmatch(bucket) is None
        and not bucket.startswith(_RESERVED_PREFIXES)
        and not bucket.endswith(_RESERVED_SUFFIXES)
    )


__all__ = ["S3Uri", "parse_s3_uri"]
