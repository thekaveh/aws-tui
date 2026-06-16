"""ThemePickerModal — keyboard-navigable theme picker.

Dedicated modal screen so the user can iterate themes with up/down/k/j
and apply with Enter (mouse still works too). Binds to the existing
:class:`ThemePickerVM`; each row mirrors a :class:`ThemeOptionVM`.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
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
    _ThemeRow.-cursor {
        background: $boost;
        text-style: bold;
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
    """Modal that lets the user pick a theme by keyboard or mouse.

    Bindings:

    - ``up``/``k`` and ``down``/``j``: move the cursor between rows.
    - ``enter``: apply the highlighted theme and close.
    - ``escape`` / ``q`` / ``t``: close without changing.
    - Mouse click on a row: apply that theme and close.
    """

    DEFAULT_CSS = """
    ThemePickerModal {
        align: center middle;
    }
    ThemePickerModal > #picker-frame {
        width: 48;
        max-height: 20;
        padding: 1 0;
    }
    ThemePickerModal #picker-title {
        text-style: bold;
        text-align: center;
        width: 100%;
        padding: 0 2 1 2;
    }
    ThemePickerModal #picker-help {
        text-align: center;
        width: 100%;
        padding: 1 2 0 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,t", "dismiss", "Close", show=True, priority=True),
        Binding("up,k", "move(-1)", "Up", show=True, priority=True),
        Binding("down,j", "move(1)", "Down", show=True, priority=True),
        Binding("enter", "apply", "Apply", show=True, priority=True),
    ]

    def __init__(self, *, picker: ThemePickerVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._picker: ThemePickerVM = picker
        self._hub: MessageHub[Message] = hub
        # Start the cursor on the currently-active theme so Enter without
        # any nav re-applies the current selection (harmless and intuitive).
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

    # ── Mouse ──────────────────────────────────────────────────────────────

    def on_click(self, event: object) -> None:
        # Hit-test which row was clicked (if any) and apply it.
        target = getattr(event, "control", None) or getattr(event, "widget", None)
        if target is None:
            return
        node: object | None = target
        while node is not None:
            if isinstance(node, _ThemeRow):
                self._picker.pick_theme_command.execute(node.theme_name)
                self.dismiss(None)
                return
            node = getattr(node, "parent", None)

    # ── Actions ────────────────────────────────────────────────────────────

    def action_move(self, delta: int) -> None:
        if not self._rows:
            return
        new = max(0, min(self._cursor + delta, len(self._rows) - 1))
        if new == self._cursor:
            return
        self._cursor = new
        self._sync_cursor_class()

    def action_apply(self) -> None:
        if not self._rows:
            self.dismiss(None)
            return
        row = self._rows[self._cursor]
        self._picker.pick_theme_command.execute(row.theme_name)
        self.dismiss(None)

    def action_dismiss(self, _result: object = None) -> None:  # type: ignore[override]
        self.dismiss(None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _sync_cursor_class(self) -> None:
        for i, row in enumerate(self._rows):
            row.set_cursor(i == self._cursor)


__all__ = ["ThemePickerModal"]
