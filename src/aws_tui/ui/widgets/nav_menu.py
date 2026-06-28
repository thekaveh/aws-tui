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

import contextlib
from typing import TYPE_CHECKING, ClassVar

from textual import events
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
        /* Width: 10 cells. User feedback (post-PR-#97): since all
           AWS service labels are at most three letters and Settings
           is rendered as the gear glyph (⚙️, 2 cells) instead of
           the 8-char "Settings" word, 10 cells fits the longest row
           (ribbon 1 + space 1 + "EMR" 3 = 5 cells of content + the
           per-row padding 2 + NavMenu borders 2 = 9 total) with
           1 cell of slack. */
        width: 10;
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

    # ── Focus chrome coordination ─────────────────────────────────────────────

    def on_focus(self, event: events.Focus) -> None:
        """When the rail gains Textual focus, mark the screen so the
        per-theme CSS can dim the file-pane border. User feedback
        (post-PR-#97): "Both the menu and the left/right panes can
        be shown as focused / selected which again is confusing.
        Only one pane should be selected at a time including the
        menu." The dual file pane keeps its VM-driven ``-focused``
        class for non-visual reasons (transfers / commands routing),
        so we drive the visual via a sibling-scope ``-nav-active``
        class on the Screen instead of mutating the pane VM.
        """
        with contextlib.suppress(Exception):
            self.screen.add_class("-nav-active")

    def on_blur(self, event: events.Blur) -> None:
        """Symmetric to :meth:`on_focus`: drop the screen-level
        marker so the file pane lights up again."""
        with contextlib.suppress(Exception):
            self.screen.remove_class("-nav-active")

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
        """ENTER on a service row = "go INTO this service": ensure the
        service is the active one (switching if needed) AND shift
        Textual focus to the service's default pane.

        User feedback (post-PR-#100): "everytime ENTER is pressed,
        while inside the menu and on a specific service, then the
        focus shifted to the next semantically logical pane inside
        that service's screen. If on S3, I want the focus shifted to
        the left pane IFF ENTER is pressed. When EMR is selected and
        ENTER is pressed, I want focus shifted to the job runs and so
        on."

        Mechanism:
        1. Drop NavMenu's own Textual focus. Each destination page's
           ``_maybe_focus_*`` auto-focus is gated on "is NavMenu
           focused?" (added post-PR-#98 so arrow-walking the rail
           wouldn't steal focus into the destination pane mid-cursor-
           walk). For an ENTER commit we WANT focus to leave NavMenu,
           so we release the gate first.
        2. If the target service isn't already active, kick off the
           content-host swap. The new page's ``on_mount`` auto-focus
           lands focus on its default pane when NavMenu doesn't own
           focus — handling the swap case asynchronously.
        3. Call ``app.focus_active_service_pane`` to handle the
           already-active and already-mounted case (where no
           ``on_mount`` will fire). When the page isn't mounted yet,
           this is a silent no-op and step 2's mount-time auto-focus
           is the safety net.
        """
        if not self._items:
            return
        target_id = self._items[self._cursor_index].descriptor.id
        needs_switch = target_id != self._vm.selected_id
        app = self.app
        with contextlib.suppress(Exception):
            app.set_focus(None)
        if needs_switch:
            self._vm.switch_service_command.execute(target_id)
        focus_active = getattr(app, "focus_active_service_pane", None)
        if callable(focus_active):
            focus_active()

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
        # pushed to the bottom by the flex spacer). The Settings row
        # uses the descriptor's ICON (a gear glyph ``⚙️``) instead of
        # the textual ``Settings`` label so the rail can stay narrow.
        # User feedback (post-PR-#97): "Let's switch back the Settings
        # to the gear emoji and then make the menu pane narrower."
        for idx, item in enumerate(self._items):
            is_settings = item.descriptor.id == _SETTINGS_NAV_ID
            display = item.descriptor.icon if is_settings else item.descriptor.label
            row = NavRow(
                descriptor_id=item.descriptor.id,
                label=display,
                is_selected=(idx == self._cursor_index),
                is_settings=is_settings,
            )
            if is_settings:
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
        4. Re-grab Textual focus AFTER the swap. The newly-mounted
           page's ``on_mount`` calls ``call_after_refresh(<pane>.focus)``
           to land focus on its LEFT pane — stealing focus from the
           rail mid-arrow-walk. User feedback (post-PR-#97): "I can
           use arrow keys to move down to EMR, but then the focus
           automatically is out of the menu and into the job runs
           which means I can't use arrow keys to move further down
           the menu". Queueing our ``self.focus`` after the page's
           ``call_after_refresh`` puts NavMenu LAST in the
           run-after-next-refresh queue, so we win the focus race.
        """
        self._repaint_rows()
        if 0 <= self._cursor_index < len(self._items):
            target_id = self._items[self._cursor_index].descriptor.id
            if target_id != self._vm.selected_id:
                self._vm.switch_service_command.execute(target_id)
                self.call_after_refresh(self.focus)

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
