from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Static, TextArea

from aws_tui.ui.widgets.glue.detail_rows import display_value
from aws_tui.vm.athena.page_vm import AthenaPageVM


class AthenaQueryView(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaQueryView {
        height: 1fr;
        layout: grid;
        grid-size: 1 3;
        grid-rows: 1fr 3 6;
        grid-columns: 1fr;
    }
    AthenaQueryView > TextArea {
        width: 1fr;
        height: 1fr;
        border: solid transparent;
        scrollbar-size: 1 1;
    }
    AthenaQueryView > #athena-query-controls {
        width: 1fr;
        height: 3;
        layout: horizontal;
        padding: 0 1;
    }
    AthenaQueryView #athena-execute,
    AthenaQueryView #athena-cancel {
        width: 5;
        min-width: 5;
        height: 3;
        margin: 0 1 0 0;
    }
    AthenaQueryView #athena-query-status {
        width: 1fr;
        height: 3;
        padding: 1 1 0 1;
        text-overflow: ellipsis;
    }
    AthenaQueryView > #athena-query-detail {
        width: 1fr;
        height: 6;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    AthenaQueryView #athena-query-detail-text {
        width: 1fr;
        height: auto;
    }
    """

    def __init__(self, vm: AthenaPageVM, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="athena-service-view")
        self._page_vm = vm
        self._vm = vm.query
        self._sub: DisposableBase | None = None
        self._syncing_editor = False

    def compose(self) -> ComposeResult:
        yield TextArea(
            self._vm.sql,
            language="sql",
            soft_wrap=True,
            tab_behavior="focus",
            show_line_numbers=True,
            placeholder="Enter a read-only query",
            id="athena-editor",
        )
        with Horizontal(id="athena-query-controls"):
            yield Button(
                "▶",
                id="athena-execute",
                classes="-primary",
                compact=True,
                flat=True,
                tooltip="Execute query",
            )
            yield Button(
                "■",
                id="athena-cancel",
                classes="-danger",
                compact=True,
                flat=True,
                tooltip="Cancel active query",
            )
            yield Static("", id="athena-query-status", markup=False)
        with VerticalScroll(id="athena-query-detail"):
            yield Static("", id="athena-query-detail-text", markup=False)

    def on_mount(self) -> None:
        self._refresh()
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "athena-editor" or self._syncing_editor:
            return
        self._vm.set_sql(event.text_area.text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "athena-execute":
            self.run_worker(
                self._vm.execute(),
                exclusive=True,
                group="athena-query-execute",
            )
        elif event.button.id == "athena-cancel":
            self.run_worker(
                self._vm.cancel(),
                exclusive=True,
                group="athena-query-cancel",
            )

    async def execute(self) -> None:
        self._sync_sql_from_editor()
        await self._vm.execute()

    async def cancel(self) -> None:
        await self._vm.cancel()

    def refresh_from_vm(self) -> None:
        self._refresh()

    def _on_vm_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._refresh)

    def _sync_sql_from_editor(self) -> None:
        try:
            editor = self.query_one("#athena-editor", TextArea)
        except Exception:
            return
        self._vm.set_sql(editor.text)

    def _refresh(self) -> None:
        try:
            editor = self.query_one("#athena-editor", TextArea)
            execute = self.query_one("#athena-execute", Button)
            cancel = self.query_one("#athena-cancel", Button)
            status = self.query_one("#athena-query-status", Static)
            detail = self.query_one("#athena-query-detail-text", Static)
        except Exception:
            return
        if editor.text != self._vm.sql:
            self._syncing_editor = True
            try:
                editor.text = self._vm.sql
            finally:
                self._syncing_editor = False
        execute.disabled = not self._vm.execute_command.can_execute()
        cancel.disabled = not self._vm.cancel_command.can_execute()
        status.update(self._status_text())
        detail.update(self._detail_text())
        detail.set_class(self._vm.error_text is not None, "-error")

    def _status_text(self) -> str:
        ref = self._vm.execution_ref
        if self._vm.is_submitting:
            state = "SUBMITTING"
        elif self._vm.state is not None:
            state = self._vm.state.value
        elif self._vm.validation_error is not None:
            state = "INVALID QUERY"
        elif self._vm.error_text is not None:
            state = "REQUEST FAILED"
        elif not self._vm.sql:
            return "Enter a read-only query"
        else:
            return "Ready · read-only SQL"
        return state if ref is None else f"{state} · {ref.execution_id}"

    def _detail_text(self) -> str:
        rows: list[str] = []
        for label, error_text in (
            ("Workgroups", self._page_vm.workgroups_error_text),
            ("Catalogs", self._page_vm.catalogs_error_text),
            ("Databases", self._page_vm.databases_error_text),
        ):
            if error_text is not None:
                rows.append(f"{label:<15} {error_text}")
        if self._vm.validation_error is not None:
            rows.append(f"Validation      {self._vm.validation_error}")
        if self._vm.error_text is not None:
            rows.append(f"Request         {self._vm.error_text}")
        if self._vm.state_reason is not None:
            rows.append(f"State detail    {self._vm.state_reason}")
        error = self._vm.query_error
        if error is not None:
            rows.extend(
                (
                    f"Error category  {display_value(error.category)}",
                    f"Error type      {display_value(error.error_type)}",
                    f"Retryable       {display_value(error.retryable)}",
                    f"Message         {error.message}",
                )
            )
        stats = self._vm.statistics
        if self._vm.execution_ref is not None:
            rows.extend(
                (
                    f"Engine          {display_value(self._vm.engine_version)}",
                    f"Bytes scanned   {display_value(stats.bytes_scanned)}",
                    f"Queue           {display_value(stats.queue_ms)} ms",
                    f"Planning        {display_value(stats.planning_ms)} ms",
                    f"Engine time     {display_value(stats.engine_ms)} ms",
                    f"Result          {display_value(self._vm.output_location)}",
                )
            )
        return "\n".join(rows) if rows else "No execution yet"


__all__ = ["AthenaQueryView"]
