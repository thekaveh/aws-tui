"""NavMenu — always-visible left rail backed by Textual's OptionList.

The rail is **always visible at a single fixed width** and shows the
TEXT LABEL of each service ("S3", "EMR", "Settings") — no icon
emojis, no collapse / expand toggle, no hamburger. User feedback
(post-PR-#93): "Let's remove the possibility of being expanded /
collapsed altogether since all aws services have short names to
begin with and I have decided to simply use 'S3' instead of the
childish bucket emoji, or 'EMR' instead of the fire emoji".

Master-detail keyboard navigation. The rail is part of the app's
Tab cycle (S3: NAV → LEFT → RIGHT; EMR: NAV → LEFT → DETAIL →
LOGS). Once focused, Up / Down arrow keys move the highlight AND
immediately execute ``switch_service_command`` — the user sees
the service swap in lockstep with the cursor, matching how the
S3 file-pane rows behave (master-detail). User feedback: "I should
be able to up or down arrow key among the menu items and in doing
so change the selected item which should result in changing the
service as selected item".

Two OptionLists internally: ``#menu-services`` (top) holds the
service rows; ``#menu-pinned`` (docked bottom) holds the Settings
peer. Splitting the rail this way is the only way to visually
separate "primary navigation" from the always-present Settings
entry — a single OptionList would render Settings immediately
below the last service with empty rows trailing it.

A left-edge ribbon glyph (``▌``) in ``$accent`` precedes the
prompt of the currently-selected service in each OptionList,
matching the file-pane row-selection treatment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import OptionList
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


#: Ribbon glyph prepended to the selected option's prompt in each
#: OptionList. The ``▌`` (LEFT HALF BLOCK, U+258C) is a single cell
#: that visually reads as a vertical bar at the row's left edge —
#: matches the "narrow vertical ribbon" treatment that the file-pane
#: rows use. Non-selected rows use a single space at the same
#: position so the label columns stay aligned.
_RIBBON_GLYPH: str = "▌"
_RIBBON_SPACER: str = " "


def _format_prompt(*, ribbon: str, label: str) -> str:
    """Render one nav option as ``"<ribbon> <label>"``.

    Single source of truth for the nav-rail row chrome. The previous
    two-helper split (collapsed icon-only vs expanded icon+label) is
    gone with the collapse/expand mode — every row is now a single
    fixed format.
    """
    return f"{ribbon} {label}"


class NavMenu(Widget):
    """OptionList-backed always-visible left rail.

    Treated as a regular pane in the app's Tab cycle. Focusing the
    NavMenu lands focus on whichever inner OptionList holds the
    currently-selected service; arrow keys then move the highlight
    AND fire ``switch_service_command`` so the service changes
    in lockstep with the cursor (master-detail).
    """

    DEFAULT_CSS = """
    /* Fixed width — labels are short enough ("S3", "EMR",
       "Settings") that a 12-cell rail leaves comfortable headroom
       for the 2-cell border + 1-cell ribbon + label + trailing
       slack. The rest of the layout (content host) gets
       ``width: 1fr`` so it absorbs the remaining horizontal space.
       Background and border colors come from per-theme ``.tcss``
       (using project tokens, not Textual-core variables) so the
       rail matches the file-pane chrome. */
    NavMenu {
        display: block;
        width: 12;
    }
    NavMenu > #menu-services {
        height: 1fr;
        background: transparent;
    }
    /* Pinned (Settings) list docks to the bottom of the rail so it
       sits visually separated from the service-item list above it,
       matching the macOS Settings-app / VS Code activity-bar
       pattern. The explicit ``height: auto`` lets the single
       Settings row size itself to one line; without it the
       OptionList would default to ``1fr`` and fight
       ``#menu-services`` for space. */
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
        self._sub: DisposableBase | None = None
        # Guard for the master-detail highlight handler — when
        # ``_rebuild_options`` programmatically restores the
        # highlighted index, Textual fires ``OptionHighlighted`` as
        # a side-effect; without this guard we'd re-execute
        # ``switch_service_command`` on every rebuild and the rail
        # would loop forever (rebuild → highlight → switch → rebuild).
        self._suppress_highlight_switch: bool = False

    @property
    def vm(self) -> NavMenuVM:
        return self._vm

    def compose(self) -> ComposeResult:
        # Two OptionLists: services on top, the Settings nav peer
        # docked at the bottom (see DEFAULT_CSS). Settings is split
        # off here as a View concern — the VM still owns ONE ordered
        # items list with Settings as the last entry.
        yield OptionList(id="menu-services")
        yield OptionList(id="menu-pinned")

    def on_mount(self) -> None:
        self._rebuild_options()
        # Subscribe to the VM's property-change broadcasts. NavMenuVM
        # fires PropertyChangedMessage("items") after _rebuild_items
        # and PropertyChangedMessage("selected_id") on selection.
        # Without this subscription the OptionList silently shows
        # stale items after a connection switch (since the VM's
        # items list filters by what the connection supports).
        #
        # Naming: NOT ``_on_message`` — Textual auto-routes any
        # ``_on_*`` method through its async message dispatcher,
        # which collides with reactivex's sync ``on_next`` callable.
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Internal ───────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.sender_object is not self._vm:
            return
        if msg.property_name in ("items", "selected_id"):
            self._rebuild_options()

    def _split_items(self) -> tuple[list[NavItemVM], list[NavItemVM]]:
        """Return ``(service_items, pinned_items)`` from the VM's
        items list. The split point is :data:`_SETTINGS_NAV_ID` —
        everything else is a service. Used by :meth:`_rebuild_options`
        (during rebuild). Centralised here so the partitioning rule
        lives in one place.
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
        """Rebuild both OptionLists to reflect the current items.

        Called on mount and whenever the VM's items or selected_id
        changes. Each option's prompt is prefixed with a single-cell
        ribbon column: ``▌`` (in ``$accent`` per per-theme tcss)
        for the currently-selected option, or a space otherwise. The
        prompt itself is the descriptor's LABEL — no icon glyph.

        The ``_suppress_highlight_switch`` guard wraps the
        ``highlighted = idx`` restoration so the programmatic
        side-effect doesn't re-fire ``switch_service_command`` and
        bounce the rail forever.
        """
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
            # ``inject_spacer`` flips True after the first mounted
            # option so subsequent options get a blank disabled
            # spacer above them — 1-row vertical breathing room.
            # Per-option CSS padding was the first attempt but
            # ``.option-list--option`` isn't a styling surface
            # Textual exposes for padding, which silently broke
            # OptionList rendering entirely.
            inject_spacer = False
            for item in items:
                if inject_spacer:
                    target.add_option(Option(" ", disabled=True))
                inject_spacer = True
                descriptor = item.descriptor
                ribbon = _RIBBON_GLYPH if descriptor.id == selected_id else _RIBBON_SPACER
                target.add_option(
                    Option(_format_prompt(ribbon=ribbon, label=descriptor.label), id=descriptor.id)
                )

        _add_to(services, service_items)
        _add_to(pinned, pinned_items)

        # Restore the cursor highlight on whichever list owns the
        # selected id (so arrow-key navigation starts from the
        # active item when the rail is focused). The
        # ``_suppress_highlight_switch`` guard prevents the
        # programmatic assignment from looping back through
        # ``on_option_list_option_highlighted`` and re-executing
        # ``switch_service_command``.
        if selected_id is None:
            return
        self._suppress_highlight_switch = True
        try:
            for idx, item in enumerate(service_items):
                if item.descriptor.id == selected_id:
                    services.highlighted = idx
                    return
            for idx, item in enumerate(pinned_items):
                if item.descriptor.id == selected_id:
                    pinned.highlighted = idx
                    return
        finally:
            self._suppress_highlight_switch = False

    # ── Event handlers ─────────────────────────────────────────────────────

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Master-detail: arrow-key highlight changes the selected
        service IMMEDIATELY. User feedback: "I should be able to up
        or down arrow key among the menu items and in doing so
        change the selected item which should result in changing
        the service".

        The ``_suppress_highlight_switch`` guard skips the switch
        when ``_rebuild_options`` is the one moving the highlight —
        otherwise every VM-driven rebuild would re-execute the
        switch and the rail would loop.
        """
        if self._suppress_highlight_switch:
            return
        option_id = event.option_id
        if option_id is None:
            # Disabled spacer or out-of-range highlight — nothing to do.
            return
        if option_id == self._vm.selected_id:
            return
        self._vm.switch_service_command.execute(option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Click or Enter on an option. The highlight handler has
        almost certainly already executed the switch (a mouse click
        moves the highlight first), but a direct keyboard Enter on
        the currently-highlighted option is also a valid commit so
        we route it through here defensively. ``event.stop()`` so the
        message doesn't bubble further up the app and trigger a
        second route.
        """
        event.stop()
        if event.option_id is None:
            return
        if event.option_id == self._vm.selected_id:
            return
        self._vm.switch_service_command.execute(event.option_id)


__all__ = ["NavMenu"]
