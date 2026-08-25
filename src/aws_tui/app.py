"""Top-level Textual application — composes RootVM + chrome + content host.

This is the real composition that replaces the M0 hello-world placeholder.
The actual layer wiring lives in :mod:`aws_tui.composition` so this module
stays focused on the Textual side (compose, mounting, action handlers).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import mimetypes
import os
import sys
import weakref
from collections import deque
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from reactivex.abc import DisposableBase
from rich.markup import escape
from textual import events

if TYPE_CHECKING:
    from aws_tui.domain.filesystem import FileEntry, FileSystemProvider, PathRef
    from aws_tui.vm.file_manager.pane_vm import PaneVM

from textual.app import App, ComposeResult
from textual.binding import BindingsMap, BindingType
from textual.containers import Container, Horizontal
from textual.css.errors import StylesheetError
from textual.css.tokenizer import TokenError
from textual.widget import Widget
from textual.widgets import Static, TextArea

from aws_tui.composition import AppContext, build_app_context
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import EntryKind
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.infra.redaction import redact_text
from aws_tui.infra.theme_store import ThemeNotFound, ThemeStore
from aws_tui.ui import notifications
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.confirm_modal import TextualDialogService
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.quick_look import QuickLook
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.service_view_factory import build_service_view
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.version import __version__
from aws_tui.vm.athena.page_vm import AthenaPageSnapshot, AthenaPageVM
from aws_tui.vm.chrome.command_palette_vm import PaletteEntry
from aws_tui.vm.chrome.confirm_vm import ConfirmPath, ConfirmRequest
from aws_tui.vm.chrome.crash_vm import CrashChoice, CrashReport, CrashVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.chrome.quick_look_vm import QuickLookContent
from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM, FocusedPane
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM, GlueView
from aws_tui.vm.messages import (
    ConnectionListChangedMessage,
    CopyTableReferenceRequest,
    OpenAthenaTableRequest,
    OpenGlueTableRequest,
    OpenS3LocationRequest,
    PaletteActionFailedMessage,
    ThemeChangedMessage,
)
from aws_tui.vm.nav_menu_vm import SETTINGS_NAV_ID
from aws_tui.vm.service_source_vm import ServiceSourceContext

_ACTION_RING_SIZE = 100
_QUICK_LOOK_PREVIEW_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class _S3HandoffSnapshot:
    connection: Connection | None
    auth_state: TokenState | None
    service_id: str | None
    athena_result_execution_id: str | None


@dataclass(frozen=True, slots=True)
class _TableHandoffSnapshot:
    connection: Connection | None
    auth_state: TokenState | None
    service_id: str | None
    athena: AthenaPageSnapshot | None = field(repr=False)
    glue_view: str | None
    glue_database_name: str | None
    glue_table_ref: TableRef | None = field(repr=False)


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
_GLUE_SERVICE_IDS = frozenset({"glue"})
_ATHENA_SERVICE_IDS = frozenset({"athena"})
_EMR_SERVICE_IDS = frozenset({"emr-serverless"})

_PALETTE_COMMANDS: tuple[PaletteEntry, ...] = (
    PaletteEntry("app.themes", "Theme picker", "app"),
    PaletteEntry("app.cycle_theme", "Cycle theme", "app"),
    PaletteEntry(
        "app.swap_source",
        "Switch source",
        "source",
        service_ids=_SOURCE_SERVICE_IDS,
    ),
    PaletteEntry(
        "emr.next_application",
        "Next EMR application",
        "emr",
        service_ids=_EMR_SERVICE_IDS,
    ),
    PaletteEntry("glue.catalog", "Glue catalog", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("glue.jobs", "Glue jobs", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("glue.crawlers", "Glue crawlers", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry(
        "glue.choose_run_state",
        "Choose Glue run state",
        "glue",
        service_ids=_GLUE_SERVICE_IDS,
    ),
    PaletteEntry(
        "glue.choose_crawler_state",
        "Choose Glue crawler state",
        "glue",
        service_ids=_GLUE_SERVICE_IDS,
    ),
    PaletteEntry(
        "glue.copy_table_ref",
        "Copy Glue table reference",
        "glue",
        service_ids=_GLUE_SERVICE_IDS,
    ),
    PaletteEntry(
        "glue.open_s3_location",
        "Open table location in S3",
        "glue",
        service_ids=_GLUE_SERVICE_IDS,
    ),
    PaletteEntry(
        "glue.query_in_athena",
        "Query table in Athena",
        "glue",
        service_ids=_GLUE_SERVICE_IDS,
    ),
    PaletteEntry(
        "glue.time_travel_in_athena",
        "Query Iceberg snapshot in Athena",
        "glue",
        service_ids=_GLUE_SERVICE_IDS,
    ),
    PaletteEntry("athena.query", "Athena query", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.history", "Athena history", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.results", "Athena results", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.saved", "Athena saved queries", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry(
        "athena.choose_workgroup",
        "Choose Athena workgroup",
        "athena",
        service_ids=_ATHENA_SERVICE_IDS,
    ),
    PaletteEntry(
        "athena.choose_catalog",
        "Choose Athena catalog",
        "athena",
        service_ids=_ATHENA_SERVICE_IDS,
    ),
    PaletteEntry(
        "athena.choose_database",
        "Choose Athena database",
        "athena",
        service_ids=_ATHENA_SERVICE_IDS,
    ),
    PaletteEntry(
        "athena.insert_table_ref",
        "Insert copied table reference",
        "athena",
        service_ids=_ATHENA_SERVICE_IDS,
    ),
    PaletteEntry(
        "athena.execute", "Execute Athena query", "athena", service_ids=_ATHENA_SERVICE_IDS
    ),
    PaletteEntry("athena.cancel", "Cancel Athena query", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry(
        "athena.load_more", "Load more Athena rows", "athena", service_ids=_ATHENA_SERVICE_IDS
    ),
    PaletteEntry(
        "athena.open_result_location",
        "Open Athena result in S3",
        "athena",
        service_ids=_ATHENA_SERVICE_IDS,
    ),
    PaletteEntry(
        "athena.open_in_glue",
        "Open query table in Glue",
        "athena",
        service_ids=_ATHENA_SERVICE_IDS,
    ),
    PaletteEntry("app.open_settings", "Settings", "app"),
    PaletteEntry("app.help", "Help", "app"),
    PaletteEntry("app.quit", "Quit", "app"),
)


async def _first_bytes(source: AsyncIterator[bytes], limit: int) -> AsyncIterator[bytes]:
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
    async for chunk in _first_bytes(source, limit):
        yield chunk


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
    for conn in ctx.connection_resolver.list():
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
        for connection in ctx.connection_resolver.list()
        if connection.kind == "aws" and service.supports(connection)
    )


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


class AwsTuiApp(App[None]):
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
    CSS = """
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
    #content-host {
        height: 1fr;
        width: 1fr;
    }
    """

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
        self._actions.register("app.help", self.action_help)
        self._actions.register("app.themes", self.action_themes)
        self._actions.register("app.cycle_theme", self.action_cycle_theme)
        self._actions.register("app.open_settings", self.action_open_settings)
        self._actions.register("pane.copy", self.action_copy)
        self._actions.register("pane.delete", self.action_delete)
        self._actions.register("app.swap_source", self.action_swap_source)
        self._actions.register("emr.next_application", self.action_next_emr_application)
        self._actions.register(
            "glue.catalog",
            partial(self.action_select_glue_view, "catalog"),
        )
        self._actions.register(
            "glue.jobs",
            partial(self.action_select_glue_view, "jobs"),
        )
        self._actions.register(
            "glue.crawlers",
            partial(self.action_select_glue_view, "crawlers"),
        )
        self._actions.register(
            "glue.choose_run_state",
            self.action_choose_glue_run_state,
        )
        self._actions.register(
            "glue.choose_crawler_state",
            self.action_choose_glue_crawler_state,
        )
        self._actions.register(
            "glue.copy_table_ref",
            self.action_copy_glue_table_reference,
        )
        self._actions.register("glue.open_s3_location", self.action_open_glue_s3_location)
        self._actions.register("glue.query_in_athena", self.action_query_glue_table_in_athena)
        self._actions.register(
            "glue.time_travel_in_athena",
            self.action_time_travel_glue_table_in_athena,
        )
        self._actions.register(
            "athena.query",
            partial(self.action_select_athena_view, "query"),
        )
        self._actions.register(
            "athena.history",
            partial(self.action_select_athena_view, "history"),
        )
        self._actions.register(
            "athena.results",
            partial(self.action_select_athena_view, "results"),
        )
        self._actions.register(
            "athena.saved",
            partial(self.action_select_athena_view, "saved"),
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
        self._actions.register("athena.load_more", self.action_load_more_athena)
        self._actions.register(
            "athena.open_result_location",
            self.action_open_athena_result_location,
        )
        self._actions.register("athena.open_in_glue", self.action_open_athena_table_in_glue)
        self._actions.register("pane.mark_up", self.action_mark_up)
        self._actions.register("pane.mark_down", self.action_mark_down)
        self._actions.register("pane.quick_look", self.action_quick_look)
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
        self._content_mount_hosts: weakref.WeakKeyDictionary[Widget, Container] = (
            weakref.WeakKeyDictionary()
        )
        self._content_mount_recovering: weakref.WeakSet[Widget] = weakref.WeakSet()
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_complete = False
        self._command_palette_populated: bool = False
        self._pane_state_sub: DisposableBase | None = None
        self._connection_list_sub: DisposableBase | None = None
        self._nav_selection_sub: DisposableBase | None = None
        self._cursor_sub: DisposableBase | None = None
        self._service_navigation_sub: DisposableBase | None = None
        self._palette_failure_sub: DisposableBase | None = None
        self._table_clipboard_sub: DisposableBase | None = None
        self._service_navigation_closed = False
        self._table_navigation_generation = 0
        self._service_navigation_owner: tuple[str, int] | None = None
        self._table_navigation_lock = asyncio.Lock()
        self._service_navigation_lock = asyncio.Lock()
        self._table_navigation_tasks: set[asyncio.Task[None]] = set()
        self._table_handoff_rollbacks: set[asyncio.Task[bool]] = set()
        self._service_navigation_suppressed_selection: (
            tuple[asyncio.Task[object] | None, str] | None
        ) = None
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
        # Sticky for the session once the boot chain ran out of
        # candidates and we mounted local-on-both-panes. Subsequent
        # Settings → S3 toggles route through
        # ``_mount_local_only_dual_pane`` instead of rebuilding the
        # S3 DualPane (which would re-attempt the same failed
        # connections and force the user back into the same
        # 60s-then-error pane the chain already negotiated past).
        # The user can still press ``r`` on a pane or ``Shift+S``
        # to re-bind to a specific connection — those are the
        # explicit recovery paths.
        self._chain_resolved_to_local: bool = False
        self._chain_initial_conn: Connection | None = None
        self._pending_boot_nav_selection: str | None = None
        # Tracks the last frozenset of skipped connection names shown in a
        # skip-toast so repeated Shift+S presses don't stack duplicate toasts.
        self._last_skip_toast_set: frozenset[str] | None = None

    @property
    def app_ctx(self) -> AppContext:
        return self._app_ctx

    def compose(self) -> ComposeResult:
        # StatusBar is no longer mounted — profile/region/auth indicator
        # now live in the left pane's border (title shows the live path,
        # subtitle shows the connection identity). Bookkeeping VMs still
        # exist in RootVM.chrome so hub subscribers stay wired up; only
        # the widget is dropped.
        ctx = self._app_ctx
        yield BrandBanner(
            theme_name=ctx.initial_theme,
            hub=ctx.hub,
            demo=ctx.demo,
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
        yield TransfersOverlay(ctx.transfers_vm, hub=ctx.hub, id="transfers-overlay")

    async def on_mount(self) -> None:
        ctx = self._app_ctx
        # Construct the VM tree.
        ctx.root_vm.construct()
        ctx.transfers_vm.construct()
        ctx.confirm_vm.construct()
        ctx.quick_look_vm.construct()
        ctx.command_palette_vm.construct()
        ctx.s3_connections_vm.construct()
        # NOTE: SettingsVM is intentionally NOT constructed here. It's
        # now built fresh per mount in ``_mount_settings_view`` because
        # ContentHostVM disposes its hosted VM on swap-out, and a
        # singleton would crash on the second Settings switch with
        # ``StatusTransitionError('Cannot construct from state Disposed.')``.

        if not ctx.demo:
            _raise_config_risk_toasts(ctx)

        self._apply_initial_theme()

        # Subscribe to PaneVM state transitions BEFORE switch_service so the
        # initial UNREACHABLE state-change (if the connection is offline) is
        # not missed (Bug 2: initial mount UNREACHABLE silently missed).
        self._pane_state_sub = ctx.hub.messages.subscribe(on_next=self._on_hub_message_pane_state)
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
            await ctx.root_vm.switch_connection_with(initial_conn, auth_state)
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
            self.run_worker(
                self._initial_mount_worker(initial_conn=initial_conn, auth_state=auth_state),
                exclusive=True,
                group="content-mount",
            )
        else:
            await self._mount_no_connection_placeholder()

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
            self.call_after_refresh(lambda: self.set_focus(None))

    async def on_unmount(self) -> None:
        await self._aws_tui_shutdown()

    def _dispose_table_clipboard_subscription(self) -> None:
        subscription = self._table_clipboard_sub
        if subscription is None:
            return
        subscription.dispose()
        self._table_clipboard_sub = None

    async def _initial_mount_worker(
        self, *, initial_conn: Connection, auth_state: TokenState
    ) -> None:
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

        ``auth_state`` is kept as a parameter for back-compat
        (caller in ``on_mount`` already probed the initial
        connection); we re-probe each AWS candidate in the chain
        instead of relying on the value because the chain may
        include connections the caller never probed.
        """
        _ = auth_state  # caller probed `initial_conn`; chain re-probes each AWS candidate
        ctx = self._app_ctx
        try:
            chain = self._build_attempt_chain(initial_conn)
            ctx.log_sink.info(
                "app.boot_chain.start",
                length=len(chain),
                initial=initial_conn.name,
            )
            for index, conn in enumerate(chain, start=1):
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
                    outcome = await self._try_connection(conn)
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
            # Chain exhausted — mount local on both panes and
            # remember the decision so a subsequent Settings → S3
            # toggle reproduces the local-on-both state instead of
            # walking the chain again (which would just replay the
            # same failures the user already saw).
            self._raise_local_fallback_toast()
            self._chain_resolved_to_local = True
            self._chain_initial_conn = initial_conn
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
            selected = self._pending_boot_nav_selection
            self._pending_boot_nav_selection = None
            if selected is not None and ctx.root_vm.services_menu.selected_id == selected:
                if selected == SETTINGS_NAV_ID:
                    await self._mount_settings_view()
                else:
                    await self._mount_service_view(selected)

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

    async def _try_connection(self, conn: Connection) -> str:
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
                await ctx.root_vm.switch_connection_with(conn, TokenState.CONNECTED)
                await ctx.root_vm.switch_service("s3")
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
                return await asyncio.wait_for(self._attempt_future, timeout=90.0)
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
        so ``Shift+S`` skips it until the user runs
        ``aws sso login --profile X`` and relaunches (or until the
        ``r`` retry path clears the mark).
        """
        from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
        from aws_tui.vm.file_manager.pane_vm import PaneVM

        ctx = self._app_ctx
        await self._cancel_transfer_workers_before_content_swap()
        # Mark the connection unreachable so the swap-source ring
        # skips it (consistent with the reactive auto-fallback path).
        self._mark_connection_unreachable(initial_conn.kind, initial_conn.name)

        # Use the registered service's public local-provider factory so the
        # fallback keeps the same configured root as ordinary S3 views.
        from aws_tui.services.s3.service import S3Service

        s3_service = cast("S3Service", ctx.registry.get("s3"))

        def _make_local() -> FileSystemProvider:
            return self._make_local_provider()

        left = PaneVM(
            provider=_make_local(),
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            id_prefix="pane.local",
            identity_label="local",
            path_protocol="",
            connection_key=None,
        )
        right = PaneVM(
            provider=_make_local(),
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            id_prefix="pane.local",
            identity_label="local",
            path_protocol="",
            connection_key=None,
        )
        journal = s3_service.transfer_journal
        if journal is None:
            ctx.log_sink.error("app.local_only_mount.missing_journal")
            return False
        dual = DualPaneVM(
            left=left,
            right=right,
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            transfer_journal=journal,
        )
        try:
            # ContentHostVM.set_content is idempotent on a matching
            # ``service_id`` — it returns early if ``current_id`` is
            # already the same. When the boot chain falls back to
            # local-on-both AFTER an s3-compatible attempt failed,
            # ``current_id`` is already "s3" (the failed attempt's
            # service id) and the idempotent check would silently skip
            # adopting the new local DualPaneVM, leaving the host
            # bound to the failed S3FS panes. User-visible symptom:
            # "upon launching the app, neither pane shows any content
            # when neither aws s3 nor s3-compatible are available.
            # Only when I browse the settings and come back to S3, it
            # shows the local source." (The toggle path worked because
            # ``current_id`` flipped to ``"settings"`` in between,
            # invalidating the idempotent skip.) Force the swap by
            # clearing first.
            await ctx.root_vm.content_host.set_content(None, service_id=None)
            await ctx.root_vm.content_host.set_content(dual, service_id="s3")
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
                f"refresh with [b]aws sso login --profile {profile}[/] and relaunch",
            ),
            "aws-no-creds": (
                f"no session for [b]{profile}[/], both panes set to local",
                f"run [b]aws sso login --profile {profile}[/] (or set $AWS_PROFILE) and relaunch",
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
        4. ``None`` — the no-connection placeholder branch.
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
        # no-connection placeholder branch and the user gets a usable UI
        # + the ``app.config_load.failed`` log line for diagnostics.
        try:
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
                    dual_pane_class=DualPane,
                    emr_page_class=EmrServerlessPage,
                    glue_page_class=GluePage,
                    athena_page_class=AthenaPage,
                )
                await self._replace_content_widget(host, replacement)
                if _svc_id in {"glue", "athena"}:
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
        """Render a clear "configure one and relaunch" message when no
        AWS / S3-compatible connection resolves at startup.
        """
        ctx = self._app_ctx
        config_path = escape(str(ctx.config_store.path))
        host = self.query_one("#content-host", Container)
        await self._replace_content_widget(
            host,
            Static(
                "\n  No AWS profile or S3-compatible connection found.\n\n"
                "  To get started, do ONE of the following and relaunch:\n\n"
                "    1. Run [b]aws configure[/]                      (interactive AWS keys setup)\n"
                "    2. Run [b]aws configure sso[/]                  (interactive SSO setup)\n"
                f"    3. Edit [b]{config_path}[/]     (add an AWS or S3-compatible connection)\n\n"
                "  See [b]docs/connections.md[/] in the repo for the [b][connections.<name>][/] schema and\n"
                "  vendor quirks (MinIO, R2, B2, Wasabi).\n\n"
                "  Press [b]q[/] to quit.",
                id="content-placeholder",
                classes="content-placeholder",
                markup=True,
            ),
        )

    async def _cancel_transfer_workers_before_content_swap(self) -> None:
        """Stop copy/delete workers before disposing the active file panes."""
        from textual.worker import WorkerCancelled

        workers = self.workers.cancel_group(self, "transfer-ops")
        if workers:
            with contextlib.suppress(WorkerCancelled):
                await self.workers.wait_for_complete(workers)

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
        disposal all leaked at exit. The async coroutine
        :meth:`action_app_quit` was wired but never invoked because no
        binding routed to it.
        """
        self.record_action("app.quit")
        await self._aws_tui_shutdown()
        self.exit()

    # BindingResolver maps user-defined ``[keybindings].app.quit`` entries
    # through this alias so every route reaches the same shutdown path.
    async def action_app_quit(self) -> None:
        await self.action_quit()

    def _handle_quit(self) -> None:
        # Synchronous fallback for the BindingResolver bridge that
        # cannot await. Schedule the async path on the event loop
        # so cleanup still runs instead of being silently dropped.
        self.run_worker(self.action_quit(), exclusive=True, group="shutdown")

    def action_dispatch(self, action_id: str) -> Awaitable[None] | None:
        """Single Textual action behind every resolver-materialized binding.

        Each installed ``Binding`` uses ``dispatch('<action_id>')``; Textual
        calls this method, which forwards to the :class:`ActionRegistry` that
        holds the real handler. Returning the handler's awaitable (if any)
        lets Textual await async actions.
        """
        return self._actions.invoke(action_id)

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

    def action_quick_look(self) -> None:
        """Open a 64 KB Quick Look preview for the focused pane's cursor file.

        Bound to ``Space`` via ``pane.quick_look``. No-op unless a file sits
        under the cursor — directories, the ``..`` parent link, and an empty
        pane are ignored.
        """
        self.record_action("pane.quick_look")
        glue_page = self._glue_page()
        if glue_page is not None and glue_page.activate_focused(space=True):
            return
        emr_page = self._emr_page()
        if emr_page is not None and emr_page.activate_focused():
            return
        pane = self._focused_file_pane()
        if pane is None:
            return
        entry_vm = pane.selected_entry
        if entry_vm is None or entry_vm.kind is not EntryKind.FILE:
            return
        entry = entry_vm.entry
        content = _build_quick_look_content(entry, pane.provider, path=pane.path.join(entry.name))
        self._app_ctx.quick_look_vm.open_command.execute(content)
        self.push_screen(QuickLook(self._app_ctx.quick_look_vm, hub=self._app_ctx.hub))

    def _populate_command_palette(self) -> None:
        """Register the curated app commands into the palette (idempotent).

        Each entry's action dispatches through the ActionRegistry, so selecting
        a command is identical to pressing its key. ``register_entry`` replaces
        by id, so re-running is a no-op; the flag just avoids redundant work.
        """
        if self._command_palette_populated:
            return
        vm = self._app_ctx.command_palette_vm
        for entry in _PALETTE_COMMANDS:
            vm.register_entry(
                entry,
                partial(self._actions.invoke, entry.id),
            )
        self._command_palette_populated = True

    def action_command_palette(self) -> None:
        """Open the fuzzy command palette (bound to ``:`` / ``Ctrl+K``)."""
        self.record_action("app.command_palette")
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
        self._cycle_focus(reverse=False)

    def action_switch_focus_reverse(self) -> None:
        """Move to the previous app-wide focus slot."""
        self.record_action("pane.switch_focus_back")
        self._cycle_focus(reverse=True)

    def _cycle_focus(self, *, reverse: bool) -> None:
        # EMR page owns its own 4-slot Tab cycle.
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
        if self._dual_pane() is None:
            coordinator.cycle_settings_focus(reverse=reverse)
        else:
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

    def _forward_to_modal(self, *action_names: str) -> bool:
        """When a modal is active, try each ``action_name`` on the active
        screen and run the first that exists. Used to work around
        Textual dispatching App-level priority bindings BEFORE modal
        ones — without forwarding, things like ↑/↓/Enter in our modals
        would never reach the modal's own handlers."""
        if len(self.screen_stack) <= 1:
            return False
        for name in action_names:
            forward = getattr(self.screen, name, None)
            if forward is not None:
                forward()
                return True
        return False

    def action_move_up(self) -> None:
        self.record_action("pane.move_up")
        if self._forward_to_modal("action_move_up"):
            return
        self._move_cursor(-1)

    def action_move_down(self) -> None:
        self.record_action("pane.move_down")
        if self._forward_to_modal("action_move_down"):
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
                focused.insert("\n")
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
        if pane is None:
            return
        target = pane.selected_entry
        if target is None:
            return
        # ".." synthetic entry — ascend to parent.
        if target.entry.name == "..":
            if not pane.path.is_root:
                await pane.navigate_to(pane.path.parent())
            return
        # Descend only into directories; files trigger Quick Look later.
        if str(target.entry.kind) == "directory":
            await pane.navigate_to(pane.path.join(target.entry.name))

    async def action_ascend(self) -> None:
        self.record_action("pane.ascend")
        # Forward Backspace to the active modal as a cancel-by-key
        # gesture (esc still works too).
        if self._forward_to_modal("action_cancel", "action_close", "action_dismiss"):
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
        await pane.navigate_to(pane.path.parent())

    async def action_modal_left_or_ascend(self) -> None:
        """In modals, move focus to the previous button; in panes, ascend to parent."""
        self.record_action("pane.ascend")
        # In a modal: Left moves arrow-key focus to the previous footer
        # button (or whatever the modal exposes as ``action_focus_prev``).
        # Outside any modal: behaves like ``ascend`` so file-pane
        # navigation is unchanged.
        if self._forward_to_modal("action_focus_prev"):
            return
        await self.action_ascend()

    def action_modal_right(self) -> None:
        """In modals, move focus to the next button. No-op in panes."""
        self.record_action("modal.focus_next")
        # In a modal: Right moves arrow-key focus to the next footer
        # button. Outside any modal: no-op (panes don't currently bind
        # Right to anything).
        self._forward_to_modal("action_focus_next")

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
            await pane.refresh()

    async def action_help(self) -> None:
        """Show the help overlay (also bound to ``:``). The theme picker
        is a separate modal — press ``t`` (or use the help modal's
        Themes link)."""
        self.record_action("app.help")
        await self.push_screen(HelpModal())

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
        src_pane = getattr(dual, "focused_pane", None)
        dst_pane = getattr(dual, "other_pane", None)
        if src_pane is None or dst_pane is None:
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
            self._confirm_copy(dual, list(targets), used_cursor_fallback, request),
            group="confirmation",
        )

    async def _confirm_copy(
        self,
        dual: object,
        targets: list[object],
        used_cursor_fallback: bool,
        request: ConfirmRequest,
    ) -> None:
        ctx = self._app_ctx
        try:
            dialogs = TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub)
            if not await ctx.confirm_vm.ask(request, dialog_service=dialogs):
                return
            self.run_worker(
                self._run_copy(dual, targets, used_cursor_fallback),
                exclusive=True,
                group="transfer-ops",
            )
        finally:
            self._confirmation_pending = False

    async def _run_copy(
        self,
        dual: object,
        targets: list[object],
        used_cursor_fallback: bool,
    ) -> None:
        """Run ``DualPaneVM.copy_across`` from a worker. Errors are
        toasted, never re-raised."""
        ctx = self._app_ctx
        copy_across = getattr(dual, "copy_across", None)
        if copy_across is None:
            return
        if used_cursor_fallback:
            for entry in targets:
                entry.set_marked(True)  # type: ignore[attr-defined]
        try:
            await copy_across()
        except Exception as exc:
            ctx.log_sink.error(
                "app.copy.failed",
                error=str(exc),
                error_type=type(exc).__name__,
                file_count=len(targets),
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
            if used_cursor_fallback:
                for entry in targets:
                    entry.set_marked(False)  # type: ignore[attr-defined]

    async def action_delete(self) -> None:
        """Delete the focused pane's marked entries (or the cursor row if
        none are marked). Pops a danger-styled confirm modal first."""
        self.record_action("pane.delete")
        dual = self._dual_pane()
        if dual is None:
            return
        src_pane = getattr(dual, "focused_pane", None)
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
            self._confirm_delete(dual, list(targets), used_cursor_fallback, request),
            group="confirmation",
        )

    async def _confirm_delete(
        self,
        dual: object,
        targets: list[object],
        used_cursor_fallback: bool,
        request: ConfirmRequest,
    ) -> None:
        ctx = self._app_ctx
        try:
            dialogs = TextualDialogService(self, ctx.confirm_vm, hub=ctx.hub)
            if not await ctx.confirm_vm.ask(request, dialog_service=dialogs):
                return
            self.run_worker(
                self._run_delete(dual, targets, used_cursor_fallback),
                exclusive=True,
                group="transfer-ops",
            )
        finally:
            self._confirmation_pending = False

    async def _run_delete(
        self,
        dual: object,
        targets: list[object],
        used_cursor_fallback: bool,
    ) -> None:
        """Mirror of :meth:`_run_copy` for the delete path."""
        ctx = self._app_ctx
        delete_in_focused = getattr(dual, "delete_in_focused", None)
        if delete_in_focused is None:
            return
        if used_cursor_fallback:
            for entry in targets:
                entry.set_marked(True)  # type: ignore[attr-defined]
        try:
            await delete_in_focused()
        except Exception as exc:
            ctx.log_sink.error(
                "app.delete.failed",
                error=str(exc),
                error_type=type(exc).__name__,
                file_count=len(targets),
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
            if used_cursor_fallback:
                for entry in targets:
                    entry.set_marked(False)  # type: ignore[attr-defined]

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
        dual = self._dual_pane()
        if dual is None:
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
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

    async def action_swap_source(self) -> None:
        """Cycle the current service's source.

        S3 retains independent source rings for its two panes. Other AWS
        services rebuild under the next supported AWS connection.
        """
        self.record_action("app.swap_source")
        generation = self._supersede_table_navigation()
        async with self._service_navigation_lock:
            if not self._service_navigation_is_owned_by("external", generation):
                return
            await self._swap_source_transaction()

    async def on_service_source_header_source_selected(
        self,
        event: ServiceSourceHeader.SourceSelected,
    ) -> None:
        """Switch to the exact source committed by a Glue/Athena picker."""
        event.stop()
        service_id = self._app_ctx.root_vm.services_menu.selected_id
        if service_id is None or service_id == SETTINGS_NAV_ID:
            return
        self.record_action("app.swap_source")
        generation = self._supersede_table_navigation()
        async with self._service_navigation_lock:
            if not self._service_navigation_is_owned_by("external", generation):
                return
            accepted = await self._switch_single_context_source_to(
                service_id,
                event.connection_name,
                event.region,
            )
            if not accepted:
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
            new_provider = self._make_s3_provider_for_connection(conn)
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
        await swap(
            new_provider,
            identity_label=next_label,
            path_protocol=new_protocol,
            connection_key=new_conn_key,
        )

    async def action_next_emr_application(self) -> None:
        self.record_action("emr.next_application")
        page = self._emr_page()
        if page is not None:
            await page.vm.cycle_application(1)

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

    async def action_load_more_athena(self) -> None:
        self.record_action("athena.load_more")
        page = self._athena_page()
        if page is not None:
            await page.action_load_more()

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

    def _bindings_overlap(self, first: str, second: str) -> bool:
        keymap = self._app_ctx.keymap_store
        return bool(set(keymap.resolve(first)) & set(keymap.resolve(second)))

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
        if page is not None and page.vm.query_in_athena():
            return
        notifications.advise(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="select a Glue table to query in Athena",
            toast_id="glue-athena-table-unavailable",
        )

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
    ) -> bool:
        """Rebuild a non-S3 service under one explicit supported source."""
        ctx = self._app_ctx
        target = next(
            (
                connection
                for connection in _service_source_candidates(ctx, service_id)
                if (connection.name, connection.region) == (connection_name, region)
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
        active = ctx.root_vm.active_connection
        if active is not None and (active.name, active.region) == (
            target.name,
            target.region,
        ):
            return True
        return await self._rebuild_single_context_source(service_id, target)

    async def _rebuild_single_context_source(
        self,
        service_id: str,
        target: Connection,
    ) -> bool:
        """Rebuild under ``target`` and restore the prior source on failure."""

        ctx = self._app_ctx
        prior_connection = ctx.root_vm.active_connection
        prior_auth_state = ctx.root_vm.active_auth_state
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
            await ctx.root_vm.switch_connection_and_service(target, auth_state, service_id)
            if await self._mount_service_view(service_id, required_connection=target):
                return True
        except Exception as exc:
            ctx.log_sink.error(
                "service_source.rebuild_failed",
                service_id=service_id,
                connection=target.name,
                error_type=type(exc).__name__,
            )

        restored = False
        if prior_connection is not None and prior_auth_state is not None:
            try:
                await ctx.root_vm.switch_connection_and_service(
                    prior_connection,
                    prior_auth_state,
                    service_id,
                )
                restored = await self._mount_service_view(
                    service_id,
                    required_connection=prior_connection,
                )
            except Exception as exc:
                ctx.log_sink.error(
                    "service_source.rollback_failed",
                    service_id=service_id,
                    connection=prior_connection.name,
                    error_type=type(exc).__name__,
                )
        notifications.error(
            ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message=(
                f"couldn't switch to {target.name}; "
                + ("restored the previous source" if restored else "source restore failed")
            ),
        )
        return False

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
        await swap(
            self._make_local_provider(),
            identity_label="local",
            path_protocol="",
            connection_key=None,
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
            self._chain_resolved_to_local = False

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
        self.run_worker(
            self._reload_panes_for(msg.names, deleted=(msg.change == "deleted")),
            exclusive=True,
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
                except Exception:
                    await self._rebind_pane_to_local(pane)
                else:
                    await self._rebind_pane_to_connection(pane, conn)

    def _on_service_navigation_message(self, msg: object) -> None:
        if self._service_navigation_closed:
            return
        if isinstance(msg, OpenS3LocationRequest):
            generation = self._advance_service_navigation(
                "external",
                cancel_table_tasks=True,
            )
            self.run_worker(
                self._open_s3_location_request(msg, generation),
                exclusive=True,
                group="content-mount",
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
            self._table_navigation_tasks.add(navigation)
            navigation.add_done_callback(self._table_navigation_tasks.discard)
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
        try:
            self.copy_to_clipboard(copied.sql_identifier)
        except Exception as exc:
            self._app_ctx.log_sink.warning(
                "table_clipboard.system_copy_unavailable",
                error_type=type(exc).__name__,
            )
        notifications.success(
            self._app_ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="copied table reference",
            toast_id="glue-table-reference-copied",
        )

    def _advance_service_navigation(
        self,
        owner: str,
        *,
        cancel_table_tasks: bool = False,
    ) -> int:
        self._table_navigation_generation += 1
        generation = self._table_navigation_generation
        self._service_navigation_owner = (owner, generation)
        if cancel_table_tasks:
            current = asyncio.current_task()
            for task in tuple(self._table_navigation_tasks):
                if task is not current and not task.done():
                    task.cancel()
        return generation

    def _supersede_table_navigation(self) -> int:
        return self._advance_service_navigation(
            "external",
            cancel_table_tasks=True,
        )

    def _service_navigation_is_owned_by(
        self,
        owner: str,
        generation: int,
    ) -> bool:
        return self._service_navigation_owner == (owner, generation)

    def _table_handoff_should_restore(self, generation: int) -> bool:
        owner = self._service_navigation_owner
        return generation <= self._table_navigation_generation and (
            owner is None or owner[0] == "table"
        )

    async def _open_table_request(
        self,
        request: OpenAthenaTableRequest | OpenGlueTableRequest,
        generation: int,
    ) -> None:
        """Switch, mount, and select one exact Glue/Athena table."""
        async with self._table_navigation_lock:
            if generation != self._table_navigation_generation:
                return
            self._service_navigation_owner = ("table", generation)
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
        mutation_started = False
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
            if generation != self._table_navigation_generation:
                return

            suppression = (asyncio.current_task(), destination)
            self._service_navigation_suppressed_selection = suppression
            try:
                mutation_started = True
                await ctx.root_vm.switch_connection_and_service(
                    connection,
                    auth_state,
                    destination,
                )
            finally:
                if self._service_navigation_suppressed_selection is suppression:
                    self._service_navigation_suppressed_selection = None
            if await self._restore_superseded_table_handoff(generation, snapshot):
                return
            if not await self._mount_service_view(
                destination,
                required_connection=connection,
            ):
                raise RuntimeError("destination mount failed")
            await self._wait_for_current_service_setup()
            if await self._restore_superseded_table_handoff(generation, snapshot):
                return

            target = ctx.root_vm.content_host.current
            if isinstance(request, OpenAthenaTableRequest):
                if not isinstance(target, AthenaPageVM):
                    raise RuntimeError("Athena destination is unavailable")
                await target.open_table(ref, request.snapshot_id)
            else:
                if not isinstance(target, GluePageVM):
                    raise RuntimeError("Glue destination is unavailable")
                await target.open_table(ref)
            await self.wait_for_refresh()
            await self._restore_superseded_table_handoff(generation, snapshot)
        except asyncio.CancelledError:
            if mutation_started and self._table_handoff_should_restore(generation):
                await self._restore_table_handoff_durably(
                    snapshot,
                    generation,
                )
            raise
        except Exception as exc:
            rollback_cancelled = False
            if mutation_started and self._table_handoff_should_restore(generation):
                _, rollback_cancelled = await self._restore_table_handoff_durably(
                    snapshot,
                    generation,
                )
            if rollback_cancelled:
                raise asyncio.CancelledError from None
            if generation != self._table_navigation_generation:
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

    def _capture_table_handoff_snapshot(self) -> _TableHandoffSnapshot:
        ctx = self._app_ctx
        current = ctx.root_vm.content_host.current
        glue_table_ref: TableRef | None = None
        if isinstance(current, GluePageVM):
            selected_table_name = current.catalog.selected_table_name
            selected = next(
                (
                    row
                    for row in current.catalog.tables
                    if row.ref.table_name == selected_table_name
                ),
                None,
            )
            glue_table_ref = selected.ref if selected is not None else None
        return _TableHandoffSnapshot(
            connection=ctx.root_vm.active_connection,
            auth_state=ctx.root_vm.active_auth_state,
            service_id=ctx.root_vm.content_host.current_id,
            athena=current.export_snapshot() if isinstance(current, AthenaPageVM) else None,
            glue_view=current.active_view if isinstance(current, GluePageVM) else None,
            glue_database_name=(
                current.catalog.selected_database_name if isinstance(current, GluePageVM) else None
            ),
            glue_table_ref=glue_table_ref,
        )

    async def _restore_superseded_table_handoff(
        self,
        generation: int,
        snapshot: _TableHandoffSnapshot,
    ) -> bool:
        if generation == self._table_navigation_generation:
            return False
        if self._table_handoff_should_restore(generation):
            _, cancelled = await self._restore_table_handoff_durably(
                snapshot,
                generation,
            )
            if cancelled:
                raise asyncio.CancelledError
        return True

    async def _restore_table_handoff_durably(
        self,
        snapshot: _TableHandoffSnapshot,
        generation: int,
    ) -> tuple[bool, bool]:
        rollback = asyncio.create_task(
            self._restore_table_handoff(snapshot, generation),
            name="table-handoff-rollback",
        )
        self._table_handoff_rollbacks.add(rollback)
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
                    error_type=type(exc).__name__,
                )
                return False, cancelled
        finally:
            self._table_handoff_rollbacks.discard(rollback)

    async def _wait_for_current_service_setup(self) -> None:
        task = self._app_ctx.root_vm.content_host._setup_task
        if task is not None and not task.done():
            await task
        await self.wait_for_refresh()

    async def _restore_table_handoff(
        self,
        snapshot: _TableHandoffSnapshot,
        generation: int,
    ) -> bool:
        async with self._service_navigation_lock:
            return await self._restore_table_handoff_transaction(
                snapshot,
                generation,
            )

    async def _restore_table_handoff_transaction(
        self,
        snapshot: _TableHandoffSnapshot,
        generation: int,
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
        suppression = (asyncio.current_task(), snapshot.service_id)
        self._service_navigation_suppressed_selection = suppression
        try:
            await self._app_ctx.root_vm.switch_connection_and_service(
                snapshot.connection,
                snapshot.auth_state,
                snapshot.service_id,
            )
        except Exception as exc:
            self._app_ctx.log_sink.error(
                "service_navigation.table_rollback_failed",
                connection=snapshot.connection.name,
                service_id=snapshot.service_id,
                stage="switch",
                error_type=type(exc).__name__,
            )
            return False
        finally:
            if self._service_navigation_suppressed_selection is suppression:
                self._service_navigation_suppressed_selection = None
        if not self._table_handoff_should_restore(generation):
            return False
        if not await self._mount_service_view(
            snapshot.service_id,
            required_connection=snapshot.connection,
        ):
            return False
        await self._wait_for_current_service_setup()
        if not self._table_handoff_should_restore(generation):
            return False
        current = self._app_ctx.root_vm.content_host.current
        if isinstance(current, AthenaPageVM):
            if snapshot.athena is not None:
                await current.restore_snapshot(snapshot.athena)
        elif isinstance(current, GluePageVM):
            if snapshot.glue_table_ref is not None:
                await current.open_table(snapshot.glue_table_ref)
            elif snapshot.glue_database_name is not None:
                await current.select_database(snapshot.glue_database_name)
            if snapshot.glue_view is not None:
                await current.select_view(cast("GlueView", snapshot.glue_view))
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
        if self._service_navigation_closed:
            return
        if generation is None:
            generation = self._supersede_table_navigation()
        async with self._service_navigation_lock:
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
        snapshot = _S3HandoffSnapshot(
            connection=ctx.root_vm.active_connection,
            auth_state=ctx.root_vm.active_auth_state,
            service_id=ctx.root_vm.content_host.current_id,
            athena_result_execution_id=(
                current.results.execution_id
                if isinstance(current, AthenaPageVM) and current.active_view == "results"
                else None
            ),
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
            suppression = (asyncio.current_task(), "s3")
            self._service_navigation_suppressed_selection = suppression
            try:
                await ctx.root_vm.switch_connection_and_service(
                    connection,
                    auth_state,
                    "s3",
                )
            finally:
                if self._service_navigation_suppressed_selection is suppression:
                    self._service_navigation_suppressed_selection = None

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
            ctx.focus_coordinator.set_focused_slot(slot)
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
                athena_result_execution_id=snapshot.athena_result_execution_id,
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
        athena_result_execution_id: str | None = None,
    ) -> bool:
        """Restore the coherent service snapshot captured before a handoff."""
        if connection is None or auth_state is None or service_id is None:
            self._app_ctx.log_sink.error(
                "service_navigation.s3_rollback_failed",
                stage="missing_snapshot",
            )
            return False

        suppression = (asyncio.current_task(), service_id)
        self._service_navigation_suppressed_selection = suppression
        try:
            with contextlib.suppress(Exception):
                host = self.query_one("#content-host", Container)
                await host.remove_children()
            await self._app_ctx.root_vm.switch_connection_and_service(
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
                error_type=type(exc).__name__,
            )
            return False
        finally:
            if self._service_navigation_suppressed_selection is suppression:
                self._service_navigation_suppressed_selection = None

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
        if (
            service_id == "athena"
            and athena_result_execution_id is not None
            and isinstance(current, AthenaPageVM)
        ):
            await current.results.load(athena_result_execution_id)
            await current.select_view("results")
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
        navigation-only). EMR panes are out of scope; their cursor
        target is a job-run, all chips stay enabled.
        """
        from vmx import PropertyChangedMessage

        from aws_tui.vm.file_manager.pane_vm import PaneVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        glue_page = self._glue_page()
        if glue_page is not None and msg.sender_object in {
            glue_page.vm,
            glue_page.vm.catalog,
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
        """Push a fresh disabled-action set to the HintLegendVM based
        on the focused pane's current cursor target. Safe to call at
        any time — no-ops on EMR / Settings (no DualPaneVM mounted).
        """
        athena_page = self._athena_page()
        if athena_page is not None:
            disabled: set[str] = set()
            query = athena_page.vm.query
            if not query.execute_command.can_execute():
                disabled.add("athena.execute")
            if not query.cancel_command.can_execute():
                disabled.add("athena.cancel")
            if not athena_page.can_load_more():
                disabled.add("athena.load_more")
            copied = self._app_ctx.table_clipboard_vm.copied_table
            active_source = (
                athena_page.vm.context.connection_name,
                athena_page.vm.context.region,
            )
            if (
                copied is None
                or (
                    copied.table_ref.connection_name,
                    copied.table_ref.region,
                )
                != active_source
            ):
                disabled.add("athena.insert_table_ref")
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(frozenset(disabled))
            return
        glue_page = self._glue_page()
        if glue_page is not None:
            glue_disabled = (
                frozenset()
                if glue_page.vm.can_copy_table_reference
                else frozenset({"glue.copy_table_ref"})
            )
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(glue_disabled)
            return
        dual = self._dual_pane()
        if dual is None:
            # No file-pane context — leave whatever the EMR / Settings
            # service set is the source of truth. Don't add disables.
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(frozenset())
            return
        pane = getattr(dual, "focused_pane", None)
        if pane is None:
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(frozenset())
            return
        cursor_idx = getattr(pane, "cursor_index", 0)
        entries = getattr(pane, "filtered_entries", ()) or getattr(pane, "entries", ())
        target = entries[cursor_idx] if 0 <= cursor_idx < len(entries) else None
        target_name = getattr(getattr(target, "entry", target), "name", None)
        if target_name == "..":
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(
                frozenset({"pane.copy", "pane.delete"})
            )
        else:
            self._app_ctx.root_vm.chrome.hint_legend.set_disabled_actions(frozenset())

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
        if self._athena_page() is not None:
            self._recompute_hint_disables()

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
        suppression = self._service_navigation_suppressed_selection
        if (
            suppression is not None
            and suppression[0] is asyncio.current_task()
            and suppression[1] == selected
        ):
            self._service_navigation_suppressed_selection = None
            return
        generation = self._supersede_table_navigation()
        # Skip the seed selected_id change that on_mount fires while
        # priming the initial service. on_mount drives that mount
        # synchronously via _mount_initial_service_view; if we ALSO
        # spawn a mount worker here, the two race against the same
        # #content-host and one silently clobbers the other → blank
        # screen at startup.
        if self._boot_in_flight:
            if selected != "s3":
                self._pending_boot_nav_selection = None
                self._boot_in_flight = False
                self.workers.cancel_group(self, "content-mount")
                self.run_worker(
                    self._mount_external_navigation(selected, generation),
                    exclusive=True,
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
            self.run_worker(
                self._mount_external_navigation(selected, generation),
                exclusive=True,
                group="content-mount",
            )
        else:
            # Re-use the S3 content if it's already hosted; switch_service
            # is idempotent on the same service_id.
            self.run_worker(
                self._mount_external_navigation(selected, generation),
                exclusive=True,
                group="content-mount",
            )

    async def _mount_external_navigation(
        self,
        selected: str,
        generation: int,
    ) -> None:
        async with self._service_navigation_lock:
            if self._service_navigation_closed or not self._service_navigation_is_owned_by(
                "external", generation
            ):
                return
            menu = self._app_ctx.root_vm.services_menu
            if menu.selected_id != selected:
                suppression = (asyncio.current_task(), selected)
                self._service_navigation_suppressed_selection = suppression
                try:
                    menu.switch_service_command.execute(selected)
                finally:
                    if self._service_navigation_suppressed_selection is suppression:
                        self._service_navigation_suppressed_selection = None
            if selected == SETTINGS_NAV_ID:
                await self._mount_settings_view()
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
        settings_vm = SettingsVM(s3=ctx.s3_connections_vm, hub=ctx.hub, dispatcher=ctx.dispatcher)
        try:
            host = self.query_one("#content-host", Container)
            await ctx.root_vm.content_host.set_content(settings_vm, service_id=SETTINGS_NAV_ID)
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

    async def _mount_service_view(
        self,
        service_id: str,
        *,
        required_connection: Connection | None = None,
    ) -> bool:
        """Swap the content host to show the DualPane for ``service_id``.

        Always runs the full ``switch_service`` + ``remove_children``
        + ``mount`` sequence. An earlier ``current_id == service_id``
        early-return shortcut was meant to skip a no-op rebuild on a
        same-service reselect, but it raced the Settings ↔ S3 toggle:
        if the user clicked S3 → Settings → S3 quickly enough, the
        Settings worker could be cancelled mid-``set_content`` before
        ``current_id`` flipped to ``"settings"``, leaving
        ``current_id == "s3"`` while the DOM held a partially-mounted
        ``SettingsView``. The next ``_mount_service_view("s3")`` would
        then hit the shortcut, see ``current_id == "s3"``, check
        ``host.query(DualPane)`` (which returned nothing because
        SettingsView was in the DOM), and re-mount using whatever
        ``content_host.current`` was — possibly the disposed DualPaneVM
        from the original mount, possibly the SettingsVM, possibly
        nothing — and the user saw "S3 selected but Settings still on
        screen". ``switch_service`` is idempotent on a matching
        ``service_id``, so always running it is cheap; always tearing
        the host children down and re-mounting forces the DOM into
        agreement with the VM regardless of which intermediate state
        a cancelled worker may have left behind.
        """
        ctx = self._app_ctx
        await self._cancel_transfer_workers_before_content_swap()
        if required_connection is not None and ctx.root_vm.active_connection != required_connection:
            active = ctx.root_vm.active_connection
            ctx.log_sink.error(
                "app.mount_service_view.connection_mismatch",
                service_id=service_id,
                required_connection=required_connection.name,
                active_connection=active.name if active is not None else None,
            )
            return False
        # Explicit S3 selection after boot-chain local fallback is a
        # retry. Earlier builds made the fallback sticky for the whole
        # session, which left users unable to recover from Settings
        # after fixing credentials. Retry the remembered initial
        # connection once; if it still fails, remount local-on-both so
        # the content host never stays stranded on the previous screen.
        if (
            service_id == "s3"
            and required_connection is None
            and self._chain_resolved_to_local
            and self._chain_initial_conn is not None
        ):
            retry_conn = self._chain_initial_conn
            self._chain_resolved_to_local = False
            ctx.unreachable_connections.discard((retry_conn.kind, retry_conn.name))
            self._last_skip_toast_set = None
            outcome = await self._try_connection(retry_conn)
            if outcome == "ok":
                self._chain_initial_conn = None
                return True
            self._mark_connection_unreachable(retry_conn.kind, retry_conn.name)
            self._raise_failure_toast(retry_conn, outcome)
            self._raise_local_fallback_toast()
            self._chain_resolved_to_local = True
            self._chain_initial_conn = retry_conn
            return await self._mount_local_only_dual_pane(
                initial_conn=retry_conn,
                reason="chain-exhausted",
            )
        try:
            host = self.query_one("#content-host", Container)
            await ctx.root_vm.switch_service(service_id)
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_service_view.switch_service_failed",
                service_id=service_id,
                error_type=type(exc).__name__,
            )
            return False
        try:
            current_vm = ctx.root_vm.content_host.current
            if current_vm is None:
                ctx.log_sink.error(
                    "app.mount_service_view.missing_view_model",
                    service_id=service_id,
                )
                return False
            replacement = build_service_view(
                service_id,
                current_vm,
                hub=ctx.hub,
                keymap=ctx.keymap_store,
                source_candidates=_service_source_contexts(ctx, service_id),
                focus_coordinator=ctx.focus_coordinator,
                dual_pane_class=DualPane,
                emr_page_class=EmrServerlessPage,
                glue_page_class=GluePage,
                athena_page_class=AthenaPage,
            )
            await self._replace_content_widget(host, replacement)
            if service_id in {"glue", "athena"}:
                self._recompute_hint_disables()
        except Exception as exc:
            ctx.log_sink.error(
                "app.mount_service_view.mount_failed",
                service_id=service_id,
                error=str(exc),
                error_type=type(exc).__name__,
                notes=getattr(exc, "__notes__", ()),
            )
            return False
        return True

    async def _replace_content_widget(self, host: Container, replacement: Widget) -> None:
        """Mount a content replacement or leave a coherent error surface."""
        if any(candidate is host for candidate in self._content_mount_hosts.values()):
            host = await self._reset_content_host(host)
        await host.remove_children()
        self._content_mount_hosts[replacement] = host
        try:
            await host.mount(replacement)
            self.call_after_refresh(lambda: self._expire_content_mount_registration(replacement))
        except Exception as exc:
            self._content_mount_hosts.pop(replacement, None)
            try:
                host = await self._reset_content_host(host)
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
        await host.remove()
        replacement = Container(id="content-host")
        await parent.mount(replacement, before=next_sibling)
        return replacement

    def _content_mount_owner(self, error: Exception) -> tuple[Widget, Container] | None:
        """Find a registered replacement implicated in a lifecycle traceback."""
        hosts = getattr(self, "_content_mount_hosts", None)
        if hosts is None:
            return None
        recovering = getattr(self, "_content_mount_recovering", ())
        frames: list[Any] = []
        traceback = error.__traceback__
        saw_pre_process = False
        while traceback is not None:
            frame = traceback.tb_frame
            frames.append(frame)
            saw_pre_process |= frame.f_code.co_name == "_pre_process"
            traceback = traceback.tb_next
        if not saw_pre_process:
            return None
        for frame in reversed(frames):
            widget = frame.f_locals.get("self")
            if not isinstance(widget, Widget):
                continue
            current: Widget | None = widget
            while current is not None:
                host = hosts.get(current)
                if host is not None and current not in recovering:
                    return current, host
                parent = current.parent
                current = parent if isinstance(parent, Widget) else None
        return None

    async def _recover_content_mount_lifecycle(
        self,
        replacement: Widget,
        host: Container,
        error: Exception,
    ) -> None:
        try:
            current_host = self.query_one("#content-host", Container)
            if current_host is not host:
                return
            self._app_ctx.log_sink.error(
                "app.content_mount.lifecycle_failed",
                error=str(error),
                error_type=type(error).__name__,
            )
            recovered_host = await self._reset_content_host(host)
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
        mount_owner = self._content_mount_owner(error)
        if mount_owner is not None:
            replacement, host = mount_owner
            # Claim recovery before yielding to the worker. Textual may surface
            # more than one lifecycle exception while unwinding a failed mount.
            self._content_mount_recovering.add(replacement)
            self.run_worker(
                self._recover_content_mount_lifecycle(replacement, host, error),
                name="content mount recovery",
                group="content-mount-recovery",
                exclusive=True,
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
        (``_handle_exception``) does not currently call this — see the
        ``deferred-from-m6`` note on ``record_action``/crash-modal
        push_screen wiring.
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
        if getattr(self, "_service_navigation_closed", False):
            return
        self._service_navigation_closed = True
        subscription = getattr(self, "_service_navigation_sub", None)
        if subscription is not None:
            subscription.dispose()
        self._service_navigation_sub = None

    async def _drain_table_navigation(self) -> None:
        if not hasattr(self, "_table_navigation_tasks"):
            self._table_navigation_tasks = set()
        if not hasattr(self, "_table_handoff_rollbacks"):
            self._table_handoff_rollbacks = set()
        while self._table_navigation_tasks or self._table_handoff_rollbacks:
            navigations = tuple(self._table_navigation_tasks)
            for task in navigations:
                if not task.done():
                    task.cancel()
            if navigations:
                await self._await_tasks_through_cancellation(navigations)
                self._table_navigation_tasks.difference_update(navigations)

            rollbacks = tuple(self._table_handoff_rollbacks)
            if rollbacks:
                await self._await_tasks_through_cancellation(rollbacks)
                self._table_handoff_rollbacks.difference_update(rollbacks)

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
        self._close_service_navigation_intake()
        self.workers.cancel_group(self, "content-mount")
        navigation_lock = getattr(self, "_service_navigation_lock", None)
        if navigation_lock is not None:
            async with navigation_lock:
                pass
        await self._drain_table_navigation()
        with contextlib.suppress(Exception):
            ctx.transfers_vm.cancel_all_command.execute()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await self._cancel_transfer_workers_before_content_swap()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await ctx.command_palette_vm.shutdown()
        # Keep the hosted VM's graceful shutdown alive through caller
        # cancellation. Remote cleanup must complete while its AWS client is open.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            host_shutdown = asyncio.create_task(ctx.root_vm.content_host.shutdown())
            while not host_shutdown.done():
                try:
                    await asyncio.shield(host_shutdown)
                except asyncio.CancelledError:
                    continue
            await host_shutdown
        # Include asyncio.CancelledError in the suppress — without it
        # an in-flight CancelledError (a BaseException, not an
        # Exception) on the aclose_all_clients await would cascade
        # past every subsequent cleanup step below this block,
        # skipping log_sink.flush()/.close() + the four reactive
        # subscription disposes. Shutdown must NOT be cancellable
        # mid-stream.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await ctx.aws_session.aclose_all_clients()
        with contextlib.suppress(Exception):
            ctx.log_sink.flush()
            ctx.log_sink.close()
        with contextlib.suppress(Exception):
            if self._pane_state_sub is not None:
                self._pane_state_sub.dispose()
                self._pane_state_sub = None
        with contextlib.suppress(Exception):
            if self._connection_list_sub is not None:
                self._connection_list_sub.dispose()
                self._connection_list_sub = None
        with contextlib.suppress(Exception):
            if self._nav_selection_sub is not None:
                self._nav_selection_sub.dispose()
                self._nav_selection_sub = None
        with contextlib.suppress(Exception):
            if self._cursor_sub is not None:
                self._cursor_sub.dispose()
                self._cursor_sub = None
        with contextlib.suppress(Exception):
            if self._service_navigation_sub is not None:
                self._service_navigation_sub.dispose()
                self._service_navigation_sub = None
        with contextlib.suppress(Exception):
            if self._palette_failure_sub is not None:
                self._palette_failure_sub.dispose()
                self._palette_failure_sub = None
        with contextlib.suppress(Exception):
            self._dispose_table_clipboard_subscription()
        with contextlib.suppress(Exception):
            # Currently-hosted SettingsVM (if any) is disposed by the
            # ContentHostVM tree teardown via ``root_vm.shutdown()``.
            ctx.s3_connections_vm.dispose()
        # One suppress PER dispose — bundling them under a single
        # suppress lets the first raise short-circuit every later
        # call (most notably the FocusCoordinatorVM cleanup the
        # comment below specifically defends against).
        with contextlib.suppress(Exception):
            ctx.command_palette_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.quick_look_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.confirm_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.transfers_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.table_clipboard_vm.dispose()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        with contextlib.suppress(Exception):
            # FocusCoordinatorVM lives on the AppContext top-level,
            # not under root_vm, so root_vm.dispose() doesn't reach
            # it. Without an explicit call the inner ComponentVM +
            # Subject leak on every shutdown.
            ctx.focus_coordinator.dispose()
        with contextlib.suppress(Exception):
            # In demo mode, cancel any in-flight clone state-machine tasks
            # so asyncio doesn't emit "Task was destroyed but it is pending"
            # warnings on exit.  The InMemoryEmr singleton is shared across
            # connection switches within the same AppContext, so we dispose
            # it here (on app shutdown) rather than in EmrServerlessPageVM.
            if ctx.demo_emr is not None:
                await ctx.demo_emr.aclose()


def main() -> None:
    """Run the Textual app with unhandled-exception capture.

    Invoked by the ``aws-tui`` console script and ``python -m aws_tui``.
    If the app surfaces an unhandled exception, ``_handle_exception``
    writes a crash dump under ``~/.cache/aws-tui/crash/`` and the
    saved :class:`CrashReport` is printed here before the exception is
    re-raised so the user knows where the dump landed.

    Recognises ``--help``, ``--version``, and ``--demo`` before
    launching the UI.
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
    args = parser.parse_args()

    demo = args.demo or is_demo_mode_enabled(argv=[])

    if args.version:
        status = "enabled" if demo else "disabled"
        # Match the pip convention: ``project-name 0.8.0``.
        print(f"aws-tui {__version__} (demo: {status})")
        return

    app = AwsTuiApp(context=build_app_context(demo=demo))
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


if __name__ == "__main__":
    main()


__all__ = ["AwsTuiApp", "main"]
