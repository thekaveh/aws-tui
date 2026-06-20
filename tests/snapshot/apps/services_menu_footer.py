"""Test app for ServicesMenuFooter snapshots."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.services_menu_footer import ServicesMenuFooter


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


class ServicesMenuFooterApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)

    def compose(self) -> ComposeResult:
        yield Static("ServicesMenuFooter snapshot")
        yield ServicesMenuFooter()


__all__ = ["ServicesMenuFooterApp"]
