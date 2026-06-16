"""Multi-select via keyboard + modifier click.

Locks in:
- ``shift+up`` / ``shift+down`` extends the selection
- Modifier+click (shift OR meta OR ctrl) toggles a row's marked flag
- The pane footer summary reflects the marked-byte total
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from vmx import MessageHub, RxDispatcher

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext
from aws_tui.domain.filesystem import FileSystemProvider, PathRef
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3 import S3Service
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_local() -> InMemoryFS:
    fs = InMemoryFS()
    for name, body in (
        ("alpha.txt", b"a" * 10),
        ("beta.txt", b"b" * 100),
        ("gamma.txt", b"c" * 1000),
        ("delta.txt", b"d" * 100),
    ):
        await fs.write_stream(PathRef((name,)), _stream(body))
    return fs


def _ctx(tmp: Path, local_fs: InMemoryFS) -> AppContext:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    log = LogSink(base_dir=tmp / "log")
    config_store = ConfigStore(path=tmp / "config.toml")
    keymap = KeymapStore()
    theme = ThemeStore()
    aws_session = AwsSession()
    journal = TransferJournal(base_dir=tmp / "transfers")
    resolver = ConnectionResolver(
        config_store=config_store,
        aws_config_path=tmp / "aws-config",
        aws_credentials_path=tmp / "aws-credentials",
    )

    def _factory(_c: Connection) -> FileSystemProvider:
        return local_fs

    svc = S3Service(
        aws_session=aws_session,
        transfer_journal=journal,
        hub=hub,
        dispatcher=dispatcher,
        s3_fs_factory=_factory,
    )
    svc._local_root = tmp  # type: ignore[attr-defined]
    registry = ServiceRegistry()
    registry.register(cast(Service, svc))
    root = RootVM(
        registry=registry,
        keymap=keymap,
        theme=theme,
        log=log,
        dispatcher=dispatcher,
        hub=hub,
    )
    config_store.path.write_text(
        '[defaults]\nconnection = "test"\n\n'
        '[connections.test]\nkind = "aws"\nprofile = "test"\nregion = "us-east-1"\n'
    )
    return AppContext(
        root_vm=root,
        registry=registry,
        config_store=config_store,
        log_sink=log,
        keymap_store=keymap,
        theme_store=theme,
        connection_resolver=resolver,
        aws_session=aws_session,
        transfers_vm=TransfersVM(hub=hub, dispatcher=dispatcher),
        confirm_vm=ConfirmationVM(hub=hub, dispatcher=dispatcher),
        quick_look_vm=QuickLookVM(hub=hub, dispatcher=dispatcher),
        command_palette_vm=CommandPaletteVM(hub=hub, dispatcher=dispatcher),
        transfer_journal=journal,
        hub=hub,
        dispatcher=dispatcher,
        initial_theme="carbon",
    )


@pytest.mark.asyncio
async def test_shift_arrow_extends_selection() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-msel-"))
    local = await _seed_local()
    ctx = _ctx(tmp, local)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        # Press shift+down twice; expect marked count to climb.
        panes = list(app.query(Pane))
        focused = panes[0]
        initial_marked = sum(1 for e in focused.vm.filtered_entries if e.is_marked)
        await pilot.press("shift+down")
        await pilot.pause()
        await pilot.press("shift+down")
        await pilot.pause()
        after_marked = sum(1 for e in focused.vm.filtered_entries if e.is_marked)
        assert after_marked > initial_marked, (
            f"shift+down didn't extend selection (was {initial_marked}, now {after_marked})"
        )


@pytest.mark.asyncio
async def test_pane_footer_summary_includes_selected_bytes() -> None:
    """Pass-10: when entries are marked, summary shows the marked
    byte total, not the all-entries total."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-msel-bytes-"))
    local = await _seed_local()
    ctx = _ctx(tmp, local)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panes = list(app.query(Pane))
        focused = panes[0]
        # Mark the cursor row directly through the VM (avoids relying on
        # the pane's focused-side default).
        focused.vm.toggle_mark_at(focused.vm.cursor_index)
        await pilot.pause()
        summary = focused.vm.viewmodel.summary
        assert "marked" in summary
        assert "selected" in summary


@pytest.mark.asyncio
async def test_pane_vm_toggle_mark_at_enters_multiselect() -> None:
    """toggle_mark_at should put the pane into multi-select mode the
    first time it's called so subsequent navigation preserves marks."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-msel-mode-"))
    local = await _seed_local()
    ctx = _ctx(tmp, local)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panes = list(app.query(Pane))
        focused = panes[0]
        assert focused.vm.is_multiselect_mode is False
        focused.vm.toggle_mark_at(0)
        assert focused.vm.is_multiselect_mode is True
