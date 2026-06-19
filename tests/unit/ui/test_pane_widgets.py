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
            # Two pauses: Pane.on_mount now defers _render_body via
            # call_after_refresh so #pane-body is fully mounted first.
            # One pilot.pause() ticks the event loop once; on slow
            # event loops (notably Windows CI runners) the deferred
            # render hasn't completed by the time control returns to
            # this test, so the second pause flushes it.
            await pilot.pause()
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
            # See note in test_pane_mounts_and_populates_rows above —
            # the deferred _render_body needs a second pump on Windows.
            await pilot.pause()
            await pilot.pause()
            # No rows; instead, a placeholder.
            rows = app.query(EntryRow)
            assert len(rows) == 0
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_pane_dynamic_mount_with_unreachable_state_does_not_crash() -> None:
    """Regression for the boot-time MountError when the pane lands in a
    non-IDLE state and is mounted dynamically (not yielded from compose).

    The real app at ``AwsTuiApp._mount_initial_service_view`` calls
    ``host.mount(DualPane(...))`` from inside the App's own ``on_mount``.
    That dynamic mount sequence — Pane gets added to the tree, its
    ``compose`` yields ``VerticalScroll(id='pane-body')``, then
    ``Pane.on_mount`` fires — leaves ``pane-body`` mid-mount when
    ``Pane.on_mount`` runs. Pre-fix, ``on_mount`` called
    ``self._render_body()`` synchronously, which immediately tried to
    ``body.mount(Static(placeholder_text))`` — raising
    ``MountError: Can't mount widget(s) before VerticalScroll(id='pane-body')
    is mounted`` whenever the pane had a placeholder to render (any
    non-IDLE state at boot — UNREACHABLE / FORBIDDEN / AUTH_REQUIRED /
    EMPTY / LOADING / ERROR).

    The user-visible trigger: an S3-compatible connection whose endpoint
    is unreachable (e.g., MinIO not running). The connection probe at
    boot drives the pane to ``UNREACHABLE``, the placeholder text is
    populated, and the app crashes on first render.

    Fix: ``on_mount`` now defers ``_render_body`` via
    ``call_after_refresh`` — same pattern every other ``_render_body``
    caller already uses.
    """
    import tempfile
    from pathlib import Path

    from textual.containers import Container

    from aws_tui.domain.filesystem import (
        FileEntry,
        FileSystemProvider,
        ProviderUnreachableError,
    )
    from aws_tui.vm.file_manager.pane_vm import PaneState

    class _UnreachableProvider(FileSystemProvider):
        """Test provider that always raises ProviderUnreachableError.

        Models the user's scenario: an S3-compatible endpoint (MinIO,
        R2, etc.) that's offline at app boot.
        """

        async def list(self, _path: PathRef) -> tuple[FileEntry, ...]:
            raise ProviderUnreachableError("Could not connect to the endpoint URL")

        async def stat(self, _path: PathRef) -> FileEntry:  # pragma: no cover
            raise ProviderUnreachableError("Could not connect")

        async def read_stream(self, _path: PathRef):  # pragma: no cover
            raise ProviderUnreachableError("Could not connect")
            yield b""

        async def write_stream(self, _path, _chunks, *, _progress=None) -> None:  # pragma: no cover
            raise ProviderUnreachableError("Could not connect")

        async def delete(self, _path: PathRef) -> None:  # pragma: no cover
            raise ProviderUnreachableError("Could not connect")

        async def mkdir(self, _path: PathRef) -> None:  # pragma: no cover
            raise ProviderUnreachableError("Could not connect")

        async def rename(self, _src: PathRef, _dst: PathRef) -> None:  # pragma: no cover
            raise ProviderUnreachableError("Could not connect")

    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    left_vm = PaneVM(
        provider=_UnreachableProvider(),
        hub=hub,
        dispatcher=dispatcher,
        id_prefix="pane.left",
    )
    right_vm = PaneVM(
        provider=_UnreachableProvider(),
        hub=hub,
        dispatcher=dispatcher,
        id_prefix="pane.right",
    )
    left_vm.construct()
    right_vm.construct()
    await left_vm.setup()  # drives left into UNREACHABLE
    await right_vm.setup()  # drives right into UNREACHABLE
    assert left_vm.state is PaneState.UNREACHABLE
    assert left_vm.viewmodel.placeholder_text is not None  # there IS something to mount

    # The real-app crash path is:
    #   AwsTuiApp.on_mount → host.mount(DualPane(...))
    #   DualPane.compose yields Horizontal containing two Panes
    #   Pane.compose yields VerticalScroll(id='pane-body')
    #   Pane.on_mount fires while pane-body is still mid-mount
    # Pre-fix: synchronous _render_body call → body.mount(Static) → MountError.
    dual_vm = DualPaneVM(
        left=left_vm,
        right=right_vm,
        hub=hub,
        dispatcher=dispatcher,
        transfer_journal=TransferJournal(base_dir=Path(tempfile.mkdtemp(prefix="aws-tui-test-"))),
    )
    dual_vm.construct()
    try:

        class _App(App[None]):
            """Mounts the DualPane dynamically from on_mount, exactly
            mirroring AwsTuiApp._mount_initial_service_view."""

            def compose(self) -> ComposeResult:
                yield Container(id="host")

            async def on_mount(self) -> None:
                host = self.query_one("#host", Container)
                host.mount(DualPane(dual_vm, hub=hub, id="content-dual-pane"))

        app = _App()
        async with app.run_test(size=(120, 30)) as pilot:
            # Double pause for the same reason described in the other
            # tests in this file — the deferred _render_body needs a
            # second event-loop pump on slow runners (Windows CI).
            await pilot.pause()
            await pilot.pause()
            # No exception => no MountError on the deferred render.
            left_placeholder = app.query("#pane-left .pane-placeholder")
            right_placeholder = app.query("#pane-right .pane-placeholder")
            assert len(left_placeholder) == 1, "left pane should have one placeholder Static"
            assert len(right_placeholder) == 1, "right pane should have one placeholder Static"
            assert "unreachable" in str(left_placeholder[0].render())
    finally:
        dual_vm.dispose()
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
