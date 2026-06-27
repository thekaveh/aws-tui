"""NavMenu — always-visible left rail backed by Textual's OptionList.

Replaces the previous ``ServiceItemView``-based ``ServicesMenu``.
Items rendered come from :class:`NavMenuVM.items`; selecting one
calls ``vm.switch_service_command.execute(item_id)``, which the app
routes to ``ContentHostVM.set_content``.

The rail is **always visible** with two width states toggled via the
inline hamburger at the top (or the ``m`` keybinding):

- **Collapsed** (default, width 8): icons / emoji only — one column
  per service plus the gear at the bottom for Settings.
- **Expanded** (width 20): icon + 1-2 word label per row.

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


# ── Row template ────────────────────────────────────────────────────────────
#
# Every option in the nav rail is rendered by ONE of these two helpers.
# Centralising the row template here means a future-tense change to the
# nav-rail row chrome (spacing, separators, accent prefix, etc.) lives in
# one place; previously the formatting was inlined inside
# ``_rebuild_options`` and a new item could silently diverge.
#
# **Icon contract.** Both helpers assume ``glyph`` renders as exactly
# 2 visual cells when the user's terminal/font combination is honest:
#
# - Use SMP single-codepoint emojis (codepoints in the U+1F***
#   blocks — e.g. 🪣 U+1FAA3 BUCKET for S3, 🔥 U+1F525 FIRE for EMR).
#   Every monospace font with emoji support ships these as 2-cell
#   colour glyphs because there's no text-presentation alternative.
# - **AVOID** BMP-plus-VS-16 combinations (e.g. ⚡ U+26A1 + ⚡️ U+26A1
#   + U+FE0F, ⚙ U+2699 + ⚙️ U+2699 + U+FE0F). VS-16 *requests* emoji
#   presentation but doesn't guarantee it: many monospace fonts ship
#   only the text-style glyph for these codepoints, falling back to a
#   1-cell outline. The collapsed-rail layout then garbles the entire
#   row because the math assumes 2 cells and the renderer delivers 1.
#   PR #77 + PR #78 tried ⚡ then ⚡️ for EMR and both broke; PR #79
#   switched to 🔥 (SMP) and the issue went away.
#
# (Settings and the EC2 stub still use BMP+VS-16 emojis ⚙️ / 🖥️.
# Both were chosen before this rule was clear and survive because the
# user's terminal/font combination renders them as 2-cell colour.
# When either is replaced, prefer an SMP alternative.)


def _format_collapsed_prompt(*, ribbon: str, glyph: str) -> str:
    """Collapsed-rail prompt for one nav option.

    6-cell content area (rail width 8 minus the 2-cell border). Layout::

        col 0: ribbon ``▌`` (selected) or space
        col 1: gap
        col 2-3: emoji (2 cells wide — see "Icon contract" above)
        col 4-5: trailing empty

    Keeps the ribbon flush against the rail's left border (matching
    file-pane cursor column) and lands the emoji centred in the rail.
    PR-history: the leading gap was dropped in PR #68 then restored in
    PR #69 after the emoji slid left without it.
    """
    return f"{ribbon} {glyph}"


def _format_expanded_prompt(*, ribbon: str, glyph: str, label: str) -> str:
    """Expanded-rail prompt for one nav option.

    Layout::

        col 0: ribbon ``▌`` (selected) or space
        col 1-2: emoji (2 cells)
        col 3: gap
        col 4+: 1-2 word label

    Same icon contract as :func:`_format_collapsed_prompt`.
    """
    return f"{ribbon}{glyph} {label}"


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
       the difference automatically. Background and border colors
       come from per-theme ``.tcss`` (using ``$bg`` / ``$rule-dim``
       project tokens, not Textual-core variables) so the rail
       matches the file-pane chrome. Sub-widgets use
       ``background: transparent`` so the NavMenu's themed
       background shows through uniformly. */
    NavMenu {
        display: block;
        /* Width 8 fits, after the per-theme single-cell border
           (``border: solid $rule-dim``) takes 2 columns: 2-cell
           border reserve + 1-cell ribbon (``▌``) + 2-cell emoji
           (most are 2 cells wide in monospace terminals — e.g.
           ``🪣``, ``⚙️``) + 3-cell OptionList padding/spacer. */
        width: 8;
    }
    NavMenu.-expanded {
        width: 20;
    }
    /* Hamburger at the top — one row, click-to-toggle. The glyph
       itself switches between "+" (collapsed → click to expand) and
       "-" (expanded → click to collapse) inside ``_rebuild_hamburger``.
       ``content-align: center middle`` centers the single-char glyph
       in the row (matches how the emoji icons read after PR #59's
       width bump to 8). ``margin-bottom: 1`` puts breathing room
       between the hamburger and the first service icon so the rail
       doesn't read as one continuous column. */
    NavMenu > #menu-hamburger {
        height: 1;
        margin-bottom: 1;
        content-align: center middle;
        text-style: bold;
        background: transparent;
    }
    NavMenu > #menu-hamburger:hover {
        text-style: bold reverse;
    }
    NavMenu > #menu-services {
        height: 1fr;
        background: transparent;
    }
    /* Pinned (Settings) list docks to the bottom of the rail so it
       sits visually separated from the service-item list above it,
       matching the macOS Settings-app / VS Code activity-bar pattern.
       The explicit ``height: auto`` lets the single Settings row
       size itself to one line; without it the OptionList would
       default to ``1fr`` and fight ``#menu-services`` for space. */
    NavMenu > #menu-pinned {
        dock: bottom;
        height: auto;
        background: transparent;
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
        # when expanded (click collapses). Tooltip surfaces the
        # action since the glyph alone is ambiguous, especially in
        # the collapsed icon-only state.
        ham = Static("+", id="menu-hamburger", markup=False)
        ham.tooltip = "Toggle nav menu (expand / collapse)"
        yield ham
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

    def _split_items(self) -> tuple[list[NavItemVM], list[NavItemVM]]:
        """Return ``(service_items, pinned_items)`` from the VM's items list.

        The split point is :data:`_SETTINGS_NAV_ID` — everything else is
        a service. Used by ``_rebuild_options`` (during rebuild) and by
        the tooltip-on-highlight handler (mapping a list widget +
        option index back to the originating ``NavItemVM``). Centralised
        here so the partitioning rule lives in one place.
        """
        service_items: list[NavItemVM] = []
        pinned_items: list[NavItemVM] = []
        for item in self._vm.items:
            if item.descriptor.id == _SETTINGS_NAV_ID:
                pinned_items.append(item)
            else:
                service_items.append(item)
        return service_items, pinned_items

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

        service_items, pinned_items = self._split_items()
        selected_id = self._vm.selected_id

        def _add_to(target: OptionList, items: list[NavItemVM]) -> None:
            # ``inject_separator`` flips True after the first
            # mounted option so subsequent options get a blank
            # ``Separator()`` above them — that's the 1-row vertical
            # breathing room between rail rows the user asked for
            # ("There's basically no vertical margin between them").
            # ``Separator`` is a Textual-native OptionList primitive
            # that renders as a single non-selectable row of empty
            # space; PR #81's first attempt at per-option CSS
            # ``padding: 1 0 0 0`` made the OptionList silently
            # stop rendering its contents — turns out
            # ``.option-list--option`` isn't a styling surface
            # Textual exposes for padding the way standard widgets do.
            # Spacer option between consecutive items gives 1 row
            # of vertical breathing room in the rail. Textual's
            # ``OptionList.add_option`` requires a prompt; a
            # ``disabled=True`` Option with a blank prompt renders
            # as an empty non-highlightable row — visually the
            # margin the user asked for. Per-option CSS padding
            # was the first attempt but ``.option-list--option``
            # isn't a styling surface Textual exposes for padding,
            # which silently broke OptionList rendering entirely.
            inject_spacer = False
            for item in items:
                if inject_spacer:
                    target.add_option(Option(" ", disabled=True))
                inject_spacer = True
                descriptor = item.descriptor
                ribbon = _RIBBON_GLYPH if descriptor.id == selected_id else _RIBBON_SPACER
                # ``icon`` is always a str on ServiceDescriptor; the
                # ``or "?"`` guard is defensive for any future
                # optional variant. The full icon string passes
                # through to the formatter (no ``[:2]`` truncation)
                # so multi-codepoint emojis (e.g. ⚙️ U+2699 + VS-16)
                # survive — truncating at 2 Python chars would
                # silently drop the variation selector and flip the
                # rendering to text presentation.
                glyph = descriptor.icon or descriptor.label or "?"
                if self._collapsed:
                    prompt = _format_collapsed_prompt(ribbon=ribbon, glyph=glyph)
                else:
                    prompt = _format_expanded_prompt(
                        ribbon=ribbon, glyph=glyph, label=descriptor.label
                    )
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

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Sync the OptionList's tooltip to the currently-highlighted
        option's full label — the KEYBOARD branch of the per-row
        tooltip approximation. Mouse hover is handled separately in
        :meth:`on_mouse_move` because Textual's OptionList does NOT
        fire ``OptionHighlighted`` on hover (only on arrow-key /
        explicit highlight assignment); without the mouse-move
        branch, hovering a different row keeps showing the LAST
        keyboarded row's tooltip — user feedback (post-PR-#92):
        "Both the S3 and the EMR menu items seem to have the same
        tooltip right now that says 'EMR'".

        Map via the Option's ``id`` (descriptor id), NOT its
        OptionList index. The PR #81 spacer ``Option(" ",
        disabled=True)`` inserted between consecutive nav items
        shifts the OptionList indices so they no longer line up
        with ``_split_items()``'s VM index.
        """
        list_widget = event.option_list
        option_id = event.option_id
        if option_id is None:
            list_widget.tooltip = None
            return
        list_widget.tooltip = self._tooltip_for_option_id(option_id)

    def on_mouse_move(self, event: object) -> None:
        """Mouse-hover branch of the per-row tooltip approximation.

        Textual's ``OptionList._on_mouse_move`` updates an internal
        ``_mouse_hovering_over`` attribute but does NOT fire
        ``OptionHighlighted``; the OptionList-wide ``tooltip``
        attribute therefore stays at whatever the keyboard last
        highlighted. The user-visible symptom: keyboard EMR row
        once, then hover S3 with the mouse — tooltip stays "EMR".

        We read Textual's own hover state directly so the tooltip
        re-resolves on every mouse move within either OptionList.
        Re-reading the framework's state (rather than computing our
        own y-offset → option-index) keeps us robust to per-version
        layout details.
        """
        for list_id in ("#menu-services", "#menu-pinned"):
            try:
                list_widget = self.query_one(list_id, OptionList)
            except Exception:
                continue
            hovered_id = getattr(list_widget, "_mouse_hovering_over", None)
            if hovered_id is None:
                # Mouse left this list (or is over a spacer). Fall
                # back to the keyboard-highlighted tooltip so the
                # popup stays meaningful when the pointer drifts
                # off the rows but stays inside the list bounds.
                highlighted = list_widget.highlighted
                if highlighted is None:
                    list_widget.tooltip = None
                    continue
                opt = list_widget.get_option_at_index(highlighted)
                hovered_id = opt.id
            if hovered_id is None:
                list_widget.tooltip = None
                continue
            list_widget.tooltip = self._tooltip_for_option_id(hovered_id)

    def _tooltip_for_option_id(self, option_id: str) -> str | None:
        for item in self._vm.items:
            if item.descriptor.id == option_id:
                return item.descriptor.label
        return None


__all__ = ["NavMenu"]
