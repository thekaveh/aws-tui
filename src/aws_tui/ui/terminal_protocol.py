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

What refusing the protocol costs
--------------------------------

Not nothing. ``SMOOTH_SCROLL`` has exactly one *use site* in the whole of
Textual 8.2.8 — the ``mode_id == "2048"`` test quoted above — but the branch
it gates reaches one hop further, and two things beyond the resize report
itself are given up with it:

* ``App.supports_smooth_scrolling`` is assigned only from the resulting
  ``InBandWindowResize`` message (``app.py:5016-5019``), and its only reader
  is ``ScrollBar._on_mouse_move`` (``scrollbar.py:395``), which posts
  ``ScrollTo(..., animate=not self.app.supports_smooth_scrolling)``. With the
  protocol refused, a scrollbar **drag animates towards the pointer instead
  of tracking it**.

* ``LinuxDriver._enable_mouse_pixels`` (``linux_driver.py:481``) is reachable
  only from that same branch. It writes ``\\x1b[?1016h`` (``:145-150``), which
  is what turns SGR mouse reports into sub-cell coordinates
  (``_xterm_parser.py:93-103``). With the protocol refused, **mouse
  coordinates stay cell-granular**.

Neither loss is new to anyone on the most common macOS terminals: both are
exactly Textual's behaviour on iTerm2 (excluded at the branch by ``IS_ITERM``)
and on Terminal.app (which never advertises mode 2048), and on Windows, whose
driver has no in-band resize path at all. So the trade is a scrollbar drag
that animates and a mouse that reports whole cells, on Ghostty, WezTerm and
kitty, against a resize the app can never recover from. Resize correctness
wins, and :class:`CellMouseXTermParser` below makes the cell-granular half
consistent instead of leaving it to chance.

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
from textual._xterm_parser import XTermParser
from textual.message import Message

__all__ = [
    "SMOOTH_SCROLL_ENV",
    "CellMouseXTermParser",
    "prefer_sigwinch_resize",
]

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


class CellMouseXTermParser(XTermParser):
    """``XTermParser`` that never reports pixel mouse coordinates unasked.

    Upstream keeps the two halves of DEC mode 1016 apart. The half that
    *requests* sub-cell reporting, ``LinuxDriver._enable_mouse_pixels``
    (``linux_driver.py:474-482``), runs only from the in-band-resize branch
    :func:`prefer_sigwinch_resize` switches off. The half that *acts* on it,
    ``self.mouse_pixels = True`` at ``_xterm_parser.py:271-283``, sits in the
    handler for an in-band resize **report** and is gated on nothing at all —
    not on ``SMOOTH_SCROLL``, not on ``IS_ITERM``.

    So a terminal that already has mode 2048 set when aws-tui starts keeps
    sending those reports, the parser flips itself into pixel mode, and 1016
    was never negotiated: the coordinates are still cells, and
    ``parse_mouse_code`` divides them by the pixel/cell ratio anyway. A click
    at cell (100, 20) of a 200x40 cell terminal 1600x680 pixels across arrives
    as (12, 1) — every coordinate collapses towards the top-left corner after
    the first resize.
    A stale mode 2048 is not exotic: any Textual app killed before
    ``stop_application_mode`` leaves one behind, and with the flag cleared
    Textual will not clear it either, because
    ``_disable_in_band_window_resize`` (``linux_driver.py:168-170``) writes
    ``\\x1b[?2048l`` only when it believes it set the mode itself.

    Restoring the invariant — pixel coordinates if and only if 1016 was
    negotiated, and 1016 is negotiated if and only if ``SMOOTH_SCROLL`` is set
    — takes one line, because ``mouse_pixels`` is read in exactly one place in
    the whole package: the conversion in ``parse_mouse_code``. Clearing it
    there is therefore total, and it leaves the ``TEXTUAL_SMOOTH_SCROLL=1``
    opt-in path untouched.
    """

    def parse_mouse_code(self, code: str) -> Message | None:
        if not constants.SMOOTH_SCROLL:
            # An in-band report may have set this behind our back; 1016 was
            # never sent, so the terminal is still counting in cells.
            self.mouse_pixels = False
        return super().parse_mouse_code(code)
