from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.await_remove import AwaitRemove
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Button, OptionList
from textual.worker import Worker

from aws_tui.domain.query import QueryState
from aws_tui.ui.widgets.athena.load_more_button import AthenaLoadMoreButton
from aws_tui.ui.widgets.glue.detail_rows import (
    DetailRows,
    DetailValue,
    ResourceListPane,
    display_time,
    display_value,
)
from aws_tui.vm.athena.page_vm import AthenaPageVM


class AthenaHistoryView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaHistoryView {
        height: 1fr;
        layout: grid;
        grid-size: 2 1;
        grid-columns: 4fr 6fr;
        grid-rows: 1fr;
    }
    AthenaHistoryView > .athena-history-detail {
        height: 1fr;
        layout: vertical;
    }
    AthenaHistoryView > .athena-history-list {
        height: 1fr;
        layout: vertical;
    }
    AthenaHistoryView > .athena-history-list > ResourceListPane {
        height: 1fr;
    }
    AthenaHistoryView > .athena-history-detail > DetailRows {
        height: 1fr;
    }
    AthenaHistoryView #athena-history-results {
        width: 5;
        min-width: 5;
        height: 3;
        margin: 0 1;
    }
    """

    def __init__(self, vm: AthenaPageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="athena-service-view")
        self._page_vm = vm
        self._vm = vm.history
        self._sub: DisposableBase | None = None
        self._removal_started = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="athena-history-list"):
            yield ResourceListPane(
                "query history",
                id="athena-history-pane",
                empty_text="No query history",
            )
            yield AthenaLoadMoreButton(
                id="athena-more-history",
                tooltip="Load more query history",
            )
        with Vertical(classes="athena-history-detail"):
            yield DetailRows("execution detail", id="athena-history-detail")
            yield Button(
                "↗",
                id="athena-history-results",
                compact=True,
                flat=True,
                tooltip="Open results",
            )

    def on_mount(self) -> None:
        self._removal_started = False
        self._refresh()
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def remove(self) -> AwaitRemove:
        self._removal_started = True
        return super().remove()

    def on_option_list_option_highlighted(
        self,
        event: OptionList.OptionHighlighted,
    ) -> None:
        option_id = event.option.id
        if option_id is None or option_id == "__placeholder__":
            return
        if option_id != self._vm.selected_execution_id:
            self._run_lifecycle_worker(
                partial(self._page_vm.select_history_execution, option_id),
                group="athena-select-history",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "athena-history-results":
            self._run_lifecycle_worker(
                self._page_vm.open_history_results,
                group="athena-open-history-results",
            )
        elif event.button.id == "athena-more-history":
            self._run_lifecycle_worker(
                self._vm.load_more,
                group="athena-more-history",
            )

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
        self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        if self._removal_started or not self.is_attached:
            return
        try:
            listing = self.query_one("#athena-history-pane", ResourceListPane)
            detail = self.query_one("#athena-history-detail", DetailRows)
            results = self.query_one("#athena-history-results", Button)
            load_more = self.query_one(
                "#athena-more-history",
                AthenaLoadMoreButton,
            )
        except Exception:
            return
        try:
            listing.replace(
                tuple(
                    (
                        row.ref.execution_id,
                        f"{row.state.value:<10}  {row.ref.execution_id}",
                    )
                    for row in self._vm.items
                ),
                selected_id=self._vm.selected_execution_id,
                state=self._vm.state,
                error_text=self._vm.error_text,
                has_more=self._vm.has_more,
            )
            detail.replace(
                self._detail_values(),
                state=self._vm.state,
                error_text=self._vm.error_text,
                empty_text="Select an execution",
            )
            selected = self._vm.detail
            results.disabled = (
                selected is None or selected.summary.state is not QueryState.SUCCEEDED
            )
            load_more.sync(
                has_more=self._vm.has_more,
                busy=self._vm.is_loading_more,
                state=self._vm.state,
                error_text=self._vm.error_text,
            )
        except NoMatches:
            return

    def _detail_values(self) -> tuple[DetailValue, ...]:
        detail = self._vm.detail
        if detail is None:
            return ()
        summary = detail.summary
        stats = detail.statistics
        rows = [
            DetailValue("Execution", summary.ref.execution_id),
            DetailValue("State", summary.state.value, f"-state-{summary.state.value.lower()}"),
            DetailValue("Submitted", display_time(summary.submitted_at)),
            DetailValue("Completed", display_time(summary.completed_at)),
            DetailValue("Statement", display_value(summary.statement_type)),
            DetailValue("Workgroup", detail.context.workgroup),
            DetailValue("Catalog", detail.context.catalog),
            DetailValue("Database", detail.context.database),
            DetailValue("Engine", display_value(detail.engine_version)),
            DetailValue("Result", display_value(detail.output_location)),
            DetailValue("State detail", display_value(detail.state_reason)),
            DetailValue("Queue", f"{display_value(stats.queue_ms)} ms"),
            DetailValue("Planning", f"{display_value(stats.planning_ms)} ms"),
            DetailValue("Engine time", f"{display_value(stats.engine_ms)} ms"),
            DetailValue("Service", f"{display_value(stats.service_ms)} ms"),
            DetailValue("Bytes scanned", display_value(stats.bytes_scanned)),
            DetailValue("Reused result", display_value(stats.reused_previous_result)),
        ]
        if detail.error is not None:
            rows.extend(
                (
                    DetailValue("Error category", display_value(detail.error.category), "-error"),
                    DetailValue("Error type", display_value(detail.error.error_type), "-error"),
                    DetailValue("Retryable", display_value(detail.error.retryable), "-error"),
                    DetailValue("Message", detail.error.message, "-error"),
                )
            )
        return tuple(rows)


__all__ = ["AthenaHistoryView"]
