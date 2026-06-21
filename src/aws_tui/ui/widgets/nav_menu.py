"""NavMenu — left-rail vertical nav backed by Textual's OptionList.

Replaces the previous ``ServiceItemView``-based ``ServicesMenu``.
Items rendered come from :class:`NavMenuVM.items`; selecting one
calls ``vm.switch_service_command.execute(item_id)``, which the app
routes to ``ContentHostVM.set_content``.

Collapsed mode shows icon glyphs only (e.g. ``S3``, ``⚙``). Expanded
mode shows full labels (``S3``, ``Settings``). The hamburger button
in the app's title bar calls :meth:`toggle_collapsed`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message, MessageHub, PropertyChangedMessage

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase

    from aws_tui.vm.nav_menu_vm import NavMenuVM


class NavMenu(Widget):
    """OptionList-backed left rail."""

    DEFAULT_CSS = """
    NavMenu {
        display: none;
        width: 0;
        height: 1fr;
    }
    NavMenu.-expanded {
        display: block;
        width: 18;
    }
    NavMenu.-collapsed.-expanded {
        width: 4;
    }
    NavMenu > #menu-header {
        padding: 0 1;
        text-style: bold;
    }
    NavMenu > OptionList {
        height: 1fr;
        background: $background;
    }
    """

    def __init__(
        self,
        *,
        vm: NavMenuVM,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: NavMenuVM = vm
        self._hub: MessageHub[Message] = hub
        # Match the legacy ServicesMenu default: collapsed at start so
        # the dual-pane gets all the horizontal space until the user
        # toggles via the hamburger.
        self._collapsed: bool = True
        self.add_class("-collapsed")
        self._sub: DisposableBase | None = None

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.add_class("-collapsed")
        else:
            self.remove_class("-collapsed")
        # Always mark expanded so the display:block/width rules apply.
        # Toggling visibility is the app's responsibility via -expanded.
        self._rebuild_options()

    @property
    def vm(self) -> NavMenuVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("menu", id="menu-header")
        yield OptionList(id="menu-options")

    def on_mount(self) -> None:
        self._rebuild_options()
        # Subscribe to the VM's property-change broadcasts. NavMenuVM
        # fires PropertyChangedMessage("items") after _rebuild_items and
        # PropertyChangedMessage("selected_id") on selection. Without
        # this subscription the OptionList silently shows stale items
        # after a connection switch (since the VM's items list filters
        # by what the connection supports).
        #
        # Naming: NOT ``_on_message`` — Textual auto-routes any
        # ``_on_*`` method through its async message dispatcher,
        # which collides with reactivex's sync ``on_next`` callable.
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.sender_object is not self._vm:
            return
        if msg.property_name in ("items", "selected_id"):
            self._rebuild_options()

    # ── Internal ───────────────────────────────────────────────────────────

    def _rebuild_options(self) -> None:
        """Rebuild the OptionList options to reflect the current
        items + collapsed state. Called on mount, on toggle, and
        whenever the VM's items or selected_id changes (via the hub
        subscription set up in :meth:`on_mount`)."""
        try:
            ol = self.query_one("#menu-options", OptionList)
        except Exception:
            return  # Not mounted yet.
        ol.clear_options()
        for item in self._vm.items:
            descriptor = item.descriptor
            if self._collapsed:
                # icon is always a str on ServiceDescriptor, but guard
                # defensively for any future optional variants.
                glyph = (descriptor.icon or descriptor.label or "?")[:2]
                prompt = glyph
            else:
                glyph = descriptor.icon or "·"
                prompt = f"{glyph} {descriptor.label}"
            ol.add_option(Option(prompt, id=descriptor.id))
        # Restore the highlight to the currently-selected item if any.
        if self._vm.selected_id is not None:
            for idx, item in enumerate(self._vm.items):
                if item.descriptor.id == self._vm.selected_id:
                    ol.highlighted = idx
                    break

    # ── Event handlers ─────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Forward selection to the VM via switch_service_command."""
        event.stop()
        if event.option_id is None:
            return
        self._vm.switch_service_command.execute(event.option_id)


__all__ = ["NavMenu"]
