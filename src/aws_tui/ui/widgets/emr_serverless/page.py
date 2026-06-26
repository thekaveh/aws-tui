"""EmrServerlessPage — content-host root for the EMR service.

Composes the top strip + 2-pane body and owns the three auto-refresh
intervals via Textual's ``set_interval``. The intervals are
independent so they back off independently on
:class:`ThrottlingException` (PR-B wires the back-off — PR-A
ships the static cadences from spec §6)."""

from __future__ import annotations

from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM


class EmrServerlessPage(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    EmrServerlessPage {
        height: 1fr;
        layout: horizontal;
    }
    EmrServerlessPage > .emr-left-column {
        width: 1fr;
        height: 1fr;
        layout: vertical;
    }
    EmrServerlessPage > .emr-left-column > .emr-app-box {
        height: 3;
    }
    EmrServerlessPage > .emr-left-column > JobRunsPane {
        height: 1fr;
    }
    EmrServerlessPage > JobRunDetailPane {
        width: 1fr;
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "open_application_picker", "Apps"),
        Binding("tab", "cycle_panes_forward", "Tab"),
        Binding("shift+tab", "cycle_panes_back", "←Tab"),
    ]

    def __init__(
        self,
        vm: EmrServerlessPageVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: EmrServerlessPageVM = vm
        self._hub: MessageHub[Message] = hub
        self._picker: ApplicationPicker | None = None
        self._left: JobRunsPane | None = None
        self._right: JobRunDetailPane | None = None
        self._runs_tick_counter: int = 0

    def compose(self) -> ComposeResult:
        self._picker = ApplicationPicker(self._vm.applications, hub=self._hub, id="emr-app-picker")
        self._left = JobRunsPane(self._vm.job_runs, hub=self._hub, id="emr-runs-pane")
        self._right = JobRunDetailPane(self._vm.job_run_detail, hub=self._hub, id="emr-detail-pane")
        # Page layout — TWO columns:
        #
        #   ┌─────────────────────┬────────────────────────────┐
        #   │ application picker  │                            │
        #   │  (bordered box,     │   JobRunDetailPane         │
        #   │   height 3)         │   (full height)            │
        #   ├─────────────────────┤                            │
        #   │ JobRunsPane         │                            │
        #   │  (height 1fr,       │                            │
        #   │   chip row + rows)  │                            │
        #   └─────────────────────┴────────────────────────────┘
        #
        # The application-picker box is width-matched to the
        # JobRunsPane below it (both share the LEFT column at
        # ``width: 1fr``) and only its border + title are visible —
        # the picker widget itself fills the box. The detail pane
        # spans the full page height, mirroring the S3 page's
        # symmetric LEFT-RIGHT split.
        with Vertical(classes="emr-left-column"):
            with Horizontal(classes="emr-app-box", id="emr-app-box"):
                yield self._picker
            yield self._left
        yield self._right

    def on_mount(self) -> None:
        # App-box border title — set here because Textual takes the
        # title from a Python attribute, not from CSS. The matching
        # ``:focus-within`` border-accent style is in the per-theme
        # .tcss so the box highlights when the picker is open.
        try:
            box = self.query_one("#emr-app-box", Horizontal)
            box.border_title = "applications"
        except Exception:
            pass
        # Initial load: applications + first-app's runs + first-run detail.
        self.run_worker(self._vm.setup(), exclusive=True, group="emr-setup")
        # Set up the three pollers per spec §6.
        self.set_interval(30.0, self._tick_applications, name="emr-poll-apps")
        self.set_interval(10.0, self._tick_runs, name="emr-poll-runs")
        self.set_interval(5.0, self._tick_detail, name="emr-poll-detail")
        # Land Textual focus on the LEFT pane so the user gets the
        # same "arrow keys move the cursor immediately" UX as the S3
        # page. Without this, neither pane shows the
        # ``:focus-within`` accent border and the user has to press
        # Tab once before arrows do anything.
        if self._left is not None:
            self.call_after_refresh(self._left.focus)

    # ── Public accessors ────────────────────────────────────────────────────

    @property
    def left_pane(self) -> JobRunsPane | None:
        """LEFT pane (job runs list). Public so ``AwsTuiApp``'s
        global priority key handlers can forward Up/Down/Enter/r to
        it the same way the S3 path forwards through
        ``dual.focused_pane``."""
        return self._left

    @property
    def right_pane(self) -> JobRunDetailPane | None:
        """RIGHT pane (job-run detail). Public for the same reason
        as :attr:`left_pane`."""
        return self._right

    # ── Pollers ─────────────────────────────────────────────────────────────

    def _tick_applications(self) -> None:
        self.run_worker(self._vm.applications.refresh(), exclusive=False, group="emr-poll-apps")

    def _tick_runs(self) -> None:
        # Cadence-decay: when no PENDING/RUNNING, only refresh every 6th tick (~60 s).
        if not self._vm.job_runs.has_active_runs() and self._poll_runs_decay():
            return
        self.run_worker(self._vm.job_runs.refresh(), exclusive=False, group="emr-poll-runs")

    def _tick_detail(self) -> None:
        # Only poll while the run is non-terminal.
        if self._vm.job_run_detail.is_terminal_state():
            return
        self.run_worker(self._vm.job_run_detail.refresh(), exclusive=False, group="emr-poll-detail")

    def _poll_runs_decay(self) -> bool:
        """Return True if THIS tick should be skipped (6:1 decay)."""
        self._runs_tick_counter = (self._runs_tick_counter + 1) % 6
        return self._runs_tick_counter != 0

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_open_application_picker(self) -> None:
        if self._picker is not None:
            self._picker.toggle_open()

    def action_cycle_panes_forward(self) -> None:
        self._cycle("right")

    def action_cycle_panes_back(self) -> None:
        self._cycle("left")

    def _cycle(self, direction: Literal["left", "right"]) -> None:
        # 2-slot cycle; direction doesn't matter for 2 slots, but keep
        # the binding shape so future expansion (e.g. log pane in PR-B)
        # has a place to grow without renaming actions.
        if self._left is None or self._right is None:
            return
        if self._left.has_focus_within or self._left.has_focus:
            self._right.focus()
        else:
            self._left.focus()

    # ── Message routing ─────────────────────────────────────────────────────

    def on_job_runs_pane_run_selected(self, event: JobRunsPane.RunSelected) -> None:
        self.run_worker(
            self._vm.select_job_run(event.run_id), exclusive=True, group="emr-select-run"
        )

    def on_job_runs_pane_refresh_requested(self, _event: JobRunsPane.RefreshRequested) -> None:
        self.run_worker(self._vm.refresh_focused("runs"), exclusive=True, group="emr-refresh")

    def on_job_run_detail_pane_refresh_requested(
        self, _event: JobRunDetailPane.RefreshRequested
    ) -> None:
        self.run_worker(self._vm.refresh_focused("detail"), exclusive=True, group="emr-refresh")


__all__ = ["EmrServerlessPage"]
