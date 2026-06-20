"""Placeholder panel rendered when a (soon) section is selected."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class _PlaceholderPanel(Widget):
    """Body widget rendered if a disabled sidebar section is ever
    programmatically selected.

    Unreachable in sub-project A (the disabled rows skip on keyboard
    nav and have no click handler), kept here so the SettingsModal
    can swap any section to a widget without conditionals.
    """

    DEFAULT_CSS = """
    _PlaceholderPanel {
        align: center middle;
    }
    _PlaceholderPanel > Static {
        text-style: italic;
        color: $text-muted;
    }
    """

    def __init__(self, *, section_id: str) -> None:
        super().__init__()
        self._section_id: str = section_id

    def compose(self) -> ComposeResult:
        yield Static(f"{self._section_id.title()} — coming in v0.8")


__all__ = ["_PlaceholderPanel"]
