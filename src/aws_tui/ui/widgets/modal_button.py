"""ModalButton — a themable button replacement used by every modal that
needs theme-conformant footer action buttons.

Textual's stock ``textual.widgets.Button`` ships with its own ANSI color
defaults that override theme ``.tcss`` palette tokens. ``ModalButton`` is
a structural-only ``Static`` subclass — every color comes from the
active theme's ``.tcss`` (``.modal-footer > ModalButton {color: $text;
background: $bg; …}``). The widget owns no state; on_click bubbles up to
the parent modal which dispatches by ``button_id``.
"""

from __future__ import annotations

from textual.events import Click
from textual.widgets import Static


class ModalButton(Static):
    """Themable clickable button replacement.

    Two CSS classes carry visual state:
    - ``-primary``: the confirm side (accent color).
    - ``-danger``: the destructive variant (theme danger color).
    """

    DEFAULT_CSS = """
    ModalButton {
        height: 1;
        min-width: 14;
        padding: 0 2;
        content-align: center middle;
        text-style: bold;
        margin: 0 1;
    }
    ModalButton.-primary {
        text-style: bold;
    }
    """

    def __init__(self, label: str, *, button_id: str, classes: str = "") -> None:
        merged = " ".join(c for c in ("modal-button", classes) if c)
        super().__init__(label, classes=merged)
        self.button_id = button_id

    def on_click(self, _event: Click) -> None:
        # Bubble up — the modal's on_click reads ``button_id`` to act on
        # the press. Textual's event bubbling delivers the event to
        # parent widgets on its way up.
        pass


__all__ = ["ModalButton"]
