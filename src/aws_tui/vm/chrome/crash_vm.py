"""CrashVM — facade for the post-mortem crash modal.

When the top-level :class:`AwsTuiApp` catches an unhandled exception, it
builds a :class:`CrashReport` describing the crash, writes the dump via
:class:`aws_tui.infra.crash_dump.CrashDump`, and instantiates a
``CrashVM(report)`` which the modal binds to.

The VM is a thin facade over VMx ``ModalVM[CrashChoice]``. Only one ask may
be in flight at a time; the typical caller pushes the modal, awaits
``ask()``, then acts on the returned :class:`CrashChoice`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from vmx import ComponentVM, Message, MessageHub, ModalVM, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


class CrashChoice(StrEnum):
    """The user's decision when faced with the crash modal."""

    CONTINUE = "continue"
    VIEW_TRACE = "view_trace"
    QUIT = "quit"


#: Last-command identifiers that are safe to drop on "continue" — purely
#: read-only navigation / display ops with no on-disk side effects. The
#: composition root tracks the last command id in :class:`RootVM` (or a
#: small adjacent ring buffer) and consults this set to decide whether
#: ``can_continue`` is True.
#:
#: These MUST be ids that ``AwsTuiApp.record_action`` actually emits. An earlier
#: version named a parallel vocabulary (``pane.cursor_up``, ``quick_look.open``,
#: ``theme.switch``, …) that the app never records, so only two entries ever
#: matched and ``can_continue`` was False after essentially every read-only
#: action. ``test_safe_continue_actions_are_recorded_ids`` pins the two lists
#: together.
#:
#: Deliberately EXCLUDED because they mutate state or end the session:
#: ``pane.copy``, ``pane.delete``, ``athena.execute``, ``athena.cancel``,
#: ``emr.clone``, ``app.open_settings``, ``app.quit``.
SAFE_CONTINUE_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        # Navigation and cursor movement.
        "pane.move_up",
        "pane.move_down",
        "pane.mark_up",
        "pane.mark_down",
        "pane.ascend",
        "pane.descend",
        "pane.refresh",
        "pane.switch_focus",
        "pane.switch_focus_back",
        "pane.quick_look",
        "pane.modal_left",
        "pane.modal_right",
        # Chrome and display.
        "app.command_palette",
        "app.themes",
        "app.cycle_theme",
        "app.help",
        "app.swap_source",
        # Read-only service views and selectors.
        "athena.choose_workgroup",
        "athena.choose_catalog",
        "athena.choose_database",
        "athena.insert_table_ref",
        "athena.load_more",
        "athena.open_in_glue",
        "athena.open_result_location",
        "glue.choose_crawler_state",
        "glue.choose_run_state",
        "glue.copy_table_ref",
        "glue.query_in_athena",
        "glue.time_travel_in_athena",
        "glue.open_s3_location",
        "emr.logs.filter",
        "emr.next_application",
    }
)


@dataclass(frozen=True, slots=True)
class CrashReport:
    """Immutable description of a crash for the view layer.

    The full dump lives on disk at :attr:`dump_path`; the modal only ever
    renders :attr:`exception_type`, :attr:`exception_message`, and
    :attr:`traceback_short` so the screen stays narrow.
    """

    timestamp: datetime
    exception_type: str
    exception_message: str
    traceback_short: str
    dump_path: Path
    can_continue: bool
    last_action_id: str | None = None

    @classmethod
    def is_safe_to_continue(cls, last_action_id: str | None) -> bool:
        """Return True if ``last_action_id`` was a read-only command.

        ``None`` (unknown) is conservatively unsafe.
        """
        if last_action_id is None:
            return False
        return last_action_id in SAFE_CONTINUE_ACTIONS


class CrashVM:
    """Async ``ask`` facade returning a :class:`CrashChoice`.

    Properties: :attr:`report`, :attr:`is_open`, :attr:`can_continue`.
    Commands: :attr:`continue_command`, :attr:`view_trace_command`,
    :attr:`quit_command`.
    """

    def __init__(
        self,
        report: CrashReport,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub

        self._report: CrashReport = report
        self._is_open: bool = False
        self._modal: ModalVM[CrashChoice] | None = None
        self._disposed: bool = False

        self._inner: ComponentVM = (
            ComponentVM.builder().name("crash").services(hub, dispatcher).build()
        )

        self._continue_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._is_open and self._report.can_continue)
            .task(lambda: self._resolve(CrashChoice.CONTINUE))
            .build()
        )
        self._view_trace_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._is_open)
            .task(lambda: self._resolve(CrashChoice.VIEW_TRACE))
            .build()
        )
        self._quit_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._is_open)
            .task(lambda: self._resolve(CrashChoice.QUIT))
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def report(self) -> CrashReport:
        return self._report

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def can_continue(self) -> bool:
        return self._report.can_continue

    @property
    def continue_command(self) -> RelayCommand:
        return self._continue_command

    @property
    def view_trace_command(self) -> RelayCommand:
        return self._view_trace_command

    @property
    def quit_command(self) -> RelayCommand:
        return self._quit_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._modal is not None:
            self._modal.dispose()
        self._continue_command.dispose()
        self._view_trace_command.dispose()
        self._quit_command.dispose()
        self._inner.dispose()

    # ── Async API ──────────────────────────────────────────────────────────

    async def ask(self) -> CrashChoice:
        """Open the crash modal and await the user's choice."""
        if self._is_open or self._modal is not None:
            raise RuntimeError("crash modal is already open")
        if self._disposed:
            raise RuntimeError("crash modal has been disposed")
        self._modal = ModalVM(CrashChoice.QUIT)
        self._set_open(True)
        try:
            return await self._modal.wait_result()
        finally:
            self._modal = None
            self._set_open(False)

    # ── Internal ────────────────────────────────────────────────────────────

    def _resolve(self, choice: CrashChoice) -> None:
        if self._modal is None or self._modal.is_dismissed:
            return
        self._modal.dismiss(choice)

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_open"))


__all__ = ["SAFE_CONTINUE_ACTIONS", "CrashChoice", "CrashReport", "CrashVM"]
