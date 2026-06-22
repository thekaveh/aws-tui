"""NavMenu — left-rail vertical nav backed by Textual's OptionList.

Replaces the previous ``ServiceItemView``-based ``ServicesMenu``.
Items rendered come from :class:`NavMenuVM.items`; selecting one
calls ``vm.switch_service_command.execute(item_id)``, which the app
routes to ``ContentHostVM.set_content``.

The widget has two orthogonal CSS axes that combine to produce the
visible layout:

- ``.-expanded`` — visibility. Without it, the rail is
  ``display: none; width: 0``. Owned by the app's
  ``action_toggle_services`` (bound to ``m``).
- ``.-collapsed`` — label density. ``-collapsed`` means icons-only
  (e.g. ``⚙`` for Settings); without ``-collapsed`` the rail renders
  full labels. Owned by this widget via :meth:`toggle_collapsed`.

The app's hamburger handler currently toggles both axes in lock-step
(it flips ``-expanded`` *and* calls ``toggle_collapsed``), so the
hamburger cycle reduces to invisible ↔ wide-labels in practice.
The narrow icons-only state is reachable programmatically (used by
snapshot tests) but not via the hamburger today; a follow-up may
expose it through a separate keybinding or a 3-state cycle if the
nav grows enough items to need denser display.
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

    from aws_tui.vm.nav_menu_vm import NavItemVM, NavMenuVM


class NavMenu(Widget):
    """OptionList-backed left rail."""

    DEFAULT_CSS = """
    NavMenu {
        display: none;
        width: 0;
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
    NavMenu > #menu-services {
        height: 1fr;
        background: $background;
    }
    /* Pinned (Settings) list docks to the bottom of the rail so it
       sits visually separated from the service-item list above it,
       matching the macOS Settings-app / VS Code activity-bar pattern
       the user asked for. The explicit ``height: auto`` lets the
       single Settings row size itself to one line; without it the
       OptionList would default to ``1fr`` and fight ``#menu-services``
       for space. Per-theme border-top is added in each theme's
       ``.tcss`` (using ``$rule-dim``) — DEFAULT_CSS keeps to
       Textual-core variables only. */
    NavMenu > #menu-pinned {
        dock: bottom;
        height: auto;
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
        # Two OptionLists: services live in #menu-services at the top;
        # the Settings nav peer lives in #menu-pinned which is docked
        # to the bottom (see DEFAULT_CSS). Splitting the rail this way
        # is the only way to visually separate "primary navigation"
        # from the always-present Settings entry — a single OptionList
        # would render Settings immediately below the last service
        # with empty rows trailing it.
        yield OptionList(id="menu-services")
        yield OptionList(id="menu-pinned")

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
        """Rebuild both OptionLists to reflect the current items +
        collapsed state. Called on mount, on toggle, and whenever the
        VM's items or selected_id changes (via the hub subscription
        set up in :meth:`on_mount`).

        Services go into ``#menu-services`` (top); the Settings nav
        peer goes into ``#menu-pinned`` (docked bottom). The split is
        purely a View concern — :class:`NavMenuVM` still owns ONE
        ordered items list with Settings as the last entry."""
        try:
            services = self.query_one("#menu-services", OptionList)
            pinned = self.query_one("#menu-pinned", OptionList)
        except Exception:
            return  # Not mounted yet.
        services.clear_options()
        pinned.clear_options()

        service_items = [item for item in self._vm.items if item.descriptor.id != "settings"]
        pinned_items = [item for item in self._vm.items if item.descriptor.id == "settings"]

        def _add_to(target: OptionList, items: list[NavItemVM]) -> None:
            for item in items:
                descriptor = item.descriptor
                if self._collapsed:
                    # icon is always a str on ServiceDescriptor, but guard
                    # defensively for any future optional variants.
                    glyph = (descriptor.icon or descriptor.label or "?")[:2]
                    prompt = glyph
                else:
                    glyph = descriptor.icon or "·"
                    prompt = f"{glyph} {descriptor.label}"
                target.add_option(Option(prompt, id=descriptor.id))

        _add_to(services, service_items)
        _add_to(pinned, pinned_items)

        # Restore the highlight on whichever list owns the selected id.
        selected_id = self._vm.selected_id
        if selected_id is not None:
            for idx, item in enumerate(service_items):
                if item.descriptor.id == selected_id:
                    services.highlighted = idx
                    return
            for idx, item in enumerate(pinned_items):
                if item.descriptor.id == selected_id:
                    pinned.highlighted = idx
                    return

    # ── Event handlers ─────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Forward selection to the VM via switch_service_command."""
        event.stop()
        if event.option_id is None:
            return
        self._vm.switch_service_command.execute(event.option_id)


__all__ = ["NavMenu"]
