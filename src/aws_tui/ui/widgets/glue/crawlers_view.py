from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
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
from aws_tui.vm.glue.page_vm import GluePageVM


class GlueCrawlersView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    GlueCrawlersView {
        height: 1fr;
        layout: grid;
        grid-size: 2 1;
        grid-columns: 4fr 6fr;
        grid-rows: 1fr;
        grid-gutter: 0;
    }
    """

    def __init__(self, vm: GluePageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="glue-service-view")
        self._page_vm = vm
        self._vm = vm.crawlers
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        yield ResourceListPane(
            "crawlers",
            id="glue-crawlers-pane",
            empty_text="no crawlers",
        )
        yield DetailRows("crawler detail", id="glue-crawler-detail-pane")

    def on_mount(self) -> None:
        self._refresh_all()
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def focus_targets(self) -> tuple[Widget, ...]:
        """Return the ordered, concrete targets for the Crawlers focus ring."""
        return (
            self.query_one("#glue-crawlers-pane", ResourceListPane).option_list,
            self.query_one("#glue-crawler-detail-pane", DetailRows).query_one(VerticalScroll),
        )

    def on_option_list_option_highlighted(
        self,
        event: OptionList.OptionHighlighted,
    ) -> None:
        option_id = event.option.id
        if option_id is None or option_id == "__placeholder__":
            return
        if (
            event.option_list.id == "glue-crawlers-pane-options"
            and option_id != self._vm.selected_crawler_name
        ):
            self._run_lifecycle_worker(
                partial(self._page_vm.select_crawler, option_id),
                group="glue-select-crawler",
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
        self.call_after_refresh(self._refresh_all)

    def _refresh_all(self) -> None:
        try:
            crawlers = self.query_one("#glue-crawlers-pane", ResourceListPane)
            detail = self.query_one("#glue-crawler-detail-pane", DetailRows)
        except NoMatches:
            return
        # The panes resolved above can still be mounted while their inner
        # OptionList has already been removed: `ResourceListPane.replace`
        # calls `query_one(OptionList)`, so `NoMatches` escapes into the
        # Textual message pump and takes the app down. `glue/catalog_view`
        # and `athena/history_view` already double-guard for this reason.
        try:
            crawlers.replace(
                tuple(
                    (
                        row.name,
                        f"{row.state:<9}  {row.name}",
                    )
                    for row in self._vm.crawlers
                ),
                selected_id=self._vm.selected_crawler_name,
                state=self._vm.state,
                error_text=self._vm.error_text,
                has_more=self._vm.has_more_crawlers,
                limit_reached=self._vm.limit_reached,
            )
            detail.replace(
                self._detail_values(),
                state=self._vm.detail_state,
                error_text=self._vm.detail_error_text,
                empty_text="select a crawler",
            )
        except NoMatches:
            return

    def _detail_values(self) -> tuple[DetailValue, ...]:
        detail = self._vm.crawler_detail
        if detail is None:
            return ()
        summary = detail.summary
        rows = [
            DetailValue("Crawler", summary.name),
            DetailValue("State", summary.state, f"-state-{summary.state.lower()}"),
            DetailValue("Role", summary.role),
            DetailValue("Database", display_value(summary.database_name)),
            DetailValue("Schedule", display_value(summary.schedule_expression)),
            DetailValue("Last crawl", display_value(detail.last_crawl_status)),
            DetailValue("Started", display_time(detail.last_crawl_started_at)),
            DetailValue("Duration", f"{display_value(detail.last_crawl_duration_seconds)} s"),
            DetailValue("Error", display_value(detail.last_crawl_error)),
            DetailValue("Recrawl", display_value(detail.recrawl_behavior)),
            DetailValue("Schema update", display_value(detail.schema_update_behavior)),
            DetailValue("Schema delete", display_value(detail.schema_delete_behavior)),
        ]
        for target in detail.targets:
            rows.append(DetailValue("Target", target))
        if detail.metrics is not None:
            rows.extend(
                (
                    DetailValue("Tables created", str(detail.metrics.tables_created)),
                    DetailValue("Tables updated", str(detail.metrics.tables_updated)),
                    DetailValue("Tables deleted", str(detail.metrics.tables_deleted)),
                )
            )
        for warning in detail.supplemental_warnings:
            rows.append(DetailValue("Warning", warning, "-warning"))
        return tuple(rows)


__all__ = ["GlueCrawlersView"]
