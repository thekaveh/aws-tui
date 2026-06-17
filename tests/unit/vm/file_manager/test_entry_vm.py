"""Tests for the EntryVM facade."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import EntryKind, FileEntry
from aws_tui.vm.file_manager.entry_vm import EntryState, EntryVM
from aws_tui.vm.messages import AuthExpiredMessage  # noqa: F401 — sanity import


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _file_entry(name: str = "a.txt", kind: EntryKind = EntryKind.FILE) -> FileEntry:
    return FileEntry(
        name=name,
        kind=kind,
        size=42 if kind is EntryKind.FILE else None,
        modified=datetime.now(UTC),
    )


def test_entry_vm_construct_dispose() -> None:
    hub = _hub()
    vm = EntryVM(entry=_file_entry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    assert vm.is_constructed
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED


def test_entry_vm_initial_state() -> None:
    vm = EntryVM(entry=_file_entry("readme.md"), hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert vm.name == "readme.md"
    assert vm.kind == EntryKind.FILE
    assert vm.state == EntryState(entry=vm.entry, is_selected=False, is_marked=False)
    assert not vm.is_selected
    assert not vm.is_marked
    vm.dispose()


def test_toggle_select_flips_and_publishes() -> None:
    hub = _hub()
    received: list[str] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(getattr(m, "property_name", "")) if m else None
    )
    vm = EntryVM(entry=_file_entry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    vm.toggle_select_command.execute()
    assert vm.is_selected
    assert "is_selected" in received
    vm.toggle_select_command.execute()
    assert not vm.is_selected
    vm.dispose()


def test_toggle_mark_flips_and_publishes() -> None:
    hub = _hub()
    received: list[str] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(getattr(m, "property_name", "")) if m else None
    )
    vm = EntryVM(entry=_file_entry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    vm.toggle_mark_command.execute()
    assert vm.is_marked
    assert "is_marked" in received
    vm.toggle_mark_command.execute()
    assert not vm.is_marked
    vm.dispose()


def test_set_selected_idempotent() -> None:
    hub = _hub()
    fires: list[str] = []
    hub.messages.subscribe(
        on_next=lambda m: fires.append(getattr(m, "property_name", "")) if m else None
    )
    vm = EntryVM(entry=_file_entry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    vm.set_selected(False)
    assert "is_selected" not in fires
    vm.set_selected(True)
    assert fires.count("is_selected") == 1
    vm.set_selected(True)
    assert fires.count("is_selected") == 1
    vm.dispose()


def test_directory_kind_round_trip() -> None:
    vm = EntryVM(
        entry=_file_entry("docs", EntryKind.DIRECTORY),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    assert vm.kind == EntryKind.DIRECTORY
    assert vm.entry.size is None
    vm.dispose()
