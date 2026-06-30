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

from urllib.parse import urlparse

from vmx import Message, MessageHub, RxDispatcher
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.transfer_journal import TransferJournal, TransferJournalEntry
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore, UnknownAction
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.paths import cache_home, config_home
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.services.s3.service import S3Service
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.first_run_vm import S3CompatForm
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.chrome.resume_vm import ResumeAction
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import Service, ServiceRegistry
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM

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
        "keymap_store",
        "log_sink",
        "quick_look_vm",
        "registry",
        "root_vm",
        "s3_connections_vm",
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
        demo: bool = False,
        demo_emr: InMemoryEmr | None = None,
        unreachable_connections: set[tuple[str, str]] | None = None,
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

    log_sink = LogSink(base_dir=cache_dir / "log")
    # read_only=demo: in demo mode all write methods on ConfigStore are
    # silent no-ops so the user's real config.toml is never mutated.
    config_store = ConfigStore(path=config_dir / "config.toml", read_only=demo)
    keybindings_overlay: dict[str, str | list[str]] = {}
    try:
        _cfg = config_store.load()
        initial_theme = _cfg.defaults.theme
        keybindings_overlay = _cfg.keybindings.bindings
    except Exception as exc:
        # Falling back silently is dishonest — first-run with a
        # malformed config.toml looks identical to a clean install.
        # Log once so an operator can find the cause in the log.
        _logger.warning(
            "composition.initial_theme.load_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        initial_theme = "carbon"
    # The CHANGELOG ``### Deferred / v0.9 roadmap`` entry promises
    # that ``[keybindings]`` overlays in ``config.toml`` "parse and
    # validate but do not yet affect the live keymap" — wiring the
    # overlay into ``KeymapStore`` is what delivers the parse-and-
    # validate half. The live-keymap half is gated on the deferred
    # ``BindingResolver`` work. A malformed overlay (unknown action
    # id, etc.) is caught and logged rather than crashing startup;
    # the keymap silently falls back to defaults.
    try:
        keymap_store = KeymapStore(overlay=keybindings_overlay)
    except UnknownAction as exc:
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
        from aws_tui.demo.seeds import seeded_demo_emr, seeded_demo_fs

        # DemoConnectionResolver is a structural subtype — typed as the
        # production class so all downstream call sites remain compatible.
        connection_resolver: ConnectionResolver = DemoConnectionResolver()  # type: ignore[assignment]
        _demo_emr: InMemoryEmr = seeded_demo_emr()
        demo_emr_ref: InMemoryEmr | None = _demo_emr
        s3_fs_factory = lambda c: seeded_demo_fs(c.profile or "demo-default")  # noqa: E731
        # Captured by the lambda so every emr_client_factory(connection)
        # call within this AppContext returns the SAME InMemoryEmr —
        # switching demo profiles in the picker preserves in-flight clone
        # state.  A second build_app_context() call (rare; mostly in tests)
        # gets its own _demo_emr; we don't share at module scope.
        emr_client_factory = lambda c: _demo_emr  # noqa: E731
    else:
        connection_resolver = ConnectionResolver(config_store=config_store)
        demo_emr_ref = None
        s3_fs_factory = None
        emr_client_factory = None
    aws_session = AwsSession()
    transfer_journal = TransferJournal(base_dir=cache_dir / "transfers")

    # ── Hub + dispatcher ───────────────────────────────────────────────────
    hub: MessageHub[Message] = MessageHub()
    dispatcher = RxDispatcher.immediate()

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
        hub=hub,
        dispatcher=dispatcher,
    )
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=dispatcher)
    focus_coordinator.construct()
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
        initial_theme=initial_theme,
        s3_connections_vm=s3_connections_vm,
        focus_coordinator=focus_coordinator,
        demo=demo,
        demo_emr=demo_emr_ref,
        unreachable_connections=set(),
    )


async def apply_resume_decision(
    *,
    decision: ResumeAction,
    entries: list[TransferJournalEntry],
    journal: TransferJournal,
    aws_session: AwsSession,
    connection: Connection | None,
) -> None:
    """Apply the user's resume-modal decision to the journal + S3.

    - ``RESUME_ALL`` is a no-op for now (the next-write path will register
      :class:`TransferVM` placeholders that pick up where the journal left
      off; that scaffolding lives in the file-manager VM and is not yet
      hooked up to this entry point). Logged for observability.
    - ``ABORT_ALL`` invokes ``AbortMultipartUpload`` per entry's
      ``upload_id`` (if any), then purges every journal file.
    - ``DECIDE_EACH`` is treated as ``KEEP_FOR_LATER`` per plan §M6 T2.
    - ``KEEP_FOR_LATER`` is a no-op.
    """
    if decision is ResumeAction.RESUME_ALL:
        # Placeholder: file-manager TransferVM resume hookup is out of
        # scope for M6 (the journal entries remain intact so a future
        # run can pick them up).
        return
    if decision is ResumeAction.ABORT_ALL:
        if connection is None:
            # Cannot abort without an S3 connection — keep the journals
            # so the next session can try again.
            return
        async with await aws_session.client(connection, "s3") as client:
            for entry in entries:
                bucket, key = _parse_s3_uri(entry.destination_uri)
                abort_succeeded = True
                if bucket and key and entry.upload_id:
                    try:
                        await client.abort_multipart_upload(
                            Bucket=bucket, Key=key, UploadId=entry.upload_id
                        )
                    except Exception as exc:
                        # Keep the journal so the next session can retry. If
                        # we purged here, the MPU would continue to live on
                        # S3 (consuming storage quota) with no local record
                        # of it — silent data leak. The bucket-level MPU
                        # lifecycle rule recommended in connections.md is
                        # the backstop, but the journal is the recovery
                        # path the user actually drives. The catch is broad
                        # by design (botocore raises many shapes, and the
                        # journal-preservation contract is verified by
                        # ``test_abort_all_preserves_journal_when_s3_abort_fails``);
                        # we log here so operators can still see *why* an
                        # abort failed without reproducing it.
                        abort_succeeded = False
                        _logger.warning(
                            "resume.abort.failed",
                            extra={
                                "transfer_id": entry.transfer_id,
                                "bucket": bucket,
                                "key": key,
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                        )
                if abort_succeeded:
                    journal.mark_aborted(entry.transfer_id)
                    journal.purge(entry.transfer_id)
        return
    if decision is ResumeAction.DECIDE_EACH:
        # Fall back per plan §M6 T2.
        return
    # KEEP_FOR_LATER -> no-op
    return


def _parse_s3_uri(uri: str) -> tuple[str | None, str | None]:
    """Extract (bucket, key) from an ``s3://bucket/key`` URI.

    Returns ``(None, None)`` if the URI has any other scheme.
    """
    if not uri.startswith("s3://"):
        return (None, None)
    parsed = urlparse(uri)
    return (parsed.netloc or None, parsed.path.lstrip("/") or None)


def needs_first_run(
    *,
    config_store: ConfigStore,
    connection_resolver: ConnectionResolver,
) -> bool:
    """Return True when neither config nor AWS profiles know any connection.

    Implements the trigger from spec §6.4 Flow 5.
    """
    # Config-store connections.
    try:
        cfg = config_store.load()
        if cfg.connections:
            return False
    except Exception as exc:
        # Treat a broken config as "user already has setup, just
        # can't read it" rather than dropping them into the first-run
        # wizard which would overwrite whatever is there.
        _logger.warning(
            "composition.needs_first_run.config_load_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        return False
    # Auto-discovered AWS profiles.
    try:
        discovered = connection_resolver.list()
    except Exception as exc:
        _logger.warning(
            "composition.needs_first_run.resolver_list_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        return True
    return not discovered


#: Hard cap on ``aws configure sso`` wall-clock. A hung wizard should
#: not freeze the TUI forever; 600 s matches the SSO device-flow grace
#: period. Returned as 124 (timeout exit code) on expiry.
_AWS_CONFIGURE_SSO_TIMEOUT_SECONDS = 600


def run_aws_configure_sso() -> int:
    """Shell out to ``aws configure sso``. Returns the subprocess return code.

    Blocks the calling thread while the wizard runs; the TUI freezes for
    that duration, which is expected per spec §6.4 Flow 5. A 10-minute
    timeout guards against a hung wizard (returns ``124``).
    """
    import subprocess

    try:
        result = subprocess.run(
            ["aws", "configure", "sso"],
            check=False,
            timeout=_AWS_CONFIGURE_SSO_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return 127
    except subprocess.TimeoutExpired:
        return 124
    return result.returncode


def add_s3_compat_connection(
    *,
    config_store: ConfigStore,
    form: S3CompatForm,
) -> None:
    """Materialize an :class:`S3CompatForm` into a config-store entry."""
    entry = ConnectionEntry(
        name=form.name,
        kind="s3-compatible",
        region=form.region,
        endpoint_url=form.endpoint_url,
        access_key_id=form.access_key_id,
        secret_access_key=form.secret_access_key,
        credentials="static",
        force_path_style=form.force_path_style,
        verify_tls=form.verify_tls,
    )
    config_store.add_connection(entry)


__all__ = [
    "AppContext",
    "add_s3_compat_connection",
    "apply_resume_decision",
    "build_app_context",
    "needs_first_run",
    "run_aws_configure_sso",
]
