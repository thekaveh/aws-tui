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

    def compose(self) -> ComposeResult:
        with Vertical(id="help-frame"):
            yield Static("aws-tui — help", id="help-title")
            yield Static("keyboard · mouse · themes · docs", id="help-subtitle")
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

                yield Static("Mouse / Trackpad", classes="help-section")
                yield self._key_row("Click pane", "switch focus to it")
                yield self._key_row("Click row", "move cursor")
                yield self._key_row("Click again", "descend / ascend on '..'")
                yield self._key_row("Scroll wheel", "scroll pane content")

                yield Static("File operations", classes="help-section")
                yield self._key_row("c", "copy selected entry to the other pane")
                yield self._key_row("d", "delete selected entry")
                yield self._key_row("Shift+↑ / ↓", "extend selection")

                yield Static("Connections", classes="help-section")
                yield self._key_row("Shift+S", "cycle the focused pane source")

                yield Static("App", classes="help-section")
                yield self._key_row(",", "open Settings")
                yield self._key_row("t", "open the theme picker (keyboard-navigable)")
                yield self._key_row("T", "cycle theme")
                yield self._key_row("?  or  :", "this help overlay")
                yield self._key_row("q / Ctrl+C", "quit")

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
            f"  [b]{key:<18}[/]  [dim]{label}[/]",
            classes="help-row",
            markup=True,
        )


__all__ = ["HelpModal"]
