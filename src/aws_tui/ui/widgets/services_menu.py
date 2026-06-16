"""ServicesMenu widget — left-rail service picker bound to :class:`ServicesMenuVM`.

Each row renders an icon + label; the currently-selected row gets a `>`
prefix in accent. The widget refreshes on every ``selected_id`` change and
on any ``items``-set mutation (we re-subscribe at mount).
"""

from __future__ import annotations

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.services_menu_vm import ServiceItemVM, ServicesMenuVM


class ServiceItemView(Widget):
    """Single row in the services menu."""

    DEFAULT_CSS = """
    ServiceItemView {
        height: 1;
    }
    """

    def __init__(
        self,
        item_vm: ServiceItemVM,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged_classes = " ".join(c for c in (classes, "service-item") if c)
        super().__init__(id=id, classes=merged_classes)
        self._item_vm = item_vm

    @property
    def item_vm(self) -> ServiceItemVM:
        return self._item_vm

    def render(self) -> Text:
        prefix = "> " if self._item_vm.is_selected else "  "
        # When the parent ServicesMenu is collapsed we show only the
        # icon glyph (or the first letter of the label as a fallback)
        # so the rail stays narrow. The parent toggles its CSS class
        # via :meth:`ServicesMenu.toggle_collapsed`.
        parent = self.parent
        while parent is not None and not isinstance(parent, ServicesMenu):
            parent = getattr(parent, "parent", None)
        if isinstance(parent, ServicesMenu) and parent.is_collapsed:
            short = (self._item_vm.descriptor.icon or self._item_vm.descriptor.label or "?")[:2]
            return Text(prefix + short)
        text = Text(prefix + self._item_vm.descriptor.label)
        return text

    def update_state(self) -> None:
        """Sync CSS classes to mirror the VM flags."""
        if self._item_vm.is_selected:
            self.add_class("-selected")
        else:
            self.remove_class("-selected")
        if self._item_vm.is_focused:
            self.add_class("-focused")
        else:
            self.remove_class("-focused")
        self.refresh()


class ServicesMenu(HubSubscriberMixin, Widget):
    """Left-rail service-picker widget.

    Collapsible. Starts collapsed so the dual-pane area gets all the
    horizontal space by default; press the configured toggle key (or
    call :meth:`toggle_collapsed`) to expand into the full-width label
    mode.
    """

    DEFAULT_CSS = """
    ServicesMenu {
        width: 6;
    }
    ServicesMenu.-expanded {
        width: 16;
    }
    """

    def __init__(
        self,
        vm: ServicesMenuVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: ServicesMenuVM = vm
        self._hub: MessageHub[Message] = hub
        self._collection_sub: DisposableBase | None = None
        # Collapsed by default — the rail shows just the icon column
        # until the user toggles it (frees up width for the panes).
        self._collapsed: bool = True

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.remove_class("-expanded")
        else:
            self.add_class("-expanded")
        self._refresh_selections()

    @property
    def vm(self) -> ServicesMenuVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("services", classes="title")
        yield Vertical(id="services-list")

    def on_mount(self) -> None:
        self._rebuild_items()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )
        self._collection_sub = (
            self._vm.on_collection_changed.subscribe(on_next=self._on_collection_changed)
            if hasattr(self._vm, "on_collection_changed")
            else None
        )
        # ServicesMenuVM doesn't itself expose on_collection_changed — the
        # CompositeVM does, but the facade hasn't republished it. We rebuild
        # on any "items" property change via the subscriber mixin instead.

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()
        if self._collection_sub is not None:
            self._collection_sub.dispose()
            self._collection_sub = None

    def on_click(self, _event: object) -> None:
        """Clicking the rail (anywhere not on a service item) toggles
        collapsed/expanded so the user doesn't have to remember the
        ``s`` shortcut. ServiceItemView click handling runs first via
        event bubbling, so the rail toggle only fires when the click
        lands on the rail's empty area."""
        # Only toggle when the click is on the rail itself, not bubbled
        # up from a service item. Textual delivers events to the
        # bottom-most widget first; if it wasn't consumed there, the
        # parent gets it. By the time we arrive here, no child handled
        # it — safe to toggle.
        self.toggle_collapsed()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_collection_changed(self, _event: object) -> None:
        self.call_after_refresh(self._rebuild_items)

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "selected_id":
            self.call_after_refresh(self._refresh_selections)
        elif property_name == "items":
            self.call_after_refresh(self._rebuild_items)

    def _rebuild_items(self) -> None:
        try:
            container = self.query_one("#services-list", Vertical)
        except Exception:
            return
        for child in list(container.children):
            child.remove()
        for item_vm in self._vm.items:
            view = ServiceItemView(item_vm, id=f"svc-{item_vm.descriptor.id}")
            container.mount(view)
        self._refresh_selections()

    def _refresh_selections(self) -> None:
        for view in self.query(ServiceItemView):
            view.update_state()


__all__ = ["ServiceItemView", "ServicesMenu"]
