"""Smoke: action_copy + action_delete must NOT crash the running app.

User-reported pass-9 regression: both commands escalate to the crash
modal during the launch flow. We can't reproduce the real S3 backend
here, but we can exercise the full UI path (focus pane → mark → press
key → confirm modal → run async op) against in-memory providers and
ensure no exception escapes.
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
from aws_tui.ui.widgets.pane import EntryRow, Pane
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_left() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"alpha-content"))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"beta"))
    return fs


def _build_ctx(tmp: Path, fs_left: FileSystemProvider) -> AppContext:
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

    def _factory(_connection: Connection) -> FileSystemProvider:
        return fs_left

    s3_service = S3Service(
        aws_session=aws_session,
        transfer_journal=journal,
        hub=hub,
        dispatcher=dispatcher,
        s3_fs_factory=_factory,
    )
    s3_service._local_root = tmp  # type: ignore[attr-defined]

    registry = ServiceRegistry()
    registry.register(cast(Service, s3_service))

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
async def test_copy_action_with_confirm_does_not_crash() -> None:
    """Press 'c', confirm in the modal, verify no exception bubbles."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-copy-"))
    fs = await _seed_left()
    ctx = _build_ctx(tmp, fs)

    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # Make sure the panes mounted with entries.
        panes = list(app.query(Pane))
        assert len(panes) == 2
        rows = list(app.query(EntryRow))
        assert len(rows) > 0

        # Press 'c' — this opens ConfirmModal.
        await pilot.press("c")
        await pilot.pause()
        # Confirm with Enter.
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        # Verify the app is still alive — no crash modal pushed, no
        # unhandled exception captured in the AwsTuiApp.crash_report.
        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"Copy command crashed the app: {app._crash_report}"  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_delete_action_with_confirm_does_not_crash() -> None:
    """Press 'd', confirm in the modal, verify no exception bubbles."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-delete-"))
    fs = await _seed_left()
    ctx = _build_ctx(tmp, fs)

    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"Delete command crashed the app: {app._crash_report}"  # type: ignore[attr-defined]
        )
