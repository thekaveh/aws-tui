"""Help + theme-picker modal — strict MVVM.

The theme-picker section is backed by :class:`ThemePickerVM`; each row is
a :class:`ThemeOptionVM` bound to its own :class:`ThemeOptionRow` view.
Clicks fire ``pick_theme_command``; the View never reaches into the App
service directly.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.theme_picker_vm import ThemeOptionVM, ThemePickerVM


class ThemeOptionRow(HubSubscriberMixin, Widget):
    """One clickable row in the theme picker — bound to a :class:`ThemeOptionVM`.

    Listens for ``is_active`` PropertyChanged on the hub so the glyph
    swaps live without the parent modal having to re-mount the section.
    """

    DEFAULT_CSS = """
    ThemeOptionRow {
        height: 1;
        padding: 0 2;
    }
    ThemeOptionRow.-active {
        background: $boost;
        text-style: bold;
    }
    """

    def __init__(
        self,
        vm: ThemeOptionVM,
        *,
        hub: MessageHub[Message],
        picker: ThemePickerVM,
    ) -> None:
        super().__init__(classes="theme-entry " + ("-active" if vm.is_active else ""))
        self._vm: ThemeOptionVM = vm
        self._hub: MessageHub[Message] = hub
        self._picker: ThemePickerVM = picker

    def render(self) -> str:
        return f"  {self._vm.marker_glyph}  {self._vm.name}"

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "is_active":
            if self._vm.is_active:
                self.add_class("-active")
            else:
                self.remove_class("-active")
            self.refresh()

    def on_click(self, _event: object) -> None:
        self._picker.pick_theme_command.execute(self._vm.name)


class HelpModal(ModalScreen[None]):
    """Help overlay listing keybindings, mouse, and a VM-backed theme picker."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > #help-frame {
        width: 78;
        max-height: 36;
        padding: 1 0;
        border: round $accent;
        background: $surface;
    }
    HelpModal #help-title {
        color: $accent;
        text-style: bold;
        padding: 0 2 1 2;
        text-align: center;
        width: 100%;
    }
    HelpModal #help-subtitle {
        color: $text-muted;
        padding: 0 2 1 2;
        text-align: center;
        width: 100%;
    }
    HelpModal VerticalScroll {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    HelpModal .help-section {
        color: $accent;
        text-style: bold;
        padding: 1 2 0 2;
    }
    HelpModal .help-row {
        padding: 0 2;
    }
    HelpModal .help-dim {
        color: $text-muted;
        padding: 0 2;
    }
    HelpModal #help-footer {
        padding: 1 2 0 2;
        color: $text-muted;
        text-align: center;
        width: 100%;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,question_mark,q,colon", "dismiss", "Close", show=True, priority=True),
    ]

    def __init__(self, *, theme_picker: ThemePickerVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._theme_picker: ThemePickerVM = theme_picker
        self._hub: MessageHub[Message] = hub

    def compose(self) -> ComposeResult:
        with Vertical(id="help-frame"):
            yield Static("aws-tui — help", id="help-title")
            yield Static(
                "keyboard shortcuts · mouse · theme · docs",
                id="help-subtitle",
            )
            with VerticalScroll():
                yield Static("Navigation", classes="help-section")
                for key, label in (
                    ("Tab / Shift+Tab", "switch pane focus"),
                    ("↑ / k     ↓ / j", "move cursor"),
                    ("Enter", "descend into directory (or ascend on '..')"),
                    ("Backspace / ←", "ascend to parent"),
                    ("r", "refresh focused pane"),
                ):
                    yield self._key_row(key, label)

                yield Static("Mouse", classes="help-section")
                yield self._key_row("Click pane", "switch focus to it")
                yield self._key_row("Click row", "move cursor")
                yield self._key_row("Click again", "descend / ascend on '..'")

                yield Static("App", classes="help-section")
                yield self._key_row("?  or  :", "this help overlay")
                yield self._key_row("q / Ctrl+C", "quit")

                yield Static("Themes (click to switch)", classes="help-section")
                for option in self._theme_picker.options:
                    yield ThemeOptionRow(option, hub=self._hub, picker=self._theme_picker)

                yield Static("Docs", classes="help-section")
                yield Static(
                    "  docs/connections.md       config schema + vendor quirks\n"
                    "  docs/theming.md           palette tokens + override recipes\n"
                    "  docs/keybindings.md       full action-id table\n"
                    "  docs/cookbook.md          step-by-step recipes",
                    classes="help-dim",
                )
            yield Static("press ?  /  Esc  to close", id="help-footer")

    def _key_row(self, key: str, label: str) -> Static:
        return Static(
            f"  [b cyan]{key:<18}[/]  [dim]{label}[/]",
            classes="help-row",
            markup=True,
        )


__all__ = ["HelpModal", "ThemeOptionRow"]
