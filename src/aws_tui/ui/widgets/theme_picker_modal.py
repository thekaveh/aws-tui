"""ThemePickerModal — keyboard-navigable theme picker.

Dedicated modal screen so the user can iterate themes with up/down/k/j
and apply with Enter (mouse still works too). Binds to the existing
:class:`ThemePickerVM`; each row mirrors a :class:`ThemeOptionVM`.

Binding strategy: every navigation key is declared **both** as a
``priority=True`` binding AND handled in :meth:`on_key` as
defense-in-depth. The priority binding wins the dispatch race against
the App's own ``priority=True`` arrow bindings (Textual iterates the
binding chain *reversed* for priority lookups, so the modal — being on
top of the screen stack — fires first). The :meth:`on_key` fallback
covers the edge case where a key isn't declared in the binding map.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.theme_picker_vm import ThemeOptionVM, ThemePickerVM


class _ThemeRow(HubSubscriberMixin, Static):
    """One selectable theme row. Owns no state — observes its
    :class:`ThemeOptionVM` for ``is_active`` and re-renders on change.

    Cursor highlight (the row the user is about to apply) is tracked by
    the modal via the ``-cursor`` CSS class — separate from
    ``is_active`` (the currently-applied theme)."""

    DEFAULT_CSS = """
    _ThemeRow {
        height: 1;
        padding: 0 2;
    }
    """

    def __init__(self, vm: ThemeOptionVM, *, hub: MessageHub[Message]) -> None:
        super().__init__(self._format(vm))
        self._vm: ThemeOptionVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def theme_name(self) -> str:
        return self._vm.name

    @staticmethod
    def _format(vm: ThemeOptionVM) -> str:
        return f"  {vm.marker_glyph}  {vm.name}"

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def _on_property_changed(self, property_name: str) -> None:
        if property_name == "is_active":
            self.update(self._format(self._vm))

    def set_cursor(self, value: bool) -> None:
        if value:
            self.add_class("-cursor")
        else:
            self.remove_class("-cursor")


class ThemePickerModal(ModalScreen[None]):
    """Modal that lets the user pick a theme by keyboard or mouse."""

    DEFAULT_CSS = """
    ThemePickerModal {
        align: center middle;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        # priority=True so these win against the App's own priority arrow
        # bindings (Textual iterates the binding chain in reverse for
        # priority lookups → modal at the top of the stack fires first).
        Binding("up,k", "move_up", "↑", show=False, priority=True),
        Binding("down,j", "move_down", "↓", show=False, priority=True),
        Binding("enter", "apply", "Apply", show=True, priority=True),
        Binding("escape,q,t", "close", "Close", show=True, priority=True),
    ]

    def __init__(self, *, picker: ThemePickerVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._picker: ThemePickerVM = picker
        self._hub: MessageHub[Message] = hub
        names = [opt.name for opt in picker.options]
        try:
            self._cursor: int = names.index(picker.active_theme)
        except ValueError:
            self._cursor = 0
        self._rows: list[_ThemeRow] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-frame"):
            yield Static("Pick a theme", id="picker-title")
            with VerticalScroll():
                for opt in self._picker.options:
                    yield _ThemeRow(opt, hub=self._hub)
            yield Static("↑/↓ move · Enter apply · Esc close", id="picker-help")

    def on_mount(self) -> None:
        self._rows = list(self.query(_ThemeRow))
        self._sync_cursor_class()

    async def on_key(self, event: Key) -> None:
        """Defense-in-depth: if a priority-binding mismatch lets a key
        slip past the binding system, catch it here at the event layer."""
        if event.key in ("up", "k"):
            self._move_by(-1)
            event.stop()
        elif event.key in ("down", "j"):
            self._move_by(1)
            event.stop()
        elif event.key == "enter":
            self.action_apply()
            event.stop()

    def on_click(self, event: object) -> None:
        target = getattr(event, "control", None) or getattr(event, "widget", None)
        node: object | None = target
        while node is not None:
            if isinstance(node, _ThemeRow):
                self._picker.pick_theme_command.execute(node.theme_name)
                self.dismiss(None)
                return
            node = getattr(node, "parent", None)

    # ── Actions (non-parameterized so the action-router can dispatch them) ──

    def action_move_up(self) -> None:
        self._move_by(-1)

    def action_move_down(self) -> None:
        self._move_by(1)

    def action_apply(self) -> None:
        if not self._rows:
            self.dismiss(None)
            return
        row = self._rows[self._cursor]
        self._picker.pick_theme_command.execute(row.theme_name)
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _move_by(self, delta: int) -> None:
        if not self._rows:
            return
        new = max(0, min(self._cursor + delta, len(self._rows) - 1))
        if new == self._cursor:
            return
        self._cursor = new
        self._sync_cursor_class()

    def _sync_cursor_class(self) -> None:
        for i, row in enumerate(self._rows):
            row.set_cursor(i == self._cursor)


__all__ = ["ThemePickerModal"]
