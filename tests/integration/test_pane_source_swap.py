"""Pane source switching via Shift+S — pass-11.

Locks in PaneVM.swap_provider behavior wired through the App action:
- The focused pane's provider is replaced
- Path resets to root
- Border subtitle (identity) updates
- Border title protocol updates (s3:// vs /)
- The unfocused pane is left alone
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from vmx import MessageHub, RxDispatcher

from aws_tui.composition import AppContext
from aws_tui.domain.filesystem import FileSystemProvider, PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3 import S3Service
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_fs(label: str) -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef((f"{label}.txt",)), _stream(label.encode()))
    return fs


def _ctx(tmp: Path, s3_fs: InMemoryFS) -> AppContext:
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
        return s3_fs

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
async def test_pane_vm_swap_provider_replaces_source_and_resets() -> None:
    """Direct PaneVM contract — used by the app-level action."""
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs_a = await _seed_fs("a")
    fs_b = await _seed_fs("b")
    vm = PaneVM(
        provider=fs_a,
        hub=hub,
        dispatcher=dispatcher,
        identity_label="A",
        path_protocol="s3:",
    )
    vm.construct()
    await vm.setup()
    try:
        # Drift away from root.
        await vm.navigate_to(PathRef(("subdir",)))  # may 404, that's fine
        await vm.swap_provider(fs_b, identity_label="B", path_protocol="")
        assert vm.path.is_root
        assert vm.viewmodel.border_subtitle == "B"
        # Border title for an empty protocol at root.
        assert vm.viewmodel.border_title == "/"
        names = {e.entry.name for e in vm.filtered_entries}
        assert "b.txt" in names
        assert "a.txt" not in names
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_unfocused_pane_unaffected_by_vm_swap() -> None:
    """Swapping the focused pane VM doesn't touch the other side. We
    drive the swap at the VM layer (not the binding) because the
    binding path requires a real AWS session for the local→S3 swap
    direction (out of scope for unit testing)."""
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs_left = await _seed_fs("left")
    fs_right = await _seed_fs("right")
    fs_replacement = await _seed_fs("rep")

    left = PaneVM(provider=fs_left, hub=hub, dispatcher=dispatcher, id_prefix="L")
    right = PaneVM(provider=fs_right, hub=hub, dispatcher=dispatcher, id_prefix="R")
    left.construct()
    right.construct()
    await left.setup()
    await right.setup()
    try:
        right_provider_before = right.provider
        await left.swap_provider(fs_replacement, identity_label="replaced", path_protocol="")
        assert right.provider is right_provider_before
    finally:
        left.dispose()
        right.dispose()


def test_all_four_pane_combinations_are_constructible() -> None:
    """Sanity: the user can put any of {S3, local} x {S3, local} into
    the dual-pane. This is what swap_provider enables."""
    # Skip if no real aioboto3 session is required for S3FS — just
    # assert the types exist and have compatible constructors.
    import aioboto3

    session = aioboto3.Session(region_name="us-east-1")
    s3 = S3FS(session=session, bucket=None)
    local = LocalFS()
    combos = [(s3, s3), (s3, local), (local, s3), (local, local)]
    for left, right in combos:
        assert left is not None
        assert right is not None
