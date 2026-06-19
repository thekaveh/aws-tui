"""Tests for DualPaneVM."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM, FocusedPane
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.messages import TransferProgressMessage
from tests.unit.domain._in_memory_fs import InMemoryFS


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


async def _astream(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


async def _seed_left() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _astream(b"alpha-bytes"))
    await fs.write_stream(PathRef(("beta.txt",)), _astream(b"beta-bytes"))
    return fs


async def _seed_right() -> InMemoryFS:
    fs = InMemoryFS()
    return fs


async def _make_dual(tmp_path: Path) -> tuple[DualPaneVM, MessageHub[Message]]:
    hub = _hub()
    left_fs = await _seed_left()
    right_fs = await _seed_right()
    left = PaneVM(provider=left_fs, hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="left")
    right = PaneVM(provider=right_fs, hub=hub, dispatcher=NULL_DISPATCHER, id_prefix="right")
    journal = TransferJournal(base_dir=tmp_path / "journal")
    dp = DualPaneVM(
        left=left,
        right=right,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        transfer_journal=journal,
    )
    dp.construct()
    await dp.setup()
    return dp, hub


@pytest.mark.asyncio
async def test_dual_construct_dispose(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    assert dp.focused == FocusedPane.LEFT
    dp.dispose()
    assert dp.status == ConstructionStatus.DISPOSED


@pytest.mark.asyncio
async def test_dual_switch_focus(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    dp.switch_focus_command.execute()
    assert dp.focused == FocusedPane.RIGHT
    dp.switch_focus_command.execute()
    assert dp.focused == FocusedPane.LEFT
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_copy_across_publishes_transfer_progress(tmp_path: Path) -> None:
    dp, hub = await _make_dual(tmp_path)
    received: list[TransferProgressMessage] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(m) if isinstance(m, TransferProgressMessage) else None
    )
    # Mark alpha.txt on the left pane.
    dp.left.enter_multiselect_command.execute()
    dp.left.toggle_select_command.execute()
    assert dp.left.marked_entries[0].entry.name == "alpha.txt"
    await dp.copy_across()
    # Right pane should now have alpha.txt.
    names = [e.entry.name for e in dp.right.entries]
    assert "alpha.txt" in names
    # At least one progress and one completed message must have fired.
    states = [m.state for m in received]
    assert "running" in states
    assert "completed" in states
    # Pin the producer-side URI shape that ``TransfersVM._infer_direction``
    # depends on: both panes are LocalFS in this test, so labels must
    # start with the absolute filesystem ``/`` (no scheme prefix).
    # Locks in the V4-001 fix end-to-end on the real flow, not just
    # the mocked consumer test in test_transfers.py.
    labeled = [m for m in received if m.source_label and m.destination_label]
    assert labeled, "expected at least one progress message to carry source/dest labels"
    sample = labeled[0]
    assert sample.source_label.startswith("/"), (
        f"local pane source must emit unprefixed posix path, got {sample.source_label!r}"
    )
    assert sample.destination_label.startswith("/"), (
        f"local pane destination must emit unprefixed posix path, got {sample.destination_label!r}"
    )
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_move_across_deletes_source(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    dp.left.enter_multiselect_command.execute()
    dp.left.toggle_select_command.execute()  # marks alpha.txt
    await dp.move_across()
    left_names = [e.entry.name for e in dp.left.entries]
    right_names = [e.entry.name for e in dp.right.entries]
    assert "alpha.txt" not in left_names
    assert "alpha.txt" in right_names
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_delete_in_focused(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    dp.left.enter_multiselect_command.execute()
    dp.left.select_all_command.execute()
    await dp.delete_in_focused()
    assert dp.left.entries == ()
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_focused_pane_property(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    assert dp.focused_pane is dp.left
    assert dp.other_pane is dp.right
    dp.switch_focus_command.execute()
    assert dp.focused_pane is dp.right
    assert dp.other_pane is dp.left
    dp.dispose()


@pytest.mark.asyncio
async def test_dual_copy_command_requires_marks(tmp_path: Path) -> None:
    dp, _ = await _make_dual(tmp_path)
    assert not dp.copy_across_command.can_execute()
    dp.left.enter_multiselect_command.execute()
    dp.left.select_all_command.execute()
    assert dp.copy_across_command.can_execute()
    dp.dispose()
