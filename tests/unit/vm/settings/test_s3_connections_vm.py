"""Tests for S3ConnectionsVM."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigError, ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.keychain import (
    InMemoryKeychain,
    app_keychain_revision_service,
    app_keychain_service,
)
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


class _FailingKeychain(InMemoryKeychain):
    def __init__(self, *, fail_key: str) -> None:
        super().__init__()
        self._fail_key = fail_key

    def set(self, service: str, key: str, value: str) -> None:
        if key == self._fail_key:
            raise RuntimeError(f"failed to write {key}")
        super().set(service, key, value)


class _ServiceFailingKeychain(InMemoryKeychain):
    def set(self, service: str, key: str, value: str) -> None:
        if "connection-revisions/" in service and key == "secret_access_key":
            raise RuntimeError("failed staged secret write")
        super().set(service, key, value)


class _StagedWriteAndCleanupFailingKeychain(_ServiceFailingKeychain):
    def delete(self, service: str, key: str) -> None:
        if "connection-revisions/" in service and key == "access_key_id":
            raise RuntimeError("failed staged cleanup")
        super().delete(service, key)


class _PartiallyDeletingKeychain(InMemoryKeychain):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def delete(self, service: str, key: str) -> None:
        super().delete(service, key)
        if key == "secret_access_key" and not self._failed:
            self._failed = True
            raise RuntimeError("keychain delete failed")


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _entry(name: str = "minio-local", region: str = "us-east-1") -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region=region,
        endpoint_url="http://localhost:9000",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


def _make_vm(tmp_path: Path) -> tuple[S3ConnectionsVM, MessageHub[Message], ConfigStore]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    vm = S3ConnectionsVM(resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, hub, store


def test_connections_filters_to_s3_compatible(tmp_path: Path) -> None:
    vm, _, store = _make_vm(tmp_path)
    store.add_connection(_entry("minio-local"))
    store.add_connection(
        ConnectionEntry(name="aws-prod", kind="aws", profile="default", region="us-east-1")
    )
    names = [c.name for c in vm.connections]
    assert names == ["minio-local"]
    vm.dispose()


def test_add_persists_and_publishes(tmp_path: Path) -> None:
    vm, hub, store = _make_vm(tmp_path)
    received: list[ConnectionListChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: (
            received.append(m) if isinstance(m, ConnectionListChangedMessage) else None
        )
    )
    vm.add(_entry("new-bucket"))
    assert "new-bucket" in store.load().connections
    assert len(received) == 1
    assert received[0].change == "added"
    assert received[0].names == ("new-bucket",)
    vm.dispose()


def test_update_persists_and_publishes(tmp_path: Path) -> None:
    vm, hub, store = _make_vm(tmp_path)
    vm.add(_entry("minio-local"))
    received: list[ConnectionListChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: (
            received.append(m) if isinstance(m, ConnectionListChangedMessage) else None
        )
    )
    vm.update("minio-local", _entry("minio-local", region="us-west-2"))
    assert store.load().connections["minio-local"].region == "us-west-2"
    assert len(received) == 1
    assert received[0].change == "updated"
    assert received[0].names == ("minio-local",)
    vm.dispose()


def test_successful_keychain_update_persists_revision_reference(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = InMemoryKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))

    vm.update("secure", _entry("secure", region="us-west-2"))

    persisted = store.load().connections["secure"]
    assert persisted.credentials == f"keychain:{app_keychain_revision_service('secure', 0)}"
    assert ":pending:" not in (persisted.credentials or "")
    assert keychain.get(app_keychain_service("secure"), "access_key_id") is None
    assert resolver.resolve("secure").access_key_id == "AKIATEST"
    assert resolver.resolve("secure").region == "us-west-2"
    vm.dispose()


def test_keychain_namespaces_keep_connection_and_revision_services_disjoint() -> None:
    primary = app_keychain_service("foo:revision:0")
    revision = app_keychain_revision_service("foo", 0)

    assert primary != revision
    assert app_keychain_revision_service("foo", 0) != app_keychain_revision_service("foo", 1)


def test_update_migrates_legacy_keychain_reference_to_bounded_namespace(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = InMemoryKeychain()
    legacy_service = "aws-tui:legacy:revision:0"
    keychain.set(legacy_service, "access_key_id", "OLDKEY")
    keychain.set(legacy_service, "secret_access_key", "OLDSECRET")
    store.add_connection(
        replace(
            _entry("legacy"),
            credentials=f"keychain:{legacy_service}",
            access_key_id=None,
            secret_access_key=None,
        )
    )
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()

    assert resolver.resolve("legacy").access_key_id == "OLDKEY"
    vm.update("legacy", _entry("legacy", region="us-west-2"))

    persisted = store.load().connections["legacy"]
    assert persisted.credentials == f"keychain:{app_keychain_revision_service('legacy', 0)}"
    assert keychain.get(legacy_service, "access_key_id") is None
    assert resolver.resolve("legacy").access_key_id == "AKIATEST"
    vm.dispose()


def test_legacy_service_is_kept_until_all_colliding_references_migrate(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = InMemoryKeychain()
    shared_service = "aws-tui:foo:revision:0"
    keychain.set(shared_service, "access_key_id", "SHAREDKEY")
    keychain.set(shared_service, "secret_access_key", "SHAREDSECRET")
    for name in ("foo", "foo:revision:0"):
        store.add_connection(
            replace(
                _entry(name),
                credentials=f"keychain:{shared_service}",
                access_key_id=None,
                secret_access_key=None,
            )
        )
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()

    vm.update("foo", _entry("foo", region="us-west-1"))
    assert keychain.get(shared_service, "access_key_id") == "SHAREDKEY"
    assert resolver.resolve("foo:revision:0").access_key_id == "SHAREDKEY"

    vm.update("foo:revision:0", _entry("foo:revision:0", region="us-west-2"))
    assert keychain.get(shared_service, "access_key_id") is None
    assert resolver.resolve("foo").access_key_id == "AKIATEST"
    assert resolver.resolve("foo:revision:0").access_key_id == "AKIATEST"
    vm.dispose()


def test_keychain_updates_reuse_two_bounded_revision_slots(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = InMemoryKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))

    vm.update("secure", _entry("secure", region="us-west-1"))
    vm.update("secure", _entry("secure", region="us-west-2"))
    vm.update("secure", _entry("secure", region="eu-west-1"))

    persisted = store.load().connections["secure"]
    assert persisted.credentials == f"keychain:{app_keychain_revision_service('secure', 0)}"
    assert {service for service, _key in keychain._store} == {
        app_keychain_revision_service("secure", 0)
    }
    assert resolver.resolve("secure").region == "eu-west-1"
    vm.dispose()


def test_concurrent_vm_update_reloads_active_revision_inside_transaction(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    keychain = InMemoryKeychain()
    first_store = ConfigStore(path=path)
    second_store = ConfigStore(path=path)
    first = S3ConnectionsVM(
        resolver=ConnectionResolver(config_store=first_store, keychain=keychain),
        config_store=first_store,
        keychain=keychain,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    second = S3ConnectionsVM(
        resolver=ConnectionResolver(config_store=second_store, keychain=keychain),
        config_store=second_store,
        keychain=keychain,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    first.construct()
    second.construct()
    first.add(_entry("secure"))

    started = threading.Event()
    errors: list[BaseException] = []

    def update_second() -> None:
        started.set()
        try:
            second.update("secure", _entry("secure", region="eu-west-1"))
        except BaseException as exc:
            errors.append(exc)

    with first_store.transaction():
        contender = threading.Thread(target=update_second)
        contender.start()
        assert started.wait(timeout=1.0)
        first.update("secure", _entry("secure", region="us-west-1"))

    contender.join(timeout=2.0)
    assert not contender.is_alive()
    assert errors == []

    persisted = first_store.load().connections["secure"]
    assert persisted.region == "eu-west-1"
    assert persisted.credentials == f"keychain:{app_keychain_revision_service('secure', 1)}"
    assert keychain.get(app_keychain_revision_service("secure", 1), "access_key_id") == "AKIATEST"
    assert {service for service, _key in keychain._store} == {
        app_keychain_revision_service("secure", 1)
    }
    first.dispose()
    second.dispose()


def test_concurrent_vm_add_revalidates_name_inside_transaction(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    keychain = InMemoryKeychain()
    first_store = ConfigStore(path=path)
    second_store = ConfigStore(path=path)
    second = S3ConnectionsVM(
        resolver=ConnectionResolver(config_store=second_store, keychain=keychain),
        config_store=second_store,
        keychain=keychain,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    second.construct()
    started = threading.Event()
    errors: list[BaseException] = []

    def add_second() -> None:
        started.set()
        try:
            second.add(_entry("shared"))
        except BaseException as exc:
            errors.append(exc)

    with first_store.transaction():
        contender = threading.Thread(target=add_second)
        contender.start()
        assert started.wait(timeout=1.0)
        first_store.add_connection(ConnectionEntry(name="shared", kind="aws", profile="shared"))

    contender.join(timeout=2.0)
    assert not contender.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "already exists" in str(errors[0])
    assert first_store.load().connections["shared"].kind == "aws"
    assert keychain._store == {}
    second.dispose()


def test_remove_persists_and_publishes(tmp_path: Path) -> None:
    vm, hub, store = _make_vm(tmp_path)
    vm.add(_entry("minio-local"))
    received: list[ConnectionListChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: (
            received.append(m) if isinstance(m, ConnectionListChangedMessage) else None
        )
    )
    vm.remove("minio-local")
    assert "minio-local" not in store.load().connections
    assert len(received) == 1
    assert received[0].change == "deleted"
    assert received[0].names == ("minio-local",)
    vm.dispose()


def test_add_duplicate_name_rejected(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.add(_entry("dup"))
    with pytest.raises(ValueError, match="already exists"):
        vm.add(_entry("dup"))
    vm.dispose()


def test_add_rejects_name_collision_with_non_s3_connection(tmp_path: Path) -> None:
    vm, _, store = _make_vm(tmp_path)
    store.add_connection(ConnectionEntry(name="shared", kind="aws", profile="shared"))

    with pytest.raises(ValueError, match="already exists"):
        vm.add(_entry("shared"))

    assert store.load().connections["shared"].kind == "aws"
    vm.dispose()


def test_update_with_renamed_entry_rejected(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.add(_entry("old"))
    with pytest.raises(ValueError, match="cannot be renamed"):
        vm.update("old", _entry("new"))
    vm.dispose()


def test_construct_dispose_clean(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.dispose()
    # No exception on double-dispose
    vm.dispose()


def test_entry_from_form_round_trip(tmp_path: Path) -> None:
    from aws_tui.vm.settings.s3_compat_form import S3CompatForm

    vm, _, _ = _make_vm(tmp_path)
    form = S3CompatForm(
        name="from-form",
        endpoint_url="http://x:9000",
        region="r1",
        access_key_id="ak",
        secret_access_key="sk",
        session_token="tok",
        force_path_style=True,
        verify_tls=False,
    )
    entry = vm.entry_from_form(form)
    assert entry.name == "from-form"
    assert entry.kind == "s3-compatible"
    assert entry.endpoint_url == "http://x:9000"
    assert entry.region == "r1"
    assert entry.access_key_id == "ak"
    assert entry.secret_access_key == "sk"
    assert entry.session_token == "tok"
    assert entry.force_path_style is True
    assert entry.verify_tls is False
    vm.dispose()


def test_production_keychain_path_keeps_secrets_out_of_toml(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = InMemoryKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()

    vm.add(_entry("secure"))

    persisted = store.load().connections["secure"]
    assert persisted.credentials == f"keychain:{app_keychain_service('secure')}"
    assert persisted.access_key_id is None
    assert persisted.secret_access_key is None
    assert "AKIATEST" not in store.path.read_text()
    assert "SECRETTEST" not in store.path.read_text()
    assert keychain.get(app_keychain_service("secure"), "access_key_id") == "AKIATEST"
    assert keychain.get(app_keychain_service("secure"), "secret_access_key") == "SECRETTEST"
    assert resolver.resolve("secure").secret_access_key == "SECRETTEST"
    vm.dispose()


def test_removing_keychain_connection_deletes_its_secrets(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = InMemoryKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))

    vm.remove("secure")

    assert keychain.get(app_keychain_service("secure"), "access_key_id") is None
    assert keychain.get(app_keychain_service("secure"), "secret_access_key") is None
    vm.dispose()


def test_remove_rollback_restores_config_and_partially_deleted_secrets(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = _PartiallyDeletingKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))

    with pytest.raises(RuntimeError, match="keychain delete failed"):
        vm.remove("secure")

    assert store.load().connections["secure"].credentials == (
        f"keychain:{app_keychain_service('secure')}"
    )
    assert resolver.resolve("secure").access_key_id == "AKIATEST"
    assert resolver.resolve("secure").secret_access_key == "SECRETTEST"
    vm.dispose()


def test_update_keeps_staged_credentials_when_config_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = _PartiallyDeletingKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))
    real_update = store.update_connection
    calls = 0

    def fail_rollback(name: str, entry: ConnectionEntry) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_update(name, entry)
            return
        raise RuntimeError("config rollback failed")

    monkeypatch.setattr(store, "update_connection", fail_rollback)

    with pytest.raises(RuntimeError, match="config rollback failed"):
        vm.update("secure", _entry("secure", region="us-west-2"))

    persisted = store.load().connections["secure"]
    assert persisted.region == "us-west-2"
    assert "connection-revisions/" in (persisted.credentials or "")
    assert resolver.resolve("secure").access_key_id == "AKIATEST"
    assert resolver.resolve("secure").secret_access_key == "SECRETTEST"
    vm.dispose()


def test_remove_restores_credentials_even_when_config_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = _PartiallyDeletingKeychain()
    vm = S3ConnectionsVM(
        resolver=ConnectionResolver(config_store=store, keychain=keychain),
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))

    def fail_rollback(_entry: ConnectionEntry) -> None:
        raise RuntimeError("config rollback failed")

    monkeypatch.setattr(store, "add_connection", fail_rollback)

    with pytest.raises(RuntimeError, match="config rollback failed"):
        vm.remove("secure")

    assert "secure" not in store.load().connections
    assert keychain.get(app_keychain_service("secure"), "access_key_id") == "AKIATEST"
    assert keychain.get(app_keychain_service("secure"), "secret_access_key") == "SECRETTEST"
    vm.dispose()


def test_add_rolls_back_partial_keychain_write(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = _FailingKeychain(fail_key="secret_access_key")
    vm = S3ConnectionsVM(
        resolver=ConnectionResolver(config_store=store, keychain=keychain),
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()

    with pytest.raises(RuntimeError, match="secret_access_key"):
        vm.add(_entry("secure"))

    assert "secure" not in store.load().connections
    assert keychain.get(app_keychain_service("secure"), "access_key_id") is None
    assert keychain.get(app_keychain_service("secure"), "secret_access_key") is None
    vm.dispose()


def test_add_failure_restores_orphaned_keychain_values(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = _FailingKeychain(fail_key="secret_access_key")
    service = app_keychain_service("secure")
    InMemoryKeychain.set(keychain, service, "access_key_id", "ORIGINALKEY")
    InMemoryKeychain.set(keychain, service, "secret_access_key", "ORIGINALSECRET")
    vm = S3ConnectionsVM(
        resolver=ConnectionResolver(config_store=store, keychain=keychain),
        config_store=store,
        keychain=keychain,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()

    with pytest.raises(RuntimeError, match="failed to write secret_access_key"):
        vm.add(_entry("secure"))

    assert keychain.get(service, "access_key_id") == "ORIGINALKEY"
    assert keychain.get(service, "secret_access_key") == "ORIGINALSECRET"
    assert store.load().connections == {}
    vm.dispose()


def test_update_failure_preserves_active_keychain_credentials(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = _ServiceFailingKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))

    with pytest.raises(RuntimeError, match="staged secret"):
        vm.update("secure", _entry("secure", region="us-west-2"))

    persisted = store.load().connections["secure"]
    assert persisted.region == "us-east-1"
    assert resolver.resolve("secure").access_key_id == "AKIATEST"
    assert resolver.resolve("secure").secret_access_key == "SECRETTEST"
    vm.dispose()


def test_update_surfaces_failed_staged_keychain_cleanup(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = _StagedWriteAndCleanupFailingKeychain()
    resolver = ConnectionResolver(config_store=store, keychain=keychain)
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.add(_entry("secure"))

    with pytest.raises(RuntimeError, match=r"rollback incomplete.*failed staged cleanup"):
        vm.update("secure", _entry("secure", region="us-west-2"))

    assert store.load().connections["secure"].region == "us-east-1"
    assert resolver.resolve("secure").secret_access_key == "SECRETTEST"
    vm.dispose()


def test_entry_from_form_normalizes_text_and_blank_session_token(tmp_path: Path) -> None:
    from aws_tui.vm.settings.s3_compat_form import S3CompatForm

    vm, _, _ = _make_vm(tmp_path)
    form = S3CompatForm(
        name=" from-form ",
        endpoint_url=" http://x:9000 ",
        region=" r1 ",
        access_key_id=" ak ",
        secret_access_key=" sk ",
        session_token="   ",
        force_path_style=True,
        verify_tls=False,
    )
    entry = vm.entry_from_form(form)
    assert entry.name == "from-form"
    assert entry.endpoint_url == "http://x:9000"
    assert entry.region == "r1"
    assert entry.access_key_id == "ak"
    assert entry.secret_access_key == "sk"
    assert entry.session_token is None
    vm.dispose()


@pytest.mark.parametrize("operation", ["add", "update", "remove"])
def test_read_only_mutations_reject_without_keychain_or_message_side_effects(
    tmp_path: Path,
    operation: str,
) -> None:
    path = tmp_path / "config.toml"
    writable = ConfigStore(path=path)
    writable.add_connection(_entry("secure"))
    store = ConfigStore(path=path, read_only=True)
    keychain = InMemoryKeychain()
    hub = _hub()
    received: list[ConnectionListChangedMessage] = []
    hub.messages.subscribe(
        on_next=lambda message: (
            received.append(message) if isinstance(message, ConnectionListChangedMessage) else None
        )
    )
    vm = S3ConnectionsVM(
        resolver=ConnectionResolver(config_store=store, keychain=keychain),
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()

    mutations = {
        "add": lambda: vm.add(_entry("new")),
        "update": lambda: vm.update("secure", _entry("secure", region="us-west-2")),
        "remove": lambda: vm.remove("secure"),
    }
    with pytest.raises(ConfigError, match="read-only"):
        mutations[operation]()

    assert store.load().connections["secure"].region == "us-east-1"
    assert keychain._store == {}
    assert received == []
    vm.dispose()
