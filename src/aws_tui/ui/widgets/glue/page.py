from __future__ import annotations

from typing import ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import OptionList, Select, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.ui.widgets.glue.catalog_view import GlueCatalogView
from aws_tui.ui.widgets.glue.crawlers_view import GlueCrawlersView
from aws_tui.ui.widgets.glue.jobs_view import GlueJobsView
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.glue.page_vm import GluePageVM, GlueView

_VIEW_ORDER: tuple[GlueView, ...] = ("catalog", "jobs", "crawlers")


class _ViewTab(Static, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "select", "Select", show=False),
    ]

    class Selected(TextualMessage):
        def __init__(self, view: GlueView) -> None:
            super().__init__()
            self.view = view

    def __init__(self, view: GlueView, number: int) -> None:
        super().__init__(
            f"{number} {view}",
            id=f"glue-tab-{view}",
            classes="glue-view-tab",
            markup=False,
        )
        self.view = view

    def on_click(self, _event: Click) -> None:
        self.post_message(self.Selected(self.view))

    def action_select(self) -> None:
        self.post_message(self.Selected(self.view))


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
    GluePage > #glue-view-tabs {
        width: 1fr;
        height: 1;
        layout: horizontal;
    }
    GluePage .glue-view-tab {
        width: 1fr;
        height: 1;
        padding: 0 1;
        content-align: center middle;
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
        focus_coordinator: FocusCoordinatorVM | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm = vm
        self._hub = hub
        self._focus_coordinator = focus_coordinator

    @property
    def vm(self) -> GluePageVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield ServiceSourceHeader(self._vm.source, id="glue-source-header")
        with Horizontal(id="glue-view-tabs"):
            for number, view in enumerate(_VIEW_ORDER, start=1):
                yield _ViewTab(view, number)
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

    async def on__view_tab_selected(self, event: _ViewTab.Selected) -> None:
        await self.action_select_view(event.view)

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
        if reverse:
            self.app.action_focus_previous()
        else:
            self.app.action_focus_next()

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
        if isinstance(focused, _ViewTab):
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
                tab = self.query_one(f"#glue-tab-{view}", _ViewTab)
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
        active = self.query_one(f"#glue-{self._vm.active_view}-view")
        focusable = next(iter(active.query("OptionList, Select")), None)
        if focusable is not None:
            focusable.focus()


__all__ = ["GluePage"]
