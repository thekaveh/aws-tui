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

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from aws_tui.demo.in_memory_emr import InMemoryEmr

from vmx import Message, MessageHub, RxDispatcher
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keychain import KeychainBackend, Keyring
from aws_tui.infra.keymap_store import KeybindingCollision, KeymapStore, UnknownAction
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.paths import cache_home, config_home
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.athena.service import AthenaService
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.services.glue.service import GlueService
from aws_tui.services.s3.service import S3Service
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.service_source_vm import ServiceSelectionStore
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.table_clipboard_vm import TableClipboardVM

_logger = logging.getLogger("aws_tui.composition")


class AppContext:
    """The bag of pre-wired objects the Textual app consumes."""

    __slots__ = (
        "aws_session",
        "command_palette_vm",
        "config_store",
        "confirm_vm",
        "connection_resolver",
        "demo",
        "demo_emr",
        "dispatcher",
        "focus_coordinator",
        "hub",
        "initial_theme",
        "keychain",
        "keymap_store",
        "log_sink",
        "quick_look_vm",
        "registry",
        "root_vm",
        "s3_connections_vm",
        "table_clipboard_vm",
        "theme_store",
        "transfer_journal",
        "transfers_vm",
        "unreachable_connections",
    )

    def __init__(
        self,
        *,
        root_vm: RootVM,
        registry: ServiceRegistry,
        config_store: ConfigStore,
        log_sink: LogSink,
        keymap_store: KeymapStore,
        keychain: KeychainBackend | None = None,
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
        s3_connections_vm: S3ConnectionsVM,
        focus_coordinator: FocusCoordinatorVM | None = None,
        table_clipboard_vm: TableClipboardVM | None = None,
        demo: bool = False,
        demo_emr: InMemoryEmr | None = None,
        unreachable_connections: set[tuple[str, str]] | None = None,
    ) -> None:
        self.root_vm = root_vm
        self.registry = registry
        self.config_store = config_store
        self.log_sink = log_sink
        self.keymap_store = keymap_store
        self.keychain = keychain
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
        self.s3_connections_vm = s3_connections_vm
        # Lifecycle: builds one if not supplied so test harnesses that
        # pre-date round-3 wiring keep working. The build_app_context
        # path always supplies a constructed one.
        self.focus_coordinator: FocusCoordinatorVM = (
            focus_coordinator
            if focus_coordinator is not None
            else FocusCoordinatorVM(hub=hub, dispatcher=dispatcher)
        )
        if focus_coordinator is None:
            self.focus_coordinator.construct()
        self.table_clipboard_vm: TableClipboardVM = (
            table_clipboard_vm
            if table_clipboard_vm is not None
            else TableClipboardVM(hub=hub, dispatcher=dispatcher)
        )
        if table_clipboard_vm is None:
            self.table_clipboard_vm.construct()
        self.demo = demo
        # Non-None only in demo mode; disposed by AwsTuiApp on shutdown so
        # in-flight clone state-machine tasks are cancelled cleanly.
        self.demo_emr: InMemoryEmr | None = demo_emr
        self.unreachable_connections: set[tuple[str, str]] = (
            unreachable_connections if unreachable_connections is not None else set()
        )


def build_app_context(
    *,
    config_dir: Path | None = None,
    cache_dir: Path | None = None,
    demo: bool = False,
) -> AppContext:
    """Build the full ``AppContext`` for a fresh aws-tui session.

    Parameters
    ----------
    config_dir:
        Override for the platform-native config directory (used by tests).
        Defaults to :func:`aws_tui.infra.paths.config_home` which resolves
        to ``%APPDATA%\\aws-tui`` on Windows, ``~/Library/Application
        Support/aws-tui`` on macOS, and ``~/.config/aws-tui`` on Linux
        (with the legacy XDG location preferred if it already exists).
    cache_dir:
        Override for the platform-native cache directory. Defaults to
        :func:`aws_tui.infra.paths.cache_home`.
    """
    # ── Infra ──────────────────────────────────────────────────────────────
    if config_dir is None:
        config_dir = config_home()
    if cache_dir is None:
        cache_dir = cache_home()

    log_sink = LogSink(base_dir=cache_dir / "log", capture_stdlib=True)
    # read_only=demo: in demo mode all write methods on ConfigStore are
    # silent no-ops so the user's real config.toml is never mutated.
    config_store = ConfigStore(path=config_dir / "config.toml", read_only=demo)
    keybindings_overlay: dict[str, str | list[str]] = {}
    try:
        _cfg = config_store.load()
        initial_theme = _cfg.defaults.theme
        # COPY the bindings dict out of the frozen Config — the
        # source dict lives on a ``frozen=True`` dataclass, and
        # handing the bare reference downstream would let any
        # consumer (KeymapStore overlay, future binding-resolver
        # logic) mutate the dict inside the supposedly-immutable
        # Config. The frozen-ness contract only blocks attribute
        # rebinding, not mutation through the reference.
        keybindings_overlay = dict(_cfg.keybindings.bindings)
    except Exception as exc:
        # Falling back silently is dishonest — first-run with a
        # malformed config.toml looks identical to a clean install.
        # Log once so an operator can find the cause in the log.
        _logger.warning(
            "composition.initial_theme.load_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        initial_theme = "carbon"
    try:
        keymap_store = KeymapStore(overlay=keybindings_overlay)
    except (KeybindingCollision, UnknownAction) as exc:
        _logger.warning(
            "composition.keymap_overlay.invalid",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        keymap_store = KeymapStore()
    theme_store = ThemeStore(
        user_themes_dir=config_dir / "themes",
        user_overlay=config_dir / "theme.tcss",
    )
    if demo:
        from aws_tui.demo.connections import DemoConnectionResolver
        from aws_tui.demo.in_memory_athena import InMemoryAthena
        from aws_tui.demo.in_memory_fs import InMemoryFS
        from aws_tui.demo.seeds import (
            seeded_demo_athena,
            seeded_demo_emr,
            seeded_demo_fs,
            seeded_demo_glue,
        )

        # DemoConnectionResolver is a structural subtype — typed as the
        # production class so all downstream call sites remain compatible.
        connection_resolver: ConnectionResolver = DemoConnectionResolver()  # type: ignore[assignment]
        _demo_emr: InMemoryEmr = seeded_demo_emr()
        demo_glue_clients = seeded_demo_glue()
        demo_s3_filesystems: dict[str, InMemoryFS] = {}
        demo_athena_clients: dict[str, InMemoryAthena] = {}

        def demo_s3_fs(connection: Connection) -> InMemoryFS:
            filesystem = demo_s3_filesystems.get(connection.name)
            if filesystem is None:
                filesystem = seeded_demo_fs(connection.profile or "demo-default")
                demo_s3_filesystems[connection.name] = filesystem
            return filesystem

        def demo_athena(connection: Connection) -> InMemoryAthena:
            client = demo_athena_clients.get(connection.name)
            if client is None:
                client = seeded_demo_athena(
                    connection.profile or "demo-default",
                    connection_name=connection.name,
                    region=connection.region,
                    result_store=demo_s3_fs(connection),
                )
                demo_athena_clients[connection.name] = client
            return client

        demo_emr_ref: InMemoryEmr | None = _demo_emr
        s3_fs_factory = demo_s3_fs
        # Captured by the lambda so every emr_client_factory(connection)
        # call within this AppContext returns the SAME InMemoryEmr —
        # switching demo profiles in the picker preserves in-flight clone
        # state.  A second build_app_context() call (rare; mostly in tests)
        # gets its own _demo_emr; we don't share at module scope.
        emr_client_factory = lambda c: _demo_emr  # noqa: E731
        glue_client_factory = lambda c: demo_glue_clients[c.name]  # noqa: E731
        athena_client_factory = demo_athena
    else:
        keychain: KeychainBackend | None = Keyring()
        connection_resolver = ConnectionResolver(
            config_store=config_store,
            keychain=keychain,
        )
        demo_emr_ref = None
        s3_fs_factory = None
        emr_client_factory = None
        glue_client_factory = None
        athena_client_factory = None
    if demo:
        keychain = None
    aws_session = AwsSession()
    transfer_journal = TransferJournal(base_dir=cache_dir / "transfers")

    # ── Hub + dispatcher ───────────────────────────────────────────────────
    hub: MessageHub[Message] = MessageHub()
    dispatcher = RxDispatcher.immediate()
    service_selections = ServiceSelectionStore()

    # ── Registry ───────────────────────────────────────────────────────────
    registry = ServiceRegistry()
    s3_service = S3Service(
        transfer_journal=transfer_journal,
        hub=hub,
        dispatcher=dispatcher,
        s3_fs_factory=s3_fs_factory,
    )
    # cast to Service: S3Service satisfies the protocol structurally; mypy
    # rejects ClassVar `descriptor` here so we widen explicitly.
    registry.register(cast("Service", s3_service))

    emr_service = EmrServerlessService(
        hub=hub,
        dispatcher=dispatcher,
        emr_client_factory=emr_client_factory,
    )
    registry.register(cast("Service", emr_service))

    glue_service = GlueService(
        hub=hub,
        dispatcher=dispatcher,
        aws_session=aws_session,
        glue_client_factory=glue_client_factory,
        athena_client_factory=athena_client_factory,
        selection_store=service_selections,
    )
    registry.register(cast("Service", glue_service))

    athena_service = AthenaService(
        hub=hub,
        dispatcher=dispatcher,
        aws_session=aws_session,
        athena_client_factory=athena_client_factory,
        selection_store=service_selections,
    )
    registry.register(cast("Service", athena_service))

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
    s3_connections_vm = S3ConnectionsVM(
        resolver=connection_resolver,
        config_store=config_store,
        keychain=keychain,
        hub=hub,
        dispatcher=dispatcher,
    )
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=dispatcher)
    focus_coordinator.construct()
    table_clipboard_vm = TableClipboardVM(hub=hub, dispatcher=dispatcher)
    table_clipboard_vm.construct()
    return AppContext(
        root_vm=root_vm,
        registry=registry,
        config_store=config_store,
        log_sink=log_sink,
        keymap_store=keymap_store,
        keychain=keychain,
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
        initial_theme=initial_theme,
        s3_connections_vm=s3_connections_vm,
        focus_coordinator=focus_coordinator,
        table_clipboard_vm=table_clipboard_vm,
        demo=demo,
        demo_emr=demo_emr_ref,
        unreachable_connections=set(),
    )


__all__ = [
    "AppContext",
    "build_app_context",
]
