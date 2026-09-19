"""Keep the ``SIGWINCH`` resize fallback available under Textual 8.2.8.

The defect, Textual 8.2.8
-------------------------

``textual/drivers/linux_driver.py:246-248`` installs this ``SIGWINCH``
handler::

    def on_terminal_resize(signum, stack) -> None:
        if not self._in_band_window_resize:
            send_size_event()

Once the in-band window-resize protocol (DEC private mode 2048) is
negotiated, ``_in_band_window_resize`` flips to ``True``
(``linux_driver.py:474``) and the kernel's resize signal becomes a no-op
for the rest of the session. The terminal's own in-band report is then the
*only* thing that can tell the app it changed size.

Negotiation is gated on ``SMOOTH_SCROLL``
(``textual/_xterm_parser.py:319-323``)::

    elif (
        mode_id == "2048"
        and constants.SMOOTH_SCROLL
        and not IS_ITERM
    ):

``SMOOTH_SCROLL`` defaults to on (``textual/constants.py:168``), so every
terminal that advertises 2048 and is not iTerm2 — Ghostty, WezTerm and
kitty among them — runs aws-tui with no resize fallback. If such a terminal
advertises the mode but drops a report (a macOS Space switch resizing a
window on an inactive desktop is the reported case), the app paints the old
geometry indefinitely. Keys still work; the frame is simply stale, and
nothing the user can do from inside the app repairs it.

Why turning ``SMOOTH_SCROLL`` off costs nothing
-----------------------------------------------

Despite the name, ``SMOOTH_SCROLL`` gates exactly one branch in the whole
of Textual 8.2.8 — the ``mode_id == "2048"`` test quoted above. A
``grep -rn SMOOTH_SCROLL`` over the installed package returns its
definition, its docstring, and that one use. It drives no scroll
rendering. Clearing it therefore buys the ``SIGWINCH`` fallback back and
gives up nothing but the in-band protocol itself, which for a Textual app
is a redundant second path to the same ``events.Resize``.

The change is a complete no-op on iTerm2 and Terminal.app, where
``IS_ITERM`` is true or mode 2048 is never advertised, and on Windows,
whose driver has no in-band resize path at all.

Why the environment variable alone is not enough
------------------------------------------------

``textual.constants`` reads the environment once, at import time, into a
module-level ``Final``. ``aws_tui.app`` imports Textual at module scope, so
by the time ``main()`` runs the value is already fixed and setting
``TEXTUAL_SMOOTH_SCROLL`` has no effect. The parser reads it back as a
module *attribute* (``constants.SMOOTH_SCROLL``, not a from-import), so
rebinding that attribute does work, and is what actually takes effect. The
environment variable is set alongside it so the two never disagree and so
any subprocess or late import sees the same answer.
"""

from __future__ import annotations

import os
from typing import Final

from textual import constants

__all__ = ["SMOOTH_SCROLL_ENV", "prefer_sigwinch_resize"]

SMOOTH_SCROLL_ENV: Final = "TEXTUAL_SMOOTH_SCROLL"
"""Textual's own knob. Set it to ``1`` to get upstream behaviour back."""


def prefer_sigwinch_resize() -> bool:
    """Default the in-band window-resize protocol off, keeping ``SIGWINCH``.

    Call this before :meth:`textual.app.App.run`; the driver and its parser
    are not built until then.

    An explicit ``TEXTUAL_SMOOTH_SCROLL`` always wins, in either direction:
    ``1`` restores Textual's default and the in-band protocol with it, ``0``
    agrees with what this function would have done anyway. A variable that
    is unset, empty, or blank counts as "the user said nothing" — an empty
    export expresses no preference, and Textual itself falls back to the
    default for one.

    Returns:
        ``True`` when this call changed the default, ``False`` when an
        explicit setting was left alone.
    """
    if os.environ.get(SMOOTH_SCROLL_ENV, "").strip():
        return False
    os.environ[SMOOTH_SCROLL_ENV] = "0"
    # Rebinding the module attribute is the half that takes effect; see the
    # module docstring. `Final` documents intent for Textual's own readers,
    # not an immutability guarantee, and this is the value Textual would
    # itself have computed had the variable been exported before launch.
    constants.SMOOTH_SCROLL = False  # type: ignore[misc]
    return True
