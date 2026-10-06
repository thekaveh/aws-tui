"""Explicit read-only Glue comparison with independently owned side controls."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click, DescendantFocus, Resize
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, TextArea

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import ValidationError
from aws_tui.domain.table_comparison import ColumnValue, Side, format_value
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.context_picker import ContextOption, ContextPicker
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.comparison_vm import GlueComparisonVM
from aws_tui.vm.service_source_vm import ServiceSourceContext

_SIDES: tuple[Side, Side] = ("left", "right")


class GlueComparisonModal(DeferredWorkerMixin, ModalScreen[None]):
    """Presentation state only; revisions and provider ownership live in the VM."""

    DEFAULT_CSS = """
    GlueComparisonModal { align: center middle; }
    GlueComparisonModal > Vertical { width: 116; max-width: 100%; height: 100%; border: solid $accent; background: $surface; padding: 0 1; }
    GlueComparisonModal .comparison-title { height: 1; text-style: bold; }
    GlueComparisonModal #comparison-headers { height: 5; }
    GlueComparisonModal .comparison-heading { width: 1fr; height: 100%; }
    GlueComparisonModal .comparison-status { height: 2; text-style: bold; }
    GlueComparisonModal .comparison-header { width: 100%; height: 1fr; border: none; padding: 0; }
    GlueComparisonModal .comparison-header:focus { border: none; }
    GlueComparisonModal #comparison-controls { height: 21; min-height: 4; }
    GlueComparisonModal .comparison-selectors { height: auto; layout: horizontal; }
    GlueComparisonModal .comparison-side { width: 1fr; height: auto; }
    GlueComparisonModal .comparison-row { height: 3; }
    GlueComparisonModal ContextPicker { min-width: 12; }
    GlueComparisonModal Input { background: $surface; color: $text; border: solid $secondary; border-title-color: $text; width: 1fr; min-width: 12; }
    GlueComparisonModal ModalButton { min-width: 8; margin: 0; padding: 0 1; }
    GlueComparisonModal .comparison-actions { height: auto; layout: grid; grid-size: 3; grid-rows: 3; }
    GlueComparisonModal .comparison-actions ModalButton { width: 1fr; }
    GlueComparisonModal ContextPicker { border-title-color: $text; }
    GlueComparisonModal TextArea { color: $text; background: $surface; }
    GlueComparisonModal #comparison-mode { height: 1; }
    GlueComparisonModal #comparison-body { height: 1fr; min-height: 3; border: solid $secondary; }
    GlueComparisonModal #comparison-help { height: 2; }
    GlueComparisonModal.-narrow #comparison-headers { height: 10; layout: vertical; }
    GlueComparisonModal.-narrow .comparison-heading { width: 100%; height: 5; }
    GlueComparisonModal.-narrow #comparison-controls { height: 4; }
    GlueComparisonModal.-narrow #comparison-help { height: 1; }
    GlueComparisonModal.-narrow .comparison-selectors { layout: vertical; }
    GlueComparisonModal.-narrow .comparison-side { width: 100%; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", priority=True),
        Binding("ctrl+1", "source('left')", "Left source", priority=True),
        Binding("ctrl+2", "source('right')", "Right source", priority=True),
        Binding("ctrl+r", "refresh", "Refresh side", priority=True),
        Binding("ctrl+d", "differences", "Differences only", priority=True),
        Binding("ctrl+c", "copy", "Copy full summary", priority=True),
    ]

    def __init__(
        self,
        vm: GlueComparisonVM,
        *,
        pin_candidate: TableRef | None,
        copy: Callable[[str, str], None],
        initial_source: ServiceSourceContext | None = None,
    ) -> None:
        super().__init__()
        self.vm = vm
        self._pin_candidate = pin_candidate
        self._copy = copy
        self._initial_source = initial_source
        self._subscription: DisposableBase | None = None
        self._dismiss_requested = False
        self._focused_side: Side = "left"
        self._show_summary = False
        self._sources_error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "Glue table comparison · Left → Right", classes="comparison-title", markup=False
            )
            with Horizontal(id="comparison-headers"):
                for side in _SIDES:
                    with Vertical(classes="comparison-heading"):
                        yield Static(
                            "", id=f"{side}-status", classes="comparison-status", markup=False
                        )
                        yield TextArea(
                            id=f"{side}-header",
                            classes="comparison-header",
                            read_only=True,
                            soft_wrap=True,
                        )
            with VerticalScroll(id="comparison-controls"):
                with Horizontal(classes="comparison-selectors"):
                    for side in _SIDES:
                        with Vertical(id=f"{side}-controls", classes="comparison-side"):
                            yield ContextPicker(
                                f"{side.title()} source", (), selected=None, id=f"{side}-source"
                            )
                            with Horizontal(classes="comparison-row"):
                                region = Input(placeholder="AWS region", id=f"{side}-region")
                                region.border_title = f"{side.title()} region · AwsDataCatalog"
                                yield region
                                yield ModalButton("Apply region", button_id=f"{side}-apply")
                            with Horizontal(classes="comparison-row"):
                                yield ContextPicker(
                                    "Database", (), selected=None, id=f"{side}-database"
                                )
                                yield ModalButton("More DBs", button_id=f"{side}-databases")
                            with Horizontal(classes="comparison-row"):
                                yield ContextPicker("Table", (), selected=None, id=f"{side}-table")
                                yield ModalButton("More tables", button_id=f"{side}-tables")
                            with Horizontal(classes="comparison-row"):
                                yield ModalButton(
                                    f"Refresh {side.title()}", button_id=f"{side}-refresh"
                                )
                                yield ModalButton(
                                    f"Pin open → {side.title()}",
                                    button_id=f"{side}-pin",
                                    disabled=self._pin_candidate is None,
                                )
                with Vertical(classes="comparison-actions"):
                    yield ModalButton("Differences only: off", button_id="differences")
                    yield ModalButton("Copy full summary", button_id="copy", disabled=True)
                    yield ModalButton("View full summary", button_id="summary")
                    yield ModalButton("Close · Esc", button_id="close")
            yield Static("Select a table on each side", id="comparison-mode", markup=False)
            yield TextArea(id="comparison-body", read_only=True, soft_wrap=True)
            yield Static(
                "Ctrl+1/2 source · Ctrl+R refresh side · Ctrl+D differences\nCtrl+C full copy · Tab controls · Esc close selector / comparison",
                id="comparison-help",
                markup=False,
            )

    def on_mount(self) -> None:
        self._layout_width(self.size.width)
        self._subscription = self.vm.on_property_changed.subscribe(self._changed)
        self._redraw()
        if self._initial_source is not None:
            for side in _SIDES:
                source = self._initial_source
                self.query_one(f"#{side}-region", Input).value = source.region
                revision = self.vm.choose_source(side, source.connection_name, source.region)
                self._load(side, revision)
        self.action_source("left")

    def on_resize(self, event: Resize) -> None:
        self._layout_width(event.size.width)

    def _layout_width(self, width: int) -> None:
        self.set_class(width < 90, "-narrow")
        if self.is_mounted:
            self.query_one("#comparison-help", Static).update(
                "Ctrl:1/2 side R refresh D diff C copy · Tab · Esc"
                if width < 90
                else "Ctrl+1/2 source · Ctrl+R refresh side · Ctrl+D differences\nCtrl+C full copy · Tab controls · Esc close selector / comparison"
            )

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        for owner in event.widget.ancestors_with_self:
            if owner.id in {"left-controls", "left-header"}:
                self._focused_side = "left"
                return
            if owner.id in {"right-controls", "right-header"}:
                self._focused_side = "right"
                return

    def _changed(self, prop: str) -> None:
        if self.is_mounted and not self._dismiss_requested:
            sides: tuple[Side, ...] = tuple(side for side in _SIDES if side == prop)
            self._redraw(sides)

    def _button(self, name: str) -> ModalButton:
        return next(button for button in self.query(ModalButton) if button.button_id == name)

    def _enable(self, name: str, enabled: bool) -> None:
        button = self._button(name)
        button.disabled = not enabled
        button.can_focus = enabled

    def _sources(self) -> tuple[ServiceSourceContext, ...]:
        try:
            sources = self.vm.sources
        except ValidationError as exc:
            self._sources_error = str(exc)
            return ()
        self._sources_error = None
        return sources

    def _redraw(self, sides: tuple[Side, ...] = _SIDES) -> None:
        sources = self._sources() if sides else ()
        for side in sides:
            state = self.vm.side(side)
            source = self.query_one(f"#{side}-source", ContextPicker)
            source.set_options(
                tuple(ContextOption(item.label, item.connection_name) for item in sources),
                selected=state.connection_name,
            )
            source.set_state(error=self._sources_error is not None, tooltip=self._sources_error)
            database = self.query_one(f"#{side}-database", ContextPicker)
            db_options = {row.ref.database_name: row.ref.database_name for row in state.databases}
            if state.selected_database is not None:
                db_options[state.selected_database.database_name] = (
                    state.selected_database.database_name
                )
            database.set_options(
                tuple(ContextOption(label, value) for value, label in db_options.items()),
                selected=state.selected_database.database_name if state.selected_database else None,
            )
            database.set_state(loading=state.databases_loading, disabled=not db_options)
            table = self.query_one(f"#{side}-table", ContextPicker)
            table_options = {row.ref.table_name: row.ref.table_name for row in state.tables}
            if state.selected_table is not None:
                table_options[state.selected_table.table_name] = state.selected_table.table_name
            table.set_options(
                tuple(ContextOption(label, value) for value, label in table_options.items()),
                selected=state.selected_table.table_name if state.selected_table else None,
            )
            table.set_state(loading=state.tables_loading, disabled=not table_options)
            idle = state.state is not PaneState.LOADING
            self._enable(f"{side}-databases", idle and state.has_more_databases)
            self._enable(f"{side}-tables", idle and state.has_more_tables)
            self._enable(f"{side}-refresh", state.connection_name is not None)
            ref = state.selected_table
            fetched = state.snapshot.fetched_at.isoformat() if state.snapshot else "<not fetched>"
            self.query_one(f"#{side}-status", Static).update(
                f"{side.title()}: {state.status_text}\nFetched UTC: {fetched}"
            )
            database_name = (
                ref.database_name
                if ref
                else (
                    state.selected_database.database_name
                    if state.selected_database
                    else "<not selected>"
                )
            )
            header = [
                f"Source: {state.connection_name or '<not selected>'} · Region: {state.region or '<not selected>'}",
                f"Catalog: AwsDataCatalog · Database: {database_name}",
                f"Table: {ref.table_name if ref else '<not selected>'}",
            ]
            if state.error_text:
                header.append(state.error_text)
            if state.database_limit_reached or state.table_limit_reached:
                header.append("Listing limit reached; refine your selection")
            self._set_text(f"{side}-header", "\n".join(header))
        summary = self.vm.summary_text()
        self._enable("copy", summary is not None)
        self._button("copy").tooltip = (
            "Copy the complete comparison"
            if summary
            else "Fetch both tables to enable full-summary copy"
        )
        self._button("differences").update(
            f"Differences only: {'on' if self.vm.differences_only else 'off'}"
        )
        self._button("summary").update(
            "View comparison" if self._show_summary else "View full summary"
        )
        comparison = self.vm.comparison
        if self._show_summary:
            body = summary or "Fetch both tables to view the full summary."
            mode = "Full summary · read-only; select text to copy manually"
        elif comparison is not None:
            rows = comparison.visible_rows(self.vm.differences_only)
            body = (
                "\n\n".join(
                    f"{row.section} · {row.key} · {', '.join(change.value for change in row.changes)}\n  Left: {format_value(row.left)}\n  Right: {format_value(row.right)}"
                    for row in rows
                )
                or "No visible differences."
            )
            mode = (
                "Differences only · unavailable values retained"
                if self.vm.differences_only
                else "All rows · Left → Right"
            )
        else:
            lines = ["Select a table on each side. Each fetch is independent."]
            for side in _SIDES:
                state = self.vm.side(side)
                if state.snapshot is not None:
                    lines.append(
                        f"{side.title()} fetched: {len(state.snapshot.detail.columns)} columns; waiting for the other side."
                    )
                    detail = state.snapshot.detail
                    for label, columns in (
                        ("columns", detail.columns),
                        ("partition keys", detail.partition_keys),
                    ):
                        for position, column in enumerate(columns, 1):
                            lines.append(
                                f"{side.title()} {label}: {format_value(ColumnValue(column, position))}"
                            )
                    lines.append(
                        f"{side.title()} location: {format_value(detail.storage.location)}"
                    )
                if state.error_text:
                    lines.append(f"{side.title()}: {state.error_text}")
            body = "\n".join(lines)
            mode = "Full-summary copy needs two successful fetches"
        if self._sources_error:
            body = f"{self._sources_error}\n\n{body}"
        self.query_one("#comparison-mode", Static).update(mode)
        self._set_text("comparison-body", body)

    def _set_text(self, widget_id: str, text: str) -> None:
        widget = self.query_one(f"#{widget_id}", TextArea)
        if widget.text != text:
            widget.load_text(text)

    def _load(self, side: Side, revision: int) -> None:
        self._run_lifecycle_worker(
            partial(self.vm.load_revision, side, revision),
            group=f"comparison-{side}",
            exit_on_error=False,
        )

    def on_context_picker_open_changed(self, event: ContextPicker.OpenChanged) -> None:
        event.stop()
        if event.is_open:
            for picker in self.query(ContextPicker):
                if picker is not event.picker and picker.is_open:
                    picker.close(refocus=False)

    def on_context_picker_changed(self, event: ContextPicker.Changed) -> None:
        event.stop()
        if self._dismiss_requested:
            return
        for side in _SIDES:
            state = self.vm.side(side)
            if event.picker.id == f"{side}-source":
                source = next(
                    (item for item in self._sources() if item.connection_name == event.value), None
                )
                if source is None:
                    self._redraw()
                    return
                self.query_one(f"#{side}-region", Input).value = source.region
                self._load(side, self.vm.choose_source(side, source.connection_name, source.region))
            elif event.picker.id == f"{side}-database":
                ref = next(
                    (item.ref for item in state.databases if item.ref.database_name == event.value),
                    state.selected_database,
                )
                if ref is not None:
                    self._load(side, self.vm.choose_database(side, ref))
            elif event.picker.id == f"{side}-table":
                table = next(
                    (item.ref for item in state.tables if item.ref.table_name == event.value),
                    state.selected_table,
                )
                if table is not None:
                    self._load(side, self.vm.choose_table(side, table))

    def action_source(self, side: Side) -> None:
        self._focused_side = side
        picker = self.query_one(f"#{side}-source", ContextPicker)
        picker.scroll_visible()
        picker.focus()

    def action_refresh(self) -> None:
        if not self._dismiss_requested:
            side = self._focused_side
            self._load(side, self.vm.refresh(side))

    def action_differences(self) -> None:
        if not self._dismiss_requested:
            self.vm.toggle_differences_only()

    def action_copy(self) -> None:
        if not self._dismiss_requested:
            text = self.vm.summary_text()
            if text is not None:
                self._copy(text, "Glue comparison")

    def on_click(self, event: Click) -> None:
        if not isinstance(event.widget, ModalButton) or self._dismiss_requested:
            return
        event.stop()
        name = event.widget.button_id
        if name == "copy":
            self.action_copy()
        elif name == "close":
            self.action_close()
        elif name == "differences":
            self.action_differences()
        elif name == "summary":
            self._show_summary = not self._show_summary
            self._redraw()
            self.query_one("#comparison-body", TextArea).focus()
        else:
            for side in _SIDES:
                if not name.startswith(f"{side}-"):
                    continue
                self._focused_side = side
                state = self.vm.side(side)
                if name == f"{side}-apply" and state.connection_name is not None:
                    region = self.query_one(f"#{side}-region", Input).value
                    self._load(side, self.vm.choose_source(side, state.connection_name, region))
                elif name == f"{side}-pin" and self._pin_candidate is not None:
                    self.query_one(f"#{side}-region", Input).value = self._pin_candidate.region
                    self._load(side, self.vm.pin(side, self._pin_candidate))
                elif name == f"{side}-refresh":
                    self.action_refresh()
                elif name in {f"{side}-databases", f"{side}-tables"}:
                    load = (
                        self.vm.load_more_databases
                        if name.endswith("-databases")
                        else self.vm.load_more_tables
                    )
                    self._run_lifecycle_worker(
                        partial(load, side, state.revision),
                        group=f"comparison-{side}",
                        exit_on_error=False,
                    )

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, ModalButton):
            self.focused.press()
        elif isinstance(self.focused, ContextPicker):
            self.focused.open()
        elif isinstance(self.focused, OptionList):
            self.focused.action_select()

    def action_focus_next(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            self.focused.action_cursor_right()
        else:
            self.focus_next()

    def action_focus_prev(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            self.focused.action_cursor_left()
        else:
            self.focus_previous()

    def action_move_up(self) -> None:
        if isinstance(self.focused, (TextArea, OptionList)):
            self.focused.action_cursor_up()
        else:
            self.focus_previous()

    def action_move_down(self) -> None:
        if isinstance(self.focused, (TextArea, OptionList)):
            self.focused.action_cursor_down()
        else:
            self.focus_next()

    def close(self) -> None:
        """Synchronously stop intake, including when the app starts shutdown."""
        self._dismiss_requested = True
        if self._subscription is not None:
            self._subscription.dispose()
            self._subscription = None
        self.vm.close()

    def action_close(self) -> None:
        for picker in self.query(ContextPicker):
            if picker.is_open:
                picker.close()
                return
        self.close()
        if self in self.app.screen_stack:
            self.dismiss()

    async def shutdown(self) -> None:
        self.close()
        for side in _SIDES:
            self.workers.cancel_group(self, f"comparison-{side}")
        await self.vm.shutdown()

    async def on_unmount(self) -> None:
        await self.shutdown()
