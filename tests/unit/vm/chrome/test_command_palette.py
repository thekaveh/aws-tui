"""Tests for the CommandPaletteVM."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from vmx import (
    NULL_DISPATCHER,
    ComponentVMOf,
    ConstructionStatus,
    MessageHub,
    PropertyChangedMessage,
)
from vmx.messages.protocols import Message

from aws_tui.vm._component import IsolatedComponentVM
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM, PaletteEntry
from aws_tui.vm.messages import PaletteActionFailedMessage


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _build() -> CommandPaletteVM:
    vm = CommandPaletteVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def _entry(
    id_: str,
    label: str,
    category: str = "test",
    keywords: tuple[str, ...] = (),
    service_ids: frozenset[str] | None = None,
) -> PaletteEntry:
    return PaletteEntry(
        id=id_,
        label=label,
        category=category,
        keywords=keywords,
        service_ids=service_ids or frozenset(),
    )


def test_initial_state() -> None:
    vm = _build()
    assert not vm.is_open
    assert vm.filter_text == ""
    assert vm.filtered_entries == ()
    assert vm.selected_index == 0
    vm.dispose()


def test_palette_uses_vmx_scored_filtered_composite() -> None:
    from vmx import ScoredFilteredCompositeVM

    vm = _build()
    try:
        assert isinstance(vm._scored_filter, ScoredFilteredCompositeVM)
    finally:
        vm.dispose()


def test_register_entries_and_filter_by_substring() -> None:
    vm = _build()
    vm.register_entry(_entry("e1", "empty bucket"), lambda: None)
    vm.register_entry(_entry("e2", "delete bucket"), lambda: None)
    vm.register_entry(_entry("e3", "create bucket"), lambda: None)
    vm.register_entry(_entry("e4", "bulk delete selected"), lambda: None)
    vm.register_entry(_entry("e5", "switch theme"), lambda: None)
    vm.register_entry(_entry("e6", "switch connection"), lambda: None)
    vm.open_command.execute()
    vm.filter_text = "buc"
    filtered_ids = {e.id for e in vm.filtered_entries}
    assert filtered_ids == {"e1", "e2", "e3", "e4"}
    vm.dispose()


def test_open_close_command_toggles_is_open() -> None:
    vm = _build()
    vm.open_command.execute()
    assert vm.is_open
    vm.close_command.execute()
    assert not vm.is_open
    vm.dispose()


def test_move_selection_clamps_within_filtered_entries() -> None:
    vm = _build()
    vm.register_entry(_entry("e1", "alpha"), lambda: None)
    vm.register_entry(_entry("e2", "beta"), lambda: None)
    vm.register_entry(_entry("e3", "gamma"), lambda: None)
    vm.open_command.execute()
    assert vm.selected_index == 0
    vm.move_selection_command.execute(1)
    assert vm.selected_index == 1
    vm.move_selection_command.execute(5)
    assert vm.selected_index == 2  # clamped at len-1
    vm.move_selection_command.execute(-10)
    assert vm.selected_index == 0  # clamped at 0
    vm.dispose()


def test_selection_is_the_scored_view_current() -> None:
    vm = _build()
    try:
        assert vm._scored_filter.current is None
        assert vm.selected_index == 0
        for i, label in enumerate(("alpha", "beta", "gamma")):
            vm.register_entry(_entry(f"e{i}", label), lambda: None)
        vm.open_command.execute()
        for delta, expected in ((1, 1), (10**9, 2), (-(10**9), 0)):
            vm.move_selection_command.execute(delta)
            assert vm.selected_index == expected
            current = vm._scored_filter.current
            assert current is not None
            assert current.model is vm.filtered_entries[vm.selected_index]
        vm.move_selection_command.execute(1)
        vm.register_entry(_entry("e3", "delta"), lambda: None)
        assert vm.selected_index == 0
        assert vm._scored_filter.current is vm._scored_filter.visible[0]
        vm.move_selection_command.execute(1)
        vm.close_command.execute()
        vm.open_command.execute()
        assert vm.selected_index == 0
        assert vm._scored_filter.current is vm._scored_filter.visible[0]
        vm.filter_text = "no match"
        assert vm.filtered_entries == ()
        assert vm._scored_filter.current is None
        assert vm.selected_index == 0
    finally:
        vm.dispose()


def test_query_change_selects_top_match_while_selection_still_matches() -> None:
    vm = _build()
    try:
        vm.register_entry(_entry("e1", "alpha"), lambda: None)
        vm.register_entry(_entry("e2", "alphabet"), lambda: None)
        vm.open_command.execute()
        vm.move_selection_command.execute(1)
        assert vm._scored_filter.current is vm._scored_filter.visible[1]
        vm.filter_text = "alph"
        assert vm.selected_index == 0
        assert vm._scored_filter.current is vm._scored_filter.visible[0]
        assert [entry.id for entry in vm.filtered_entries] == ["e1", "e2"]
    finally:
        vm.dispose()


def test_context_change_selects_top_match_while_selection_stays_visible() -> None:
    vm = _build()
    try:
        vm.register_entry(_entry("global", "Help"), lambda: None)
        vm.register_entry(
            _entry("shared", "Shared", service_ids=frozenset({"glue", "athena"})),
            lambda: None,
        )
        vm.set_active_service("glue")
        vm.open_command.execute()
        vm.move_selection_command.execute(1)
        assert vm._scored_filter.current is vm._scored_filter.visible[1]
        vm.set_active_service("athena")
        assert vm.selected_index == 0
        assert vm._scored_filter.current is vm._scored_filter.visible[0]
        assert [entry.id for entry in vm.filtered_entries] == ["global", "shared"]
    finally:
        vm.dispose()


def test_property_notifications_match_each_operation() -> None:
    vm = _build()
    names: list[str] = []
    subscription = vm._hub.messages.subscribe(
        on_next=lambda message: (
            names.append(message.property_name)
            if isinstance(message, PropertyChangedMessage) and message.sender_object is vm
            else None
        )
    )
    try:
        for i, label in enumerate(("alpha", "alphabet", "beta")):
            vm.register_entry(_entry(f"e{i}", label), lambda: None)
        vm.open_command.execute()
        names.clear()
        vm.move_selection_command.execute(1)
        assert names == ["selected_index"]
        names.clear()
        vm.move_selection_command.execute(100)
        assert names == ["selected_index"]
        names.clear()
        vm.move_selection_command.execute(100)
        assert names == []
        vm.filter_text = "alph"
        assert names == ["filter_text", "filtered_entries", "selected_index"]
        names.clear()
        vm.filter_text = "alph"
        vm.set_active_service(None)
        vm.unregister_entry("missing")
        vm.move_selection_command.execute(0)
        vm.move_selection_command.execute(None)
        vm.move_selection_command.execute(-100)
        assert names == []
    finally:
        subscription.dispose()
        vm.dispose()


def test_registration_scores_each_entry_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import aws_tui.vm.chrome.command_palette_vm as palette

    score = palette._score
    calls = 0

    def counting(entry: PaletteEntry, query: str) -> int | None:
        nonlocal calls
        calls += 1
        return score(entry, query)

    monkeypatch.setattr(palette, "_score", counting)
    vm = _build()
    try:
        total = 0
        for i in range(20):
            calls = 0
            vm.register_entry(_entry(f"e{i}", f"entry {i}"), lambda: None)
            assert calls == i + 1
            total += calls
        assert total == 210
    finally:
        vm.dispose()


def test_execute_selected_invokes_callable() -> None:
    vm = _build()
    seen: list[str] = []
    vm.register_entry(_entry("e1", "do thing"), lambda: seen.append("e1"))
    vm.register_entry(_entry("e2", "do other"), lambda: seen.append("e2"))
    vm.open_command.execute()
    vm.move_selection_command.execute(1)
    vm.execute_selected_command.execute()
    assert seen == ["e2"]
    # Executing closes the palette by default.
    assert not vm.is_open
    vm.dispose()


async def test_execute_selected_awaits_coroutine_callable() -> None:
    vm = _build()
    seen: list[str] = []

    async def _async_action() -> None:
        seen.append("ran")

    cb: Callable[[], Awaitable[None]] = _async_action
    vm.register_entry(_entry("e1", "do thing"), cb)
    vm.open_command.execute()
    vm.execute_selected_command.execute()
    # Allow the scheduled task to run.
    import asyncio

    for _ in range(5):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert seen == ["ran"]
    vm.dispose()


async def test_shutdown_cancels_and_drains_pending_actions() -> None:
    import asyncio

    vm = _build()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_action() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    vm.register_entry(_entry("pending", "Pending"), pending_action)
    vm.open_command.execute()
    vm.execute_selected_command.execute()
    await started.wait()

    await vm.shutdown()

    assert cancelled.is_set()
    assert vm._pending_tasks == {}
    vm.dispose()


@pytest.mark.parametrize("cleanup", ["shutdown", "dispose"])
async def test_palette_cleanup_closes_an_action_cancelled_before_it_starts(cleanup: str) -> None:
    import asyncio
    import inspect

    vm = _build()
    started = False

    async def action() -> None:
        nonlocal started
        started = True
        await asyncio.Event().wait()

    coroutine = action()
    vm.register_entry(_entry("pending", "Pending"), lambda: coroutine)
    vm.open_command.execute()
    vm.execute_selected_command.execute()
    try:
        # Cleanup runs before the task's first event-loop turn.
        if cleanup == "shutdown":
            await vm.shutdown()
        else:
            vm.dispose()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert not started
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
        assert vm._pending_tasks == {}
    finally:
        coroutine.close()
        vm.dispose()


@pytest.mark.parametrize("kind", ["task", "future"])
async def test_shutdown_owns_an_action_returning_an_existing_awaitable(kind: str) -> None:
    import asyncio

    vm = _build()

    async def action() -> None:
        await asyncio.Event().wait()

    awaitable: asyncio.Future[None] = (
        asyncio.create_task(action())
        if kind == "task"
        else asyncio.get_running_loop().create_future()
    )
    vm.register_entry(_entry("pending", "Pending"), lambda: awaitable)
    vm.open_command.execute()
    vm.execute_selected_command.execute()
    try:
        await vm.shutdown()
        assert awaitable.cancelled()
        assert vm._pending_tasks == {}
    finally:
        awaitable.cancel()
        await asyncio.gather(awaitable, return_exceptions=True)
        vm.dispose()


@pytest.mark.parametrize("async_action", [False, True])
async def test_execute_failure_publishes_entry_identity(async_action: bool) -> None:
    hub = _hub()
    vm = CommandPaletteVM(hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    messages: list[PaletteActionFailedMessage] = []
    subscription = hub.messages.subscribe(
        on_next=lambda message: (
            messages.append(message) if isinstance(message, PaletteActionFailedMessage) else None
        )
    )

    if async_action:

        async def fail() -> None:
            raise RuntimeError("sensitive details")

    else:

        def fail() -> None:
            raise RuntimeError("sensitive details")

    vm.register_entry(_entry("dangerous.action", "Fail"), fail)
    vm.open_command.execute()
    vm.execute_selected_command.execute()
    if async_action:
        import asyncio

        for _ in range(20):
            if messages:
                break
            await asyncio.sleep(0.01)

    assert messages == [
        PaletteActionFailedMessage(
            entry_id="dangerous.action",
            error_type="RuntimeError",
        )
    ]
    subscription.dispose()
    vm.dispose()


def test_execute_with_empty_filtered_is_noop() -> None:
    vm = _build()
    vm.open_command.execute()
    # No entries registered.
    vm.execute_selected_command.execute()
    assert vm.is_open  # remained open (no-op)
    vm.dispose()


def test_async_action_without_running_loop_is_closed_and_reported() -> None:
    hub = _hub()
    vm = CommandPaletteVM(hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    messages: list[PaletteActionFailedMessage] = []
    subscription = hub.messages.subscribe(
        on_next=lambda message: (
            messages.append(message) if isinstance(message, PaletteActionFailedMessage) else None
        )
    )

    async def action() -> None:
        return None

    coroutine = action()
    vm.register_entry(_entry("async.action", "Async"), lambda: coroutine)
    vm.open_command.execute()
    vm.execute_selected_command.execute()

    assert coroutine.cr_frame is None
    assert messages == [
        PaletteActionFailedMessage(entry_id="async.action", error_type="RuntimeError")
    ]
    subscription.dispose()
    vm.dispose()


def test_unregister_entry_removes_from_filter() -> None:
    vm = _build()
    vm.register_entry(_entry("e1", "alpha"), lambda: None)
    vm.register_entry(_entry("e2", "beta"), lambda: None)
    vm.open_command.execute()
    assert len(vm.filtered_entries) == 2
    vm.unregister_entry("e1")
    assert {e.id for e in vm.filtered_entries} == {"e2"}
    vm.dispose()


def test_keyword_match_falls_back() -> None:
    vm = _build()
    vm.register_entry(
        _entry("e1", "Activate Workspace", keywords=("buck", "bucket")),
        lambda: None,
    )
    vm.open_command.execute()
    vm.filter_text = "buck"
    assert {e.id for e in vm.filtered_entries} == {"e1"}
    vm.dispose()


def test_substring_scores_higher_than_keyword() -> None:
    """Entries whose label contains the query come before keyword-only hits."""
    vm = _build()
    vm.register_entry(_entry("e1", "switch bucket"), lambda: None)
    vm.register_entry(_entry("e2", "create new", keywords=("bucket",)), lambda: None)
    vm.open_command.execute()
    vm.filter_text = "bucket"
    ids = [e.id for e in vm.filtered_entries]
    assert ids[0] == "e1"
    vm.dispose()


def test_filter_clear_restores_all_entries() -> None:
    vm = _build()
    vm.register_entry(_entry("e1", "alpha"), lambda: None)
    vm.register_entry(_entry("e2", "beta"), lambda: None)
    vm.open_command.execute()
    vm.filter_text = "alp"
    assert len(vm.filtered_entries) == 1
    vm.filter_text = ""
    assert len(vm.filtered_entries) == 2
    vm.dispose()


def test_active_service_filters_entries_through_vmx_projection() -> None:
    vm = _build()
    vm.register_entry(_entry("global", "Help"), lambda: None)
    vm.register_entry(
        _entry("glue", "Choose Glue run state", service_ids=frozenset({"glue"})),
        lambda: None,
    )
    vm.register_entry(
        _entry("athena", "Choose Athena workgroup", service_ids=frozenset({"athena"})),
        lambda: None,
    )

    vm.set_active_service("glue")
    assert [entry.id for entry in vm.filtered_entries] == ["global", "glue"]

    vm.set_active_service("athena")
    assert [entry.id for entry in vm.filtered_entries] == ["global", "athena"]

    vm.set_active_service(None)
    assert [entry.id for entry in vm.filtered_entries] == ["global"]
    vm.dispose()


def test_context_change_resets_palette_selection() -> None:
    vm = _build()
    vm.register_entry(_entry("global", "Help"), lambda: None)
    vm.register_entry(
        _entry("glue-1", "Glue catalog", service_ids=frozenset({"glue"})),
        lambda: None,
    )
    vm.register_entry(
        _entry("glue-2", "Glue jobs", service_ids=frozenset({"glue"})),
        lambda: None,
    )
    vm.set_active_service("glue")
    vm.open_command.execute()
    vm.move_selection_command.execute(2)
    assert vm.selected_index == 2

    vm.set_active_service("athena")
    assert vm.selected_index == 0
    assert [entry.id for entry in vm.filtered_entries] == ["global"]
    vm.dispose()


def test_selected_index_resets_when_filter_changes() -> None:
    vm = _build()
    vm.register_entry(_entry("e1", "alpha"), lambda: None)
    vm.register_entry(_entry("e2", "beta"), lambda: None)
    vm.open_command.execute()
    vm.move_selection_command.execute(1)
    assert vm.selected_index == 1
    vm.filter_text = "alp"
    assert vm.selected_index == 0
    vm.dispose()


def test_palette_entry_immutable() -> None:
    e = _entry("x", "y")
    with pytest.raises(AttributeError):
        e.label = "z"  # type: ignore[misc]


def test_dispose_releases_commands() -> None:
    """``dispose()`` releases every RelayCommand and leaves it inert."""
    vm = _build()
    vm.dispose()
    assert not vm.open_command.can_execute()
    vm.open_command.execute()  # must not raise


def test_equal_registration_preserves_child_and_refreshes_callback():
    vm = _build()
    calls = []
    names: list[str] = []
    subscription = vm._hub.messages.subscribe(
        on_next=lambda message: (
            names.append(message.property_name)
            if isinstance(message, PropertyChangedMessage) and message.sender_object is vm
            else None
        )
    )
    try:
        vm.register_entry(_entry("first", "First"), lambda: calls.append("first"))
        entry = _entry("same", "Same")
        vm.register_entry(entry, lambda: calls.append("old"))
        vm.register_entry(_entry("third", "Third"), lambda: calls.append("third"))
        vm.open_command.execute()
        vm.move_selection_command.execute(1)
        child = vm._items["same"]
        current = vm._scored_filter.current
        names.clear()
        vm.register_entry(entry, lambda: calls.append("fresh"))
        assert vm._items["same"] is child
        assert child.is_constructed
        assert vm._scored_filter.current is current
        assert vm.selected_index == 1
        assert names == []
        vm.execute_selected_command.execute()
        assert calls == ["fresh"]
        vm.open_command.execute()
        vm.move_selection_command.execute(2)
        names.clear()
        vm.register_entry(_entry("same", "Changed"), lambda: calls.append("changed"))
        assert vm._items["same"] is not child
        assert not child.is_constructed
        assert vm.selected_index == 0
        assert vm._scored_filter.current is vm._scored_filter.visible[0]
        # Replacing an entry recomputes the view twice (removal, then append), so
        # each name can appear twice. The order of first publication is what matters.
        assert list(dict.fromkeys(names)) == ["filtered_entries", "selected_index"]
        vm.move_selection_command.execute(2)
        vm.execute_selected_command.execute()
        assert calls == ["fresh", "changed"]
    finally:
        subscription.dispose()
        vm.dispose()


def test_removal_resets_current_and_discards_removed_action() -> None:
    vm = _build()
    calls: list[str] = []
    try:
        for id_ in ("first", "second", "third"):
            vm.register_entry(_entry(id_, id_), lambda id_=id_: calls.append(id_))
        vm.open_command.execute()
        vm.move_selection_command.execute(2)
        vm.unregister_entry("first")
        assert vm.selected_index == 0
        assert vm._scored_filter.current is vm._scored_filter.visible[0]
        assert vm._scored_filter.current.model.id == "second"
        vm.unregister_entry("second")
        assert vm.selected_index == 0
        assert vm._scored_filter.current is vm._scored_filter.visible[0]
        vm.execute_selected_command.execute()
        assert calls == ["third"]
        vm.unregister_entry("third")
        assert vm._scored_filter.current is None
        assert vm.selected_index == 0
        vm.open_command.execute()
        vm.execute_selected_command.execute()
        assert calls == ["third"]
    finally:
        vm.dispose()


def test_unavailable_entries_are_excluded_before_fuzzy_scoring():
    from dataclasses import replace

    vm = _build()
    try:
        vm.register_entry(
            replace(_entry("busy", "Query"), availability_reason="busy"), lambda: None
        )
        vm.register_entry(_entry("ready", "Query"), lambda: None)
        assert [row.id for row in vm.filtered_entries] == ["ready"]
        vm.filter_text = "qry"
        assert [row.id for row in vm.filtered_entries] == ["ready"]
    finally:
        vm.dispose()


def test_cursor_move_outside_the_vm_methods_is_published() -> None:
    vm = _build()
    names: list[str] = []
    subscription = vm._hub.messages.subscribe(
        on_next=lambda message: (
            names.append(message.property_name)
            if isinstance(message, PropertyChangedMessage) and message.sender_object is vm
            else None
        )
    )
    try:
        for i, label in enumerate(("alpha", "alphabet", "beta")):
            vm.register_entry(_entry(f"e{i}", label), lambda: None)
        vm.open_command.execute()
        names.clear()
        vm._scored_filter.move_to_next_visible()
        assert names == ["selected_index"]
        assert vm.selected_index == 1
    finally:
        subscription.dispose()
        vm.dispose()


def test_dispose_publishes_no_projection_names() -> None:
    vm = _build()
    names: list[str] = []
    subscription = vm._hub.messages.subscribe(
        on_next=lambda message: (
            names.append(message.property_name)
            if isinstance(message, PropertyChangedMessage) and message.sender_object is vm
            else None
        )
    )
    try:
        for i, label in enumerate(("alpha", "beta")):
            vm.register_entry(_entry(f"e{i}", label), lambda: None)
        vm.open_command.execute()
        names.clear()
        vm.dispose()
        assert "filtered_entries" not in names
        assert "selected_index" not in names
    finally:
        subscription.dispose()


def test_registered_entry_is_constructed_by_the_constructed_palette() -> None:
    vm = _build()
    try:
        vm.register_entry(_entry("a", "Alpha"), lambda: None)
        rows = list(vm._inner_registry)
        assert rows
        assert all(row.is_constructed for row in rows)
    finally:
        vm.dispose()


def test_registry_rows_hold_the_entry_as_model() -> None:
    vm = _build()
    try:
        entry = _entry("a", "Alpha")
        vm.register_entry(entry, lambda: None)
        row = vm._items["a"]
        assert isinstance(row, ComponentVMOf)
        assert row.model == entry
        assert row in vm._inner_registry
    finally:
        vm.dispose()


def test_command_palette_lifecycle_drives_members() -> None:
    palette = _build()
    assert isinstance(palette, IsolatedComponentVM)
    assert palette.status is ConstructionStatus.CONSTRUCTED
    assert palette._inner_registry.status is ConstructionStatus.CONSTRUCTED
    palette.destruct()
    assert palette.status is ConstructionStatus.DESTRUCTED
    assert palette._inner_registry.status is ConstructionStatus.DESTRUCTED
    palette.construct()
    palette.dispose()
    assert palette.status is ConstructionStatus.DISPOSED
    assert palette._inner_registry.status is ConstructionStatus.DISPOSED
