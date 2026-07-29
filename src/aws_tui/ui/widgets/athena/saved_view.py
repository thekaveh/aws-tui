from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Button, OptionList

from aws_tui.ui.widgets.athena.load_more_button import AthenaLoadMoreButton
from aws_tui.ui.widgets.glue.detail_rows import (
    DetailRows,
    DetailValue,
    ResourceListPane,
    display_time,
    display_value,
)
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.athena.saved_vm import SavedQueryKind


class AthenaSavedView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaSavedView {
        height: 1fr;
        layout: grid;
        grid-size: 3 1;
        grid-columns: 2fr 2fr 6fr;
        grid-rows: 1fr;
    }
    AthenaSavedView > .athena-saved-detail {
        height: 1fr;
        layout: vertical;
    }
    AthenaSavedView > .athena-saved-list {
        height: 1fr;
        layout: vertical;
    }
    AthenaSavedView > .athena-saved-list > ResourceListPane {
        height: 1fr;
    }
    AthenaSavedView > .athena-saved-detail > DetailRows {
        height: 1fr;
    }
    AthenaSavedView #athena-open-editor {
        width: 5;
        min-width: 5;
        height: 3;
        margin: 0 1;
    }
    """

    def __init__(self, vm: AthenaPageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="athena-service-view")
        self._page_vm = vm
        self._vm = vm.saved
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="athena-saved-list"):
            yield ResourceListPane(
                "named queries",
                id="athena-named-pane",
                empty_text="No named queries",
            )
            yield AthenaLoadMoreButton(
                id="athena-more-named",
                tooltip="Load more named queries",
            )
        with Vertical(classes="athena-saved-list"):
            yield ResourceListPane(
                "prepared",
                id="athena-prepared-pane",
                empty_text="No prepared statements",
            )
            yield AthenaLoadMoreButton(
                id="athena-more-prepared",
                tooltip="Load more prepared statements",
            )
        with Vertical(classes="athena-saved-detail"):
            yield DetailRows("saved query detail", id="athena-saved-detail")
            yield Button(
                "↗",
                id="athena-open-editor",
                compact=True,
                flat=True,
                tooltip="Open in query editor",
            )

    def on_mount(self) -> None:
        self._refresh()
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
        if event.option_list.id == "athena-named-pane-options":
            if (
                self._vm.selected_kind is not SavedQueryKind.NAMED
                or option_id != self._vm.selected_query_id
            ):
                self.run_worker(
                    self._page_vm.select_named_query(option_id),
                    exclusive=True,
                    group="athena-select-saved",
                )
        elif event.option_list.id == "athena-prepared-pane-options" and (
            self._vm.selected_kind is not SavedQueryKind.PREPARED
            or option_id != self._vm.selected_query_id
        ):
            self.run_worker(
                self._page_vm.select_prepared_statement(option_id),
                exclusive=True,
                group="athena-select-saved",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "athena-open-editor":
            self.run_worker(
                self._page_vm.open_saved_in_editor(),
                exclusive=True,
                group="athena-open-saved",
            )
        elif event.button.id == "athena-more-named":
            self.run_worker(
                self._vm.load_more_named_queries(),
                exclusive=True,
                group="athena-more-named",
            )
        elif event.button.id == "athena-more-prepared":
            self.run_worker(
                self._vm.load_more_prepared_statements(),
                exclusive=True,
                group="athena-more-prepared",
            )

    def _on_vm_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            named = self.query_one("#athena-named-pane", ResourceListPane)
            prepared = self.query_one("#athena-prepared-pane", ResourceListPane)
            detail = self.query_one("#athena-saved-detail", DetailRows)
            open_editor = self.query_one("#athena-open-editor", Button)
            load_more_named = self.query_one(
                "#athena-more-named",
                AthenaLoadMoreButton,
            )
            load_more_prepared = self.query_one(
                "#athena-more-prepared",
                AthenaLoadMoreButton,
            )
        except Exception:
            return
        named.replace(
            tuple(
                (
                    row.query_id,
                    f"{row.name}  {row.database}",
                )
                for row in self._vm.named_queries
            ),
            selected_id=(
                self._vm.selected_query_id
                if self._vm.selected_kind is SavedQueryKind.NAMED
                else None
            ),
            state=self._vm.named_state,
            error_text=self._vm.named_error_text,
            has_more=self._vm.has_more_named_queries,
        )
        prepared.replace(
            tuple(
                (
                    row.name,
                    f"{row.name}  {display_time(row.last_modified_at)}",
                )
                for row in self._vm.prepared_statements
            ),
            selected_id=(
                self._vm.selected_query_id
                if self._vm.selected_kind is SavedQueryKind.PREPARED
                else None
            ),
            state=self._vm.prepared_state,
            error_text=self._vm.prepared_error_text,
            has_more=self._vm.has_more_prepared_statements,
        )
        detail.replace(
            self._detail_values(),
            state=self._vm.detail_state,
            error_text=self._vm.detail_error_text,
            empty_text="Select a saved query",
        )
        open_editor.disabled = self._vm.selected_sql() is None
        load_more_named.sync(
            has_more=self._vm.has_more_named_queries,
            busy=self._vm.is_loading_more_named_queries,
            state=self._vm.named_state,
            error_text=self._vm.named_error_text,
        )
        load_more_prepared.sync(
            has_more=self._vm.has_more_prepared_statements,
            busy=self._vm.is_loading_more_prepared_statements,
            state=self._vm.prepared_state,
            error_text=self._vm.prepared_error_text,
        )

    def _detail_values(self) -> tuple[DetailValue, ...]:
        named = self._vm.selected_named_query
        if named is not None:
            return (
                DetailValue("Kind", "named query"),
                DetailValue("Name", named.name),
                DetailValue("Description", display_value(named.description)),
                DetailValue("Database", named.database),
                DetailValue("Workgroup", named.workgroup),
                DetailValue("SQL", named.query_string),
            )
        prepared = self._vm.selected_prepared_statement
        if prepared is not None:
            return (
                DetailValue("Kind", "prepared statement"),
                DetailValue("Name", prepared.name),
                DetailValue("Description", display_value(prepared.description)),
                DetailValue("Workgroup", prepared.workgroup),
                DetailValue("Modified", display_time(prepared.last_modified_at)),
                DetailValue("SQL", prepared.query_statement),
            )
        return ()


__all__ = ["AthenaSavedView"]
