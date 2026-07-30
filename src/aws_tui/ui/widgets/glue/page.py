from __future__ import annotations

import contextlib
from typing import ClassVar, cast

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widget import Widget
from textual.widgets import OptionList, Select
from vmx import Message, MessageHub

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.ui.widgets.glue.catalog_view import GlueCatalogView
from aws_tui.ui.widgets.glue.crawlers_view import GlueCrawlersView
from aws_tui.ui.widgets.glue.detail_rows import DetailRows, ResourceListPane
from aws_tui.ui.widgets.glue.jobs_view import GlueJobsView
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.glue.page_vm import GluePageVM, GlueView

_VIEW_ORDER: tuple[GlueView, ...] = ("catalog", "jobs", "crawlers")


class _FocusableSourceHeader(ServiceSourceHeader, can_focus=True):
    """Temporary focus bridge until Task 3 supplies the source picker."""


class GluePage(HubSubscriberMixin, Widget):
    DEFAULT_CSS: ClassVar[str] = """
    GluePage {
        height: 1fr;
        layout: vertical;
    }
    GluePage > ServiceSourceHeader {
        width: 1fr;
        height: 1;
    }
    GluePage > #glue-view-host {
        width: 1fr;
        height: 1fr;
    }
    GluePage .glue-service-view {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(
        self,
        vm: GluePageVM,
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

    @property
    def vm(self) -> GluePageVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield _FocusableSourceHeader(self._vm.source, id="glue-source-header")
        yield ServiceTabStrip(
            tuple(
                (
                    view,
                    f"{keys[0] if keys else ''} {view}".strip(),
                )
                for view in _VIEW_ORDER
                for keys in (self._keymap.resolve(f"glue.{view}"),)
            ),
            active=self._vm.active_view,
            id="glue-view-tabs",
            tab_id_prefix="glue-tab",
        )
        with Container(id="glue-view-host"):
            yield GlueCatalogView(self._vm, id="glue-catalog-view")
            yield GlueJobsView(self._vm, id="glue-jobs-view")
            yield GlueCrawlersView(self._vm, id="glue-crawlers-view")

    def on_mount(self) -> None:
        self._sync_view()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=("active_view",),
            on_property_changed=self._on_active_view_changed,
        )
        self.call_after_refresh(self._maybe_focus_active)

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    async def on_service_tab_strip_changed(self, event: ServiceTabStrip.Changed) -> None:
        await self.action_select_view(event.value)

    async def action_select_view(self, view: str) -> None:
        selected = cast(GlueView, view)
        if selected not in _VIEW_ORDER:
            return
        await self._vm.select_view(selected)
        self._sync_view()
        self.call_after_refresh(self._maybe_focus_active)

    async def action_refresh_active(self) -> None:
        await self._vm.refresh_active()

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
        self._project_focus_slot(FocusSlot.GLUE_PRIMARY)

    def _focus_targets(self) -> tuple[tuple[FocusSlot, Widget], ...]:
        source = self.query_one("#glue-source-header", Widget)
        tabs = self.query_one("#glue-view-tabs", ServiceTabStrip)
        active = self._vm.active_view
        if active == "catalog":
            catalog = self.query_one(GlueCatalogView)
            return (
                (FocusSlot.GLUE_SOURCE, source),
                (FocusSlot.GLUE_TABS, tabs),
                (
                    FocusSlot.GLUE_PRIMARY,
                    catalog.query_one("#glue-databases-pane", ResourceListPane).option_list,
                ),
                (
                    FocusSlot.GLUE_SECONDARY,
                    catalog.query_one("#glue-tables-pane", ResourceListPane).option_list,
                ),
                (
                    FocusSlot.GLUE_DETAIL,
                    catalog.query_one("#glue-table-detail-pane", DetailRows).query_one(
                        VerticalScroll
                    ),
                ),
            )
        if active == "jobs":
            filter_target, primary, secondary, detail = self.query_one(GlueJobsView).focus_targets()
            return (
                (FocusSlot.GLUE_SOURCE, source),
                (FocusSlot.GLUE_FILTER, filter_target),
                (FocusSlot.GLUE_TABS, tabs),
                (FocusSlot.GLUE_PRIMARY, primary),
                (FocusSlot.GLUE_SECONDARY, secondary),
                (FocusSlot.GLUE_DETAIL, detail),
            )
        filter_target, primary, detail = self.query_one(GlueCrawlersView).focus_targets()
        return (
            (FocusSlot.GLUE_SOURCE, source),
            (FocusSlot.GLUE_FILTER, filter_target),
            (FocusSlot.GLUE_TABS, tabs),
            (FocusSlot.GLUE_PRIMARY, primary),
            (FocusSlot.GLUE_DETAIL, detail),
        )

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
        if isinstance(focused, OptionList):
            if delta < 0:
                focused.action_cursor_up()
            else:
                focused.action_cursor_down()
            return
        if isinstance(focused, Select):
            focused.action_show_overlay()
            return
        action = getattr(
            focused,
            "action_scroll_up" if delta < 0 else "action_scroll_down",
            None,
        )
        if action is not None:
            action()

    def activate_focused(self, *, space: bool) -> bool:
        focused = self.app.focused
        if not self._contains_focus(focused):
            return False
        if isinstance(focused, ServiceTabStrip):
            focused.action_select()
        elif isinstance(focused, Select):
            focused.action_show_overlay()
        elif not space and isinstance(focused, OptionList):
            focused.action_select()
        return True

    def _contains_focus(self, focused: Widget | None) -> bool:
        return focused is not None and (focused is self or self in focused.ancestors_with_self)

    def _on_active_view_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._sync_view)

    def _sync_view(self) -> None:
        active = self._vm.active_view
        for view in _VIEW_ORDER:
            try:
                child = self.query_one(f"#glue-{view}-view")
            except Exception:
                continue
            child.display = view == active
        with contextlib.suppress(Exception):
            self.query_one("#glue-view-tabs", ServiceTabStrip).set_active(active)

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
        targets = self._focus_targets()
        current_slot = (
            self._focus_coordinator.focused_slot
            if self._focus_coordinator is not None
            else FocusSlot.GLUE_PRIMARY
        )
        if current_slot not in dict(targets):
            current_slot = FocusSlot.GLUE_PRIMARY
        self._project_focus_slot(current_slot, targets=targets)


__all__ = ["GluePage"]
