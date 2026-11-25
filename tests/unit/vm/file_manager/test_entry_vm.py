"""Tests for the EntryVM facade."""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import getattr_static

import pytest
from reactivex import Observable
from reactivex import operators as ops
from vmx import NULL_DISPATCHER, ComponentVMOf, CompositeVM, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import EntryKind, FileEntry
from aws_tui.vm.file_manager.entry_vm import EntryState, EntryVM
from tests.helpers import new_hub


def _file_entry(name: str = "a.txt", kind: EntryKind = EntryKind.FILE) -> FileEntry:
    return FileEntry(
        name=name,
        kind=kind,
        size=42 if kind is EntryKind.FILE else None,
        modified=datetime.now(UTC),
    )


def _parent_composite(hub: MessageHub[Message], *entries: EntryVM) -> CompositeVM:
    parent = (
        CompositeVM.builder()
        .name("entries")
        .services(hub, NULL_DISPATCHER)
        .children(lambda: tuple(entry.inner for entry in entries))
        .build()
    )
    parent.construct()
    return parent


def test_entry_vm_construct_dispose() -> None:
    hub = new_hub()
    vm = EntryVM(entry=_file_entry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    assert vm.is_constructed
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED


def test_entry_vm_initial_state() -> None:
    vm = EntryVM(entry=_file_entry("readme.md"), hub=new_hub(), dispatcher=NULL_DISPATCHER)
    assert vm.name == "readme.md"
    assert vm.kind == EntryKind.FILE
    assert vm.state == EntryState(entry=vm.entry, is_marked=False)
    assert not vm.is_current
    assert not vm.is_marked
    vm.dispose()


def test_is_current_follows_the_parent_composite() -> None:
    hub = new_hub()
    a = EntryVM(entry=_file_entry("a"), hub=hub, dispatcher=NULL_DISPATCHER)
    b = EntryVM(entry=_file_entry("b"), hub=hub, dispatcher=NULL_DISPATCHER)
    parent = _parent_composite(hub, a, b)
    changes: list[tuple[str, str, bool, bool]] = []
    a_sub = a.on_property_changed.subscribe(
        lambda prop: changes.append(("a", prop, a.inner.is_current, b.inner.is_current))
    )
    b_sub = b.on_property_changed.subscribe(
        lambda prop: changes.append(("b", prop, a.inner.is_current, b.inner.is_current))
    )
    try:
        parent.current = a.inner
        assert (a.cursor_glyph, b.cursor_glyph) == ("▌", " ")
        assert (a.is_current, b.is_current) == (True, False)
        assert changes == [("a", "is_current", True, False)]
        changes.clear()

        parent.current = b.inner
        assert (a.cursor_glyph, b.cursor_glyph) == (" ", "▌")
        assert (a.is_current, b.is_current) == (False, True)
        assert changes == [
            ("a", "is_current", False, True),
            ("b", "is_current", False, True),
        ]
        changes.clear()

        parent.current = b.inner
        assert changes == []

        parent.current = None
        assert (a.cursor_glyph, b.cursor_glyph) == (" ", " ")
        assert (a.is_current, b.is_current) == (False, False)
        assert changes == [("b", "is_current", False, False)]
    finally:
        a_sub.dispose()
        b_sub.dispose()
        a.dispose()
        b.dispose()
        parent.dispose()
        hub.dispose()


def test_toggle_mark_flips_and_publishes() -> None:
    hub = new_hub()
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


def test_set_marked_notifies_both_the_hub_and_the_per_vm_observable() -> None:
    """Round-3 §9.bis.11 / PR #103: BOTH channels, never either.

    The per-VM Observable is what ``EntryRow`` binds to, and the hub send is
    the published contract other subscribers may still rely on. Dropping
    either half is a silent regression: without the subject a listing stops
    repainting, and without the hub send anything filtering the hub for this
    entry goes deaf.
    """
    hub = new_hub()
    hub_props: list[str] = []
    hub.messages.subscribe(
        on_next=lambda m: hub_props.append(getattr(m, "property_name", "")) if m else None
    )
    vm = EntryVM(entry=_file_entry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    bound: list[str] = []
    vm.on_property_changed.subscribe(on_next=bound.append)
    try:
        vm.set_marked(True)
        assert bound == ["is_marked"]
        assert "is_marked" in hub_props

        # Reapplying the same mark must not repaint the bound row.
        vm.set_marked(True)
        assert bound == ["is_marked"]
    finally:
        vm.dispose()


def test_is_current_is_not_forwarded_after_dispose(monkeypatch: pytest.MonkeyPatch) -> None:
    inner_property_changed: property = getattr_static(ComponentVMOf, "property_changed")
    lifecycle: list[tuple[str, bool]] = []

    def track_subscription(inner: ComponentVMOf[EntryState]) -> Observable[str]:
        changes: Observable[str] = inner_property_changed.__get__(inner)
        if inner.name != "entry.a":
            return changes
        return changes.pipe(
            ops.finally_action(lambda: lifecycle.append(("unsubscribed", inner.is_constructed)))
        )

    monkeypatch.setattr(ComponentVMOf, "property_changed", property(track_subscription))
    hub = new_hub()
    a = EntryVM(entry=_file_entry("a"), hub=hub, dispatcher=NULL_DISPATCHER)
    b = EntryVM(entry=_file_entry("b"), hub=hub, dispatcher=NULL_DISPATCHER)
    parent = _parent_composite(hub, a, b)
    changes: list[str] = []
    sub = a.on_property_changed.subscribe(
        changes.append,
        on_completed=lambda: lifecycle.append(("completed", a.inner.is_constructed)),
    )
    try:
        parent.current = a.inner
        a.dispose()
        a.dispose()
        parent.current = b.inner

        assert lifecycle == [("unsubscribed", True), ("completed", True)]
        assert changes == ["is_current"]
        assert b.inner.is_current
    finally:
        sub.dispose()
        a.dispose()
        b.dispose()
        parent.dispose()
        hub.dispose()


def test_dispose_completes_the_per_vm_observable_and_is_idempotent() -> None:
    """A disposed entry must end its stream, once.

    Textual removes children asynchronously, so a row can still be mounted
    and subscribed when ``PaneVM._replace_entries`` disposes the entry it is
    bound to. Completing the subject tells it the stream ended instead of
    leaving it holding a live observer on a disposed view model.
    """
    vm = EntryVM(entry=_file_entry(), hub=new_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    completions: list[bool] = []
    vm.on_property_changed.subscribe(
        on_next=lambda _prop: None, on_completed=lambda: completions.append(True)
    )
    vm.set_marked(True)
    assert completions == []

    vm.dispose()
    assert completions == [True]
    assert vm.status == ConstructionStatus.DISPOSED

    # Idempotent: a second dispose neither completes again nor raises.
    vm.dispose()
    assert completions == [True]


def test_directory_kind_round_trip() -> None:
    vm = EntryVM(
        entry=_file_entry("docs", EntryKind.DIRECTORY),
        hub=new_hub(),
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
    vm = EntryVM(entry=entry, hub=new_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        assert vm.size_display == expected
    finally:
        vm.dispose()


def test_size_display_is_dir_for_directories_regardless_of_size() -> None:
    entry = FileEntry(name="d", kind=EntryKind.DIRECTORY, size=4096, modified=None)
    vm = EntryVM(entry=entry, hub=new_hub(), dispatcher=NULL_DISPATCHER)
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
    vm = EntryVM(entry=stamped, hub=new_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        assert vm.modified_display == "2026-09-04 13:45"
    finally:
        vm.dispose()
