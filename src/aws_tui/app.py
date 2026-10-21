"""Top-level Textual application — composes RootVM + chrome + content host.

This is the real composition that replaces the M0 hello-world placeholder.
The actual layer wiring lives in :mod:`aws_tui.composition` so this module
stays focused on the Textual side (compose, mounting, action handlers).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import mimetypes
import os
import sys
import weakref
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import cached_property, partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from reactivex.abc import DisposableBase
from textual import events
from vmx import PropertyChangedMessage

if TYPE_CHECKING:
    from aws_tui.domain.filesystem import FileEntry, FileSystemProvider, PathRef
    from aws_tui.vm.file_manager.pane_vm import PaneVM

from textual.app import App, ComposeResult
from textual.binding import BindingsMap, BindingType
from textual.containers import Container, Horizontal
from textual.css.errors import StylesheetError
from textual.css.tokenizer import TokenError
from textual.driver import Driver
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, OptionList, Static, TextArea

from aws_tui.composition import AppContext, build_app_context
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    EntryKind,
    PermissionDeniedError,
    ProviderUnreachableError,
)
from aws_tui.domain.preview import load_preview
from aws_tui.domain.preview_limits import RAW_PREVIEW_BYTES
from aws_tui.domain.query import QueryState
from aws_tui.domain.s3_object_details import S3ObjectDetailsProvider
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.domain.s3_versions import S3VersionProvider
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection, ConnectionDiscovery, ConnectionNotFound
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.infra.doctor import collect_local_diagnostics
from aws_tui.infra.doctor_probe import probe_source
from aws_tui.infra.keymap_store import textual_key_name
from aws_tui.infra.redaction import redact_text
from aws_tui.infra.theme_store import ThemeNotFound, ThemeStore
from aws_tui.service_navigation import (
    ConnectionAdoptionOptions,
    ServiceMountPorts,
    ServiceNavigation,
    ViewPublisher,
)
from aws_tui.services.glue.service import GlueService
from aws_tui.ui import notifications
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from aws_tui.ui.paste_guard import guarded_driver_class
from aws_tui.ui.query_cache import release_detached_query_results
from aws_tui.ui.terminal_protocol import prefer_sigwinch_resize
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.athena.results_view import AthenaResultsView
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.confirm_modal import TextualDialogService
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.first_run import (
    FIRST_RUN_FORM_CSS,
    PROBE_FAILED,
    FirstRunConnectionList,
    FirstRunView,
)
from aws_tui.ui.widgets.glue.comparison_modal import GlueComparisonModal
from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.pane import EntryRow, Pane
from aws_tui.ui.widgets.pane_listing_controls import FilterPaneModal, FindPaneModal, SortPaneModal
from aws_tui.ui.widgets.quick_look import QuickLook
from aws_tui.ui.widgets.recursive_upload import RecursiveUploadModal
from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
from aws_tui.ui.widgets.s3_versions import S3VersionsModal
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.ui.widgets.service_view_factory import build_service_view
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormInline,
    ConnectionFormSubmitted,
)
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.ui.widgets.transfer_history_modal import TransferHistoryModal
from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.version import __version__
from aws_tui.vm.athena.history_vm import _execution_identity_belongs_to
from aws_tui.vm.athena.page_vm import AthenaPageEditorSnapshot, AthenaPageSnapshot, AthenaPageVM
from aws_tui.vm.chrome.action_catalog import (
    ACTION_SPECS,
    ActionPresentation,
    ActionSpec,
    project_actions,
    scope_label,
)
from aws_tui.vm.chrome.confirm_vm import ConfirmPath, ConfirmRequest
from aws_tui.vm.chrome.crash_vm import CrashChoice, CrashReport, CrashVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.chrome.quick_look_vm import QuickLookContent
from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM
from aws_tui.vm.clipboard_vm import ClipboardChannel
from aws_tui.vm.credential_recovery import (
    RecoveryGuidance,
    classify_recovery_failure,
    connection_binding_identity,
    connection_history_identity,
)
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM, FocusedPane
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.file_manager.recursive_upload_vm import PreparedUpload, RecursiveUploadVM
from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsVM
from aws_tui.vm.file_manager.s3_version_transfer import execute_version_transfer
from aws_tui.vm.file_manager.s3_versions_vm import S3VersionsVM, VersionTransferRequest
from aws_tui.vm.glue.catalog_vm import GlueSelectionSuperseded
from aws_tui.vm.glue.iceberg_vm import IcebergView
from aws_tui.vm.glue.page_vm import GluePageVM, GlueView
from aws_tui.vm.messages import (
    ConnectionChangedMessage,
    ConnectionListChangedMessage,
    CopyTableReferenceRequest,
    OpenAthenaTableRequest,
    OpenGlueTableRequest,
    OpenS3LocationRequest,
    PaletteActionFailedMessage,
    ThemeChangedMessage,
)
from aws_tui.vm.nav_menu_vm import SETTINGS_NAV_ID
from aws_tui.vm.service_diagnostics import (
    CapturedServiceDiagnostic,
    capture_service_diagnostics,
)
from aws_tui.vm.service_source_vm import ServiceSourceContext

_ACTION_RING_SIZE = 100
_QUICK_LOOK_PREVIEW_BYTES = RAW_PREVIEW_BYTES
_BOOT_CHAIN_BUDGET_SECONDS = 90.0


@dataclass(frozen=True, slots=True)
class _S3HandoffSnapshot:
    connection: Connection | None
    auth_state: TokenState | None
    service_id: str | None
    athena: AthenaPageSnapshot | None = field(repr=False)
    athena_result_execution_id: str | None
    glue: _GluePageSnapshot | None = field(repr=False)


@dataclass(slots=True)
class _StagedServiceRecovery:
    """Off-screen AWS service tree whose credential read has completed."""

    state: PaneState
    vm: object = field(repr=False)
    commit_selection: Callable[[], None] = field(repr=False)
    diagnostics: tuple[CapturedServiceDiagnostic, ...] = field(repr=False)
    live_state_is_current: Callable[[], bool] = field(repr=False)


@dataclass(slots=True)
class _SourceInputObservation:
    vm: object = field(repr=False)
    sql_changes: int = 0
    context_choices: int = 0
    subscription: DisposableBase | None = field(default=None, repr=False)

    def changed(self, property_name: str) -> None:
        if property_name == "sql":
            self.sql_changes += 1


@dataclass(slots=True)
class _SourceRollback:
    service_id: str
    connection: Connection | None
    auth_state: TokenState | None
    owner: tuple[str, int] | None
    outgoing: object = field(repr=False)
    athena: AthenaPageSnapshot | None = field(default=None, repr=False)
    editor: AthenaPageEditorSnapshot | None = field(default=None, repr=False)
    claimed: bool = False
    target: _SourceInputObservation | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _GluePageSnapshot:
    active_view: GlueView
    database_name: str | None
    table_ref: TableRef | None = field(repr=False)
    iceberg_view: IcebergView | None
    iceberg_snapshot_id: int | None
    job_name: str | None
    job_run_id: str | None
    job_run_states: frozenset[str]
    crawler_name: str | None
    crawler_state: str | None


@dataclass(frozen=True, slots=True)
class _TableHandoffSnapshot:
    connection: Connection | None
    auth_state: TokenState | None
    service_id: str | None
    athena: AthenaPageSnapshot | None = field(repr=False)
    glue: _GluePageSnapshot | None = field(repr=False)


class _S3HandoffStageError(Exception):
    def __init__(self, stage: str, error_type: str) -> None:
        super().__init__(f"S3 handoff failed during {stage}")
        self.stage = stage
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class _ThemeApplyFailure:
    stage: str
    error: Exception


_SOURCE_SERVICE_IDS = frozenset({"s3", "emr-serverless", "glue", "athena"})
_ATHENA_SQL_ACTIONS = {
    "athena.sql_authoring": "menu",
    "athena.complete": "complete",
    "athena.format_sql": "format",
    "athena.open_sql": "open",
    "athena.save_sql": "save",
    "athena.save_sql_as": "save_as",
    "athena.insert_snippet": "insert_snippet",
    "athena.save_snippet": "save_snippet",
}

_ATHENA_RESULT_ACTIONS = frozenset(
    {
        "athena.inspect_cell",
        "athena.copy_cell",
        "athena.copy_row",
        "athena.filter_results",
        "athena.sort_results",
        "athena.reset_results",
    }
)
# Copy and delete run in SEPARATE exclusive groups. Sharing one group meant
# ``exclusive=True`` made a confirmed delete cancel an in-flight copy: the copy
# worker awaits the whole byte-streaming batch, so it died mid-transfer leaving
# a partial object at the destination, and because ``_run_copy`` catches only
# ``Exception`` the ``CancelledError`` produced no toast and no log line.
# Exclusivity within each group still prevents two concurrent copies.
_TRANSFER_COPY_GROUP = "transfer-copy"
_TRANSFER_DELETE_GROUP = "transfer-delete"
_TRANSFER_WORKER_GROUPS = (_TRANSFER_COPY_GROUP, _TRANSFER_DELETE_GROUP)

# Action ids that :meth:`AwsTuiApp.action_dispatch` still honours while a
# screen sits on the stack (or the coordinator reports modal precedence).
# Everything else is deliberately inert behind a modal so a stray shortcut
# cannot mutate the page the user cannot currently see.
#
# ``app.quit`` is in here as an ESCAPE HATCH, not as a routed pane action.
# Without it, any modal that cannot be dismissed — e.g. one wedged by a
# deferred focus projection landing in its ``focused`` (see
# ``ui/widgets/_focus_guard``) — also made the whole app unquittable:
# ``q`` and ``ctrl+c`` both dispatch ``app.quit`` and both returned None.
# Quitting is never destructive to remote state and always runs the full
# ``_aws_tui_shutdown``, so it must work from every UI state.
#
# This does NOT make ``q`` / ``ctrl+c`` live inside a well-formed modal.
# ``app.quit`` is in ``ui/bindings.py`` ``_NON_PRIORITY_ACTIONS``, so both of
# its keys bind non-priority, and Textual's ``Screen._modal_binding_chain``
# truncates the chain at the first modal node — the App namespace is never
# consulted while a ``ModalScreen`` owns focus. The hatch fires only when the
# chain holds no modal at all: the wedged state, and a coordinator stuck in
# ``is_modal`` with nothing on the screen stack. Pinned both ways by
# ``tests/integration/test_modal_key_containment.py`` and
# ``tests/integration/test_keybinding_wiring.py``.
_MODAL_ROUTED_ACTIONS = frozenset(
    {
        "app.quit",
        "pane.switch_focus",
        "pane.switch_focus_back",
        "pane.move_up",
        "pane.move_down",
        "pane.descend",
        "pane.ascend",
        "pane.modal_left",
        "pane.modal_right",
    }
)

_PANE_SELECTION_ACTIONS = frozenset(
    {
        "pane.enter_multiselect",
        "pane.toggle_select",
        "pane.select_all",
        "pane.clear_selection",
        "pane.exit_multiselect",
    }
)


@dataclass(frozen=True, slots=True)
class _VersionBrowserOrigin:
    dual: DualPaneVM
    pane: PaneVM
    provider: object
    source: tuple[str, str]
    path: PathRef
    revision: int
    selected: object


@dataclass(frozen=True, slots=True)
class _FolderUploadOrigin:
    dual: DualPaneVM
    source: PaneVM
    destination: PaneVM
    bindings: tuple[object, ...]
    selected: object
    providers: tuple[object, object]


@dataclass(frozen=True, slots=True)
class _DiscoveryOrigin:
    service_id: str | None
    vm: object
    focus: Widget | None
    slot: FocusSlot
    focused_ids: frozenset[str]
    object_details: tuple[PaneVM, object, object, object, int, EntryRow] | None
    result_generation: int | None = None
    object_versions: _VersionBrowserOrigin | None = None
    folder_upload: _FolderUploadOrigin | None = None


@dataclass(frozen=True, slots=True)
class _DiscoverySourceTarget:
    service_id: str
    connection_kind: str
    connection_name: str
    region: str


async def _first_bytes(source: AsyncIterator[bytes], limit: int) -> AsyncGenerator[bytes, None]:
    """Yield chunks from ``source`` until ``limit`` bytes have been emitted.

    Bounds a Quick Look preview to the first ``limit`` bytes regardless of the
    provider's chunk size, truncating the final chunk if it would overshoot.
    """
    remaining = limit
    try:
        async for chunk in source:
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                yield chunk[:remaining]
                break
            yield chunk
            remaining -= len(chunk)
            if remaining <= 0:
                break
    finally:
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            await aclose()


async def _stream_preview(
    provider: FileSystemProvider, path: PathRef, limit: int
) -> AsyncIterator[bytes]:
    """Await the provider's stream, then yield only its first ``limit`` bytes.

    ``read_stream`` is ``async def`` (returns the iterator once awaited), so the
    await is deferred into this lazy generator — the file isn't opened until the
    view starts consuming the preview.
    """
    source = await provider.read_stream(path, chunk_size=limit)
    bounded = _first_bytes(source, limit)
    try:
        async for chunk in bounded:
            yield chunk
    finally:
        await bounded.aclose()


def _build_quick_look_content(
    entry: FileEntry, provider: FileSystemProvider, *, path: PathRef
) -> QuickLookContent:
    """Build a 64 KB Quick Look preview payload for ``entry``.

    ``mime`` is guessed from the filename (falling back to a generic binary
    type); ``chunks`` streams only the first ``_QUICK_LOOK_PREVIEW_BYTES``.
    """
    mime, _ = mimetypes.guess_type(entry.name)
    return QuickLookContent(
        title=entry.name,
        mime=mime or "application/octet-stream",
        chunks=_stream_preview(provider, path, _QUICK_LOOK_PREVIEW_BYTES),
        line_count_estimate=None,
        load_preview=partial(
            load_preview, provider, path, name=entry.name, mime=mime or "application/octet-stream"
        ),
    )


def _build_swap_candidates(
    ctx: AppContext,
) -> tuple[list[tuple[str, str | Connection]], list[str]]:
    """Build the (label, payload) ring for ``action_swap_source``,
    filtering out connections in ``ctx.unreachable_connections``.

    Returns ``(candidates, skipped_names)`` where ``skipped_names`` is
    the list of TOML section names / profile names that were filtered
    out (used by ``_raise_skip_toast`` to inform the user).
    """
    from aws_tui.services.s3.service import _format_pane_title

    candidates: list[tuple[str, str | Connection]] = [("local", "local")]
    skipped: list[str] = []
    for conn in _live_connections(ctx):
        if (conn.kind, conn.name) in ctx.unreachable_connections:
            skipped.append(conn.name)
            continue
        candidates.append((_format_pane_title(conn), conn))
    return candidates, skipped


def _service_source_candidates(ctx: AppContext, service_id: str) -> tuple[Connection, ...]:
    """Return AWS connections supported by one single-context service."""
    service = ctx.registry.get(service_id)
    return tuple(
        connection
        for connection in _live_connections(ctx)
        if connection.kind == "aws" and service.supports(connection)
    )


def _live_connections(ctx: AppContext) -> tuple[Connection, ...]:
    """Read the mutable connection catalog without crashing a live action."""
    try:
        return tuple(ctx.connection_resolver.list())
    except Exception as exc:
        ctx.log_sink.error(
            "app.live_connection_discovery.failed",
            error=redact_text(str(exc)),
            error_type=type(exc).__name__,
        )
        notifications.advise(
            ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="could not reload connections — keeping current source",
            toast_id="connection-discovery-failed",
        )
        return ()


def _service_source_contexts(
    ctx: AppContext,
    service_id: str,
) -> tuple[ServiceSourceContext, ...]:
    """Project supported connections into immutable source-picker values."""
    return tuple(
        ServiceSourceContext.from_connection(connection)
        for connection in _service_source_candidates(ctx, service_id)
    )


def _next_service_source(
    candidates: tuple[Connection, ...],
    active: Connection | None,
) -> Connection | None:
    """Return the next connection in a stable service source ring."""
    if not candidates:
        return None
    if active is None:
        return candidates[0]
    active_key = active.name, active.region
    for index, connection in enumerate(candidates):
        if (connection.name, connection.region) == active_key:
            return candidates[(index + 1) % len(candidates)]
    return candidates[0]


def _raise_skip_toast(ctx: AppContext, skipped: list[str]) -> None:
    """Raise a one-line INFO toast naming the skipped connections.

    No-op if ``skipped`` is empty. The toast id is built from a SORTED
    join so it is stable regardless of the iteration order of
    ``ConnectionResolver.list()``.
    """
    if not skipped:
        return
    # Keep displayed text in original (config-file) order; sort only the id.
    notifications.advise(
        ctx.root_vm.chrome.toast_stack,
        subject="Source",
        message=f"skipped unreachable {', '.join(skipped)}",
        toast_id=f"swap-skip-{','.join(sorted(skipped))}",
    )


def _raise_config_risk_toasts(ctx: AppContext) -> None:
    """Warn once at launch for plaintext or TLS-disabled S3 configs."""
    try:
        cfg = ctx.config_store.load()
    except Exception as exc:
        ctx.log_sink.warning(
            "app.config_risk_scan.failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return

    static_names: list[str] = []
    tls_disabled_names: list[str] = []
    for entry in cfg.connections.values():
        if entry.kind != "s3-compatible":
            continue
        if entry.credentials == "static":
            static_names.append(entry.name)
        if entry.verify_tls is False:
            tls_disabled_names.append(entry.name)

    if static_names:
        notifications.advise(
            ctx.root_vm.chrome.toast_stack,
            subject="Settings",
            message=f"plaintext static credentials in {', '.join(static_names)}",
            action="move secrets to keychain or env-backed credentials",
            toast_id="config-static-credentials",
        )
    if tls_disabled_names:
        notifications.advise(
            ctx.root_vm.chrome.toast_stack,
            subject="Settings",
            message=f"TLS verification disabled for {', '.join(tls_disabled_names)}",
            action="enable verify_tls unless this is local development",
            toast_id="config-verify-tls-disabled",
        )


def _join_path(base: str, name: str) -> str:
    """Append ``name`` to ``base`` with a single ``/`` separator. Used
    only by the copy confirm modal to surface source/destination paths."""
    if not base:
        return name
    if base.endswith("/"):
        return f"{base}{name}"
    return f"{base}/{name}"


def _mutation_log_context(dual: object) -> dict[str, str]:
    """Identity for a data-mutation log line.

    `app.copy.failed` and `app.delete.failed` are the ONLY log lines covering
    user data mutation — `src/aws_tui/domain/` and `src/aws_tui/vm/file_manager/`
    emit none — and they carried just an error and a file count. "My copy
    failed" therefore produced one context-free line that could not be
    correlated to a file, bucket or connection.
    """
    context: dict[str, str] = {}
    for side in ("left", "right"):
        pane = getattr(dual, side, None)
        path = getattr(pane, "path", None)
        if path is not None:
            context[f"{side}_path"] = str(path)
        provider = getattr(pane, "provider", None)
        identity = getattr(provider, "storage_identity", None)
        if identity is not None:
            context[f"{side}_storage"] = str(identity() if callable(identity) else identity)
    focused = getattr(dual, "focused_side", None)
    if focused is not None:
        context["focused_side"] = str(getattr(focused, "value", focused))
    return context


def _flash_cursor_fallback_marks(
    src_pane: object,
    targets: list[object],
    *,
    marked: bool,
) -> None:
    """Mark the rows a cursor-fallback copy/delete is acting on.

    NOT cosmetic, despite the name, and do not "simplify" it away.
    ``DualPaneVM.copy_across`` and ``PaneVM.delete_marked`` re-derive their
    own targets from ``src_pane.marked_entries`` and this app takes no
    target argument when it invokes them, so with nothing multi-selected
    these marks ARE the channel that tells the transfer which entry to move.
    Drop the call and a cursor-fallback copy or delete becomes a silent
    no-op. (That overloading is a known design debt — the operation's
    targets should be their own VM concept rather than a borrowed
    user-facing bit — but it is the contract today.)

    Routed through ``PaneVM.set_marked_entries`` rather than
    ``EntryVM.set_marked`` because the pane VM owns the collection and the
    notification. The rows themselves need no help: each one binds to its
    own ``EntryVM.on_property_changed`` and would repaint either way. What
    only ``set_marked_entries`` does is emit the pane-level ``"viewmodel"``
    notify, and that is the one thing that recomputes the footer summary's
    marked count — bypass it and the row lights up while the footer keeps
    reporting the pre-transfer line.

    ``src_pane`` is the pane captured when the action fired, not
    ``dual.focused_pane`` re-read here — focus can move while the confirm
    modal is open, and the flash must land on the pane the targets came
    from. ``getattr`` because the worker's collaborators are duck-typed
    (same convention as ``copy_across``/``delete_in_focused`` above).
    """
    setter = getattr(src_pane, "set_marked_entries", None)
    if setter is None:
        return
    setter(targets, marked=marked)


class AwsTuiApp(DeferredWorkerMixin, App[None]):
    """The aws-tui Textual application.

    Composition root, real version. Constructor accepts an optional
    :class:`AppContext` so tests / E2E journeys can inject pre-wired
    state instead of touching ``~/.config/aws-tui``.
    """

    TITLE = "aws-tui"
    SUB_TITLE = f"v{__version__}"

    # Declare the notifications layer so ToastStack floats above the
    # main layout instead of consuming flow space.
    #
    # ``#main-area`` and ``#content-host`` need explicit ``1fr`` sizing
    # so the Horizontal layout allocates the remaining width to the
    # content host after the always-visible NavMenu takes its fixed
    # width (single mode, no collapse/expand, sized by
    # ``nav_menu.NAV_MENU_WIDTH`` to center the longest service label
    # alongside the ``▌`` ribbon). Without
    # this, the DualPane mounted inside renders at zero width and the
    # user sees a blank screen at startup. The pre-#94 standalone
    # ServicesHamburger widget + the toggle/collapse modes were both
    # dropped in the always-visible nav rework.
    CSS = (
        """
    Screen {
        /* ``dropdown`` is declared between ``base`` and
           ``notifications`` so the EMR application picker's
           OptionList renders as a true overlay above the page
           body (not embedded in the LEFT column's flow, which
           was the long-standing "There's no dropdown!" bug —
           prior comments in ``application_picker.py`` already
           identified Screen as the missing piece). ``notifications``
           stays the top layer so toasts overlay everything,
           including any open picker. */
        layers: base dropdown notifications;
    }
    #main-area {
        height: 1fr;
        width: 1fr;
        /* Match HintLegend's ``margin: 0 1 1 1`` and BrandBanner's
           (override below) horizontal margin so all three pieces of
           top-level chrome line up on the same x-axis. User
           feedback: "I want the top border pane … the bottom border
           pane … and all that is in between to have the similar
           length and margins from the left and right edges of the
           screen". */
        margin: 0 1;
    }
    BrandBanner {
        margin: 1 1 0 1;
    }
    Screen.-compact-height BrandBanner {
        height: 1;
        margin: 0 1;
        border: none;
    }
    #content-host {
        height: 1fr;
        width: 1fr;
    }
    """
        + FIRST_RUN_FORM_CSS
    )

    # Minimum-viable input router (input-router-deferred from M6). The
    # Bindings are installed at runtime in ``__init__`` from
    # ``BindingResolver.to_textual_bindings()`` so ``config.toml``
    # ``[keybindings]`` overlays take effect. Textual's base-App bindings
    # (``ctrl+q`` quit, ``ctrl+p`` command palette) survive ``super().__init__``
    # and are preserved. The empty ClassVar keeps Textual's binding machinery
    # happy before the resolver install.
    BINDINGS: ClassVar[list[BindingType]] = []

    def __init__(self, context: AppContext | None = None) -> None:
        super().__init__()
        self._app_ctx = context if context is not None else build_app_context()
        # Theme CSS is injected as the App's stylesheet (see on_mount).
        self._actions = ActionRegistry()
        self._resolver = BindingResolver(
            keymap=self._app_ctx.keymap_store,
            actions=self._actions,
        )
        # Register handlers for the action ids the BindingResolver advertises.
        # The resolver materializes a Textual binding only for registered ids,
        # so deferred/unwired actions stay unbound. Each id maps to its existing
        # ``action_*`` handler.
        self._actions.register("app.quit", self._handle_quit)
        self._actions.register("pane.switch_focus", self.action_switch_focus)
        self._actions.register("pane.switch_focus_back", self.action_switch_focus_reverse)
        self._actions.register("pane.move_up", self.action_move_up)
        self._actions.register("pane.move_down", self.action_move_down)
        self._actions.register("pane.descend", self.action_descend)
        self._actions.register("pane.ascend", self.action_ascend)
        self._actions.register("pane.modal_left", self.action_modal_left_or_ascend)
        self._actions.register("pane.modal_right", self.action_modal_right)
        self._actions.register("pane.refresh", self.action_refresh)
        self._actions.register("auth.authenticate", self._handle_authenticate)
        self._actions.register("app.help", self.action_help)
        self._actions.register("app.themes", self.action_themes)
        self._actions.register("app.transfer_history", self.action_transfer_history)
        self._actions.register("app.focus_transfers", self.action_focus_transfers)
        self._actions.register("app.cycle_theme", self.action_cycle_theme)
        self._actions.register("app.open_settings", self.action_open_settings)
        for service_id in ("s3", "athena", "glue", "emr-serverless"):
            self._actions.register(
                f"service.open.{service_id}", partial(self._select_discovery_service, service_id)
            )
        self._actions.register("pane.copy", self.action_copy)
        self._actions.register("pane.copy_entry_path", self.action_copy_entry_path)
        self._actions.register("pane.copy_path", self.action_copy_path)
        self._actions.register("pane.delete", self.action_delete)
        self._actions.register("app.swap_source", self._handle_swap_source)
        self._actions.register("emr.next_application", self.action_next_emr_application)
        self._actions.register("emr.clone", self.action_clone_emr_run)
        self._actions.register("emr.cancel", self.action_cancel_emr_run)
        self._actions.register("emr.logs.filter", self.action_filter_emr_logs)
        self._actions.register("emr.logs.source", self.action_cycle_emr_log_source)
        self._actions.register("emr.logs.follow", self.action_toggle_emr_log_follow)
        for view in ("catalog", "jobs", "crawlers"):
            self._actions.register(
                f"glue.{view}",
                partial(
                    self._handle_service_read,
                    partial(self.action_select_glue_view, view),
                    group="service-view",
                ),
            )
        self._actions.register(
            "glue.choose_run_state",
            partial(
                self._handle_service_read, self.action_choose_glue_run_state, group="service-view"
            ),
        )
        self._actions.register(
            "glue.choose_crawler_state",
            partial(
                self._handle_service_read,
                self.action_choose_glue_crawler_state,
                group="service-view",
            ),
        )
        self._actions.register("glue.compare_tables", self.action_glue_compare_tables)
        self._actions.register(
            "glue.copy_table_ref",
            self.action_copy_glue_table_reference,
        )
        self._actions.register("glue.open_s3_location", self.action_open_glue_s3_location)
        self._actions.register("glue.query_in_athena", self.action_query_glue_table_in_athena)
        self._actions.register("glue.load_more", self.action_load_more_glue)
        self._actions.register("glue.iceberg_info", self.action_glue_iceberg_info)
        self._actions.register(
            "glue.time_travel_in_athena",
            self.action_time_travel_glue_table_in_athena,
        )
        for view in ("query", "history", "results", "saved"):
            self._actions.register(
                f"athena.{view}",
                partial(
                    self._handle_service_read,
                    partial(self.action_select_athena_view, view),
                    group="service-view",
                ),
            )
        self._actions.register(
            "athena.choose_workgroup",
            self.action_choose_athena_workgroup,
        )
        self._actions.register(
            "athena.choose_catalog",
            self.action_choose_athena_catalog,
        )
        self._actions.register(
            "athena.choose_database",
            self.action_choose_athena_database,
        )
        self._actions.register(
            "athena.insert_table_ref",
            self.action_insert_athena_table_reference,
        )
        self._actions.register("athena.execute", self.action_execute_athena)
        self._actions.register("athena.cancel", self.action_cancel_athena)
        for action_id, command in _ATHENA_SQL_ACTIONS.items():
            self._actions.register(action_id, partial(self._athena_sql_control, action_id, command))
        self._actions.register(
            "athena.load_more",
            partial(
                self._handle_service_read,
                self.action_load_more_athena,
                group="athena-load-more",
                require_focus=True,
            ),
        )
        for action in (
            "inspect_cell",
            "copy_cell",
            "copy_row",
            "filter_results",
            "sort_results",
            "reset_results",
        ):
            self._actions.register(f"athena.{action}", partial(self._athena_result_control, action))
        self._actions.register(
            "athena.open_result_location",
            self.action_open_athena_result_location,
        )
        self._actions.register("athena.open_in_glue", self.action_open_athena_table_in_glue)
        self._actions.register("pane.mark_up", self.action_mark_up)
        self._actions.register("pane.mark_down", self.action_mark_down)
        self._actions.register("pane.quick_look", self.action_quick_look)
        self._actions.register("pane.object_details", self.action_object_details)
        self._actions.register("pane.object_versions", self.action_object_versions)
        self._actions.register("pane.upload_folder", self.action_upload_folder)
        self._actions.register("pane.enter_multiselect", self.action_enter_multiselect)
        self._actions.register("pane.toggle_select", self.action_toggle_select)
        self._actions.register("pane.select_all", self.action_select_all)
        self._actions.register("pane.clear_selection", self.action_clear_selection)
        self._actions.register("pane.filter", partial(self._listing_control, "filter"))
        self._actions.register("pane.fuzzy_find", partial(self._listing_control, "find"))
        self._actions.register("pane.sort", partial(self._listing_control, "sort"))
        self._actions.register("pane.clear_filter", partial(self._listing_control, "clear"))
        self._actions.register("pane.exit_multiselect", self.action_exit_multiselect)
        self._actions.register("app.command_palette", self.action_command_palette)
        # Install the resolver-materialized bindings, keeping Textual's built-in
        # ``ctrl+q`` (alt-quit) and ``ctrl+p`` (command palette) that arrived via
        # ``super().__init__``. ``ctrl+c`` is overridden by the resolver's
        # ``app.quit`` binding (Textual's default maps it to help_quit).
        installed = list(self._resolver.to_textual_bindings())
        # Textual has no dynamic API that both replaces a key and preserves
        # Binding.priority. App.bind() appends and drops priority, so isolate
        # this compatibility point and guard the resulting map in integration
        # tests until Textual exposes an equivalent supported operation.
        for key, bindings in BindingsMap(installed).key_to_bindings.items():
            self._bindings.key_to_bindings[key] = list(bindings)
        # Action ring buffer feeds the crash dump per spec §7.10. Each entry
        # is a short ISO-timestamped action id string; we keep the most
        # recent ``_ACTION_RING_SIZE`` to bound memory.
        self._action_ring: deque[str] = deque(maxlen=_ACTION_RING_SIZE)
        self._last_action_id: str | None = None
        self._confirmation_pending = False
        # Populated by ``_handle_exception`` when Textual surfaces an
        # unhandled exception so ``main()`` can print the dump path and
        # re-raise after the app has torn down.
        self._crash_report: CrashReport | None = None
        self._content_mount_hosts: weakref.WeakKeyDictionary[
            Widget, tuple[Container, tuple[str, int] | None]
        ] = weakref.WeakKeyDictionary()
        self._content_mount_recovering: weakref.WeakSet[Widget] = weakref.WeakSet()
        self._recovery_mount_errors: dict[Widget, list[Exception]] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_complete = False
        self._shutdown_errors: tuple[tuple[str, str], ...] = ()
        self._command_palette_populated: bool = False
        self._transfers_discovery_active = bool(self._app_ctx.transfers_vm.active_count)
        self._discovery_origin: _DiscoveryOrigin | None = None
        self._discovery_palette_ids: set[str] = set()
        self._discovery_source_ids: dict[_DiscoverySourceTarget, str] = {}
        self._discovery_source_targets: dict[str, _DiscoverySourceTarget] = {}
        self._discovery_source_counter = 0
        self._refreshing_discovery = False
        self._object_details_focus_restoration: Callable[[], None] | None = None
        self._versions_focus_restoration: Callable[[], None] | None = None
        self._version_browsers: set[S3VersionsModal] = set()
        self._upload_reviews: set[RecursiveUploadModal] = set()
        self._pane_state_sub: DisposableBase | None = None
        self._connection_state_sub: DisposableBase | None = None
        self._connection_list_sub: DisposableBase | None = None
        self._nav_selection_sub: DisposableBase | None = None
        self._cursor_sub: DisposableBase | None = None
        self._service_navigation_sub: DisposableBase | None = None
        self._pending_table_handoff: _TableHandoffSnapshot | None = None
        self._palette_failure_sub: DisposableBase | None = None
        self._table_clipboard_sub: DisposableBase | None = None
        self.launch_ready = False
        self.launch_error: str | None = None
        # True while ``on_mount`` is driving the initial service mount —
        # gates ``_on_nav_selection_changed`` so the seed selected_id
        # change doesn't spawn a duplicate mount worker that races the
        # on_mount direct mount (blank-screen-at-startup regression).
        self._boot_in_flight: bool = False
        # Set to the active connection key while ``_initial_mount_worker``
        # is waiting for the LEFT pane to reach a terminal state for
        # that connection. ``_on_pane_state_changed`` resolves
        # ``_attempt_future`` when an UNREACHABLE / AUTH_REQUIRED /
        # FORBIDDEN / IDLE / EMPTY transition arrives for that key.
        # Filtering by key keeps stale messages from a just-disposed
        # PaneVM (the prior failed attempt) from resolving the future
        # for the next attempt.
        self._attempt_conn_key: tuple[str, str] | None = None
        self._attempt_future: asyncio.Future[str] | None = None
        # Tracks the last frozenset of skipped connection names shown in a
        # skip-toast so repeated Shift+S presses don't stack duplicate toasts.
        self._last_skip_toast_set: frozenset[str] | None = None
        self._source_revision = 0
        self._auth_recovery_task: asyncio.Task[None] | None = None

    @cached_property
    def service_navigation(self) -> ServiceNavigation:
        """Pure lazy construction also supports isolated legacy App test stubs."""
        return ServiceNavigation()

    @property
    def _table_navigation_generation(self) -> int:
        return self.service_navigation.generation

    @_table_navigation_generation.setter
    def _table_navigation_generation(self, generation: int) -> None:
        self.service_navigation.set_generation(generation)

    @property
    def _service_navigation_owner(self) -> tuple[str, int] | None:
        return self.service_navigation.owner

    @property
    def _service_navigation_closed(self) -> bool:
        return self.service_navigation.closed

    @property
    def _service_navigation_lock(self) -> asyncio.Lock:
        return self.service_navigation.service_lock

    @property
    def _table_navigation_lock(self) -> asyncio.Lock:
        return self.service_navigation.table_lock

    @property
    def _table_navigation_tasks(self) -> frozenset[asyncio.Task[None]]:
        return self.service_navigation.navigation_tasks

    @property
    def _table_handoff_rollbacks(self) -> frozenset[asyncio.Task[bool]]:
        return self.service_navigation.rollback_tasks

    @property
    def _chain_resolved_to_local(self) -> bool:
        return self.service_navigation.local_fallback

    @_chain_resolved_to_local.setter
    def _chain_resolved_to_local(self, local_only: bool) -> None:
        self.service_navigation.restore_fallback_state(
            local_only, self.service_navigation.fallback_connection
        )

    @property
    def _chain_initial_conn(self) -> Connection | None:
        return self.service_navigation.fallback_connection

    @_chain_initial_conn.setter
    def _chain_initial_conn(self, connection: Connection | None) -> None:
        self.service_navigation.restore_fallback_state(
            self.service_navigation.local_fallback, connection
        )

    @property
    def app_ctx(self) -> AppContext:
        return self._app_ctx

    def get_driver_class(self) -> type[Driver]:
        """Wrap the platform driver in the bracketed-paste guard.

        Textual 8.2.8's ``XTermParser`` can be stranded permanently inside a
        bracketed paste when the closing ``\\x1b[201~`` is split by more than
        ``ESCAPE_DELAY`` — the app keeps repainting while every key, ``q`` and
        ``Ctrl+C`` included, is swallowed into the paste buffer. See
        ``ui/paste_guard.py`` for the mechanism. Wrapping whatever driver the
        platform (or ``TEXTUAL_DRIVER``) chose keeps the guard on every real
        terminal path without refusing bracketed paste, which the Athena
        editor and the EMR log filter genuinely need.
        """
        return guarded_driver_class(super().get_driver_class())

    def compose(self) -> ComposeResult:
        # Profile/region/auth identity lives in the left pane's border:
        # the title shows the live path and the subtitle shows the connection.
        ctx = self._app_ctx
        yield BrandBanner(
            theme_name=ctx.initial_theme,
            hub=ctx.hub,
            demo=ctx.demo,
            identity=self._compact_identity,
            id="brand-banner",
        )
        with Horizontal(id="main-area"):
            # NavMenu mounts as the always-visible left rail. Post-
            # PR-#94 there is no hamburger / collapse / expand mode —
            # the rail is a fixed-width pane that joins the Tab cycle
            # like any other.
            yield NavMenu(
                vm=ctx.root_vm.services_menu,
                hub=ctx.hub,
                focus_coordinator=ctx.focus_coordinator,
                id="nav-menu",
            )
            yield Container(id="content-host")
        yield HintLegend(ctx.root_vm.chrome.hint_legend, hub=ctx.hub, id="hint-legend")
        yield ToastStack(ctx.root_vm.chrome.toast_stack, hub=ctx.hub, id="toast-stack")
        yield TransfersOverlay(
            ctx.transfers_vm,
            hub=ctx.hub,
            history_vm=ctx.transfer_history_vm,
            open_history=self._open_transfer_history,
            restore_focus=self._restore_transfer_focus,
            id="transfers-overlay",
        )

    def on_resize(self, event: events.Resize) -> None:
        self._sync_compact_chrome(event.size.height)

    def _compact_identity(self) -> str:
        ctx = self._app_ctx
        service_id = ctx.root_vm.content_host.current_id
        parts: list[str] = []
        if service_id == SETTINGS_NAV_ID:
            parts.append("Settings")
        elif service_id is not None:
            parts.append(ctx.registry.get(service_id).descriptor.label)
        connection = ctx.root_vm.active_connection
        if connection is not None:
            parts.extend((connection.name, connection.region))
        return " · ".join(parts)

    def _sync_compact_chrome(self, height: int) -> None:
        # Below 34 rows the banner, context and query controls leave fewer
        # than three rows for SQL. Keep the existing page and editor mounted.
        compact = height < 34
        for screen in self.screen_stack:
            screen.set_class(compact, "-compact-height")
            for banner in screen.query(BrandBanner):
                banner.compact = compact

    async def on_mount(self) -> None:
        ctx = self._app_ctx
        # Construct the VM tree.
        ctx.root_vm.construct()
        ctx.transfers_vm.construct()
        self._history_sub = ctx.hub.messages.subscribe(on_next=self._on_history_message)
        self._run_lifecycle_worker(ctx.transfer_history_vm.load, group="transfer-history-load")
        ctx.confirm_vm.construct()
        ctx.quick_look_vm.construct()
        ctx.command_palette_vm.construct()
        ctx.s3_connections_vm.construct()
        self.screen_change_signal.subscribe(self, self._sync_modal_focus, immediate=True)
        # NOTE: SettingsVM is intentionally NOT constructed here. It's
        # now built fresh per mount in ``_mount_settings_view`` because
        # ContentHostVM disposes its hosted VM on swap-out, and a
        # singleton would crash on the second Settings switch with
        # ``StatusTransitionError('Cannot construct from state Disposed.')``.

        if not ctx.demo:
            _raise_config_risk_toasts(ctx)

        self._apply_initial_theme()
        self._sync_compact_chrome(self.size.height)

        # Subscribe to PaneVM state transitions BEFORE switch_service so the
        # initial UNREACHABLE state-change (if the connection is offline) is
        # not missed (Bug 2: initial mount UNREACHABLE silently missed).
        self._pane_state_sub = ctx.hub.messages.subscribe(on_next=self._on_hub_message_pane_state)
        self._connection_state_sub = ctx.hub.messages.subscribe(
            on_next=self._on_connection_changed_for_recovery
        )
        self._connection_list_sub = ctx.hub.messages.subscribe(
            on_next=self._on_connection_list_changed
        )
        self._nav_selection_sub = ctx.hub.messages.subscribe(on_next=self._on_nav_selection_changed)
        # Subscribe to pane cursor / entries changes so the HintLegend can
        # mark ``pane.copy`` / ``pane.delete`` disabled when the cursor sits
        # on the ``..`` parent row. User feedback: "if the selected item is
        # the '..' representing the parent folder, I shouldn't be able to
        # invoke copy or delete commands and I expect them greyed out".
        self._cursor_sub = ctx.hub.messages.subscribe(on_next=self._on_hub_message_cursor)
        self._service_navigation_sub = ctx.hub.messages.subscribe(
            on_next=self._on_service_navigation_message
        )
        self._palette_failure_sub = ctx.hub.messages.subscribe(
            on_next=self._on_palette_action_message
        )
        self._table_clipboard_sub = ctx.table_clipboard_vm.on_property_changed.subscribe(
            on_next=self._on_table_clipboard_changed
        )

        if ctx.launch is not None:
            self._boot_in_flight = True
            self._run_lifecycle_worker(self._explicit_mount_worker, group="content-mount")
            return

        initial_conn = self._resolve_initial_connection()
        if initial_conn is not None:
            try:
                auth_state = ctx.aws_session.probe_token(initial_conn).state
            except Exception as exc:
                ctx.log_sink.error(
                    "app.initial_probe.failed",
                    name=initial_conn.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                auth_state = TokenState.MISSING
            await self.service_navigation.switch_connection(ctx.root_vm, initial_conn, auth_state)
            # Run the initial DualPane build in a background worker
            # so on_mount returns immediately and Textual paints the
            # chrome (banner, nav rail, hint legend, etc.) without
            # waiting for boto3 — see the worker docstring for the
            # blank-screen-on-launch history.
            #
            # AWS+EXPIRED is NOT routed to a special "auth required"
            # placeholder anymore (PR #55 originally did that). User
            # feedback after PR #59: an error-message-instead-of-panes
            # is a worse UX than panes-with-graceful-fallback. The
            # worker checks the auth state itself and, when EXPIRED,
            # skips the S3 build entirely (which would otherwise
            # block 15s on boto3 trying to refresh the SSO token) and
            # builds a LocalFS-only DualPane directly with a toast
            # telling the user how to recover.
            self._boot_in_flight = True
            self._run_lifecycle_worker(
                partial(self._initial_mount_worker, initial_conn=initial_conn),
                group="content-mount",
            )
        else:
            await self._mount_no_connection_placeholder()
            self._request_first_run_discovery()

        if ctx.demo:
            self.call_after_refresh(self._focus_demo_launch_nav)
            # Spec: one-shot Advisory toast on mount so the user
            # learns the in-session contract on first run. The
            # persistent banner subtitle keeps reminding them
            # afterwards.
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="Demo mode active — AWS data resets; local pane is real",
            )
        else:
            # Drop Textual's automatic first-focus pass. By default Textual
            # focuses the first focusable widget on mount, which on the S3
            # screen is the NavMenu's services OptionList — the user then
            # has to press Tab once just to MOVE focus off the rail before
            # the second Tab actually toggles LEFT ↔ RIGHT. Same
            # ``set_focus(None)`` trick the snapshot fixtures already use
            # (``_UnfocusedMixin``); deferred via ``call_after_refresh`` so
            # it runs AFTER Textual's first focus pass instead of being
            # silently undone by it.
            if initial_conn is not None:
                self.call_after_refresh(partial(self._drop_initial_focus, self.screen))

    def _drop_initial_focus(self, boot_screen: object) -> None:
        """Clear Textual's automatic first-focus pass — but only if the
        boot screen is still the active one.

        ``App.set_focus`` always writes into ``App.screen``, the TOP screen,
        so if anything pushed a modal between ``on_mount`` and this deferred
        callback this would blank the MODAL's focus instead of the main
        screen's. Same class of defect as the page-level projections guarded
        by ``ui/widgets/_focus_guard.is_on_active_screen``.
        """
        with contextlib.suppress(Exception):
            if self.screen is not boot_screen:
                return
            self.set_focus(None)

    async def on_unmount(self) -> None:
        await self._aws_tui_shutdown()

    def _sync_modal_focus(self, _screen: object) -> None:
        """Project Textual's screen stack into VMx modal precedence."""
        coordinator = self._app_ctx.focus_coordinator
        if len(self.screen_stack) > 1:
            self._object_details_focus_restoration = None
            self._versions_focus_restoration = None
            coordinator.modal_open()
        else:
            was_modal = coordinator.is_modal
            coordinator.modal_close()
            if was_modal:
                self.call_after_refresh(self._restore_focus_after_modal, coordinator.focused_slot)

    def _restore_focus_after_modal(self, slot: FocusSlot) -> None:
        details_restore = getattr(self, "_object_details_focus_restoration", None)
        self._object_details_focus_restoration = None
        versions_restore = self._versions_focus_restoration
        self._versions_focus_restoration = None
        coordinator = self._app_ctx.focus_coordinator
        if len(self.screen_stack) > 1 or coordinator.is_modal:
            return
        view = self._first_run_view()
        if view is not None:
            view.focus_default()
            return
        coordinator.set_focused_slot(slot)
        self._project_focus_slot(slot)
        if details_restore is not None:
            details_restore()
        if versions_restore is not None:
            versions_restore()

    def _dispose_table_clipboard_subscription(self) -> None:
        subscription = getattr(self, "_table_clipboard_sub", None)
        if subscription is None:
            return
        subscription.dispose()
        self._table_clipboard_sub = None

    async def _explicit_mount_worker(self) -> None:
        """Adopt and prove exactly one selected launch; never enter the boot chain."""
        from aws_tui.composition import connection_route

        ctx = self._app_ctx
        launch = ctx.launch
        assert launch is not None
        owner = asyncio.current_task()
        assert owner is not None
        try:
            async with asyncio.timeout(_BOOT_CHAIN_BUDGET_SECONDS):
                if launch.connection is None:
                    dual = self._compose_local_dual_pane(
                        initial_right_path=launch.location.path
                        if launch.location is not None
                        else None,
                    )
                    await self.service_navigation.adopt_content(ctx.root_vm, dual, service_id="s3")
                    setup = ctx.root_vm.content_host._setup_task
                    if setup is not None:
                        await setup
                    # ContentHost's setup wrapper consumes cancellation. Honor
                    # this worker's cancellation before any view/focus publication.
                    if owner.cancelling():
                        raise asyncio.CancelledError
                    host = self.query_one("#content-host", Container)
                    await self._replace_content_widget(
                        host,
                        DualPane(
                            dual,
                            hub=ctx.hub,
                            focus_coordinator=ctx.focus_coordinator,
                            id="content-dual-pane",
                        ),
                    )
                else:
                    connection = await asyncio.to_thread(
                        ctx.connection_resolver.resolve_selected, launch.connection.name
                    )
                    if connection_route(connection) != connection_route(launch.connection):
                        raise RuntimeError("selected source changed before startup")
                    await self.service_navigation.switch_connection_and_service(
                        ctx.root_vm, connection, TokenState.CONNECTED, launch.service_id
                    )
                    setup = ctx.root_vm.content_host._setup_task
                    if setup is not None:
                        await setup
                    # ContentHost's setup wrapper consumes cancellation. Honor
                    # this worker's cancellation before any view/focus publication.
                    if owner.cancelling():
                        raise asyncio.CancelledError
                    if not await self._mount_service_view(
                        launch.service_id, required_connection=connection
                    ):
                        raise RuntimeError("selected service could not be mounted")
                current = ctx.root_vm.content_host.current
                states: tuple[PaneState, ...]
                if isinstance(current, DualPaneVM):
                    states = (current.left.state, current.right.state)
                elif isinstance(current, (AthenaPageVM, GluePageVM)):
                    states = (current.credential_recovery_state(),)
                elif isinstance(current, EmrServerlessPageVM):
                    states = (
                        current.applications.state,
                        current.job_runs.state,
                        current.job_run_detail.state,
                    )
                else:
                    raise RuntimeError("selected service has no readiness state")
                if any(state not in {PaneState.IDLE, PaneState.EMPTY} for state in states):
                    raise RuntimeError("selected service did not become ready")
            if not self.service_navigation.closed and not self._exit:
                self.call_after_refresh(partial(self._finish_explicit_launch, current))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.service_navigation.closed and not self._exit:
                ctx.log_sink.error("app.explicit_launch.failed", error_type=type(exc).__name__)
                self.launch_error = "explicit launch failed for the selected source or location"
                self.exit(return_code=1)
        finally:
            self._boot_in_flight = False

    def _finish_explicit_launch(self, expected_vm: object) -> None:
        if (
            self.service_navigation.closed
            or self._exit
            or self._app_ctx.root_vm.content_host.current is not expected_vm
        ):
            return
        launch = self._app_ctx.launch
        assert launch is not None
        if launch.service_id == "s3":
            slot = (
                FocusSlot.S3_RIGHT
                if launch.location is not None and launch.location.scheme == "local"
                else FocusSlot.S3_LEFT
            )
            self._app_ctx.focus_coordinator.set_focused_slot(slot)
            self._project_focus_slot(slot)
        else:
            self.focus_active_service_pane()
        self.launch_ready = True

    async def _initial_mount_worker(self, *, initial_conn: Connection) -> None:
        """Walk the configured-connections chain, narrating each step
        via toasts, until one succeeds OR all fail (→ local-only).

        Boot UX history. v0.7 single-shot: try the resolved initial
        connection; on terminal-bad pane state, swap LEFT to LocalFS
        with a one-line toast. The user complaint after PR #69:
        "since it takes such a long time loading the proper content
        on the left side on app launch when it goes through all the
        fallbacks, I think we'd better show toast notifications
        when each source is about to be tried. And if that source
        turns out not available, we show another toast that it's
        not or that it failed, and then show another one about
        trying the next option and so on". So: build an ORDERED
        chain (initial first, then the rest in resolver order),
        and for each candidate raise a "▸ Trying <name>…" sticky
        INFO toast, attempt, dismiss it on outcome, raise a success
        SUCCESS or failure WARNING outcome toast. On chain
        exhaustion, raise a WARNING fallback toast and mount
        LocalFS-on-both-panes.

        AWS+(EXPIRED|MISSING) is short-circuited via offline
        ``probe_token`` so we don't burn the 15s SSO-refresh wait
        on a candidate we already know has no working session;
        s3-compatible and AWS+CONNECTED candidates are attempted
        live via ``switch_service("s3")`` and the LEFT pane's
        terminal state (IDLE / EMPTY = success; UNREACHABLE /
        AUTH_REQUIRED / FORBIDDEN = failure) signals the outcome
        through ``_attempt_future`` (resolved by
        ``_on_pane_state_changed`` for the matching connection key).

        The caller probes the initial connection before publishing it to
        ``RootVM``. This worker re-probes every AWS candidate because the
        chain can include connections that caller did not inspect.
        """
        ctx = self._app_ctx
        try:
            chain = self._build_attempt_chain(initial_conn)
            ctx.log_sink.info(
                "app.boot_chain.start",
                length=len(chain),
                initial=initial_conn.name,
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _BOOT_CHAIN_BUDGET_SECONDS
            for index, conn in enumerate(chain, start=1):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    ctx.log_sink.info(
                        "app.boot_chain.budget_exhausted",
                        attempted=index - 1,
                        total=len(chain),
                    )
                    break
                # Defer the "▸ Trying X…" toast so a fast happy boot
                # (e.g. silent SSO succeeds in <500ms) stays silent.
                # The deferred task self-cancels if the attempt
                # resolves before the grace window. We then ONLY
                # raise the success toast if the pre-attempt toast
                # was visible — otherwise the success is implicit
                # (panes populated) and a redundant success toast
                # would just flash by.
                pre_attempt_visible = False
                pre_attempt_task = asyncio.create_task(
                    self._raise_attempt_toast_after_grace(conn, index=index, total=len(chain))
                )
                try:
                    outcome = await self._try_connection(conn, timeout=remaining)
                finally:
                    pre_attempt_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pre_attempt_task
                    # If the grace window elapsed and the toast was
                    # actually raised, dismiss it before emitting the
                    # outcome toast.
                    if pre_attempt_task.done() and not pre_attempt_task.cancelled():
                        pre_attempt_visible = bool(pre_attempt_task.result())
                if pre_attempt_visible:
                    self._dismiss_attempt_toast(conn)
                if outcome == "ok":
                    if pre_attempt_visible:
                        self._raise_success_toast(conn)
                    ctx.log_sink.info(
                        "app.boot_chain.success",
                        kind=conn.kind,
                        name=conn.name,
                        attempt=index,
                    )
                    return
                self._mark_connection_unreachable(conn.kind, conn.name)
                self._raise_failure_toast(conn, outcome)
                ctx.log_sink.info(
                    "app.boot_chain.attempt_failed",
                    kind=conn.kind,
                    name=conn.name,
                    attempt=index,
                    outcome=outcome,
                )
                if outcome == "timeout":
                    ctx.log_sink.info(
                        "app.boot_chain.budget_exhausted",
                        attempted=index,
                        total=len(chain),
                    )
                    break
            # Chain exhausted — mount local on both panes and
            # remember the decision so a subsequent Settings → S3
            # toggle reproduces the local-on-both state instead of
            # walking the chain again (which would just replay the
            # same failures the user already saw).
            self._raise_local_fallback_toast()
            self.service_navigation.remember_local_fallback(initial_conn)
            mounted = await self._mount_local_only_dual_pane(
                initial_conn=initial_conn,
                reason="chain-exhausted",
            )
            if mounted:
                ctx.log_sink.info("app.boot_chain.local_fallback", initial=initial_conn.name)
            else:
                ctx.log_sink.error(
                    "app.boot_chain.local_fallback_failed",
                    initial=initial_conn.name,
                )
        finally:
            self._boot_in_flight = False

    def _build_attempt_chain(self, initial: Connection) -> list[Connection]:
        """Order the resolver's connection list with ``initial`` first.

        Per-launch ``unreachable_connections`` starts empty — nothing
        to skip on a cold start. If a previous attempt in this same
        boot marked a candidate unreachable, it's still in the chain
        but the pre-flight ``probe_token`` (for AWS) or the live
        attempt outcome will surface the same failure again, which
        is fine — the chain decides termination, not a static skip
        list. We DO de-duplicate so the initial isn't tried twice
        when it also appears in the resolver list.
        """
        ctx = self._app_ctx
        try:
            others = ctx.connection_resolver.list()
        except Exception as exc:
            ctx.log_sink.error(
                "app.boot_chain.resolver_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            others = []
        chain: list[Connection] = [initial]
        seen = {(initial.kind, initial.name)}
        for conn in others:
            key = (conn.kind, conn.name)
            if key in seen:
                continue
            chain.append(conn)
            seen.add(key)
        return chain

    async def _try_connection(
        self,
        conn: Connection,
        *,
        timeout: float = _BOOT_CHAIN_BUDGET_SECONDS,
    ) -> str:
        """Attempt to mount ``conn`` as the LEFT pane's source.

        Returns one of: ``"ok"``, ``"aws-sso-expired"``,
        ``"aws-no-creds"``, ``"unreachable"``, ``"auth-required"``,
        ``"forbidden"``, ``"timeout"``, ``"error"``. The chain
        narrator maps these into toast text.
        """
        ctx = self._app_ctx
        if conn.kind == "aws" and not ctx.demo:
            try:
                state = ctx.aws_session.probe_token(conn).state
            except Exception as exc:
                ctx.log_sink.error(
                    "app.boot_chain.probe_failed",
                    name=conn.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return "error"
            if state is TokenState.EXPIRED:
                return "aws-sso-expired"
            if state is TokenState.MISSING:
                return "aws-no-creds"
        # ``get_event_loop`` from inside a coroutine is deprecated in
        # 3.12+. ``_try_connection`` is always awaited from a worker,
        # so the running loop is the right and stable handle.
        loop = asyncio.get_running_loop()
        self._attempt_future = loop.create_future()
        self._attempt_conn_key = (conn.kind, conn.name)
        try:
            try:
                await self.service_navigation.switch_connection(
                    ctx.root_vm, conn, TokenState.CONNECTED
                )
                await self.service_navigation.switch_service(ctx.root_vm, "s3")
            except Exception as exc:
                ctx.log_sink.error(
                    "app.boot_chain.switch_failed",
                    name=conn.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return "error"
            if not await self._mount_initial_service_view():
                return "error"
            try:
                return await asyncio.wait_for(self._attempt_future, timeout=timeout)
            except TimeoutError:
                return "timeout"
        finally:
            self._attempt_future = None
            self._attempt_conn_key = None

    # ── Boot-chain narration (toasts) ──────────────────────────────────────

    @staticmethod
    def _attempt_toast_id(conn: Connection) -> str:
        # Must match the ``progress`` helper's id shape:
        # ``f"progress-{key}"``.
        return f"progress-boot-attempt-{conn.kind}-{conn.name}"

    @staticmethod
    def _outcome_toast_id(conn: Connection) -> str:
        return f"boot-outcome-{conn.kind}-{conn.name}"

    # Grace window before the "▸ Trying X…" toast appears, so a fast
    # happy boot (silent SSO succeeds in well under this) stays
    # silent. The slow-boot UX the user actually asked for narration
    # of is well above this threshold (a single boto SSO refresh or
    # an unreachable s3 endpoint takes seconds, not hundreds of ms).
    _ATTEMPT_TOAST_GRACE_SECONDS: ClassVar[float] = 0.5

    async def _raise_attempt_toast_after_grace(
        self, conn: Connection, *, index: int, total: int
    ) -> bool:
        """Raise the pre-attempt toast after a grace window.

        Returns True if the toast was raised (caller will dismiss
        it on outcome), False otherwise. The outer caller cancels
        this task when the attempt's outcome arrives — a cancel
        before the grace window elapses means no toast was raised,
        so success is silent.
        """
        await asyncio.sleep(self._ATTEMPT_TOAST_GRACE_SECONDS)
        ctx = self._app_ctx
        posted = False
        try:
            notifications.progress(
                ctx.root_vm.chrome.toast_stack,
                key=f"boot-attempt-{conn.kind}-{conn.name}",
                subject="Connection",
                message=(
                    f"trying {conn.name} ({self._friendly_kind(conn.kind)}) — {index}/{total}"
                ),
            )
            posted = True
        except Exception:
            # Toast stack disposed / full / otherwise unhappy. Caller
            # should NOT later try to dismiss a toast that was never
            # raised — return ``False`` to telegraph that.
            posted = False
        return posted

    def _dismiss_attempt_toast(self, conn: Connection) -> None:
        with contextlib.suppress(Exception):
            self._app_ctx.root_vm.chrome.toast_stack.dismiss(self._attempt_toast_id(conn))

    def _raise_success_toast(self, conn: Connection) -> None:
        ctx = self._app_ctx
        with contextlib.suppress(Exception):
            notifications.success(
                ctx.root_vm.chrome.toast_stack,
                subject="Connection",
                message=f"{conn.name} connected",
                toast_id=self._outcome_toast_id(conn),
            )

    def _raise_failure_toast(self, conn: Connection, outcome: str) -> None:
        ctx = self._app_ctx
        reason = self._friendly_outcome(outcome)
        with contextlib.suppress(Exception):
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message=f"{conn.name} {reason}",
                action="trying next",
                toast_id=self._outcome_toast_id(conn),
            )

    def _raise_local_fallback_toast(self) -> None:
        ctx = self._app_ctx
        with contextlib.suppress(Exception):
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Fallback",
                message="all sources unavailable, both panes set to local",
                action="press r in a pane to retry",
                toast_id="boot-fallback-local",
            )

    @staticmethod
    def _friendly_kind(kind: str) -> str:
        return {"aws": "AWS", "s3-compatible": "S3-compatible"}.get(kind, kind)

    @staticmethod
    def _friendly_outcome(outcome: str) -> str:
        return {
            "aws-sso-expired": "SSO token expired",
            "aws-no-creds": "no AWS credentials",
            "unreachable": "endpoint unreachable",
            "auth-required": "authentication required",
            "forbidden": "access denied",
            "timeout": "timed out",
            "error": "errored",
        }.get(outcome, outcome)

    def _compose_local_dual_pane(self, *, initial_right_path: PathRef | None = None) -> DualPaneVM:
        """Compose local panes without marking or inventing a failed remote source."""
        from aws_tui.domain.filesystem import PathRef
        from aws_tui.services.s3.service import S3Service
        from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
        from aws_tui.vm.file_manager.pane_vm import PaneVM

        ctx = self._app_ctx
        s3_service = cast("S3Service", ctx.registry.get("s3"))
        _make_local = self._make_local_provider
        left_provider = _make_local()
        right_provider = s3_service.build_initial_local_provider()
        native_root = (
            ctx.launch.location.native_root
            if ctx.launch is not None and ctx.launch.location is not None
            else None
        )
        left = PaneVM(
            provider=left_provider,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            id_prefix="pane.local",
            identity_label="local",
            path_protocol="",
            connection_key=None,
            transfer_connection=connection_history_identity(None, left_provider),
        )
        right = PaneVM(
            provider=right_provider,
            initial_path=initial_right_path or PathRef(),
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            id_prefix="pane.local",
            identity_label=f"local · {native_root}" if native_root is not None else "local",
            path_protocol="",
            connection_key=None,
            transfer_connection=connection_history_identity(None, right_provider),
        )
        journal = s3_service.transfer_journal
        return DualPaneVM(
            left=left,
            right=right,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            transfer_journal=journal,
            transfer_runtime=s3_service.transfer_runtime,
        )

    async def _mount_local_only_dual_pane(
        self,
        *,
        initial_conn: Connection,
        reason: str,
    ) -> bool:
        """Mount a DualPane with ``LocalFS`` on BOTH panes, bypassing
        ``S3Service.build_vm`` so we never construct an S3FS provider
        that would block 15s on boto3.

        Used by the AWS+EXPIRED proactive-fallback path. The
        ``initial_conn`` is still in ``ctx.unreachable_connections``
        so ``Shift+S`` skips it until a successful in-session credential
        retry clears the mark.
        """

        ctx = self._app_ctx
        await self._cancel_transfer_workers_before_content_swap()
        # Mark the connection unreachable so the swap-source ring
        # skips it (consistent with the reactive auto-fallback path).
        self._mark_connection_unreachable(initial_conn.kind, initial_conn.name)

        from aws_tui.services.s3.service import S3Service

        s3_service = cast("S3Service", ctx.registry.get("s3"))
        if s3_service.transfer_journal is None:
            ctx.log_sink.error("app.local_only_mount.missing_journal")
            return False
        dual = self._compose_local_dual_pane()
        try:
            # ContentHostVM constructs the candidate before retiring the
            # outgoing VM, so a malformed fallback cannot empty the host.
            await self.service_navigation.adopt_content(ctx.root_vm, dual, service_id="s3")
        except Exception as exc:
            ctx.log_sink.error(
                "app.local_only_mount.set_content_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False
        # Mount the widget.
        try:
            host = self.query_one("#content-host", Container)
            await self._replace_content_widget(
                host,
                DualPane(
                    dual,
                    hub=ctx.hub,
                    focus_coordinator=ctx.focus_coordinator,
                    id="content-dual-pane",
                ),
            )
        except Exception as exc:
            ctx.log_sink.error(
                "app.local_only_mount.mount_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

        # Recovery-hint toast. Originally sticky so the user could read
        # the recovery command at their own pace; the user reported the
        # sticky behavior felt intrusive ("remains there which is not
        # something I want, I want it to disappear after a count down"),
        # so switched to a non-sticky toast with a generous 12-second
        # timeout. That's long enough to read a one-line command,
        # short enough not to crowd subsequent toasts.
        profile = initial_conn.profile or initial_conn.name
        message, action = {
            "aws-sso-expired": (
                f"SSO expired for [b]{profile}[/], both panes set to local",
                f"run [b]aws sso login --profile {profile}[/], then press [b]a[/] to retry",
            ),
            "aws-no-creds": (
                f"no session for [b]{profile}[/], both panes set to local",
                "repair credentials externally, then press [b]a[/] to retry",
            ),
        }.get(
            reason,
            (f"{initial_conn.name} unavailable, both panes set to local", None),
        )
        # ``chain-exhausted`` callers (``_initial_mount_worker``)
        # already raised the boot-chain's own local-fallback toast;
        # don't double-up. Other callers (legacy direct invocation)
        # still get the per-reason recovery hint.
        if reason != "chain-exhausted":
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Auth",
                message=message,
                action=action,
                toast_id=f"initial-fallback-{reason}-{initial_conn.name}",
            )
        ctx.log_sink.info(
            "app.local_only_mount.success",
            reason=reason,
            kind=initial_conn.kind,
            name=initial_conn.name,
        )
        return True

    # ── on_mount helpers ───────────────────────────────────────────────────

    def _apply_initial_theme(self) -> None:
        """Apply the configured theme or a packaged, user-file-free fallback."""
        ctx = self._app_ctx
        configured_name = ctx.initial_theme
        failure: _ThemeApplyFailure | None
        try:
            theme_css = ctx.theme_store.load(configured_name)
        except (OSError, ThemeNotFound, UnicodeError) as exc:
            failure = _ThemeApplyFailure("load", exc)
        else:
            failure = self._apply_theme_css(configured_name, theme_css)

        if failure is None:
            ctx.initial_theme = configured_name
            return

        ctx.log_sink.error(
            "app.theme.initial_failed",
            name=configured_name,
            stage=failure.stage,
            error=str(failure.error),
            error_type=type(failure.error).__name__,
        )
        fallback_name = ThemeStore.DEFAULT_NAME
        try:
            fallback_css = ctx.theme_store.load_builtin(fallback_name)
        except (OSError, ThemeNotFound, UnicodeError) as exc:
            ctx.log_sink.error(
                "app.theme.fallback_failed",
                name=fallback_name,
                stage="load",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return

        fallback_failure = self._apply_theme_css(fallback_name, fallback_css)
        if fallback_failure is not None:
            ctx.log_sink.error(
                "app.theme.fallback_failed",
                name=fallback_name,
                stage=fallback_failure.stage,
                error=str(fallback_failure.error),
                error_type=type(fallback_failure.error).__name__,
            )
            return

        ctx.initial_theme = fallback_name
        self.query_one(BrandBanner).set_theme(fallback_name)
        ctx.log_sink.info(
            "app.theme.fallback_applied",
            configured_name=configured_name,
            fallback_name=fallback_name,
        )

    def _resolve_initial_connection(self) -> Connection | None:
        """Pick the initial connection in this order:

        1. ``[defaults].connection`` from config.toml, if it resolves.
        2. ``$AWS_PROFILE`` if exported — matches the AWS CLI's resolution
           order so the TUI lands on the same identity a user gets from
           ``aws s3 ls`` in the same shell. This is the SSO-recovery path
           for users whose ``[default]`` profile has no creds but whose
           working profile is the env var.
        3. The first auto-discovered profile (legacy fallback).
        4. ``None`` — the connection setup branch.
        """
        ctx = self._app_ctx
        try:
            cfg = ctx.config_store.load()
        except Exception as exc:
            ctx.log_sink.error(
                "app.config_load.failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            cfg = None
        # ``connection_resolver.list()`` calls ``_explicit_connections()``
        # which calls ``config_store.load()`` AGAIN — without this guard,
        # a malformed ~/.config/aws-tui/config.toml propagates the second
        # parse failure up through ``on_mount``, crashing the app with a
        # raw Python traceback before any UI lands. The first ``load()``
        # above is already guarded, but the resolver's internal load was
        # not. Fall back to an empty list so the boot chain reaches the
        # connection setup branch and the user gets a usable UI
        # + the ``app.config_load.failed`` log line for diagnostics.
        try:
            snapshot = ctx.connection_resolver.discover()
            if snapshot.invalid_sources:
                return None
            connections = ctx.connection_resolver.list()
        except Exception as exc:
            ctx.log_sink.error(
                "app.connection_resolver_list.failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            connections = []
        initial_conn = None
        if cfg is not None and cfg.defaults.connection:
            initial_conn = next(
                (c for c in connections if c.name == cfg.defaults.connection),
                None,
            )
        if initial_conn is None:
            env_profile = (
                os.environ.get("AWS_DEFAULT_PROFILE") or os.environ.get("AWS_PROFILE") or ""
            ).strip()
            if env_profile:
                initial_conn = next(
                    (c for c in connections if c.profile == env_profile),
                    None,
                )
        if initial_conn is None and connections:
            initial_conn = connections[0]
        return initial_conn

    async def _mount_initial_service_view(self) -> bool:
        """Mount the current service's view widget into the content host.

        ``switch_service`` updates the VM tree; the View layer has to follow
        explicitly — Textual won't infer that from VMx state.

        Connection attribution is now carried by ``PaneVM.current_connection_key``
        (set by ``S3Service.build_vm``), so no per-pane tracker is needed here.
        """
        ctx = self._app_ctx
        _svc_id: str | None = None
        try:
            current_vm = ctx.root_vm.content_host.current
            _svc_id = ctx.root_vm.content_host.current_id
            if current_vm is not None:
                host = self.query_one("#content-host", Container)
                replacement = build_service_view(
                    _svc_id or "unknown",
                    current_vm,
                    hub=ctx.hub,
                    keymap=getattr(ctx, "keymap_store", None),
                    source_candidates=_service_source_contexts(
                        ctx,
                        _svc_id or "unknown",
                    ),
                    focus_coordinator=ctx.focus_coordinator,
                )
                await self._replace_content_widget(host, replacement)
                if _svc_id in {"glue", "athena", "emr-serverless"}:
                    self._recompute_hint_disables()
            return True
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_service_view.failed",
                service_id=_svc_id or "unknown",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Surface the failure so the user gets *some* explanation
            # instead of a blank screen. Suppressed because the toast
            # stack may not be mounted yet during startup; the log
            # entry above is the durable record.
            with contextlib.suppress(Exception):
                notifications.error(
                    ctx.root_vm.chrome.toast_stack,
                    subject="Mount",
                    message=f"Service view failed: {type(exc).__name__}",
                    action=f"see {ctx.log_sink.path}",
                    toast_id="mount-service-failed",
                )
            return False

    async def _mount_no_connection_placeholder(self) -> None:
        """Mount the existing form and local setup guidance without opening a source."""
        ctx = self._app_ctx
        host = self.query_one("#content-host", Container)
        await self._replace_content_widget(host, FirstRunView(ctx.config_store.path, ctx.hub))

    def _first_run_view(self) -> FirstRunView | None:
        if not self.screen_stack:
            return None
        return next(
            (
                view
                for view in self.query(FirstRunView)
                if view.is_mounted and view.query(ConnectionFormInline)
            ),
            None,
        )

    def _first_run_owned(self, generation: int) -> bool:
        return not self.service_navigation.closed and self._service_navigation_is_owned_by(
            "first-run", generation
        )

    def _focus_first_run(self, view: FirstRunView, generation: int) -> None:
        if (
            self._first_run_owned(generation)
            and len(self.screen_stack) == 1
            and self._first_run_view() is view
        ):
            view.focus_default()

    def _set_first_run_busy(self, busy: bool) -> None:
        view = self._first_run_view()
        if view is not None:
            view.set_busy(busy)
        for section in self.query(FirstRunConnectionList):
            section.disabled = busy

    def _request_first_run_discovery(self, *, mount: bool = False) -> None:
        if self.service_navigation.closed:
            return
        generation = self._advance_service_navigation("first-run", cancel_table_tasks=True)
        self._set_first_run_busy(True)
        self._run_lifecycle_worker(
            partial(self._first_run_discovery_worker, generation, mount=mount),
            group="content-mount",
        )

    async def _discover_first_run(self, generation: int) -> ConnectionDiscovery | None:
        """Contain external credential failures without hiding resolver errors elsewhere."""
        try:
            snapshot = await asyncio.to_thread(self._app_ctx.connection_resolver.discover)
        except Exception as exc:
            if self._first_run_owned(generation):
                self._app_ctx.log_sink.error(
                    "app.first_run.discovery_failed", error_type=type(exc).__name__
                )
                view = self._first_run_view()
                if view is not None:
                    view.show_error(
                        "Unable to discover connections. Check application configuration and keychain access, then Retry discovery."
                    )
            return None
        return snapshot if self._first_run_owned(generation) else None

    async def _refresh_first_run_discovery(self, generation: int) -> ConnectionDiscovery | None:
        snapshot = await self._discover_first_run(generation)
        if snapshot is None:
            return None
        if not self._first_run_owned(generation):
            return None
        view = self._first_run_view()
        if view is not None:
            view.show_discovery(snapshot)
        await self.query_one(NavMenu).show_first_run_connections(snapshot)
        if not self._first_run_owned(generation):
            return None
        return snapshot

    async def _first_run_discovery_worker(self, generation: int, *, mount: bool) -> None:
        async with self.service_navigation.service_lock:
            if not self._first_run_owned(generation):
                return
            if mount:
                await self._mount_no_connection_placeholder()
                if not self._first_run_owned(generation):
                    return
            try:
                await self._refresh_first_run_discovery(generation)
            finally:
                if self._first_run_owned(generation):
                    self._set_first_run_busy(False)
                    view = self._first_run_view()
                    if view is not None:
                        self.call_after_refresh(partial(self._focus_first_run, view, generation))

    def on_first_run_connection_list_setup_requested(
        self, event: FirstRunConnectionList.SetupRequested
    ) -> None:
        event.stop()
        self._request_first_run_discovery(mount=True)

    def on_first_run_view_retry_requested(self, event: FirstRunView.RetryRequested) -> None:
        event.stop()
        self._request_first_run_discovery()

    def on_first_run_connection_list_connection_selected(
        self, event: FirstRunConnectionList.ConnectionSelected
    ) -> None:
        event.stop()
        if self.service_navigation.closed:
            return
        generation = self._advance_service_navigation("first-run", cancel_table_tasks=True)
        self._set_first_run_busy(True)
        self._run_lifecycle_worker(
            partial(self._first_run_select, event.name, generation), group="content-mount"
        )

    def on_connection_form_submitted(self, event: ConnectionFormSubmitted) -> None:
        view = self._first_run_view()
        form = event.control
        if (
            view is None
            or form is None
            or not form.is_attached
            or form is not view.query_one(ConnectionFormInline)
            or view not in form.ancestors_with_self
        ):
            return
        event.stop()
        if self.service_navigation.closed or event.mode != "add":
            return
        generation = self._advance_service_navigation("first-run", cancel_table_tasks=True)
        self._set_first_run_busy(True)
        # Persistence has its own worker: navigation away must not cancel a committed save.
        self._run_lifecycle_worker(
            partial(self._first_run_save, event, form, generation), group="first-run-save"
        )

    async def _first_run_save(
        self, event: ConnectionFormSubmitted, form: ConnectionFormInline, generation: int
    ) -> None:
        ctx = self._app_ctx
        entry = ctx.s3_connections_vm.entry_from_form(event.form)
        try:
            await ctx.s3_connections_vm.add_async(entry)
        except Exception as exc:
            if not self._first_run_owned(generation):
                if not self.service_navigation.closed:
                    notifications.error(
                        ctx.root_vm.chrome.toast_stack,
                        subject="Connection",
                        message="Unable to save connection. Check application configuration and keychain access, then try again.",
                    )
                return
            self._set_first_run_busy(False)
            if isinstance(exc, ValueError):
                form.mark_name_invalid()
                text = "Connection name already exists. Choose another name and Save and open."
            else:
                form.clear_submitting()
                text = "Unable to save connection. Check application configuration and keychain access, then Save and open again."
            view = self._first_run_view()
            if view is not None:
                view.show_error(text)
                self.call_after_refresh(partial(self._focus_first_run, view, generation))
            return
        if not self._first_run_owned(generation):
            if not self.service_navigation.closed:
                notifications.success(
                    ctx.root_vm.chrome.toast_stack,
                    subject="Connection",
                    message="Connection saved. Reopen Settings to refresh connections, or choose Connection setup to select it.",
                )
            return
        form.close()
        self._run_lifecycle_worker(
            partial(self._first_run_select, entry.name, generation), group="content-mount"
        )

    async def _first_run_select(self, name: str, generation: int) -> None:
        ctx = self._app_ctx
        async with self.service_navigation.service_lock:
            if not self._first_run_owned(generation):
                return
            try:
                snapshot = await self._refresh_first_run_discovery(generation)
                if snapshot is None:
                    return
                connection = next((c for c in snapshot.connections if c.name == name), None)
                if snapshot.invalid_sources or connection is None:
                    return
                try:
                    state = (await asyncio.to_thread(ctx.aws_session.probe_token, connection)).state
                except Exception:
                    state = TokenState.MISSING
                if not self._first_run_owned(generation):
                    return
                if state != TokenState.CONNECTED:
                    view = self._first_run_view()
                    if view is not None:
                        view.show_error(PROBE_FAILED)
                    return
                # Re-read identity after the probe; external edits must not adopt stale credentials.
                fresh = await self._discover_first_run(generation)
                if fresh is None:
                    return
                current = next((c for c in fresh.connections if c.name == name), None)
                if fresh.invalid_sources or current != connection:
                    await self._refresh_first_run_discovery(generation)
                    return
                with self.service_navigation.suppress_selection("s3"):
                    await self.service_navigation.switch_connection_and_service(
                        ctx.root_vm, connection, state, "s3"
                    )
                    if not self._first_run_owned(generation):
                        return
                    if not await self._mount_service_view("s3", required_connection=connection):
                        raise RuntimeError("service mount failed")
                    if not self._first_run_owned(generation):
                        return
                await self.query_one(NavMenu).show_first_run_connections(None)
                if not self._first_run_owned(generation):
                    return
            except Exception as exc:
                if not self._first_run_owned(generation):
                    return
                ctx.log_sink.error("app.first_run.open_failed", error_type=type(exc).__name__)
                await self._mount_no_connection_placeholder()
                if not self._first_run_owned(generation):
                    return
                recovered = await self._refresh_first_run_discovery(generation)
                if recovered is None:
                    return
                view = self._first_run_view()
                if view is not None:
                    view.show_error(
                        "Unable to open connection. Check the connection, then select it again."
                    )
            finally:
                if self._first_run_owned(generation):
                    self._set_first_run_busy(False)
                    view = self._first_run_view()
                    if view is not None:
                        self.call_after_refresh(partial(self._focus_first_run, view, generation))

    async def _cancel_transfer_workers_before_content_swap(self) -> None:
        """Stop copy and delete workers before disposing the active file panes.

        Both groups must be cancelled: they are deliberately separate so that
        confirming a delete does not cancel an in-flight copy.
        """
        from textual.worker import Worker, WorkerCancelled

        workers: list[Worker[object]] = []
        for group in _TRANSFER_WORKER_GROUPS:
            # Tolerate a falsy return: the real manager yields a list, but
            # callers stub this and a bare `for ... in None` would raise.
            cancelled = self.workers.cancel_group(self, group)
            if cancelled:
                workers.extend(cancelled)
        if workers:
            results = await asyncio.gather(
                *(worker.wait() for worker in workers),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, WorkerCancelled):
                    continue
                if isinstance(result, BaseException):
                    raise result

    # ── Action handlers ────────────────────────────────────────────────────

    async def action_quit(self) -> None:
        """Override Textual's built-in ``action_quit`` so every exit path
        (the ``q`` and ``ctrl+c`` BINDINGS above, plus Textual's own
        SIGINT handler, plus the action-registry ``app.quit`` bridge)
        flows through the async shutdown
        defined at :meth:`_aws_tui_shutdown`.

        Previously ``BINDINGS`` mapped ``q`` / ``ctrl+c`` to the bare
        ``"quit"`` action which Textual resolved to its sync
        :meth:`App.action_quit` (just ``self.exit()``), bypassing
        :meth:`_aws_tui_shutdown` entirely — aioboto3 client closures,
        in-flight transfer cancellation, log-sink flush, and VM
        disposal all leaked at exit.
        """
        self.record_action("app.quit")
        await self._aws_tui_shutdown()
        self.exit()

    def _handle_quit(self) -> None:
        # Synchronous fallback for the BindingResolver bridge that
        # cannot await. Schedule the async path on the event loop
        # so cleanup still runs instead of being silently dropped.
        self._run_lifecycle_worker(self.action_quit, group="shutdown")

    def action_dispatch(self, action_id: str, key: str | None = None) -> Awaitable[None] | None:
        """Single Textual action behind every resolver-materialized binding.

        Contextual bindings carry their normalized physical key; other bindings
        use only the action ID. This forwards to the :class:`ActionRegistry` that
        holds the real handler. Returning the handler's awaitable (if any)
        lets Textual await async actions.
        """
        if (
            len(self.screen_stack) > 1 or self._app_ctx.focus_coordinator.is_modal
        ) and action_id not in _MODAL_ROUTED_ACTIONS:
            return None
        # Physical aliases share context; named registry/palette actions keep
        # their explicit meaning even when users remap the pair independently.
        if (
            key is not None
            and action_id in {"pane.quick_look", "pane.toggle_select"}
            and self._bindings_overlap("pane.quick_look", "pane.toggle_select", key=key)
        ):
            pane = self._focused_file_pane()
            if pane is not None and pane.state is PaneState.LOADING:
                return None
            action_id = (
                "pane.toggle_select"
                if pane is not None and pane.is_multiselect_mode
                else "pane.quick_look"
            )
        elif (
            key is not None
            and action_id in {"auth.authenticate", "pane.select_all"}
            and self._bindings_overlap("auth.authenticate", "pane.select_all", key=key)
        ):
            pane = self._focused_file_pane()
            if pane is not None and pane.state is PaneState.LOADING:
                return None
            action_id = (
                "pane.select_all"
                if (
                    pane is not None
                    and pane.state in {PaneState.IDLE, PaneState.EMPTY}
                    and self._app_ctx.root_vm.active_auth_state
                    not in {TokenState.MISSING, TokenState.EXPIRED}
                )
                else "auth.authenticate"
            )
        elif (
            key is not None
            and action_id in {"glue.load_more", "athena.load_more"}
            and self._bindings_overlap("glue.load_more", "athena.load_more", key=key)
        ):
            service = self._app_ctx.root_vm.content_host.current_id
            if service not in {"glue", "athena"}:
                return None
            action_id = f"{service}.load_more"
        return self._actions.invoke(action_id)

    async def action_transfer_history(self) -> None:
        self.record_action("app.transfer_history")
        if len(self.screen_stack) == 1:
            self.query_one(TransfersOverlay).open_history("history")

    def action_focus_transfers(self) -> None:
        if len(self.screen_stack) == 1 and self._shutdown_task is None:
            self.record_action("app.focus_transfers")
            self.query_one(TransfersOverlay).focus_controls()

    def _focused_transfer_overlay(self) -> TransfersOverlay | None:
        if len(self.screen_stack) != 1 or self.focused is None:
            return None
        return next(
            (
                node
                for node in self.focused.ancestors_with_self
                if isinstance(node, TransfersOverlay)
            ),
            None,
        )

    def _restore_transfer_focus(self) -> None:
        if self._shutdown_task is None and len(self.screen_stack) == 1:
            self._project_focus_slot(self._app_ctx.focus_coordinator.focused_slot)

    def _open_transfer_history(self, mode: Literal["history", "recovery"]) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(
                TransferHistoryModal(
                    self._app_ctx.transfer_history_vm, hub=self._app_ctx.hub, mode=mode
                )
            )

    def _on_history_message(self, message: object) -> None:
        vm = self._app_ctx.transfer_history_vm
        if not isinstance(message, PropertyChangedMessage) or message.sender_object not in (
            vm,
            vm.runtime,
        ):
            return
        if message.property_name == "error_text":
            text = vm.runtime.error_text if message.sender_object is vm.runtime else vm.error_text
            if text:
                notifications.advise(
                    self._app_ctx.root_vm.chrome.toast_stack,
                    subject="Transfer",
                    message=text,
                    toast_id="transfer-history-warning",
                )

    def _refresh_transfer_history(self) -> None:
        if self._shutdown_task is None:
            self._run_lifecycle_worker(
                self._app_ctx.transfer_history_vm.load, group="transfer-history-load"
            )

    def _on_palette_action_message(self, message: object) -> None:
        if not isinstance(message, PaletteActionFailedMessage):
            return
        self._app_ctx.log_sink.error(
            "command_palette.action_failed",
            entry_id=message.entry_id,
            error_type=message.error_type,
        )
        notifications.error(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Command",
            message=f"{message.entry_id} failed ({message.error_type})",
            toast_id="command-palette-action-failed",
        )

    def _focused_file_pane(self) -> PaneVM | None:
        """Return the focused file-manager pane, or None when not applicable.

        Mirrors ``action_copy``'s dual-pane lookup; None when the file manager
        isn't the active page (e.g. the Settings / EMR pages have no panes).
        """
        dual = self._dual_pane()
        if dual is None:
            return None
        return getattr(dual, "focused_pane", None)

    def _discovery_file_pane(self, origin: _DiscoveryOrigin) -> PaneVM | None:
        """Resolve physical focus before inheriting a retained pane cursor."""
        return self._file_pane_at_focus(origin.focus, origin.slot)

    def _file_pane_at_focus(self, focus: Widget | None, slot: FocusSlot) -> PaneVM | None:
        dual = self._dual_pane()
        if dual is None:
            return None
        if focus is not None:
            if isinstance(focus, (Input, TextArea, Button, ModalButton)):
                return None
            owner = next(
                (node for node in focus.ancestors_with_self if isinstance(node, Pane)),
                None,
            )
            return owner.vm if owner is not None and owner.vm in (dual.left, dual.right) else None
        return (
            dual.left
            if slot is FocusSlot.S3_LEFT
            else dual.right
            if slot is FocusSlot.S3_RIGHT
            else None
        )

    def _ready_file_action_pane(self) -> PaneVM | None:
        """Use discovery's focused, live, ready-pane admission for every route."""
        if (
            len(self.screen_stack) != 1
            or self._app_ctx.focus_coordinator.is_modal
            or self._shutdown_task is not None
            or self.service_navigation.closed
        ):
            return None
        origin = self._capture_discovery_origin()
        if not self._discovery_origin_owned(origin):
            return None
        pane = self._discovery_file_pane(origin)
        return (
            pane if pane is not None and pane.state in {PaneState.IDLE, PaneState.EMPTY} else None
        )

    def _file_confirmation_guard(
        self,
        dual: DualPaneVM,
        source: PaneVM,
        targets: list[object],
        cursor_fallback: bool,
        destination: PaneVM | None = None,
    ) -> Callable[[], bool]:
        """Bind a confirmation to its exact listing, source and target identities."""
        origin = self._capture_discovery_origin()
        panes = (source,) if destination is None else (source, destination)
        tokens = tuple(
            (pane.provider, pane.path, pane.current_connection_key, pane.listing_revision)
            for pane in panes
        )
        confirmed = tuple(targets)

        def current() -> bool:
            if (
                self._shutdown_task is not None
                or self.service_navigation.closed
                or not self._discovery_origin_owned(origin)
                or self._dual_pane() is not dual
                or dual.focused_pane is not source
            ):
                return False
            for pane, (provider, path, connection, revision) in zip(panes, tokens, strict=True):
                if (
                    pane.state not in {PaneState.IDLE, PaneState.EMPTY}
                    or pane.provider is not provider
                    or pane.path != path
                    or pane.current_connection_key != connection
                    or pane.listing_revision != revision
                ):
                    return False
            marked = tuple(source.marked_entries)
            same_marks = len(marked) == len(confirmed) and all(
                entry is target for entry, target in zip(marked, confirmed, strict=True)
            )
            if cursor_fallback:
                # The worker's own transient mark may already be installed.
                return source.selected_entry is confirmed[0] and (not marked or same_marks)
            return same_marks

        return current

    def _file_confirmation_invalidated(self) -> None:
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Transfer",
            message="confirmation expired because the listing, selection or source changed",
            action="review the current entries and try again",
            toast_id="file-confirmation-invalidated",
        )

    def _execute_pane_selection(self, action_id: str, command_name: str) -> None:
        if len(self.screen_stack) > 1 or self._app_ctx.focus_coordinator.is_modal:
            return
        pane = self._ready_file_action_pane()
        if pane is None or pane.state not in {PaneState.IDLE, PaneState.EMPTY}:
            return
        self.record_action(action_id)
        command = getattr(pane, command_name)
        if command.can_execute():
            command.execute()

    def _listing_control(self, operation: str) -> None:
        if len(self.screen_stack) > 1 or self._app_ctx.focus_coordinator.is_modal:
            return
        pane = self._ready_file_action_pane()
        if pane is None or pane.state not in {PaneState.IDLE, PaneState.EMPTY}:
            return
        if operation == "clear":
            pane.set_filter_command.execute("")
        else:
            screen = {"filter": FilterPaneModal, "find": FindPaneModal, "sort": SortPaneModal}[
                operation
            ]
            self.push_screen(screen(pane))

    def action_enter_multiselect(self) -> None:
        self._execute_pane_selection("pane.enter_multiselect", "enter_multiselect_command")

    def action_toggle_select(self) -> None:
        self._execute_pane_selection("pane.toggle_select", "toggle_select_command")

    def action_select_all(self) -> None:
        self._execute_pane_selection("pane.select_all", "select_all_command")

    def action_clear_selection(self) -> None:
        self._execute_pane_selection("pane.clear_selection", "clear_selection_command")

    def action_exit_multiselect(self) -> None:
        self._execute_pane_selection("pane.exit_multiselect", "exit_multiselect_command")

    @staticmethod
    def _upload_bindings(source: PaneVM, destination: PaneVM) -> tuple[object, ...]:
        return tuple(
            value
            for pane in (source, destination)
            for value in (
                pane.path,
                pane.current_connection_key,
                pane.listing_revision,
                pane.status,
                pane.state,
                pane.path_protocol,
            )
        )

    def _upload_panes_mounted(self, source: PaneVM, destination: PaneVM) -> bool:
        root = self.screen_stack[0]
        return all(
            any(
                view.vm is pane
                and view.is_attached
                and view.is_mounted
                and not view.is_disabled
                and all(node.display and node.visible for node in view.ancestors_with_self)
                for view in root.query(Pane)
            )
            for pane in (source, destination)
        )

    def _folder_upload_origin(self) -> _FolderUploadOrigin | None:
        dual = self._dual_pane()
        source = self._file_pane_at_focus(
            self.focused, self._app_ctx.focus_coordinator.focused_slot
        )
        if dual is None or source is None:
            return None
        destination = dual.right if source is dual.left else dual.left
        if not self._upload_panes_mounted(source, destination):
            return None
        selected = source.selected_entry
        if (
            source.path_protocol not in {"", "file:", "local:"}
            or selected is None
            or selected.is_parent_link
            or selected.kind is not EntryKind.DIRECTORY
            or destination.path_protocol != "s3:"
            or not destination.path.segments
            or destination.current_connection_key is None
            or not all(
                pane.is_constructed and pane.state in {PaneState.IDLE, PaneState.EMPTY}
                for pane in (source, destination)
            )
        ):
            return None
        for provider, methods in (
            (
                source.provider,
                ("inspect_upload_entry", "list_upload_directory", "read_upload_stream"),
            ),
            (
                destination.provider,
                ("upload_location", "inspect_upload_destination", "write_upload_stream"),
            ),
        ):
            if any(not callable(getattr(provider, method, None)) for method in methods):
                return None
        return _FolderUploadOrigin(
            dual,
            source,
            destination,
            self._upload_bindings(source, destination),
            selected,
            (source.provider, destination.provider),
        )

    def _folder_upload_origin_current(self, origin: _FolderUploadOrigin | None) -> bool:
        return origin is not None and (
            self._shutdown_task is None
            and self._dual_pane() is origin.dual
            and origin.source.provider is origin.providers[0]
            and origin.destination.provider is origin.providers[1]
            and self._upload_panes_mounted(origin.source, origin.destination)
            and self._upload_bindings(origin.source, origin.destination) == origin.bindings
            and origin.source.selected_entry is origin.selected
        )

    def action_upload_folder(self) -> None:
        self._open_folder_upload(self._folder_upload_origin())

    def _open_folder_upload(self, origin: _FolderUploadOrigin | None) -> None:
        if (
            len(self.screen_stack) != 1
            or self._app_ctx.focus_coordinator.is_modal
            or not self._folder_upload_origin_current(origin)
        ):
            return
        current = self._folder_upload_origin()
        if (
            current is None
            or origin is None
            or current.source is not origin.source
            or current.selected is not origin.selected
        ):
            return
        ctx = self._app_ctx
        self.record_action("pane.upload_folder")
        vm = RecursiveUploadVM(source=origin.source, destination=origin.destination, hub=ctx.hub)
        vm.construct()
        screen: RecursiveUploadModal

        def submit() -> None:
            if self._confirmation_pending or ctx.confirm_vm.is_open or vm.state != "ready":
                return
            self._confirmation_pending = True
            self._run_lifecycle_worker(
                partial(self._confirm_folder_upload, screen, origin.dual),
                group="confirmation",
                exit_on_error=False,
            )

        screen = RecursiveUploadModal(
            vm, submit=submit, on_drained=lambda: self._upload_reviews.discard(screen)
        )
        self._upload_reviews.add(screen)
        focused, slot = self.focused, ctx.focus_coordinator.focused_slot

        def dismissed(_result: None) -> None:
            def restore() -> None:
                if self._shutdown_task is None and self._dual_pane() is origin.dual:
                    ctx.focus_coordinator.set_focused_slot(slot)
                    if focused is None or (focused.is_attached and focused.screen is self.screen):
                        self.set_focus(focused)

            self._versions_focus_restoration = restore

        self.push_screen(screen, dismissed)

    async def _confirm_folder_upload(self, screen: RecursiveUploadModal, dual: DualPaneVM) -> None:
        ctx = self._app_ctx
        vm = screen.vm
        try:
            prepared = vm.prepare()
            if (
                self._shutdown_task is not None
                or self._dual_pane() is not dual
                or not self._upload_panes_mounted(vm.source_pane, vm.destination_pane)
                or not vm.accepts(prepared)
            ):
                self._file_confirmation_invalidated()
                return
            request = ConfirmRequest(
                title="Upload reviewed folder objects?",
                paths=(
                    ConfirmPath("Local source", vm.source_label),
                    ConfirmPath("S3 prefix", vm.destination_label),
                ),
                body_lines=(
                    f"{len(prepared.plan.objects)} objects · {prepared.plan.known_bytes} bytes · {prepared.plan.conflicts} conflicts · {prepared.plan.policy.value}",
                    "Empty folders omitted. Published objects remain on cancel.",
                ),
                confirm_label="Upload",
                danger=prepared.plan.policy.value == "overwrite",
            )
            dialogs = TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub)
            if not await ctx.confirm_vm.ask(request, dialog_service=dialogs):
                return
            if (
                self._shutdown_task is not None
                or self._dual_pane() is not dual
                or not self._upload_panes_mounted(vm.source_pane, vm.destination_pane)
                or not vm.accepts(prepared)
            ):
                self._file_confirmation_invalidated()
                return
            vm.accept(prepared)
            self._run_lifecycle_worker(
                partial(self._run_folder_upload, vm, prepared, dual),
                group=_TRANSFER_COPY_GROUP,
                exclusive=False,
                exit_on_error=False,
            )
        except Exception as error:
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                subject="Transfer",
                message=redact_text(str(error)) or "Upload review unavailable",
            )
        finally:
            self._confirmation_pending = False

    async def _run_folder_upload(
        self, vm: RecursiveUploadVM, prepared: PreparedUpload, dual: DualPaneVM
    ) -> None:
        try:
            await vm.execute(prepared, dual.transfer_runtime)
        except Exception as error:
            notifications.error(
                self._app_ctx.root_vm.chrome.toast_stack,
                subject="Transfer",
                message=redact_text(str(error)) or "Folder upload failed",
            )
        finally:
            self._refresh_transfer_history()
            pane = vm.destination_pane
            if (
                self._shutdown_task is None
                and pane.provider is cast(object, prepared.plan.destination)
                and pane.path == prepared.plan.destination_prefix.parent()
            ):
                await pane.refresh()

    def _versions_origin(self) -> _VersionBrowserOrigin | None:
        dual = self._dual_pane()
        pane = self._file_pane_at_focus(self.focused, self._app_ctx.focus_coordinator.focused_slot)
        if (
            dual is None
            or pane is None
            or pane.state not in {PaneState.IDLE, PaneState.EMPTY}
            or pane.path_protocol != "s3:"
            or pane.current_connection_key is None
            or pane.current_connection_key[0] not in {"aws", "s3-compatible"}
            or not isinstance(pane.provider, S3VersionProvider)
        ):
            return None
        try:
            pane.provider.version_location(pane.path)
        except Exception:
            return None
        return _VersionBrowserOrigin(
            dual,
            pane,
            pane.provider,
            pane.current_connection_key,
            pane.path,
            pane.listing_revision,
            pane.selected_entry,
        )

    def _versions_origin_current(self, origin: _VersionBrowserOrigin | None) -> bool:
        return origin is not None and (
            self._shutdown_task is None
            and self._dual_pane() is origin.dual
            and origin.pane.provider is origin.provider
            and origin.pane.path == origin.path
            and origin.pane.current_connection_key == origin.source
            and origin.pane.listing_revision == origin.revision
            and origin.pane.selected_entry is origin.selected
            and origin.pane.is_constructed
            and origin.pane.state in {PaneState.IDLE, PaneState.EMPTY}
        )

    def action_object_versions(self) -> None:
        self._open_versions(self._versions_origin())

    def _open_versions(self, origin: _VersionBrowserOrigin | None) -> None:
        if (
            len(self.screen_stack) != 1
            or self._app_ctx.focus_coordinator.is_modal
            or not self._versions_origin_current(origin)
            or self._versions_origin() != origin
        ):
            return
        assert origin is not None
        ctx = self._app_ctx
        self.record_action("pane.object_versions")
        vm = S3VersionsVM(pane=origin.pane, hub=ctx.hub, dispatcher=ctx.dispatcher)
        vm.construct()
        destination = origin.dual.right if origin.pane is origin.dual.left else origin.dual.left
        local_scope = (
            destination.viewmodel.border_title
            if destination.path_protocol in {"", "file:", "local:"}
            else None
        )
        screen: S3VersionsModal

        def submit(mode: str, value: str) -> None:
            if (
                self._confirmation_pending
                or ctx.confirm_vm.is_open
                or not self._versions_origin_current(origin)
            ):
                return
            self._confirmation_pending = True
            self._run_lifecycle_worker(
                partial(self._confirm_version_transfer, screen, origin, destination, mode, value),
                group="confirmation",
                exit_on_error=False,
            )

        screen = S3VersionsModal(
            vm,
            local_scope=local_scope,
            submit=submit,
            on_drained=lambda: self._version_browsers.discard(screen),
        )
        self._version_browsers.add(screen)
        focused, slot = self.focused, ctx.focus_coordinator.focused_slot

        def lifetime_changed(_message: object) -> None:
            if not self._versions_origin_current(origin):
                screen.action_close()

        subscription = ctx.hub.messages.subscribe(lifetime_changed)
        screen._source_subscription = subscription

        def dismissed(_result: None) -> None:
            subscription.dispose()

            def restore() -> None:
                if self._versions_origin_current(origin):
                    ctx.focus_coordinator.set_focused_slot(slot)
                    if focused is None or (focused.is_attached and focused.screen is self.screen):
                        self.set_focus(focused)

            # Textual calls the dismissal callback before popping this screen.
            # _restore_focus_after_modal applies this guarded owner after pop.
            self._versions_focus_restoration = restore

        self.push_screen(screen, dismissed)

    async def _confirm_version_transfer(
        self,
        screen: S3VersionsModal,
        origin: _VersionBrowserOrigin,
        destination: PaneVM,
        mode: str,
        value: str,
    ) -> None:
        ctx = self._app_ctx
        try:
            if not self._versions_origin_current(origin) or screen.vm.state == "closed":
                return
            prepared = (
                screen.vm.prepare_download(destination, value)
                if mode == "download"
                else await screen.vm.prepare_recovery(value)
            )
            if not screen.vm.accepts(prepared) or not self._versions_origin_current(origin):
                self._file_confirmation_invalidated()
                return
            request = prepared.request
            body: tuple[str, ...] = (
                "Current key is not replaced; existing destinations are refused.",
            )
            if mode == "recovery":
                body += (
                    "Copies content/user metadata + tags. Destination policy defaults apply.",
                    "ACL, encryption, lock and storage policy are not copied.",
                    "Cancel may leave a published NEW key; inspect it before retrying.",
                )
            confirm = ConfirmRequest(
                title="Download historical version?"
                if mode == "download"
                else "Recover historical version to a new key?",
                paths=(
                    ConfirmPath("Historical source", request.source_uri),
                    ConfirmPath("New destination", request.destination_label),
                ),
                body_lines=body,
                confirm_label="Download" if mode == "download" else "Create new key",
            )
            dialogs = TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub)
            if not await ctx.confirm_vm.ask(confirm, dialog_service=dialogs):
                return
            if not screen.vm.accepts(prepared) or not self._versions_origin_current(origin):
                self._file_confirmation_invalidated()
                return
            screen.action_close()
            self.record_action("pane.copy")
            self._run_lifecycle_worker(
                partial(self._run_version_transfer, request, origin, destination),
                group=_TRANSFER_COPY_GROUP,
                exclusive=False,
                exit_on_error=False,
            )
        except Exception as error:
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                subject="Transfer",
                message=redact_text(str(error)) or "Historical transfer unavailable",
            )
        finally:
            self._confirmation_pending = False

    async def _run_version_transfer(
        self, request: VersionTransferRequest, origin: _VersionBrowserOrigin, destination: PaneVM
    ) -> None:
        completed = False
        ctx = self._app_ctx
        try:
            completed = await execute_version_transfer(request, origin.dual.transfer_runtime)
        except Exception as error:
            safe = redact_text(str(error)) or "Historical transfer failed"
            ctx.log_sink.error(
                "app.version_transfer.failed", error=safe, error_type=type(error).__name__
            )
            notifications.error(ctx.root_vm.chrome.toast_stack, subject="Transfer", message=safe)
        finally:
            self._refresh_transfer_history()
            if self._shutdown_task is None:
                if not completed and request.warning:
                    notifications.advise(
                        ctx.root_vm.chrome.toast_stack, subject="Transfer", message=request.warning
                    )
                pane = destination if request.kind == "download" else origin.pane
                provider = request.destination if request.kind == "download" else request.source
                path = (
                    request.destination_path.parent() if request.kind == "download" else origin.path
                )
                if (
                    self._dual_pane() is origin.dual
                    and pane.provider is provider
                    and pane.path == path
                ):
                    try:
                        await pane.refresh()
                    except Exception as error:
                        notifications.advise(
                            ctx.root_vm.chrome.toast_stack,
                            subject="Transfer",
                            message="Destination refresh failed: "
                            + (redact_text(str(error)) or "read unavailable"),
                        )

    def _object_details_origin(self) -> tuple[PaneVM, object, object, object, int, EntryRow] | None:
        """Resolve physical ownership before consulting the pane cursor.

        File rows use cursor focus and deliberately leave Textual focus unset.
        A focused source/navigation control must never inherit that cursor.
        """
        dual = self._dual_pane()
        if dual is None:
            return None
        focused = self.focused
        pane = None
        if focused is not None:
            if isinstance(focused, (Input, TextArea)):
                return None
            owner = next(
                (node for node in focused.ancestors_with_self if isinstance(node, Pane)), None
            )
            if owner is None:
                return None
            pane = owner.vm
            row = next(
                (node for node in focused.ancestors_with_self if isinstance(node, EntryRow)), None
            )
            if row is not None and row.entry_vm is not pane.selected_entry:
                return None
        else:
            slot = self._app_ctx.focus_coordinator.focused_slot
            if slot not in {FocusSlot.S3_LEFT, FocusSlot.S3_RIGHT}:
                return None
            pane = dual.left if slot is FocusSlot.S3_LEFT else dual.right
        entry = pane.selected_entry
        if (
            pane not in (dual.left, dual.right)
            or pane.state is not PaneState.IDLE
            or pane.path_protocol != "s3:"
            or pane.current_connection_key is None
            or pane.current_connection_key[0] not in {"aws", "s3-compatible"}
            or entry is None
            or entry.kind is not EntryKind.FILE
            or entry.is_parent_link
            or not isinstance(pane.provider, S3ObjectDetailsProvider)
        ):
            return None
        rendered_row = next(
            (
                row
                for row in self.screen.query(EntryRow)
                if row.entry_vm is entry
                and any(isinstance(owner, Pane) and owner.vm is pane for owner in row.ancestors)
            ),
            None,
        )
        if rendered_row is None or not rendered_row.is_attached:
            return None
        return (
            pane,
            entry,
            pane.provider,
            pane.current_connection_key,
            pane.listing_revision,
            rendered_row,
        )

    def action_object_details(self) -> None:
        """Open current-object properties only from the visible owning pane."""
        if len(self.screen_stack) > 1 or self._app_ctx.focus_coordinator.is_modal:
            return
        self._open_object_details(self._object_details_origin())

    def _open_object_details(
        self, origin: tuple[PaneVM, object, object, object, int, EntryRow] | None
    ) -> None:
        if len(self.screen_stack) > 1 or self._app_ctx.focus_coordinator.is_modal:
            return
        if origin is None or self._object_details_origin() != origin:
            notifications.advise(
                self._app_ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="Select a focused S3 object to inspect its details",
                toast_id="s3-details-unavailable",
            )
            return
        pane = origin[0]
        self.record_action("pane.object_details")
        vm = S3ObjectDetailsVM(
            pane=pane, hub=self._app_ctx.hub, dispatcher=self._app_ctx.dispatcher
        )
        vm.construct()
        screen = S3ObjectDetailsModal(
            vm, copy_value=partial(self._put_on_clipboard, label="S3 object detail")
        )
        dual = self._dual_pane()
        host = self._app_ctx.root_vm.content_host
        focused_origin = self.focused

        def restore_focus() -> None:
            if (
                self._object_details_origin() == origin
                and focused_origin is not None
                and focused_origin.is_attached
                and focused_origin.screen is self.screen
            ):
                self.set_focus(focused_origin)

        # Content/page replacement can destruct panes without a pane property
        # event. Watch host publication as well as source/provider changes.
        def lifetime_changed(_message: object) -> None:
            if (
                host.current is not dual
                or pane.provider is not origin[2]
                or pane.current_connection_key != origin[3]
            ):
                screen.action_close()

        subscription = self._app_ctx.hub.messages.subscribe(lifetime_changed)
        screen._source_subscription = subscription

        def dismissed(_result: None) -> None:
            subscription.dispose()
            self._object_details_focus_restoration = (
                restore_focus if len(self.screen_stack) == 1 else None
            )
            if (
                host.current is not dual
                or pane.provider is not origin[2]
                or pane.current_connection_key != origin[3]
            ):
                focused = self.focused
                if focused is not None and any(
                    isinstance(owner, Pane) and owner.vm is pane
                    for owner in focused.ancestors_with_self
                ):
                    self.set_focus(None)

        self.push_screen(screen, dismissed)

    def action_quick_look(self) -> None:
        """Open a 64 KB Quick Look preview for the focused pane's cursor file.

        Bound to ``Space`` via ``pane.quick_look``. No-op unless a file sits
        under the cursor — directories, the ``..`` parent link, and an empty
        pane are ignored.
        """
        self.record_action("pane.quick_look")
        if self._focused_transfer_overlay() is not None:
            return
        glue_page = self._glue_page()
        if glue_page is not None and glue_page.activate_focused(space=True):
            return
        emr_page = self._emr_page()
        if emr_page is not None and emr_page.activate_focused():
            return
        pane = self._ready_file_action_pane()
        if pane is None:
            return
        entry_vm = pane.selected_entry
        if entry_vm is None or entry_vm.kind is not EntryKind.FILE:
            return
        entry = entry_vm.entry
        content = _build_quick_look_content(entry, pane.provider, path=pane.path.join(entry.name))
        self._app_ctx.quick_look_vm.open_command.execute(content)
        self.push_screen(QuickLook(self._app_ctx.quick_look_vm, hub=self._app_ctx.hub))

    def _capture_discovery_origin(self) -> _DiscoveryOrigin:
        host = self._app_ctx.root_vm.content_host
        focused = self.focused
        ids = (
            frozenset(node.id for node in focused.ancestors_with_self if node.id)
            if focused
            else frozenset()
        )
        page = self._athena_page()
        return _DiscoveryOrigin(
            host.current_id,
            host.current,
            focused,
            self._app_ctx.focus_coordinator.focused_slot,
            ids,
            self._object_details_origin(),
            page.vm.results.projection_generation if page and page.vm is host.current else None,
            self._versions_origin(),
            self._folder_upload_origin(),
        )

    def _discovery_origin_owned(self, origin: _DiscoveryOrigin) -> bool:
        host = self._app_ctx.root_vm.content_host
        if host.current_id != origin.service_id or host.current is not origin.vm:
            return False
        page = {
            "athena": self._athena_page,
            "glue": self._glue_page,
            "emr-serverless": self._emr_page,
        }.get(origin.service_id or "")
        if page is not None:
            mounted = page()
            return (
                mounted is not None
                and mounted.is_attached
                and not mounted.is_disabled
                and mounted.vm is origin.vm
            )
        if origin.service_id == "s3":
            return any(view.is_attached and view.vm is origin.vm for view in self.query(DualPane))
        if origin.service_id == SETTINGS_NAV_ID:
            return any(
                view.is_attached and view.vm is origin.vm for view in self.query(SettingsView)
            )
        return True

    def _readiness_disabled(self, focused_ids: frozenset[str] | None = None) -> frozenset[str]:
        """Existing footer predicates, with an optional underlying focus snapshot."""
        emr = self._emr_page()
        if emr is not None:
            return frozenset() if emr.vm.can_cancel_selected_run() else frozenset({"emr.cancel"})
        athena = self._athena_page()
        if athena is not None:
            disabled: set[str] = set()
            authoring = tuple(athena.query(AthenaQueryView))
            disabled.update(
                action
                for action, command in _ATHENA_SQL_ACTIONS.items()
                if not authoring or not authoring[0].authoring_available(command)
            )
            if not athena.vm.query.execute_command.can_execute():
                disabled.add("athena.execute")
            if not athena.vm.query.cancel_command.can_execute():
                disabled.add("athena.cancel")
            if not athena.can_load_more(focused_ids=focused_ids):
                disabled.add("athena.load_more")
            copied = self._app_ctx.table_clipboard_vm.copied_table
            if copied is None or (copied.table_ref.connection_name, copied.table_ref.region) != (
                athena.vm.context.connection_name,
                athena.vm.context.region,
            ):
                disabled.add("athena.insert_table_ref")
            return frozenset(disabled)
        glue = self._glue_page()
        if glue is not None:
            disabled = set()
            for action, ready in (
                ("glue.compare_tables", glue.vm.actions_available),
                ("glue.copy_table_ref", glue.vm.can_copy_table_reference),
                ("glue.query_in_athena", glue.vm.can_query_in_athena),
                ("glue.time_travel_in_athena", glue.vm.can_time_travel_in_athena),
                (
                    "glue.iceberg_info",
                    any(view.info_available for view in glue.query(GlueIcebergView)),
                ),
                ("glue.load_more", glue.can_load_more(focused_ids=focused_ids)),
            ):
                if not ready:
                    disabled.add(action)
            return frozenset(disabled)
        dual = self._dual_pane()
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            return frozenset()
        entries = pane.filtered_entries or pane.entries
        target = entries[pane.cursor_index] if 0 <= pane.cursor_index < len(entries) else None
        return (
            frozenset({"pane.copy", "pane.delete"})
            if getattr(target, "name", None) == ".."
            or getattr(getattr(target, "entry", None), "name", None) == ".."
            else frozenset()
        )

    def _discovery_can_descend(self, origin: _DiscoveryOrigin) -> bool:
        """Mirror non-modal activation targets, excluding text-editing effects."""
        focused = origin.focus
        if isinstance(focused, (Input, TextArea)):
            return False
        if isinstance(focused, NavMenu):
            return True
        for page in (self._glue_page(), self._athena_page(), self._emr_page()):
            if page is not None:
                return page.can_activate_focused(focused)
        if isinstance(focused, (ServiceSourceHeader, ContextPicker, ServiceTabStrip)):
            return True
        if isinstance(focused, (Button, ModalButton)):
            return not focused.disabled
        if isinstance(focused, OptionList):
            return focused.highlighted is not None
        if isinstance(focused, DataTable):
            return focused.row_count > 0
        if focused is not None and callable(getattr(focused, "action_toggle_collapsible", None)):
            return True
        dual = self._dual_pane()
        if dual is not None:
            pane = (
                dual.left
                if origin.slot is FocusSlot.S3_LEFT
                else dual.right
                if origin.slot is FocusSlot.S3_RIGHT
                else None
            )
            if focused is not None:
                owner = next(
                    (node for node in focused.ancestors_with_self if isinstance(node, Pane)), None
                )
                pane = owner.vm if owner is not None else None
            entry = pane.selected_entry if pane is not None else None
            return (
                pane is not None
                and pane.state is PaneState.IDLE
                and entry is not None
                and entry.kind is EntryKind.DIRECTORY
                and (not entry.is_parent_link or not pane.path.is_root)
            )
        return False

    def _discovery_service_supported(self, service_id: str) -> bool:
        ctx = self._app_ctx
        connection = ctx.root_vm.active_connection
        return (
            not self.service_navigation.closed
            and self._shutdown_task is None
            and connection is not None
            and service_id in ctx.registry
            and ctx.registry.get(service_id).supports(connection)
        )

    def _select_discovery_service(self, service_id: str) -> None:
        if not self._discovery_service_supported(service_id):
            return
        if service_id == "athena":
            glue = self._glue_page()
            if glue is not None and glue.query_in_athena():
                return
        self._app_ctx.root_vm.services_menu.switch_service_command.execute(service_id)

    def _discovery_unavailability(self, origin: _DiscoveryOrigin) -> dict[str, str]:
        reasons = {
            action: "selection_required" for action in self._readiness_disabled(origin.focused_ids)
        }
        if not self._app_ctx.transfers_vm.active_count:
            reasons["app.focus_transfers"] = "selection_required"
        discovery_ids = {spec.id for spec in ACTION_SPECS} | self._discovery_source_targets.keys()
        if not self._discovery_origin_owned(origin):
            return dict.fromkeys(discovery_ids, "page_unavailable")
        if origin.focus is not None and not origin.focus.is_attached:
            return dict.fromkeys(discovery_ids, "focus_required")
        dual = self._dual_pane()
        if dual is not None:
            pane = self._discovery_file_pane(origin)
            for spec in ACTION_SPECS:
                if spec.service_ids == frozenset({"s3"}) and (
                    pane is None or pane.state not in {PaneState.IDLE, PaneState.EMPTY}
                ):
                    reasons[spec.id] = "focus_required" if pane is None else "busy"
            if pane is not None:
                destination = dual.right if pane is dual.left else dual.left
                if destination.state not in {PaneState.IDLE, PaneState.EMPTY}:
                    reasons["pane.copy"] = "busy"
                selected = pane.selected_entry
                # Copy/delete use marks first; the footer's parent-row denial
                # applies only to their cursor fallback in discovery.
                for action in ("pane.copy", "pane.delete"):
                    if pane.marked_entries:
                        if reasons.get(action) == "selection_required":
                            reasons.pop(action)
                    elif selected is None or selected.is_parent_link:
                        reasons[action] = "selection_required"
                for action in (
                    "pane.copy_entry_path",
                    "pane.mark_up",
                    "pane.mark_down",
                ):
                    if selected is None or selected.is_parent_link:
                        reasons[action] = "selection_required"
                if selected is None or selected.kind is not EntryKind.FILE:
                    reasons["pane.quick_look"] = "selection_required"
                if pane.path.is_root or isinstance(origin.focus, (Input, TextArea)):
                    reasons["pane.ascend"] = "selection_required"
                    reasons["pane.modal_left"] = "selection_required"
                if selected is None or selected.kind is not EntryKind.DIRECTORY:
                    reasons["pane.descend"] = "selection_required"
                for action in _PANE_SELECTION_ACTIONS:
                    command = getattr(pane, action.split(".")[1] + "_command")
                    if not command.can_execute():
                        reasons[action] = "selection_required"
            else:
                reasons["pane.descend"] = "focus_required"
                reasons["pane.modal_left"] = "focus_required"
                reasons["app.swap_source"] = "focus_required"
            if origin.object_details is None:
                reasons["pane.object_details"] = "selection_required"
            if origin.object_versions is None:
                reasons["pane.object_versions"] = "selection_required"
            if not self._folder_upload_origin_current(origin.folder_upload):
                reasons["pane.upload_folder"] = "selection_required"
            if len(_build_swap_candidates(self._app_ctx)[0]) <= 1:
                reasons["app.swap_source"] = "source_missing"
        athena = self._athena_page()
        if athena is not None:
            authoring = tuple(athena.query(AthenaQueryView))
            for action, command in _ATHENA_SQL_ACTIONS.items():
                if not authoring or not authoring[0].authoring_available(command):
                    reasons[action] = (
                        "busy" if athena.vm.active_view == "query" else "focus_required"
                    )
            results = athena.vm.results
            controls = tuple(athena.query(AthenaResultsView))
            live_results = (
                bool(controls)
                and controls[0].is_running
                and controls[0].is_attached
                and controls[0]._controls_are_live()
                and athena.vm._is_alive()
            )
            for action in _ATHENA_RESULT_ACTIONS:
                if (
                    athena.vm.active_view != "results"
                    or not live_results
                    or isinstance(origin.focus, (Input, TextArea))
                    or origin.result_generation != results.projection_generation
                ):
                    reasons[action] = "focus_required"
                elif (
                    action
                    in {
                        "athena.inspect_cell",
                        "athena.copy_cell",
                        "athena.copy_row",
                        "athena.sort_results",
                    }
                    and results.selection is None
                ):
                    reasons[action] = "selection_required"
            if (
                athena.vm.active_view != "query"
                or not athena.vm._is_alive()
                or len(athena.vm._policy.table_refs(athena.vm.query.sql, athena.vm.context)) != 1
            ):
                reasons["athena.open_in_glue"] = "selection_required"
            detail = athena.vm.history.detail if athena.vm.active_view == "history" else None
            result_location = (
                athena.vm.active_view == "results"
                and athena.vm._is_alive()
                and results.execution_id is not None
            )
            history_location = (
                detail is not None
                and athena.vm._is_alive()
                and detail.summary.state is QueryState.SUCCEEDED
                and detail.summary.ref.execution_id == athena.vm.history.selected_execution_id
                and _execution_identity_belongs_to(detail, athena.vm.context)
                and parse_s3_uri(detail.output_location) is not None
            )
            if not (result_location or history_location):
                reasons["athena.open_result_location"] = "selection_required"
        glue = self._glue_page()
        if glue is not None:
            glue_detail = glue.vm.catalog.table_detail
            if (
                not glue.vm.actions_available
                or glue_detail is None
                or parse_s3_uri(glue_detail.storage.location) is None
            ):
                reasons["glue.open_s3_location"] = "selection_required"
        emr = self._emr_page()
        if emr is not None:
            emr_detail = emr.vm.job_run_detail.detail
            if emr_detail is None or not emr.vm.can_clone_source(
                emr_detail.application_id, emr_detail.job_run_id
            ):
                reasons["emr.clone"] = "selection_required"
            logs = emr.right_pane
            if logs is None or origin.focus is None or logs not in origin.focus.ancestors_with_self:
                for action in (
                    "emr.logs.filter",
                    "emr.logs.source",
                    "emr.logs.follow",
                    "pane.modal_left",
                    "pane.modal_right",
                ):
                    reasons[action] = "focus_required"
            else:
                if not emr.vm.job_run_logs.can_follow:
                    reasons["emr.logs.follow"] = "selection_required"
                if (
                    sum(
                        emr.vm.job_run_logs.source_selectable(row.source)
                        for row in emr.vm.job_run_logs.sources
                    )
                    < 2
                ):
                    reasons["emr.logs.source"] = "selection_required"
        if origin.service_id in {
            "athena",
            "glue",
            "emr-serverless",
        } and (
            origin.service_id not in self._app_ctx.registry
            or not _service_source_candidates(self._app_ctx, origin.service_id)
        ):
            reasons["app.swap_source"] = "source_missing"
        if self._discovery_can_descend(origin):
            reasons.pop("pane.descend", None)
        else:
            reasons["pane.descend"] = "focus_required"
        if self._app_ctx.root_vm.active_connection is None:
            reasons["auth.authenticate"] = "source_missing"
        for service_id in ("s3", "athena", "glue", "emr-serverless"):
            if not self._discovery_service_supported(service_id):
                reasons[f"service.open.{service_id}"] = "source_unsupported"
        if origin.service_id == "s3" and origin.slot not in {FocusSlot.S3_LEFT, FocusSlot.S3_RIGHT}:
            reasons.update(dict.fromkeys(self._discovery_source_targets, "focus_required"))
        return reasons

    def _discovery_source_candidates(self, service_id: str | None) -> tuple[Connection, ...]:
        ctx = self._app_ctx
        if (
            service_id not in {"s3", "athena", "glue", "emr-serverless"}
            or service_id not in ctx.registry
        ):
            return ()
        service = ctx.registry.get(service_id)
        return tuple(
            connection
            for connection in _live_connections(ctx)
            if service.supports(connection)
            and (
                (connection.kind, connection.name) not in ctx.unreachable_connections
                if service_id == "s3"
                else connection.kind == "aws"
            )
        )

    def _reconcile_discovery_sources(self, origin: _DiscoveryOrigin) -> tuple[ActionSpec, ...]:
        service_id = origin.service_id
        candidates = self._discovery_source_candidates(service_id)
        targets = tuple(
            dict.fromkeys(
                _DiscoverySourceTarget(
                    service_id, connection.kind, connection.name, connection.region
                )
                for connection in candidates
                if service_id is not None
            )
        )
        present = set(targets)
        for removed in self._discovery_source_ids.keys() - present:
            removed_id = self._discovery_source_ids.pop(removed)
            self._discovery_source_targets.pop(removed_id)
            self._actions.unregister(removed_id)
            self._app_ctx.command_palette_vm.unregister_entry(removed_id)
        specs = []
        for target in targets:
            entry_id = self._discovery_source_ids.get(target)
            if entry_id is None:
                self._discovery_source_counter += 1
                entry_id = f"source.choice.{self._discovery_source_counter}"
                self._discovery_source_ids[target] = entry_id
            self._discovery_source_targets[entry_id] = target
            self._actions.register(
                entry_id, partial(self._select_discovery_source, entry_id, origin)
            )
            name = target.connection_name
            if (
                sum(
                    (other.connection_name, other.region) == (name, target.region)
                    for other in targets
                )
                > 1
            ):
                name += f" ({target.connection_kind})"
            label = f"Use {name} · {target.region or '(default region)'} for {scope_label(frozenset({target.service_id}), target.service_id)}"
            specs.append(
                ActionSpec(
                    entry_id,
                    label,
                    "Source",
                    (
                        target.connection_name,
                        target.region,
                        target.connection_kind,
                        target.service_id,
                        "source",
                        "switch",
                        "connection",
                    ),
                    frozenset({target.service_id}),
                    "unbound",
                )
            )
        return tuple(specs)

    async def _select_discovery_source(self, entry_id: str, origin: _DiscoveryOrigin) -> None:
        target = self._discovery_source_targets.get(entry_id)
        if target is None or not self._discovery_origin_owned(origin):
            return
        generation = self._supersede_table_navigation()
        # PaletteVM still owns the coroutine; the existing navigation task set
        # also makes it cancellable by newer navigation and shutdown.
        task = asyncio.current_task()
        if task is not None:
            self.service_navigation.track_navigation(task)
        try:
            async with self.service_navigation.service_lock:
                if (
                    self.service_navigation.closed
                    or self._shutdown_task is not None
                    or not self._service_navigation_is_owned_by("external", generation)
                    or not self._discovery_origin_owned(origin)
                    or self._discovery_source_targets.get(entry_id) != target
                ):
                    return
                if not any(
                    (connection.kind, connection.name, connection.region)
                    == (target.connection_kind, target.connection_name, target.region)
                    for connection in self._discovery_source_candidates(target.service_id)
                ):
                    return
                if target.service_id == "s3":
                    dual = self._dual_pane()
                    pane = (
                        dual.left
                        if dual is not None and origin.slot is FocusSlot.S3_LEFT
                        else dual.right
                        if dual is not None and origin.slot is FocusSlot.S3_RIGHT
                        else None
                    )
                    if (
                        pane is None
                        or dual is None
                        or dual is not origin.vm
                        or dual.focused_pane is not pane
                    ):
                        return
                accepted = await self._switch_single_context_source_to(
                    target.service_id,
                    target.connection_name,
                    target.region,
                    connection_kind=target.connection_kind,
                )
                host = self._app_ctx.root_vm.content_host
                if (
                    not accepted
                    and not self.service_navigation.closed
                    and self._service_navigation_is_owned_by("external", generation)
                    and host.current_id == target.service_id
                ):
                    for header in self.query(ServiceSourceHeader):
                        if (
                            header.is_attached
                            and header.display
                            and header.can_focus
                            and any(
                                getattr(node, "vm", None) is host.current
                                for node in header.ancestors
                            )
                        ):
                            header.restore_source()
        finally:
            if task is not None:
                self.service_navigation.untrack_navigation(task)

    def _project_discovery_actions(
        self, origin: _DiscoveryOrigin
    ) -> tuple[ActionPresentation, ...]:
        return project_actions(
            (*ACTION_SPECS, *self._reconcile_discovery_sources(origin)),
            registered_ids=frozenset(self._actions.known_actions()),
            bindings=self._app_ctx.keymap_store.all(),
            active_service_id=origin.service_id,
            unavailable_reasons=self._discovery_unavailability(origin),
        )

    def _refresh_discovery_surfaces(self) -> None:
        if self._refreshing_discovery or self._shutdown_task is not None:
            return
        if len(self.screen_stack) == 1 and self._command_palette_populated:
            self._discovery_origin = self._capture_discovery_origin()
        origin = self._discovery_origin
        if origin is None:
            return
        self._refreshing_discovery = True
        try:
            rows = self._project_discovery_actions(origin)
            vm = self._app_ctx.command_palette_vm
            selected = vm.filtered_entries[vm.selected_index].id if vm.filtered_entries else None
            current_ids = {row.id for row in rows if row.available}
            for removed in self._discovery_palette_ids - current_ids:
                vm.unregister_entry(removed)
            for row in rows:
                if row.available:
                    vm.register_entry(row, partial(self._invoke_discovery_action, row.id, origin))
            self._discovery_palette_ids = current_ids
            vm.set_active_service(origin.service_id)
            if vm.is_open and selected is not None:
                target = next(
                    (i for i, row in enumerate(vm.filtered_entries) if row.id == selected), None
                )
                if target is not None:
                    vm.move_selection_command.execute(target - vm.selected_index)
            for screen in self.screen_stack:
                if isinstance(screen, HelpModal):
                    self.call_after_refresh(screen.update_actions, rows)
        finally:
            self._refreshing_discovery = False

    def _populate_command_palette(self) -> None:
        if len(self.screen_stack) == 1 or self._discovery_origin is None:
            self._discovery_origin = self._capture_discovery_origin()
        self._command_palette_populated = True
        self._refresh_discovery_surfaces()

    async def _invoke_discovery_action(self, action_id: str, origin: _DiscoveryOrigin) -> None:
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def after_dismissal() -> None:
            if not ready.done():
                ready.set_result(None)

        self.call_after_refresh(after_dismissal)
        await ready
        if (
            self._shutdown_task is not None
            or len(self.screen_stack) != 1
            or self._app_ctx.focus_coordinator.is_modal
            or not self._discovery_origin_owned(origin)
            or not self._actions.has(action_id)
        ):
            return
        row = next(
            (row for row in self._project_discovery_actions(origin) if row.id == action_id), None
        )
        if row is None or not row.available:
            return
        if action_id == "pane.upload_folder":
            if self._folder_upload_origin_current(origin.folder_upload) and (
                origin.focus is None or origin.focus.is_attached
            ):
                self._app_ctx.focus_coordinator.set_focused_slot(origin.slot)
                self.set_focus(origin.focus)
                self._open_folder_upload(origin.folder_upload)
            return
        if action_id == "pane.object_versions":
            if self._versions_origin_current(origin.object_versions) and (
                origin.focus is None or origin.focus.is_attached
            ):
                self._app_ctx.focus_coordinator.set_focused_slot(origin.slot)
                self.set_focus(origin.focus)
                self._open_versions(origin.object_versions)
            return
        if action_id == "pane.object_details":
            self._palette_object_details_origin = origin.object_details
            self._palette_object_details_focus = origin.focus
            self._palette_object_details_slot = origin.slot
            self._finish_palette_object_details()
            return
        if origin.focus is None or origin.focus.is_attached:
            self._app_ctx.focus_coordinator.set_focused_slot(origin.slot)
            self.set_focus(origin.focus)
        result = self._actions.invoke(action_id)
        if inspect.isawaitable(result):
            await result

    def _finish_palette_object_details(self) -> None:
        origin = self._palette_object_details_origin
        dual = self._dual_pane()
        if origin is not None and dual is not None:
            pane, entry, provider, source, revision, row = origin
            if (
                pane in (dual.left, dual.right)
                and pane.selected_entry is entry
                and pane.provider is provider
                and pane.current_connection_key == source
                and pane.listing_revision == revision
                and row.is_attached
            ):
                # Pop may temporarily autofocus the navigation rail before
                # the coordinator's deferred restoration. Restore the captured
                # slot only after validating its exact live row/source.
                self._app_ctx.focus_coordinator.set_focused_slot(self._palette_object_details_slot)
                self.set_focus(self._palette_object_details_focus)
        self._open_object_details(origin)

    def action_command_palette(self) -> None:
        """Open the fuzzy command palette (bound to ``:`` / ``Ctrl+K``)."""
        self.record_action("app.command_palette")
        for screen in self.screen_stack:
            if isinstance(screen, CommandPalette):
                with contextlib.suppress(Exception):
                    screen.query_one("#palette-input", Input).focus()
                return
        self._discovery_origin = self._capture_discovery_origin()
        self._populate_command_palette()
        vm = self._app_ctx.command_palette_vm
        vm.set_active_service(self._app_ctx.root_vm.content_host.current_id)
        vm.open_command.execute()
        self.push_screen(CommandPalette(vm, hub=self._app_ctx.hub))

    def _dual_pane(self) -> DualPaneVM | None:
        """Return the currently-hosted ``DualPaneVM`` (or None).

        Returns None when the content host is showing a non-file-manager
        view (e.g. SettingsView).
        """
        current = self._app_ctx.root_vm.content_host.current
        return current if isinstance(current, DualPaneVM) else None

    def _emr_page(self) -> EmrServerlessPage | None:
        """Return the currently-mounted :class:`EmrServerlessPage`
        widget, or None when EMR isn't the active service.

        Mirror of :meth:`_dual_pane` for the EMR page so the global
        priority bindings (``up``/``down``/``enter``/``r``) can route
        keystrokes to the focused EMR pane the same way they route to
        the S3 file pane via ``dual.focused_pane``.
        """
        with contextlib.suppress(Exception):
            return self.query_one("#content-emr-page", EmrServerlessPage)
        return None

    def _glue_page(self) -> GluePage | None:
        """Return the mounted Glue page, or None for another service."""
        with contextlib.suppress(Exception):
            return self.query_one("#content-glue-page", GluePage)
        return None

    def _athena_page(self) -> AthenaPage | None:
        """Return the mounted Athena page, or None for another service."""
        with contextlib.suppress(Exception):
            return self.query_one("#content-athena-page", AthenaPage)
        return None

    def _emr_active_pane(self, emr_page: EmrServerlessPage) -> object | None:
        """Return the focused EMR pane for refresh routing."""
        focused = self.focused
        if focused is not None:
            for pane in (emr_page.left_pane, emr_page.right_detail, emr_page.right_pane):
                if pane is not None and (focused is pane or pane in focused.ancestors_with_self):
                    return pane
        return emr_page.left_pane

    def action_switch_focus(self) -> None:
        """Move to the next app-wide focus slot."""
        self.record_action("pane.switch_focus")
        if len(self.screen_stack) > 1:
            self.screen.focus_next()
            return
        self._cycle_focus(reverse=False)

    def action_switch_focus_reverse(self) -> None:
        """Move to the previous app-wide focus slot."""
        self.record_action("pane.switch_focus_back")
        if len(self.screen_stack) > 1:
            self.screen.focus_previous()
            return
        self._cycle_focus(reverse=True)

    def _cycle_focus(self, *, reverse: bool) -> None:
        overlay = self._focused_transfer_overlay()
        if overlay is not None:
            overlay.cycle_controls(reverse=reverse)
            return
        view = self._first_run_view()
        if view is not None:
            if not view.cycle_focus(reverse=reverse):
                if reverse:
                    self.screen.focus_previous()
                else:
                    self.screen.focus_next()
            return
        with contextlib.suppress(Exception):
            form = self.query_one(ConnectionFormInline)
            if form.cycle_focus(reverse=reverse):
                return
        # EMR page owns its own six-slot Tab cycle.
        with contextlib.suppress(Exception):
            emr_page = self.query_one("#content-emr-page", EmrServerlessPage)
            if reverse:
                emr_page.action_cycle_panes_back()
            else:
                emr_page.action_cycle_panes_forward()
            return

        glue_page = self._glue_page()
        if glue_page is not None:
            glue_page.cycle_focus(reverse=reverse)
            return
        athena_page = self._athena_page()
        if athena_page is not None:
            athena_page.cycle_focus(reverse=reverse)
            return

        coordinator = self._app_ctx.focus_coordinator
        with contextlib.suppress(Exception):
            settings = self.query_one(SettingsView)
            if settings.cycle_focus(reverse=reverse):
                coordinator.set_focused_slot(FocusSlot.SETTINGS)
                return
            coordinator.cycle_settings_focus(reverse=reverse)
            if coordinator.focused_slot is FocusSlot.SETTINGS:
                settings.focus_boundary(reverse=reverse)
            else:
                self._project_focus_slot(coordinator.focused_slot)
            return
        coordinator.cycle_s3_focus(reverse=reverse)
        self._project_focus_slot(coordinator.focused_slot)

    def _project_focus_slot(self, slot: FocusSlot) -> None:
        if slot is FocusSlot.NAV_MENU:
            with contextlib.suppress(Exception):
                nav = self.query_one("#nav-menu", NavMenu)
                self._focus_active_nav_list(nav)
            return
        if slot is FocusSlot.S3_LEFT or slot is FocusSlot.S3_RIGHT:
            with contextlib.suppress(Exception):
                self.set_focus(None)
            dual = self._dual_pane()
            if dual is None:
                return
            target = FocusedPane.LEFT if slot is FocusSlot.S3_LEFT else FocusedPane.RIGHT
            dual.set_focused(target)
            return
        if slot is FocusSlot.SETTINGS:
            with contextlib.suppress(Exception):
                settings = self.query_one(SettingsView)
                settings.focus_default()
            return
        if slot.value.startswith("glue."):
            page = self._glue_page()
            if page is not None:
                page.project_focus_slot(slot)
            return
        if slot.value.startswith("athena."):
            athena_page = self._athena_page()
            if athena_page is not None:
                athena_page.project_focus_slot(slot)
            return
        if slot.value.startswith("emr."):
            with contextlib.suppress(Exception):
                emr_page = self.query_one("#content-emr-page", EmrServerlessPage)
                emr_page.project_focus_slot(slot)

    def _focus_demo_launch_nav(self) -> None:
        self._app_ctx.focus_coordinator.set_focused_slot(FocusSlot.NAV_MENU)
        with contextlib.suppress(Exception):
            nav = self.query_one("#nav-menu", NavMenu)
            self._focus_active_nav_list(nav)

    def focus_active_service_pane(self) -> None:
        """Move focus to the currently-active service's default pane.

        Called by :meth:`NavMenu.action_commit` when ENTER is pressed
        on a service row — the user's explicit intent to "enter" the
        service. Per-service mapping:

        - ``s3`` → :class:`DualPane.focus_left_pane` (Panes decline
          Textual focus by design; the visual indicator is driven by
          ``DualPaneVM.focused`` via CSS, so we drop App focus and
          ensure the VM is on LEFT).
        - ``emr-serverless`` → :class:`JobRunsPane` (the focusable
          LEFT pane of the EMR page).
        - ``settings`` → first :class:`CollapsibleTitle` of the
          Settings view.

        For service swaps (target ≠ current selected), the new page
        may not be mounted yet when this fires; each branch's
        ``query_one`` silently no-ops in that case, and the
        destination page's own ``on_mount`` auto-focus is the safety
        net (gated on "is NavMenu still focused?", which NavMenu
        clears before calling here).
        """
        current_id = self._app_ctx.root_vm.content_host.current_id
        if current_id == "s3":
            self._app_ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_LEFT)
            with contextlib.suppress(Exception):
                dual_widget = self.query_one("#content-dual-pane", DualPane)
                dual_widget.focus_left_pane()
            return
        if current_id == "emr-serverless":
            self._app_ctx.focus_coordinator.set_focused_slot(FocusSlot.EMR_RUNS)
            with contextlib.suppress(Exception):
                emr_page = self.query_one("#content-emr-page", EmrServerlessPage)
                left = emr_page.left_pane
                if left is not None:
                    left.focus()
            return
        if current_id == "glue":
            with contextlib.suppress(Exception):
                page = self.query_one("#content-glue-page", GluePage)
                page.focus_default()
            return
        if current_id == "athena":
            with contextlib.suppress(Exception):
                athena_page = self.query_one("#content-athena-page", AthenaPage)
                athena_page.focus_default()
            return
        if current_id == SETTINGS_NAV_ID:
            with contextlib.suppress(Exception):
                settings = self.query_one(SettingsView)
                settings.focus_default()
            return

    def _focus_active_nav_list(self, nav: NavMenu) -> None:
        """Hand Textual focus to the NavMenu.

        Post-PR-#94 rework: NavMenu is itself the focusable widget
        (no more internal OptionLists). Lands focus directly so
        arrow keys reach :meth:`NavMenu.action_cursor_up` /
        ``action_cursor_down``. Name kept (``_focus_active_nav_list``)
        for backward compatibility with the call sites the App and
        the EMR page already use.
        """
        with contextlib.suppress(Exception):
            nav.focus()

    def _consumed_by_modal(self, *action_names: str) -> bool:
        """Route a key to the active modal and report whether the modal
        layer consumed it.

        When a modal is active, try each ``action_name`` on the active
        screen and run the first that exists. This works around Textual
        dispatching App-level priority bindings BEFORE modal ones —
        without forwarding, things like ↑/↓/Enter in our modals would
        never reach the modal's own handlers.

        A modal consumes the keystroke **whether or not it implements a
        handler for it**. Returning ``False`` for "no handler found" let
        callers fall through to the pane/page behind the overlay, so
        Enter and the arrow keys drove hidden content: with the Help
        overlay open, Enter navigated the file pane underneath it and
        ↑/↓ moved a cursor the user could not see. Every screen this app
        pushes is a :class:`ModalScreen`, so an active screen stack is
        always an overlay that must swallow the key.
        """
        if len(self.screen_stack) <= 1:
            return False
        for name in action_names:
            forward = getattr(self.screen, name, None)
            if forward is not None:
                forward()
                break
        return True

    def action_move_up(self) -> None:
        self.record_action("pane.move_up")
        if isinstance(self.screen, CommandPalette):
            self.screen.action_move_up()
            return
        if len(self.screen_stack) > 1 and isinstance(self.focused, (Input, TextArea)):
            move = getattr(self.focused, "action_cursor_up", None)
            if callable(move):
                move()
            return
        if self._consumed_by_modal("action_move_up"):
            return
        self._move_cursor(-1)

    def action_move_down(self) -> None:
        self.record_action("pane.move_down")
        if isinstance(self.screen, CommandPalette):
            self.screen.action_move_down()
            return
        if len(self.screen_stack) > 1 and isinstance(self.focused, (Input, TextArea)):
            move = getattr(self.focused, "action_cursor_down", None)
            if callable(move):
                move()
            return
        if self._consumed_by_modal("action_move_down"):
            return
        self._move_cursor(1)

    def _move_cursor(self, delta: int) -> None:
        # If Textual focus is in the NavMenu's OptionList, Up/Down
        # should navigate THAT list, not the pane cursor. Textual's
        # ``priority=True`` on the App binding steals the keystroke
        # before the OptionList sees it; we manually forward the
        # cursor action so OptionList navigation still works while
        # nav is the active Tab-cycle slot.
        if self._nav_has_focus():
            # NavMenu owns its own cursor; ``priority=True`` on the
            # App-level Up/Down binding would otherwise steal the
            # keystroke before NavMenu's binding fires. Manual
            # forward keeps the master-detail switch happening on
            # every arrow press.
            try:
                nav = self.query_one("#nav-menu", NavMenu)
            except Exception:
                return
            if delta < 0:
                nav.action_cursor_up()
            else:
                nav.action_cursor_down()
            return
        glue_page = self._glue_page()
        if glue_page is not None:
            glue_page.move_focused(delta)
            return
        athena_page = self._athena_page()
        if athena_page is not None:
            athena_page.move_focused(delta)
            return
        emr_page = self._emr_page()
        if emr_page is not None:
            emr_page.move_focused(delta)
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            return
        cmd = getattr(pane, "move_cursor_command", None)
        if cmd is not None:
            cmd.execute(delta)

    def _nav_has_focus(self) -> bool:
        """True if Textual focus is currently on the NavMenu.

        Post-PR-#94 rework the NavMenu is itself the focusable
        widget — no internal OptionLists. The descendant check
        survives in case any future child widget grabs focus
        (none currently does), but the common case is
        ``self.focused is nav``.

        Used to gate priority bindings (Up/Down/Enter) so they
        don't steal keystrokes from the NavMenu's cursor
        navigation when the user has Tab-cycled focus to the
        rail.
        """
        try:
            nav = self.query_one("#nav-menu", NavMenu)
        except Exception:
            return False
        focused = self.focused
        if focused is None:
            return False
        return focused is nav or nav in focused.ancestors_with_self

    async def action_descend(self) -> None:
        self.record_action("pane.descend")
        overlay = self._focused_transfer_overlay()
        if overlay is not None:
            overlay.activate_focused()
            return
        focused = self.focused if len(self.screen_stack) == 1 else None
        if (
            len(self.screen_stack) == 1
            and isinstance(focused, ModalButton)
            and focused.has_class("pane-clear-filter")
        ):
            focused.press()
            return
        # Forward Enter to the active modal first. Most of our modals
        # treat Enter as confirm/apply (ConfirmModal.action_confirm,
        # ThemePickerModal.action_apply). Without this, App's
        # priority=True enter binding always wins and Enter never
        # reaches the modal's handler. ``commit_focused`` is the
        # confirm-modal handler that commits whichever button has
        # arrow-key focus — checked first so it wins over the plain
        # ``confirm`` fallback.
        if len(self.screen_stack) > 1:
            focused = self.focused
            if isinstance(focused, TextArea):
                if not focused.read_only:
                    focused.replace("\n", *focused.selection, maintain_selection_offset=False)
                return
            if isinstance(self.screen, CrashModal):
                self.screen.action_default()
                return
            if isinstance(focused, ModalButton):
                focused.press()
                return
            for action_name in (
                "action_commit_focused",
                "action_confirm",
                "action_apply",
                "action_execute",
                "action_submit",
                "action_default",
            ):
                action = getattr(self.screen, action_name, None)
                if action is None:
                    continue
                result = action()
                if isinstance(result, Awaitable):
                    await result
                break
            # A modal swallows Enter even when it implements none of the
            # handlers above. Falling through instead ran the ladder
            # below against the content *behind* the overlay: with the
            # Help modal open, Enter descended into the highlighted
            # directory, and on a service page it committed a row
            # activation the user never saw.
            return
        view = self._first_run_view()
        if view is not None and view.activate_focused():
            return
        # If Textual focus is in the NavMenu, forward Enter to its
        # own commit action (re-fires the switch on the
        # currently-highlighted row). Post-PR-#94 NavMenu is the
        # focusable widget directly, no OptionList layer.
        if self._nav_has_focus():
            try:
                nav = self.query_one("#nav-menu", NavMenu)
            except Exception:
                return
            nav.action_commit()
            return
        with contextlib.suppress(Exception):
            settings = self.query_one(SettingsView)
            if settings.activate_focused():
                return
        glue_page = self._glue_page()
        if glue_page is not None and glue_page.activate_focused(space=False):
            return
        athena_page = self._athena_page()
        if athena_page is not None and athena_page.activate_focused():
            return
        emr_page = self._emr_page()
        if emr_page is not None:
            emr_page.activate_focused()
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None or pane.state is PaneState.LOADING:
            return
        target = pane.selected_entry
        if target is None:
            return
        # ".." synthetic entry — ascend to parent.
        if target.entry.name == "..":
            if not pane.path.is_root:
                dual.schedule_listing(pane, path=pane.path.parent())
            return
        # Descend only into directories; files trigger Quick Look later.
        if str(target.entry.kind) == "directory":
            dual.schedule_listing(pane, path=pane.path.join(target.entry.name))

    async def action_ascend(self) -> None:
        self.record_action("pane.ascend")
        if isinstance(self.focused, (Input, TextArea)):
            self.focused.action_delete_left()
            return
        # Forward Backspace to the active modal as a cancel-by-key
        # gesture (esc still works too).
        if self._consumed_by_modal("action_cancel", "action_close", "action_dismiss"):
            return
        # EMR page: Backspace is currently a deliberate no-op (the
        # page is a 2-slot master-detail with no hierarchical
        # navigation), but we short-circuit here so the keystroke
        # isn't silently consumed by ``_dual_pane()``-style
        # fallthrough. Symmetric with ``action_descend``'s EMR
        # branch (the user reaches the EMR page through the rail,
        # not by navigating up out of it).
        if self._emr_page() is not None:
            return
        athena_page = self._athena_page()
        if athena_page is not None and athena_page.delete_focused():
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None or pane.path.is_root:
            return
        dual.schedule_listing(pane, path=pane.path.parent())

    async def action_modal_left_or_ascend(self) -> None:
        """In modals, move focus to the previous button; in panes, ascend to parent."""
        # Record the id this handler is REGISTERED under. Recording
        # "pane.ascend" attributed the keystroke to a different action in the
        # crash-dump ring buffer, which an operator then could not cross-
        # reference against the keymap.
        self.record_action("pane.modal_left")
        # In a modal: Left moves arrow-key focus to the previous footer
        # button (or whatever the modal exposes as ``action_focus_prev``).
        # Outside any modal: behaves like ``ascend`` so file-pane
        # navigation is unchanged.
        if self._consumed_by_modal("action_focus_prev"):
            return
        emr_page = self._emr_page()
        if emr_page is not None and emr_page.select_adjacent_log_file(-1):
            return
        await self.action_ascend()

    def action_modal_right(self) -> None:
        """In modals, move focus to the next button. No-op in panes."""
        # "modal.focus_next" exists in neither the ActionRegistry nor the
        # keymap; use the registered id.
        self.record_action("pane.modal_right")
        # In a modal: Right moves arrow-key focus to the next footer
        # button. Outside any modal: no-op (panes don't currently bind
        # Right to anything).
        if self._consumed_by_modal("action_focus_next"):
            return
        emr_page = self._emr_page()
        if emr_page is not None:
            emr_page.select_adjacent_log_file(1)

    async def action_refresh(self) -> None:
        self.record_action("pane.refresh")
        glue_page = self._glue_page()
        if glue_page is not None:
            await glue_page.action_refresh_active()
            return
        athena_page = self._athena_page()
        if athena_page is not None:
            await athena_page.action_refresh_active()
            return
        # EMR page: ``r`` forwards to whichever pane currently holds
        # Textual focus. Runs/detail panes post ``RefreshRequested``;
        # logs calls ``action_reload`` to refresh/reload logs.
        emr_page = self._emr_page()
        if emr_page is not None:
            emr_pane = self._emr_active_pane(emr_page)
            if emr_pane is not None:
                # Try refresh request first (LEFT pane).
                refresh = getattr(emr_pane, "action_request_refresh", None)
                if refresh is not None:
                    refresh()
                    return
                # Fallback to reload action for logs pane.
                reload = getattr(emr_pane, "action_reload", None)
                if reload is not None:
                    reload()
            return
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is not None:
            dual.schedule_listing(pane)

    def _handle_authenticate(self) -> None:
        if self._shutdown_task is None and not self.service_navigation.closed:
            self._run_lifecycle_worker(
                self.action_authenticate, group="credential-recovery", exclusive=False
            )

    async def action_authenticate(self) -> None:
        """Retry the active source after credentials are repaired externally."""
        if self._shutdown_task is not None or self.service_navigation.closed:
            return
        self.record_action("auth.authenticate")
        task = self._auth_recovery_task
        if task is None or task.done():
            task = asyncio.create_task(
                self._recover_active_source(),
                name="credential-recovery",
            )
            self._auth_recovery_task = task
        try:
            await asyncio.shield(task)
        finally:
            if task.done() and self._auth_recovery_task is task:
                self._auth_recovery_task = None

    def _on_connection_changed_for_recovery(self, message: object) -> None:
        if isinstance(message, ConnectionChangedMessage):
            self._source_revision += 1

    def _credential_recovery_is_current(
        self,
        *,
        connection: Connection,
        revision: int,
        service_id: str,
        hosted: object,
    ) -> bool:
        root = self._app_ctx.root_vm
        return (
            self._source_revision == revision
            and root.active_connection == connection
            and root.content_host.current_id == service_id
            and root.content_host.current is hosted
        )

    @staticmethod
    def _credential_error_for_state(state: PaneState) -> BaseException:
        if state is PaneState.AUTH_REQUIRED:
            return AuthRequiredError("credential recovery read requires authentication")
        if state is PaneState.FORBIDDEN:
            return PermissionDeniedError("credential recovery read was denied")
        if state is PaneState.UNREACHABLE:
            return ProviderUnreachableError("credential recovery endpoint is unreachable")
        return RuntimeError("credential recovery read failed")

    def _show_credential_guidance(self, guidance: RecoveryGuidance) -> None:
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Auth",
            message=guidance.message,
            toast_id=f"credential-recovery-{guidance.kind.value.replace('_', '-')}",
        )

    def _show_generic_credential_guidance(self, connection: Connection) -> None:
        self._show_credential_guidance(
            classify_recovery_failure(connection, error=RuntimeError("recovery failed"))
        )

    async def _recover_active_source(self) -> None:
        ctx = self._app_ctx
        root = ctx.root_vm
        connection = root.active_connection
        service_id = root.content_host.current_id
        hosted = root.content_host.current
        revision = self._source_revision
        navigation_owner = self.service_navigation.owner
        if connection is None or service_id not in _SOURCE_SERVICE_IDS or hosted is None:
            notifications.advise(
                root.chrome.toast_stack,
                subject="Auth",
                message="No active remote source is available to retry.",
                toast_id="credential-recovery-no-source",
            )
            return

        try:
            resolved = ctx.connection_resolver.resolve(connection.name)
        except Exception as exc:
            ctx.log_sink.warning(
                "credential_recovery.resolve_failed",
                error_type=type(exc).__name__,
            )
            self._show_generic_credential_guidance(connection)
            return
        if connection_binding_identity(resolved) != connection_binding_identity(connection):
            ctx.log_sink.warning(
                "credential_recovery.identity_changed",
                kind=connection.kind,
                name=connection.name,
                region=connection.region,
            )
            self._show_generic_credential_guidance(connection)
            return

        try:
            token_state = ctx.aws_session.probe_token(resolved).state
        except Exception as exc:
            ctx.log_sink.warning(
                "credential_recovery.probe_failed",
                error_type=type(exc).__name__,
            )
            self._show_generic_credential_guidance(connection)
            return
        if token_state is not TokenState.CONNECTED:
            self._show_credential_guidance(
                classify_recovery_failure(resolved, token_state=token_state)
            )
            return

        try:
            recovery = await self._refresh_source_for_credential_recovery(
                connection=connection,
                resolved=resolved,
                revision=revision,
                service_id=service_id,
                hosted=hosted,
            )
        except asyncio.CancelledError:
            raise
        except (AuthRequiredError, PermissionDeniedError, ProviderUnreachableError) as exc:
            self._show_credential_guidance(
                classify_recovery_failure(resolved, token_state=token_state, error=exc)
            )
            return
        except Exception as exc:
            ctx.log_sink.error(
                "credential_recovery.read_failed",
                service=service_id,
                error_type=type(exc).__name__,
            )
            self._show_generic_credential_guidance(resolved)
            return

        if recovery is None:
            return
        state = recovery.state if isinstance(recovery, _StagedServiceRecovery) else recovery
        if isinstance(recovery, _StagedServiceRecovery):
            for diagnostic in recovery.diagnostics:
                ctx.log_sink.error(
                    "credential_recovery.candidate_failure",
                    service=diagnostic.service,
                    operation=diagnostic.operation,
                    error_type=diagnostic.error_type,
                )
        if state not in {PaneState.IDLE, PaneState.EMPTY}:
            if isinstance(recovery, _StagedServiceRecovery):
                await self._discard_staged_service_recovery(recovery.vm)
            self._show_credential_guidance(
                classify_recovery_failure(
                    resolved,
                    token_state=token_state,
                    error=self._credential_error_for_state(state),
                )
            )
            return
        if not self._credential_recovery_is_current(
            connection=connection,
            revision=revision,
            service_id=service_id,
            hosted=hosted,
        ):
            if isinstance(recovery, _StagedServiceRecovery):
                await self._discard_staged_service_recovery(recovery.vm)
            return

        if isinstance(recovery, _StagedServiceRecovery):
            try:

                def is_current() -> bool:
                    return (
                        not self.service_navigation.closed
                        and self.service_navigation.owner == navigation_owner
                        and recovery.live_state_is_current()
                        and self._credential_recovery_is_current(
                            connection=connection,
                            revision=revision,
                            service_id=service_id,
                            hosted=hosted,
                        )
                    )

                async with self.service_navigation.service_lock:
                    if not is_current():
                        await self._discard_staged_service_recovery(recovery.vm)
                        return
                    if not await self._commit_staged_service_recovery(
                        recovery,
                        connection=resolved,
                        service_id=service_id,
                        is_current=is_current,
                    ):
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ctx.log_sink.error(
                    "credential_recovery.commit_failed",
                    service=service_id,
                    error_type=type(exc).__name__,
                )
                self._show_generic_credential_guidance(resolved)
                return
        else:
            root.refresh_connection_state(resolved, TokenState.CONNECTED)
        self._clear_connection_unreachable(resolved.kind, resolved.name)
        self.service_navigation.clear_local_fallback()
        notifications.success(
            root.chrome.toast_stack,
            subject="Auth",
            message=f"credentials refreshed for {resolved.name}",
            toast_id="credential-recovery-success",
        )

    async def _refresh_source_for_credential_recovery(
        self,
        *,
        connection: Connection,
        resolved: Connection,
        revision: int,
        service_id: str,
        hosted: object,
    ) -> PaneState | _StagedServiceRecovery | None:
        if service_id == "s3":
            if not isinstance(hosted, DualPaneVM):
                return None
            return await self._recover_s3_credentials(
                connection=connection,
                resolved=resolved,
                revision=revision,
                hosted=hosted,
            )
        if service_id in {"athena", "glue", "emr-serverless"}:
            return await self._stage_service_credential_recovery(
                resolved=resolved,
                service_id=service_id,
                hosted=hosted,
            )
        return None

    async def _stage_service_credential_recovery(
        self,
        *,
        resolved: Connection,
        service_id: str,
        hosted: object,
    ) -> _StagedServiceRecovery | None:
        """Verify credentials in a fresh service tree without touching the live one."""
        focus: Literal["applications", "runs", "detail", "logs"] = "applications"
        if service_id == "emr-serverless":
            if not isinstance(hosted, EmrServerlessPageVM):
                return None
            page = self._emr_page()
            if page is not None:
                active = self._emr_active_pane(page)
                if active is page.left_pane:
                    focus = "runs"
                elif active is page.right_detail:
                    focus = "detail"
                elif active is page.right_pane:
                    focus = "logs"
        elif (service_id == "athena" and not isinstance(hosted, AthenaPageVM)) or (
            service_id == "glue" and not isinstance(hosted, GluePageVM)
        ):
            return None

        try:
            retained_state = self._service_recovery_retained_state(hosted)
        except ValueError:
            notifications.advise(
                self._app_ctx.root_vm.chrome.toast_stack,
                subject="Auth",
                message="Wait for the active operation to finish before retrying credentials.",
                toast_id="credential-recovery-busy",
            )
            return None

        def live_state_is_current() -> bool:
            try:
                return self._service_recovery_retained_state(hosted) == retained_state
            except ValueError:
                return False

        athena_snapshot: AthenaPageSnapshot | None = None
        athena_fallback_editor: AthenaPageEditorSnapshot | None = None
        if isinstance(hosted, AthenaPageVM):
            try:
                athena_snapshot = hosted.export_snapshot()
            except ValueError:
                # A page whose first authenticated read failed has no complete
                # context snapshot yet.  Its draft SQL is still durable state,
                # while the active view is already carried by the isolated
                # selection-store clone used to build the candidate.
                athena_fallback_editor = hosted.export_editor_snapshot()
        athena_result_execution_id = (
            hosted.results.execution_id
            if isinstance(hosted, AthenaPageVM) and hosted.active_view == "results"
            else None
        )
        glue_snapshot = (
            self._capture_glue_page_snapshot(hosted) if isinstance(hosted, GluePageVM) else None
        )
        emr_snapshot = (
            hosted.export_credential_recovery_snapshot()
            if isinstance(hosted, EmrServerlessPageVM)
            else None
        )
        recovery_vm = self._app_ctx.root_vm.build_recovery_service_vm(service_id, resolved)
        candidate = recovery_vm.vm
        try:
            candidate.construct()  # type: ignore[attr-defined]
            with capture_service_diagnostics() as diagnostics:
                if isinstance(candidate, AthenaPageVM):
                    await candidate.setup()
                    state = candidate.credential_recovery_state()
                    if state in {PaneState.IDLE, PaneState.EMPTY}:
                        if athena_snapshot is not None:
                            await candidate.restore_snapshot(athena_snapshot)
                            if athena_result_execution_id is not None:
                                await candidate.results.load(
                                    athena_result_execution_id,
                                    from_history=True,
                                )
                                state = candidate.results.state
                            else:
                                state = candidate.credential_recovery_state()
                        else:
                            if athena_fallback_editor is not None:
                                if athena_fallback_editor.context_selection_required:
                                    await candidate.restore_source_editor(athena_fallback_editor)
                                else:
                                    candidate.install_editor_snapshot(athena_fallback_editor)
                            state = candidate.credential_recovery_state()
                elif isinstance(candidate, GluePageVM) and glue_snapshot is not None:
                    await candidate.setup()
                    state = await candidate.refresh_for_credential_recovery()
                    if state in {PaneState.IDLE, PaneState.EMPTY}:
                        await self._restore_glue_page_snapshot(candidate, glue_snapshot)
                        state = candidate.credential_recovery_state()
                elif isinstance(candidate, EmrServerlessPageVM) and emr_snapshot is not None:
                    state = await candidate.restore_and_refresh_for_credential_recovery(
                        emr_snapshot,
                        focus,
                    )
                else:
                    raise TypeError(f"unsupported recovery VM for {service_id}")
        except BaseException:
            await self._discard_staged_service_recovery(candidate)
            raise
        return _StagedServiceRecovery(
            state=state,
            vm=candidate,
            commit_selection=recovery_vm.commit_selection,
            diagnostics=tuple(diagnostics),
            live_state_is_current=live_state_is_current,
        )

    @staticmethod
    def _service_recovery_retained_state(hosted: object) -> object:
        """Fence user state and active writes while an off-screen read awaits."""
        if isinstance(hosted, AthenaPageVM):
            query = hosted.query
            if (
                query.is_executing
                or query.is_submitting
                or query.owns_active_query
                or query.is_context_resolving
                or query.execute_command.is_executing
            ):
                raise ValueError("Athena recovery is unavailable during query work")
            try:
                return hosted.export_snapshot()
            except ValueError:
                # Initial reads can leave incomplete context. Retain the
                # still-editable SQL and selection instead of treating busy
                # execution as an incomplete page.
                return (
                    hosted.context,
                    hosted.export_editor_snapshot(),
                    hosted.active_view,
                    hosted.history.selected_execution_id,
                    hosted.saved.selected_kind,
                    hosted.saved.selected_query_id,
                    hosted.results.execution_id,
                    query.snapshot_generation,
                )
        if isinstance(hosted, GluePageVM):
            preview = hosted.catalog.iceberg.preview
            return (
                AwsTuiApp._capture_glue_page_snapshot(hosted),
                preview.limit,
                preview.snapshot_id,
            )
        if isinstance(hosted, EmrServerlessPageVM):
            if hosted.cancel_busy:
                raise ValueError("EMR recovery is unavailable during cancellation")
            return (hosted.export_credential_recovery_snapshot(), hosted.job_run_logs.following)
        raise TypeError("unsupported credential recovery page")

    async def _discard_staged_service_recovery(self, candidate: object) -> None:
        """Drain and dispose an off-screen candidate that was not adopted."""
        shutdown = getattr(candidate, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                result = shutdown()
                if isinstance(result, Awaitable):
                    await result
        with contextlib.suppress(Exception):
            candidate.dispose()  # type: ignore[attr-defined]

    async def _commit_staged_service_recovery(
        self,
        recovery: _StagedServiceRecovery,
        *,
        connection: Connection,
        service_id: str,
        is_current: Callable[[], bool] | None = None,
    ) -> bool:
        """Adopt a verified candidate and mount its matching view."""
        ctx = self._app_ctx
        replacement: Widget | None = None
        staging_host: Container | None = None
        host: Container | None = None
        root = ctx.root_vm
        outgoing_vm = root.content_host.current
        outgoing_id = root.content_host.current_id
        original_connection = root.active_connection
        original_revision = self._source_revision
        navigation_owner = self.service_navigation.owner
        input_claimed = False

        def owns_adoption() -> bool:
            if not input_claimed:
                return is_current is None or is_current()
            # Shutdown invalidates the outgoing snapshot. Once input is
            # claimed, compare transaction ownership rather than exporting
            # state from a retired page.
            current = root.content_host.current
            return (
                not self.service_navigation.closed
                and self._shutdown_task is None
                and self.service_navigation.owner == navigation_owner
                and self._source_revision == original_revision
                and root.active_connection == original_connection
                and (
                    current is None
                    or (current is outgoing_vm and root.content_host.current_id == outgoing_id)
                )
            )

        def claim_outgoing_input() -> bool:
            """Close live Athena intake only at the host's shutdown boundary."""
            nonlocal input_claimed
            if not owns_adoption():
                return False
            outgoing = ctx.root_vm.content_host.current
            if not isinstance(outgoing, AthenaPageVM):
                input_claimed = True
                return True
            page = next((page for page in self.query(AthenaPage) if page.vm is outgoing), None)
            if page is None or not page.is_attached:
                return False
            editor = page.query_one("#athena-editor", TextArea)
            # A Changed message may still be queued. Keep the live tree if
            # its visible editor has not settled with the captured VM state.
            if editor.text != outgoing.query.sql:
                return False
            editor.read_only = True
            page.disabled = True
            outgoing.close_input_admission()
            input_claimed = True
            return True

        try:
            replacement = build_service_view(
                service_id,
                recovery.vm,
                hub=ctx.hub,
                keymap=ctx.keymap_store,
                source_candidates=_service_source_contexts(ctx, service_id),
                focus_coordinator=ctx.focus_coordinator,
            )
            host = self.query_one("#content-host", Container)
            # Textual requires sibling ids to be unique.  The live and staged
            # service roots intentionally have the same stable id, so mount
            # the candidate one level deeper while it proves that its widget
            # tree can initialize.  The wrapper remains the content-host child
            # after commit and is removed by the normal next service swap.
            staging_host = Container(replacement)
            staging_host.styles.width = "100%"
            staging_host.styles.height = "100%"
            staging_host.display = False
            errors: list[Exception] = []
            self._recovery_mount_errors[staging_host] = errors
            await host.mount(staging_host)
            if errors:
                raise errors[0]
            if is_current is not None and not is_current():
                await staging_host.remove()
                await self._discard_staged_service_recovery(recovery.vm)
                return False
        except BaseException:
            if staging_host is not None and staging_host.parent is not None:
                with contextlib.suppress(Exception):
                    await staging_host.remove()
            await self._discard_staged_service_recovery(recovery.vm)
            raise
        finally:
            if staging_host is not None:
                self._recovery_mount_errors.pop(staging_host, None)

        prior_children = tuple(child for child in host.children if child is not staging_host)
        try:
            adopted = await self.service_navigation.adopt_prepared_service(
                ctx.root_vm,
                connection,
                TokenState.CONNECTED,
                service_id,
                recovery.vm,
                ownership_is_current=owns_adoption,
                claim_input=claim_outgoing_input,
            )
            if not adopted:
                try:
                    await staging_host.remove()
                finally:
                    await self._discard_staged_service_recovery(recovery.vm)
                return False
        except BaseException:
            with contextlib.suppress(Exception):
                await staging_host.remove()
            raise

        recovery.commit_selection()
        for child in prior_children:
            child.display = False
        staging_host.display = True
        for child in prior_children:
            try:
                await child.remove()
            except Exception as exc:
                ctx.log_sink.warning(
                    "credential_recovery.stale_widget_cleanup_failed",
                    service=service_id,
                    error_type=type(exc).__name__,
                )
        release_detached_query_results(host.ancestors_with_self)
        if service_id in {"glue", "athena", "emr-serverless"}:
            self._recompute_hint_disables()
        return True

    async def _recover_s3_credentials(
        self,
        *,
        connection: Connection,
        resolved: Connection,
        revision: int,
        hosted: DualPaneVM,
    ) -> PaneState | None:
        from aws_tui.domain.filesystem import PathRef
        from aws_tui.services.s3.service import _format_pane_title

        targets: list[tuple[PaneVM, PathRef]] = []
        if self.service_navigation.local_fallback:
            targets.append((hosted.left, PathRef(())))
        else:
            key = (connection.kind, connection.name)
            for pane in (hosted.left, hosted.right):
                if pane.current_connection_key == key:
                    targets.append((pane, pane.path))
        if not targets:
            return None

        staged: list[tuple[PaneVM, Any]] = []
        for pane, path in targets:
            provider = self._make_s3_provider_for_connection(resolved)
            stage = await pane.stage_provider_recovery(
                provider,
                path=path,
                identity_label=_format_pane_title(resolved),
                path_protocol="s3:",
                connection_key=(resolved.kind, resolved.name),
                transfer_connection=connection_history_identity(resolved, provider),
            )
            staged.append((pane, stage))

        if not self._credential_recovery_is_current(
            connection=connection,
            revision=revision,
            service_id="s3",
            hosted=hosted,
        ) or not all(pane.can_commit_provider_recovery(candidate) for pane, candidate in staged):
            return None
        for pane, candidate in staged:
            pane.commit_provider_recovery(candidate)
        return PaneState.IDLE

    async def _put_on_clipboard(self, value: str, label: str) -> None:
        """Copy ``value`` on both channels and report what actually happened.

        Two channels, only one of which can be checked. ``copy_to_clipboard``
        emits OSC 52, and OSC 52 has no acknowledgement: macOS Terminal.app
        ignores it outright and iTerm2 does unless the user opted in, and
        neither says so. Reporting "Copied" off that write is a guess dressed
        as a fact, so the report is driven by
        :class:`~aws_tui.vm.clipboard_vm.ClipboardVM` -- which owns the port
        that spawns the platform helper and returns whether the write landed
        -- and by nothing else.

        The split is deliberate. The OSC 52 leg stays here because it is
        Textual's own ``App.copy_to_clipboard`` and ``vm/`` may not import
        textual; the port, the thread offload and the four-way classification
        are the view model's, so this method never sees a
        :class:`~aws_tui.infra.clipboard.ClipboardResult` or compares a
        mechanism string. What is left is the mapping from an outcome to a
        toast and a log line, which is where every other multi-outcome report
        in this app already lives -- no view model in this repo raises a toast.
        """
        # The OSC 52 write stays guarded. Textual base64s
        # ``text.encode("utf-8")``, and a POSIX name that is not valid UTF-8
        # arrives here carrying lone surrogates (``domain/local_fs.py``
        # decodes with ``surrogateescape``), so this genuinely can raise --
        # and an escape would both skip the native write below and, inside a
        # worker, take the app down with it. Log the exception's class name
        # and never the payload: a copied path may be a credential.
        terminal_written = True
        try:
            self.copy_to_clipboard(value)
        except Exception as exc:
            terminal_written = False
            self._app_ctx.log_sink.warning(
                "clipboard.osc52_write_failed",
                error_type=type(exc).__name__,
            )
        # Two offloads stand between a wedged ``xclip`` and a frozen app, and
        # neither substitutes for the other: the VM's ``run_sync`` keeps the
        # blocking call off the event loop, and every caller reaches this
        # coroutine through :meth:`copy_value`'s worker, which keeps it off
        # the App's own message pump. Awaiting it inline from an action
        # handler would leave the loop spinning and the pump still blocked --
        # and the pump is where the next keypress, ``ctrl+q`` included, is
        # dequeued.
        write = await self._app_ctx.clipboard_vm.write_async(
            value, terminal_written=terminal_written
        )
        stack = self._app_ctx.root_vm.chrome.toast_stack
        slug = label.replace(" ", "-")
        if write.channel is ClipboardChannel.NATIVE:
            notifications.success(
                stack,
                subject="Source",
                message=f"copied {label}",
                toast_id=f"clipboard-{slug}",
            )
            return
        if write.channel is ClipboardChannel.HELPER_FAILED:
            # A helper was there and refused the payload. That is a fault
            # worth a durable record; the toast carries which helper failed
            # so the user can reproduce it from a shell.
            self._app_ctx.log_sink.warning(
                "clipboard.native_write_failed",
                mechanism=write.mechanism,
                error_type=write.error_type,
            )
            notifications.advise(
                stack,
                subject="Source",
                message=f"could not copy {label} to the system clipboard",
                action=(
                    f"{write.mechanism} failed; the terminal was sent OSC 52 instead"
                    if terminal_written
                    else f"{write.mechanism} failed; the terminal refused OSC 52"
                ),
                toast_id=f"clipboard-failed-{slug}",
            )
            return
        if write.channel is ClipboardChannel.TERMINAL_ONLY:
            # No helper is not a fault -- ssh, tmux, a headless CI shell --
            # so no log line. The toast names the one channel that was used
            # and refuses to call an unacknowledged write a success.
            notifications.advise(
                stack,
                subject="Source",
                message=f"sent {label} to the terminal via OSC 52",
                action="no native clipboard helper; your terminal may ignore it",
                toast_id=f"clipboard-osc52-{slug}",
            )
            return
        notifications.advise(
            stack,
            subject="Source",
            message=f"could not copy {label}",
            action="no native clipboard helper, and the terminal refused OSC 52",
            toast_id=f"clipboard-unavailable-{slug}",
        )

    def copy_value(self, value: str, label: str) -> None:
        """Put ``value`` on the clipboard without blocking the caller.

        The one seam every copy goes through: widgets that hold a value but
        have no route to the toast stack reach it by duck typing (``Pane``),
        and the keyboard actions call it rather than awaiting the writer, so
        no copy path can stall the App's message pump on a wedged helper.
        ``exclusive`` in the ``clipboard`` group means a second copy
        supersedes a first that is still waiting, which is what the user
        meant.
        """
        self._run_lifecycle_worker(
            partial(self._put_on_clipboard, value, label),
            group="clipboard",
        )

    async def action_copy_entry_path(self) -> None:
        """Copy the focused pane's cursor entry path.

        The pane's view model owns the formatting, so this reads the prepared
        payload rather than reassembling a path from chrome that the border may
        have truncated for display. It owns the *availability* too:
        ``copy_selected_path`` is ``None`` exactly when there is nothing a
        user could mean (an empty listing, or the cursor on the ``..`` parent
        link), and this is now the only place in the app that reads that
        answer -- ``Pane.copy_selected_path`` used to make the same test and
        return silently, so the keyboard and the widget gave the user two
        different answers to one question.

        The write itself goes through :meth:`copy_value` rather than being
        awaited here: this handler runs on the App's message pump, and
        awaiting the ~2 s port timeout on it would make the app deaf to every
        later keystroke, ``ctrl+q`` included.
        """
        self.record_action("pane.copy_entry_path")
        pane = self._ready_file_action_pane()
        if pane is None:
            return
        target = pane.viewmodel.copy_selected_path
        if target is None:
            # Through the toast stack, never ``self.notify``: Textual's own
            # notifier wrecks the footer (see ``_raise_theme_changed_toast``).
            notifications.advise(
                self._app_ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="nothing selected to copy",
                toast_id="clipboard-nothing-selected",
            )
            return
        self.copy_value(target, "file path")

    async def action_copy_path(self) -> None:
        """Copy the focused pane's current location.

        Through :meth:`copy_value`, for the same reason as
        :meth:`action_copy_entry_path`: the port call belongs in a worker, not
        on the pump that dequeues the next key.
        """
        self.record_action("pane.copy_path")
        pane = self._ready_file_action_pane()
        if pane is None:
            return
        self.copy_value(pane.viewmodel.copy_path, "path")

    async def action_help(self) -> None:
        """Show the help overlay with the active configurable keymap."""
        self.record_action("app.help")
        self._discovery_origin = self._capture_discovery_origin()
        await self.push_screen(
            HelpModal(
                actions=self._project_discovery_actions(self._discovery_origin),
                active_service_id=self._discovery_origin.service_id,
                keymap=self._app_ctx.keymap_store,
                log_path=self._app_ctx.log_sink.path,
                crash_path=self._app_ctx.log_sink.path.parent.parent / "crash",
            )
        )

    async def action_copy(self) -> None:
        """Copy the focused pane's marked entries (or the cursor row if
        none are marked) into the *other* pane. Pops a confirm modal
        showing source → destination paths first; only proceeds on
        explicit confirm.

        EMR-page hijack: when the EMR Serverless page is the active
        content (not S3), ``c`` reroutes to the page widget's own
        ``action_clone_selected_run`` — same priority-binding-hijack
        pattern PR #77 used for Tab and PR #78 used for Up/Down/Enter/r.
        Without this short-circuit the App-level priority binding
        swallows ``c`` before the page widget's binding can run.
        """
        self.record_action("pane.copy")
        emr_page = self._emr_page()
        if emr_page is not None:
            await emr_page.action_clone_selected_run()
            return
        dual = self._dual_pane()
        if dual is None:
            return
        src_pane = self._ready_file_action_pane()
        dst_pane = dual.other_pane
        if src_pane is None or dst_pane.state not in {PaneState.IDLE, PaneState.EMPTY}:
            return

        targets = list(src_pane.marked_entries)
        # Fall back to the cursor row if nothing is multi-selected.
        used_cursor_fallback = not targets
        if used_cursor_fallback:
            selected = src_pane.selected_entry
            if selected is not None and not selected.is_parent_link:
                targets = [selected]
        if not targets:
            return

        src_base = src_pane.viewmodel.border_title
        dst_base = dst_pane.viewmodel.border_title
        names_preview = (
            targets[0].entry.name
            if len(targets) == 1
            else f"{len(targets)} items ({targets[0].entry.name}, …)"
        )
        items_summary = "1 item" if len(targets) == 1 else f"{len(targets)} items"
        request = ConfirmRequest(
            title=f"Copy {items_summary}?",
            paths=(
                ConfirmPath(label="From", path=_join_path(src_base, names_preview)),
                ConfirmPath(label="To", path=_join_path(dst_base, names_preview)),
            ),
            confirm_label="Copy",
            cancel_label="Cancel",
        )

        ctx = self._app_ctx
        if ctx.confirm_vm.is_open or self._confirmation_pending:
            return
        self._confirmation_pending = True
        self.run_worker(
            self._confirm_copy(
                dual,
                src_pane,
                list(targets),
                used_cursor_fallback,
                request,
                self._file_confirmation_guard(
                    dual, src_pane, list(targets), used_cursor_fallback, dst_pane
                ),
            ),
            group="confirmation",
        )

    async def _confirm_copy(
        self,
        dual: object,
        src_pane: object,
        targets: list[object],
        used_cursor_fallback: bool,
        request: ConfirmRequest,
        is_current: Callable[[], bool],
    ) -> None:
        ctx = self._app_ctx
        try:
            if not is_current():
                self._file_confirmation_invalidated()
                return
            dialogs = TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub)
            if not await ctx.confirm_vm.ask(request, dialog_service=dialogs):
                return
            if not is_current():
                self._file_confirmation_invalidated()
                return
            self._run_lifecycle_worker(
                partial(self._run_copy, dual, src_pane, targets, used_cursor_fallback, is_current),
                group=_TRANSFER_COPY_GROUP,
            )
        finally:
            self._confirmation_pending = False

    async def _run_copy(
        self,
        dual: object,
        src_pane: object,
        targets: list[object],
        used_cursor_fallback: bool,
        is_current: Callable[[], bool],
    ) -> None:
        """Run ``DualPaneVM.copy_across`` from a worker. Errors are
        toasted, never re-raised."""
        ctx = self._app_ctx
        copy_across = getattr(dual, "copy_across", None)
        if copy_across is None:
            return
        if not is_current():
            self._file_confirmation_invalidated()
            return
        if used_cursor_fallback:
            _flash_cursor_fallback_marks(src_pane, targets, marked=True)
        try:
            if not is_current():
                self._file_confirmation_invalidated()
                return
            await copy_across()
        except Exception as exc:
            ctx.log_sink.error(
                "app.copy.failed",
                error=str(exc),
                error_type=type(exc).__name__,
                file_count=len(targets),
                **_mutation_log_context(dual),
            )
            # User feedback: "when a copy or delete fails, I see an
            # error box shown ON the command section at the bottom
            # which then disappears. I want all such errors be
            # handled gracefully and be shown as toast notifications
            # on top right with their proper icons and emojis". The
            # ``notifications.error`` helper routes through the
            # canonical ``ToastStack`` (top-right, ✖ glyph,
            # ``$danger`` colour, 30-s auto-dismiss); Textual's
            # bare ``self.notify`` paints over the Commands strip
            # and steals the eye there.
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                toast_id="copy-failed",
                subject="Transfer",
                message=f"copy failed: {exc}",
            )
        finally:
            self._refresh_transfer_history()
            if used_cursor_fallback:
                _flash_cursor_fallback_marks(src_pane, targets, marked=False)

    async def action_delete(self) -> None:
        """Delete the focused pane's marked entries (or the cursor row if
        none are marked). Pops a danger-styled confirm modal first."""
        self.record_action("pane.delete")
        dual = self._dual_pane()
        if dual is None:
            return
        src_pane = self._ready_file_action_pane()
        if src_pane is None:
            return

        targets = list(src_pane.marked_entries)
        used_cursor_fallback = not targets
        if used_cursor_fallback:
            selected = src_pane.selected_entry
            if selected is not None and not selected.is_parent_link:
                targets = [selected]
        if not targets:
            return

        ctx = self._app_ctx
        base = src_pane.viewmodel.border_title
        names_preview = (
            targets[0].entry.name
            if len(targets) == 1
            else f"{len(targets)} items ({targets[0].entry.name}, …)"
        )
        items_summary = "1 item" if len(targets) == 1 else f"{len(targets)} items"
        request = ConfirmRequest(
            title=f"Delete {items_summary}?",
            paths=(ConfirmPath(label="Target", path=_join_path(base, names_preview)),),
            body_lines=("This cannot be undone.",),
            confirm_label="Delete",
            cancel_label="Cancel",
            danger=True,
        )
        if ctx.confirm_vm.is_open or self._confirmation_pending:
            return
        self._confirmation_pending = True
        self.run_worker(
            self._confirm_delete(
                dual,
                src_pane,
                list(targets),
                used_cursor_fallback,
                request,
                self._file_confirmation_guard(dual, src_pane, list(targets), used_cursor_fallback),
            ),
            group="confirmation",
        )

    async def _confirm_delete(
        self,
        dual: object,
        src_pane: object,
        targets: list[object],
        used_cursor_fallback: bool,
        request: ConfirmRequest,
        is_current: Callable[[], bool],
    ) -> None:
        ctx = self._app_ctx
        try:
            if not is_current():
                self._file_confirmation_invalidated()
                return
            dialogs = TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub)
            if not await ctx.confirm_vm.ask(request, dialog_service=dialogs):
                return
            if not is_current():
                self._file_confirmation_invalidated()
                return
            self._run_lifecycle_worker(
                partial(
                    self._run_delete, dual, src_pane, targets, used_cursor_fallback, is_current
                ),
                group=_TRANSFER_DELETE_GROUP,
            )
        finally:
            self._confirmation_pending = False

    async def _run_delete(
        self,
        dual: object,
        src_pane: object,
        targets: list[object],
        used_cursor_fallback: bool,
        is_current: Callable[[], bool],
    ) -> None:
        """Mirror of :meth:`_run_copy` for the delete path."""
        ctx = self._app_ctx
        delete_in_focused = getattr(dual, "delete_in_focused", None)
        if delete_in_focused is None:
            return
        if not is_current():
            self._file_confirmation_invalidated()
            return
        if used_cursor_fallback:
            _flash_cursor_fallback_marks(src_pane, targets, marked=True)
        try:
            if not is_current():
                self._file_confirmation_invalidated()
                return
            await delete_in_focused()
        except Exception as exc:
            ctx.log_sink.error(
                "app.delete.failed",
                error=str(exc),
                error_type=type(exc).__name__,
                file_count=len(targets),
                **_mutation_log_context(dual),
            )
            # See action_copy for the rationale — same Commands-strip
            # paint-over problem when using bare ``self.notify``.
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                toast_id="delete-failed",
                subject="Transfer",
                message=f"delete failed: {exc}",
            )
        finally:
            self._refresh_transfer_history()
            if used_cursor_fallback:
                _flash_cursor_fallback_marks(src_pane, targets, marked=False)

    def action_cycle_theme(self) -> None:
        """Cycle to the next theme without opening the picker modal —
        bound to ``Shift+T`` so the footer chip is reachable too.

        Uses the canonical :class:`ThemePickerVM` (same VMx model the
        modal flow uses) to determine the next theme, then raises a
        top-right toast through :class:`ToastStackVM` so the
        notification overlay layer (not Textual's built-in bottom-
        center notify) handles placement + theme conformance.
        """
        self.record_action("app.cycle_theme")
        ctx = self._app_ctx

        def _pick_with_toast(name: str) -> bool:
            if not self.switch_theme(name):
                return False
            self._raise_theme_changed_toast(name)
            return True

        picker = ThemePickerVM(
            # Built-ins ONLY, deliberately: `test_cycle_theme_remains_limited_to_builtins`
            # pins Shift+T to a predictable quick-cycle over the shipped themes,
            # while `action_open_themes` offers the full `list_themes()` set
            # including user themes. The shared "canonical source of truth" the
            # docstrings mention is the `ThemePickerVM` MODEL, not the theme list.
            themes=ctx.theme_store.BUILTIN_NAMES,
            active_theme=ctx.initial_theme,
            on_pick=_pick_with_toast,
            on_preview=self.switch_theme,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
        )
        picker.construct()
        try:
            nxt = picker.next_theme()
            picker.pick_theme_command.execute(nxt)
        finally:
            self.call_after_refresh(picker.dispose)

    def action_mark_up(self) -> None:
        self.record_action("pane.mark_up")
        if len(self.screen_stack) > 1:
            return
        self._extend_selection(-1)

    def action_mark_down(self) -> None:
        self.record_action("pane.mark_down")
        if len(self.screen_stack) > 1:
            return
        self._extend_selection(1)

    def _extend_selection(self, delta: int) -> None:
        """Shift+arrow handler: TOGGLE the row we are leaving, then move.

        Rule (from the user — see PR comments): the only row whose
        mark changes is the row the cursor is *moving away from*. The
        target row is never touched, and we never modify both rows in
        the same press. This gives clean, predictable semantics:

        - Walking down through an unmarked range with Shift+Down marks
          each row as you leave it.
        - Walking back up through a marked range with Shift+Up unmarks
          each row as you leave it.
        - On a row whose mark you want flipped, point at it and press
          Shift+Arrow — the row toggles, cursor moves on.

        Cursor still moves even when the move would land out of range
        of the entries list (handled by ``move_cursor_command``'s own
        clamp), but the toggle only happens when ``cur`` is a real
        row (always true here since we got it from ``cursor_index``)."""
        pane = self._ready_file_action_pane()
        if pane is None or pane.selected_entry is None or pane.selected_entry.is_parent_link:
            return
        cur = pane.cursor_index
        entries = pane.filtered_entries
        if not (0 <= cur < len(entries)):
            return
        mark = getattr(pane, "mark_at", None)
        if mark is None:
            return
        mark(cur, marked=not entries[cur].is_marked)
        move = getattr(pane, "move_cursor_command", None)
        if move is not None:
            move.execute(delta)

    def _handle_swap_source(self) -> None:
        if self._shutdown_task is not None or self.service_navigation.closed:
            return
        origin = self._capture_discovery_origin()
        if not self._discovery_origin_owned(origin):
            return
        dual = self._dual_pane()
        pane = dual.focused_pane if dual is not None else None
        self._run_lifecycle_worker(
            partial(self.action_swap_source, origin=origin, pane=pane), group="content-mount"
        )

    async def action_swap_source(
        self, *, origin: _DiscoveryOrigin | None = None, pane: PaneVM | None = None
    ) -> None:
        """Cycle the current service's source.

        S3 retains independent source rings for its two panes. Other AWS
        services rebuild under the next supported AWS connection.
        """
        origin = origin or self._capture_discovery_origin()
        if not self._discovery_origin_owned(origin):
            return
        self.record_action("app.swap_source")
        generation = self._supersede_table_navigation()
        async with self.service_navigation.service_lock:
            if (
                self._shutdown_task is not None
                or self.service_navigation.closed
                or not self._service_navigation_is_owned_by("external", generation)
            ):
                return
            if origin is not None and not self._discovery_origin_owned(origin):
                return
            dual = self._dual_pane()
            if pane is not None and (dual is None or dual.focused_pane is not pane):
                return
            await self._swap_source_transaction()

    def on_service_source_header_source_selected(
        self,
        event: ServiceSourceHeader.SourceSelected,
    ) -> None:
        """Switch to the exact source committed by a Glue/Athena picker."""
        event.stop()
        if self._shutdown_task is not None or self.service_navigation.closed:
            return
        origin = self._capture_discovery_origin()
        if (
            not self._discovery_origin_owned(origin)
            or not event.header.is_attached
            or not any(getattr(node, "vm", None) is origin.vm for node in event.header.ancestors)
        ):
            return
        service_id = self._app_ctx.root_vm.services_menu.selected_id
        if service_id is None or service_id == SETTINGS_NAV_ID:
            return
        self.record_action("app.swap_source")
        generation = self._supersede_table_navigation()
        self._run_lifecycle_worker(
            partial(self._select_header_source, event, origin, service_id, generation),
            group="content-mount",
        )

    async def _select_header_source(
        self,
        event: ServiceSourceHeader.SourceSelected,
        origin: _DiscoveryOrigin,
        service_id: str,
        generation: int,
    ) -> None:
        async with self.service_navigation.service_lock:
            if (
                self._shutdown_task is not None
                or self.service_navigation.closed
                or not self._service_navigation_is_owned_by("external", generation)
                or not self._discovery_origin_owned(origin)
                or not event.header.is_attached
            ):
                return
            accepted = await self._switch_single_context_source_to(
                service_id,
                event.connection_name,
                event.region,
            )
            if not accepted and event.header.is_attached and self._discovery_origin_owned(origin):
                event.header.restore_source()

    async def _swap_source_transaction(self) -> None:
        ctx = self._app_ctx
        dual = self._dual_pane()
        if dual is None:
            service_id = ctx.root_vm.services_menu.selected_id
            if service_id is not None and service_id != SETTINGS_NAV_ID:
                await self._swap_single_context_source(service_id)
            return
        focused = getattr(dual, "focused_pane", None)
        if focused is None:
            return
        _LOCAL_LABEL = "local"
        candidates, skipped = _build_swap_candidates(ctx)
        # Bug 4 fix: deduplicate skip toasts — only raise when the skipped
        # set changes (avoid stacking N identical toasts on repeated Shift+S).
        skipped_fs = frozenset(skipped)
        if skipped_fs != self._last_skip_toast_set:
            _raise_skip_toast(ctx, skipped)
            self._last_skip_toast_set = skipped_fs if skipped else None
        if len(candidates) <= 1:
            # Only local — either no connections configured, or every
            # configured connection has been observed unreachable.
            if skipped:
                notifications.advise(
                    ctx.root_vm.chrome.toast_stack,
                    toast_id="swap-source-unreachable",
                    subject="Source",
                    message="all connections unreachable — staying on local",
                )
            else:
                notifications.advise(
                    ctx.root_vm.chrome.toast_stack,
                    toast_id="swap-source-empty",
                    subject="Source",
                    message="no connections configured — can't swap source",
                )
            return

        current_label = focused.identity_label or _LOCAL_LABEL
        try:
            idx = next(i for i, (label, _) in enumerate(candidates) if label == current_label)
        except StopIteration:
            idx = -1  # current label unknown → start of ring on next++
        next_label, payload = candidates[(idx + 1) % len(candidates)]

        new_provider: object
        new_protocol: str
        if payload == "local":
            new_provider = self._make_local_provider()
            new_protocol = ""
        else:
            assert not isinstance(payload, str)  # narrows payload to Connection
            conn = payload
            try:
                new_provider = self._make_s3_provider_for_connection(conn)
            except AuthRequiredError:
                ctx.log_sink.warning(
                    "pane.swap_source.auth_required",
                    connection=conn.name,
                )
                notifications.advise(
                    ctx.root_vm.chrome.toast_stack,
                    subject="Source",
                    message=f"authentication required for {conn.name} — keeping current source",
                    toast_id="swap-source-auth-required",
                )
                return
            new_protocol = "s3:"

        swap = getattr(focused, "swap_provider", None)
        if swap is None:
            return
        # Compute the connection_key passed to swap_provider so it is stored
        # atomically BEFORE _reload() fires — eliminating the attribution race
        # (Bug 1) where _on_hub_message_pane_state would read a stale key.
        new_conn_key: tuple[str, str] | None
        if payload == "local":
            new_conn_key = None
        else:
            assert not isinstance(payload, str)  # narrows payload to Connection
            new_conn_key = (payload.kind, payload.name)
        ctx.log_sink.info("pane.swap_source", to=next_label)
        await dual.cancel_listings(focused)
        await swap(
            new_provider,
            identity_label=next_label,
            path_protocol=new_protocol,
            connection_key=new_conn_key,
            transfer_connection=connection_history_identity(
                None if isinstance(payload, str) else payload, new_provider
            ),
        )

    async def action_next_emr_application(self) -> None:
        self.record_action("emr.next_application")
        page = self._emr_page()
        if page is not None:
            page.action_next_application()

    async def action_clone_emr_run(self) -> None:
        page = self._emr_page()
        if page is not None:
            self.record_action("emr.clone")
            await page.action_clone_selected_run()
            return
        if self._bindings_overlap("emr.clone", "pane.copy"):
            await self.action_copy()

    def action_cancel_emr_run(self) -> None:
        if len(self.screen_stack) > 1 or self._app_ctx.focus_coordinator.is_modal:
            return
        page = self._emr_page()
        if page is not None:
            self.record_action("emr.cancel")
            page.action_cancel_selected_run()

    def action_filter_emr_logs(self) -> None:
        page = self._emr_page()
        if page is not None and page.open_focused_log_filter():
            self.record_action("emr.logs.filter")

    def action_cycle_emr_log_source(self) -> None:
        page = self._emr_page()
        if page is not None and page.cycle_focused_log_source():
            self.record_action("emr.logs.source")

    def action_toggle_emr_log_follow(self) -> None:
        page = self._emr_page()
        if page is not None and page.toggle_focused_log_follow():
            self.record_action("emr.logs.follow")

    def _handle_service_read(
        self,
        read: Callable[[], Awaitable[None]],
        *,
        group: str,
        require_focus: bool = False,
    ) -> None:
        page = self._athena_page() or self._glue_page()
        if page is None or self._shutdown_task is not None or self.service_navigation.closed:
            return
        origin = self._capture_discovery_origin()

        async def read_owned() -> None:
            if (
                self._shutdown_task is not None
                or not self._discovery_origin_owned(origin)
                or (require_focus and self.focused is not origin.focus)
            ):
                return
            await read()

        page._run_lifecycle_worker(read_owned, group=group)

    async def action_select_glue_view(self, view: str) -> None:
        self.record_action(f"glue.{view}")
        page = self._glue_page()
        if page is not None:
            await page.action_select_view(view)
            return
        athena_view = {
            "catalog": "query",
            "jobs": "history",
            "crawlers": "results",
        }.get(view)
        athena_page = self._athena_page()
        if (
            athena_page is not None
            and athena_view is not None
            and self._bindings_overlap(f"glue.{view}", f"athena.{athena_view}")
        ):
            await athena_page.action_select_view(athena_view)

    async def action_select_athena_view(self, view: str) -> None:
        self.record_action(f"athena.{view}")
        page = self._athena_page()
        if page is not None:
            await page.action_select_view(view)
            return
        glue_view = {
            "query": "catalog",
            "history": "jobs",
            "results": "crawlers",
        }.get(view)
        glue_page = self._glue_page()
        if (
            glue_page is not None
            and glue_view is not None
            and self._bindings_overlap(f"athena.{view}", f"glue.{glue_view}")
        ):
            await glue_page.action_select_view(glue_view)

    async def action_choose_glue_run_state(self) -> None:
        self.record_action("glue.choose_run_state")
        page = self._glue_page()
        if page is not None:
            await page.action_choose_run_state()

    async def action_choose_glue_crawler_state(self) -> None:
        self.record_action("glue.choose_crawler_state")
        page = self._glue_page()
        if page is not None:
            await page.action_choose_crawler_state()

    def action_choose_athena_workgroup(self) -> None:
        self.record_action("athena.choose_workgroup")
        page = self._athena_page()
        if page is not None:
            page.action_choose_workgroup()

    def action_choose_athena_catalog(self) -> None:
        self.record_action("athena.choose_catalog")
        page = self._athena_page()
        if page is not None:
            page.action_choose_catalog()

    def action_choose_athena_database(self) -> None:
        self.record_action("athena.choose_database")
        page = self._athena_page()
        if page is not None:
            page.action_choose_database()

    async def action_execute_athena(self) -> None:
        self.record_action("athena.execute")
        page = self._athena_page()
        if page is not None:
            await page.action_execute()

    async def action_cancel_athena(self) -> None:
        self.record_action("athena.cancel")
        page = self._athena_page()
        if page is not None:
            await page.action_cancel()

    def _athena_sql_control(self, action_id: str, command: str) -> None:
        if len(self.screen_stack) > 1 or self._app_ctx.focus_coordinator.is_modal:
            return
        page = self._athena_page()
        if page is None:
            return
        controls = tuple(page.query(AthenaQueryView))
        if not controls or not controls[0].authoring_available(command):
            return
        self.record_action(action_id)
        controls[0].dispatch_authoring(command)

    async def action_load_more_athena(self) -> None:
        self.record_action("athena.load_more")
        page = self._athena_page()
        if page is not None:
            await page.action_load_more()

    def _athena_result_control(self, action: str) -> None:
        if (
            len(self.screen_stack) > 1
            or self._app_ctx.focus_coordinator.is_modal
            or isinstance(self.focused, (Input, TextArea))
        ):
            return
        page = self._athena_page()
        if page is None or page.vm.active_view != "results":
            return
        self.record_action(f"athena.{action}")
        getattr(page.query_one(AthenaResultsView), f"action_{action}")()

    async def action_open_athena_result_location(self) -> None:
        self.record_action("athena.open_result_location")
        page = self._athena_page()
        if page is None:
            return
        opened = False
        if page.vm.active_view == "history":
            opened = page.vm.history.open_s3_location()
        elif page.vm.active_view == "results":
            opened = await page.vm.results.open_s3_location()
        if opened:
            return
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="selected execution has no valid S3 result location",
            toast_id="athena-result-location-invalid",
        )

    def _bindings_overlap(self, first: str, second: str, *, key: str | None = None) -> bool:
        keymap = self._app_ctx.keymap_store
        shared = {textual_key_name(token) for token in keymap.resolve(first)} & {
            textual_key_name(token) for token in keymap.resolve(second)
        }
        return bool(shared) if key is None else textual_key_name(key) in shared

    async def action_open_glue_s3_location(self) -> None:
        self.record_action("glue.open_s3_location")
        page = self._glue_page()
        if page is None:
            return
        if not page.vm.actions_available:
            return
        if page.vm.open_s3_location():
            return
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="selected table has no valid S3 location",
            toast_id="glue-s3-location-invalid",
        )

    async def action_query_glue_table_in_athena(self) -> None:
        self.record_action("glue.query_in_athena")
        page = self._glue_page()
        if page is not None and not page.vm.actions_available:
            return
        if page is not None and page.query_in_athena():
            return
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="select a Glue table to query in Athena",
            toast_id="glue-athena-table-unavailable",
        )

    async def action_load_more_glue(self) -> None:
        self.record_action("glue.load_more")
        page = self._glue_page()
        if page is not None:
            await page.action_load_more()

    def action_glue_compare_tables(self) -> None:
        page = self._glue_page()
        if page is None or not page.vm.actions_available or len(self.screen_stack) > 1:
            return
        service = self._app_ctx.registry.get("glue")
        if not isinstance(service, GlueService):
            return
        self.record_action("glue.compare_tables")
        detail = page.vm.catalog.table_detail
        candidate = (
            detail.summary.ref if page.vm.can_copy_table_reference and detail is not None else None
        )
        vm = service.build_comparison_vm(
            connections=partial(_service_source_candidates, self._app_ctx, "glue")
        )
        origin = self.focused
        screen = GlueComparisonModal(
            vm, pin_candidate=candidate, copy=self.copy_value, initial_source=page.vm.source
        )

        def dismissed(_result: None) -> None:
            if origin is not None and origin.is_attached and origin.screen is self.screen:
                self.set_focus(origin)

        self.push_screen(screen, dismissed)

    def action_copy_glue_table_reference(self) -> None:
        self.record_action("glue.copy_table_ref")
        page = self._glue_page()
        if page is not None and not page.vm.actions_available:
            return
        if page is not None and page.vm.copy_table_reference():
            return
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="select a Glue table to copy",
            toast_id="glue-table-reference-unavailable",
        )

    async def action_insert_athena_table_reference(self) -> None:
        self.record_action("athena.insert_table_ref")
        page = self._athena_page()
        if page is None:
            return
        copied = self._app_ctx.table_clipboard_vm.copied_table
        if copied is None:
            notifications.advise(
                self._app_ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="copy a Glue table reference first",
                toast_id="athena-table-reference-empty",
            )
            return
        context = page.vm.context
        copied_source = (
            copied.table_ref.connection_name,
            copied.table_ref.region,
        )
        active_source = (context.connection_name, context.region)
        if copied_source != active_source:
            notifications.advise(
                self._app_ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message=(
                    f"copied {copied_source[0]} ({copied_source[1]}); "
                    f"active {active_source[0]} ({active_source[1]})"
                ),
                toast_id="athena-table-reference-source-mismatch",
            )
            return
        if not await page.insert_table_reference(copied.sql_identifier):
            return
        notifications.success(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="inserted copied table reference",
            toast_id="athena-table-reference-inserted",
        )

    def action_glue_iceberg_info(self) -> None:
        self.record_action("glue.iceberg_info")
        page = self._glue_page()
        if page is not None and page.vm.actions_available:
            page.query_one(GlueIcebergView).show_info()

    async def action_time_travel_glue_table_in_athena(self) -> None:
        self.record_action("glue.time_travel_in_athena")
        page = self._glue_page()
        if page is not None and not page.vm.actions_available:
            return
        if page is not None and page.vm.time_travel_in_athena():
            return
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="select an Iceberg snapshot to query in Athena",
            toast_id="glue-athena-snapshot-unavailable",
        )

    async def action_open_athena_table_in_glue(self) -> None:
        self.record_action("athena.open_in_glue")
        page = self._athena_page()
        if page is not None and page.vm.open_table_in_glue():
            return
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="query must reference one visible Glue table",
            toast_id="athena-glue-table-ambiguous",
        )

    async def _swap_single_context_source(self, service_id: str) -> None:
        """Rebuild a non-S3 service under its next supported AWS profile."""
        ctx = self._app_ctx
        target = _next_service_source(
            _service_source_candidates(ctx, service_id),
            ctx.root_vm.active_connection,
        )
        if target is None:
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="no AWS profiles configured",
            )
            return
        await self._rebuild_single_context_source(service_id, target)

    async def _switch_single_context_source_to(
        self,
        service_id: str,
        connection_name: str,
        region: str,
        *,
        connection_kind: str | None = None,
    ) -> bool:
        """Rebuild an AWS service, or delegate S3 to the focused pane's source swap.

        Discovery callers hold the navigation lock and validate captured pane ownership.
        Legacy source-header callers omit kind and retain AWS-only resolution.
        """
        ctx = self._app_ctx
        candidates = (
            self._discovery_source_candidates(service_id)
            if service_id == "s3"
            else _service_source_candidates(ctx, service_id)
        )
        target = next(
            (
                connection
                for connection in candidates
                if (connection.name, connection.region) == (connection_name, region)
                and (connection_kind is None or connection.kind == connection_kind)
            ),
            None,
        )
        if target is None:
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="selected AWS profile is no longer available",
            )
            return False
        if service_id == "s3":
            dual = self._dual_pane()
            if dual is None:
                return False
            pane = dual.focused_pane
            if pane.transfer_connection == connection_history_identity(target, pane.provider):
                return True
            try:
                await self._rebind_pane_to_connection(pane, target)
            except Exception as exc:
                ctx.log_sink.warning(
                    "discovery.source.failed",
                    service_id="s3",
                    error_type=type(exc).__name__,
                )
                notifications.advise(
                    ctx.root_vm.chrome.toast_stack,
                    subject="Source",
                    message="could not switch source — keeping current source",
                    toast_id="swap-source-auth-required",
                )
                return False
            return True
        active = ctx.root_vm.active_connection
        if active is not None and (active.kind, active.name, active.region) == (
            target.kind,
            target.name,
            target.region,
        ):
            return True
        return await self._rebuild_single_context_source(service_id, target)

    def _source_rollback_owned(self, state: _SourceRollback) -> bool:
        return (
            not self.service_navigation.closed
            and self._shutdown_task is None
            and self.service_navigation.owner == state.owner
        )

    def _source_input_untouched(self, observation: _SourceInputObservation) -> bool:
        if self._app_ctx.root_vm.content_host.current is not observation.vm:
            return False
        if observation.sql_changes:
            return False
        if isinstance(observation.vm, AthenaPageVM):
            if observation.vm.context_choice_revision != observation.context_choices:
                return False
            page = next((p for p in self.query(AthenaPage) if p.vm is observation.vm), None)
            if page is not None and page.is_attached:
                # Protect actual typing whose Changed message is still queued.
                return page.query_one("#athena-editor", TextArea).text == observation.vm.query.sql
        return True

    def _source_rollback_may_replace(self, state: _SourceRollback) -> bool:
        if not self._source_rollback_owned(state):
            return False
        current = self._app_ctx.root_vm.content_host.current
        return (
            current is None
            or current is state.outgoing
            or (state.target is not None and self._source_input_untouched(state.target))
        )

    def _claim_source_input(self, vm: object) -> bool:
        if isinstance(vm, AthenaPageVM):
            page = next((p for p in self.query(AthenaPage) if p.vm is vm), None)
            if page is not None and page.is_attached:
                editor = page.query_one("#athena-editor", TextArea)
                if editor.text != vm.query.sql:
                    return False
                editor.read_only = True
                page.disabled = True
            vm.close_input_admission()
        return True

    async def _rebuild_single_context_source(
        self,
        service_id: str,
        target: Connection,
    ) -> bool:
        """Rebuild a source, retaining exact owned Athena input for rollback."""
        ctx = self._app_ctx
        state = _SourceRollback(
            service_id,
            ctx.root_vm.active_connection,
            ctx.root_vm.active_auth_state,
            self.service_navigation.owner,
            ctx.root_vm.content_host.current,
        )

        def capture_outgoing() -> bool:
            if (
                not self._source_rollback_owned(state)
                or ctx.root_vm.content_host.current is not state.outgoing
            ):
                return False
            if isinstance(state.outgoing, AthenaPageVM):
                page = self._athena_page()
                if page is None or page.vm is not state.outgoing or page.is_disabled:
                    return False
                if page.query_one("#athena-editor", TextArea).text != state.outgoing.query.sql:
                    return False
                # Exports must precede input closure, and capture the latest
                # state after outgoing setup cleanup has finished yielding.
                state.editor = state.outgoing.export_editor_snapshot()
                try:
                    state.athena = state.outgoing.export_snapshot()
                except ValueError:
                    state.athena = None
            if not self._claim_source_input(state.outgoing):
                return False
            state.claimed = True
            return True

        def observe_target(vm: object) -> None:
            observation = _SourceInputObservation(vm)
            if isinstance(vm, AthenaPageVM):
                observation.context_choices = vm.context_choice_revision
                observation.subscription = vm.query.on_property_changed.subscribe(
                    observation.changed
                )
            state.target = observation

        try:
            try:
                auth_state = ctx.aws_session.probe_token(target).state
            except Exception as exc:
                ctx.log_sink.warning(
                    "service_source.probe_failed",
                    service_id=service_id,
                    connection=target.name,
                    error_type=type(exc).__name__,
                )
                auth_state = TokenState.MISSING
            try:
                adopted = await self.service_navigation.switch_connection_and_service(
                    ctx.root_vm,
                    target,
                    auth_state,
                    service_id,
                    prepare_vm=observe_target,
                    ownership_is_current=lambda: self._source_rollback_owned(state),
                    claim_input=capture_outgoing,
                )
                if not adopted or not self._source_rollback_owned(state):
                    return False
                if await self._mount_service_view(service_id, required_connection=target):
                    return self._source_rollback_owned(state)
            except asyncio.CancelledError:
                await self._restore_single_context_source_durably(state)
                raise
            except Exception as exc:
                ctx.log_sink.error(
                    "service_source.rebuild_failed",
                    service_id=service_id,
                    connection=target.name,
                    error_type=type(exc).__name__,
                )

            restored, rollback_cancelled = await self._restore_single_context_source_durably(state)
            if rollback_cancelled:
                raise asyncio.CancelledError
            if not self._source_rollback_owned(state):
                return False
            if (
                state.target is not None
                and ctx.root_vm.content_host.current is state.target.vm
                and not self._source_input_untouched(state.target)
            ):
                # A newer target edit owns this page, even if its mount failed.
                return False
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message=f"couldn't switch to {target.name}; "
                + ("restored the previous source" if restored else "source restore failed"),
            )
            return False
        finally:
            if state.target is not None and state.target.subscription is not None:
                state.target.subscription.dispose()

    async def _restore_single_context_source_durably(
        self,
        state: _SourceRollback,
    ) -> tuple[bool, bool]:
        rollback = asyncio.create_task(
            self._restore_single_context_source(state),
            name=f"{state.service_id}-source-rollback",
        )
        cancelled = False
        abort_requested = False
        while not rollback.done():
            try:
                await asyncio.shield(rollback)
            except asyncio.CancelledError:
                cancelled = True
                if not self._source_rollback_owned(state) and not abort_requested:
                    abort_requested = True
                    rollback.cancel()
                # Subsequent caller cancellation must not reach the child's
                # asynchronous cleanup through an unshielded gather.
        if abort_requested and rollback.cancelled():
            return False, True
        return rollback.result(), cancelled

    async def _restore_single_context_source(self, state: _SourceRollback) -> bool:
        if not self._source_rollback_may_replace(state):
            return False
        root = self._app_ctx.root_vm
        if not state.claimed:
            return root.content_host.current is state.outgoing
        connection, auth_state = state.connection, state.auth_state
        if connection is None or auth_state is None:
            return False
        candidate: object | None = None
        adopted = False
        publication_error: Exception | asyncio.CancelledError | None = None
        try:
            if state.service_id == "athena" and state.editor is not None:
                # Restore off-screen with inactive draft ownership. The live
                # target remains authoritative throughout awaited preflight.
                recovery = root.build_recovery_service_vm("athena", connection)
                candidate = recovery.vm
                if not isinstance(candidate, AthenaPageVM):
                    raise TypeError("Athena rollback requires an Athena page")
                candidate.construct()
                await candidate.setup()
                if not self._source_rollback_may_replace(state):
                    return False
                if state.athena is not None:
                    try:
                        await candidate.restore_snapshot(state.athena)
                    except ValueError:
                        # This candidate has never been exposed to user input.
                        # Retain SQL and its origin if context is unavailable;
                        # admission is checked again before publishing it.
                        await candidate.restore_source_editor(state.editor)
                else:
                    await candidate.restore_source_editor(state.editor)
                if not self._source_rollback_may_replace(state):
                    return False
                with self.service_navigation.suppress_selection(state.service_id):
                    try:
                        adopted = await self.service_navigation.adopt_prepared_service(
                            root,
                            connection,
                            auth_state,
                            state.service_id,
                            candidate,
                            ownership_is_current=lambda: self._source_rollback_may_replace(state),
                            claim_input=lambda: self._claim_source_input(root.content_host.current),
                        )
                    except (Exception, asyncio.CancelledError) as exc:
                        # Publication is the ownership transfer, even when a
                        # synchronous observer interrupts the return path.
                        adopted = root.content_host.current is candidate
                        if not adopted:
                            raise
                        publication_error = exc
                if not adopted:
                    return False
                if self._source_rollback_owned(state):
                    recovery.commit_selection()
            else:
                with self.service_navigation.suppress_selection(state.service_id):
                    adopted = await self.service_navigation.switch_connection_and_service(
                        root,
                        connection,
                        auth_state,
                        state.service_id,
                        ownership_is_current=lambda: self._source_rollback_may_replace(state),
                    )
            if not adopted or not self._source_rollback_owned(state):
                if publication_error is not None:
                    raise publication_error
                return False
            # No SQL write follows publication: newer mounted input always wins.
            mounted = await self._mount_service_view(
                state.service_id, required_connection=connection
            )
            if publication_error is not None:
                raise publication_error
            return mounted
        except Exception as exc:
            self._app_ctx.log_sink.error(
                "service_source.rollback_failed",
                service_id=state.service_id,
                connection=connection.name,
                error_type=type(exc).__name__,
            )
            return False
        finally:
            if candidate is not None and not adopted and root.content_host.current is not candidate:
                retirement = asyncio.create_task(
                    self._discard_staged_service_recovery(candidate),
                    name="athena-source-rollback-retirement",
                )
                retirement_cancelled = False
                while not retirement.done():
                    try:
                        await asyncio.shield(retirement)
                    except asyncio.CancelledError:
                        retirement_cancelled = True
                retirement.result()
                if retirement_cancelled:
                    raise asyncio.CancelledError

    def _make_s3_provider_for_connection(self, conn: Connection) -> FileSystemProvider:
        """Build the S3 pane provider through the registered S3 service.

        Demo and integration contexts inject ``S3Service.s3_fs_factory`` so
        every S3 provider must flow through the service; otherwise source
        switching can bypass seeded in-memory data and reach real boto
        sessions. The fallback keeps tiny unit harnesses that instantiate
        ``AwsTuiApp`` via ``object.__new__`` working.
        """
        from aws_tui.domain.s3_fs import S3FS
        from aws_tui.services.s3.service import S3Service, _aioboto3_session_for

        ctx = getattr(self, "_app_ctx", None)
        if ctx is not None:
            service = cast("S3Service", ctx.registry.get("s3"))
            return service.build_remote_provider(conn)
        session = _aioboto3_session_for(conn)
        return S3FS(
            session=session,
            bucket=None,
            endpoint_url=conn.endpoint_url,
            force_path_style=conn.force_path_style,
            verify_tls=conn.verify_tls,
        )

    def _make_local_provider(self) -> FileSystemProvider:
        """Build a LocalFS using the registered S3 service's local root."""
        from aws_tui.domain.local_fs import LocalFS
        from aws_tui.services.s3.service import S3Service

        ctx = getattr(self, "_app_ctx", None)
        if ctx is not None:
            service = cast("S3Service", ctx.registry.get("s3"))
            return service.build_local_provider()
        return LocalFS()

    def action_open_settings(self) -> None:
        """Select the Settings entry in the nav menu (programmatic equivalent
        of clicking it). Bound to ``,`` (comma)."""
        self.record_action("app.open_settings")
        self._app_ctx.root_vm.services_menu.switch_service_command.execute(SETTINGS_NAV_ID)

    async def _rebind_pane_to_local(self, pane: object) -> None:
        """Rebind a pane to the local filesystem provider.

        Mirrors the local-branch of ``action_swap_source``.
        """
        swap = getattr(pane, "swap_provider", None)
        if swap is None:
            return
        provider = self._make_local_provider()
        await swap(
            provider,
            identity_label="local",
            path_protocol="",
            connection_key=None,
            transfer_connection=connection_history_identity(None, provider),
        )

    async def _rebind_pane_to_connection(self, pane: object, conn: object) -> None:
        """Rebind a pane to an S3FS provider for ``conn``.

        Mirrors the remote-branch of ``action_swap_source``. ``conn`` is
        typed as ``object`` here to avoid a circular import with
        ``infra.connection_resolver``; runtime attribute access is safe
        (Connection is the only thing the resolver returns).
        """
        from aws_tui.services.s3.service import _format_pane_title

        swap = getattr(pane, "swap_provider", None)
        if swap is None:
            return
        provider = self._make_s3_provider_for_connection(conn)  # type: ignore[arg-type]
        await swap(
            provider,
            identity_label=_format_pane_title(conn),  # type: ignore[arg-type]
            path_protocol="s3:",
            connection_key=(conn.kind, conn.name),  # type: ignore[attr-defined]
            transfer_connection=connection_history_identity(conn, provider),  # type: ignore[arg-type]
        )

    async def action_themes(self) -> None:
        """Open the keyboard-navigable theme picker modal.

        Idempotent — if a ThemePickerModal is already on the screen
        stack (user pressed ``t`` more than once), this is a no-op.
        Previously each ``t`` press stacked another picker on top,
        requiring N ``Esc`` presses to clear N stacked modals.
        """
        self.record_action("app.themes")
        # ``self.screen_stack`` is ordered: the active screen is last.
        # Walk it and bail if any layer is already a ThemePickerModal.
        for screen in self.screen_stack:
            if isinstance(screen, ThemePickerModal):
                return

        ctx = self._app_ctx

        def _pick_with_toast(name: str) -> bool:
            if not self.switch_theme(name):
                return False
            self._raise_theme_changed_toast(name)
            return True

        picker = ThemePickerVM(
            themes=tuple(ctx.theme_store.list_themes()),
            active_theme=ctx.initial_theme,
            on_pick=_pick_with_toast,
            on_preview=self.switch_theme,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
        )
        picker.construct()
        modal = ThemePickerModal(picker=picker, hub=ctx.hub)

        # ``push_screen`` returns AwaitMount which resolves on MOUNT,
        # not dismiss — the dedicated wait-for-dismiss is
        # ``push_screen_wait`` (or the callback form). The prior
        # ``finally`` ran the moment the modal APPEARED, scheduling
        # picker.dispose on the next tick. Every subsequent user
        # action (cursor preview, Enter apply, Esc rollback) then
        # called pick_theme_command.execute / preview_command.execute
        # on already-disposed RelayCommands and mutated already-
        # disposed ThemeOptionVM._inner (whose _trigger_disposed=True
        # silently swallows property-change subjects). Use the
        # callback form so dispose runs on DISMISSAL.
        def _on_picker_dismiss(_result: None) -> None:
            self.call_after_refresh(picker.dispose)

        self.push_screen(modal, _on_picker_dismiss)

    def _raise_theme_changed_toast(self, theme_name: str) -> None:
        """Routed through :class:`ToastStackVM` (notifications overlay
        layer) rather than ``self.notify()`` (Textual's built-in
        bottom-center notify, which wrecks the footer)."""
        ctx = self._app_ctx
        notifications.announce(
            ctx.root_vm.chrome.toast_stack,
            subject="Theme",
            message=f"switched to {theme_name}",
            toast_id=f"theme-changed-{theme_name}",
        )

    # Stable read_from key for the aws-tui theme source — re-using it on
    # every ``add_source`` call means subsequent theme swaps REPLACE the
    # source instead of stacking (the old code accumulated one source
    # per swap, which is wasteful and can leak cached rules).
    _THEME_SOURCE_KEY: ClassVar[tuple[str, str]] = ("aws_tui", "active-theme.tcss")

    def _report_theme_switch_failure(
        self,
        name: str,
        stage: str,
        exc: Exception,
    ) -> None:
        ctx = self._app_ctx
        toast_id = f"theme-switch-failed-{name}"
        if any(toast.model.id == toast_id for toast in ctx.root_vm.chrome.toast_stack.toasts):
            return
        ctx.log_sink.error(
            "app.theme.switch_failed",
            name=name,
            stage=stage,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        notifications.error(
            ctx.root_vm.chrome.toast_stack,
            subject="Theme",
            message=f"could not switch to {name}",
            action="check theme syntax and logs",
            toast_id=toast_id,
        )

    def switch_theme(self, name: str) -> bool:
        """Runtime theme swap.

        Mirrors Textual's transactional ``_on_css_change`` flow and uses its
        public refresh pipeline:

        1. Load and parse a copied stylesheet with the candidate theme.
        2. Swap the validated copy via the stable ``read_from`` key so
           sources don't accumulate, then call ``refresh_css(animate=False)``.
        3. Restore the previous stylesheet if live application fails.
        4. Publish a ThemeChangedMessage on the hub so VMx-bound widgets
           that bake colors into Python (BrandBanner) can swap their
           per-theme palette without us reaching in by widget type.

        Returns ``True`` only after the candidate is applied successfully.

        ``refresh_css`` re-parses the stylesheet, re-resolves variables, and
        applies styles to every screen in the stack. It's the same API Textual
        uses internally for its theme reactive.
        """
        ctx = self._app_ctx
        try:
            theme_css = ctx.theme_store.load(name)
        except (OSError, ThemeNotFound, UnicodeError) as exc:
            self._report_theme_switch_failure(name, "load", exc)
            return False

        failure = self._apply_theme_css(name, theme_css)
        if failure is not None:
            self._report_theme_switch_failure(name, failure.stage, failure.error)
            return False

        ctx.initial_theme = name

        # 4. Broadcast for Python-side palettes (e.g. the banner).
        ctx.hub.send(ThemeChangedMessage(name=name))
        return True

    def _apply_theme_css(self, name: str, theme_css: str) -> _ThemeApplyFailure | None:
        """Validate and apply theme CSS while preserving the live stylesheet."""
        previous_stylesheet = self.stylesheet
        previous_sources = previous_stylesheet.source
        candidate = previous_stylesheet.copy()
        candidate.set_variables(self.get_css_variables())
        candidate.add_source(theme_css, read_from=self._THEME_SOURCE_KEY)
        try:
            candidate.parse()
        except (StylesheetError, TokenError) as exc:
            return _ThemeApplyFailure("validate", exc)

        self.stylesheet = candidate
        try:
            self.refresh_css(animate=False)
        except Exception as exc:
            self.stylesheet = previous_stylesheet
            try:
                self.refresh_css(animate=False)
            except Exception as rollback_exc:
                self._app_ctx.log_sink.error(
                    "app.theme.rollback_failed",
                    name=name,
                    error=str(rollback_exc),
                    error_type=type(rollback_exc).__name__,
                )
            finally:
                previous_stylesheet.source = previous_sources
            return _ThemeApplyFailure("apply", exc)
        return None

    # ── Connection-reachability tracking ───────────────────────────────────

    def _mark_connection_unreachable(self, kind: str, name: str) -> None:
        """Add ``(kind, name)`` to the unreachable set so subsequent
        Shift+S cycles skip this connection. Idempotent.
        """
        self._app_ctx.unreachable_connections.add((kind, name))
        self._app_ctx.log_sink.info("connection.unreachable.mark", kind=kind, name=name)

    def _clear_connection_unreachable(self, kind: str, name: str) -> None:
        """Remove ``(kind, name)`` from the unreachable set so it
        re-enters the swap-source ring. Idempotent.
        """
        self._app_ctx.unreachable_connections.discard((kind, name))
        self._app_ctx.log_sink.info("connection.unreachable.clear", kind=kind, name=name)

    def _on_pane_state_changed(
        self,
        *,
        kind: str,
        name: str,
        new_state: PaneState,
    ) -> None:
        """Hub-subscriber dispatch. When an active pane's state hits
        UNREACHABLE, mark its connection. When it transitions to
        IDLE / EMPTY from UNREACHABLE, clear the mark.

        Also resolves ``_attempt_future`` for the boot-time chain
        narrator when the LEFT pane reaches a terminal state for the
        currently-attempting connection key. ``_initial_mount_worker``
        is awaiting that future to decide success vs. failure vs.
        move-to-next-candidate. Filtering by ``_attempt_conn_key``
        keeps stale messages from a prior disposed PaneVM from
        resolving the next attempt's future.
        """
        from aws_tui.vm.file_manager.pane_vm import PaneState

        was_marked = (kind, name) in self._app_ctx.unreachable_connections
        if new_state is PaneState.UNREACHABLE:
            self._mark_connection_unreachable(kind, name)
        elif was_marked and new_state in (PaneState.IDLE, PaneState.EMPTY):
            self._clear_connection_unreachable(kind, name)
            # ``r`` retry (or any post-boot rebind) that actually
            # reached IDLE/EMPTY means the user has a working live
            # connection again — drop the chain-fallback memo so
            # Settings → S3 toggles return to the real DualPane
            # instead of staying pinned to local-on-both.
            self.service_navigation.mark_local_recovered()

        # Boot-chain attempt narrator: resolve the awaited future on
        # first terminal state for the matching connection key. The
        # chain owns the user-facing fallback decision (no reactive
        # auto-fallback anymore — the chain walks every candidate
        # explicitly and falls back to local on exhaustion).
        if (
            self._attempt_future is not None
            and not self._attempt_future.done()
            and self._attempt_conn_key == (kind, name)
        ):
            outcome: str | None = None
            if new_state in (PaneState.IDLE, PaneState.EMPTY):
                outcome = "ok"
            elif new_state is PaneState.UNREACHABLE:
                outcome = "unreachable"
            elif new_state is PaneState.AUTH_REQUIRED:
                outcome = "auth-required"
            elif new_state is PaneState.FORBIDDEN:
                outcome = "forbidden"
            elif new_state is PaneState.ERROR:
                # Generic ``ProviderError`` — without this branch the
                # boot chain stalls the full 90-s ``wait_for`` budget
                # instead of moving to "errored — trying next". The
                # friendly map at ``_friendly_outcome`` already carries
                # an ``"error"`` key.
                outcome = "error"
            if outcome is not None:
                self._attempt_future.set_result(outcome)

    def _on_connection_list_changed(self, msg: object) -> None:
        """Hub subscriber: drop deleted connection names from the
        unreachable set AND reload any pane bound to a changed connection."""
        if not isinstance(msg, ConnectionListChangedMessage):
            return
        if msg.change == "deleted":
            for name in msg.names:
                self._app_ctx.unreachable_connections.discard(("s3-compatible", name))
        # Schedule pane reload for affected connections (immediate, not
        # deferred). Skip on 'added' — new connections aren't bound yet.
        if msg.change == "added":
            return
        self._run_lifecycle_worker(
            partial(self._reload_panes_for, msg.names, deleted=msg.change == "deleted"),
            group="settings-reload",
        )

    async def _reload_panes_for(self, names: tuple[str, ...], *, deleted: bool) -> None:
        """Walk both panes; rebind any pane bound to a connection in ``names``."""
        dual = self._dual_pane()
        if dual is None:
            return
        for pane in (dual.left, dual.right):
            key = pane.current_connection_key
            if key is None:
                continue
            pane_kind, pane_name = key
            if pane_kind != "s3-compatible":
                continue
            if pane_name not in names:
                continue
            if deleted:
                await self._rebind_pane_to_local(pane)
            else:
                try:
                    conn = self._app_ctx.connection_resolver.resolve(pane_name)
                except ConnectionNotFound:
                    # The connection was deleted in Settings; falling back to
                    # the local filesystem is the intended recovery. A broader
                    # catch also swallowed a locked keychain and a corrupt
                    # config.toml, silently turning a live remote pane into a
                    # local one -- so the next copy targeted ``~`` instead of
                    # the bucket, with no toast and no log line.
                    await self._rebind_pane_to_local(pane)
                except Exception as exc:
                    self._app_ctx.log_sink.error(
                        "pane.rebind.resolve_failed",
                        connection=pane_name,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    notifications.error(
                        self._app_ctx.root_vm.chrome.toast_stack,
                        subject="Connection",
                        message=f"could not read connection '{pane_name}'",
                        toast_id=f"pane-rebind-{pane_name}",
                    )
                else:
                    await self._rebind_pane_to_connection(pane, conn)

    def _on_service_navigation_message(self, msg: object) -> None:
        if self.service_navigation.closed:
            return
        if isinstance(msg, OpenS3LocationRequest):
            generation = self._advance_service_navigation(
                "external",
                cancel_table_tasks=True,
            )
            self._run_lifecycle_worker(
                partial(self._open_s3_location_request, msg, generation), group="content-mount"
            )
            return
        if isinstance(msg, CopyTableReferenceRequest):
            self._copy_table_reference_request(msg)
            return
        if isinstance(msg, (OpenAthenaTableRequest, OpenGlueTableRequest)):
            generation = self._advance_service_navigation("table")
            navigation = asyncio.create_task(
                self._open_table_request(msg, generation),
                name=f"table-navigation-{generation}",
            )
            self.service_navigation.track_navigation(navigation)
            navigation.add_done_callback(self.service_navigation.untrack_navigation)
            self.run_worker(
                navigation,
                exclusive=True,
                group="content-mount",
            )

    def _copy_table_reference_request(self, request: CopyTableReferenceRequest) -> None:
        clipboard = self._app_ctx.table_clipboard_vm
        clipboard.copy_command.execute(request.table_ref)
        copied = clipboard.copied_table
        if copied is None:
            return
        # The typed in-app clipboard above is authoritative and already
        # holds the reference; this is the OS-clipboard leg, and it goes
        # through the same honest writer as every other copy. It used to
        # raise a success toast whether or not the write reached anything.
        self.copy_value(copied.sql_identifier, "table reference")

    def _advance_service_navigation(self, owner: str, *, cancel_table_tasks: bool = False) -> int:
        return self.service_navigation.advance(owner, cancel_table_tasks=cancel_table_tasks)

    def _supersede_table_navigation(self) -> int:
        return self.service_navigation.supersede()

    def _service_navigation_is_owned_by(self, owner: str, generation: int) -> bool:
        return self.service_navigation.owns(owner, generation)

    def _table_handoff_should_restore(self, generation: int) -> bool:
        return self.service_navigation.may_restore_handoff(generation)

    async def _open_table_request(
        self, request: OpenAthenaTableRequest | OpenGlueTableRequest, generation: int
    ) -> None:
        """Switch, mount, and select one exact Glue/Athena table."""
        async with self.service_navigation.table_lock:
            if not self.service_navigation.claim_table(generation):
                return
            await self._open_table_request_transaction(request, generation)

    async def _open_table_request_transaction(
        self,
        request: OpenAthenaTableRequest | OpenGlueTableRequest,
        generation: int,
    ) -> None:
        ctx = self._app_ctx
        ref = request.table_ref
        try:
            connection = ctx.connection_resolver.resolve(ref.connection_name)
        except Exception as exc:
            ctx.log_sink.warning(
                "service_navigation.table_connection_missing",
                connection=ref.connection_name,
                error_type=type(exc).__name__,
            )
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message=f"connection {ref.connection_name!r} is unavailable",
                toast_id="table-handoff-connection-missing",
            )
            return
        if connection.region != ref.region:
            ctx.log_sink.warning(
                "service_navigation.table_region_mismatch",
                connection=ref.connection_name,
                expected_region=ref.region,
                resolved_region=connection.region,
            )
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="table region does not match the source connection",
                toast_id="table-handoff-region-mismatch",
            )
            return

        if not await self._retry_pending_table_handoff(generation):
            return
        if generation != self.service_navigation.generation:
            return
        try:
            snapshot = self._capture_table_handoff_snapshot()
        except ValueError:
            ctx.log_sink.info(
                "service_navigation.table_handoff_deferred",
                destination=("athena" if isinstance(request, OpenAthenaTableRequest) else "glue"),
            )
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="finish the active Athena operation before switching services",
                toast_id="table-handoff-athena-busy",
            )
            return
        destination = "athena" if isinstance(request, OpenAthenaTableRequest) else "glue"
        current = ctx.root_vm.content_host.current
        outgoing: AthenaPageVM | None = (
            current
            if isinstance(request, OpenAthenaTableRequest) and isinstance(current, AthenaPageVM)
            else None
        )
        outgoing_claimed = False

        def editor_identity(page: AthenaPageVM) -> tuple[object, ...]:
            return (
                page.query.editor_revision,
                page.authoring.ownership_revision,
                page.context_choice_revision,
                page.context,
                page.query._draft_recovery_guard_revision,
            )

        outgoing_identity = editor_identity(outgoing) if outgoing is not None else None
        mutation_started = False

        def owns_outgoing() -> bool:
            if (
                generation != self.service_navigation.generation
                or self.service_navigation.closed
                or self._shutdown_task is not None
            ):
                return False
            if outgoing_claimed:
                return True  # Our own shutdown retires the captured page.
            if outgoing is None:
                return True
            current_vm: object = ctx.root_vm.content_host.current
            if (
                current_vm is not outgoing
                or not outgoing._is_alive()
                or editor_identity(outgoing) != outgoing_identity
            ):
                return False
            page = next((p for p in self.query(AthenaPage) if p.vm is outgoing), None)
            return (
                page is None
                or not page.is_attached
                or (page.query_one("#athena-editor", TextArea).text == outgoing.query.sql)
            )

        async def confirm_replace(message: str) -> bool:
            if not owns_outgoing() or ctx.confirm_vm.is_open:
                return False
            dialogs = TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub)
            return await ctx.confirm_vm.ask(
                ConfirmRequest(title=message, confirm_label="Replace"), dialog_service=dialogs
            )

        if outgoing is not None and not await outgoing.authoring.confirm_replacement(
            confirm=confirm_replace, still_selected=owns_outgoing
        ):
            return

        def claim_outgoing() -> bool:
            nonlocal snapshot, outgoing_claimed, mutation_started
            if not owns_outgoing():
                return False
            try:
                latest = self._capture_table_handoff_snapshot()
            except ValueError:
                return False
            if not owns_outgoing() or not self._claim_source_input(outgoing):
                return False
            snapshot = latest
            outgoing_claimed = True
            mutation_started = True
            return True

        primed_athena: AthenaPageVM | None = None
        primed_athena_token: object | None = None

        def destination_input_is_owned() -> bool:
            current = ctx.root_vm.content_host.current
            if outgoing is not None:
                if not outgoing_claimed:
                    return False  # A still-live source has not transferred its input.
                if current is None or current is outgoing:
                    # ContentHost may retire both source and unpublished candidate
                    # before raising cancellation or returning a rejected adoption.
                    return self._shutdown_task is None and self._table_handoff_should_restore(
                        generation
                    )
            return primed_athena is None or (
                primed_athena_token is not None
                and primed_athena.owns_table_query_input(primed_athena_token)
            )

        try:
            try:
                auth_state = ctx.aws_session.probe_token(connection).state
            except Exception as exc:
                ctx.log_sink.warning(
                    "service_navigation.table_probe_failed",
                    connection=connection.name,
                    error_type=type(exc).__name__,
                )
                auth_state = TokenState.MISSING
            if generation != self.service_navigation.generation:
                return

            with self.service_navigation.suppress_selection(destination):
                prepare_candidate: Callable[[object], None] | None = None
                if isinstance(request, OpenAthenaTableRequest):

                    def prime_athena_candidate(candidate: object) -> None:
                        nonlocal primed_athena, primed_athena_token
                        if not isinstance(candidate, AthenaPageVM):
                            raise RuntimeError("Athena destination is unavailable")
                        primed_athena_token = candidate.prime_table_query(ref, request.snapshot_id)
                        primed_athena = candidate

                    prepare_candidate = prime_athena_candidate

                options: ConnectionAdoptionOptions = {"prepare_vm": prepare_candidate}
                if outgoing is not None:
                    options["ownership_is_current"] = owns_outgoing
                    options["claim_input"] = claim_outgoing
                mutation_started = outgoing is None
                adopted = await self.service_navigation.switch_connection_and_service(
                    ctx.root_vm,
                    connection,
                    auth_state,
                    destination,
                    **options,
                )
                if not adopted:
                    if outgoing_claimed:
                        raise RuntimeError("destination adoption was retired")
                    return
            if await self._restore_superseded_table_handoff(
                generation, snapshot, input_is_owned=destination_input_is_owned
            ):
                if primed_athena is not None:
                    primed_athena.abandon_table_query_prime(prime_token=primed_athena_token)
                    primed_athena = None
                return
            if not await self._mount_service_view(
                destination,
                required_connection=connection,
            ):
                raise RuntimeError("destination mount failed")
            if isinstance(request, OpenAthenaTableRequest):
                target = ctx.root_vm.content_host.current
                if not isinstance(target, AthenaPageVM):
                    raise RuntimeError("Athena destination is unavailable")
                if primed_athena is not target:
                    raise RuntimeError("Athena destination was not prepared")
                page = self._athena_page()
                if page is None or page.vm is not target:
                    raise RuntimeError("Athena destination view is unavailable")
                page.refresh_from_vm()
                await self.wait_for_refresh()
                if await self._restore_superseded_table_handoff(
                    generation, snapshot, input_is_owned=destination_input_is_owned
                ):
                    primed_athena.abandon_table_query_prime(prime_token=primed_athena_token)
                    primed_athena = None
                    return
            await self._wait_for_current_service_setup()
            if await self._restore_superseded_table_handoff(
                generation, snapshot, input_is_owned=destination_input_is_owned
            ):
                return

            target = ctx.root_vm.content_host.current
            if isinstance(request, OpenAthenaTableRequest):
                if not isinstance(target, AthenaPageVM):
                    raise RuntimeError("Athena destination is unavailable")
                await target.open_table(ref, request.snapshot_id)
                page = self._athena_page()
                if page is None or page.vm is not target:
                    raise RuntimeError("Athena destination view is unavailable")
                page.refresh_from_vm()
            else:
                if not isinstance(target, GluePageVM):
                    raise RuntimeError("Glue destination is unavailable")
                await target.open_table(ref)
            await self.wait_for_refresh()
            await self._restore_superseded_table_handoff(
                generation, snapshot, input_is_owned=destination_input_is_owned
            )
        except GlueSelectionSuperseded:
            # A newer catalog choice owns the mounted destination.
            return
        except asyncio.CancelledError:
            restore_owned_destination = destination_input_is_owned()
            if primed_athena is not None:
                primed_athena.abandon_table_query_prime(prime_token=primed_athena_token)
            if (
                mutation_started
                and restore_owned_destination
                and self._table_handoff_should_restore(generation)
            ):
                restored, _ = await self._restore_table_handoff_durably(
                    snapshot,
                    generation,
                    input_is_owned=destination_input_is_owned,
                )
                if not restored and outgoing_claimed:
                    self._retain_failed_table_handoff(snapshot, generation)
            raise
        except Exception as exc:
            restore_owned_destination = destination_input_is_owned()
            if primed_athena is not None:
                primed_athena.abandon_table_query_prime(prime_token=primed_athena_token)
            rollback_cancelled = False
            if (
                mutation_started
                and restore_owned_destination
                and self._table_handoff_should_restore(generation)
            ):
                restored, rollback_cancelled = await self._restore_table_handoff_durably(
                    snapshot,
                    generation,
                    input_is_owned=destination_input_is_owned,
                )
                if not restored and outgoing_claimed:
                    self._retain_failed_table_handoff(snapshot, generation)
            if rollback_cancelled:
                raise asyncio.CancelledError from None
            if generation != self.service_navigation.generation:
                return
            ctx.log_sink.error(
                "service_navigation.table_handoff_failed",
                connection=connection.name,
                destination=destination,
                error_type=type(exc).__name__,
            )
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message=f"could not open the table in {destination.title()}",
                toast_id="table-handoff-destination-failed",
            )

    def _retain_failed_table_handoff(
        self, snapshot: _TableHandoffSnapshot, generation: int
    ) -> None:
        if snapshot.athena is not None and self._table_handoff_should_restore(generation):
            # One owned recovery obligation; an empty successor capture must not
            # replace the only remaining copy of the accepted outgoing document.
            if self._pending_table_handoff is None:
                self._pending_table_handoff = snapshot
            self._pending_table_handoff_notice()

    def _pending_table_handoff_notice(self) -> None:
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="previous SQL is retained in this session. Retry opening the table to recover it",
            toast_id="table-handoff-recovery-pending",
        )

    async def _retry_pending_table_handoff(self, generation: int) -> bool:
        snapshot = self._pending_table_handoff
        if snapshot is None:
            return True
        current = self._app_ctx.root_vm.content_host.current
        if isinstance(current, AthenaPageVM) and (
            current.query.is_executing
            or current.query.is_submitting
            or current.query.is_context_resolving
            or current.results.is_loading_more
        ):
            self._pending_table_handoff_notice()
            return False
        editor_identity = (
            (
                current.query.editor_revision,
                current.authoring.ownership_revision,
                current.context_choice_revision,
                current.query.context,
                current.query._draft_recovery_guard_revision,
            )
            if isinstance(current, AthenaPageVM)
            else None
        )
        claimed = False

        def owns_retry() -> bool:
            if self._shutdown_task is not None or not self._table_handoff_should_restore(
                generation
            ):
                return False
            if claimed:
                return True
            hosted: object = self._app_ctx.root_vm.content_host.current
            if hosted is not current or generation != self.service_navigation.generation:
                return False
            if isinstance(current, AthenaPageVM):
                identity = (
                    current.query.editor_revision,
                    current.authoring.ownership_revision,
                    current.context_choice_revision,
                    current.query.context,
                    current.query._draft_recovery_guard_revision,
                )
                if not current._is_alive() or identity != editor_identity:
                    return False
                page = next((p for p in self.query(AthenaPage) if p.vm is current), None)
                if page is not None and page.is_attached:
                    return page.query_one("#athena-editor", TextArea).text == current.query.sql
            return True

        def claim_retry() -> bool:
            nonlocal claimed
            if not owns_retry() or not self._claim_source_input(current):
                return False
            claimed = True
            return True

        if isinstance(current, AthenaPageVM) and (current.query.sql or current.authoring.dirty):
            ctx = self._app_ctx
            if not owns_retry() or ctx.confirm_vm.is_open:
                return False
            accepted = await ctx.confirm_vm.ask(
                ConfirmRequest(
                    title="Restore previous SQL before opening the table?",
                    body_lines=("This replaces the current editor with the retained document.",),
                    confirm_label="Restore",
                ),
                dialog_service=TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub),
            )
            if not accepted or not owns_retry():
                return False

        restored, cancelled = await self._restore_table_handoff_durably(
            snapshot,
            generation,
            input_is_owned=owns_retry,
            adoption_options={"ownership_is_current": owns_retry, "claim_input": claim_retry},
        )
        if restored and self._pending_table_handoff is snapshot:
            self._pending_table_handoff = None
        if cancelled:
            raise asyncio.CancelledError
        if not restored:
            self._pending_table_handoff_notice()
        return restored

    def _capture_table_handoff_snapshot(self) -> _TableHandoffSnapshot:
        ctx = self._app_ctx
        current = ctx.root_vm.content_host.current
        return _TableHandoffSnapshot(
            connection=ctx.root_vm.active_connection,
            auth_state=ctx.root_vm.active_auth_state,
            service_id=ctx.root_vm.content_host.current_id,
            athena=current.export_snapshot() if isinstance(current, AthenaPageVM) else None,
            glue=self._capture_glue_page_snapshot(current)
            if isinstance(current, GluePageVM)
            else None,
        )

    @staticmethod
    def _capture_glue_page_snapshot(page: GluePageVM) -> _GluePageSnapshot:
        selected_table_name = page.catalog.selected_table_name
        selected = next(
            (row for row in page.catalog.tables if row.ref.table_name == selected_table_name),
            None,
        )
        iceberg = page.catalog.iceberg
        return _GluePageSnapshot(
            active_view=page.active_view,
            database_name=page.catalog.selected_database_name,
            table_ref=selected.ref if selected is not None else None,
            iceberg_view=iceberg.active_view if iceberg.available else None,
            iceberg_snapshot_id=iceberg.selected_snapshot_id,
            job_name=page.jobs.selected_job_name,
            job_run_id=page.jobs.selected_run_id,
            job_run_states=page.jobs.run_state_filter,
            crawler_name=page.crawlers.selected_crawler_name,
            crawler_state=page.crawlers.state_filter,
        )

    @staticmethod
    async def _restore_glue_page_snapshot(
        page: GluePageVM,
        snapshot: _GluePageSnapshot,
    ) -> None:
        if page.active_view != snapshot.active_view:
            await page.select_view(snapshot.active_view)
        if snapshot.active_view == "catalog":
            if snapshot.table_ref is not None:
                await page.open_table(snapshot.table_ref)
            elif snapshot.database_name is not None:
                await page.select_database(snapshot.database_name)
            if snapshot.table_ref is not None and snapshot.iceberg_view is not None:
                iceberg = page.catalog.iceberg
                view_restored = await iceberg.select_view(snapshot.iceberg_view)
                if not view_restored or iceberg.active_view != snapshot.iceberg_view:
                    raise ValueError("Glue recovery Iceberg view is unavailable")
                if snapshot.iceberg_snapshot_id is not None:
                    snapshot_restored = iceberg.select_snapshot(snapshot.iceberg_snapshot_id)
                    if (
                        not snapshot_restored
                        or iceberg.selected_snapshot_id != snapshot.iceberg_snapshot_id
                    ):
                        raise ValueError("Glue recovery Iceberg snapshot is unavailable")
            if snapshot.database_name is not None and (
                page.catalog.selected_database_name != snapshot.database_name
            ):
                raise ValueError("Glue recovery database is unavailable")
            if snapshot.table_ref is not None and (
                page.catalog.selected_table_name != snapshot.table_ref.table_name
            ):
                raise ValueError("Glue recovery table is unavailable")
        elif snapshot.active_view == "jobs":
            await page.set_job_run_states(snapshot.job_run_states)
            if snapshot.job_name is not None:
                await page.select_job(snapshot.job_name)
            if snapshot.job_run_id is not None:
                page.select_job_run(snapshot.job_run_id)
            if page.jobs.run_state_filter != snapshot.job_run_states:
                raise ValueError("Glue recovery job filter is unavailable")
            if snapshot.job_name is not None and page.jobs.selected_job_name != snapshot.job_name:
                raise ValueError("Glue recovery job is unavailable")
            if snapshot.job_run_id is not None and page.jobs.selected_run_id != snapshot.job_run_id:
                raise ValueError("Glue recovery job run is unavailable")
        else:
            await page.set_crawler_state(snapshot.crawler_state)
            if snapshot.crawler_name is not None:
                await page.select_crawler(snapshot.crawler_name)
            if page.crawlers.state_filter != snapshot.crawler_state:
                raise ValueError("Glue recovery crawler filter is unavailable")
            if (
                snapshot.crawler_name is not None
                and page.crawlers.selected_crawler_name != snapshot.crawler_name
            ):
                raise ValueError("Glue recovery crawler is unavailable")

    async def _restore_superseded_table_handoff(
        self,
        generation: int,
        snapshot: _TableHandoffSnapshot,
        *,
        input_is_owned: Callable[[], bool] | None = None,
    ) -> bool:
        if generation == self.service_navigation.generation:
            return False
        if self._table_handoff_should_restore(generation) and (
            input_is_owned is None or input_is_owned()
        ):
            restored, cancelled = await self._restore_table_handoff_durably(
                snapshot,
                generation,
                input_is_owned=input_is_owned,
            )
            if not restored:
                self._retain_failed_table_handoff(snapshot, generation)
            if cancelled:
                raise asyncio.CancelledError
        return True

    async def _restore_table_handoff_durably(
        self,
        snapshot: _TableHandoffSnapshot,
        generation: int,
        *,
        input_is_owned: Callable[[], bool] | None = None,
        adoption_options: ConnectionAdoptionOptions | None = None,
    ) -> tuple[bool, bool]:
        rollback = asyncio.create_task(
            self._restore_table_handoff(
                snapshot,
                generation,
                input_is_owned=input_is_owned,
                adoption_options=adoption_options,
            ),
            name="table-handoff-rollback",
        )
        self.service_navigation.track_rollback(rollback)
        current = asyncio.current_task()
        cancellation_count = current.cancelling() if current is not None else 0
        cancelled = False
        try:
            while not rollback.done():
                try:
                    await asyncio.shield(rollback)
                except asyncio.CancelledError:
                    next_count = current.cancelling() if current is not None else 0
                    if next_count > cancellation_count:
                        cancelled = True
                        cancellation_count = next_count
                    continue
            try:
                return rollback.result(), cancelled
            except Exception as exc:
                self._app_ctx.log_sink.error(
                    "service_navigation.table_rollback_failed",
                    stage="restore",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return False, cancelled
        finally:
            self.service_navigation.untrack_rollback(rollback)

    async def _wait_for_current_service_setup(self) -> None:
        task = self._app_ctx.root_vm.content_host._setup_task
        if task is not None and not task.done():
            await task
        await self.wait_for_refresh()

    async def _restore_table_handoff(
        self,
        snapshot: _TableHandoffSnapshot,
        generation: int,
        *,
        input_is_owned: Callable[[], bool] | None = None,
        adoption_options: ConnectionAdoptionOptions | None = None,
    ) -> bool:
        async with self.service_navigation.service_lock:
            if input_is_owned is not None and not input_is_owned():
                return False
            if adoption_options is None and input_is_owned is not None:
                claimed = False

                def owns_restore() -> bool:
                    if self._shutdown_task is not None or not self._table_handoff_should_restore(
                        generation
                    ):
                        return False
                    return claimed or input_is_owned()

                def claim_restore() -> bool:
                    nonlocal claimed
                    if not owns_restore() or not self._claim_source_input(
                        self._app_ctx.root_vm.content_host.current
                    ):
                        return False
                    claimed = True
                    return True

                adoption_options = {
                    "ownership_is_current": owns_restore,
                    "claim_input": claim_restore,
                }
            return await self._restore_table_handoff_transaction(
                snapshot,
                generation,
                adoption_options=adoption_options,
            )

    async def _restore_table_handoff_transaction(
        self,
        snapshot: _TableHandoffSnapshot,
        generation: int,
        *,
        adoption_options: ConnectionAdoptionOptions | None = None,
    ) -> bool:
        if not self._table_handoff_should_restore(generation):
            return False
        if (
            snapshot.connection is None
            or snapshot.auth_state is None
            or snapshot.service_id is None
        ):
            self._app_ctx.log_sink.error(
                "service_navigation.table_rollback_failed",
                stage="missing_snapshot",
            )
            return False
        with self.service_navigation.suppress_selection(snapshot.service_id):
            try:
                adopted = await self.service_navigation.switch_connection_and_service(
                    self._app_ctx.root_vm,
                    snapshot.connection,
                    snapshot.auth_state,
                    snapshot.service_id,
                    **(adoption_options or {}),
                )
                if not adopted:
                    return False
            except Exception as exc:
                self._app_ctx.log_sink.error(
                    "service_navigation.table_rollback_failed",
                    connection=snapshot.connection.name,
                    service_id=snapshot.service_id,
                    stage="switch",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return False
        if not self._table_handoff_should_restore(generation):
            return False
        await self._wait_for_current_service_setup()
        if not self._table_handoff_should_restore(generation):
            return False
        current = self._app_ctx.root_vm.content_host.current
        if isinstance(current, AthenaPageVM):
            if snapshot.athena is not None:
                await current.restore_snapshot(snapshot.athena)
        elif isinstance(current, GluePageVM) and snapshot.glue is not None:
            await self._restore_glue_page_snapshot(current, snapshot.glue)
        if not self._table_handoff_should_restore(generation):
            return False
        # Restore before mounting. An editable empty recovery view would accept
        # newer input that the later snapshot installation could overwrite.
        if not await self._mount_service_view(
            snapshot.service_id,
            required_connection=snapshot.connection,
        ):
            return False
        if not self._table_handoff_should_restore(generation):
            return False
        await self.wait_for_refresh()
        return True

    async def _open_s3_location_request(
        self,
        request: OpenS3LocationRequest,
        generation: int | None = None,
    ) -> None:
        """Resolve and mount an S3 request without changing source identity."""
        if self.service_navigation.closed:
            return
        if generation is None:
            generation = self._supersede_table_navigation()
        async with self.service_navigation.service_lock:
            if not self._service_navigation_is_owned_by("external", generation):
                return
            await self._open_s3_location_request_transaction(request)

    async def _open_s3_location_request_transaction(
        self,
        request: OpenS3LocationRequest,
    ) -> None:
        ctx = self._app_ctx
        try:
            connection = ctx.connection_resolver.resolve(request.connection_name)
        except Exception as exc:
            ctx.log_sink.warning(
                "service_navigation.connection_missing",
                connection=request.connection_name,
                error_type=type(exc).__name__,
            )
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message=f"connection {request.connection_name!r} is unavailable",
                toast_id="s3-handoff-connection-missing",
            )
            return

        if connection.region != request.region:
            ctx.log_sink.warning(
                "service_navigation.region_mismatch",
                connection=request.connection_name,
                expected_region=request.region,
                resolved_region=connection.region,
            )
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="S3 location region does not match the source connection",
                toast_id="s3-handoff-region-mismatch",
            )
            return

        location = parse_s3_uri(request.uri)
        if location is None:
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="requested location is not a valid S3 URI",
                toast_id="s3-handoff-invalid-location",
            )
            return

        target = self._s3_handoff_target(request, location.bucket, location.path)
        if target is None:
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="requested location is not a valid S3 URI",
                toast_id="s3-handoff-invalid-location",
            )
            return
        destination, object_name = target
        current = ctx.root_vm.content_host.current
        athena_snapshot: AthenaPageSnapshot | None = None
        if isinstance(current, AthenaPageVM):
            with contextlib.suppress(ValueError):
                athena_snapshot = current.export_snapshot()
        snapshot = _S3HandoffSnapshot(
            connection=ctx.root_vm.active_connection,
            auth_state=ctx.root_vm.active_auth_state,
            service_id=ctx.root_vm.content_host.current_id,
            athena=athena_snapshot,
            athena_result_execution_id=(
                current.results.execution_id
                if isinstance(current, AthenaPageVM) and current.active_view == "results"
                else None
            ),
            glue=self._capture_glue_page_snapshot(current)
            if isinstance(current, GluePageVM)
            else None,
        )

        try:
            auth_state = ctx.aws_session.probe_token(connection).state
        except Exception as exc:
            ctx.log_sink.warning(
                "service_navigation.probe_failed",
                connection=connection.name,
                error_type=type(exc).__name__,
            )
            auth_state = TokenState.MISSING

        stage = "mount"
        try:
            with self.service_navigation.suppress_selection("s3"):
                await self.service_navigation.switch_connection_and_service(
                    ctx.root_vm,
                    connection,
                    auth_state,
                    "s3",
                )

            if not await self._mount_service_view(
                "s3",
                required_connection=connection,
            ):
                raise _S3HandoffStageError("mount", "MountFailed")

            dual = self._dual_pane()
            if dual is None:
                raise _S3HandoffStageError("mount", "MissingDualPane")

            pane = dual.left if request.preferred_pane == "left" else dual.right
            connection_key = (connection.kind, connection.name)
            stage = "bind"
            if pane.current_connection_key != connection_key:
                await self._rebind_pane_to_connection(pane, connection)

            stage = "navigation"
            await pane.navigate_to(destination)
            if pane.state not in {PaneState.IDLE, PaneState.EMPTY}:
                raise _S3HandoffStageError(
                    "navigation",
                    f"Pane{pane.state.value.title()}",
                )
            if object_name is not None:
                object_index = next(
                    (
                        index
                        for index, entry in enumerate(pane.filtered_entries)
                        if entry.entry.name == object_name and entry.kind is EntryKind.FILE
                    ),
                    None,
                )
                if object_index is None:
                    raise _S3HandoffStageError(
                        "navigation",
                        "ResultObjectMissing",
                    )
                pane.move_cursor_to(object_index)

            stage = "focus"
            focused = FocusedPane.LEFT if request.preferred_pane == "left" else FocusedPane.RIGHT
            slot = FocusSlot.S3_LEFT if focused is FocusedPane.LEFT else FocusSlot.S3_RIGHT
            dual.set_focused(focused)
            ctx.focus_coordinator.set_underlying_slot(slot)
            if not ctx.focus_coordinator.is_modal:
                self._project_focus_slot(slot)
        except asyncio.CancelledError:
            if not self._s3_handoff_was_superseded(snapshot):
                await self._restore_s3_handoff_snapshot(snapshot)
            raise
        except _S3HandoffStageError as exc:
            await self._restore_s3_handoff_snapshot(snapshot)
            self._raise_s3_handoff_failure(
                connection=connection,
                stage=exc.stage,
                error_type=exc.error_type,
            )
        except Exception as exc:
            await self._restore_s3_handoff_snapshot(snapshot)
            self._raise_s3_handoff_failure(
                connection=connection,
                stage=stage,
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _s3_handoff_target(
        request: OpenS3LocationRequest,
        bucket: str,
        raw_path: str,
    ) -> tuple[PathRef, str | None] | None:
        """Resolve a directory destination and optional object cursor target."""
        from aws_tui.domain.filesystem import PathRef

        if not request.reveal_object:
            return PathRef.from_posix(bucket + raw_path), None
        key = raw_path.removeprefix("/")
        if (
            not key
            or key.endswith("/")
            or raw_path.startswith("//")
            or any(not segment for segment in key.split("/"))
        ):
            return None
        object_path = PathRef((bucket, *key.split("/")))
        return object_path.parent(), object_path.name

    def _s3_handoff_was_superseded(
        self,
        snapshot: _S3HandoffSnapshot,
    ) -> bool:
        del snapshot
        selected = self._app_ctx.root_vm.services_menu.selected_id
        return selected != "s3"

    async def _restore_s3_handoff_snapshot(
        self,
        snapshot: _S3HandoffSnapshot,
    ) -> bool:
        """Finish rollback even when the handoff task itself is cancelled."""
        rollback = asyncio.create_task(
            self._restore_service_after_s3_handoff_failure(
                connection=snapshot.connection,
                auth_state=snapshot.auth_state,
                service_id=snapshot.service_id,
                athena=snapshot.athena,
                athena_result_execution_id=snapshot.athena_result_execution_id,
                glue=snapshot.glue,
            )
        )
        while True:
            try:
                return await asyncio.shield(rollback)
            except asyncio.CancelledError:
                if self._s3_handoff_was_superseded(snapshot):
                    rollback.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await rollback
                    raise
                if rollback.done():
                    return rollback.result()
                continue

    async def _restore_service_after_s3_handoff_failure(
        self,
        *,
        connection: Connection | None,
        auth_state: TokenState | None,
        service_id: str | None,
        athena: AthenaPageSnapshot | None = None,
        athena_result_execution_id: str | None = None,
        glue: _GluePageSnapshot | None = None,
    ) -> bool:
        """Restore the coherent service snapshot captured before a handoff."""
        if connection is None or auth_state is None or service_id is None:
            self._app_ctx.log_sink.error(
                "service_navigation.s3_rollback_failed",
                stage="missing_snapshot",
            )
            return False

        with self.service_navigation.suppress_selection(service_id):
            try:
                with contextlib.suppress(Exception):
                    host = self.query_one("#content-host", Container)
                    await host.remove_children()
                    release_detached_query_results(host.ancestors_with_self)
                await self.service_navigation.switch_connection_and_service(
                    self._app_ctx.root_vm,
                    connection,
                    auth_state,
                    service_id,
                )
            except Exception as exc:
                self._app_ctx.log_sink.error(
                    "service_navigation.s3_rollback_failed",
                    connection=connection.name,
                    service_id=service_id,
                    stage="switch",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return False

        restored = await self._mount_service_view(
            service_id,
            required_connection=connection,
        )
        if not restored:
            self._app_ctx.log_sink.error(
                "service_navigation.s3_rollback_failed",
                connection=connection.name,
                service_id=service_id,
                stage="mount",
            )
            return False
        setup_task = self._app_ctx.root_vm.content_host._setup_task
        if setup_task is not None and not setup_task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await setup_task
        current = self._app_ctx.root_vm.content_host.current
        if service_id == "athena" and isinstance(current, AthenaPageVM):
            if athena is not None:
                await current.restore_snapshot(athena)
            elif athena_result_execution_id is not None:
                await current.results.load(athena_result_execution_id, from_history=True)
                await current.select_view("results")
        elif service_id == "glue" and isinstance(current, GluePageVM) and glue is not None:
            await self._restore_glue_page_snapshot(current, glue)
        return restored

    def _raise_s3_handoff_failure(
        self,
        *,
        connection: Connection,
        stage: str,
        error_type: str = "HandoffFailed",
    ) -> None:
        """Record a handoff failure without retaining exception or URI text."""
        ctx = self._app_ctx
        ctx.log_sink.error(
            "service_navigation.s3_handoff_failed",
            connection=connection.name,
            stage=stage,
            error_type=error_type,
        )
        with contextlib.suppress(Exception):
            notifications.advise(
                ctx.root_vm.chrome.toast_stack,
                subject="Source",
                message="could not open the S3 location",
                toast_id=f"s3-handoff-{stage}-failed",
            )

    def _on_hub_message_pane_state(self, msg: object) -> None:
        """Hub subscriber: route PaneVM state changes to the reachability set.

        Reads ``PaneVM.current_connection_key`` directly — the key is stored
        atomically inside ``swap_provider`` BEFORE ``_reload()`` runs, so
        there is no race between the state-change notification and the key
        update (Bug 1 fix). The subscription is established BEFORE
        ``switch_service`` in ``on_mount`` so the initial UNREACHABLE
        state-change is never missed (Bug 2 fix).
        """
        from vmx import PropertyChangedMessage

        from aws_tui.vm.file_manager.pane_vm import PaneVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name != "state":
            return
        if not isinstance(msg.sender_object, PaneVM):
            return
        sender_vm = msg.sender_object
        key = sender_vm.current_connection_key
        if key is None:
            return  # local pane — never tracked
        self._on_pane_state_changed(kind=key[0], name=key[1], new_state=sender_vm.state)

    def _on_hub_message_cursor(self, msg: object) -> None:
        """Hub subscriber: when ANY PaneVM emits a cursor / entries
        change, recompute which Commands chips are disabled based on
        the focused pane's cursor target. Today's only rule: the
        ``..`` parent row disables ``pane.copy`` and ``pane.delete``
        (no source to copy/delete — the parent reference is
        navigation-only). EMR selection, detail, and busy events also
        update cancellation availability.
        """
        from vmx import PropertyChangedMessage

        from aws_tui.vm.file_manager.pane_vm import PaneVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        from aws_tui.vm.file_manager.transfer_vm import TransferVM

        if msg.sender_object is self._app_ctx.transfers_vm or isinstance(
            msg.sender_object, TransferVM
        ):
            # State notifications also carry byte-only progress. Discovery
            # needs only transitions between no active transfers and some.
            active = bool(self._app_ctx.transfers_vm.active_count)
            if active != self._transfers_discovery_active:
                self._transfers_discovery_active = active
                self._refresh_discovery_surfaces()
            return
        emr_page = self._emr_page()
        if emr_page is not None and msg.sender_object in {
            emr_page.vm,
            emr_page.vm.applications,
            emr_page.vm.job_runs,
            emr_page.vm.job_run_detail,
            emr_page.vm.job_run_logs,
        }:
            self._recompute_hint_disables()
            return
        glue_page = self._glue_page()
        if glue_page is not None and msg.sender_object in {
            glue_page.vm,
            glue_page.vm.catalog,
            glue_page.vm.catalog.iceberg,
            glue_page.vm.jobs,
            glue_page.vm.crawlers,
        }:
            self._recompute_hint_disables()
            return
        athena_page = self._athena_page()
        if athena_page is not None and msg.sender_object in {
            athena_page.vm,
            athena_page.vm.query,
            athena_page.vm.history,
            athena_page.vm.results,
            athena_page.vm.saved,
        }:
            self._recompute_hint_disables()
            return
        if msg.property_name not in {"cursor_index", "viewmodel", "entries"}:
            return
        if not isinstance(msg.sender_object, PaneVM):
            return
        self._recompute_hint_disables()

    def _on_table_clipboard_changed(self, _property_name: str) -> None:
        self._recompute_hint_disables()

    def _recompute_hint_disables(self) -> None:
        self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(self._readiness_disabled())
        self._refresh_discovery_surfaces()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        ContextPicker.close_open_for_outside_mouse_down(
            self.screen.query(ContextPicker),
            event.widget,
        )
        ApplicationPicker.close_open_for_outside_mouse_down(
            self.screen.query(ApplicationPicker),
            event.widget,
        )

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        if self._athena_page() is not None or self._glue_page() is not None:
            self._recompute_hint_disables()

    def on_app_blur(self, _event: events.AppBlur) -> None:
        """Tear down pointer-driven state the app can no longer observe.

        Switching macOS Spaces or terminal tabs sends ``AppBlur``, and Textual
        8.2.8 tears down only *focus* on that path (``App._watch_app_focus``,
        ``textual/app.py:4418-4448``, blur branch ``:4443-4448``). Two pieces
        of interaction state survive it, both reported by a user doing exactly
        that gesture:

        1. **The tooltip.** Nothing clears it on blur, so a tooltip opened by
           resting the pointer on a row stays painted over the pane for as
           long as the app is away. ``Screen._clear_tooltip`` is the routine
           Textual itself uses for this, from ``_on_screen_suspend``
           (``textual/screen.py:1502-1508``); blur is simply a case it does
           not cover.
        2. **The mouse capture.** A scrollbar drag interrupted by a blur
           leaves ``App.mouse_captured`` pointing at the scrollbar. Every
           other Textual teardown of interaction state calls
           ``capture_mouse(None)`` (``textual/app.py:2868``, ``:2941``,
           ``:3017``); the blur path is the odd one out.

        The capture orphan is *measurably harmless today* — 0 B/s of output,
        0.0% CPU, and it self-heals on the next click, which costs the user
        exactly one swallowed click. It is fixed here because of what sits
        under it: ``ScrollBar._on_mouse_capture``
        (``textual/scrollbar.py:363``) calls ``App._realtime_animation_begin``,
        which calls ``gc.disable()`` when ``PAUSE_GC_ON_SCROLL`` is true, and
        the matching ``_realtime_animation_complete`` runs only on
        ``MouseRelease``. Textual's class default is ``False``
        (``textual/app.py:526``) and this app does not override it — that, and
        only that, is why the orphan is benign. Set that flag with the blur
        path unfixed and the same orphan leaves ``gc.disable()`` in force for
        the rest of the process, degrading into precisely the progressive "the
        app got unusably slow after I switched away and back" the user
        reported. ``capture_mouse(None)`` posts the ``MouseRelease`` that
        balances the begin.

        Defensive throughout: this runs on a path where the screen stack may
        be in any state (mid-push, mid-pop, or empty during shutdown), and a
        raise here would reach ``App._handle_exception`` and kill the app over
        a cosmetic cleanup. Nothing in here is allowed to propagate.
        """
        # Two suppressions, not one: the outer covers reading the stack at all
        # (``screen_stack`` raises before the mode is set up and during
        # shutdown), the inner keeps one uncooperative screen from skipping
        # the rest of them.
        with contextlib.suppress(Exception):
            # Clear across the whole stack, not just the top: a tooltip that
            # was showing on a screen a modal has since covered is still a
            # painted artefact when the modal pops. ``_clear_tooltip``
            # early-returns when a screen has no ``Tooltip`` child, so this is
            # free for every screen that was never hovered.
            for screen in self.screen_stack:
                with contextlib.suppress(Exception):
                    screen._clear_tooltip()
        with contextlib.suppress(Exception):
            # No-ops when nothing is captured: ``App.capture_mouse`` returns
            # immediately when the new widget equals ``mouse_captured``.
            self.capture_mouse(None)

    def _on_nav_selection_changed(self, msg: object) -> None:
        """Hub subscriber: route NavMenuVM selected_id changes to the content host.

        When "settings" is selected, calls ``ContentHostVM.set_content`` with
        ``settings_vm`` and mounts ``SettingsView`` in the content host.
        For any other service id, rebuilds the DualPane-based S3 view.
        """
        from vmx import PropertyChangedMessage

        from aws_tui.vm.nav_menu_vm import NavMenuVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name != "selected_id":
            return
        if not isinstance(msg.sender_object, NavMenuVM):
            return
        ctx = self._app_ctx
        selected = ctx.root_vm.services_menu.selected_id
        if selected is None:
            return
        # Push the active service id down to the HintLegend so the
        # bottom Commands pane re-renders its service-specific chips
        # (S3 → copy/delete/swap-src; EMR → switch-app/refresh; …).
        # The legend's trailing globals (themes/help/quit) are
        # independent of this.
        ctx.root_vm.chrome.hint_legend.set_current_service(selected)
        if self.service_navigation.consume_selection(selected):
            return
        generation = self._supersede_table_navigation()
        # Skip the seed selected_id change that on_mount fires while
        # priming the initial service. on_mount drives that mount
        # synchronously via _mount_initial_service_view; if we ALSO
        # spawn a mount worker here, the two race against the same
        # #content-host and one silently clobbers the other → blank
        # screen at startup.
        if self._boot_in_flight:
            launch = getattr(ctx, "launch", None)
            if launch is not None:
                if selected == launch.service_id:
                    return
                self._boot_in_flight = False
                self.workers.cancel_group(self, "content-mount")
                self._run_lifecycle_worker(
                    partial(self._mount_external_navigation, selected, generation),
                    group="content-mount",
                )
                return
            if selected != "s3":
                self._boot_in_flight = False
                self.workers.cancel_group(self, "content-mount")
                self._run_lifecycle_worker(
                    partial(self._mount_external_navigation, selected, generation),
                    group="content-mount",
                )
            return
        # Serialize the two mount workers via an exclusive worker
        # group so a rapid Settings → S3 → Settings toggle can't race
        # against itself. Without exclusivity, the awaited
        # ``switch_service`` inside ``_mount_service_view`` would
        # interleave with a later ``_mount_settings_view`` task: the
        # service worker would resume after the settings worker
        # replaced ContentHost.current, read the now-SettingsVM, and
        # try to wrap it in a DualPane — which then crashes with
        # ``AttributeError: 'SettingsVM' object has no attribute 'left'``
        # (observed on Windows py3.11 in CI; rarer but possible
        # everywhere). ``exclusive=True`` + a shared group name makes
        # Textual cancel any in-flight worker in the group before
        # starting the new one.
        if selected == SETTINGS_NAV_ID:
            self._run_lifecycle_worker(
                partial(self._mount_external_navigation, selected, generation),
                group="content-mount",
            )
        else:
            # Re-use the S3 content if it's already hosted; switch_service
            # is idempotent on the same service_id.
            self._run_lifecycle_worker(
                partial(self._mount_external_navigation, selected, generation),
                group="content-mount",
            )

    async def _mount_external_navigation(
        self,
        selected: str,
        generation: int,
    ) -> None:
        async with self.service_navigation.service_lock:
            if self.service_navigation.closed or not self._service_navigation_is_owned_by(
                "external", generation
            ):
                return
            menu = self._app_ctx.root_vm.services_menu
            if menu.selected_id != selected:
                with self.service_navigation.suppress_selection(selected):
                    menu.switch_service_command.execute(selected)
            for section in self.query(FirstRunConnectionList):
                section.disabled = False
            if selected == SETTINGS_NAV_ID:
                view = self._first_run_view()
                focused = self.focused
                if (
                    view is not None
                    and focused is not None
                    and (
                        view in focused.ancestors_with_self
                        or any(
                            isinstance(node, FirstRunConnectionList)
                            for node in focused.ancestors_with_self
                        )
                    )
                ):
                    # Pruning a focused setup control otherwise transfers focus to the
                    # rail, whose existing ownership gate prevents Settings focus.
                    self.set_focus(None)
                await self._mount_settings_view()
            elif self._app_ctx.root_vm.active_connection is None:
                await self._mount_no_connection_placeholder()
                if self._service_navigation_is_owned_by("external", generation):
                    self._request_first_run_discovery()
            else:
                await self._mount_service_view(selected)

    async def _mount_settings_view(self) -> None:
        """Swap the content host to show SettingsView.

        Build a fresh :class:`SettingsVM` per mount. The previous
        singleton pattern crashed on the second Settings switch:
        ``ContentHostVM.set_content`` calls ``vm.dispose()`` on the
        outgoing VM and ``vm.construct()`` on the incoming one, so
        after the first Settings → S3 swap the singleton was in
        ``Disposed`` state and re-mounting raised
        ``StatusTransitionError('Cannot construct from state Disposed.')``.
        ``SettingsVM.dispose()`` does NOT cascade to its
        :class:`S3ConnectionsVM` child (per ``SettingsVM.dispose``),
        so the shared connection list/selection state survives across
        rebuilds — only the thin ComponentVM wrapper is recreated.
        """
        from aws_tui.vm.settings.settings_vm import SettingsVM

        ctx = self._app_ctx
        await self._cancel_transfer_workers_before_content_swap()
        settings_vm = SettingsVM(
            s3=ctx.s3_connections_vm,
            athena_drafts=ctx.athena_drafts_vm,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
        )
        try:
            host = self.query_one("#content-host", Container)
            await self.service_navigation.adopt_content(
                ctx.root_vm, settings_vm, service_id=SETTINGS_NAV_ID
            )
            replacement = SettingsView(
                vm=settings_vm,
                hub=ctx.hub,
                focus_coordinator=ctx.focus_coordinator,
            )
        except BaseException as exc:
            if ctx.root_vm.content_host.current is not settings_vm:
                with contextlib.suppress(Exception):
                    settings_vm.dispose()
            if isinstance(exc, asyncio.CancelledError):
                raise
            ctx.log_sink.error(
                "app.mount_settings_view.set_content_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._restore_navigation_after_failed_adoption("Settings")
            return
        try:
            # ``await`` both the remove and the mount so a cancelled
            # worker (e.g. user toggling Settings ↔ S3 rapidly) can't
            # leave the host with a half-attached widget. Without the
            # awaits, ``mount`` returns an AwaitMount that's never
            # consumed — if the worker is cancelled between remove and
            # mount completion, the pending mount still fires after the
            # next worker's remove_children, leaving BOTH widgets in
            # the DOM.
            await self._replace_content_widget(host, replacement)
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_settings_view.mount_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Same recovery as the ``set_content`` failure branch above.
            # ``_replace_content_widget`` mounts its own generic placeholder,
            # but without this the nav rail still showed Settings selected and
            # no toast fired -- so a one-off mount failure read as "Settings is
            # broken forever" rather than "that failed, try again".
            self._restore_navigation_after_failed_adoption("Settings")

    async def _mount_service_view(
        self, service_id: str, *, required_connection: Connection | None = None
    ) -> bool:
        """Adapt the UI operations for one shared navigation-owner attempt."""
        ctx = self._app_ctx

        def prepare_mount() -> ViewPublisher:
            host = self.query_one("#content-host", Container)

            async def publish(service: str, vm: object) -> None:
                replacement = build_service_view(
                    service,
                    vm,
                    hub=ctx.hub,
                    keymap=ctx.keymap_store,
                    source_candidates=_service_source_contexts(ctx, service),
                    focus_coordinator=ctx.focus_coordinator,
                )
                await self._replace_content_widget(host, replacement)

            return publish

        def begin_retry(connection: Connection) -> None:
            ctx.unreachable_connections.discard((connection.kind, connection.name))
            self._last_skip_toast_set = None

        def report_retry_failure(connection: Connection, outcome: str) -> None:
            self._mark_connection_unreachable(connection.kind, connection.name)
            self._raise_failure_toast(connection, outcome)

        return await self.service_navigation.mount_service(
            service_id,
            required_connection=required_connection,
            ports=ServiceMountPorts(
                root=ctx.root_vm,
                log=ctx.log_sink,
                cancel_transfers=self._cancel_transfer_workers_before_content_swap,
                prepare_mount=prepare_mount,
                retry_connection=self._try_connection,
                mount_local_fallback=lambda connection: self._mount_local_only_dual_pane(
                    initial_conn=connection, reason="chain-exhausted"
                ),
                begin_retry=begin_retry,
                report_retry_failure=report_retry_failure,
                report_local_fallback=self._raise_local_fallback_toast,
                restore_navigation=self._restore_navigation_after_failed_adoption,
                refresh_hints=self._recompute_hint_disables,
            ),
        )

    def _restore_navigation_after_failed_adoption(self, requested_id: str) -> None:
        ctx = self._app_ctx

        def notify_failure(requested: str, current: str | None) -> None:
            notifications.error(
                ctx.root_vm.chrome.toast_stack,
                subject="Mount",
                message=f"Could not open {requested}; restored {current or 'current view'}",
                toast_id="navigation-adoption-failed",
            )

        self.service_navigation.restore_navigation(
            ctx.root_vm, requested_id, notify_failure=notify_failure
        )

    async def _replace_content_widget(self, host: Container, replacement: Widget) -> None:
        """Mount a content replacement or leave a coherent error surface."""
        navigation_owner = self.service_navigation.owner
        if any(candidate[0] is host for candidate in self._content_mount_hosts.values()):
            host = await self._reset_content_host(host)
            if self.service_navigation.closed or self.service_navigation.owner != navigation_owner:
                return
        await host.remove_children()
        release_detached_query_results(host.ancestors_with_self)
        if self.service_navigation.closed or self.service_navigation.owner != navigation_owner:
            return
        self._content_mount_hosts[replacement] = (host, navigation_owner)
        try:
            await host.mount(replacement)
            self.call_after_refresh(lambda: self._expire_content_mount_registration(replacement))
        except Exception as exc:
            self._content_mount_hosts.pop(replacement, None)
            try:
                host = await self._reset_content_host(host)
                if (
                    self.service_navigation.closed
                    or self.service_navigation.owner != navigation_owner
                ):
                    return
                await host.mount(
                    Static(
                        "Unable to render this view.",
                        id="content-mount-error",
                        markup=False,
                    )
                )
            except Exception as recovery_exc:
                exc.add_note(f"content error surface also failed to mount: {recovery_exc}")
            raise

    def _expire_content_mount_registration(self, replacement: Widget) -> None:
        if replacement not in self._content_mount_recovering:
            self._content_mount_hosts.pop(replacement, None)

    async def _reset_content_host(self, host: Container) -> Container:
        """Replace the host boundary, including children unable to process pruning."""
        parent = host.parent
        if not isinstance(parent, Widget):
            raise RuntimeError("content host has no mounted widget parent")
        siblings = list(parent.children)
        index = siblings.index(host)
        next_sibling = siblings[index + 1] if index + 1 < len(siblings) else None
        cache_owners = host.ancestors
        await host.remove()
        release_detached_query_results(cache_owners)
        replacement = Container(id="content-host")
        if self.service_navigation.closed:
            return replacement
        await parent.mount(replacement, before=next_sibling)
        return replacement

    def _content_mount_owner(
        self, error: Exception
    ) -> tuple[Widget, Container, tuple[str, int] | None] | None:
        """Find a registered replacement implicated in a lifecycle traceback."""
        hosts = getattr(self, "_content_mount_hosts", None)
        if hosts is None:
            return None
        recovering = getattr(self, "_content_mount_recovering", ())
        replacement = self._lifecycle_mount_widget(
            error, tuple(widget for widget in hosts if widget not in recovering)
        )
        if replacement is None:
            return None
        host, navigation_owner = hosts[replacement]
        return replacement, host, navigation_owner

    @staticmethod
    def _lifecycle_mount_widget(error: Exception, candidates: tuple[Widget, ...]) -> Widget | None:
        """Find the registered ancestor of a failing Textual lifecycle widget."""
        frames: list[Any] = []
        traceback = error.__traceback__
        saw_lifecycle = False
        while traceback is not None:
            frame = traceback.tb_frame
            frames.append(frame)
            saw_lifecycle |= (
                frame.f_globals.get("__name__") == "textual.message_pump"
                and frame.f_code.co_name == "_pre_process"
            ) or (
                frame.f_globals.get("__name__") == "textual.widget"
                and frame.f_code.co_name == "_compose"
            )
            traceback = traceback.tb_next
        if not saw_lifecycle:
            return None
        for frame in reversed(frames):
            widget = frame.f_locals.get("self")
            if not isinstance(widget, Widget):
                continue
            current: Widget | None = widget
            while current is not None:
                if current in candidates:
                    return current
                parent = current.parent
                current = parent if isinstance(parent, Widget) else None
        return None

    async def _recover_content_mount_lifecycle(
        self,
        replacement: Widget,
        host: Container,
        error: Exception,
        navigation_owner: tuple[str, int] | None,
    ) -> None:
        def is_owned() -> bool:
            return (
                not self.service_navigation.closed
                and self.service_navigation.owner == navigation_owner
            )

        try:
            # Intent can advance while resetting, but the winning service mount
            # must wait until the empty host boundary is repaired.
            async with self.service_navigation.service_lock:
                if not is_owned():
                    return
                current_host = self.query_one("#content-host", Container)
                if current_host is not host:
                    return
                self._app_ctx.log_sink.error(
                    "app.content_mount.lifecycle_failed",
                    error=str(error),
                    error_type=type(error).__name__,
                )
                recovered_host = await self._reset_content_host(host)
                if (
                    not is_owned()
                    or self.query_one("#content-host", Container) is not recovered_host
                ):
                    return
                if navigation_owner is not None and navigation_owner[0] == "first-run":
                    await self._mount_no_connection_placeholder()
                    if (
                        not is_owned()
                        or self.query_one("#content-host", Container) is not recovered_host
                    ):
                        return
                    recovered = await self._refresh_first_run_discovery(navigation_owner[1])
                    if (
                        not is_owned()
                        or self.query_one("#content-host", Container) is not recovered_host
                    ):
                        return
                    self._set_first_run_busy(False)
                    view = self._first_run_view()
                    if view is not None:
                        if recovered is not None:
                            view.show_error(
                                "Unable to open connection. Check the connection, then select it again."
                            )
                        self.call_after_refresh(
                            partial(self._focus_first_run, view, navigation_owner[1])
                        )
                    return
                await recovered_host.mount(
                    Static(
                        "Unable to render this view.",
                        id="content-mount-error",
                        markup=False,
                    )
                )
        finally:
            self._content_mount_hosts.pop(replacement, None)
            self._content_mount_recovering.discard(replacement)

    # ── Crash handling ─────────────────────────────────────────────────────

    def record_action(self, action_id: str) -> None:
        """Record an action id in the ring buffer and track it as the latest.

        The shipped binding dispatcher and action registry call this so the
        crash modal can decide whether ``continue`` is safe and the dump can
        include the last 100 user actions per spec §7.10.
        """
        ts = datetime.now(UTC).isoformat()
        self._action_ring.append(f"{ts} {action_id}")
        self._last_action_id = action_id

    @property
    def last_action_id(self) -> str | None:
        return self._last_action_id

    def _build_crash_report(self, exc: BaseException) -> CrashReport:
        """Write the dump and assemble the matching :class:`CrashReport`.

        Side effects: a new file under ``~/.cache/aws-tui/crash/`` and an
        ``ERROR``-level log line tagged ``crash.captured``. Always
        succeeds (falls back to a side-channel path if the write fails).
        """
        ctx = self._app_ctx
        log_path = ctx.log_sink.path
        try:
            dump = CrashDump(base_dir=log_path.parent.parent / "crash")
            dump_path = dump.write(
                exc=exc,
                log_path=log_path,
                action_ring=list(self._action_ring),
            )
        except Exception as dump_exc:
            fallback_stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")
            dump_path = log_path.parent / f"crash-fallback-{fallback_stamp}.txt"
            fallback_body = redact_text(
                "\n".join(
                    [
                        "aws-tui crash dump unavailable; fallback report",
                        f"exception: {type(exc).__name__}: {exc}",
                        f"dump_error: {type(dump_exc).__name__}: {dump_exc}",
                        "",
                        "== traceback ==",
                        CrashDump.short_traceback(exc, max_lines=20),
                        "",
                    ]
                )
            )
            try:
                dump_path.write_text(fallback_body, encoding="utf-8")
                with contextlib.suppress(OSError, NotImplementedError):
                    dump_path.chmod(0o600)
            except Exception:
                dump_path = Path("<crash dump unavailable>")
        last_id = self._last_action_id
        report = CrashReport(
            timestamp=datetime.now(UTC),
            exception_type=type(exc).__name__,
            exception_message=redact_text(str(exc) or repr(exc)),
            traceback_short=redact_text(CrashDump.short_traceback(exc)),
            dump_path=dump_path,
            can_continue=CrashReport.is_safe_to_continue(last_id),
            last_action_id=last_id,
        )
        with contextlib.suppress(Exception):
            ctx.log_sink.error(
                "crash.captured",
                exception_type=report.exception_type,
                dump_path=str(report.dump_path),
                last_action_id=report.last_action_id,
            )
            ctx.log_sink.flush()
        return report

    def _handle_exception(self, error: Exception) -> None:
        """Override Textual's fatal handler to write a crash dump first.

        We still defer to the upstream behavior (which sets ``_return_code``
        and tears down) — the dump and report are the only thing we add
        before the app exits.
        """
        recovery_errors = getattr(self, "_recovery_mount_errors", {})
        recovery_widget = self._lifecycle_mount_widget(error, tuple(recovery_errors))
        if recovery_widget is not None:
            recovery_errors[recovery_widget].append(error)
            return
        mount_owner = self._content_mount_owner(error)
        if mount_owner is not None:
            replacement, host, navigation_owner = mount_owner
            # Claim recovery before yielding to the worker. Textual may surface
            # more than one lifecycle exception while unwinding a failed mount.
            self._content_mount_recovering.add(replacement)
            self._run_lifecycle_worker(
                partial(
                    self._recover_content_mount_lifecycle,
                    replacement,
                    host,
                    error,
                    navigation_owner,
                ),
                name="content mount recovery",
                group="content-mount-recovery",
            )
            return
        try:
            self._crash_report = self._build_crash_report(error)
        finally:
            super()._handle_exception(error)

    async def show_crash_modal(self, report: CrashReport) -> CrashChoice:
        """Push the crash modal for ``report`` and await the user's choice.

        Public so tests and recovery flows can drive the modal without
        also having to raise an exception. The in-app crash path
        (``_handle_exception``) does not currently call this — see
        ``CHANGELOG.md``'s ``Deferred / v0.9 roadmap`` block. (The label
        this used to cite, ``deferred-from-m6``, no longer exists anywhere,
        and the ``record_action`` half of the claim it pointed at was false:
        ``record_action`` is invoked from every bound action.)
        """
        ctx = self._app_ctx
        crash_vm = CrashVM(report, hub=ctx.hub, dispatcher=ctx.dispatcher)
        crash_vm.construct()
        ask_task: asyncio.Task[CrashChoice] | None = None
        try:
            ask_task = asyncio.create_task(crash_vm.ask())
            await self.push_screen(CrashModal(crash_vm, hub=ctx.hub))
            return await ask_task
        finally:
            # Cancel + drain ask_task on the cancellation path BEFORE
            # disposing the VM. Without this, an outer cancellation at
            # ``push_screen`` or ``ask_task`` raises CancelledError —
            # crash_vm.dispose() runs but ask_task is never explicitly
            # cancelled nor awaited. CrashVM.dispose happens to resolve
            # the future via set_result(QUIT) today, but the coupling
            # is fragile (a future refactor switching to ``cancel()``
            # would orphan ask_task), and a race window exists where
            # the outer cancel lands BEFORE ask_task creates the
            # future — then dispose's short-circuit leaves ask_task
            # raising a "modal disposed" RuntimeError nobody awaits,
            # triggering asyncio's "never retrieved" warning. Same
            # R38 family.
            if ask_task is not None and not ask_task.done():
                ask_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await ask_task
            crash_vm.dispose()

    @property
    def crash_report(self) -> CrashReport | None:
        """The last crash report captured via ``_handle_exception``."""
        return self._crash_report

    def _close_service_navigation_intake(self) -> None:
        if self.service_navigation.closed:
            return
        self.service_navigation.close_intake()
        subscription = getattr(self, "_service_navigation_sub", None)
        if subscription is not None:
            subscription.dispose()
        self._service_navigation_sub = None

    async def _drain_table_navigation(self) -> None:
        await self.service_navigation.drain()

    async def _await_tasks_through_cancellation(
        self,
        tasks: tuple[asyncio.Task[Any], ...],
    ) -> None:
        for task in tasks:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            with contextlib.suppress(Exception, asyncio.CancelledError):
                task.result()

    async def _aws_tui_shutdown(self) -> None:
        """Run shutdown once and let every caller await the same task."""
        if getattr(self, "_shutdown_complete", False):
            return
        task = getattr(self, "_shutdown_task", None)
        if task is None:
            task = asyncio.create_task(self._perform_aws_tui_shutdown())
            self._shutdown_task = task
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        self._shutdown_complete = True

    async def _perform_aws_tui_shutdown(self) -> None:
        """Graceful shutdown per spec sec 5.4.

        Renamed away from ``_shutdown`` to avoid colliding with the
        internal ``App._shutdown`` lifecycle hook on Textual.
        """
        ctx = self._app_ctx
        errors: list[tuple[str, BaseException]] = []

        def run_cleanup(step: str, cleanup: Any) -> None:
            try:
                cleanup()
            except (Exception, asyncio.CancelledError) as exc:
                errors.append((step, exc))

        async def await_cleanup(step: str, cleanup: Any) -> None:
            try:
                await cleanup()
            except (Exception, asyncio.CancelledError) as exc:
                errors.append((step, exc))

        def dispose_subscription(attribute: str) -> None:
            subscription = getattr(self, attribute, None)
            if subscription is None:
                return
            subscription.dispose()
            setattr(self, attribute, None)

        for screen in tuple(getattr(self, "screen_stack", ())):
            if isinstance(screen, GlueComparisonModal):
                run_cleanup("glue_comparison.close", screen.close)
                await await_cleanup("glue_comparison.shutdown", screen.shutdown)

        # Popped screens can still be draining cancellation-resistant reads.
        # Keep ownership until unmount reports actual drainage, not just pop.
        for browser in tuple(getattr(self, "_version_browsers", ())):
            await await_cleanup("s3_versions.shutdown", browser.shutdown)
        for review in tuple(getattr(self, "_upload_reviews", ())):
            await await_cleanup("recursive_upload.shutdown", review.shutdown)

        run_cleanup("service_navigation.close_intake", self._close_service_navigation_intake)
        recovery_task = getattr(self, "_auth_recovery_task", None)
        if recovery_task is not None and not recovery_task.done():
            recovery_task.cancel()
            await await_cleanup(
                "credential_recovery.task",
                partial(self._await_tasks_through_cancellation, (recovery_task,)),
            )
        self._auth_recovery_task = None
        run_cleanup(
            "workers.cancel_content_mount",
            lambda: self.workers.cancel_group(self, "content-mount"),
        )

        palette_shutdown = getattr(ctx.command_palette_vm, "shutdown", None)
        if callable(palette_shutdown):
            await await_cleanup("command_palette.shutdown", palette_shutdown)

        navigation_lock = getattr(self, "_service_navigation_lock", None)
        if navigation_lock is not None:

            async def drain_navigation_lock() -> None:
                async with navigation_lock:
                    pass

            await await_cleanup("service_navigation.lock", drain_navigation_lock)
        await await_cleanup("table_navigation.drain", self._drain_table_navigation)
        run_cleanup("transfers.cancel_all", ctx.transfers_vm.cancel_all_command.execute)
        await await_cleanup(
            "transfer_workers.cancel",
            self._cancel_transfer_workers_before_content_swap,
        )

        async def shutdown_hosted_content() -> None:
            host_shutdown = asyncio.create_task(ctx.root_vm.content_host.shutdown())
            while not host_shutdown.done():
                try:
                    await asyncio.shield(host_shutdown)
                except asyncio.CancelledError:
                    continue
            await host_shutdown

        await await_cleanup("transfer_history.shutdown", ctx.transfer_history_vm.shutdown)

        async def shutdown_drafts() -> None:
            report = await ctx.athena_drafts_vm.shutdown()
            if report.unpersisted:
                ctx.athena_drafts_shutdown_warning = (
                    "Some Athena SQL edits were not confirmed saved before exit."
                )
                ctx.log_sink.warning(
                    "athena.drafts.unpersisted",
                    count=report.unpersisted,
                    timed_out=report.timed_out,
                )

        await await_cleanup("athena_drafts.shutdown", shutdown_drafts)
        await await_cleanup("content_host.shutdown", shutdown_hosted_content)
        await await_cleanup("aws_session.aclose_all_clients", ctx.aws_session.aclose_all_clients)

        for attribute in (
            "_pane_state_sub",
            "_connection_state_sub",
            "_connection_list_sub",
            "_nav_selection_sub",
            "_cursor_sub",
            "_service_navigation_sub",
            "_palette_failure_sub",
            "_history_sub",
        ):
            run_cleanup(
                f"subscription.{attribute.removeprefix('_')}.dispose",
                partial(dispose_subscription, attribute),
            )
        run_cleanup(
            "table_clipboard.subscription.dispose",
            self._dispose_table_clipboard_subscription,
        )

        for step, disposable in (
            ("s3_connections_vm.dispose", ctx.s3_connections_vm),
            ("command_palette_vm.dispose", ctx.command_palette_vm),
            ("quick_look_vm.dispose", ctx.quick_look_vm),
            ("confirm_vm.dispose", ctx.confirm_vm),
            ("transfers_vm.dispose", ctx.transfers_vm),
            ("athena_drafts_vm.dispose", ctx.athena_drafts_vm),
            ("transfer_history_vm.dispose", ctx.transfer_history_vm),
            ("table_clipboard_vm.dispose", ctx.table_clipboard_vm),
            ("clipboard_vm.dispose", ctx.clipboard_vm),
            ("root_vm.dispose", ctx.root_vm),
            ("focus_coordinator.dispose", ctx.focus_coordinator),
        ):
            run_cleanup(step, disposable.dispose)

        for connection_name, demo_emr in ctx.demo_emrs.items():
            await await_cleanup(f"demo_emrs.{connection_name}.aclose", demo_emr.aclose)

        # Keep structured logging available until every other owner has been
        # drained. Cleanup errors are durable diagnostics, but shutdown remains
        # best-effort and does not turn an ordinary quit into a crash.
        for step, exc in errors:
            try:
                ctx.log_sink.error(
                    "app.shutdown.cleanup_failed",
                    step=step,
                    error_type=type(exc).__name__,
                    error=redact_text(str(exc)),
                )
            except Exception:
                break
        run_cleanup("log_sink.flush", ctx.log_sink.flush)
        run_cleanup("log_sink.close", ctx.log_sink.close)
        self._shutdown_errors = tuple((step, type(exc).__name__) for step, exc in errors)


def main() -> None:
    """Run diagnostics before composition, or launch the Textual app.

    Invoked by the ``aws-tui`` console script and ``python -m aws_tui``.
    If the app surfaces an unhandled exception, ``_handle_exception``
    writes a crash dump under the runtime cache's crash directory and the
    saved :class:`CrashReport` is printed here before the exception is
    re-raised so the user knows where the dump landed.

    Recognises ``doctor``, ``--help``, ``--version``, and ``--demo`` before
    launching the UI. Doctor renders a read-only report and exits without
    creating app state or negotiating terminal protocols.
    """
    from aws_tui.demo import is_demo_mode_enabled

    parser = argparse.ArgumentParser(
        prog="aws-tui",
        description="Cross-platform TUI for AWS and S3-compatible services.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="launch with deterministic in-memory demo data and no real AWS calls",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the aws-tui version and demo-mode status, then exit",
    )
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser(
        "doctor", help="inspect local configuration without launching the UI"
    )
    doctor.add_argument(
        "--json", action="store_true", help="print the schema-versioned JSON report"
    )
    doctor.add_argument(
        "--probe", metavar="NAME", help="perform a read-only network probe of one exact source name"
    )
    from aws_tui.launch import LaunchError, LaunchRequest, resolve_launch, service_definitions

    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--connection", metavar="NAME", help="exact known connection name")
    identity.add_argument(
        "--profile", metavar="NAME", help="exact locally discoverable AWS profile"
    )
    parser.add_argument(
        "--region",
        metavar="REGION",
        help="session region: lowercase letters/digits separated by hyphens, ending in digits (eu-west-1)",
    )
    parser.add_argument(
        "--service",
        choices=[definition.descriptor.id for definition in service_definitions()],
        help="registered service ID (default: s3)",
    )
    parser.add_argument(
        "--location",
        metavar="LOCATION",
        help="s3://BUCKET[/PREFIX] or existing native directory; implies s3",
    )
    parser.epilog = (
        "Selectors override [defaults].connection and AWS_DEFAULT_PROFILE/AWS_PROFILE "
        "for this session only; configuration is not saved."
    )
    args = parser.parse_args()
    request = LaunchRequest(args.connection, args.profile, args.region, args.service, args.location)
    demo = args.demo or is_demo_mode_enabled(argv=[])
    if request.explicit and (demo or args.command == "doctor"):
        parser.error("launch selectors cannot be combined with demo mode or doctor")

    if args.command == "doctor":
        if args.demo or args.version:
            parser.error("doctor cannot be combined with --demo or --version")
        doctor_report = collect_local_diagnostics()
        if args.probe is not None:
            doctor_report = replace(
                doctor_report,
                checks=(
                    *tuple(check for check in doctor_report.checks if check.name != "probe"),
                    probe_source(args.probe),
                ),
            )
        print(doctor_report.render_json() if args.json else doctor_report.render_text())
        raise SystemExit(doctor_report.exit_code)

    if args.version:
        status = "enabled" if demo else "disabled"
        # Match the pip convention: ``project-name 0.8.0``.
        print(f"aws-tui {__version__} (demo: {status})")
        return

    launch = None
    if request.explicit:
        try:
            launch = resolve_launch(request)
        except LaunchError as exc:
            print(f"aws-tui: {exc}", file=sys.stderr)
            raise SystemExit(2) from None

    context: AppContext | None = None
    try:
        context = (
            build_app_context(demo=demo, launch=launch)
            if launch is not None
            else build_app_context(demo=demo)
        )
        app = AwsTuiApp(context=context)
    except Exception as exc:
        if context is not None:
            context.close_unstarted()
        if launch is not None:
            print("aws-tui: explicit launch could not be constructed", file=sys.stderr)
            raise SystemExit(1) from None
        message = redact_text(str(exc)) or "startup failed"
        print(
            f"\naws-tui failed to start.\n  {type(exc).__name__}: {message}\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    # Must precede `run()`: the driver and its input parser are built there,
    # and the in-band resize protocol is negotiated during driver start-up.
    prefer_sigwinch_resize()

    try:
        app.run()
    except BaseException as exc:
        report = app.crash_report
        if report is None:
            report = app._build_crash_report(exc)
        # Print to stderr (after Textual has restored the terminal).
        print(
            "\naws-tui crashed.\n"
            f"  {report.exception_type}: {report.exception_message}\n"
            f"  dump: {report.dump_path}\n",
            file=sys.stderr,
        )
        raise
    else:
        if launch is not None and app.launch_error is not None:
            print(f"aws-tui: {app.launch_error}", file=sys.stderr)
            raise SystemExit(1)
        # Normal exit; crash report would be set only if `_handle_exception`
        # fired and Textual swallowed the exception (it does this when
        # rendering a fatal panel).
        report = app.crash_report
        if report is not None:
            print(
                "\naws-tui crashed.\n"
                f"  {report.exception_type}: {report.exception_message}\n"
                f"  dump: {report.dump_path}\n",
                file=sys.stderr,
            )
            raise SystemExit(1)
    finally:
        if context.athena_drafts_shutdown_warning is not None:
            print(context.athena_drafts_shutdown_warning, file=sys.stderr)


if __name__ == "__main__":
    main()


__all__ = ["AwsTuiApp", "main"]
