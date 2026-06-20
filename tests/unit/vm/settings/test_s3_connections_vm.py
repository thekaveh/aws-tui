"""Tests for S3ConnectionsVM."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


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
