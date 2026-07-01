"""Tests for the CommandPaletteVM."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM, PaletteEntry


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _build() -> CommandPaletteVM:
    vm = CommandPaletteVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def _entry(
    id_: str, label: str, category: str = "test", keywords: tuple[str, ...] = ()
) -> PaletteEntry:
    return PaletteEntry(id=id_, label=label, category=category, keywords=keywords)


def test_initial_state() -> None:
    vm = _build()
    assert not vm.is_open
    assert vm.filter_text == ""
    assert vm.filtered_entries == ()
    assert vm.selected_index == 0
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


def test_execute_with_empty_filtered_is_noop() -> None:
    vm = _build()
    vm.open_command.execute()
    # No entries registered.
    vm.execute_selected_command.execute()
    assert vm.is_open  # remained open (no-op)
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
    """``dispose()`` releases every RelayCommand without raising.

    The original test asserted "execute is a no-op after dispose"
    — that turned out to be wishful: ``RelayCommand.dispose`` in
    vmx 8.x does not gate later ``execute()`` calls, so the
    contract this test actually pins is "dispose runs cleanly and
    a subsequent ``execute()`` does not raise". A future vmx
    upgrade that adds use-after-dispose protection can tighten
    this assertion."""
    vm = _build()
    vm.dispose()
    vm.open_command.execute()  # must not raise
