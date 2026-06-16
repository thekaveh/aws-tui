"""Regression: pressing 's' collapses/expands the services rail."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

import pytest
from vmx import MessageHub, RxDispatcher

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext
from aws_tui.domain.filesystem import FileSystemProvider
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3 import S3Service
from aws_tui.ui.widgets.services_menu import ServicesMenu
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from tests.unit.domain._in_memory_fs import InMemoryFS


def _build_ctx(tmp: Path) -> AppContext:
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
        return InMemoryFS()

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
async def test_s_key_toggles_services_collapsed_state() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-svc-"))
    ctx = _build_ctx(tmp)

    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        menu = app.query_one(ServicesMenu)
        # Pass-10 default: starts collapsed.
        assert menu.is_collapsed is True

        await pilot.press("s")
        await pilot.pause()
        assert menu.is_collapsed is False, "'s' didn't expand the services rail"

        await pilot.press("s")
        await pilot.pause()
        assert menu.is_collapsed is True, "'s' didn't collapse the rail again"
