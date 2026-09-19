"""Screen-identity guard for deferred ``App.set_focus`` calls.

``App.set_focus`` delegates straight to ``App.screen`` — the TOP of the
screen stack — and never checks that the widget it was handed belongs to
that screen::

    def set_focus(self, widget: Widget | None, scroll_visible: bool = True) -> None:
        self.screen.set_focus(widget, scroll_visible)

(Textual 8.2.8, ``textual/app.py:3150-3157``.) Textual's own
``Widget.focus()`` does the safe thing instead: it routes through
``widget.screen`` — the widget's OWN screen —
(``textual/widget.py:4589-4597``), and ``DOMNode.screen`` even documents
the difference in a comment: *"Note that self.screen may not be the same
as self.app.screen"* (``textual/dom.py:794``).

That asymmetry is a hard wedge for us. Every service page projects focus
through a ``call_after_refresh`` callback. When a modal screen is pushed
between the schedule and the callback, the callback parks a *base-screen*
widget in the *modal* screen's ``focused``. Key events then bubble the
base-screen widget's ancestry instead of the modal's, so the modal's own
``escape`` binding never fires and the modal cannot be dismissed — and
because ``AwsTuiApp.action_dispatch`` gates almost every action while a
screen sits on the stack, an app in that state is close to unusable.

So: guard every deferred ``self.app.set_focus(...)`` with
:func:`is_on_active_screen`. Plain ``Widget.focus()`` needs no guard.
"""

from __future__ import annotations

from textual.app import ModeError, ScreenError
from textual.dom import NoScreen
from textual.widget import Widget

__all__ = ["is_on_active_screen"]


def is_on_active_screen(widget: Widget) -> bool:
    """Is ``widget``'s own screen still the app's active (top) screen?

    Returns ``False`` — never raises — when the widget is detached, has no
    screen, or the app has no usable screen stack. A deferred focus
    projection that lands in any of those states must simply be abandoned;
    there is nothing sensible to focus and the user's real focus (a modal's,
    typically) must not be disturbed.
    """
    # ``is_attached`` swallows ``NoActiveAppError`` itself and returns False,
    # so it doubles as the guard that makes ``widget.app`` below safe without
    # importing from ``textual._context``.
    if not widget.is_attached:
        return False
    try:
        own_screen = widget.screen
    except NoScreen:
        return False
    try:
        return own_screen is widget.app.screen
    except (ScreenError, ModeError):
        return False
