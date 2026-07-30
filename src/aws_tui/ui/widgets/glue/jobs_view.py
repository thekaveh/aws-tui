from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import OptionList
from textual.worker import Worker

from aws_tui.ui.widgets.glue.detail_rows import (
    DetailRows,
    DetailValue,
    ResourceListPane,
    display_time,
    display_value,
)
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM


class GlueJobsView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    GlueJobsView {
        height: 1fr;
        layout: grid;
        grid-size: 3 1;
        grid-columns: 2fr 3fr 5fr;
        grid-rows: 1fr;
        grid-gutter: 0;
    }
    """

    def __init__(self, vm: GluePageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="glue-service-view")
        self._page_vm = vm
        self._vm = vm.jobs
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        yield ResourceListPane("jobs", id="glue-jobs-pane", empty_text="no jobs")
        yield ResourceListPane(
            "runs",
            id="glue-runs-pane",
            empty_text="no runs",
        )
        yield DetailRows("job and run detail", id="glue-job-detail-pane")

    def on_mount(self) -> None:
        self._refresh_all()
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def focus_targets(self) -> tuple[Widget, ...]:
        """Return the ordered, concrete targets for the Jobs focus ring."""
        return (
            self.query_one("#glue-jobs-pane", ResourceListPane).option_list,
            self.query_one("#glue-runs-pane", ResourceListPane).option_list,
            self.query_one("#glue-job-detail-pane", DetailRows).query_one(VerticalScroll),
        )

    def on_option_list_option_highlighted(
        self,
        event: OptionList.OptionHighlighted,
    ) -> None:
        option_id = event.option.id
        if option_id is None or option_id == "__placeholder__":
            return
        if event.option_list.id == "glue-jobs-pane-options":
            if option_id != self._vm.selected_job_name:
                self._run_lifecycle_worker(
                    partial(self._page_vm.select_job, option_id),
                    group="glue-select-job",
                )
        elif (
            event.option_list.id == "glue-runs-pane-options"
            and option_id != self._vm.selected_run_id
        ):
            self._page_vm.select_job_run(option_id)

    def _run_lifecycle_worker(
        self,
        work: Callable[[], Awaitable[None]],
        *,
        group: str,
    ) -> Worker[None]:
        async def deferred() -> None:
            await work()

        return self.run_worker(deferred, exclusive=True, group=group)

    def _on_vm_changed(self, _property_name: str) -> None:
        self._refresh_all()

    def _refresh_all(self) -> None:
        try:
            jobs = self.query_one("#glue-jobs-pane", ResourceListPane)
            runs = self.query_one("#glue-runs-pane", ResourceListPane)
            detail = self.query_one("#glue-job-detail-pane", DetailRows)
        except Exception:
            return
        jobs.replace(
            tuple((row.name, f"{row.name}  {row.command_name}") for row in self._vm.jobs),
            selected_id=self._vm.selected_job_name,
            state=self._vm.jobs_state,
            error_text=self._vm.jobs_error_text,
            has_more=self._vm.has_more_jobs,
        )
        runs.replace(
            tuple(
                (
                    row.run_id,
                    f"{row.state:<10}  {row.run_id}",
                )
                for row in self._vm.runs
            ),
            selected_id=self._vm.selected_run_id,
            state=self._vm.runs_state,
            error_text=self._vm.runs_error_text,
            has_more=self._vm.has_more_runs,
        )
        detail.replace(
            self._detail_values(),
            state=(
                PaneState.IDLE
                if self._vm.selected_job is not None and self._vm.runs_state is PaneState.EMPTY
                else self._vm.runs_state
            ),
            error_text=self._vm.runs_error_text,
            empty_text="select a job and run",
        )

    def _detail_values(self) -> tuple[DetailValue, ...]:
        job = self._vm.selected_job
        run = self._vm.selected_run
        if job is None:
            return ()
        rows = [
            DetailValue("Job", job.name),
            DetailValue("Role", job.role),
            DetailValue("Command", job.command_name),
            DetailValue("Script", display_value(job.script_location)),
            DetailValue("Glue version", display_value(job.glue_version)),
            DetailValue(
                "Workers", f"{display_value(job.worker_count)} x {display_value(job.worker_type)}"
            ),
        ]
        if run is None:
            return tuple(rows)
        rows.extend(
            (
                DetailValue("Run", run.run_id),
                DetailValue("State", run.state, f"-state-{run.state.lower()}"),
                DetailValue("Started", display_time(run.started_at)),
                DetailValue("Completed", display_time(run.completed_at)),
                DetailValue("Runtime", f"{display_value(run.execution_time_seconds)} s"),
                DetailValue("Attempt", str(run.attempt)),
                DetailValue("Error", display_value(run.error_message)),
                DetailValue("State detail", display_value(run.state_detail)),
                DetailValue("Log group", display_value(run.log_group_name)),
            )
        )
        return tuple(rows)


__all__ = ["GlueJobsView"]
