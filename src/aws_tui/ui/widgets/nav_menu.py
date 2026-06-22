"""NavMenu — always-visible left rail backed by Textual's OptionList.

Replaces the previous ``ServiceItemView``-based ``ServicesMenu``.
Items rendered come from :class:`NavMenuVM.items`; selecting one
calls ``vm.switch_service_command.execute(item_id)``, which the app
routes to ``ContentHostVM.set_content``.

The rail is **always visible** with two width states toggled via the
inline hamburger at the top (or the ``m`` keybinding):

- **Collapsed** (default, width 4): icons / emoji only — one column
  per service plus the gear at the bottom for Settings.
- **Expanded** (width 18): icon + 1-2 word label per row.

The legacy ``display: none`` state was dropped in this rework: a
minimally collapsed icon-only column is always shown so users can
always see and click their way to a service without first
"summoning" the rail. The hamburger lives at the top of the rail
(``#menu-hamburger``); the docked-bottom Settings list (``#menu-pinned``)
houses the gear (Settings) per the PR #56 split.

Selection highlight matches the file-pane row pattern: a left-edge
ribbon glyph (``▌``) in ``$accent`` precedes the prompt of the
highlighted option in each OptionList, plus the standard
``$bg-sel`` / ``$accent-soft`` background+foreground applied via the
per-theme ``.tcss`` to ``.option-list--option-highlighted``.

The widget participates in the Tab focus cycle: ``Tab`` from the
right pane moves focus to the nav menu's currently-selected
OptionList; arrow keys navigate options; ``Enter`` selects.
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


#: Canonical id for the synthetic Settings nav peer (defined by
#: NavMenuVM._rebuild_items). Kept as a module-level constant here
#: so the partitioning in _rebuild_options can be grepped easily; if
#: NavMenuVM ever exports a public constant for this id, switch to it.
_SETTINGS_NAV_ID: str = "settings"


#: Ribbon glyph prepended to the highlighted option's prompt in each
#: OptionList. The ``▌`` (LEFT HALF BLOCK, U+258C) is a single cell
#: that visually reads as a vertical bar at the row's left edge —
#: matches the "narrow vertical ribbon" treatment the user asked for.
#: Non-highlighted rows use a single space at the same position so
#: glyph columns stay aligned.
_RIBBON_GLYPH: str = "▌"
_RIBBON_SPACER: str = " "


class NavMenu(Widget):
    """OptionList-backed always-visible left rail."""

    DEFAULT_CSS = """
    /* Always visible. Two width states: collapsed (icon-only) is the
       default; ``-expanded`` widens to fit the labels. The rest of
       the layout (content host) gets ``width: 1fr`` so it absorbs
       the difference automatically. */
    NavMenu {
        display: block;
        width: 4;
        background: $background;
    }
    NavMenu.-expanded {
        width: 18;
    }
    /* Hamburger at the top — one row, click-to-toggle. The glyph
       itself switches between "+" (collapsed → click to expand) and
       "-" (expanded → click to collapse) inside ``_rebuild_hamburger``. */
    NavMenu > #menu-hamburger {
        height: 1;
        padding: 0 1;
        text-style: bold;
        background: $background;
    }
    NavMenu > #menu-hamburger:hover {
        text-style: bold reverse;
    }
    NavMenu > #menu-services {
        height: 1fr;
        background: $background;
    }
    /* Pinned (Settings) list docks to the bottom of the rail so it
       sits visually separated from the service-item list above it,
       matching the macOS Settings-app / VS Code activity-bar pattern.
       The explicit ``height: auto`` lets the single Settings row
       size itself to one line; without it the OptionList would
       default to ``1fr`` and fight ``#menu-services`` for space.
       Per-theme border-top is added in each theme's ``.tcss`` (using
       ``$rule-dim``) — DEFAULT_CSS keeps to Textual-core variables
       only. */
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
        # Default to collapsed (icon-only). Visibility itself is never
        # toggled — the rail is always shown, just at one of two
        # widths. The `-expanded` class is added/removed by
        # `toggle_collapsed()`.
        self._collapsed: bool = True
        self._sub: DisposableBase | None = None

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        """Flip between the icon-only (collapsed) and icon+label
        (expanded) width states. The rail is always visible regardless;
        this only changes the width and the row prompts."""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.remove_class("-expanded")
        else:
            self.add_class("-expanded")
        self._rebuild_hamburger()
        self._rebuild_options()

    @property
    def vm(self) -> NavMenuVM:
        return self._vm

    def compose(self) -> ComposeResult:
        # Hamburger at the top, click-to-toggle. The glyph reflects
        # the NEXT action: '+' when collapsed (click expands), '-'
        # when expanded (click collapses).
        yield Static("+", id="menu-hamburger", markup=False)
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
        self._rebuild_hamburger()
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

    def _rebuild_hamburger(self) -> None:
        """Refresh the hamburger glyph to reflect the NEXT action."""
        try:
            ham = self.query_one("#menu-hamburger", Static)
        except Exception:
            return
        ham.update("+" if self._collapsed else "-")

    def _rebuild_options(self) -> None:
        """Rebuild both OptionLists to reflect the current items +
        collapsed state. Called on mount, on toggle, and whenever the
        VM's items or selected_id changes (via the hub subscription
        set up in :meth:`on_mount`).

        Services go into ``#menu-services`` (top); the Settings nav
        peer goes into ``#menu-pinned`` (docked bottom). The split is
        purely a View concern — :class:`NavMenuVM` still owns ONE
        ordered items list with Settings as the last entry.

        Each option's prompt is prefixed with a single-cell ribbon
        column: ``▌`` (in ``$accent`` per per-theme tcss) for the
        currently-selected option, or a space otherwise. This is the
        "narrow vertical ribbon" treatment that matches how file-pane
        row selection reads."""
        try:
            services = self.query_one("#menu-services", OptionList)
            pinned = self.query_one("#menu-pinned", OptionList)
        except Exception:
            return  # Not mounted yet.
        services.clear_options()
        pinned.clear_options()

        service_items = [item for item in self._vm.items if item.descriptor.id != _SETTINGS_NAV_ID]
        pinned_items = [item for item in self._vm.items if item.descriptor.id == _SETTINGS_NAV_ID]
        selected_id = self._vm.selected_id

        def _add_to(target: OptionList, items: list[NavItemVM]) -> None:
            for item in items:
                descriptor = item.descriptor
                ribbon = _RIBBON_GLYPH if descriptor.id == selected_id else _RIBBON_SPACER
                if self._collapsed:
                    # icon is always a str on ServiceDescriptor, but guard
                    # defensively for any future optional variants.
                    glyph = (descriptor.icon or descriptor.label or "?")[:2]
                    prompt = f"{ribbon}{glyph}"
                else:
                    glyph = descriptor.icon or "·"
                    prompt = f"{ribbon}{glyph} {descriptor.label}"
                target.add_option(Option(prompt, id=descriptor.id))

        _add_to(services, service_items)
        _add_to(pinned, pinned_items)

        # Restore the cursor highlight on whichever list owns the
        # selected id (so arrow-key navigation starts from the active
        # item when the rail is focused).
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

    def on_click(self, event: object) -> None:
        """Click on the hamburger toggles the collapsed/expanded state.

        Clicks on the OptionLists themselves are handled by Textual's
        own option-selection flow and bubble up as OptionSelected.
        """
        # ``event`` is a textual.events.Click; we sniff the chain to
        # see if the click landed on the hamburger Static. Anywhere
        # else inside the widget is a no-op (the OptionLists handle
        # their own clicks).
        widget = getattr(event, "widget", None)
        if widget is not None and getattr(widget, "id", None) == "menu-hamburger":
            self.toggle_collapsed()


__all__ = ["NavMenu"]
