"""HelpModal — read-only overlay listing keybindings, mouse, and docs.

Theme switching lives in its own keyboard-navigable
:class:`ThemePickerModal` (press ``t``) — keeps the help modal focused
on documentation and avoids cramming a stateful list inside a static
overlay.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from aws_tui.infra.keymap_store import KeymapStore

_KEY_LABELS: dict[str, str] = {
    "backspace": "Backspace",
    "enter": "Enter",
    "escape": "Esc",
    "left": "←",
    "right": "→",
    "tab": "Tab",
    "up": "↑",
    "down": "↓",
}


def _format_key(key: str) -> str:
    """Return a compact, reader-facing label for one configured key."""
    parts = key.split("+")
    labels = {
        "alt": "Alt",
        "ctrl": "Ctrl",
        "meta": "Meta",
        "shift": "Shift",
    }
    return "+".join(labels.get(part, _KEY_LABELS.get(part, part)) for part in parts)


class HelpModal(ModalScreen[None]):
    """Help overlay listing keybindings, mouse, and docs."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > #help-frame {
        width: 78;
        max-height: 32;
        padding: 1 0;
    }
    HelpModal #help-title {
        text-style: bold;
        padding: 0 2 1 2;
        text-align: center;
        width: 100%;
    }
    HelpModal #help-subtitle {
        padding: 0 2 1 2;
        text-align: center;
        width: 100%;
    }
    HelpModal VerticalScroll {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    HelpModal .help-section {
        text-style: bold;
        padding: 1 2 0 2;
    }
    HelpModal .help-row {
        padding: 0 2;
    }
    HelpModal .help-dim {
        padding: 0 2;
    }
    HelpModal #help-footer {
        padding: 1 2 0 2;
        text-align: center;
        width: 100%;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,question_mark,q,colon", "dismiss", "Close", show=True, priority=True),
    ]

    def __init__(self, *, keymap: KeymapStore | None = None) -> None:
        super().__init__()
        self._keymap = keymap or KeymapStore()

    def compose(self) -> ComposeResult:
        with Vertical(id="help-frame"):
            yield Static("aws-tui — help", id="help-title")
            yield Static("keyboard · mouse · themes · docs", id="help-subtitle")
            with VerticalScroll():
                yield Static("Navigation", classes="help-section")
                yield self._action_row(
                    ("pane.switch_focus", "pane.switch_focus_back"), "switch pane focus"
                )
                yield self._action_row(("pane.move_up", "pane.move_down"), "move cursor")
                yield self._action_row("pane.descend", "descend into directory")
                yield self._action_row("pane.ascend", "ascend to parent")
                yield self._action_row("pane.refresh", "refresh focused pane")

                yield Static("Mouse / Trackpad", classes="help-section")
                yield self._key_row("Click pane", "switch focus to it")
                yield self._key_row("Click row", "move cursor")
                yield self._key_row("Click again", "descend / ascend on '..'")
                yield self._key_row("Scroll wheel", "scroll pane content")

                yield Static("File operations", classes="help-section")
                yield self._action_row("pane.copy", "copy selected entry to the other pane")
                yield self._action_row("pane.delete", "delete selected entry")
                yield self._action_row(("pane.mark_up", "pane.mark_down"), "extend selection")

                yield Static("Connections", classes="help-section")
                yield self._action_row("app.swap_source", "cycle the focused pane source")

                yield Static("App", classes="help-section")
                yield self._action_row("app.open_settings", "open Settings")
                yield self._action_row("app.themes", "open the theme picker")
                yield self._action_row("app.cycle_theme", "cycle theme")
                yield self._action_row("app.help", "open this help overlay")
                yield self._action_row("app.command_palette", "open the command palette")
                yield self._action_row("app.quit", "quit")

                yield Static("Docs", classes="help-section")
                yield Static(
                    "  https://thekaveh.github.io/aws-tui/connections/\n"
                    "  https://thekaveh.github.io/aws-tui/theming/\n"
                    "  https://thekaveh.github.io/aws-tui/keybindings/\n"
                    "  https://thekaveh.github.io/aws-tui/cookbook/",
                    classes="help-dim",
                )
            help_keys = self._action_keys("app.help")
            yield Static(f"press {help_keys} / Esc to close", id="help-footer")

    def _action_row(self, action: str | tuple[str, ...], label: str) -> Static:
        actions = (action,) if isinstance(action, str) else action
        keys = "  /  ".join(self._action_keys(item) for item in actions)
        return self._key_row(keys, label)

    def _action_keys(self, action: str) -> str:
        return " / ".join(_format_key(key) for key in self._keymap.resolve(action))

    def _key_row(self, key: str, label: str) -> Static:
        return Static(
            f"  [b]{key:<18}[/]  [dim]{label}[/]",
            classes="help-row",
            markup=True,
        )


__all__ = ["HelpModal"]
