"""CrashVM — facade for the post-mortem crash modal.

When the top-level :class:`AwsTuiApp` catches an unhandled exception, it
builds a :class:`CrashReport` describing the crash, writes the dump via
:class:`aws_tui.infra.crash_dump.CrashDump`, and instantiates a
``CrashVM(report)`` which the modal binds to.

The VM is a thin shim around an :class:`asyncio.Future[CrashChoice]`
(same pattern as :class:`ConfirmationVM`). Only one ask may be in flight
at a time; the typical caller pushes the modal, awaits ``ask()``, then
acts on the returned :class:`CrashChoice`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage, RelayCommand
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
SAFE_CONTINUE_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "pane.navigate",
        "pane.refresh",
        "pane.filter",
        "pane.cursor_up",
        "pane.cursor_down",
        "pane.cursor_top",
        "pane.cursor_bottom",
        "pane.page_up",
        "pane.page_down",
        "pane.toggle_select",
        "pane.select_all",
        "pane.clear_selection",
        "pane.set_filter",
        "pane.toggle_hidden",
        "pane.switch_focus",
        "command_palette.open",
        "command_palette.close",
        "command_palette.move",
        "quick_look.open",
        "quick_look.close",
        "quick_look.scroll",
        "quick_look.find",
        "services_menu.switch_service",
        "theme.switch",
        "app.focus",
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
        self._future: asyncio.Future[CrashChoice] | None = None
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
        if self._future is not None and not self._future.done():
            self._future.set_result(CrashChoice.QUIT)
        self._continue_command.dispose()
        self._view_trace_command.dispose()
        self._quit_command.dispose()
        self._inner.dispose()

    # ── Async API ──────────────────────────────────────────────────────────

    async def ask(self) -> CrashChoice:
        """Open the crash modal and await the user's choice."""
        if self._is_open or self._future is not None:
            raise RuntimeError("crash modal is already open")
        if self._disposed:
            raise RuntimeError("crash modal has been disposed")
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self._set_open(True)
        try:
            return await self._future
        finally:
            self._future = None
            self._set_open(False)

    # ── Internal ────────────────────────────────────────────────────────────

    def _resolve(self, choice: CrashChoice) -> None:
        if self._future is None or self._future.done():
            return
        self._future.set_result(choice)

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_open"))


__all__ = ["SAFE_CONTINUE_ACTIONS", "CrashChoice", "CrashReport", "CrashVM"]
