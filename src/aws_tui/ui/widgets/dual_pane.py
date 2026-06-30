"""DualPane widget — composes left + right :class:`Pane` widgets.

Layout is a horizontal container split 50/50. The focused pane reflects
:class:`DualPaneVM.focused` via the ``-focused`` CSS class on the active
:class:`Pane`.
"""

from __future__ import annotations

import contextlib

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM, FocusedPane


class DualPane(HubSubscriberMixin, Widget):
    """Two-pane file manager."""

    DEFAULT_CSS = """
    DualPane {
        layout: horizontal;
        height: 1fr;
    }

    DualPane > Horizontal {
        width: 100%;
        height: 100%;
        layout: horizontal;
    }

    DualPane Pane {
        width: 1fr;
        height: 100%;
    }
    """

    def __init__(
        self,
        vm: DualPaneVM,
        *,
        hub: MessageHub[Message],
        focus_coordinator: FocusCoordinatorVM | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: DualPaneVM = vm
        self._hub: MessageHub[Message] = hub
        self._focus_coordinator: FocusCoordinatorVM | None = focus_coordinator
        self._left_widget: Pane | None = None
        self._right_widget: Pane | None = None

    @property
    def vm(self) -> DualPaneVM:
        return self._vm

    @property
    def left_widget(self) -> Pane | None:
        return self._left_widget

    @property
    def right_widget(self) -> Pane | None:
        return self._right_widget

    def compose(self) -> ComposeResult:
        self._left_widget = Pane(self._vm.left, hub=self._hub, id="pane-left")
        self._right_widget = Pane(self._vm.right, hub=self._hub, id="pane-right")
        with Horizontal():
            yield self._left_widget
            yield self._right_widget

    def on_mount(self) -> None:
        self._sync_focus()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "focused":
            self.call_after_refresh(self._sync_focus)

    def _sync_focus(self) -> None:
        if self._left_widget is None or self._right_widget is None:
            return
        focused = self._vm.focused
        self._left_widget.set_focused(focused is FocusedPane.LEFT)
        self._right_widget.set_focused(focused is FocusedPane.RIGHT)
        # Project the new pane focus through the coordinator. The
        # coordinator is the single source of truth for which slot
        # holds focus app-wide; without this projection the
        # ``-rail-active`` Screen class set by NavMenu.on_focus
        # stays put when Tab / mouse-click moves focus from the
        # rail into a file pane, and the per-theme
        # ``Screen.-rail-active Pane.-focused`` rule dims the
        # focused pane's border instead of highlighting it. The
        # MVVM projection is symmetric with NavMenu.on_focus →
        # set_focused_slot(NAV_MENU); now the pane move also drives
        # the coordinator slot.
        if self._focus_coordinator is not None:
            slot = FocusSlot.S3_LEFT if focused is FocusedPane.LEFT else FocusSlot.S3_RIGHT
            self._focus_coordinator.set_focused_slot(slot)

    def focus_focused_pane(self) -> None:
        """Move Textual focus to whichever pane the VM marks active.

        Used by ``AwsTuiApp.action_switch_focus`` when Tab cycles
        back from the NavMenu into the dual-pane area: the focus
        should land on the VM-tracked active pane so subsequent
        keystrokes go where the user expects.
        """
        focused = self._vm.focused
        target = self._left_widget if focused is FocusedPane.LEFT else self._right_widget
        if target is not None:
            target.focus()

    def focus_left_pane(self) -> None:
        """Make the LEFT pane the visually-active pane and route arrow
        keys to it. Called when the user explicitly requests "enter
        the S3 service" (NavMenu ENTER). :class:`Pane` widgets decline
        Textual focus by design — the visual indicator is driven by
        :class:`DualPaneVM.focused` via the ``-focused`` CSS class, and
        arrow keys reach the left pane through ``App._move_cursor`` →
        ``dual.focused_pane.move_cursor_command``. So "focus LEFT"
        means (a) drop any App-level focus that might be elsewhere
        (NavMenu) and (b) ensure the VM is on LEFT.
        """
        with contextlib.suppress(Exception):
            self.app.set_focus(None)
        if self._vm.focused is FocusedPane.LEFT:
            return
        # ``switch_focus_command`` is a typed property on DualPaneVM —
        # access it OUTSIDE the suppress so a rename / retire of the
        # command breaks the call site loudly. The suppress wraps
        # ``execute()`` only so a relay-task failure doesn't crash
        # the widget.
        cmd = self._vm.switch_focus_command
        with contextlib.suppress(Exception):
            cmd.execute()


__all__ = ["DualPane"]
