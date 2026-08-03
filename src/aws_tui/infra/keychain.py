"""Keychain abstraction for secret storage.

The :class:`KeychainBackend` protocol is what the rest of the infra layer
depends on. Two concrete implementations:

* :class:`Keyring` — thin adapter over the ``keyring`` library (macOS
  Keychain on darwin, gnome-keyring / kwallet on Linux).
* :class:`InMemoryKeychain` — a dict-backed fake used by tests and by
  callers that want an explicit no-touch backend (e.g. dry-runs).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from urllib.parse import quote

import keyring as _keyring_lib


def app_keychain_service(connection_name: str) -> str:
    """Return the collision-free primary service for a connection."""
    return f"aws-tui:connections/{quote(connection_name, safe='')}"


def app_keychain_revision_service(connection_name: str, slot: int) -> str:
    """Return one of the two bounded credential staging services."""
    if slot not in {0, 1}:
        raise ValueError("keychain revision slot must be 0 or 1")
    return f"aws-tui:connection-revisions/{quote(connection_name, safe='')}/{slot}"


@runtime_checkable
class KeychainBackend(Protocol):
    """Tiny CRUD protocol for secret storage.

    All methods are synchronous. Real Keychain access is fast (~1 ms) on
    macOS so async would be overkill here.
    """

    def get(self, service: str, key: str) -> str | None: ...

    def set(self, service: str, key: str, value: str) -> None: ...

    def delete(self, service: str, key: str) -> None: ...


class Keyring:
    """Thin wrapper around the ``keyring`` library."""

    def get(self, service: str, key: str) -> str | None:
        return _keyring_lib.get_password(service, key)

    def set(self, service: str, key: str, value: str) -> None:
        # No suppression on set: the caller explicitly asked to
        # persist a credential; silently dropping it would leave the
        # user thinking it was saved when the next session can't
        # read it. Let ``KeyringError`` propagate.
        _keyring_lib.set_password(service, key, value)

    def delete(self, service: str, key: str) -> None:
        # Missing secrets are already absent. Other backend failures must
        # propagate so callers can roll back configuration and report that
        # credential removal did not complete.
        if _keyring_lib.get_password(service, key) is None:
            return
        _keyring_lib.delete_password(service, key)


class InMemoryKeychain:
    """Test fake. Backed by a plain dict keyed on ``(service, key)``."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get(self, service: str, key: str) -> str | None:
        return self._store.get((service, key))

    def set(self, service: str, key: str, value: str) -> None:
        self._store[(service, key)] = value

    def delete(self, service: str, key: str) -> None:
        self._store.pop((service, key), None)


__all__ = [
    "InMemoryKeychain",
    "KeychainBackend",
    "Keyring",
    "app_keychain_revision_service",
    "app_keychain_service",
]
