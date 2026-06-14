"""Composition root — wires every layer together.

This module deliberately lives outside the strict five-layer tree
(``src/aws_tui/{infra,domain,vm,services,ui}/``) so it may legally import
from every layer. The layer-rule check (``scripts/check-layers.sh``)
only walks the five layer folders; ``composition.py`` and ``app.py`` are
the only two top-level files allowed to know about all of them.

The composition builds:

- ``ConfigStore``, ``LogSink``, ``KeymapStore``, ``ThemeStore`` (infra)
- ``ConnectionResolver``, ``AwsSession`` (infra; aware of boto3)
- ``ServiceRegistry`` with ``S3Service`` registered (services)
- ``RootVM`` with the four chrome VMs and the file-manager VMs ready
  to be filled by ``RootVM.switch_service`` (vm)
- ``AppContext`` — the bag the Textual ``AwsTuiApp`` consumes
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from vmx import Message, MessageHub, RxDispatcher
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3.service import S3Service
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry


class AppContext:
    """The bag of pre-wired objects the Textual app consumes."""

    __slots__ = (
        "aws_session",
        "command_palette_vm",
        "config_store",
        "confirm_vm",
        "connection_resolver",
        "dispatcher",
        "hub",
        "initial_theme",
        "keymap_store",
        "log_sink",
        "quick_look_vm",
        "registry",
        "root_vm",
        "theme_store",
        "transfer_journal",
        "transfers_vm",
    )

    def __init__(
        self,
        *,
        root_vm: RootVM,
        registry: ServiceRegistry,
        config_store: ConfigStore,
        log_sink: LogSink,
        keymap_store: KeymapStore,
        theme_store: ThemeStore,
        connection_resolver: ConnectionResolver,
        aws_session: AwsSession,
        transfers_vm: TransfersVM,
        confirm_vm: ConfirmationVM,
        quick_look_vm: QuickLookVM,
        command_palette_vm: CommandPaletteVM,
        transfer_journal: TransferJournal,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        initial_theme: str,
    ) -> None:
        self.root_vm = root_vm
        self.registry = registry
        self.config_store = config_store
        self.log_sink = log_sink
        self.keymap_store = keymap_store
        self.theme_store = theme_store
        self.connection_resolver = connection_resolver
        self.aws_session = aws_session
        self.transfers_vm = transfers_vm
        self.confirm_vm = confirm_vm
        self.quick_look_vm = quick_look_vm
        self.command_palette_vm = command_palette_vm
        self.transfer_journal = transfer_journal
        self.hub = hub
        self.dispatcher = dispatcher
        self.initial_theme = initial_theme


def build_app_context(
    *,
    config_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> AppContext:
    """Build the full ``AppContext`` for a fresh aws-tui session.

    Parameters
    ----------
    config_dir:
        Override for ``~/.config/aws-tui`` (used by tests).
    cache_dir:
        Override for ``~/.cache/aws-tui`` (used by tests).
    """
    # ── Infra ──────────────────────────────────────────────────────────────
    if config_dir is None:
        config_dir = Path.home() / ".config" / "aws-tui"
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "aws-tui"

    log_sink = LogSink(base_dir=cache_dir / "log")
    config_store = ConfigStore(path=config_dir / "config.toml")
    keymap_store = KeymapStore()
    theme_store = ThemeStore(
        user_themes_dir=config_dir / "themes",
        user_overlay=config_dir / "theme.tcss",
    )
    connection_resolver = ConnectionResolver(config_store=config_store)
    aws_session = AwsSession()
    transfer_journal = TransferJournal(base_dir=cache_dir / "transfers")

    # ── Hub + dispatcher ───────────────────────────────────────────────────
    hub: MessageHub[Message] = MessageHub()
    dispatcher = RxDispatcher.immediate()

    # ── Registry ───────────────────────────────────────────────────────────
    registry = ServiceRegistry()
    s3_service = S3Service(
        aws_session=aws_session,
        transfer_journal=transfer_journal,
        hub=hub,
        dispatcher=dispatcher,
    )
    # cast to Service: S3Service satisfies the protocol structurally; mypy
    # rejects ClassVar `descriptor` here so we widen explicitly.
    registry.register(cast("Service", s3_service))

    # ── Root VM ───────────────────────────────────────────────────────────
    root_vm = RootVM(
        registry=registry,
        keymap=keymap_store,
        theme=theme_store,
        log=log_sink,
        dispatcher=dispatcher,
        hub=hub,
    )

    # ── Overlay VMs (lifetime managed at the app level, not in RootVM) ────
    command_palette_vm = CommandPaletteVM(hub=hub, dispatcher=dispatcher)
    confirm_vm = ConfirmationVM(hub=hub, dispatcher=dispatcher)
    quick_look_vm = QuickLookVM(hub=hub, dispatcher=dispatcher)
    transfers_vm = TransfersVM(hub=hub, dispatcher=dispatcher)

    return AppContext(
        root_vm=root_vm,
        registry=registry,
        config_store=config_store,
        log_sink=log_sink,
        keymap_store=keymap_store,
        theme_store=theme_store,
        connection_resolver=connection_resolver,
        aws_session=aws_session,
        transfers_vm=transfers_vm,
        confirm_vm=confirm_vm,
        quick_look_vm=quick_look_vm,
        command_palette_vm=command_palette_vm,
        transfer_journal=transfer_journal,
        hub=hub,
        dispatcher=dispatcher,
        initial_theme="carbon",
    )


__all__ = ["AppContext", "build_app_context"]
