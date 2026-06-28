"""NavMenu — always-visible left rail of NavRows.

The rail mounts one :class:`NavRow` per item (services + Settings)
directly as child widgets. NO OptionList. Each NavRow uses the
SAME ``entry-row`` CSS class the S3 file-pane rows use — so the
visuals (height, padding, highlight, selected ribbon, theme
colors) match the file panes by construction. User feedback
(post-PR-#94): "I expect to see exactly the same style of text,
highlights, selected items, current theme, thin vertical ribbon
etc that is currently applied to the selected and non-selected
items inside the left or right s3 panes".

Settings is just the last row, visually pushed to the bottom by
a flex-height spacer Static. Arrow keys move the cursor across
ALL rows including Settings — no Tab-jump between separate
OptionLists. User feedback (same PR): "I expect to be able to
easily move between the menu items using arrow keys (including
the settings item)".

Master-detail keyboard nav: cursor changes execute
``switch_service_command.execute(<descriptor_id>)`` immediately,
matching the LEFT pane's "selection follows cursor" pattern.

The NavMenu participates in the App's Tab cycle as a regular
focusable pane (S3: NAV → LEFT → RIGHT; EMR: NAV → LEFT →
DETAIL → LOGS — wired in :mod:`aws_tui.app`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.events import Click
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.ui.widgets.nav_row import NavRow

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase

    from aws_tui.vm.nav_menu_vm import NavItemVM, NavMenuVM


#: Canonical id for the synthetic Settings nav peer (defined by
#: NavMenuVM._rebuild_items). The Settings row is pushed to the
#: bottom of the rail via a flex spacer; logically it's still
#: in the same cursor-navigable list as the services.
_SETTINGS_NAV_ID: str = "settings"


class NavMenu(Widget, can_focus=True):
    """Always-visible left rail of :class:`NavRow` widgets."""

    DEFAULT_CSS: ClassVar[str] = """
    NavMenu {
        display: block;
        /* Width: 14 cells leaves room for the rail's longest label
           (``Settings`` = 8 chars) plus the ``<ribbon><space>``
           prefix and the per-row padding without clipping. */
        width: 14;
        /* Fill the available vertical space so the flex spacer
           below has room to push Settings to the bottom. Without
           an explicit ``height: 1fr`` the rail collapses to the
           sum-of-children height and Settings ends up immediately
           below the last service row instead of docked at the
           bottom. */
        height: 1fr;
        layout: vertical;
    }
    /* The spacer is the flex-height filler between the services
       and the Settings row. Pushes Settings to the bottom of the
       rail without needing dock: bottom on a separate container
       (which was the prior OptionList approach). */
    NavMenu > #menu-spacer {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up,k", "cursor_up", "Up", show=False),
        Binding("down,j", "cursor_down", "Down", show=False),
        Binding("enter", "commit", "Open", show=False),
    ]

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
        # Flat ordered list of every visible item (services then
        # Settings). Cursor moves across this whole list — there
        # is NO separation between services and Settings at the
        # navigation level. ``_cursor_index`` indexes into it.
        self._items: list[NavItemVM] = []
        self._cursor_index: int = 0

    @property
    def vm(self) -> NavMenuVM:
        return self._vm

    def compose(self) -> ComposeResult:
        # Two Vertical containers separated by a flex spacer:
        # services land in the top container, Settings in the
        # bottom. The spacer is what pushes Settings against the
        # rail's bottom border regardless of how many services
        # are present.
        yield Vertical(id="menu-services-rows")
        yield Static("", id="menu-spacer")
        yield Vertical(id="menu-settings-rows")

    def on_mount(self) -> None:
        self.border_title = "menu"
        self._rebuild_rows()
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_cursor_up(self) -> None:
        if not self._items:
            return
        if self._cursor_index > 0:
            self._cursor_index -= 1
            self._after_cursor_move()

    def action_cursor_down(self) -> None:
        if not self._items:
            return
        if self._cursor_index + 1 < len(self._items):
            self._cursor_index += 1
            self._after_cursor_move()

    def action_commit(self) -> None:
        """Enter on a row is the same as the cursor landing on
        it — the master-detail switch already fired on the
        cursor move. Here purely for parity with the file pane's
        ``Enter to open`` affordance: a defensive re-execute in
        case the row hasn't been switched to yet (e.g., the
        cursor never moved since mount)."""
        if not self._items:
            return
        target_id = self._items[self._cursor_index].descriptor.id
        if target_id != self._vm.selected_id:
            self._vm.switch_service_command.execute(target_id)

    # ── Mouse ────────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        """Click on a row → cursor moves to that row + executes
        the switch. Mirrors the file pane's click semantics."""
        target: object | None = event.widget
        while target is not None:
            if isinstance(target, NavRow):
                target_id = target.descriptor_id
                for idx, item in enumerate(self._items):
                    if item.descriptor.id == target_id:
                        if idx != self._cursor_index:
                            self._cursor_index = idx
                            self._after_cursor_move()
                        elif target_id != self._vm.selected_id:
                            self._vm.switch_service_command.execute(target_id)
                        return
                return
            target = getattr(target, "parent", None)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.sender_object is not self._vm:
            return
        if msg.property_name in ("items", "selected_id"):
            self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        """Re-mount all NavRows from the current VM items list.

        Settings is split off into the ``#menu-settings-rows``
        container so the flex spacer pushes it to the bottom.
        Cursor index is preserved if the underlying row still
        exists; otherwise clamped to a valid range.
        """
        try:
            services_container = self.query_one("#menu-services-rows", Vertical)
            settings_container = self.query_one("#menu-settings-rows", Vertical)
        except Exception:
            return  # Not mounted yet.
        services_container.remove_children()
        settings_container.remove_children()

        # Try to preserve the cursor's current selection across
        # the rebuild. If the user's previous cursor row vanished
        # (e.g., a connection-switch dropped one service), fall
        # back to whichever row the VM's ``selected_id`` points
        # at, or 0.
        prior_id = self._items[self._cursor_index].descriptor.id if self._items else None
        self._items = list(self._vm.items)
        selected_id = self._vm.selected_id
        if prior_id is not None:
            for idx, item in enumerate(self._items):
                if item.descriptor.id == prior_id:
                    self._cursor_index = idx
                    break
            else:
                self._cursor_index = self._index_of(selected_id, default=0)
        else:
            self._cursor_index = self._index_of(selected_id, default=0)

        # Mount the rows. Services first, Settings last (visually
        # pushed to the bottom by the flex spacer).
        for idx, item in enumerate(self._items):
            row = NavRow(
                descriptor_id=item.descriptor.id,
                label=item.descriptor.label,
                is_selected=(idx == self._cursor_index),
                is_settings=(item.descriptor.id == _SETTINGS_NAV_ID),
            )
            if item.descriptor.id == _SETTINGS_NAV_ID:
                settings_container.mount(row)
            else:
                services_container.mount(row)

    def _index_of(self, descriptor_id: str | None, *, default: int) -> int:
        if descriptor_id is None:
            return default
        for idx, item in enumerate(self._items):
            if item.descriptor.id == descriptor_id:
                return idx
        return default

    def _after_cursor_move(self) -> None:
        """Cursor moved by arrow key or click. Three things:

        1. Re-paint row chrome so the new cursor row carries the
           ``-selected`` class (and the previous one drops it).
        2. Scroll the cursor row into view if needed.
        3. Execute the master-detail switch — user feedback:
           "arrow key among the menu items … should result in
           changing the service as selected item, or selecting
           the settings".
        """
        self._repaint_rows()
        # Hand off the switch immediately. The VM dispatches the
        # content-host swap asynchronously, so the arrow key
        # remains responsive.
        if 0 <= self._cursor_index < len(self._items):
            target_id = self._items[self._cursor_index].descriptor.id
            if target_id != self._vm.selected_id:
                self._vm.switch_service_command.execute(target_id)

    def _repaint_rows(self) -> None:
        """Flip the ``-selected`` class on every mounted row to
        match the current cursor index. Avoids a full re-mount on
        every arrow keypress."""
        for row in self.query(NavRow):
            on_cursor = (
                0 <= self._cursor_index < len(self._items)
                and row.descriptor_id == self._items[self._cursor_index].descriptor.id
            )
            row.set_class(on_cursor, "-selected")


__all__ = ["NavMenu"]
