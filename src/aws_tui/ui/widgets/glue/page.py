from __future__ import annotations

import contextlib
from functools import partial
from typing import ClassVar, cast

from reactivex.abc import DisposableBase
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import OptionList
from vmx import Message, MessageHub

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.ui.widgets.context_picker import ContextOption, ContextPicker
from aws_tui.ui.widgets.glue.catalog_view import GlueCatalogView
from aws_tui.ui.widgets.glue.crawlers_view import GlueCrawlersView
from aws_tui.ui.widgets.glue.detail_rows import DetailRows, ResourceListPane
from aws_tui.ui.widgets.glue.iceberg_view import GlueIcebergView
from aws_tui.ui.widgets.glue.jobs_view import GlueJobsView
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.glue.page_vm import GluePageVM, GlueView
from aws_tui.vm.service_source_vm import ServiceSourceContext

_VIEW_ORDER: tuple[GlueView, ...] = ("catalog", "jobs", "crawlers")
_RUN_FILTERS = (
    ("All states", "ALL"),
    ("Running", "RUNNING"),
    ("Succeeded", "SUCCEEDED"),
    ("Failed", "FAILED"),
    ("Stopped", "STOPPED"),
    ("Timed out", "TIMEOUT"),
)
_CRAWLER_FILTERS = (
    ("All states", "ALL"),
    ("Ready", "READY"),
    ("Running", "RUNNING"),
    ("Stopping", "STOPPING"),
)
_GLUE_FOCUS_ORDER = (
    FocusSlot.GLUE_SOURCE,
    FocusSlot.GLUE_FILTER,
    FocusSlot.GLUE_TABS,
    FocusSlot.GLUE_PRIMARY,
    FocusSlot.GLUE_SECONDARY,
    FocusSlot.GLUE_DETAIL,
    FocusSlot.GLUE_ICEBERG_SNAPSHOTS,
    FocusSlot.GLUE_ICEBERG_HISTORY,
    FocusSlot.GLUE_ICEBERG_MANIFESTS,
    FocusSlot.GLUE_ICEBERG_FILES,
    FocusSlot.GLUE_ICEBERG_PARTITIONS,
    FocusSlot.GLUE_ICEBERG_REFS,
    FocusSlot.GLUE_ICEBERG_TABLE,
    FocusSlot.GLUE_ICEBERG_MORE,
    FocusSlot.GLUE_ICEBERG_RETRY,
    FocusSlot.GLUE_ICEBERG_TIME_TRAVEL,
    FocusSlot.NAV_MENU,
)
_ICEBERG_FOCUS_SLOTS = {
    "glue-iceberg-tab-snapshots": FocusSlot.GLUE_ICEBERG_SNAPSHOTS,
    "glue-iceberg-tab-history": FocusSlot.GLUE_ICEBERG_HISTORY,
    "glue-iceberg-tab-manifests": FocusSlot.GLUE_ICEBERG_MANIFESTS,
    "glue-iceberg-tab-files": FocusSlot.GLUE_ICEBERG_FILES,
    "glue-iceberg-tab-partitions": FocusSlot.GLUE_ICEBERG_PARTITIONS,
    "glue-iceberg-tab-refs": FocusSlot.GLUE_ICEBERG_REFS,
    "glue-iceberg-table": FocusSlot.GLUE_ICEBERG_TABLE,
    "glue-iceberg-more": FocusSlot.GLUE_ICEBERG_MORE,
    "glue-iceberg-retry": FocusSlot.GLUE_ICEBERG_RETRY,
    "glue-iceberg-time-travel": FocusSlot.GLUE_ICEBERG_TIME_TRAVEL,
}


class GluePage(HubSubscriberMixin, Widget):
    DEFAULT_CSS: ClassVar[str] = """
    GluePage {
        height: 1fr;
        layout: vertical;
    }
    GluePage > #glue-context-row {
        width: 1fr;
        height: auto;
        min-height: 3;
        layout: horizontal;
        border: none;
    }
    GluePage > #glue-context-row > ServiceSourceHeader,
    GluePage > #glue-context-row > ContextPicker {
        width: 1fr;
        height: auto;
        min-height: 3;
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
        source_candidates: tuple[ServiceSourceContext, ...] = (),
        focus_coordinator: FocusCoordinatorVM | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm = vm
        self._hub = hub
        self._keymap = keymap or KeymapStore()
        self._source_candidates = source_candidates
        self._focus_coordinator = focus_coordinator
        self._focus_subscriptions: list[DisposableBase] = []

    @property
    def vm(self) -> GluePageVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Horizontal(id="glue-context-row"):
            yield ServiceSourceHeader(
                self._vm.source,
                candidates=self._source_candidates,
                id="glue-source-header",
            )
            run_filter = ContextPicker(
                "Run state",
                tuple(ContextOption(label, value) for label, value in _RUN_FILTERS),
                selected=self._job_filter_value(),
                id="glue-run-state-filter",
            )
            run_filter.display = self._vm.active_view == "jobs"
            yield run_filter
            crawler_filter = ContextPicker(
                "Crawler state",
                tuple(ContextOption(label, value) for label, value in _CRAWLER_FILTERS),
                selected=self._vm.crawlers.state_filter or "ALL",
                id="glue-crawler-state-filter",
            )
            crawler_filter.display = self._vm.active_view == "crawlers"
            yield crawler_filter
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
        self._sync_context()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=("active_view",),
            on_property_changed=self._on_active_view_changed,
        )
        iceberg_slots = frozenset(_ICEBERG_FOCUS_SLOTS.values())
        for child_vm, sensitive_slots in (
            (self._vm.catalog.iceberg, iceberg_slots),
            (self._vm.jobs, frozenset((FocusSlot.GLUE_FILTER,))),
            (self._vm.crawlers, frozenset((FocusSlot.GLUE_FILTER,))),
        ):
            self._focus_subscriptions.append(
                child_vm.on_property_changed.subscribe(
                    on_next=partial(
                        self._on_child_vm_changed,
                        sensitive_slots,
                    )
                )
            )
        self.call_after_refresh(self._deferred_maybe_focus_active)

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()
        for subscription in self._focus_subscriptions:
            subscription.dispose()
        self._focus_subscriptions.clear()

    async def on_service_tab_strip_changed(self, event: ServiceTabStrip.Changed) -> None:
        await self.action_select_view(event.value)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if event.widget is self.app.focused:
            self._sync_focused_widget(event.widget)

    async def action_select_view(self, view: str) -> None:
        selected = cast(GlueView, view)
        if selected not in _VIEW_ORDER:
            return
        reference = (
            self._focus_coordinator.focused_slot if self._focus_coordinator is not None else None
        )
        await self._vm.select_view(selected)
        self._sync_view()
        self._maybe_focus_active(reference)

    async def action_refresh_active(self) -> None:
        await self._vm.refresh_active()

    async def action_choose_run_state(self) -> None:
        await self.action_select_view("jobs")
        self._focus_and_open_picker(
            FocusSlot.GLUE_FILTER,
            "#glue-run-state-filter",
        )

    async def action_choose_crawler_state(self) -> None:
        await self.action_select_view("crawlers")
        self._focus_and_open_picker(
            FocusSlot.GLUE_FILTER,
            "#glue-crawler-state-filter",
        )

    def on_context_picker_changed(self, event: ContextPicker.Changed) -> None:
        if event.control.id == "glue-run-state-filter":
            states = frozenset() if event.value == "ALL" else frozenset((event.value,))
            if states != self._vm.jobs.run_state_filter:
                self.run_worker(
                    self._vm.set_job_run_states(states),
                    exclusive=True,
                    group="glue-filter-runs",
                )
        elif event.control.id == "glue-crawler-state-filter":
            state = None if event.value == "ALL" else event.value
            if state != self._vm.crawlers.state_filter:
                self.run_worker(
                    self._vm.set_crawler_state(state),
                    exclusive=True,
                    group="glue-filter-crawlers",
                )

    def on_context_picker_open_changed(self, event: ContextPicker.OpenChanged) -> None:
        event.stop()
        if not event.is_open:
            return
        for picker in self.query(ContextPicker):
            if picker is not event.picker and picker.is_open:
                picker.close(refocus=False)

    def cycle_focus(self, *, reverse: bool) -> None:
        if self._focus_coordinator is None or not self._focus_projection_available():
            return
        focused = self.app.focused
        if focused is not None:
            self._sync_focused_widget(focused)
        targets = self._focus_targets()
        slot = self._focus_coordinator.cycle_focus_ring(
            tuple(slot for slot, _widget in targets),
            reverse=reverse,
        )
        self._close_departed_picker(focused)
        self._project_focus_slot(slot, targets=targets)

    @staticmethod
    def _close_departed_picker(focused: Widget | None) -> None:
        if focused is None:
            return
        picker = next(
            (
                widget
                for widget in focused.ancestors_with_self
                if isinstance(widget, ContextPicker) and widget.is_open
            ),
            None,
        )
        if picker is not None:
            picker.close(refocus=False)

    def focus_default(self) -> None:
        """Focus the active view's primary operational target."""
        self._project_focus_slot(FocusSlot.GLUE_PRIMARY)

    def project_focus_slot(self, slot: FocusSlot) -> None:
        """Project an app-coordinated focus slot onto this page."""
        self._project_focus_slot(slot)

    def _focus_targets(self) -> tuple[tuple[FocusSlot, Widget], ...]:
        source = self.query_one("#glue-source-header", Widget)
        tabs = self.query_one("#glue-view-tabs", ServiceTabStrip)
        active = self._vm.active_view
        if active == "catalog":
            catalog = self.query_one(GlueCatalogView)
            targets: list[tuple[FocusSlot, Widget]] = [
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
            ]
            iceberg = catalog.query_one(GlueIcebergView)
            targets.extend(
                (_ICEBERG_FOCUS_SLOTS[widget.id or ""], widget)
                for widget in iceberg.focus_targets()
            )
        elif active == "jobs":
            primary, secondary, detail = self.query_one(GlueJobsView).focus_targets()
            targets = [
                (FocusSlot.GLUE_SOURCE, source),
                (
                    FocusSlot.GLUE_FILTER,
                    self.query_one("#glue-run-state-filter", ContextPicker),
                ),
                (FocusSlot.GLUE_TABS, tabs),
                (FocusSlot.GLUE_PRIMARY, primary),
                (FocusSlot.GLUE_SECONDARY, secondary),
                (FocusSlot.GLUE_DETAIL, detail),
            ]
        else:
            primary, detail = self.query_one(GlueCrawlersView).focus_targets()
            targets = [
                (FocusSlot.GLUE_SOURCE, source),
                (
                    FocusSlot.GLUE_FILTER,
                    self.query_one("#glue-crawler-state-filter", ContextPicker),
                ),
                (FocusSlot.GLUE_TABS, tabs),
                (FocusSlot.GLUE_PRIMARY, primary),
                (FocusSlot.GLUE_DETAIL, detail),
            ]
        nav = self._nav_focus_target()
        if nav is not None:
            targets.append((FocusSlot.NAV_MENU, nav))
        return tuple(
            (slot, widget)
            for slot, widget in targets
            if widget.display and not widget.disabled and widget.can_focus
        )

    def _nav_focus_target(self) -> Widget | None:
        try:
            return self.screen.query_one("#nav-menu", Widget)
        except NoMatches:
            return None

    def _sync_focused_widget(self, focused: Widget) -> None:
        if self._focus_coordinator is None or not self._focus_projection_available():
            return
        ancestors = set(focused.ancestors_with_self)
        for slot, target in self._focus_targets():
            if target in ancestors:
                self._focus_coordinator.set_focused_slot(slot)
                return

    def _project_focus_slot(
        self,
        slot: FocusSlot,
        *,
        targets: tuple[tuple[FocusSlot, Widget], ...] | None = None,
    ) -> None:
        if not self._focus_projection_available():
            return
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
        if isinstance(focused, ContextPicker):
            focused.open()
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
        if focused is None or not self._contains_focus(focused):
            return False
        if self.query_one(GlueIcebergView).activate_focused(focused):
            return True
        if isinstance(focused, ServiceTabStrip):
            focused.action_select()
            return True
        elif isinstance(focused, ServiceSourceHeader | ContextPicker):
            focused.open()
            return True
        elif not space and isinstance(focused, OptionList):
            focused.action_select()
            return True
        return False

    def _focus_and_open_picker(self, slot: FocusSlot, selector: str) -> None:
        with contextlib.suppress(NoMatches):
            picker = self.query_one(selector, ContextPicker)
            for sibling in self.query(ContextPicker):
                if sibling is not picker:
                    sibling.close(refocus=False)
            self._project_focus_slot(slot)
            picker.open()

    def _contains_focus(self, focused: Widget | None) -> bool:
        return focused is not None and (focused is self or self in focused.ancestors_with_self)

    def _on_active_view_changed(self, _property_name: str) -> None:
        self.call_after_refresh(self._sync_view)

    def _on_child_vm_changed(
        self,
        sensitive_slots: frozenset[FocusSlot],
        _property_name: str,
    ) -> None:
        self.call_after_refresh(self._sync_context)
        if (
            self._focus_coordinator is None
            or self._focus_coordinator.focused_slot not in sensitive_slots
        ):
            return
        reference = self._focus_coordinator.focused_slot
        self.call_after_refresh(partial(self._deferred_maybe_focus_active, reference))

    def _sync_view(self) -> None:
        if not self._focus_projection_available():
            return
        active = self._vm.active_view
        children = tuple(self.query_one(f"#glue-{view}-view") for view in _VIEW_ORDER)
        tabs = self.query_one("#glue-view-tabs", ServiceTabStrip)
        for view, child in zip(_VIEW_ORDER, children, strict=True):
            child.display = view == active
        tabs.set_active(active)
        self._sync_context()

    def _sync_context(self) -> None:
        if not self._focus_projection_available():
            return
        run_filter = self.query_one("#glue-run-state-filter", ContextPicker)
        crawler_filter = self.query_one(
            "#glue-crawler-state-filter",
            ContextPicker,
        )
        run_filter.display = self._vm.active_view == "jobs"
        if not run_filter.display:
            run_filter.close(refocus=False)
        run_filter.set_options(
            tuple(ContextOption(label, value) for label, value in _RUN_FILTERS),
            selected=self._job_filter_value(),
        )
        crawler_filter.display = self._vm.active_view == "crawlers"
        if not crawler_filter.display:
            crawler_filter.close(refocus=False)
        crawler_filter.set_options(
            tuple(ContextOption(label, value) for label, value in _CRAWLER_FILTERS),
            selected=self._vm.crawlers.state_filter or "ALL",
        )

    def _job_filter_value(self) -> str:
        return next(iter(sorted(self._vm.jobs.run_state_filter)), "ALL")

    def _focus_projection_available(self) -> bool:
        return self.is_running and self.is_attached and self.display

    def _deferred_maybe_focus_active(self, reference: FocusSlot | None = None) -> None:
        if not self._focus_projection_available():
            return
        self._maybe_focus_active(reference)

    def _maybe_focus_active(self, reference: FocusSlot | None = None) -> None:
        focused = self.app.focused
        if (
            reference is None
            and self._focus_coordinator is not None
            and focused is not None
            and not self.has_focus_within
            and self._focus_coordinator.focused_slot is FocusSlot.NAV_MENU
        ):
            return
        if reference is None and focused is not None and not self.has_focus_within:
            return
        targets = self._focus_targets()
        if not targets:
            return
        current_slot = (
            reference or self._focus_coordinator.focused_slot
            if self._focus_coordinator is not None
            else FocusSlot.GLUE_PRIMARY
        )
        if current_slot not in dict(targets):
            if self._focus_coordinator is None:
                current_slot = FocusSlot.GLUE_PRIMARY
            else:
                current_slot = self._focus_coordinator.select_nearest_focus_slot(
                    tuple(slot for slot, _widget in targets),
                    order=_GLUE_FOCUS_ORDER,
                    reference=current_slot,
                )
        self._project_focus_slot(current_slot, targets=targets)


__all__ = ["GluePage"]
