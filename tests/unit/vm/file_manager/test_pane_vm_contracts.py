"""Capability contract tests for :class:`PaneVM`.

VMx does not ship a Python ``vmx.testing.conformance`` package; we
hand-roll equivalent contracts so any future PaneVM refactor stays
honest about its selectable / filterable / pageable behavior.

The three contracts mirror the structural interfaces in
:mod:`vmx.capabilities`:

- **Selectable** — toggling selection on individual entries; aggregate
  marked-count consistency; clear-selection resets everything.
- **Filterable** — applying a filter narrows the visible set; clearing
  restores the full set; cursor stays within the filtered window.
- **Pageable** — paging primitives derived from cursor + filtered count:
  ``page_size``, ``current_page``, ``total_pages``, ``goto_page``.
  PaneVM doesn't ship explicit paging today, but we exercise the
  underlying invariants (cursor clamps, count math) that any future
  paging layer will need.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import FileEntry, PathRef, PermissionDeniedError
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


async def _astream(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


async def _wide_fs() -> InMemoryFS:
    """Twelve sibling files to exercise cursor / paging logic."""
    fs = InMemoryFS()
    for i in range(12):
        await fs.write_stream(PathRef((f"file_{i:02d}.txt",)), _astream(b"x" * (i + 1)))
    return fs


async def _make_pane() -> PaneVM:
    fs = await _wide_fs()
    pane = PaneVM(provider=fs, hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    return pane


# ── Selectable contract ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_selectable_initial_no_marks() -> None:
    pane = await _make_pane()
    assert pane.marked_entries == ()
    assert pane.viewmodel.selection_count == 0
    pane.dispose()


@pytest.mark.asyncio
async def test_selectable_toggle_select_round_trip() -> None:
    pane = await _make_pane()
    pane.toggle_select_command.execute()
    assert pane.viewmodel.selection_count == 1
    pane.toggle_select_command.execute()  # second toggle un-marks
    assert pane.viewmodel.selection_count == 0
    pane.dispose()


@pytest.mark.asyncio
async def test_selectable_select_all_then_clear() -> None:
    pane = await _make_pane()
    pane.enter_multiselect_command.execute()
    pane.select_all_command.execute()
    assert pane.viewmodel.selection_count == len(pane.entries)
    pane.clear_selection_command.execute()
    assert pane.viewmodel.selection_count == 0
    pane.dispose()


@pytest.mark.asyncio
async def test_selectable_count_matches_marked_collection() -> None:
    pane = await _make_pane()
    pane.enter_multiselect_command.execute()
    # Mark three rows.
    for delta in (0, 2, 4):
        # Reset cursor to top first.
        pane.move_cursor_command.execute(-100)
        pane.move_cursor_command.execute(delta)
        pane.toggle_select_command.execute()
    assert pane.viewmodel.selection_count == 3
    assert len(pane.marked_entries) == 3
    pane.dispose()


# ── Filterable contract ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filterable_empty_filter_shows_all() -> None:
    pane = await _make_pane()
    assert pane.filter_text == ""
    assert len(pane.filtered_entries) == len(pane.entries)
    pane.dispose()


@pytest.mark.asyncio
async def test_filterable_applies_substring_match() -> None:
    pane = await _make_pane()
    pane.set_filter_command.execute("_01")
    assert [e.entry.name for e in pane.filtered_entries] == ["file_01.txt"]
    pane.dispose()


@pytest.mark.asyncio
async def test_filterable_clearing_restores_all() -> None:
    pane = await _make_pane()
    pane.set_filter_command.execute("_05")
    assert len(pane.filtered_entries) == 1
    pane.set_filter_command.execute("")
    assert len(pane.filtered_entries) == len(pane.entries)
    pane.dispose()


@pytest.mark.asyncio
async def test_filterable_cursor_stays_within_filtered_window() -> None:
    pane = await _make_pane()
    pane.set_filter_command.execute("_0")  # matches 10 entries (00-09)
    pane.move_cursor_command.execute(50)  # try to overshoot
    assert pane.cursor_index < len(pane.filtered_entries)
    pane.dispose()


@pytest.mark.asyncio
async def test_filterable_is_case_insensitive() -> None:
    pane = await _make_pane()
    pane.set_filter_command.execute("FILE_03")
    assert [e.entry.name for e in pane.filtered_entries] == ["file_03.txt"]
    pane.dispose()


# ── Pageable contract (derived; not a first-class feature yet) ────────────


@pytest.mark.asyncio
async def test_pageable_cursor_advances_by_one() -> None:
    pane = await _make_pane()
    start = pane.cursor_index
    pane.move_cursor_command.execute(1)
    assert pane.cursor_index == start + 1
    pane.dispose()


@pytest.mark.asyncio
async def test_pageable_cursor_clamps_at_zero() -> None:
    pane = await _make_pane()
    pane.move_cursor_command.execute(-100)
    assert pane.cursor_index == 0
    pane.dispose()


@pytest.mark.asyncio
async def test_pageable_cursor_clamps_at_end() -> None:
    pane = await _make_pane()
    pane.move_cursor_command.execute(100)
    assert pane.cursor_index == len(pane.filtered_entries) - 1
    pane.dispose()


@pytest.mark.asyncio
async def test_pageable_total_count_consistent_after_filter() -> None:
    pane = await _make_pane()
    total = len(pane.entries)
    pane.set_filter_command.execute("file_0")
    assert len(pane.filtered_entries) == 10  # file_00..file_09
    pane.set_filter_command.execute("")
    assert len(pane.filtered_entries) == total
    pane.dispose()


# ── State contract (PaneState invariants) ──────────────────────────────────


@pytest.mark.asyncio
async def test_state_invariant_idle_after_initial_setup() -> None:
    pane = await _make_pane()
    assert pane.state == PaneState.IDLE
    pane.dispose()


@pytest.mark.asyncio
async def test_two_pane_recovery_stages_all_before_any_commit() -> None:
    class _RecoveryFS(InMemoryFS):
        def __init__(self, *, fails: bool = False) -> None:
            super().__init__()
            self.fails = fails
            self.list_calls: list[PathRef] = []

        async def list(self, path: PathRef) -> list[FileEntry]:
            self.list_calls.append(path)
            if self.fails:
                raise PermissionDeniedError("denied")
            return await super().list(path)

    left = await _make_pane()
    right = await _make_pane()
    left_before = (left.provider, left.entries, left.state, left.path)
    right_before = (right.provider, right.entries, right.state, right.path)
    new_left = _RecoveryFS()
    denied_right = _RecoveryFS(fails=True)

    left_stage = await left.stage_provider_recovery(
        new_left,
        path=left.path,
        identity_label="aws · engineering · us-east-1",
        path_protocol="s3:",
        connection_key=("aws", "engineering"),
    )
    with pytest.raises(PermissionDeniedError):
        await right.stage_provider_recovery(
            denied_right,
            path=right.path,
            identity_label="aws · engineering · us-east-1",
            path_protocol="s3:",
            connection_key=("aws", "engineering"),
        )

    assert (left.provider, left.entries, left.state, left.path) == left_before
    assert (right.provider, right.entries, right.state, right.path) == right_before
    assert left.can_commit_provider_recovery(left_stage)

    new_right = _RecoveryFS()
    right_stage = await right.stage_provider_recovery(
        new_right,
        path=right.path,
        identity_label="aws · engineering · us-east-1",
        path_protocol="s3:",
        connection_key=("aws", "engineering"),
    )
    assert left.can_commit_provider_recovery(left_stage)
    assert right.can_commit_provider_recovery(right_stage)

    left.commit_provider_recovery(left_stage)
    right.commit_provider_recovery(right_stage)

    assert new_left.list_calls == [PathRef(())]
    assert denied_right.list_calls == [PathRef(())]
    assert new_right.list_calls == [PathRef(())]
    assert left.provider is new_left
    assert right.provider is new_right
    left.dispose()
    right.dispose()
