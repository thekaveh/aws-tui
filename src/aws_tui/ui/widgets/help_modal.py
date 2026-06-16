"""Help + theme-picker modal.

Stand-in for the full command-palette deferred from M6. The modal lists
every wired keyboard binding, lets the user click a theme to switch at
runtime, and points at the deeper docs. Dismisses on Esc / ? / q.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static


class ThemePickEntry(Widget):
    """Single themed row in the theme-picker section.

    Bound to a theme name; on click, posts a ``ThemePicked`` message that
    the parent modal routes to the App for a runtime stylesheet swap.
    """

    DEFAULT_CSS = """
    ThemePickEntry {
        height: 1;
        padding: 0 2;
    }
    ThemePickEntry.-active {
        background: $boost;
        text-style: bold;
    }
    """

    class ThemePicked(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, name: str, *, active: bool) -> None:
        super().__init__(classes="theme-entry " + ("-active" if active else ""))
        self._theme_name = name
        self._active = active

    def render(self) -> str:
        mark = "●" if self._active else "○"
        return f"  {mark}  {self._theme_name}"

    def on_click(self, _event: object) -> None:
        self.post_message(self.ThemePicked(self._theme_name))


class HelpModal(ModalScreen[None]):
    """Help overlay listing keybindings, mouse, and a theme picker."""

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
    HelpModal .help-key {
        color: $accent;
        text-style: bold;
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

    def __init__(self, *, current_theme: str, themes: tuple[str, ...]) -> None:
        super().__init__()
        self._current_theme = current_theme
        self._themes = themes

    def compose(self) -> ComposeResult:
        with Vertical(id="help-frame"):
            yield Static("aws-tui — help", id="help-title")
            yield Static(
                "keyboard shortcuts · mouse · theme · docs",
                id="help-subtitle",
            )
            with VerticalScroll():
                # ── Keyboard ──────────────────────────────────────────────
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

                # ── Themes ────────────────────────────────────────────────
                yield Static("Themes (click to switch)", classes="help-section")
                for name in self._themes:
                    yield ThemePickEntry(name, active=name == self._current_theme)

                # ── Docs ──────────────────────────────────────────────────
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
        """Render one shortcut row with the key in accent and label in dim."""
        return Static(f"  [b cyan]{key:<18}[/]  [dim]{label}[/]", classes="help-row", markup=True)

    def on_theme_pick_entry_theme_picked(self, event: ThemePickEntry.ThemePicked) -> None:
        """Route a theme click up to the App for a stylesheet swap."""
        from aws_tui.app import AwsTuiApp  # local import to avoid cycle

        app = self.app
        if isinstance(app, AwsTuiApp):
            app.switch_theme(event.name)
        self.app.pop_screen()


__all__ = ["HelpModal", "ThemePickEntry"]
