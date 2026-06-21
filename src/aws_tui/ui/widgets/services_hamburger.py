"""ServicesHamburger — top-left ``[≡]`` button that toggles the
nav rail open/closed.

Mounted as the first child of ``#main-area`` (next to the file panes,
*below* the brand banner) so its visual position reads as "the
control for the column to my right" rather than floating over the
brand banner. A fixed-width ``3`` column with no border so the panes
reclaim the rest of the row.

Clicking fires :meth:`AwsTuiApp.action_toggle_services`. We
intentionally avoid reaching into ``NavMenu`` directly so the rail
can be ``display: none`` when collapsed (truly zero column) and still
reachable. (The widget keeps the ``ServicesHamburger`` and
``action_toggle_services`` names for backwards-compat with the
keybinding config; the rail itself was renamed to ``NavMenu`` when
Settings became a peer of S3 — see
``docs/superpowers/specs/2026-06-20-settings-as-first-class-nav-page-design.md``.)
"""

from __future__ import annotations

from textual.events import Click
from textual.widgets import Static


class ServicesHamburger(Static):
    """Persistent hamburger button that opens/closes the services rail."""

    DEFAULT_CSS = """
    ServicesHamburger {
        width: 3;
        height: 1;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("≡", id=id, classes="services-hamburger")

    def on_click(self, event: Click) -> None:
        # Re-export the click as an app action so AwsTuiApp owns the
        # actual collapsed/expanded state — the widget is a pure
        # affordance. ``event.stop()`` is critical: without it, the
        # click also bubbles to ``NavMenu``'s own ``on_click`` and the
        # user gets an immediate re-toggle ("expands then collapses").
        stop = getattr(event, "stop", None)
        if callable(stop):
            stop()
        app = getattr(self, "app", None)
        if app is None:
            return
        action = getattr(app, "action_toggle_services", None)
        if callable(action):
            action()


__all__ = ["ServicesHamburger"]
