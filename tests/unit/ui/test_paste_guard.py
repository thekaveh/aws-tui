"""The bracketed-paste wedge, and the guard that ends it.

``test_stock_parser_goes_permanently_deaf_after_a_split_closing_marker`` is
the control: it exercises the upstream Textual 8.2.8 defect directly so the
guard below is measured against a reproduced failure and not against an
assumption. If a future Textual fixes the parser, that test fails and the
guard can be retired.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable

import pytest
from textual import constants, events
from textual._xterm_parser import (
    BRACKETED_PASTE_END,
    BRACKETED_PASTE_START,
    XTermParser,
)
from textual.driver import Driver
from textual.drivers.headless_driver import HeadlessDriver
from textual.message import Message

from aws_tui.ui.paste_guard import (
    BracketedPasteGuard,
    GuardedXTermParser,
    guarded_driver_class,
    install_guarded_parser,
)

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="textual.drivers.linux_driver imports the POSIX-only `termios`, so it cannot even be imported on Windows. The guard itself is platform-neutral and stays covered there by the tests above.",
)

if sys.platform != "win32":
    from textual.drivers import linux_driver
    from textual.drivers.linux_driver import LinuxDriver

# The guard wraps whatever driver the app resolves, so the HeadlessDriver case
# is the one that matters on every platform; LinuxDriver is added only where it
# can be imported.
_GUARD_BASES: list[type[Driver]] = [HeadlessDriver]
if sys.platform != "win32":
    _GUARD_BASES.insert(0, LinuxDriver)

# Larger than ``constants.ESCAPE_DELAY`` so the parser's inner loop really
# does raise ``ParseTimeout`` on the lone ESC, which is the whole defect.
_SPLIT_GAP_SECONDS = constants.ESCAPE_DELAY + 0.05

# Small enough to keep the wall-clock tests short, large enough that neither
# bound can be reached by the scheduler alone.
_TEST_IDLE_TIMEOUT = 0.4
_TEST_ABANDON_GRACE = 0.05

_RECOVERY_BUDGET_SECONDS = 3.0


class _FakeClock:
    """A clock the test advances by hand.

    The guard takes the current time as an argument everywhere, so a fake
    clock pins exactly which bound a test is exercising instead of leaving it
    to the scheduler. ``XTermParser``'s own ``ESCAPE_DELAY`` still runs on the
    real clock -- that is upstream's business and these tests never depend on
    which of its two reissue paths fires.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _keys(tokens: Iterable[Message]) -> list[str]:
    return [token.key for token in tokens if isinstance(token, events.Key)]


def _pastes(tokens: Iterable[Message]) -> list[str]:
    return [token.text for token in tokens if isinstance(token, events.Paste)]


def _wedge(parser: XTermParser) -> list[Message]:
    """Drive ``parser`` into the stuck state and return what it emitted.

    Exactly the field shape: an open marker, some pasted text, then the
    closing marker split so its ESC arrives more than ``ESCAPE_DELAY``
    before its ``[201~``.
    """
    tokens: list[Message] = []
    tokens += list(parser.feed(f"{BRACKETED_PASTE_START}SELECT 1"))
    tokens += list(parser.feed("\x1b"))
    time.sleep(_SPLIT_GAP_SECONDS)
    # The driver's select loop calls ``tick`` at least every 0.1 s; this is
    # where ``ParseTimeout`` is thrown into the parse generator.
    tokens += list(parser.tick())
    tokens += list(parser.feed("[201~"))
    return tokens


def _guarded_parser(clock: _FakeClock | None = None) -> GuardedXTermParser:
    guard = BracketedPasteGuard(
        idle_timeout=_TEST_IDLE_TIMEOUT,
        abandon_grace=_TEST_ABANDON_GRACE,
    )
    if clock is None:
        return GuardedXTermParser(guard=guard)
    return GuardedXTermParser(guard=guard, clock=clock)


def _drain_until_recovered(parser: GuardedXTermParser) -> list[Message]:
    tokens: list[Message] = []
    deadline = time.monotonic() + _RECOVERY_BUDGET_SECONDS
    while time.monotonic() < deadline:
        tokens += list(parser.tick())
        if parser.guard.recoveries:
            return tokens
        time.sleep(0.02)
    raise AssertionError("the guard never recovered the parser")


def test_stock_parser_goes_permanently_deaf_after_a_split_closing_marker() -> None:
    parser = XTermParser()
    _wedge(parser)

    # The user now presses keys. Every byte lands in ``paste_buffer`` because
    # ``bracketed_paste`` is still True and can no longer be cleared.
    after: list[Message] = []
    for _ in range(20):
        after += list(parser.feed("q"))
        after += list(parser.tick())

    assert after == [], "upstream parser recovered; the guard's premise is gone"


def test_guarded_parser_answers_a_key_after_a_split_closing_marker() -> None:
    parser = _guarded_parser()
    deaf = _wedge(parser)
    # Control inside the same test: the wedge is real before the guard acts.
    assert _keys(parser.feed("q")) == []
    assert _pastes(deaf) == []

    recovered = _drain_until_recovered(parser)

    # The buffer is flushed as a Paste rather than discarded, and the paste
    # carries the stranded closing bytes -- see the note in ``concerns``.
    assert _pastes(recovered) == ["SELECT 1\x1b[201~q"]
    assert _keys(parser.feed("q")) == ["q"]
    assert parser.guard.recoveries == 1


def test_recovery_fires_while_the_user_is_mashing_keys() -> None:
    """The idle bound alone would never fire for a user hammering the keyboard.

    The abandoned-sequence bound is what covers this, and it is the likely
    real-world shape: the app stops answering and the user mashes ``q`` and
    ``Ctrl+C``.
    """
    parser = _guarded_parser()
    _wedge(parser)

    tokens: list[Message] = []
    deadline = time.monotonic() + _RECOVERY_BUDGET_SECONDS
    while time.monotonic() < deadline and not parser.guard.recoveries:
        # Keystrokes keep resetting the idle clock; they must not keep the
        # parser deaf.
        tokens += list(parser.feed("q"))
        tokens += list(parser.tick())
        time.sleep(0.01)

    assert parser.guard.recoveries == 1
    assert _keys(parser.feed("q")) == ["q"]


def test_a_paste_that_is_still_arriving_is_not_truncated() -> None:
    """The bound is on silence, not on how long a paste takes.

    A slow paste of a big file is legitimate; it just has to keep delivering
    bytes. This feeds for well over both bounds without a gap and asserts the
    guard never cut it short.
    """
    parser = _guarded_parser()
    list(parser.feed(BRACKETED_PASTE_START))

    tokens: list[Message] = []
    deadline = time.monotonic() + (_TEST_IDLE_TIMEOUT * 3)
    chunks = 0
    while time.monotonic() < deadline:
        tokens += list(parser.feed("line\n"))
        tokens += list(parser.tick())
        chunks += 1
        time.sleep(0.01)

    assert parser.guard.recoveries == 0
    assert _pastes(tokens) == []
    tokens += list(parser.feed(BRACKETED_PASTE_END))
    tokens += list(parser.feed("q"))
    assert _pastes(tokens) == ["line\n" * chunks]
    assert _keys(tokens) == ["q"]


def test_a_stray_escape_inside_a_healthy_paste_does_not_truncate_it() -> None:
    """An ESC in the pasted *content* is not the wedge signature.

    ``parse`` reissues an abandoned sequence as key events from two sites that
    sit outside its ``if not bracketed_paste:`` guard -- ESC-follows-ESC
    (``_xterm_parser.py:250``) and the 32-character threshold (``:256``) --
    so a healthy paste whose content contains an ESC emits ``Key`` tokens
    mid-paste with no wedge anywhere. That is an ordinary thing to paste into
    the EMR log-filter ``TextArea``: a colour-coded log excerpt.

    Upstream mangles the stray sequence and the characters immediately after
    it, then delivers the remainder as one ``Paste``. The guard must not turn
    that remainder into keystrokes on top of it.
    """
    clock = _FakeClock()
    parser = _guarded_parser(clock)
    tokens = list(parser.feed(BRACKETED_PASTE_START))

    for chunk in ["\x1bZ", *(f"select {index} from t;\n" for index in range(8))]:
        tokens += list(parser.feed(chunk))
        # Far past ``abandon_grace`` between chunks, and never idle: exactly
        # the window in which the bound used to fire.
        clock.advance(_TEST_ABANDON_GRACE * 4)
        tokens += list(parser.tick())
    tokens += list(parser.feed(BRACKETED_PASTE_END))
    tokens += list(parser.feed("x"))

    assert parser.guard.recoveries == 0
    pasted = _pastes(tokens)
    assert len(pasted) == 1
    # The assertion is about the remainder, which is what was being cut; how
    # much upstream eats at the front depends on which reissue path fired.
    assert pasted[0].endswith("select 7 from t;\n")
    assert "select 3 from t;\n" in pasted[0]
    assert _keys(tokens)[-1] == "x"


def test_a_keystroke_in_the_read_that_opened_the_paste_is_not_an_abandoned_sequence() -> None:
    """Terminals coalesce: one read can hold a keystroke *and* ``\\x1b[200~``.

    That ``Key`` is parsed from bytes that preceded the marker, so it says
    nothing about the parser's state inside the paste that the same read
    opened.
    """
    clock = _FakeClock()
    parser = _guarded_parser(clock)
    tokens = list(parser.feed(f"j{BRACKETED_PASTE_START}SELECT 1\n"))
    assert _keys(tokens) == ["j"], "the coalesced keystroke is still delivered"

    for index in range(8):
        clock.advance(_TEST_ABANDON_GRACE * 4)
        tokens += list(parser.tick())
        tokens += list(parser.feed(f"  more line {index}\n"))
    tokens += list(parser.feed(BRACKETED_PASTE_END))
    tokens += list(parser.feed("x"))

    assert parser.guard.recoveries == 0
    body = "".join(f"  more line {index}\n" for index in range(8))
    assert _pastes(tokens) == [f"SELECT 1\n{body}"]
    assert _keys(tokens) == ["j", "x"]


def test_guard_ignores_a_start_marker_split_across_reads_harmlessly() -> None:
    """A start marker the parser missed arms the guard, and that is safe.

    The scanner sees the six bytes across the read boundary; the parser does
    not, because its inner loop timed the sequence out. The guard therefore
    fires into a parser that is not pasting -- which upstream treats as a
    no-op: no flush, no key events, nothing on the wire.
    """
    parser = _guarded_parser()
    list(parser.feed("\x1b"))
    time.sleep(_SPLIT_GAP_SECONDS)
    escape = list(parser.tick())
    assert _keys(escape) == ["escape"]
    tokens = list(parser.feed("[200~"))

    assert parser.guard.paste_open is True
    tokens += _drain_until_recovered(parser)

    assert _pastes(tokens) == []
    assert _keys(parser.feed("q")) == ["q"]


def test_guard_state_machine_bounds() -> None:
    guard = BracketedPasteGuard(idle_timeout=3.0, abandon_grace=0.5)

    guard.note_input("hello", now=0.0)
    assert guard.paste_open is False
    assert guard.due(now=100.0) is False, "a closed parser is never forced"

    guard.note_input(BRACKETED_PASTE_START, now=1.0)
    assert guard.paste_open is True
    assert guard.due(now=3.9) is False
    assert guard.due(now=4.0) is True, "idle bound"

    guard.note_input("x", now=4.0)
    assert guard.due(now=4.1) is False
    guard.note_token(events.Key("escape", "\x1b"), now=4.1)
    assert guard.due(now=4.9) is False, (
        "an abandoned sequence alone is not the wedge -- a healthy paste "
        "carrying an ESC produces one too"
    )

    # The close really is on the wire; the parser just did not act on it.
    guard.note_input(BRACKETED_PASTE_END, now=4.2)
    assert guard.due(now=4.5) is False
    assert guard.due(now=4.6) is True, "abandoned-sequence bound"

    guard.note_token(events.Paste("x"), now=4.6)
    assert guard.paste_open is False
    assert guard.due(now=999.0) is False, "a Paste token proves the parser closed"


def test_guard_ignores_a_key_parsed_from_the_read_that_armed_it() -> None:
    """``paste_was_open`` is the only thing separating these two keystrokes.

    Both arrive while the guard believes a paste is open, and the closing
    marker is on the wire in both, so nothing else in the state machine can
    tell them apart.
    """
    coalesced = BracketedPasteGuard(idle_timeout=3.0, abandon_grace=0.5)
    coalesced.note_input(f"j{BRACKETED_PASTE_START}", now=0.0)
    coalesced.note_token(events.Key("j", "j"), now=0.0, paste_was_open=False)
    coalesced.note_input(BRACKETED_PASTE_END, now=0.1)
    assert coalesced.due(now=1.0) is False

    inside = BracketedPasteGuard(idle_timeout=3.0, abandon_grace=0.5)
    inside.note_input(BRACKETED_PASTE_START, now=0.0)
    inside.note_token(events.Key("j", "j"), now=0.0)
    inside.note_input(BRACKETED_PASTE_END, now=0.1)
    assert inside.due(now=1.0) is True


def test_a_re_arming_start_marker_drops_the_previous_paste_s_evidence() -> None:
    """Evidence belongs to the paste it was gathered for, never to the next."""
    guard = BracketedPasteGuard(idle_timeout=3.0, abandon_grace=0.5)
    guard.note_input(BRACKETED_PASTE_START, now=0.0)
    guard.note_token(events.Key("escape", "\x1b"), now=0.1)
    guard.note_input(BRACKETED_PASTE_END, now=0.2)
    assert guard.due(now=0.7) is True

    guard.note_input(BRACKETED_PASTE_START, now=0.8)
    assert guard.due(now=3.0) is False, "the new paste inherited the old close"


def test_guard_sees_a_start_marker_split_across_two_chunks() -> None:
    guard = BracketedPasteGuard()
    guard.note_input("\x1b[20", now=0.0)
    assert guard.paste_open is False
    guard.note_input("0~", now=0.1)
    assert guard.paste_open is True


@pytest.mark.parametrize("base", _GUARD_BASES)
def test_guarded_driver_class_is_a_subclass_and_is_cached(base: type[Driver]) -> None:
    guarded = guarded_driver_class(base)
    assert issubclass(guarded, base)
    assert guarded is guarded_driver_class(base), "a new class per call breaks identity"


@_POSIX_ONLY
def test_the_parser_swap_still_reaches_the_linux_driver() -> None:
    """Pin the seam the guard rides on.

    ``LinuxDriver.run_input_thread`` builds its parser from the module-level
    ``XTermParser`` name (``linux_driver.py:426``). If a Textual upgrade moves
    that construction, the guard would silently stop being installed; this
    turns that into a failing test instead.
    """
    # The sharp half: the method must still LOOK UP the module-level name.
    # A Textual release that inlined the import, or built the parser from a
    # direct attribute, would leave the rebind below green but ineffective.
    assert "XTermParser" in LinuxDriver.run_input_thread.__code__.co_names

    assert linux_driver.XTermParser is XTermParser
    with install_guarded_parser() as patched:
        assert linux_driver in patched
        assert linux_driver.XTermParser is GuardedXTermParser
    assert linux_driver.XTermParser is XTermParser


class _StubDriver(Driver):
    """Minimal concrete ``Driver`` used to observe the swap's scope.

    Instances are built with ``object.__new__`` so no app, loop or terminal is
    needed: the guard mixins only wrap methods and touch no driver state.
    """

    seen: list[type]

    def write(self, data: str) -> None: ...

    def start_application_mode(self) -> None: ...

    def disable_input(self) -> None: ...

    def stop_application_mode(self) -> None: ...


@_POSIX_ONLY
def test_input_thread_guard_swaps_the_parser_only_for_the_thread_body() -> None:
    class _WithInputThread(_StubDriver):
        def run_input_thread(self) -> None:
            self.seen.append(linux_driver.XTermParser)

    guarded = guarded_driver_class(_WithInputThread)
    driver = object.__new__(guarded)
    driver.seen = []

    driver.run_input_thread()

    assert driver.seen == [GuardedXTermParser], "the thread body built the stock parser"
    assert linux_driver.XTermParser is XTermParser, "the swap outlived the thread"


@_POSIX_ONLY
def test_application_mode_guard_spans_start_to_stop() -> None:
    """``WindowsDriver`` builds its parser on a thread started by
    ``start_application_mode``, so the swap has to survive that method's
    return and only unwind once ``stop_application_mode`` has joined it."""

    class _WithoutInputThread(_StubDriver):
        def start_application_mode(self) -> None:
            self.seen.append(linux_driver.XTermParser)

        def stop_application_mode(self) -> None:
            self.seen.append(linux_driver.XTermParser)

    guarded = guarded_driver_class(_WithoutInputThread)
    driver = object.__new__(guarded)
    driver.seen = []

    driver.start_application_mode()
    assert linux_driver.XTermParser is GuardedXTermParser
    driver.stop_application_mode()

    assert driver.seen == [GuardedXTermParser, GuardedXTermParser]
    assert linux_driver.XTermParser is XTermParser
