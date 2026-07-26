"""Structured, non-throwing validation for S3 locations."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class S3Uri:
    """Validated S3 URI parts whose repr does not expose the location."""

    bucket: str = field(repr=False)
    path: str = field(repr=False)


def parse_s3_uri(uri: str | None) -> S3Uri | None:
    """Return validated S3 URI parts, or ``None`` for any malformed input."""
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "s3"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return S3Uri(parsed.netloc, parsed.path)


__all__ = ["S3Uri", "parse_s3_uri"]
