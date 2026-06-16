"""Help modal — shows the wired keybindings and a pointer to the docs.

Stand-in for the full command-palette / help-overlay system deferred from
M6 (see memory ``deferred-from-m6``). Pushable from anywhere via
``App.push_screen(HelpModal())``; dismisses on ``Esc``, ``?``, or ``q``.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class HelpModal(ModalScreen[None]):
    """Modal overlay listing the wired keybindings + how to change themes."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > Vertical {
        width: 70;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    HelpModal .help-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    HelpModal .help-section {
        margin-top: 1;
        text-style: bold;
    }
    HelpModal .help-dim {
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,question_mark,q", "dismiss", "Close", show=True, priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("aws-tui — help", classes="help-title")
            yield Static("Navigation", classes="help-section")
            yield Static(
                "  Tab / Shift+Tab     switch pane focus\n"
                "  ↑ / k   ↓ / j       move cursor\n"
                "  Enter               descend into directory (or ascend if on '..')\n"
                "  Backspace / ←       parent directory\n"
                "  r                   refresh focused pane",
            )
            yield Static("Mouse", classes="help-section")
            yield Static("  Click a pane     switch focus to that pane")
            yield Static("App", classes="help-section")
            yield Static(
                "  ?                   this help overlay\n  q / Ctrl+C          quit",
            )
            yield Static("Theme + config", classes="help-section")
            yield Static(
                "Themes: carbon (default), voidline, lattice, amber. To switch,\n"
                "edit ~/.config/aws-tui/config.toml:\n"
                "\n"
                "    [defaults]\n"
                '    theme = "voidline"\n'
                "\n"
                "Connections, keybindings, and full docs:\n"
                "  docs/connections.md  ·  docs/theming.md  ·  docs/keybindings.md",
                classes="help-dim",
            )
            yield Static("Press ? or Esc to close.", classes="help-dim")

    def action_dismiss(self, _result: object = None) -> None:  # type: ignore[override]
        self.app.pop_screen()


__all__ = ["HelpModal"]
