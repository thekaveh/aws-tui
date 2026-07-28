from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import OptionList

from aws_tui.ui.widgets.glue.detail_rows import (
    DetailRows,
    DetailValue,
    ResourceListPane,
    display_time,
    display_value,
)
from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from aws_tui.vm.glue.page_vm import GluePageVM


class GlueCatalogView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    GlueCatalogView {
        height: 1fr;
        layout: grid;
        grid-size: 3 1;
        grid-columns: 2fr 3fr 5fr;
        grid-rows: 1fr;
        grid-gutter: 0;
    }
    GlueCatalogView > #glue-table-detail-region {
        width: 1fr;
        height: 1fr;
        layout: vertical;
    }
    GlueCatalogView > #glue-table-detail-region > #glue-table-detail-pane {
        width: 1fr;
        height: 2fr;
        min-height: 6;
    }
    """

    def __init__(self, vm: GluePageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="glue-service-view")
        self._page_vm = vm
        self._vm = vm.catalog
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        yield ResourceListPane(
            "databases",
            id="glue-databases-pane",
            empty_text="no databases",
        )
        yield ResourceListPane(
            "tables",
            id="glue-tables-pane",
            empty_text="no tables",
        )
        with Vertical(id="glue-table-detail-region"):
            yield DetailRows("table detail", id="glue-table-detail-pane")
            yield GlueIcebergView(self._vm.iceberg, id="glue-iceberg-view")

    def on_mount(self) -> None:
        self._refresh_all()
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def on_option_list_option_highlighted(
        self,
        event: OptionList.OptionHighlighted,
    ) -> None:
        option_id = event.option.id
        if option_id is None or option_id == "__placeholder__":
            return
        if event.option_list.id == "glue-databases-pane-options":
            if option_id != self._vm.selected_database_name:
                self.run_worker(
                    self._page_vm.select_database(option_id),
                    exclusive=True,
                    group="glue-select-database",
                )
        elif (
            event.option_list.id == "glue-tables-pane-options"
            and option_id != self._vm.selected_table_name
        ):
            self.run_worker(
                self._page_vm.select_table(option_id),
                exclusive=True,
                group="glue-select-table",
            )

    def _on_vm_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._refresh_all)

    def _refresh_all(self) -> None:
        try:
            databases = self.query_one("#glue-databases-pane", ResourceListPane)
            tables = self.query_one("#glue-tables-pane", ResourceListPane)
            detail = self.query_one("#glue-table-detail-pane", DetailRows)
        except NoMatches:
            return
        try:
            databases.replace(
                tuple((row.ref.database_name, row.ref.database_name) for row in self._vm.databases),
                selected_id=self._vm.selected_database_name,
                state=self._vm.databases_state,
                error_text=self._vm.databases_error_text,
                has_more=self._vm.has_more_databases,
            )
            tables.replace(
                tuple(
                    (
                        row.ref.table_name,
                        f"{row.ref.table_name}  {display_value(row.table_type)}",
                    )
                    for row in self._vm.tables
                ),
                selected_id=self._vm.selected_table_name,
                state=self._vm.tables_state,
                error_text=self._vm.tables_error_text,
                has_more=self._vm.has_more_tables,
            )
            detail.replace(
                self._detail_values(),
                state=self._vm.detail_state,
                error_text=self._vm.detail_error_text,
                empty_text="select a table",
            )
        except NoMatches:
            return

    def _detail_values(self) -> tuple[DetailValue, ...]:
        detail = self._vm.table_detail
        if detail is None:
            return ()
        rows = [
            DetailValue("Database", detail.summary.ref.database_name),
            DetailValue("Table", detail.summary.ref.table_name),
            DetailValue("Type", display_value(detail.summary.table_type)),
            DetailValue("Format", detail.table_format.value),
            DetailValue("Classification", display_value(detail.classification)),
            DetailValue("Location", display_value(detail.storage.location)),
            DetailValue("Owner", display_value(detail.summary.owner)),
            DetailValue("Updated", display_time(detail.summary.updated_at)),
            DetailValue("Columns", str(len(detail.columns))),
        ]
        for column in detail.columns:
            marker = "partition" if column.partition_key else "column"
            rows.append(DetailValue(marker, f"{column.name}: {column.type_name}"))
        rows.append(DetailValue("Partitions", str(len(self._vm.partitions))))
        for partition in self._vm.partitions:
            rows.append(DetailValue("partition", " / ".join(partition.values)))
        rows.append(DetailValue("Statistics", str(len(self._vm.column_statistics))))
        return tuple(rows)


__all__ = ["GlueCatalogView"]
