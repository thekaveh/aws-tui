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

import contextlib
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from vmx import Message, MessageHub, RxDispatcher
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.transfer_journal import TransferJournal, TransferJournalEntry
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3.service import S3Service
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.first_run_vm import S3CompatForm
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.chrome.resume_vm import ResumeAction
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
                if bucket and key and entry.upload_id:
                    with contextlib.suppress(Exception):
                        await client.abort_multipart_upload(
                            Bucket=bucket, Key=key, UploadId=entry.upload_id
                        )
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
    except Exception:
        return False
    # Auto-discovered AWS profiles.
    try:
        discovered = connection_resolver.list()
    except Exception:
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
