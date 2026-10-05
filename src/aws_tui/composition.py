"""Composition root — wires every layer together.

This module deliberately lives outside the strict five-layer tree
(``src/aws_tui/{infra,domain,vm,services,ui}/``) so it may legally import
from every layer. The layer-rule check (``scripts/check-layers.sh``)
only walks the five layer folders; ``composition.py`` and ``app.py`` are
the only two top-level files allowed to know about all of them.

The composition builds:

- ``ConfigStore``, ``LogSink``, ``KeymapStore``, ``ThemeStore`` (infra)
- ``ConnectionResolver``, ``AwsSession`` (infra; aware of boto3)
- ``ServiceRegistry`` with every built-in service registered (services)
- ``RootVM`` with shared chrome and an active-service content host (vm)
- ``AppContext`` — the bag the Textual ``AwsTuiApp`` consumes
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from aws_tui.demo.in_memory_emr import InMemoryEmr

from vmx import Message, MessageHub, RxDispatcher
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.transfer_history import TransferConnectionIdentity
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.athena_draft_store import AthenaDraftStore
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.clipboard import ClipboardPort, NativeClipboard
from aws_tui.infra.config_store import Config, ConfigStore, ConnectionEntry, Defaults, Keybindings
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.duckdb import DuckDbPort, NativeDuckDb
from aws_tui.infra.keychain import KeychainBackend, Keyring
from aws_tui.infra.keymap_store import (
    InvalidKeybinding,
    KeybindingCollision,
    KeymapStore,
    UnknownAction,
)
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.paths import cache_home, config_home
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.athena.service import AthenaService
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.services.glue.service import GlueService
from aws_tui.services.s3.service import S3Service
from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.clipboard_vm import ClipboardVM
from aws_tui.vm.credential_recovery import connection_history_identity
from aws_tui.vm.file_manager.transfer_history_vm import (
    RecoveryRefused,
    ResolvedTransferEndpoint,
    TransferHistoryVM,
)
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.service_source_vm import ServiceSelectionStore
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.table_clipboard_vm import TableClipboardVM

_logger = logging.getLogger("aws_tui.composition")


def entry_source_identity(entry: ConnectionEntry | None) -> tuple[object, ...]:
    if entry is None:
        return (False, None, None, None, None, None, None, None)
    return (
        True,
        entry.kind,
        entry.profile,
        entry.region,
        entry.endpoint_url,
        entry.credentials,
        entry.force_path_style,
        entry.verify_tls,
    )


def connection_route(connection: Connection) -> tuple[object, ...]:
    return (
        connection.kind,
        connection.name,
        connection.region,
        connection.source,
        connection.profile,
        connection.endpoint_url,
        connection.force_path_style,
        connection.verify_tls,
    )


def make_source_check_factory(
    config: ConfigStore,
    resolver: ConnectionResolver,
) -> Callable[[Connection], Callable[[], Awaitable[bool]]]:
    def factory(captured: Connection) -> Callable[[], Awaitable[bool]]:
        expected_route = connection_route(captured)
        baseline: tuple[object, ...] | None = None
        check_lock = asyncio.Lock()

        def read_identity() -> tuple[object, ...] | None:
            try:
                before = entry_source_identity(config.load().connections.get(captured.name))
                current = resolver.resolve_selected(captured.name)
                after = entry_source_identity(config.load().connections.get(captured.name))
                if before != after or connection_route(current) != expected_route:
                    return None
                return after
            except Exception:
                return None

        async def check() -> bool:
            nonlocal baseline
            async with check_lock:
                current = await asyncio.to_thread(read_identity)
                if current is None:
                    return False
                if baseline is None:
                    baseline = current
                return current == baseline

        return check

    return factory


class AppContext:
    """The bag of pre-wired objects the Textual app consumes."""

    __slots__ = (
        "athena_drafts_shutdown_warning",
        "athena_drafts_vm",
        "aws_session",
        "clipboard",
        "clipboard_vm",
        "command_palette_vm",
        "config_store",
        "confirm_vm",
        "connection_resolver",
        "demo",
        "demo_emrs",
        "dispatcher",
        "duckdb",
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
        "transfer_history_vm",
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
        athena_drafts_vm: AthenaDraftsVM | None = None,
        focus_coordinator: FocusCoordinatorVM | None = None,
        table_clipboard_vm: TableClipboardVM | None = None,
        clipboard: ClipboardPort | None = None,
        clipboard_vm: ClipboardVM | None = None,
        duckdb_port: DuckDbPort | None = None,
        transfer_history_vm: TransferHistoryVM | None = None,
        demo: bool = False,
        demo_emrs: dict[str, InMemoryEmr] | None = None,
        unreachable_connections: set[tuple[str, str]] | None = None,
    ) -> None:
        self.athena_drafts_vm = (
            athena_drafts_vm
            if athena_drafts_vm is not None
            else AthenaDraftsVM(
                store=AthenaDraftStore(
                    config=config_store, directory=config_store.path.parent / "athena-drafts"
                ),
                enabled=False,
                read_only=demo,
                directory=config_store.path.parent / "athena-drafts",
                hub=hub,
                dispatcher=dispatcher,
            )
        )
        self.athena_drafts_shutdown_warning: str | None = None
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
        service = registry.get("s3") if "s3" in registry else None
        runtime = (
            service.transfer_runtime
            if isinstance(service, S3Service)
            else TransferRuntime(transfer_journal, hub)
        )

        async def resolve_history_endpoint(
            identity: TransferConnectionIdentity,
        ) -> ResolvedTransferEndpoint:
            def build() -> ResolvedTransferEndpoint:
                current_service = registry.get("s3")
                if not isinstance(current_service, S3Service):
                    raise RecoveryRefused(
                        "connection_changed", "The original connection is unavailable or changed."
                    )
                if identity.kind == "local":
                    provider = current_service.build_local_provider()
                    bound = connection_history_identity(None, provider)
                else:
                    connection = connection_resolver.resolve(identity.name)
                    if connection.kind != identity.kind:
                        raise RecoveryRefused(
                            "connection_changed",
                            "The original connection is unavailable or changed.",
                        )
                    provider = current_service.build_remote_provider(connection)
                    if getattr(provider, "storage_identity", None) is None:
                        raise RecoveryRefused(
                            "connection_changed",
                            "The original connection is unavailable or changed.",
                        )
                    bound = connection_history_identity(connection, provider)
                if bound != identity:
                    raise RecoveryRefused(
                        "connection_changed", "The original connection is unavailable or changed."
                    )
                return ResolvedTransferEndpoint(provider, bound)

            return await runtime.disk.call(build)

        self.transfer_history_vm = transfer_history_vm or TransferHistoryVM(
            transfer_journal,
            resolve_history_endpoint,
            hub,
            dispatcher,
            runtime=runtime,
        )
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
        # Same "keep pre-existing harnesses working" default as above. The
        # port itself is a plain infra object, not a VMx disposable: it owns
        # no resources, so it is deliberately absent from
        # ``close_unstarted`` and from ``AwsTuiApp._aws_tui_shutdown``. It is
        # held here because it is what the VM below is built around -- the
        # App reads ``clipboard_vm``, never the port.
        self.clipboard: ClipboardPort = clipboard if clipboard is not None else NativeClipboard()
        # Same lifecycle note as ``clipboard`` above: the port owns no
        # resources, so it is deliberately absent from ``close_unstarted``
        # and ``AwsTuiApp._aws_tui_shutdown``.
        self.duckdb: DuckDbPort = duckdb_port if duckdb_port is not None else NativeDuckDb()
        self.clipboard_vm: ClipboardVM = (
            clipboard_vm
            if clipboard_vm is not None
            else ClipboardVM(clipboard=self.clipboard, hub=hub, dispatcher=dispatcher)
        )
        if clipboard_vm is None:
            self.clipboard_vm.construct()
        self.demo = demo
        # Populated lazily in demo mode; each AWS source owns a separate
        # provider so profile switches cannot share clone mutations or data.
        self.demo_emrs: dict[str, InMemoryEmr] = demo_emrs if demo_emrs is not None else {}
        self.unreachable_connections: set[tuple[str, str]] = (
            unreachable_connections if unreachable_connections is not None else set()
        )

    def close_unstarted(self) -> None:
        """Release a context that never reached Textual's mount lifecycle.

        ``AwsTuiApp.on_unmount`` owns normal async shutdown. This synchronous
        fallback is only for composition or app-constructor failures, before
        workers and AWS clients can exist.
        """
        for disposable in (
            self.athena_drafts_vm,
            self.transfer_history_vm,
            self.s3_connections_vm,
            self.command_palette_vm,
            self.quick_look_vm,
            self.confirm_vm,
            self.transfers_vm,
            self.table_clipboard_vm,
            self.clipboard_vm,
            self.root_vm,
            self.focus_coordinator,
        ):
            with suppress(Exception):
                disposable.dispose()
        for demo_emr in self.demo_emrs.values():
            with suppress(Exception):
                demo_emr.dispose()
        with suppress(Exception):
            self.log_sink.flush()
        with suppress(Exception):
            self.log_sink.close()


def build_app_context(
    *,
    config_dir: Path | None = None,
    cache_dir: Path | None = None,
    demo: bool = False,
    clipboard: ClipboardPort | None = None,
    duckdb_port: DuckDbPort | None = None,
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
    clipboard:
        Override for the OS clipboard port (used by tests). Defaults to
        :class:`~aws_tui.infra.clipboard.NativeClipboard`. A parameter and
        not a post-build attribute assignment: ``ClipboardVM`` is built
        around the port here, so replacing ``AppContext.clipboard``
        afterwards would leave the view model still holding — and still
        spawning — the real platform helper.
    duckdb_port:
        Override for the local Iceberg-preview engine (used by tests).
        Defaults to :class:`~aws_tui.infra.duckdb.NativeDuckDb`. Threaded
        through :class:`~aws_tui.services.glue.service.GlueService` to
        ``GluePageVM`` for the same reason as ``clipboard`` above.
    """
    # ── Infra ──────────────────────────────────────────────────────────────
    if config_dir is None:
        config_dir = config_home()
    if cache_dir is None:
        cache_dir = cache_home()

    log_sink = LogSink(base_dir=cache_dir / "log", capture_stdlib=True)
    try:
        return _build_app_context(
            config_dir=config_dir,
            cache_dir=cache_dir,
            demo=demo,
            log_sink=log_sink,
            clipboard=clipboard,
            duckdb_port=duckdb_port,
        )
    except BaseException:
        log_sink.close()
        raise


def _build_app_context(
    *,
    config_dir: Path,
    cache_dir: Path,
    demo: bool,
    log_sink: LogSink,
    clipboard: ClipboardPort | None = None,
    duckdb_port: DuckDbPort | None = None,
) -> AppContext:
    # read_only=demo: in demo mode all write methods on ConfigStore are
    # silent no-ops so the user's real config.toml is never mutated.
    config_store = ConfigStore(path=config_dir / "config.toml", read_only=demo)
    keybindings_overlay: dict[str, str | list[str]] = {}
    config_load_failed = False
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
        config_load_failed = True
        _cfg = Config(connections={}, defaults=Defaults(), keybindings=Keybindings())
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
    except (InvalidKeybinding, KeybindingCollision, UnknownAction) as exc:
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
        demo_glue_clients = seeded_demo_glue()
        demo_s3_filesystems: dict[str, InMemoryFS] = {}
        demo_athena_clients: dict[str, InMemoryAthena] = {}
        demo_emr_clients: dict[str, InMemoryEmr] = {}

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

        def demo_emr(connection: Connection) -> InMemoryEmr:
            client = demo_emr_clients.get(connection.name)
            if client is None:
                client = seeded_demo_emr(connection.profile or connection.name)
                demo_emr_clients[connection.name] = client
            return client

        demo_emrs_ref = demo_emr_clients
        s3_fs_factory = demo_s3_fs
        emr_client_factory = demo_emr
        glue_client_factory = lambda c: demo_glue_clients[c.name]  # noqa: E731
        athena_client_factory = demo_athena
    else:
        keychain: KeychainBackend | None = Keyring()
        connection_resolver = ConnectionResolver(
            config_store=config_store,
            keychain=keychain,
        )
        demo_emrs_ref = {}
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

    # Resolved once, here, and threaded to both ``GlueService`` (needed at
    # registry-build time, below) and ``AppContext`` (built further down):
    # the same "one port, not two" reasoning as ``clipboard`` -- except the
    # clipboard port isn't needed until ``AppContext.__init__`` builds
    # ``ClipboardVM`` around it, so its default lives there instead.
    #
    # In demo mode, unlike Glue/Athena/S3/EMR above, an explicit
    # ``duckdb_port`` is still honoured -- a test that injects one wants
    # exactly that fake, demo or not. Only the *default* differs: real
    # DuckDB reaches the network (``INSTALL httpfs``/``aws``/``iceberg`` from
    # extensions.duckdb.org) and resolves the demo profile's credentials,
    # which breaks demo's no-real-AWS contract (README.md, "keeps the
    # no-real-AWS contract obvious"; ``tests/integration/test_demo_mode.py``
    # asserts the app never touches real AWS). The demo backing is a demo-layer
    # fake alongside the Glue and Athena ones, NOT ``infra``'s
    # ``InMemoryDuckDb``, which is documented as a test double and so must not
    # sit on a user-facing path.
    if duckdb_port is not None:
        resolved_duckdb_port: DuckDbPort = duckdb_port
    elif demo:
        from aws_tui.demo.in_memory_duckdb import InMemoryDuckDb as DemoDuckDb

        resolved_duckdb_port = DemoDuckDb()
    else:
        resolved_duckdb_port = NativeDuckDb()

    with ExitStack() as rollback:
        athena_draft_store = AthenaDraftStore(
            config=config_store,
            directory=config_store.path.parent / "athena-drafts",
        )
        athena_drafts_vm = AthenaDraftsVM(
            store=athena_draft_store,
            enabled=(not demo and _cfg.athena_sql_drafts),
            read_only=demo,
            directory=config_store.path.parent / "athena-drafts",
            preference_error=config_load_failed,
            hub=hub,
            dispatcher=dispatcher,
        )
        rollback.callback(athena_drafts_vm.dispose)
        source_check_factory = make_source_check_factory(config_store, connection_resolver)
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
            duckdb_port=resolved_duckdb_port,
        )
        registry.register(cast("Service", glue_service))

        athena_service = AthenaService(
            hub=hub,
            dispatcher=dispatcher,
            aws_session=aws_session,
            athena_client_factory=athena_client_factory,
            selection_store=service_selections,
            drafts=athena_drafts_vm,
            source_check_factory=source_check_factory,
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
        rollback.callback(focus_coordinator.dispose)
        focus_coordinator.construct()
        table_clipboard_vm = TableClipboardVM(hub=hub, dispatcher=dispatcher)
        rollback.callback(table_clipboard_vm.dispose)
        table_clipboard_vm.construct()
        context = AppContext(
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
            athena_drafts_vm=athena_drafts_vm,
            focus_coordinator=focus_coordinator,
            table_clipboard_vm=table_clipboard_vm,
            clipboard=clipboard,
            duckdb_port=resolved_duckdb_port,
            demo=demo,
            demo_emrs=demo_emrs_ref,
            unreachable_connections=set(),
        )
        rollback.pop_all()
        return context


__all__ = [
    "AppContext",
    "build_app_context",
]
