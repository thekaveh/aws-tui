"""Shared validation helpers for persisted and interactive connections."""

from __future__ import annotations

from urllib.parse import urlsplit


def validate_endpoint_url(value: str) -> str | None:
    """Return an endpoint validation error, or ``None`` when valid."""
    stripped = value.strip()
    if not stripped:
        return "is required"
    try:
        parsed = urlsplit(stripped)
        port = parsed.port
        host = parsed.hostname
    except ValueError:
        return "is not a valid URL"
    if parsed.scheme not in {"http", "https"}:
        return "must start with http:// or https://"
    if not parsed.netloc or not host:
        return "is missing a host"
    if any(character.isspace() for character in host):
        return "contains an invalid host"
    if port is not None and not 1 <= port <= 65535:
        return "contains an invalid port"
    if parsed.username is not None or parsed.password is not None:
        return "must not include username or password"
    if parsed.query or parsed.fragment:
        return "must not include query or fragment"
    return None


__all__ = ["validate_endpoint_url"]
