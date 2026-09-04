"""Unit tests for the KeychainBackend protocol and its two implementations."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from aws_tui.infra.keychain import InMemoryKeychain, KeychainBackend, Keyring


class TestInMemoryKeychain:
    def test_get_returns_none_when_missing(self) -> None:
        kc = InMemoryKeychain()
        assert kc.get("svc", "k") is None

    def test_set_then_get(self) -> None:
        kc = InMemoryKeychain()
        kc.set("svc", "k", "v")
        assert kc.get("svc", "k") == "v"

    def test_different_services_are_isolated(self) -> None:
        kc = InMemoryKeychain()
        kc.set("svc-a", "k", "alpha")
        kc.set("svc-b", "k", "beta")
        assert kc.get("svc-a", "k") == "alpha"
        assert kc.get("svc-b", "k") == "beta"

    def test_delete_removes_key(self) -> None:
        kc = InMemoryKeychain()
        kc.set("svc", "k", "v")
        kc.delete("svc", "k")
        assert kc.get("svc", "k") is None

    def test_delete_missing_key_is_silent(self) -> None:
        kc = InMemoryKeychain()
        kc.delete("svc", "absent")  # must not raise

    def test_overwrite(self) -> None:
        kc = InMemoryKeychain()
        kc.set("svc", "k", "v1")
        kc.set("svc", "k", "v2")
        assert kc.get("svc", "k") == "v2"

    def test_satisfies_keychain_backend_protocol(self) -> None:
        kc: KeychainBackend = InMemoryKeychain()
        assert isinstance(kc, KeychainBackend)


class TestKeyring:
    def test_get_delegates_to_keyring_get_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import keyring as keyring_lib

        spy = MagicMock(return_value="value-from-os-keychain")
        monkeypatch.setattr(keyring_lib, "get_password", spy)
        kr = Keyring()
        result = kr.get("svc", "username")
        assert result == "value-from-os-keychain"
        spy.assert_called_once_with("svc", "username")

    def test_set_delegates_to_keyring_set_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import keyring as keyring_lib

        spy: MagicMock = MagicMock()
        monkeypatch.setattr(keyring_lib, "set_password", spy)
        kr = Keyring()
        kr.set("svc", "username", "secret")
        spy.assert_called_once_with("svc", "username", "secret")

    def test_get_propagates_backend_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import keyring as keyring_lib
        import keyring.errors as keyring_errors

        def raise_backend_failure(*_args: Any, **_kwargs: Any) -> None:
            raise keyring_errors.KeyringError("backend unavailable")

        monkeypatch.setattr(keyring_lib, "get_password", raise_backend_failure)
        with pytest.raises(keyring_errors.KeyringError, match="backend unavailable"):
            Keyring().get("svc", "username")

    def test_delete_delegates_to_keyring_delete_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import keyring as keyring_lib

        spy: MagicMock = MagicMock()
        monkeypatch.setattr(keyring_lib, "get_password", MagicMock(return_value="present"))
        monkeypatch.setattr(keyring_lib, "delete_password", spy)
        kr = Keyring()
        kr.delete("svc", "username")
        spy.assert_called_once_with("svc", "username")

    def test_delete_missing_password_does_not_call_delete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import keyring as keyring_lib

        monkeypatch.setattr(keyring_lib, "get_password", MagicMock(return_value=None))
        delete = MagicMock()
        monkeypatch.setattr(keyring_lib, "delete_password", delete)
        Keyring().delete("svc", "absent")
        delete.assert_not_called()

    def test_delete_propagates_password_delete_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import keyring as keyring_lib
        import keyring.errors as keyring_errors

        monkeypatch.setattr(keyring_lib, "get_password", MagicMock(return_value="present"))
        monkeypatch.setattr(
            keyring_lib,
            "delete_password",
            MagicMock(side_effect=keyring_errors.PasswordDeleteError("delete failed")),
        )
        with pytest.raises(keyring_errors.PasswordDeleteError, match="delete failed"):
            Keyring().delete("svc", "username")

    def test_delete_propagates_backend_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import keyring as keyring_lib
        import keyring.errors as keyring_errors

        def raise_backend_failure(*_args: Any, **_kwargs: Any) -> None:
            raise keyring_errors.KeyringError("backend unavailable")

        monkeypatch.setattr(keyring_lib, "get_password", MagicMock(return_value="present"))
        monkeypatch.setattr(keyring_lib, "delete_password", raise_backend_failure)
        with pytest.raises(keyring_errors.KeyringError, match="backend unavailable"):
            Keyring().delete("svc", "username")
