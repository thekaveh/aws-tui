"""Top-level Textual application — M0 hello-world.

This is a placeholder for the v0.1.0 app described in
``docs/superpowers/specs/2026-06-13-aws-tui-design.md``. Subsequent
milestones replace ``compose`` with the real ``AppScreen`` containing
the services menu, dual-pane file manager, hint legend, and status bar.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Static

from aws_tui.version import __version__


class AwsTuiApp(App[None]):
    """The aws-tui Textual application (M0 stage)."""

    TITLE = "aws-tui"
    SUB_TITLE = f"v{__version__}"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(
            f"aws-tui v{__version__}\n\nservice menu placeholder\n\npress q to quit",
            id="placeholder",
        )


def main() -> None:
    """Run the Textual app. Invoked by ``aws-tui`` console script and ``python -m aws_tui``."""
    AwsTuiApp().run()


if __name__ == "__main__":
    main()
