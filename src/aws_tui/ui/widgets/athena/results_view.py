from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static

from aws_tui.ui.widgets.athena.load_more_button import AthenaLoadMoreButton
from aws_tui.ui.widgets.glue.detail_rows import state_placeholder
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState


class AthenaResultsView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaResultsView {
        height: 1fr;
        layout: grid;
        grid-size: 1 3;
        grid-rows: 1 1fr 3;
        grid-columns: 1fr;
    }
    AthenaResultsView > #athena-results-status,
    AthenaResultsView #athena-results-footer {
        width: 1fr;
        height: 1;
        padding: 0 1;
        text-overflow: ellipsis;
    }
    AthenaResultsView #athena-results-footer {
        text-align: right;
    }
    AthenaResultsView > #athena-results-controls {
        width: 1fr;
        height: 3;
        layout: horizontal;
    }
    AthenaResultsView > DataTable {
        width: 1fr;
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, vm: AthenaPageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="athena-service-view")
        self._vm = vm.results
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="athena-results-status", markup=False)
        yield DataTable(
            id="athena-results-table",
            cursor_type="row",
            zebra_stripes=True,
            header_height=2,
        )
        with Horizontal(id="athena-results-controls"):
            yield Static("", id="athena-results-footer", markup=False)
            yield AthenaLoadMoreButton(
                id="athena-more-results",
                tooltip="Load more result rows",
            )

    def on_mount(self) -> None:
        self._refresh()
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "athena-more-results":
            self.run_worker(
                self._vm.load_more(),
                exclusive=True,
                group="athena-more-results",
            )

    def _on_vm_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            table = self.query_one("#athena-results-table", DataTable)
            status = self.query_one("#athena-results-status", Static)
            footer = self.query_one("#athena-results-footer", Static)
            load_more = self.query_one(
                "#athena-more-results",
                AthenaLoadMoreButton,
            )
        except Exception:
            return
        table.clear(columns=True)
        for index, column in enumerate(self._vm.columns):
            table.add_column(
                Text(f"{column.name}\n{column.type_name}", no_wrap=True),
                key=f"athena-result-column-{index}",
            )
        for row in self._vm.rendered_rows:
            table.add_row(
                *(
                    Text(
                        "NULL" if cell.is_null else ('""' if cell.text == "" else cell.text),
                        style="dim italic" if cell.is_null else "",
                        no_wrap=True,
                    )
                    for cell in row
                )
            )
        placeholder = state_placeholder(
            self._vm.state,
            error_text=self._vm.error_text,
            empty_text="No results loaded",
        )
        if placeholder is None or self._vm.state is PaneState.IDLE:
            execution = self._vm.execution_id or "No execution"
            status.update(f"Execution {execution}")
        else:
            status.update(placeholder[0])
        status.set_class(self._vm.state is PaneState.FORBIDDEN, "-warning")
        status.set_class(self._vm.state is PaneState.ERROR, "-error")
        suffix = " · more available" if self._vm.has_more else ""
        footer.update(f"{len(self._vm.rows)} rows{suffix}")
        load_more.sync(
            has_more=self._vm.has_more,
            busy=self._vm.is_loading_more,
            state=self._vm.state,
            error_text=self._vm.error_text,
        )


__all__ = ["AthenaResultsView"]
