from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Static, TextArea

from aws_tui.ui.widgets.glue.detail_rows import display_value
from aws_tui.vm.athena.page_vm import AthenaPageVM


class AthenaQueryView(Widget):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("tab", "focus_next", show=False),
        Binding("shift+tab", "focus_previous", show=False),
    ]

    DEFAULT_CSS: ClassVar[str] = """
    AthenaQueryView {
        height: 1fr;
        layout: grid;
        grid-size: 1 3;
        grid-rows: 5 1fr 7;
        grid-columns: 1fr;
    }
    AthenaQueryView > TextArea {
        width: 1fr;
        height: 1fr;
        scrollbar-size: 1 1;
    }
    AthenaQueryView > #athena-query-controls {
        width: 1fr;
        height: 5;
        layout: horizontal;
        padding: 0 1;
        border-title-align: left;
    }
    AthenaQueryView #athena-execute,
    AthenaQueryView #athena-cancel {
        width: 3;
        min-width: 3;
        height: 3;
        margin: 0 1 0 0;
    }
    AthenaQueryView #athena-query-status {
        width: 1fr;
        height: 3;
        content-align: left middle;
        text-overflow: ellipsis;
    }
    AthenaQueryView > #athena-query-detail {
        width: 1fr;
        height: 7;
        padding: 0 1;
        scrollbar-size: 1 1;
        border-title-align: left;
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
        with Horizontal(id="athena-query-controls"):
            yield Button(
                "▶",
                id="athena-execute",
                classes="-primary",
                compact=True,
                flat=True,
                tooltip="Run the valid read-only query",
            )
            yield Button(
                "■",
                id="athena-cancel",
                classes="-danger",
                compact=True,
                flat=True,
                tooltip="Stop query submission or the active query",
            )
            yield Static("", id="athena-query-status", markup=False)
        yield TextArea(
            self._vm.sql,
            language="sql",
            soft_wrap=True,
            tab_behavior="focus",
            show_line_numbers=True,
            placeholder="Enter a read-only query",
            id="athena-editor",
        )
        with VerticalScroll(id="athena-query-detail"):
            yield Static("", id="athena-query-detail-text", markup=False)

    def on_mount(self) -> None:
        self.query_one("#athena-editor", TextArea).border_title = "query editor"
        self.query_one("#athena-query-controls").border_title = "query controls"
        self.query_one("#athena-query-detail").border_title = "execution detail"
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

    def action_focus_next(self) -> None:
        self._move_focus(forward=True)

    def action_focus_previous(self) -> None:
        self._move_focus(forward=False)

    def insert_table_reference(self, identifier: str) -> bool:
        if not identifier:
            return False
        try:
            editor = self.query_one("#athena-editor", TextArea)
        except Exception:
            return False
        selection = editor.selection
        editor.replace(
            identifier,
            selection.start,
            selection.end,
            maintain_selection_offset=False,
        )
        self._vm.set_sql(editor.text)
        return True

    def refresh_from_vm(self) -> None:
        self._refresh()

    def _move_focus(self, *, forward: bool) -> None:
        targets: tuple[Widget, ...] = (
            self.query_one("#athena-editor", TextArea),
            self.query_one("#athena-execute", Button),
            self.query_one("#athena-cancel", Button),
            self.query_one("#athena-query-detail", VerticalScroll),
        )
        focus_chain = self.screen.focus_chain
        focused = self.app.focused
        try:
            index = targets.index(focused)
        except ValueError:
            return
        candidates = targets[index + 1 :] if forward else targets[:index][::-1]
        for target in candidates:
            if target in focus_chain:
                target.focus()
                return
        self._move_focus_outside_surface(focus_chain, forward=forward)

    def _move_focus_outside_surface(
        self,
        focus_chain: list[Widget],
        *,
        forward: bool,
    ) -> None:
        focused = self.app.focused
        if focused is None:
            return
        try:
            index = focus_chain.index(focused)
        except ValueError:
            return
        ordered = (
            [*focus_chain[index + 1 :], *focus_chain[:index]]
            if forward
            else [*focus_chain[:index][::-1], *focus_chain[index + 1 :][::-1]]
        )
        for target in ordered:
            if self not in target.ancestors_with_self:
                target.focus()
                return

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
        detail.set_class(
            self._vm.error_text is not None
            or self._page_vm.workgroup_detail_error_text is not None,
            "-error",
        )

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
            ("Workgroup", self._page_vm.workgroup_detail_error_text),
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
        workgroup = self._page_vm.workgroup_detail
        if workgroup is not None:
            managed = workgroup.managed_query_results_enabled
            rows.extend(
                (
                    f"{'Workgroup mode':<15} {'managed results' if managed else 'S3 output'}",
                    f"{'Configuration':<15} "
                    f"{'enforced' if workgroup.enforce_workgroup_configuration else 'caller configurable'}",
                    f"{'Workgroup output':<15} "
                    f"{'Athena managed' if managed else display_value(workgroup.output_location)}",
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
