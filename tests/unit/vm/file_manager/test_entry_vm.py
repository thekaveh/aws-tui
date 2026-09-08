"""Tests for the EntryVM facade."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import EntryKind, FileEntry
from aws_tui.vm.file_manager.entry_vm import EntryState, EntryVM


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


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),
        # The boundary itself: 1024 must roll over to K, not render as "1024 B".
        # `size < 1024` -> `size <= 1024` survived the whole repo suite, because
        # nothing exercised _format_size at all.
        (1024, "1.0 K"),
        (1025, "1.0 K"),
        (1024 * 1024 - 1, "1024.0 K"),
        (1024 * 1024, "1.0 M"),
        (1024**3, "1.0 G"),
        (1024**4, "1.0 T"),
        (1024**5, "1.0 P"),
        (None, "?"),
    ],
)
def test_size_display_covers_every_unit_boundary(size: int | None, expected: str) -> None:
    entry = FileEntry(
        name="f",
        kind=EntryKind.FILE,
        size=size,
        modified=None,
    )
    vm = EntryVM(entry=entry, hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        assert vm.size_display == expected
    finally:
        vm.dispose()


def test_size_display_is_dir_for_directories_regardless_of_size() -> None:
    entry = FileEntry(name="d", kind=EntryKind.DIRECTORY, size=4096, modified=None)
    vm = EntryVM(entry=entry, hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        assert vm.size_display == "<DIR>"
    finally:
        vm.dispose()


def test_modified_display_formats_and_handles_absence() -> None:
    stamped = FileEntry(
        name="f",
        kind=EntryKind.FILE,
        size=1,
        modified=datetime(2026, 9, 4, 13, 45, tzinfo=UTC),
    )
    vm = EntryVM(entry=stamped, hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        assert vm.modified_display == "2026-09-04 13:45"
    finally:
        vm.dispose()
