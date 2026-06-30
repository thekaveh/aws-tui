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
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.nav_menu_vm import SETTINGS_NAV_ID

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase

    from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM
    from aws_tui.vm.nav_menu_vm import NavItemVM, NavMenuVM


#: Round-3 / PR #101: per-service default focus slot. On ENTER,
#: ``action_commit`` projects this slot through the coordinator so
#: VM subscribers observe the user's intent to "enter" the service
#: without the View-layer having to inspect the destination page's
#: widget tree. Mapping uses the same service ids ``ServiceDescriptor``
#: registers (``ServiceRegistry.register``).
_SERVICE_DEFAULT_SLOT: dict[str, FocusSlot] = {
    "s3": FocusSlot.S3_LEFT,
    "emr-serverless": FocusSlot.EMR_RUNS,
    SETTINGS_NAV_ID: FocusSlot.SETTINGS,
}


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
        focus_coordinator: FocusCoordinatorVM | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: NavMenuVM = vm
        self._hub: MessageHub[Message] = hub
        self._focus_coordinator: FocusCoordinatorVM | None = focus_coordinator
        self._coord_sub: DisposableBase | None = None
        self._sub: DisposableBase | None = None
        # Flat ordered list of every visible item (services then
        # Settings). Cursor moves across this whole list — there
        # is NO separation between services and Settings at the
        # navigation level. ``_cursor_index`` indexes into it.
        self._items: list[NavItemVM] = []
        # Round-3 directive (spec §9.bis.11, §3.2.bis row 1): the
        # cursor index is NO LONGER a stored field. It's derived on
        # demand from ``vm.selected_id`` (the VM-owned canonical
        # slot). Arrow handlers compute the NEXT id and call
        # ``switch_service_command.execute(next_id)`` instead of
        # mutating a local index.

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
        # Subscribe to the focus coordinator (round-3 / §4.3 / Phase
        # 7) so the screen's ``-rail-active`` class follows the VM-
        # owned slot, not direct Screen-class mutation on this
        # widget's focus events.
        if self._focus_coordinator is not None:
            self._coord_sub = self._focus_coordinator.on_focused_slot_changed.subscribe(
                on_next=self._apply_focus_slot_class
            )
            # Apply the initial state once.
            self._apply_focus_slot_class(self._focus_coordinator.focused_slot)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        if self._coord_sub is not None:
            self._coord_sub.dispose()
            self._coord_sub = None

    # ── Focus chrome coordination ─────────────────────────────────────────────

    def on_focus(self, event: events.Focus) -> None:
        """When the rail gains Textual focus, project the slot
        through the :class:`FocusCoordinatorVM` (round-3 / §4.3).
        The coordinator's ``on_focused_slot_changed`` subscriber
        (wired in :meth:`on_mount`) is what mutates the
        Screen's class — no direct Screen mutation here. Fallback to
        the legacy direct mutation when no coordinator is wired
        (e.g. test harnesses that haven't migrated yet)."""
        if self._focus_coordinator is not None:
            self._focus_coordinator.set_focused_slot(FocusSlot.NAV_MENU)
            return
        with contextlib.suppress(Exception):
            self.screen.add_class("-rail-active")

    def on_blur(self, event: events.Blur) -> None:
        """Symmetric to :meth:`on_focus`. When a coordinator is
        wired, blurring doesn't directly demote the slot — the slot
        only changes when something ELSE gains focus and projects
        its own slot through the coordinator. The class mutation is
        handled by the coordinator subscription."""
        if self._focus_coordinator is not None:
            return
        with contextlib.suppress(Exception):
            self.screen.remove_class("-rail-active")

    def _apply_focus_slot_class(self, slot: object) -> None:
        """Mutate the Screen's ``-rail-active`` class from the
        coordinator's projected slot. Single source of truth: this
        is the only place the class is touched when a coordinator is
        wired."""
        with contextlib.suppress(Exception):
            screen = self.screen
            if slot is FocusSlot.NAV_MENU:
                screen.add_class("-rail-active")
            else:
                screen.remove_class("-rail-active")

    # ── Actions ──────────────────────────────────────────────────────────────

    def _cursor_index(self) -> int:
        """Derived cursor index over ``vm.selected_id``. Returns 0
        when no service is selected (the default focus row)."""
        return self._index_of(self._vm.selected_id, default=0)

    def action_cursor_up(self) -> None:
        if not self._items:
            return
        cur = self._cursor_index()
        if cur <= 0:
            return
        target_id = self._items[cur - 1].descriptor.id
        self._after_cursor_move(target_id)

    def action_cursor_down(self) -> None:
        if not self._items:
            return
        cur = self._cursor_index()
        if cur + 1 >= len(self._items):
            return
        target_id = self._items[cur + 1].descriptor.id
        self._after_cursor_move(target_id)

    def _after_cursor_move(self, target_id: str) -> None:
        """Cursor moved by arrow key or click — drive the VM's
        selection slot; the View paints from the resulting
        PropertyChangedMessage.

        Round-3 directive §9.bis.11 / PR #98(2) closure: when a
        :class:`FocusCoordinatorVM` is wired (the live app), the
        ``call_after_refresh(self.focus)`` re-grab is unnecessary —
        the destination page's :meth:`_maybe_focus_*` chain reads
        the coordinator's `focused_slot` (which is `NAV_MENU` here
        because :meth:`on_focus` projected it) and bails on the
        rail-walk gate. No focus race, no defensive re-grab. The
        legacy re-grab fallback is preserved when no coordinator is
        wired so test harnesses that haven't migrated still pass.
        """
        if target_id == self._vm.selected_id:
            return
        self._vm.switch_service_command.execute(target_id)
        if self._focus_coordinator is None:
            self.call_after_refresh(self.focus)

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
        target_id = self._items[self._cursor_index()].descriptor.id
        needs_switch = target_id != self._vm.selected_id
        app = self.app
        with contextlib.suppress(Exception):
            app.set_focus(None)
        if needs_switch:
            self._vm.switch_service_command.execute(target_id)
        # Round-3 directive §9.bis.11 / PR #101 closure: project the
        # service's default focus slot through the coordinator so VM-
        # side subscribers observe the user's intent to "enter" the
        # service. The App-level :meth:`focus_active_service_pane`
        # below still drives the actual Textual `set_focus` call —
        # the coordinator becomes the data source, the dispatcher
        # remains the View-side projection mechanism.
        if self._focus_coordinator is not None:
            slot = _SERVICE_DEFAULT_SLOT.get(target_id)
            if slot is not None:
                self._focus_coordinator.set_focused_slot(slot)
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
                        if idx != self._cursor_index():
                            self._after_cursor_move(target_id)
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
        # ``items`` changes require re-mounting the row widgets;
        # ``selected_id`` only flips the ``-selected`` class on the
        # already-mounted rows. Routing the lighter path through
        # ``_repaint_rows`` honours the per-arrow-keypress
        # responsiveness target the method's docstring records.
        if msg.property_name == "items":
            self._rebuild_rows()
        elif msg.property_name == "selected_id":
            self._repaint_rows()

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

        self._items = list(self._vm.items)
        cursor_idx = self._cursor_index()
        # Mount the rows. Services first, Settings last (visually
        # pushed to the bottom by the flex spacer). The Settings row
        # uses the descriptor's ICON (a gear glyph ``⚙️``) instead of
        # the textual ``Settings`` label so the rail can stay narrow.
        # User feedback (post-PR-#97): "Let's switch back the Settings
        # to the gear emoji and then make the menu pane narrower."
        for idx, item in enumerate(self._items):
            is_settings = item.descriptor.id == SETTINGS_NAV_ID
            display = item.descriptor.icon if is_settings else item.descriptor.label
            row = NavRow(
                descriptor_id=item.descriptor.id,
                label=display,
                is_selected=(idx == cursor_idx),
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

    def _repaint_rows(self) -> None:
        """Flip the ``-selected`` class on every mounted row to
        match the current cursor (derived from ``vm.selected_id``).
        Avoids a full re-mount on every arrow keypress."""
        selected_id = self._vm.selected_id
        for row in self.query(NavRow):
            row.set_class(row.descriptor_id == selected_id, "-selected")


__all__ = ["NavMenu"]
