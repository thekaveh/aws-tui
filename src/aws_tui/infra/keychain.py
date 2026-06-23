"""Keychain abstraction for secret storage.

The :class:`KeychainBackend` protocol is what the rest of the infra layer
depends on. Two concrete implementations:

* :class:`Keyring` — thin adapter over the ``keyring`` library (macOS
  Keychain on darwin, gnome-keyring / kwallet on Linux).
* :class:`InMemoryKeychain` — a dict-backed fake used by tests and by
  callers that want an explicit no-touch backend (e.g. dry-runs).
"""

from __future__ import annotations

import contextlib
from typing import Protocol, runtime_checkable

import keyring as _keyring_lib
from keyring.errors import KeyringError, PasswordDeleteError


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
        # Treat any keyring-side failure (locked keychain, no backend
        # configured on headless Linux, OS error, etc.) as "no
        # credential available" rather than letting the exception
        # escape and crash startup. The caller (``ConnectionResolver
        # ._dispatch_s3_credentials``) is already prepared to handle
        # ``None`` — it just means the credential lookup didn't
        # resolve here and the connection will surface as
        # ``AUTH_REQUIRED`` once it's used.
        try:
            return _keyring_lib.get_password(service, key)
        except KeyringError:
            return None

    def set(self, service: str, key: str, value: str) -> None:
        # No suppression on set: the caller explicitly asked to
        # persist a credential; silently dropping it would leave the
        # user thinking it was saved when the next session can't
        # read it. Let ``KeyringError`` propagate.
        _keyring_lib.set_password(service, key, value)

    def delete(self, service: str, key: str) -> None:
        # Deleting a non-existent secret should be a no-op so callers can
        # call delete unconditionally on cleanup paths. Also suppress
        # the general ``KeyringError`` for the same reason as ``get``
        # — a failed delete on a degraded keychain backend shouldn't
        # block a cleanup flow.
        with contextlib.suppress(PasswordDeleteError, KeyringError):
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


__all__ = ["InMemoryKeychain", "KeychainBackend", "Keyring"]
