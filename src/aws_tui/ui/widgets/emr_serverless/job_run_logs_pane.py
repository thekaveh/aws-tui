"""JobRunLogsPane — RIGHT-bottom pane of the EMR page.

Renders the state-machine of ``JobRunLogsVM``:

    EMPTY_TARGET   →  ``(no run selected)``
    IDLE           →  ``(press Enter to load logs)``
    LOADING        →  ``loading <log_file>: N bytes read, M lines scanned`` + spinner
    READY          →  scrollable line list
    TRUNCATED      →  same, with banner ``(truncated at byte cap — press r to reload)``
    NO_LOG_CONFIG  →  ``(no log monitoring configured for this job)``
    NO_FILES       →  ``(no log files yet — try again once the run starts logging)``
    ERROR          →  red placeholder + error text

Filter and file-selector chips are above the body; the file
selector shows the currently-loaded LogFile and dispatches a
``LogFileSelected`` message when the user changes it.
"""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.emr_logs import LogFileKind
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogsState


class _LogFileChip(Static):
    """One log-file chip in the file selector. Carries the LogFileKind
    so the pane's on_click can map a clicked chip to the file kind."""

    def __init__(self, content: str, *, kind: LogFileKind, classes: str | None = None) -> None:
        super().__init__(content, classes=classes)
        self.kind: LogFileKind = kind


class JobRunLogsPane(Widget, can_focus=True):
    DEFAULT_CSS: ClassVar[str] = """
    JobRunLogsPane {
        height: 1fr;
        layout: vertical;
    }
    JobRunLogsPane > .logs-chip-row {
        height: 1;
        layout: horizontal;
        padding: 0 1;
        overflow-x: auto;
        overflow-y: hidden;
    }
    JobRunLogsPane > .logs-chip-row > .logs-chip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 1 0 0;
    }
    JobRunLogsPane > VerticalScroll {
        height: 1fr;
    }
    JobRunLogsPane .logs-line {
        height: auto;
        padding: 0 1;
    }
    JobRunLogsPane .logs-placeholder {
        height: auto;
        padding: 0 1;
    }
    JobRunLogsPane > .logs-status {
        height: 1;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "load", "Load", show=False),
        Binding("r", "reload", "Reload", show=False),
        Binding("f", "open_filter", "Filter", show=False),
        Binding("up", "scroll_up", "Up", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("down", "scroll_down", "Down", show=False),
        Binding("j", "scroll_down", "Down", show=False),
    ]

    class LoadRequested(TextualMessage):
        """User pressed Enter to load logs."""

        pass

    class RefreshRequested(TextualMessage):
        """User pressed r to refresh/reload logs."""

        pass

    class OpenFilterRequested(TextualMessage):
        """User pressed f to open the filter modal."""

        pass

    class LogFileSelected(TextualMessage):
        """User selected a different log file from the chip strip."""

        def __init__(self, kind: LogFileKind) -> None:
            super().__init__()
            self.kind = kind

    def __init__(
        self,
        vm: JobRunLogsVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunLogsVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="logs-chip-row"):
            pass  # Chips are added dynamically in _refresh_chips
        yield VerticalScroll(id="logs-body")
        yield Static("", classes="logs-status", id="logs-status")

    def on_mount(self) -> None:
        self.border_title = "logs"
        self._refresh_chips()
        self._refresh_body()
        self._refresh_status()
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_load(self) -> None:
        """Post LoadRequested when in a loadable state."""
        if self._vm.state in (LogsState.IDLE, LogsState.NO_FILES):
            self.post_message(self.LoadRequested())

    def action_reload(self) -> None:
        """Post RefreshRequested."""
        self.post_message(self.RefreshRequested())

    def action_open_filter(self) -> None:
        """Post OpenFilterRequested."""
        self.post_message(self.OpenFilterRequested())

    def action_scroll_up(self) -> None:
        """Scroll body up."""
        try:
            body = self.query_one("#logs-body", VerticalScroll)
            body.scroll_up()
        except Exception:
            pass

    def action_scroll_down(self) -> None:
        """Scroll body down."""
        try:
            body = self.query_one("#logs-body", VerticalScroll)
            body.scroll_down()
        except Exception:
            pass

    # ── Mouse ───────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        """Click on a file chip → select that file."""
        target: object | None = event.widget
        kind: LogFileKind | None = None
        while target is not None:
            if isinstance(target, _LogFileChip):
                kind = target.kind
                break
            target = getattr(target, "parent", None)
        if kind is not None:
            self.post_message(self.LogFileSelected(kind))

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name in {"available_files", "current_file"}:
            self.call_after_refresh(self._refresh_chips)
        elif msg.property_name in {"state", "lines", "progress"}:
            self.call_after_refresh(self._refresh_body)
            self.call_after_refresh(self._refresh_status)

    def _refresh_chips(self) -> None:
        """Render file-selector chip strip."""
        try:
            chip_row = self.query_one(".logs-chip-row", Horizontal)
        except Exception:
            return
        chip_row.remove_children()
        current = self._vm.current_file
        for f in self._vm.available_files:
            label = _format_log_file_label(f.kind)
            classes = "logs-chip"
            if f == current:
                classes += " -active"
            chip = _LogFileChip(label, kind=f.kind, classes=classes)
            chip_row.mount(chip)

    def _refresh_body(self) -> None:
        """Render body based on state."""
        try:
            body = self.query_one("#logs-body", VerticalScroll)
        except Exception:
            return
        body.remove_children()
        state = self._vm.state

        if state is LogsState.EMPTY_TARGET:
            body.mount(Static("(no run selected)", classes="logs-placeholder"))
            return
        if state is LogsState.IDLE:
            body.mount(Static("(press Enter to load logs)", classes="logs-placeholder"))
            return
        if state is LogsState.NO_LOG_CONFIG:
            body.mount(
                Static(
                    "(no log monitoring configured for this job)",
                    classes="logs-placeholder",
                )
            )
            return
        if state is LogsState.NO_FILES:
            body.mount(
                Static(
                    "(no log files yet — try again once the run starts logging)",
                    classes="logs-placeholder",
                )
            )
            return
        if state is LogsState.LOADING:
            current = self._vm.current_file
            file_label = _format_log_file_label(current.kind) if current else "?"
            text = (
                f"loading {file_label}: {self._vm.bytes_read} bytes, "
                f"{self._vm.lines_scanned} lines scanned, {len(self._vm.lines)} matches"
            )
            body.mount(Static(text, classes="logs-placeholder"))
            return
        if state is LogsState.ERROR:
            error_msg = self._vm.error_text or "error"
            body.mount(Static(error_msg, classes="logs-placeholder -error"))
            return
        if state in (LogsState.READY, LogsState.TRUNCATED):
            # Render log lines
            for line in self._vm.lines:
                body.mount(Static(line, classes="logs-line -match"))
            # Add truncation banner if needed
            if state is LogsState.TRUNCATED:
                body.mount(
                    Static(
                        "(truncated at 100 MB — press r to reload)",
                        classes="logs-placeholder",
                    )
                )
            return

    def _refresh_status(self) -> None:
        """Update status footer."""
        try:
            status = self.query_one("#logs-status", Static)
        except Exception:
            return
        state = self._vm.state
        if state is LogsState.READY:
            text = (
                f"READY · {self._vm.bytes_read / 1024 / 1024:.1f} MB · "
                f"{len(self._vm.lines)} matches"
            )
            status.update(text)
        elif state is LogsState.TRUNCATED:
            text = (
                f"TRUNCATED · {self._vm.bytes_read / 1024 / 1024:.1f} MB · "
                f"{len(self._vm.lines)} matches"
            )
            status.update(text)
        elif state is LogsState.LOADING:
            text = (
                f"LOADING · {self._vm.bytes_read / 1024 / 1024:.1f} MB · "
                f"{len(self._vm.lines)} matches"
            )
            status.update(text)
        else:
            status.update("")


def _format_log_file_label(kind: LogFileKind) -> str:
    """Format a LogFileKind as a chip label.

    Examples:
        DRIVER_STDOUT → "DRIVER stdout"
        DRIVER_STDERR → "DRIVER stderr"
        EXECUTOR_STDOUT → "EXEC 0 stdout" (extracted from S3 key)
        EXECUTOR_STDERR → "EXEC 1 stderr"
    """
    if kind == LogFileKind.DRIVER_STDOUT:
        return "DRIVER stdout"
    if kind == LogFileKind.DRIVER_STDERR:
        return "DRIVER stderr"
    if kind == LogFileKind.EXECUTOR_STDOUT:
        return "EXEC stdout"
    if kind == LogFileKind.EXECUTOR_STDERR:
        return "EXEC stderr"
    return str(kind)


__all__ = ["JobRunLogsPane"]
