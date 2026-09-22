"""Guards for deferred ``App.set_focus`` calls.

A service page never focuses a slot target the moment it decides to: the
projection is queued with ``call_after_refresh`` and runs a refresh cycle
later, on the page's own message pump. Whatever the callback assumed about
the world may no longer hold by the time it lands. Two of those stale
assumptions have bitten us, and each has a guard here:
:func:`is_on_active_screen` for "the page is still the visible screen", and
:func:`focus_rests_within` for "the slot does not already hold the focus".

Screen identity
---------------

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

Focus already inside the slot
-----------------------------

A focus slot's target is sometimes a *container* — a ``ContextPicker`` or a
``ServiceSourceHeader`` — whose open overlay is the widget that actually
holds the focus. Re-projecting such a slot is not the no-op it looks like:
``App.set_focus`` short-circuits only when the target *is* the focused
widget, so focusing the container while its overlay is focused moves focus
*up*, out of the overlay. ``OverlayOptionList.on_blur`` reads that as the
user clicking away and posts ``Dismissed(lost_focus=True)``, which closes
the picker.

That is how #235 manifested. Opening a named Glue filter runs the view's VM
load inline, and every property that load notifies schedules another
``GLUE_FILTER`` projection on the page's pump; the picker's own
``_focus_options`` is scheduled on the *picker's* pump. The two pumps are
separate asyncio tasks with no ordering guarantee between them, so on a
loaded runner a projection queued *before* the picker opened could land
*after* it and close the filter the user had just asked for — which is the
intermittent windows-latest failure of
``test_named_filter_action_closes_hidden_filter_from_previous_view``.

So: gate the ``set_focus`` of a slot projection on
:func:`focus_rests_within`. Recording the slot on the coordinator stays
*outside* the gate — the slot really is current, it is merely already
satisfied.
"""

from __future__ import annotations

from textual.app import ModeError, ScreenError
from textual.dom import NoScreen
from textual.widget import Widget

__all__ = ["focus_rests_within", "is_on_active_screen"]


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


def focus_rests_within(target: Widget, focused: Widget | None) -> bool:
    """Does ``focused`` already sit on ``target`` or inside it?

    When it does, a slot projection onto ``target`` has nothing to achieve
    and must be skipped: the slot is already satisfied, and re-focusing the
    container would blur whichever descendant currently owns the focus. For
    an open ``ContextPicker`` that blur is a dismissal, so the projection
    would close the picker — see the module docstring and #235.

    ``focused is target`` is included for symmetry only; ``App.set_focus``
    already returns early in that case, so folding it in here changes
    nothing beyond saving the call.
    """
    return focused is not None and target in focused.ancestors_with_self
