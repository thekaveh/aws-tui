"""Bounded recovery from Textual's permanently stuck bracketed-paste parser.

Upstream defect, Textual 8.2.8, ``textual/_xterm_parser.py``
``XTermParser.parse``
-----------------------------------------------------------------

``bracketed_paste`` is a local of the ``parse`` generator. It is set to
``True`` on an exact ``\\x1b[200~`` and cleared **only** by an exact
``\\x1b[201~``. While it is ``True`` every character read is appended to
``paste_buffer`` and no key event is emitted (``_xterm_parser.py:218-228``).

The escape-sequence inner loop, however, abandons a partial sequence with no
regard for that flag and without returning the consumed bytes to
``paste_buffer``::

    while True:
        try:
            new_character = yield read1(constants.ESCAPE_DELAY)
        except ParseTimeout:
            send_sequence()
            break

So when the ``\\x1b`` of the closing ``\\x1b[201~`` is separated from its
``[201~`` by more than ``constants.ESCAPE_DELAY`` (0.1 s,
``textual/constants.py:155``) — an ordinary occurrence over a laggy ssh or
tmux link, because the split point is wherever the kernel happened to break
the read — the ESC is consumed alone, the remaining ``[201~`` is swallowed as
paste content, and ``bracketed_paste`` is stuck ``True`` forever. The screen
still repaints, but every subsequent byte lands in ``paste_buffer``: ``q`` and
``Ctrl+C`` are raw-mode bytes the parser eats, so nothing short of a SIGKILL
from another terminal ends the session. Injecting a clean ``\\x1b[201~``
un-wedges it instantly, which is what this module does.

Refusing to request bracketed-paste mode is **not** an acceptable mitigation
here: the Athena SQL editor and the EMR log-filter modal are ``TextArea``\\ s
and pasting a multi-line query is a real feature.

What this guard does
--------------------

``BracketedPasteGuard`` watches the byte stream and the token stream and
bounds how long the parser may stay in paste mode. It never inspects the
generator's locals (they are unreachable); it uses two facts that *are*
observable from outside:

* an exact ``\\x1b[200~`` in the fed bytes is the only thing that puts the
  parser into paste mode, so it is the only thing that arms the guard;
* an ``events.Paste`` token is emitted by, and only by, the flush at the top
  of the parse loop — it is therefore **proof** that the parser really left
  paste mode. A closing marker seen in the raw bytes proves nothing, because
  the wedge is precisely the case where that marker is present in the stream
  and the parser still did not act on it.

Two bounds end a paste that is not going to close. Both are evaluated in
``tick()``, which every Textual driver already calls at least every 0.1 s
(``linux_driver.py:453``, ``win32.py:247``):

1. **Idle bound** (``DEFAULT_IDLE_TIMEOUT``). No input *at all* has arrived
   for that long while a paste is open. This is where the bound belongs: a
   slow paste of a large file is legitimate and may take minutes, but it is
   never *silent* — the terminal keeps delivering bytes. A marker that never
   closes is silent as soon as the user stops typing. Bounding total paste
   duration instead would truncate exactly the legitimate case.

2. **Abandoned-sequence bound** (``DEFAULT_ABANDON_GRACE``). Two independent
   facts have to hold at once.

   *The closing marker is on the wire.* ``note_input`` scans the carry-joined
   window for ``\\x1b[201~`` as well as for the start marker. In the wedge it
   is always there — split across a read boundary that the five-character
   carry rejoins — and the parser simply failed to act on it. A paste that is
   merely still arriving has not delivered its close yet, so it can never
   satisfy this half.

   *A ``Key`` token emerged from a parser the guard already believed was
   pasting.* Reading ``parse`` line by line, a key emitted inside paste mode
   always comes from the inner loop giving up on an escape sequence —
   ``ParseTimeout`` (``:243``), ``ParseEOF`` (``:246``), ESC-follows-ESC
   (``:250``), or the 32-character threshold (``:256``) — and each of those
   consumes bytes without returning them to ``paste_buffer``. On its own that
   is *not* proof of the wedge, which is why the first fact is required:
   ``:250`` and ``:256`` sit outside ``parse``'s ``if not bracketed_paste:``
   guard and fire for a perfectly healthy paste whose *content* happens to
   contain an ESC (a colour-coded log excerpt). The ``paste_was_open``
   argument covers the other direction: a keystroke the terminal coalesced
   into the same read as ``\\x1b[200~`` is parsed from bytes that preceded the
   marker and says nothing about the parser's state inside the paste.

   This bound is what rescues the user who is *mashing* keys, whose
   keystrokes would otherwise keep resetting the idle bound forever. The idle
   bound stays the backstop for a close that genuinely never arrives.

A false positive costs very differently on each side of paste mode, and that
asymmetry is why both bounds are deliberately reluctant.

Injecting ``\\x1b[201~`` into a parser that is *not* in paste mode is a
complete no-op: the outer loop reads the ESC (not appended — ``bracketed_paste``
is ``False``), the inner loop matches ``BRACKETED_PASTE_END`` in
``SPECIAL_SEQUENCES``, sets an already-``False`` flag and breaks;
``paste_buffer`` is empty so nothing is flushed and no key event is produced.

Injecting it into a parser that *is* legitimately pasting is the opposite of
harmless: the buffer is flushed as a truncated ``Paste`` and every byte the
terminal has not delivered yet arrives as an individual key press, through the
binding system — the hazard ``DEFAULT_IDLE_TIMEOUT`` below is sized to avoid.
No bound may fire on evidence that a healthy paste can also produce.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from types import ModuleType
from typing import Final, cast

from textual import events
from textual._time import get_time
from textual._xterm_parser import (
    BRACKETED_PASTE_END,
    BRACKETED_PASTE_START,
    XTermParser,
)
from textual.driver import Driver
from textual.message import Message

__all__ = [
    "DEFAULT_ABANDON_GRACE",
    "DEFAULT_IDLE_TIMEOUT",
    "BracketedPasteGuard",
    "GuardedXTermParser",
    "guarded_driver_class",
    "install_guarded_parser",
]

_LOGGER: Final = logging.getLogger("aws_tui.ui.paste_guard")
# The recovery is logged from the driver's input THREAD while the alt
# screen is live. Without a handler of its own the record would fall through
# to ``logging.lastResort``, which writes to stderr and would paint over the
# TUI. ``LogSink(capture_stdlib=True)`` attaches the real handler on the
# parent ``aws_tui`` logger; this only removes the stderr fallback.
_LOGGER.addHandler(logging.NullHandler())

DEFAULT_IDLE_TIMEOUT: Final[float] = 3.0
"""Seconds of *total* input silence that end an unclosed paste.

Deliberately generous. Firing mid-paste would flush a partial ``Paste`` and
turn the remaining pasted bytes into keystrokes, and aws-tui binds destructive
keys, so the cost of a false positive is much higher than three seconds of
deafness. No terminal that is still delivering a paste goes silent this long.
"""

DEFAULT_ABANDON_GRACE: Final[float] = 0.5
"""Seconds allowed after the parser abandons an escape sequence mid-paste.

Counted only once the closing marker has also been seen in the byte stream,
so the grace is the window in which a well-formed close that is merely late
still wins; past it, the sequence the parser threw away is treated as the lost
closing marker.
"""

# The longest marker is six characters, so five characters of the previous
# chunk are enough to catch a marker split across a read boundary, and are too
# few to hold a complete marker that was already counted.
_CARRY_LENGTH: Final[int] = max(len(BRACKETED_PASTE_START), len(BRACKETED_PASTE_END)) - 1

_PARSER_SYMBOL: Final[str] = "XTermParser"
_DRIVER_PACKAGE: Final[str] = "textual.drivers."


class BracketedPasteGuard:
    """Tracks whether the parser is stuck in bracketed-paste mode.

    Pure state machine: every method takes the current time rather than
    reading a clock, so the bounds are testable without sleeping.
    """

    __slots__ = (
        "_abandoned_at",
        "_carry",
        "_end_seen",
        "_last_input",
        "_open",
        "abandon_grace",
        "idle_timeout",
        "recoveries",
    )

    def __init__(
        self,
        *,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        abandon_grace: float = DEFAULT_ABANDON_GRACE,
    ) -> None:
        self.idle_timeout = idle_timeout
        self.abandon_grace = abandon_grace
        self.recoveries = 0
        self._open = False
        self._carry = ""
        self._last_input = 0.0
        self._abandoned_at: float | None = None
        self._end_seen = False

    @property
    def paste_open(self) -> bool:
        """Does the guard believe the parser is inside a bracketed paste?"""
        return self._open

    def note_input(self, data: str, now: float) -> None:
        """Record a chunk of raw terminal input."""
        self._last_input = now
        # Prepend the carry so a marker split across two reads is still seen.
        # A marker that was complete in an earlier window cannot recur here:
        # the carry is one character shorter than the shortest marker.
        window = self._carry + data
        self._carry = window[-_CARRY_LENGTH:]
        if self._open and BRACKETED_PASTE_END in window:
            # The close really is on the wire. That is what separates the
            # wedge -- where the parser read these very bytes and failed to
            # act on them -- from a healthy paste that is merely still
            # arriving and whose close is simply not here yet.
            self._end_seen = True
        if BRACKETED_PASTE_START in window:
            # A start inside an already-open paste is a no-op upstream too
            # (the flag is simply set again), so re-arming is faithful. Any
            # evidence gathered so far belongs to the paste that just ended,
            # never to this one, so it goes with the rest of the state.
            self._open = True
            self._abandoned_at = None
            self._end_seen = False

    def note_token(self, token: Message, now: float, *, paste_was_open: bool = True) -> None:
        """Record a token the parser produced.

        ``paste_was_open`` says whether the guard already believed a paste was
        open *before* the read this token was parsed from. Terminals coalesce,
        so the read carrying ``\\x1b[200~`` can carry the keystroke that
        preceded it; such a ``Key`` comes from bytes parsed outside paste mode
        and is no evidence about the parser's state inside it.
        """
        if not self._open:
            return
        if isinstance(token, events.Paste):
            # The only flush site in ``parse`` — proof the parser left paste
            # mode. Note the corner this deliberately does not cover: a single
            # read holding a close *and* a later re-open would clear the flag
            # the re-open had just set, leaving the guard disarmed for the
            # second paste. That needs two pastes inside one 4 KiB read, and
            # its only cost is falling back to upstream behaviour.
            self._settle()
        elif paste_was_open and isinstance(token, events.Key) and self._abandoned_at is None:
            # The inner loop abandoned a partial escape sequence mid-paste.
            # Necessary for the wedge but not sufficient -- a healthy paste
            # carrying an ESC does this too -- so ``due`` also demands the
            # closing marker.
            self._abandoned_at = now

    def due(self, now: float) -> bool:
        """Should the parser be forced out of paste mode?"""
        if not self._open:
            return False
        if now - self._last_input >= self.idle_timeout:
            return True
        if self._abandoned_at is None or not self._end_seen:
            return False
        return now - self._abandoned_at >= self.abandon_grace

    def note_recovery(self, now: float) -> None:
        """Record that a synthetic closing marker was injected."""
        self.recoveries += 1
        self._last_input = now
        self._settle()

    def _settle(self) -> None:
        self._open = False
        self._abandoned_at = None
        self._end_seen = False


class GuardedXTermParser(XTermParser):
    """``XTermParser`` that cannot stay deaf inside an unclosed paste.

    Overrides only ``feed`` and ``tick``; the 190-line ``parse`` generator is
    untouched, so the guard adds no second implementation of the protocol to
    keep in step with upstream.
    """

    def __init__(
        self,
        debug: bool = False,
        *,
        guard: BracketedPasteGuard | None = None,
        clock: Callable[[], float] = get_time,
    ) -> None:
        # Set before ``super().__init__`` -- the base constructor primes the
        # parse generator, and a subclass must not be half-built when it runs.
        self.guard = guard if guard is not None else BracketedPasteGuard()
        self._clock = clock
        super().__init__(debug)

    def feed(self, data: str) -> Iterable[Message]:
        now = self._clock()
        # Read before ``note_input``: this chunk may hold the start marker
        # itself, and anything parsed from the bytes ahead of that marker was
        # parsed outside paste mode.
        was_open = self.guard.paste_open
        self.guard.note_input(data, now)
        for token in super().feed(data):
            self.guard.note_token(token, now, paste_was_open=was_open)
            yield token

    def tick(self) -> Iterable[Message]:
        for token in super().tick():
            # No bytes arrive in ``tick``, so a paste the guard sees open here
            # was already open when the token's bytes were read.
            self.guard.note_token(token, self._clock())
            yield token
        if self.is_eof:
            return
        now = self._clock()
        if not self.guard.due(now):
            return
        self.guard.note_recovery(now)
        # By construction the parser is parked on the outer, untimed ``read1``
        # here: any pending escape-sequence timeout is shorter than either
        # bound and ``super().tick()`` above has already thrown it. So the
        # injected marker is read as a marker and not as sequence filler.
        _LOGGER.warning(
            "ui.paste_guard.recovered",
            extra={"recoveries": self.guard.recoveries},
        )
        yield from self.feed(BRACKETED_PASTE_END)


def _patchable_driver_modules() -> list[ModuleType]:
    """Imported Textual driver modules that build an ``XTermParser``.

    Every 8.2.8 driver constructs the parser from a module-level name
    (``linux_driver.py:426``, ``linux_inline_driver.py:129``,
    ``web_driver.py:187``, ``win32.py:230``), so rebinding that one name is
    enough and no input loop has to be forked. If a future Textual stops
    doing that, nothing matches and the app simply runs unguarded rather than
    crashing; ``tests/unit/ui/test_paste_guard.py`` fails loudly instead.
    """
    return [
        module
        for name, module in list(sys.modules.items())
        if name.startswith(_DRIVER_PACKAGE) and getattr(module, _PARSER_SYMBOL, None) is XTermParser
    ]


@contextmanager
def install_guarded_parser(
    factory: type[XTermParser] = GuardedXTermParser,
) -> Iterator[list[ModuleType]]:
    """Make Textual's drivers build ``factory`` instead of ``XTermParser``."""
    patched = _patchable_driver_modules()
    for module in patched:
        setattr(module, _PARSER_SYMBOL, factory)
    try:
        yield patched
    finally:
        for module in patched:
            setattr(module, _PARSER_SYMBOL, XTermParser)


class _InputThreadGuard:
    """Scopes the parser swap to a driver that owns ``run_input_thread``.

    Covers ``LinuxDriver`` (macOS and Linux), ``LinuxInlineDriver`` and
    ``WebDriver``: each builds its parser inside that method.
    """

    def run_input_thread(self) -> None:
        with install_guarded_parser():
            super().run_input_thread()  # type: ignore[misc]


class _ApplicationModeGuard:
    """Scopes the parser swap to a driver that builds its parser elsewhere.

    ``WindowsDriver`` starts ``win32.EventMonitor`` from
    ``start_application_mode`` and that thread builds the parser itself, so
    the swap has to span application mode. ``stop_application_mode`` joins the
    monitor through ``disable_input`` before this restores the symbol.
    """

    _paste_guard_patch: AbstractContextManager[list[ModuleType]] | None = None

    def start_application_mode(self) -> None:
        patch = install_guarded_parser()
        patch.__enter__()
        self._paste_guard_patch = patch
        super().start_application_mode()  # type: ignore[misc]

    def stop_application_mode(self) -> None:
        try:
            super().stop_application_mode()  # type: ignore[misc]
        finally:
            patch = self._paste_guard_patch
            if patch is not None:
                self._paste_guard_patch = None
                patch.__exit__(None, None, None)


# Memoized so repeated ``get_driver_class`` calls hand back one class:
# ``App`` stores the result and tests compare identity. Bounded by the number
# of distinct driver classes a process ever selects, which is one or two.
_GUARDED_CLASSES: dict[type[Driver], type[Driver]] = {}


def guarded_driver_class(base: type[Driver]) -> type[Driver]:
    """Return ``base`` with the bracketed-paste guard installed.

    Reachable from ``App.get_driver_class`` and the ``driver_class``
    constructor argument, so the guard rides on whatever driver the platform
    or ``TEXTUAL_DRIVER`` actually selected instead of assuming one.
    """
    cached = _GUARDED_CLASSES.get(base)
    if cached is not None:
        return cached
    mixin: type = _InputThreadGuard if hasattr(base, "run_input_thread") else _ApplicationModeGuard
    guarded = cast(
        "type[Driver]",
        type(f"PasteGuarded{base.__name__}", (mixin, base), {}),
    )
    _GUARDED_CLASSES[base] = guarded
    return guarded
