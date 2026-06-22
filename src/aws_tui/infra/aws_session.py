"""AWS session factory + SSO cache token probe.

This module is the only place in the infra layer that touches ``aioboto3``
and ``botocore``. It serves two roles:

1. ``probe_token`` performs a cheap, fully-offline freshness check on the
   SSO token cache so the UI can render a connection's auth status
   without doing a network call.
2. ``client`` returns an async context manager for an aioboto3 client,
   pre-configured with retries, timeouts, and the right credentials.

``AwsSession`` does NOT initiate sign-in; that's a shell-out
(``aws sso login --profile <name>``) orchestrated by callers higher up
the stack. We only observe.
"""

from __future__ import annotations

import configparser
import contextlib
import hashlib
import json
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import aioboto3
from botocore.config import Config as BotoConfig

from aws_tui.infra.connection_resolver import Connection
from aws_tui.version import __version__

_logger = logging.getLogger("aws_tui.infra.aws_session")

#: Seconds of skew tolerated before a token is treated as expired.
_SKEW_BUFFER: Final[timedelta] = timedelta(seconds=60)


class TokenState(StrEnum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class TokenProbeResult:
    state: TokenState
    expires_at: datetime | None = None


class AuthExpiredError(Exception):
    """Raised by callers higher up the stack when a 401-equivalent fires."""


class TokenLoadError(Exception):
    """Raised when the SSO cache file is present but unparseable."""


def _default_sso_cache_dir() -> Path:
    return Path.home() / ".aws" / "sso" / "cache"


def _default_aws_config_path() -> Path:
    return Path.home() / ".aws" / "config"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


class AwsSession:
    """boto3 + aioboto3 session factory. Owns aioboto3 client lifecycle."""

    def __init__(
        self,
        *,
        sso_cache_dir: Path | None = None,
        aws_config_path: Path | None = None,
    ) -> None:
        self._sso_cache_dir: Path = (
            sso_cache_dir if sso_cache_dir is not None else _default_sso_cache_dir()
        )
        self._aws_config_path: Path = (
            aws_config_path if aws_config_path is not None else _default_aws_config_path()
        )
        # Open client context managers we still need to __aexit__ on shutdown.
        self._open_clients: list[AbstractAsyncContextManager[Any]] = []

    # ------------------------------------------------------------------
    # Token probe
    # ------------------------------------------------------------------

    def probe_token(self, connection: Connection) -> TokenProbeResult:
        """Cheap freshness check on the local SSO cache. No network calls.

        For ``s3-compatible`` connections (no SSO involved) the result is
        purely a function of whether ``access_key_id`` and
        ``secret_access_key`` are populated on the connection.
        """
        if connection.kind == "s3-compatible":
            if connection.access_key_id and connection.secret_access_key:
                return TokenProbeResult(state=TokenState.CONNECTED)
            return TokenProbeResult(state=TokenState.MISSING)

        if connection.kind != "aws":
            return TokenProbeResult(state=TokenState.MISSING)

        cache_key = self._sso_cache_key_for_profile(connection.profile)
        if cache_key is None:
            return TokenProbeResult(state=TokenState.MISSING)

        cache_file = self._sso_cache_dir / f"{cache_key}.json"
        if not cache_file.is_file():
            return TokenProbeResult(state=TokenState.MISSING)

        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TokenLoadError(f"could not read SSO token cache at {cache_file}") from exc

        expires_raw = payload.get("expiresAt")
        if not isinstance(expires_raw, str):
            return TokenProbeResult(state=TokenState.MISSING)

        try:
            expires_at = _parse_iso8601(expires_raw)
        except ValueError as exc:
            raise TokenLoadError(f"unparseable expiresAt in {cache_file}") from exc

        now = datetime.now(UTC)
        if expires_at - _SKEW_BUFFER <= now:
            return TokenProbeResult(state=TokenState.EXPIRED, expires_at=expires_at)
        return TokenProbeResult(state=TokenState.CONNECTED, expires_at=expires_at)

    def _sso_cache_key_for_profile(self, profile: str | None) -> str | None:
        """Resolve the SSO cache filename stem for an AWS profile.

        Looks at ``~/.aws/config`` for either ``sso_session = <name>`` (in
        which case the key is ``sha1(name)``) or ``sso_start_url = <url>``
        (in which case the key is ``sha1(url)``). Returns ``None`` if the
        profile has no SSO configuration or the config file is absent.
        """
        if profile is None:
            return None
        if not self._aws_config_path.is_file():
            return None

        parser = configparser.ConfigParser()
        parser.read(self._aws_config_path, encoding="utf-8")

        section: str | None = None
        if parser.has_section(f"profile {profile}"):
            section = f"profile {profile}"
        elif profile == "default" and parser.has_section("default"):
            section = "default"
        if section is None:
            return None

        sso_session = parser.get(section, "sso_session", fallback=None)
        if sso_session:
            return _sha1(sso_session)
        start_url = parser.get(section, "sso_start_url", fallback=None)
        if start_url:
            return _sha1(start_url)
        return None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    async def client(
        self, connection: Connection, service: str
    ) -> AbstractAsyncContextManager[Any]:
        """Return an aioboto3 client context manager for ``service``.

        Each opened client is tracked so :meth:`aclose_all_clients` can
        ``__aexit__`` them all on app shutdown.
        """
        boto_config = BotoConfig(
            s3={"addressing_style": "path" if connection.force_path_style else "auto"},
            retries={"max_attempts": 6, "mode": "adaptive"},
            connect_timeout=10,
            read_timeout=60,
            user_agent_extra=f"aws-tui/{__version__}",
        )

        if connection.kind == "aws":
            session = aioboto3.Session(
                profile_name=connection.profile,
                region_name=connection.region,
            )
            client_cm = session.client(service, config=boto_config)
        elif connection.kind == "s3-compatible":
            session = aioboto3.Session(
                aws_access_key_id=connection.access_key_id,
                aws_secret_access_key=connection.secret_access_key,
                region_name=connection.region,
            )
            client_cm = session.client(
                service,
                endpoint_url=connection.endpoint_url,
                verify=connection.verify_tls,
                config=boto_config,
            )
        else:
            raise ValueError(f"unsupported connection kind: {connection.kind!r}")

        tracked = _TrackedClientCM(client_cm, self._open_clients)
        self._open_clients.append(tracked)
        return tracked

    async def aclose_all_clients(self) -> None:
        """Exit every still-open client context manager. Safe to call twice."""
        # Copy and clear so re-entry / parallel closes don't double-exit.
        pending = list(self._open_clients)
        self._open_clients.clear()
        for cm in pending:
            try:
                await cm.__aexit__(None, None, None)
            except Exception as exc:
                # Shutdown is best-effort by design (the user has already
                # asked us to quit), but a silent suppress hid genuine
                # leaks. Log so crash-dump triage has a signal.
                _logger.warning(
                    "aws_session.aclose_failed",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                )


def _parse_iso8601(value: str) -> datetime:
    """Parse the SSO cache's ISO-8601 timestamp.

    Botocore writes ``YYYY-MM-DDTHH:MM:SSZ``; ``datetime.fromisoformat`` in
    3.11+ accepts the trailing ``Z`` but earlier versions don't. We
    normalize to be safe.
    """
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class _TrackedClientCM(AbstractAsyncContextManager[Any]):
    """Wraps an aioboto3 client CM so we can de-register it on exit."""

    def __init__(
        self,
        inner: AbstractAsyncContextManager[Any],
        registry: list[AbstractAsyncContextManager[Any]],
    ) -> None:
        self._inner = inner
        self._registry = registry
        self._client: Any = None

    async def __aenter__(self) -> Any:
        self._client = await self._inner.__aenter__()
        return self._client

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        # Best-effort: remove ourselves so aclose_all_clients doesn't
        # double-exit if the caller manages lifecycle directly.
        with contextlib.suppress(ValueError):
            self._registry.remove(self)
        return await self._inner.__aexit__(exc_type, exc, tb)


__all__ = [
    "AuthExpiredError",
    "AwsSession",
    "TokenLoadError",
    "TokenProbeResult",
    "TokenState",
]
