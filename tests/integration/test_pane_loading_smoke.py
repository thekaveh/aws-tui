"""Smoke: mount the full AwsTuiApp and verify panes load entries.

Catches the kind of pane-loading regression that snapshot/unit tests
miss because they exercise sub-components, not the full app composition
(`AwsTuiApp.on_mount` → `switch_connection_with` → `switch_service` →
`_mount_initial_service_view`).
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
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_fs() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"alpha"))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"beta"))
    await fs.mkdir(PathRef(("data",)))
    return fs


@pytest.mark.asyncio
async def test_panes_populate_with_entries_after_mount() -> None:
    """Mounting the full app with a wired S3Service must surface entry rows
    in both panes. Regression guard against the failure mode where the
    cursor/path/border refactor + StatusBar removal left both panes
    empty in real launches even though unit tests passed."""

    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-smoke-"))

    # Pre-seed the S3 provider so build_vm gets a populated FS. The local
    # pane reads the real OS filesystem under the rooted directory below.
    s3_fs = await _seed_fs()

    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()

    log = LogSink(base_dir=tmp / "log")
    config_store = ConfigStore(path=tmp / "config.toml")
    keymap = KeymapStore()
    theme = ThemeStore()
    aws_session = AwsSession()
    journal = TransferJournal(base_dir=tmp / "transfers")

    # ConnectionResolver needs ~/.aws/* paths to exist or be skipped. Force
    # an empty list so we drive a synthetic connection via the registry.
    resolver = ConnectionResolver(
        config_store=config_store,
        aws_config_path=tmp / "aws-config",
        aws_credentials_path=tmp / "aws-credentials",
    )

    # Wire an S3Service with a fixed provider so build_vm doesn't try to
    # reach AWS.
    def _factory(_connection: Connection) -> FileSystemProvider:
        return s3_fs

    s3_service = S3Service(
        aws_session=aws_session,
        transfer_journal=journal,
        hub=hub,
        dispatcher=dispatcher,
        s3_fs_factory=_factory,
    )
    # Use the local_fs for the right pane regardless of host filesystem.
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

    # Provide one connection so _resolve_initial_connection lands on it.
    config_store.path.write_text(
        '[defaults]\nconnection = "test"\n\n'
        '[connections.test]\nkind = "aws"\nprofile = "test"\nregion = "us-east-1"\n'
    )

    s3_connections_vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=config_store,
        hub=hub,
        dispatcher=dispatcher,
    )

    ctx = AppContext(
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
        s3_connections_vm=s3_connections_vm,
    )

    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        panes = list(app.query(Pane))
        assert len(panes) == 2, f"Expected 2 panes, got {len(panes)}"

        # Both panes should be in IDLE state with entries populated, NOT a
        # placeholder.
        for pane in panes:
            vm = pane.vm.viewmodel
            assert vm.placeholder_text is None, (
                f"Pane {pane.id}: unexpected placeholder {vm.placeholder_text!r}, "
                f"state={pane.vm.state}, error={vm.error_text}"
            )

        rows = list(app.query(EntryRow))
        assert len(rows) > 0, "Expected at least one EntryRow visible across panes"
