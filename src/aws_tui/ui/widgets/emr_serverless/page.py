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
from textual.containers import Horizontal
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
        layout: vertical;
    }
    EmrServerlessPage > .emr-top-strip {
        height: 3;
        layout: horizontal;
        padding: 0 1;
    }
    EmrServerlessPage > .emr-body {
        height: 1fr;
        layout: horizontal;
    }
    EmrServerlessPage > .emr-body > JobRunsPane {
        width: 1fr;
    }
    EmrServerlessPage > .emr-body > JobRunDetailPane {
        width: 2fr;
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
        with Horizontal(classes="emr-top-strip"):
            yield self._picker
        with Horizontal(classes="emr-body"):
            yield self._left
            yield self._right

    def on_mount(self) -> None:
        # Initial load: applications + first-app's runs + first-run detail.
        self.run_worker(self._vm.setup(), exclusive=True, group="emr-setup")
        # Set up the three pollers per spec §6.
        self.set_interval(30.0, self._tick_applications, name="emr-poll-apps")
        self.set_interval(10.0, self._tick_runs, name="emr-poll-runs")
        self.set_interval(5.0, self._tick_detail, name="emr-poll-detail")

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
