# src/aws_tui/ui/widgets/emr_serverless/job_runs_pane.py
"""JobRunsPane — LEFT pane of the EMR page.

Pane chrome + state-filter chip row + column header + scrollable run
list. Master-detail; the parent ``EmrServerlessPage`` listens for
the ``RunSelected`` message and re-points the RIGHT detail pane.

Selection follows the cursor: ``Up`` / ``Down`` move the cursor AND
fire ``RunSelected`` (the detail follows immediately, no Enter
required — same UX as a list-master-detail UI). ``Enter`` and click
are also valid commits. ``r`` posts ``RefreshRequested``.

Row layout — three columns aligned per row:

    STATUS         NAME                                TIME
    ✓ SUCCESS      nightly-2026-06-25                 12:01:34
    ● RUNNING      ad-hoc                             12:05:00
    ✗ FAILED       retry-7b3                          11:58:00
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

from aws_tui.domain.emr_serverless import JobRunState, JobRunSummary
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_STATE_GLYPH: dict[JobRunState, str] = {
    JobRunState.SUCCESS: "✓",
    JobRunState.RUNNING: "●",
    JobRunState.PENDING: "⏸",
    JobRunState.SUBMITTED: "↻",
    JobRunState.SCHEDULED: "↻",
    JobRunState.QUEUED: "↻",
    JobRunState.FAILED: "✗",
    JobRunState.CANCELLED: "⊘",
    JobRunState.CANCELLING: "⊘",
}

_KEY_TO_STATE: dict[str, JobRunState] = {
    "1": JobRunState.SUCCESS,
    "2": JobRunState.RUNNING,
    "3": JobRunState.PENDING,
    "4": JobRunState.FAILED,
    "5": JobRunState.CANCELLED,
}

# Status column = ``"<glyph> <STATE_NAME>"`` padded to this width.
# Longest state name is ``CANCELLED`` (9 chars); the column adds 1
# leading glyph + 1 gap + 9 = 11 chars; pad to 12 for breathing room.
_STATUS_COL_WIDTH: int = 12
# Time column = ``HH:MM:SS`` = 8 chars.
_TIME_COL_WIDTH: int = 8


class _JobRunRow(Static):
    """One job-run row. Carries ``run_id`` so the pane's
    :meth:`JobRunsPane.on_click` can map a clicked row to the
    underlying run without sharing a Textual widget id between
    paint cycles (which would race ``remove_children`` / ``mount``
    and crash with ``DuplicateIds`` when the pane re-renders)."""

    def __init__(self, content: str, *, run_id: str, classes: str | None = None) -> None:
        super().__init__(content, classes=classes)
        self.run_id: str = run_id


class JobRunsPane(Widget, can_focus=True):
    DEFAULT_CSS: ClassVar[str] = """
    JobRunsPane {
        height: 1fr;
        layout: vertical;
    }
    JobRunsPane > .runs-chip-row {
        height: 1;
        layout: horizontal;
        padding: 0 1;
    }
    JobRunsPane > .runs-chip-row > .runs-chip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 1 0 0;
    }
    JobRunsPane > .runs-column-header {
        height: 1;
        padding: 0 1;
    }
    JobRunsPane > VerticalScroll {
        height: 1fr;
    }
    JobRunsPane .runs-row {
        height: 1;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "commit_selection", "Open"),
        Binding("r", "request_refresh", "Refresh"),
        *[
            Binding(k, f"toggle_state_filter('{s.value}')", show=False)
            for k, s in _KEY_TO_STATE.items()
        ],
    ]

    class RunSelected(TextualMessage):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class RefreshRequested(TextualMessage):
        pass

    def __init__(
        self,
        vm: JobRunsVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunsVM = vm
        self._hub: MessageHub[Message] = hub
        self._cursor_index: int = 0
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="runs-chip-row"):
            for state in (
                JobRunState.SUCCESS,
                JobRunState.RUNNING,
                JobRunState.PENDING,
                JobRunState.FAILED,
                JobRunState.CANCELLED,
            ):
                yield Static(
                    f" {_STATE_GLYPH[state]} ",
                    classes=f"runs-chip runs-chip-{state.value.lower()}",
                    id=f"runs-chip-{state.value.lower()}",
                )
        yield Static(_format_column_header(), classes="runs-column-header")
        yield VerticalScroll(id="runs-body")

    def on_mount(self) -> None:
        self.border_title = "runs"
        self._refresh_chips()
        self._refresh_rows()
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_toggle_state_filter(self, state_value: str) -> None:
        self._vm.toggle_state_filter(JobRunState(state_value))

    def action_cursor_up(self) -> None:
        if self._cursor_index > 0:
            self._cursor_index -= 1
            self._refresh_rows()
            # Master-detail: the detail pane follows the cursor.
            # Posting RunSelected on every cursor move (instead of
            # waiting for Enter) is the UX the user asked for —
            # arrow through the list and the right pane updates in
            # lock-step, like Spark History Server or a typical
            # master/detail list. Real-boto cost is one extra
            # ``get_job_run`` call per arrow press; PR-B's response
            # cache (spec §6) keeps that bounded.
            self._post_run_selected_for_cursor()

    def action_cursor_down(self) -> None:
        if self._cursor_index + 1 < len(self._vm.runs):
            self._cursor_index += 1
            self._refresh_rows()
            self._post_run_selected_for_cursor()

    def action_commit_selection(self) -> None:
        # Enter is a no-op-vs-Up/Down: the cursor-move already fired
        # RunSelected. We still post it on commit so a click or Enter
        # without any prior arrow press also lands the detail.
        self._post_run_selected_for_cursor()

    def _post_run_selected_for_cursor(self) -> None:
        runs = self._vm.runs
        if not runs or not (0 <= self._cursor_index < len(runs)):
            return
        run_id = runs[self._cursor_index].job_run_id
        self.post_message(self.RunSelected(run_id))

    def action_request_refresh(self) -> None:
        self.post_message(self.RefreshRequested())

    # ── Mouse ───────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        """Click on a row → move cursor to that row + select it.

        Mirrors S3's pane click behaviour (per the user feedback
        "Mouse also doesn't work … like it does for s3"). Rows
        mount as :class:`_JobRunRow` widgets carrying ``run_id``
        as a Python attribute; we walk the event widget chain to
        find the first ``_JobRunRow`` and translate its ``run_id``
        to a cursor index."""
        target: object | None = event.widget
        run_id: str | None = None
        while target is not None:
            if isinstance(target, _JobRunRow):
                run_id = target.run_id
                break
            target = getattr(target, "parent", None)
        if run_id is None:
            return
        runs = self._vm.runs
        for idx, r in enumerate(runs):
            if r.job_run_id == run_id:
                self._cursor_index = idx
                self._refresh_rows()
                self.post_message(self.RunSelected(run_id))
                return

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name == "state_filter":
            self.call_after_refresh(self._refresh_chips)
            self.call_after_refresh(self._refresh_rows)
        elif msg.property_name in {"runs", "selected_id", "state"}:
            self.call_after_refresh(self._refresh_rows)

    def _refresh_chips(self) -> None:
        active = self._vm.state_filter
        for state in (
            JobRunState.SUCCESS,
            JobRunState.RUNNING,
            JobRunState.PENDING,
            JobRunState.FAILED,
            JobRunState.CANCELLED,
        ):
            try:
                chip = self.query_one(f"#runs-chip-{state.value.lower()}", Static)
            except Exception:
                continue
            if state in active:
                chip.add_class("-active")
            else:
                chip.remove_class("-active")

    def _refresh_rows(self) -> None:
        try:
            body = self.query_one("#runs-body", VerticalScroll)
        except Exception:
            return
        body.remove_children()
        state = self._vm.state
        runs = self._vm.runs
        if state is PaneState.LOADING:
            body.mount(Static("loading…", classes="runs-placeholder"))
            return
        if state is PaneState.EMPTY or not runs:
            body.mount(Static("(no runs)", classes="runs-placeholder"))
            return
        if state is PaneState.UNREACHABLE:
            body.mount(
                Static(
                    self._vm.error_text or "endpoint unreachable — press r to retry",
                    classes="runs-placeholder",
                )
            )
            return
        if state is PaneState.AUTH_REQUIRED:
            body.mount(
                Static(
                    "authentication required — aws sso login --profile <X>",
                    classes="runs-placeholder",
                )
            )
            return
        if self._cursor_index >= len(runs):
            self._cursor_index = max(0, len(runs) - 1)
        for idx, r in enumerate(runs):
            row_classes = "runs-row"
            if idx == self._cursor_index:
                row_classes += " -selected"
            body.mount(
                _JobRunRow(_format_run_row(r), run_id=r.job_run_id, classes=row_classes),
            )


def _format_column_header() -> str:
    """Header row matching :func:`_format_run_row`'s columns."""
    return f"{'STATUS':<{_STATUS_COL_WIDTH}}  {'NAME':<24}  {'TIME':<{_TIME_COL_WIDTH}}"


def _format_run_row(r: JobRunSummary) -> str:
    """One row, three columns:

    - **Status** = ``"<glyph> <STATE_NAME>"`` left-padded to
      ``_STATUS_COL_WIDTH``.
    - **Name** = run name or id; takes the middle ``1fr`` flex
      column. Rich/Textual handles overflow via the pane's
      ``text-overflow`` rule — we don't truncate here.
    - **Time** = ``HH:MM:SS`` from ``created_at``, fixed-width.
    """
    glyph = _STATE_GLYPH.get(r.state, "?")
    status = f"{glyph} {r.state.value}"
    name = r.name or r.job_run_id
    ts = r.created_at.strftime("%H:%M:%S")
    return f"{status:<{_STATUS_COL_WIDTH}}  {name:<24}  {ts:<{_TIME_COL_WIDTH}}"


__all__ = ["JobRunsPane"]
