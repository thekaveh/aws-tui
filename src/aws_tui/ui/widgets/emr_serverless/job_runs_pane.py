# src/aws_tui/ui/widgets/emr_serverless/job_runs_pane.py
"""JobRunsPane — LEFT pane of the EMR page.

Pane chrome + state-filter chip row + scrollable run list. Selection
is master-detail; the parent ``EmrServerlessPage`` listens for the
``RunSelected`` message and re-points the RIGHT detail pane.

Keybindings (active when the pane has Textual focus):
- ``1``..``5`` toggle state-filter chips (PR-A scope; PR-B reuses
  the same keys for log-level chips on the RIGHT pane).
- ``Up`` / ``Down`` move row cursor.
- ``Enter`` commits selection.
- ``r`` requests refresh."""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
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

    def action_cursor_down(self) -> None:
        if self._cursor_index + 1 < len(self._vm.runs):
            self._cursor_index += 1
            self._refresh_rows()

    def action_commit_selection(self) -> None:
        runs = self._vm.runs
        if not runs or not (0 <= self._cursor_index < len(runs)):
            return
        run_id = runs[self._cursor_index].job_run_id
        self.post_message(self.RunSelected(run_id))

    def action_request_refresh(self) -> None:
        self.post_message(self.RefreshRequested())

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
            row = _format_run_row(r)
            row_classes = "runs-row"
            if idx == self._cursor_index:
                row_classes += " -selected"
            body.mount(Static(row, classes=row_classes))


def _format_run_row(r: JobRunSummary) -> str:
    glyph = _STATE_GLYPH.get(r.state, "?")
    ts = r.created_at.strftime("%H:%M:%S")
    label = r.name or r.job_run_id
    return f"{glyph} {label} · {ts}"


__all__ = ["JobRunsPane"]
