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

from aws_tui.domain.emr_logs import LogFile, LogFileKind
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogsState


class _LogFileChip(Static):
    """One log-file chip in the file selector. Carries the LogFileKind
    so the pane's on_click can map a clicked chip to the file kind."""

    def __init__(self, content: str, *, key: str, classes: str | None = None) -> None:
        super().__init__(content, classes=classes)
        self.key = key


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
    /* Filter row — sits between the file-chip row and the body so
       the user always sees WHICH patterns are gating the lines and
       how to edit / reset them. Single-line, ellipsizes on overflow.
       User feedback (post-PR-#92): "I don't see the keyword filters
       being applied to the log via grey mentioned anywhere or the
       ability to customize them". */
    JobRunLogsPane > .logs-filter-row {
        height: 1;
        padding: 0 1;
        text-style: dim;
        text-overflow: ellipsis;
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
        Binding("F", "reset_filter", "Reset filter", show=False),
        Binding("left", "previous_file", "Previous file", show=False),
        Binding("right", "next_file", "Next file", show=False),
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

    class ResetFilterRequested(TextualMessage):
        """User pressed Shift+F to reset the filter to the default
        keyword set. Surfaces an explicit affordance for "I changed
        my mind / start over" without making the user re-type the
        canonical patterns in the filter modal."""

        pass

    class LogFileSelected(TextualMessage):
        """User selected a different log file from the chip strip."""

        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    def __init__(
        self,
        vm: JobRunLogsVM,
        *,
        keymap: KeymapStore | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunLogsVM = vm
        self._keymap = keymap or KeymapStore()
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="logs-chip-row"):
            pass  # Chips are added dynamically in _refresh_chips
        # ``markup=False`` on the filter row — its content is
        # ``f"filter: {' · '.join(filter.patterns)}{hint}"`` and
        # those patterns ARE user-typed regex strings. Common
        # patterns like ``[INFO]``, ``[ERROR]``, ``[0-9]+`` are
        # the obvious thing a user types to filter logs, and Rich
        # would try to parse them as style tags and crash the
        # filter-row render. (The status Static is dev-controlled
        # text but gets the guard for parity.)
        yield Static("", classes="logs-filter-row", id="logs-filter", markup=False)
        yield VerticalScroll(id="logs-body")
        yield Static("", classes="logs-status", id="logs-status", markup=False)

    def on_mount(self) -> None:
        self.border_title = "logs"
        self._refresh_chips()
        self._refresh_filter_row()
        self._refresh_body()
        self._refresh_status()
        # Round-3 / PR #103 retirement: subscribe to the VM's
        # per-instance Observable instead of filtering the shared
        # hub by sender_object.
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_property_changed)

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

    def action_reset_filter(self) -> None:
        """Post ResetFilterRequested (Shift+F)."""
        self.post_message(self.ResetFilterRequested())

    def action_previous_file(self) -> None:
        self._select_adjacent_file(-1)

    def action_next_file(self) -> None:
        self._select_adjacent_file(1)

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
        key: str | None = None
        while target is not None:
            if isinstance(target, _LogFileChip):
                key = target.key
                break
            target = getattr(target, "parent", None)
        if key is not None:
            self.post_message(self.LogFileSelected(key))

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, prop: str) -> None:
        """Round-3 directive: per-VM Observable subscription. The
        cross-VM `state` collisions PR #103 hub-filter was guarding
        against can't reach here because this Subject is scoped to
        JobRunLogsVM only."""
        if prop in {"available_files", "current_file"}:
            self.call_after_refresh(self._refresh_chips)
        elif prop == "filter":
            self.call_after_refresh(self._refresh_filter_row)
        elif prop == "state":
            self.call_after_refresh(self._refresh_body)
            self.call_after_refresh(self._refresh_status)
        elif prop == "lines":
            self.call_after_refresh(self._refresh_body)
        elif prop == "progress":
            self.call_after_refresh(self._refresh_status)

    def _refresh_filter_row(self) -> None:
        """Render the always-visible filter affordance:
        ``filter: <patterns…>  ·  <configured key> edit  ·  shift+f reset``. The
        patterns list is comma-joined and ellipsized by the
        text-overflow rule on ``.logs-filter-row``. In PASSTHROUGH
        mode the label flips to ``filter: off`` so the user can
        tell at a glance whether lines are being gated."""
        try:
            row = self.query_one("#logs-filter", Static)
        except Exception:
            return
        f = self._vm.filter
        from aws_tui.domain.emr_logs import FilterMode

        if f.mode is FilterMode.PASSTHROUGH or not f.patterns:
            patterns_text = "off"
        else:
            patterns_text = " · ".join(f.patterns)
        keys = self._keymap.resolve("emr.logs.filter")
        edit_hint = f" · {keys[0]} edit" if keys else ""
        hint = f"{edit_hint} · shift+f reset"
        row.update(f"filter: {patterns_text}{hint}")

    def _select_adjacent_file(self, delta: int) -> None:
        files = self._vm.available_files
        if len(files) < 2:
            return
        current = self._vm.current_file
        try:
            index = files.index(current) if current is not None else 0
        except ValueError:
            index = 0
        selected = files[(index + delta) % len(files)]
        if selected != current:
            self.post_message(self.LogFileSelected(selected.key))

    def _refresh_chips(self) -> None:
        """Render file-selector chip strip."""
        try:
            chip_row = self.query_one(".logs-chip-row", Horizontal)
        except Exception:
            return
        chip_row.remove_children()
        current = self._vm.current_file
        for f in self._vm.available_files:
            label = _format_log_file_label(f)
            classes = "logs-chip"
            if f == current:
                classes += " -active"
            chip = _LogFileChip(label, key=f.key, classes=classes)
            chip_row.mount(chip)

    def _refresh_body(self) -> None:
        """Render body based on state."""
        try:
            body = self.query_one("#logs-body", VerticalScroll)
        except Exception:
            return
        state = self._vm.state

        if state is LogsState.EMPTY_TARGET:
            self._update_body(body, "(no run selected)", classes="logs-placeholder")
            return
        if state is LogsState.IDLE:
            self._update_body(body, "(press Enter to load logs)", classes="logs-placeholder")
            return
        if state is LogsState.NO_LOG_CONFIG:
            self._update_body(
                body,
                "(no log monitoring configured for this job)",
                classes="logs-placeholder",
            )
            return
        if state is LogsState.NO_FILES:
            self._update_body(
                body,
                "(no log files yet — try again once the run starts logging)",
                classes="logs-placeholder",
            )
            return
        if state is LogsState.LOADING:
            current = self._vm.current_file
            file_label = _format_log_file_label(current) if current else "?"
            text = (
                f"loading {file_label}: {self._vm.bytes_read} bytes, "
                f"{self._vm.lines_scanned} lines scanned, {len(self._vm.lines)} matches"
            )
            self._update_body(body, text, classes="logs-placeholder")
            return
        if state is LogsState.ERROR:
            error_msg = self._vm.error_text or "error"
            # AWS-returned error text — never parse as markup. The
            # raw text frequently contains brackets (boto's error
            # serialisation includes ``[ContainerError(...)]`` etc.)
            # which Rich's parser blows up on. Defensive default.
            self._update_body(body, error_msg, classes="logs-placeholder -error")
            return
        if state in (LogsState.READY, LogsState.TRUNCATED):
            # Log lines are AWS-returned content — ``[INFO]``,
            # ``[WARN]``, ``[ERROR]`` etc. are universally present
            # in real log output. Rich's markup parser tries to read
            # the next token as a tag value and either crashes
            # (``MarkupError``) or silently corrupts the displayed
            # line. ``markup=False`` is the only safe default for
            # untrusted log content.
            text = "\n".join(self._vm.lines)
            if state is LogsState.TRUNCATED:
                text = (f"{text}\n" if text else "") + "(truncated at 100 MB — press r to reload)"
            self._update_body(
                body,
                text,
                classes="logs-line -match",
            )
            return

    @staticmethod
    def _update_body(body: VerticalScroll, text: str, *, classes: str) -> None:
        """Update one reusable text widget instead of remounting every log line."""
        try:
            content = body.query_one("#logs-content", Static)
        except Exception:
            body.remove_children()
            body.mount(
                Static(
                    text,
                    id="logs-content",
                    classes=classes,
                    markup=False,
                )
            )
            return
        content.set_classes(classes)
        content.update(text)

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


def _format_log_file_label(log_file: LogFile) -> str:
    """Format a LogFileKind as a chip label.

    Examples:
        DRIVER_STDOUT → "DRIVER stdout"
        DRIVER_STDERR → "DRIVER stderr"
        EXECUTOR_STDOUT → "EXEC 0 stdout" (extracted from S3 key)
        EXECUTOR_STDERR → "EXEC 1 stderr"
    """
    kind = log_file.kind
    segments = log_file.key.split("/")
    suffixes: list[str] = []
    if "attempts" in segments:
        index = len(segments) - 1 - segments[::-1].index("attempts")
        if index + 1 < len(segments):
            suffixes.append(f"try {segments[index + 1]}")
    filename = segments[-1]
    if filename.startswith(("stdout_", "stderr_")):
        suffixes.append(f"archive {filename.rsplit('_', 1)[-1].removesuffix('.gz')}")

    def decorated(label: str) -> str:
        return f"{label} · {' · '.join(suffixes)}" if suffixes else label

    if kind == LogFileKind.DRIVER_STDOUT:
        return decorated("DRIVER stdout")
    if kind == LogFileKind.DRIVER_STDERR:
        return decorated("DRIVER stderr")
    if kind == LogFileKind.EXECUTOR_STDOUT:
        worker = segments[len(segments) - segments[::-1].index("SPARK_EXECUTOR")]
        return decorated(f"EXEC {worker} stdout")
    if kind == LogFileKind.EXECUTOR_STDERR:
        worker = segments[len(segments) - segments[::-1].index("SPARK_EXECUTOR")]
        return decorated(f"EXEC {worker} stderr")
    if kind == LogFileKind.HIVE_DRIVER_STDOUT:
        return decorated("HIVE stdout")
    if kind == LogFileKind.HIVE_DRIVER_STDERR:
        return decorated("HIVE stderr")
    if kind == LogFileKind.TEZ_TASK_STDOUT:
        worker = segments[len(segments) - segments[::-1].index("TEZ_TASK")]
        return decorated(f"TEZ {worker} stdout")
    if kind == LogFileKind.TEZ_TASK_STDERR:
        worker = segments[len(segments) - segments[::-1].index("TEZ_TASK")]
        return decorated(f"TEZ {worker} stderr")
    return str(kind)


__all__ = ["JobRunLogsPane"]
