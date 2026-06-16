"""Chrome composition + hint legend content — locks in pass-7 through
pass-12 visual decisions:

- ``BrandBanner`` is mounted at the top
- The old top-strip ``StatusBar`` widget is NOT present (identity moved
  to the pane border subtitle in pass-7)
- ``ServicesMenu`` starts collapsed (pass-10)
- ``HintLegend`` includes the action ids the user can reach via
  bindings: t themes, T cycle, S swap source, c copy, d delete,
  enter open, tab switch, r refresh, ? help, q quit
- Footer chips use the themable ``.hint-key`` / ``.hint-label`` /
  ``.hint-sep`` classes (not Rich inline styles)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

import pytest
from textual.widgets import Static
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
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.services_menu import ServicesMenu
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from tests.unit.domain._in_memory_fs import InMemoryFS


def _ctx(tmp: Path) -> AppContext:
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
        return InMemoryFS()

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


def _strip_text(host: HintLegend) -> str:
    return " ".join(str(s.render()) for s in host.query(Static))


@pytest.mark.asyncio
async def test_chrome_has_banner_no_statusbar() -> None:
    """StatusBar was removed in pass-7. BrandBanner mounts at top."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-chrome-"))
    ctx = _ctx(tmp)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert len(app.query(BrandBanner)) == 1
        # No StatusBar widget should be mounted.
        from aws_tui.ui.widgets.status_bar import StatusBar

        assert len(app.query(StatusBar)) == 0


@pytest.mark.asyncio
async def test_services_menu_starts_collapsed() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-svc-default-"))
    ctx = _ctx(tmp)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        menu = app.query_one(ServicesMenu)
        assert menu.is_collapsed is True


@pytest.mark.asyncio
async def test_hint_legend_contains_all_expected_action_chips() -> None:
    """Every action the user might reach for must be discoverable in
    the bottom strip."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-hints-"))
    ctx = _ctx(tmp)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        legend = app.query_one(HintLegend)
        text = _strip_text(legend)
        # Action labels from hint_legend_vm _ACTION_LABELS.
        for label in (
            "open",
            "switch",
            "copy",
            "delete",
            "refresh",
            "themes",
            "cycle",
            "swap src",
            "help",
            "quit",
        ):
            assert label in text, f"hint legend missing chip: {label!r}"


@pytest.mark.asyncio
async def test_hint_legend_chips_use_themable_css_classes() -> None:
    """Pass-10/11 split each chip into ``.hint-key`` and ``.hint-label``
    Statics so theme tcss can color them. Verify the CSS classes are
    actually applied."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-hint-css-"))
    ctx = _ctx(tmp)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        legend = app.query_one(HintLegend)
        statics = list(legend.query(Static))
        assert statics, "legend should compose into Static chips"
        # At least one of each role should exist (each chip = key + label).
        has_key = any("hint-key" in (s.classes or "") for s in statics)
        has_label = any("hint-label" in (s.classes or "") for s in statics)
        assert has_key, "no .hint-key Statics in HintLegend"
        assert has_label, "no .hint-label Statics in HintLegend"
