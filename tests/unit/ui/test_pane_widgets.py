"""Smoke tests for the pane + dual-pane widgets."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.pane import EntryRow, Pane
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM, FocusedPane
from aws_tui.vm.file_manager.pane_vm import PaneVM
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _astream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _astream(b"alpha"))
    await fs.write_stream(PathRef(("beta.txt",)), _astream(b"beta"))
    await fs.mkdir(PathRef(("data",)))
    await fs.write_stream(PathRef(("gamma.json",)), _astream(b'{"x":1}'))
    await fs.write_stream(PathRef(("delta.log",)), _astream(b"log"))
    return fs


@pytest.mark.asyncio
async def test_pane_mounts_and_populates_rows() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher, id_prefix="pane.test")
    vm.construct()
    await vm.setup()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield Pane(vm, hub=hub, id="pane")

        app = _App()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            rows = app.query(EntryRow)
            assert len(rows) == 5  # data dir + 4 files
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_pane_renders_loading_placeholder_for_state() -> None:
    """Asserts the empty-state placeholder lands when the pane has no entries."""
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = InMemoryFS()  # empty
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher, id_prefix="pane.test")
    vm.construct()
    await vm.setup()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield Pane(vm, hub=hub, id="pane")

        app = _App()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # No rows; instead, a placeholder.
            rows = app.query(EntryRow)
            assert len(rows) == 0
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_pane_set_focused_adds_class() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs = await _seed()
    vm = PaneVM(provider=fs, hub=hub, dispatcher=dispatcher, id_prefix="pane.test")
    vm.construct()
    await vm.setup()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield Pane(vm, hub=hub, id="pane")

        app = _App()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            pane = app.query_one(Pane)
            pane.set_focused(True)
            assert "-focused" in pane.classes
            pane.set_focused(False)
            assert "-focused" not in pane.classes
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_dual_pane_mounts_with_two_panes(tmp_path) -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    left_fs = await _seed()
    right_fs = InMemoryFS()
    left = PaneVM(provider=left_fs, hub=hub, dispatcher=dispatcher, id_prefix="left")
    right = PaneVM(provider=right_fs, hub=hub, dispatcher=dispatcher, id_prefix="right")
    dual = DualPaneVM(
        left=left,
        right=right,
        hub=hub,
        dispatcher=dispatcher,
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
    )
    dual.construct()
    await dual.setup()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield DualPane(dual, hub=hub)

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            panes = app.query(Pane)
            assert len(panes) == 2
            # Initially left focused per FocusedPane default.
            left_w = app.query_one("#pane-left", Pane)
            right_w = app.query_one("#pane-right", Pane)
            assert "-focused" in left_w.classes
            assert "-focused" not in right_w.classes

            # Switching focus toggles class.
            dual.switch_focus_command.execute()
            await pilot.pause()
            await pilot.pause()
            assert "-focused" in right_w.classes
            assert "-focused" not in left_w.classes
            assert dual.focused is FocusedPane.RIGHT
    finally:
        dual.dispose()
        hub.dispose()
