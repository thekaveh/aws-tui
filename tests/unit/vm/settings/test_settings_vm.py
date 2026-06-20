"""Tests for SettingsVM."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _entry(name: str) -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region="us-east-1",
        endpoint_url="http://localhost:9000",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


def _make_vm(tmp_path: Path) -> tuple[SettingsVM, MessageHub[Message], S3ConnectionsVM]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER)
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, hub, s3


def test_default_active_section_is_connections(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    assert vm.active_section == "connections"
    vm.dispose()


def test_sections_and_enabled_constants(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    assert vm.SECTIONS == ("connections", "themes", "keymap")
    assert frozenset({"connections"}) == vm.ENABLED
    vm.dispose()


def test_change_section_to_enabled_works(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.change_section("connections")
    assert vm.active_section == "connections"
    vm.dispose()


def test_change_section_to_disabled_is_noop(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    vm.change_section("themes")
    assert vm.active_section == "connections"  # unchanged
    vm.change_section("keymap")
    assert vm.active_section == "connections"
    vm.dispose()


def test_dirty_set_accumulates_updates_and_deletes(tmp_path: Path) -> None:
    vm, hub, _ = _make_vm(tmp_path)
    hub.send(ConnectionListChangedMessage(names=("a",), change="updated"))
    hub.send(ConnectionListChangedMessage(names=("b",), change="deleted"))
    assert vm.dirty_connection_names == frozenset({"a", "b"})
    vm.dispose()


def test_dirty_set_ignores_adds(tmp_path: Path) -> None:
    vm, hub, _ = _make_vm(tmp_path)
    hub.send(ConnectionListChangedMessage(names=("new",), change="added"))
    assert vm.dirty_connection_names == frozenset()
    vm.dispose()


def test_clear_dirty_resets_set(tmp_path: Path) -> None:
    vm, hub, _ = _make_vm(tmp_path)
    hub.send(ConnectionListChangedMessage(names=("a",), change="updated"))
    assert vm.dirty_connection_names == frozenset({"a"})
    vm.clear_dirty()
    assert vm.dirty_connection_names == frozenset()
    vm.dispose()


def test_lifecycle_status(tmp_path: Path) -> None:
    vm, _, _ = _make_vm(tmp_path)
    assert vm.status == ConstructionStatus.CONSTRUCTED
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED


def test_dirty_set_accumulates_then_clears_for_reload_flow(tmp_path: Path) -> None:
    """Pins the contract AwsTuiApp._reload_after_settings depends on:
    dirty_connection_names accumulates 'updated' and 'deleted' names
    during the modal's lifetime; clear_dirty() resets atomically after
    the reload worker is scheduled."""
    vm, hub, _ = _make_vm(tmp_path)
    # Simulate three CRUD events during the modal's lifetime
    hub.send(ConnectionListChangedMessage(names=("a",), change="updated"))
    hub.send(ConnectionListChangedMessage(names=("b",), change="deleted"))
    hub.send(ConnectionListChangedMessage(names=("c",), change="added"))  # ignored
    # AwsTuiApp reads this snapshot before scheduling the reload worker
    snapshot = vm.dirty_connection_names
    assert snapshot == frozenset({"a", "b"})
    # Then immediately calls clear_dirty so the next modal open starts fresh
    vm.clear_dirty()
    assert vm.dirty_connection_names == frozenset()
    # Snapshot taken before clear is unaffected (frozenset is immutable)
    assert snapshot == frozenset({"a", "b"})
    vm.dispose()
