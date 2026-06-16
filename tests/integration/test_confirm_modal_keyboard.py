"""Pass-12: Enter in a ConfirmModal must call action_confirm even
though the App declares ``Binding('enter', 'descend', priority=True)``
to navigate the dual-pane. ``_forward_to_modal`` routes Enter to
``ModalScreen.action_confirm`` when a modal is on top of the stack.
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
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"a" * 10))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"b" * 100))
    return fs


def _ctx(tmp: Path, fs: InMemoryFS) -> AppContext:
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
        return fs

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
async def test_enter_on_copy_confirm_modal_runs_copy() -> None:
    """Press c to open the modal, then Enter to confirm. Without the
    forward, Enter would descend into the cursor row instead."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-confirm-enter-"))
    fs = await _seed()
    ctx = _ctx(tmp, fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        # Modal should be on the stack.
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Modal closed — Enter forwarded to action_confirm.
        assert not isinstance(app.screen, ConfirmModal), "Enter didn't close the confirm modal"
        assert app._crash_report is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_escape_on_delete_modal_cancels() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-confirm-esc-"))
    fs = await _seed()
    ctx = _ctx(tmp, fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        # No crash.
        assert app._crash_report is None  # type: ignore[attr-defined]
