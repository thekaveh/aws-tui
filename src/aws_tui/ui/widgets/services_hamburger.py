"""ServicesHamburger — fixed top-left ``[≡]`` button that toggles the
services rail open/closed.

Lives on the ``notifications`` layer with ``dock: top`` so it floats
above the brand banner without taking any extra flow space at the App
level — the same trick the toast stack and transfers overlay use.

Clicking the glyph fires :meth:`AwsTuiApp.action_toggle_services`. We
intentionally avoid reaching into the ``ServicesMenu`` widget directly
so the rail can be ``display: none`` when collapsed (truly zero
column) and still reachable.
"""

from __future__ import annotations

from textual.events import Click
from textual.widgets import Static


class ServicesHamburger(Static):
    """Persistent floating hamburger that opens/closes the services rail."""

    DEFAULT_CSS = """
    ServicesHamburger {
        layer: notifications;
        dock: top;
        offset: 1 1;
        width: 3;
        height: 1;
        background: transparent;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("≡", id=id, classes="services-hamburger")

    def on_click(self, event: Click) -> None:
        # Re-export the click as an app action so AwsTuiApp owns the
        # actual collapsed/expanded state — the widget is a pure
        # affordance.
        app = getattr(self, "app", None)
        if app is None:
            return
        action = getattr(app, "action_toggle_services", None)
        if callable(action):
            action()
            stop = getattr(event, "stop", None)
            if callable(stop):
                stop()


__all__ = ["ServicesHamburger"]
