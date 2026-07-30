from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Button, DataTable, OptionList, Select, TextArea
from textual.worker import Worker
from vmx import Message, MessageHub

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.athena.load_more_button import AthenaLoadMoreButton
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.athena.results_view import AthenaResultsView
from aws_tui.ui.widgets.athena.saved_view import AthenaSavedView
from aws_tui.ui.widgets.glue.detail_rows import DetailRows, ResourceListPane
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.athena.page_vm import AthenaPageVM, AthenaView
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.file_manager.pane_vm import PaneState

_VIEW_ORDER: tuple[AthenaView, ...] = ("query", "history", "results", "saved")
_ContextControls = tuple[
    tuple[Select[str], Select[str], Select[str]],
    tuple[AthenaLoadMoreButton, AthenaLoadMoreButton, AthenaLoadMoreButton],
]


class _FocusableSourceHeader(ServiceSourceHeader, can_focus=True):
    """Temporary focus bridge until Task 3 supplies the source picker."""


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
    AthenaPage #athena-context-header > AthenaLoadMoreButton {
        width: 3;
        min-width: 3;
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
        keymap: KeymapStore | None = None,
        focus_coordinator: FocusCoordinatorVM | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm = vm
        self._hub = hub
        self._keymap = keymap or KeymapStore()
        self._focus_coordinator = focus_coordinator
        self._syncing_context = False
        self._context_options: dict[str, tuple[str, ...]] = {}

    @property
    def vm(self) -> AthenaPageVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Horizontal(id="athena-context-header"):
            yield _FocusableSourceHeader(self._vm.source, id="athena-source-header")
            yield Select(
                (),
                prompt="workgroup",
                allow_blank=True,
                compact=True,
                id="athena-workgroup",
                tooltip="Athena workgroup",
            )
            yield AthenaLoadMoreButton(
                id="athena-more-workgroups",
                tooltip="Load more workgroups",
            )
            yield Select(
                (),
                prompt="catalog",
                allow_blank=True,
                compact=True,
                id="athena-catalog",
                tooltip="Data catalog",
            )
            yield AthenaLoadMoreButton(
                id="athena-more-catalogs",
                tooltip="Load more data catalogs",
            )
            yield Select(
                (),
                prompt="database",
                allow_blank=True,
                compact=True,
                id="athena-database",
                tooltip="Database",
            )
            yield AthenaLoadMoreButton(
                id="athena-more-databases",
                tooltip="Load more databases",
            )
        yield ServiceTabStrip(
            tuple(
                (
                    view,
                    f"{keys[0] if keys else ''} {view}".strip(),
                )
                for view in _VIEW_ORDER
                for keys in (self._keymap.resolve(f"athena.{view}"),)
            ),
            active=self._vm.active_view,
            id="athena-view-tabs",
            tab_id_prefix="athena-tab",
        )
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
                "workgroup_detail",
                "workgroup_detail_state",
                "workgroup_detail_error_text",
                "is_loading_more_workgroups",
                "is_loading_more_catalogs",
                "is_loading_more_databases",
            ),
            on_property_changed=self._on_page_changed,
        )
        self.call_after_refresh(self._refresh_page)
        self.call_after_refresh(self._maybe_focus_active)

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    async def on_service_tab_strip_changed(self, event: ServiceTabStrip.Changed) -> None:
        await self.action_select_view(event.value)

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._syncing_context or event.value is Select.NULL:
            return
        value = str(event.value)
        if event.select.id == "athena-workgroup" and value != self._vm.context.workgroup:
            self._run_lifecycle_worker(
                partial(self._vm.select_workgroup, value),
                group="athena-context",
            )
        elif event.select.id == "athena-catalog" and value != self._vm.context.catalog:
            self._run_lifecycle_worker(
                partial(self._vm.select_catalog, value),
                group="athena-context",
            )
        elif event.select.id == "athena-database" and value != self._vm.context.database:
            self._run_lifecycle_worker(
                partial(self._vm.select_database, value),
                group="athena-context",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        loaders = {
            "athena-more-workgroups": self._vm.load_more_workgroups,
            "athena-more-catalogs": self._vm.load_more_catalogs,
            "athena-more-databases": self._vm.load_more_databases,
        }
        loader = loaders.get(event.button.id or "")
        if loader is not None:
            self._run_lifecycle_worker(
                loader,
                group=f"{event.button.id}-load",
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

    async def action_load_more(self) -> None:
        focused_ids = self._focused_ids()
        if focused_ids & {"athena-workgroup", "athena-more-workgroups"}:
            await self._vm.load_more_workgroups()
        elif focused_ids & {"athena-catalog", "athena-more-catalogs"}:
            await self._vm.load_more_catalogs()
        elif focused_ids & {"athena-database", "athena-more-databases"}:
            await self._vm.load_more_databases()
        elif self._vm.active_view == "history":
            await self._vm.history.load_more()
        elif self._vm.active_view == "results":
            await self._vm.results.load_more()
        elif self._vm.active_view == "saved":
            if focused_ids & {
                "athena-prepared-pane-options",
                "athena-more-prepared",
            }:
                await self._vm.saved.load_more_prepared_statements()
            else:
                await self._vm.saved.load_more_named_queries()
        elif self._vm.active_view == "query":
            if self._vm.has_more_databases:
                await self._vm.load_more_databases()
            elif self._vm.has_more_catalogs:
                await self._vm.load_more_catalogs()
            elif self._vm.has_more_workgroups:
                await self._vm.load_more_workgroups()

    def can_load_more(self) -> bool:
        focused_ids = self._focused_ids()
        if focused_ids & {"athena-workgroup", "athena-more-workgroups"}:
            return self._vm.has_more_workgroups and not self._vm.is_loading_more_workgroups
        if focused_ids & {"athena-catalog", "athena-more-catalogs"}:
            return self._vm.has_more_catalogs and not self._vm.is_loading_more_catalogs
        if focused_ids & {"athena-database", "athena-more-databases"}:
            return self._vm.has_more_databases and not self._vm.is_loading_more_databases
        if self._vm.active_view == "history":
            return self._vm.history.has_more and not self._vm.history.is_loading_more
        if self._vm.active_view == "results":
            return self._vm.results.has_more and not self._vm.results.is_loading_more
        if self._vm.active_view == "saved":
            if focused_ids & {
                "athena-prepared-pane-options",
                "athena-more-prepared",
            }:
                return (
                    self._vm.saved.has_more_prepared_statements
                    and not self._vm.saved.is_loading_more_prepared_statements
                )
            return (
                self._vm.saved.has_more_named_queries
                and not self._vm.saved.is_loading_more_named_queries
            )
        return any(
            (
                self._vm.has_more_databases and not self._vm.is_loading_more_databases,
                self._vm.has_more_catalogs and not self._vm.is_loading_more_catalogs,
                self._vm.has_more_workgroups and not self._vm.is_loading_more_workgroups,
            )
        )

    async def action_refresh_active(self) -> None:
        active = self._vm.active_view
        if active == "query":
            await self._vm.refresh_query_context()
        elif active == "history":
            await self._vm.history.refresh()
        elif active == "results":
            if self._vm.results.execution_id is not None:
                await self._vm.results.load(self._vm.results.execution_id)
        else:
            await self._vm.saved.setup()

    def cycle_focus(self, *, reverse: bool) -> None:
        if self._focus_coordinator is None:
            return
        targets = self._focus_targets()
        slot = self._focus_coordinator.cycle_focus_ring(
            tuple(slot for slot, _widget in targets),
            reverse=reverse,
        )
        self._project_focus_slot(slot, targets=targets)

    def focus_default(self) -> None:
        """Focus the active view's primary operational target."""
        self._project_focus_slot(FocusSlot.ATHENA_PRIMARY)

    def _focus_targets(self) -> tuple[tuple[FocusSlot, Widget], ...]:
        targets: list[tuple[FocusSlot, Widget]] = [
            (FocusSlot.ATHENA_SOURCE, self.query_one("#athena-source-header", Widget)),
        ]
        for slot, selector in (
            (FocusSlot.ATHENA_WORKGROUP, "#athena-workgroup"),
            (FocusSlot.ATHENA_CATALOG, "#athena-catalog"),
            (FocusSlot.ATHENA_DATABASE, "#athena-database"),
        ):
            widget = self.query_one(selector, Select)
            if self._is_focus_target(widget):
                targets.append((slot, widget))
        targets.append(
            (
                FocusSlot.ATHENA_TABS,
                self.query_one("#athena-view-tabs", ServiceTabStrip),
            )
        )
        targets.extend(self._active_surface_focus_targets())
        return tuple(targets)

    def _active_surface_focus_targets(self) -> tuple[tuple[FocusSlot, Widget], ...]:
        candidates: tuple[tuple[FocusSlot, Widget], ...]
        if self._vm.active_view == "query":
            candidates = (
                (FocusSlot.ATHENA_PRIMARY, self.query_one("#athena-editor", TextArea)),
                (FocusSlot.ATHENA_SECONDARY, self.query_one("#athena-execute", Button)),
                (
                    FocusSlot.ATHENA_DETAIL,
                    self.query_one("#athena-query-detail", VerticalScroll),
                ),
            )
        elif self._vm.active_view == "history":
            candidates = (
                (
                    FocusSlot.ATHENA_PRIMARY,
                    self.query_one("#athena-history-pane", ResourceListPane).option_list,
                ),
                (
                    FocusSlot.ATHENA_SECONDARY,
                    self.query_one("#athena-history-results", Button),
                ),
                (
                    FocusSlot.ATHENA_DETAIL,
                    self.query_one("#athena-history-detail", DetailRows).query_one(VerticalScroll),
                ),
            )
        elif self._vm.active_view == "results":
            candidates = (
                (
                    FocusSlot.ATHENA_PRIMARY,
                    self.query_one("#athena-results-table", DataTable),
                ),
                (
                    FocusSlot.ATHENA_SECONDARY,
                    self.query_one("#athena-more-results", Button),
                ),
            )
        else:
            candidates = (
                (
                    FocusSlot.ATHENA_PRIMARY,
                    self.query_one("#athena-named-pane", ResourceListPane).option_list,
                ),
                (
                    FocusSlot.ATHENA_SECONDARY,
                    self.query_one("#athena-prepared-pane", ResourceListPane).option_list,
                ),
                (
                    FocusSlot.ATHENA_DETAIL,
                    self.query_one("#athena-saved-detail", DetailRows).query_one(VerticalScroll),
                ),
            )
        return tuple((slot, widget) for slot, widget in candidates if self._is_focus_target(widget))

    @staticmethod
    def _is_focus_target(widget: Widget) -> bool:
        return widget.display and not widget.disabled and widget.can_focus

    def _project_focus_slot(
        self,
        slot: FocusSlot,
        *,
        targets: tuple[tuple[FocusSlot, Widget], ...] | None = None,
    ) -> None:
        target = dict(targets or self._focus_targets()).get(slot)
        if target is None:
            return
        if self._focus_coordinator is not None:
            self._focus_coordinator.set_focused_slot(slot)
        self.app.set_focus(target)

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
        elif isinstance(focused, ServiceTabStrip):
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

    def _focused_ids(self) -> set[str]:
        focused = self.app.focused
        if focused is None:
            return set()
        return {widget.id for widget in focused.ancestors_with_self if widget.id}

    def _on_page_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._refresh_page)

    def _refresh_page(self) -> None:
        context_controls = self._context_controls()
        view_controls = self._view_controls()
        if context_controls is None or view_controls is None:
            return
        self._sync_context(context_controls)
        self._sync_view(view_controls)
        cast(AthenaQueryView, view_controls[0][0]).refresh_from_vm()

    def _context_controls(self) -> _ContextControls | None:
        if not self.is_mounted:
            return None
        try:
            controls = (
                (
                    self.query_one("#athena-workgroup", Select),
                    self.query_one("#athena-catalog", Select),
                    self.query_one("#athena-database", Select),
                ),
                (
                    self.query_one("#athena-more-workgroups", AthenaLoadMoreButton),
                    self.query_one("#athena-more-catalogs", AthenaLoadMoreButton),
                    self.query_one("#athena-more-databases", AthenaLoadMoreButton),
                ),
            )
        except NoMatches:
            return None
        if not all(control.is_mounted for group in controls for control in group):
            return None
        return controls

    def _sync_context(
        self,
        controls: _ContextControls | None = None,
    ) -> None:
        controls = controls or self._context_controls()
        if controls is None:
            return
        selects, load_more = controls
        workgroup, catalog, database = selects
        self._syncing_context = True
        try:
            with self.prevent(Select.Changed):
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
            self._sync_context_load_more(load_more)
        finally:
            self._syncing_context = False

    def _sync_context_load_more(
        self,
        buttons: tuple[AthenaLoadMoreButton, ...],
    ) -> None:
        for button, (has_more, busy, state, error_text) in zip(
            buttons,
            (
                (
                    self._vm.has_more_workgroups,
                    self._vm.is_loading_more_workgroups,
                    self._vm.workgroups_state,
                    self._vm.workgroups_error_text,
                ),
                (
                    self._vm.has_more_catalogs,
                    self._vm.is_loading_more_catalogs,
                    self._vm.catalogs_state,
                    self._vm.catalogs_error_text,
                ),
                (
                    self._vm.has_more_databases,
                    self._vm.is_loading_more_databases,
                    self._vm.databases_state,
                    self._vm.databases_error_text,
                ),
            ),
            strict=True,
        ):
            button.sync(
                has_more=has_more,
                busy=busy,
                state=state,
                error_text=error_text,
            )

    def _view_controls(self) -> tuple[tuple[Widget, ...], ServiceTabStrip] | None:
        if not self.is_mounted:
            return None
        try:
            controls = (
                tuple(self.query_one(f"#athena-{view}-view", Widget) for view in _VIEW_ORDER),
                self.query_one("#athena-view-tabs", ServiceTabStrip),
            )
        except NoMatches:
            return None
        if not all(control.is_mounted for control in (*controls[0], controls[1])):
            return None
        return controls

    def _sync_view(
        self,
        controls: tuple[tuple[Widget, ...], ServiceTabStrip] | None = None,
    ) -> None:
        controls = controls or self._view_controls()
        if controls is None:
            return
        active = self._vm.active_view
        views, tabs = controls
        for view, child in zip(_VIEW_ORDER, views, strict=True):
            child.display = view == active
        tabs.set_active(active)

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
        try:
            targets = self._focus_targets()
        except NoMatches:
            return
        current_slot = (
            self._focus_coordinator.focused_slot
            if self._focus_coordinator is not None
            else FocusSlot.ATHENA_PRIMARY
        )
        if current_slot not in dict(targets):
            current_slot = FocusSlot.ATHENA_PRIMARY
        self._project_focus_slot(current_slot, targets=targets)


__all__ = ["AthenaPage"]
