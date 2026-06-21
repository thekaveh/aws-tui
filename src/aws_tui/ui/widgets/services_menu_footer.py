"""ServicesMenuFooter — bottom-pinned gear button band."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button


class _GearButton(Button):
    """The ⚙ Settings button inside the footer band."""


class ServicesMenuFooter(Widget):
    """Bottom-of-rail band exposing the App Settings entry point.

    Single button labeled ``⚙  Settings``. On click, calls
    ``app.action_open_settings()`` if the action is wired (otherwise
    no-op — the action lands in Task 9).
    """

    DEFAULT_CSS = """
    ServicesMenuFooter {
        height: 2;
        width: 1fr;
        dock: bottom;
    }
    ServicesMenuFooter > Horizontal {
        height: 1;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield _GearButton("⚙  Settings", id="gear-button")

    @on(Button.Pressed, "#gear-button")
    def _on_gear(self, event: Button.Pressed) -> None:
        event.stop()
        action = getattr(self.app, "action_open_settings", None)
        if callable(action):
            action()


__all__ = ["ServicesMenuFooter"]
