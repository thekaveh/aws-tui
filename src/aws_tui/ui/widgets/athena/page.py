from __future__ import annotations

from typing import ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Button, DataTable, OptionList, Select, Static, TextArea
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.athena.results_view import AthenaResultsView
from aws_tui.ui.widgets.athena.saved_view import AthenaSavedView
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.athena.page_vm import AthenaPageVM, AthenaView
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.file_manager.pane_vm import PaneState

_VIEW_ORDER: tuple[AthenaView, ...] = ("query", "history", "results", "saved")


class _ViewTab(Static, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "select", "Select", show=False),
    ]

    class Selected(TextualMessage):
        def __init__(self, view: AthenaView) -> None:
            super().__init__()
            self.view = view

    def __init__(self, view: AthenaView, number: int) -> None:
        super().__init__(
            f"{number} {view}",
            id=f"athena-tab-{view}",
            classes="athena-view-tab",
            markup=False,
        )
        self.view = view

    def on_click(self, _event: Click) -> None:
        self.post_message(self.Selected(self.view))

    def action_select(self) -> None:
        self.post_message(self.Selected(self.view))


class AthenaPage(HubSubscriberMixin, Widget):
    DEFAULT_CSS: ClassVar[str] = """
    AthenaPage {
        height: 1fr;
        layout: vertical;
    }
    AthenaPage > #athena-context-header {
        width: 1fr;
        height: 3;
        layout: horizontal;
    }
    AthenaPage #athena-source-header {
        width: 2fr;
        min-width: 22;
        height: 3;
        padding: 1 1 0 1;
    }
    AthenaPage #athena-context-header > Select {
        width: 2fr;
        min-width: 14;
        height: 3;
    }
    AthenaPage > #athena-view-tabs {
        width: 1fr;
        height: 1;
        layout: horizontal;
    }
    AthenaPage .athena-view-tab {
        width: 1fr;
        height: 1;
        padding: 0 1;
        content-align: center middle;
    }
    AthenaPage > #athena-view-host,
    AthenaPage .athena-service-view {
        width: 1fr;
        height: 1fr;
    }
    AthenaPage ResourceListPane,
    AthenaPage DetailRows {
        height: 1fr;
        border: solid transparent;
    }
    AthenaPage .glue-list-footer,
    AthenaPage .glue-detail-placeholder {
        height: 1;
    }
    AthenaPage .glue-detail-row {
        height: auto;
    }
    """

    def __init__(
        self,
        vm: AthenaPageVM,
        *,
        hub: MessageHub[Message],
        focus_coordinator: FocusCoordinatorVM | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm = vm
        self._hub = hub
        self._focus_coordinator = focus_coordinator
        self._syncing_context = False
        self._context_options: dict[str, tuple[str, ...]] = {}

    @property
    def vm(self) -> AthenaPageVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Horizontal(id="athena-context-header"):
            yield ServiceSourceHeader(self._vm.source, id="athena-source-header")
            yield Select(
                (),
                prompt="workgroup",
                allow_blank=True,
                compact=True,
                id="athena-workgroup",
                tooltip="Athena workgroup",
            )
            yield Select(
                (),
                prompt="catalog",
                allow_blank=True,
                compact=True,
                id="athena-catalog",
                tooltip="Data catalog",
            )
            yield Select(
                (),
                prompt="database",
                allow_blank=True,
                compact=True,
                id="athena-database",
                tooltip="Database",
            )
        with Horizontal(id="athena-view-tabs"):
            for number, view in enumerate(_VIEW_ORDER, start=1):
                tab = _ViewTab(view, number)
                tab.set_class(view == self._vm.active_view, "-active")
                yield tab
        with Container(id="athena-view-host"):
            views: tuple[tuple[AthenaView, Widget], ...] = (
                ("query", AthenaQueryView(self._vm, id="athena-query-view")),
                ("history", AthenaHistoryView(self._vm, id="athena-history-view")),
                ("results", AthenaResultsView(self._vm, id="athena-results-view")),
                ("saved", AthenaSavedView(self._vm, id="athena-saved-view")),
            )
            for view, child in views:
                child.display = view == self._vm.active_view
                yield child

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=(
                "active_view",
                "context",
                "workgroups",
                "catalogs",
                "databases",
                "workgroups_state",
                "catalogs_state",
                "databases_state",
                "workgroups_error_text",
                "catalogs_error_text",
                "databases_error_text",
            ),
            on_property_changed=self._on_page_changed,
        )
        self.call_after_refresh(self._refresh_page)
        self.call_after_refresh(self._maybe_focus_active)

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    async def on__view_tab_selected(self, event: _ViewTab.Selected) -> None:
        await self.action_select_view(event.view)

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._syncing_context or event.value is Select.NULL:
            return
        value = str(event.value)
        if event.select.id == "athena-workgroup" and value != self._vm.context.workgroup:
            self.run_worker(
                self._vm.select_workgroup(value),
                exclusive=True,
                group="athena-context",
            )
        elif event.select.id == "athena-catalog" and value != self._vm.context.catalog:
            self.run_worker(
                self._vm.select_catalog(value),
                exclusive=True,
                group="athena-context",
            )
        elif event.select.id == "athena-database" and value != self._vm.context.database:
            self.run_worker(
                self._vm.select_database(value),
                exclusive=True,
                group="athena-context",
            )

    async def action_select_view(self, view: str) -> None:
        selected = cast(AthenaView, view)
        if selected not in _VIEW_ORDER:
            return
        await self._vm.select_view(selected)
        self._sync_view()
        self.call_after_refresh(self._maybe_focus_active)

    async def action_execute(self) -> None:
        await self.query_one(AthenaQueryView).execute()

    async def action_cancel(self) -> None:
        await self.query_one(AthenaQueryView).cancel()

    async def action_refresh_active(self) -> None:
        active = self._vm.active_view
        if active == "query":
            await self._vm.refresh_workgroups()
        elif active == "history":
            await self._vm.history.refresh()
        elif active == "results":
            if self._vm.results.execution_id is not None:
                await self._vm.results.load(self._vm.results.execution_id)
        else:
            await self._vm.saved.setup()

    def cycle_focus(self, *, reverse: bool) -> None:
        if reverse:
            self.app.action_focus_previous()
        else:
            self.app.action_focus_next()

    def move_focused(self, delta: int) -> None:
        focused = self.app.focused
        if not self._contains_focus(focused):
            return
        if isinstance(focused, TextArea | DataTable | OptionList):
            if delta < 0:
                focused.action_cursor_up()
            else:
                focused.action_cursor_down()
        elif isinstance(focused, Select):
            focused.action_show_overlay()

    def activate_focused(self) -> bool:
        focused = self.app.focused
        if not self._contains_focus(focused):
            return False
        if isinstance(focused, TextArea):
            focused.insert("\n")
        elif isinstance(focused, _ViewTab):
            focused.action_select()
        elif isinstance(focused, Select):
            focused.action_show_overlay()
        elif isinstance(focused, OptionList):
            focused.action_select()
        elif isinstance(focused, DataTable):
            focused.action_select_cursor()
        elif isinstance(focused, Button):
            focused.press()
        return True

    def delete_focused(self) -> bool:
        focused = self.app.focused
        if not self._contains_focus(focused):
            return False
        if isinstance(focused, TextArea):
            focused.action_delete_left()
            return True
        return False

    def _contains_focus(self, focused: Widget | None) -> bool:
        return focused is not None and (focused is self or self in focused.ancestors_with_self)

    def _on_page_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._refresh_page)

    def _refresh_page(self) -> None:
        self._sync_context()
        self._sync_view()
        self.query_one(AthenaQueryView).refresh_from_vm()

    def _sync_context(self) -> None:
        try:
            workgroup = self.query_one("#athena-workgroup", Select)
            catalog = self.query_one("#athena-catalog", Select)
            database = self.query_one("#athena-database", Select)
        except Exception:
            return
        self._syncing_context = True
        try:
            self._replace_select(
                workgroup,
                tuple(row.name for row in self._vm.workgroups),
                self._vm.context.workgroup,
                self._vm.workgroups_state,
            )
            self._replace_select(
                catalog,
                tuple(row.name for row in self._vm.catalogs),
                self._vm.context.catalog,
                self._vm.catalogs_state,
            )
            self._replace_select(
                database,
                tuple(row.ref.database_name for row in self._vm.databases),
                self._vm.context.database,
                self._vm.databases_state,
            )
        finally:
            self._syncing_context = False

    def _replace_select(
        self,
        select: Select[str],
        values: tuple[str, ...],
        selected: str,
        state: PaneState,
    ) -> None:
        key = select.id or ""
        if self._context_options.get(key) != values:
            select.set_options((Text(value), value) for value in values)
            self._context_options[key] = values
        select.disabled = state is not PaneState.IDLE or not values
        if selected in values:
            select.value = selected
        else:
            select.clear()
        error_text = {
            "athena-workgroup": self._vm.workgroups_error_text,
            "athena-catalog": self._vm.catalogs_error_text,
            "athena-database": self._vm.databases_error_text,
        }.get(key)
        select.tooltip = error_text
        select.set_class(state is PaneState.FORBIDDEN, "-warning")
        select.set_class(state is PaneState.ERROR, "-error")

    def _sync_view(self) -> None:
        active = self._vm.active_view
        for view in _VIEW_ORDER:
            try:
                child = self.query_one(f"#athena-{view}-view")
                tab = self.query_one(f"#athena-tab-{view}", _ViewTab)
            except Exception:
                continue
            child.display = view == active
            tab.set_class(view == active, "-active")

    def _maybe_focus_active(self) -> None:
        focused = self.app.focused
        if (
            self._focus_coordinator is not None
            and focused is not None
            and not self.has_focus_within
            and self._focus_coordinator.focused_slot is FocusSlot.NAV_MENU
        ):
            return
        if focused is not None and not self.has_focus_within:
            return
        active = self.query_one(f"#athena-{self._vm.active_view}-view")
        focusable = next(
            iter(active.query("TextArea, DataTable, OptionList, Button, Select")),
            None,
        )
        if focusable is not None:
            focusable.focus()


__all__ = ["AthenaPage"]
