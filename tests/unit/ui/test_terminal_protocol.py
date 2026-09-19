"""The in-band-resize default, and the upstream facts it is built on.

Two control tests exercise the stock Textual 8.2.8 parser — one for the
negotiation this module switches off, one for the stale-mode coordinate
collapse :class:`CellMouseXTermParser` closes — so both behaviours are
measured against reproduced upstream code rather than against an assumption.

The source tripwires at the bottom fail loudly if a Textual bump moves the
branch, renames the attribute, or starts reading it by value (any of which
would silently turn :func:`prefer_sigwinch_resize` into a no-op), and if it
moves either of the two *indirect* consumers of the gated branch, which is
what the default actually costs. The use-site count is deliberately not
presented as proof that the cost is nil: ``SMOOTH_SCROLL`` is already wired
to scroll behaviour and to mouse granularity one hop away, and a literal-name
count cannot see either.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
import textual
from textual import app as textual_app
from textual import constants, events, scrollbar
from textual._xterm_parser import XTermParser
from textual.messages import InBandWindowResize

from aws_tui.ui.terminal_protocol import (
    SMOOTH_SCROLL_ENV,
    CellMouseXTermParser,
    prefer_sigwinch_resize,
)

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="textual.drivers.linux_driver imports the POSIX-only `termios`, so it cannot even be imported on Windows. The guard itself is platform-neutral and stays covered there by the tests above.",
)

if sys.platform != "win32":
    from textual.drivers import linux_driver


# DECRPM reply for "mode 2048 is supported but currently reset" — what a
# Ghostty / WezTerm / kitty class terminal answers to ``ESC [ ? 2048 $ p``.
_MODE_2048_SUPPORTED_RESET = "\x1b[?2048;2$y"

# DECRPM reply for "mode 2048 is supported and already SET" — what a terminal
# answers when some earlier Textual app was killed before it could reset the
# mode. The terminal then keeps volunteering in-band reports unasked.
_MODE_2048_ALREADY_SET = "\x1b[?2048;1$y"

# An unsolicited in-band window-resize report: 40 rows x 200 columns, in a
# window 680 x 1600 pixels. Ratio 8 px per cell each way.
_IN_BAND_REPORT = "\x1b[48;40;200;680;1600t"

# SGR mouse press at column 101, row 21, which is cell (100, 20) zero-based.
_SGR_PRESS_AT_CELL_100_20 = "\x1b[<0;101;21M"

_SENTINEL_UNTOUCHED = "sentinel: prefer_sigwinch_resize must not write this"


def test_unset_variable_turns_the_in_band_protocol_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SMOOTH_SCROLL_ENV, raising=False)
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)

    assert prefer_sigwinch_resize() is True

    assert constants.SMOOTH_SCROLL is False
    assert os.environ[SMOOTH_SCROLL_ENV] == "0"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_variable_expresses_no_preference(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """Textual itself falls back to the default for a blank value.

    ``constants._get_environ_int`` calls ``int("")``, catches the
    ``ValueError`` and returns the default, so a blank export is not an
    opt-in to smooth scroll upstream either.
    """
    monkeypatch.setenv(SMOOTH_SCROLL_ENV, blank)
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)

    assert prefer_sigwinch_resize() is True

    assert constants.SMOOTH_SCROLL is False
    assert os.environ[SMOOTH_SCROLL_ENV] == "0"


@pytest.mark.parametrize("chosen", ["1", "0", "yes"])
def test_an_explicit_setting_wins_in_either_direction(
    monkeypatch: pytest.MonkeyPatch, chosen: str
) -> None:
    monkeypatch.setenv(SMOOTH_SCROLL_ENV, chosen)
    # A value Textual would never compute: if the function writes anything at
    # all here, the assertion below catches it.
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", _SENTINEL_UNTOUCHED)

    assert prefer_sigwinch_resize() is False

    assert constants.SMOOTH_SCROLL == _SENTINEL_UNTOUCHED
    assert os.environ[SMOOTH_SCROLL_ENV] == chosen


def test_main_settles_the_default_before_app_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The driver and its parser are built inside ``run()``, not before it.

    Deciding after ``run()`` has started would be too late: the 2048 query
    goes out during driver start-up.
    """
    from aws_tui import app as app_module

    observed: list[object] = []

    def fake_build_app_context(*, demo: bool) -> object:
        observed.append(("context", constants.SMOOTH_SCROLL))
        return object()

    class FakeApp:
        crash_report = None

        def __init__(self, *, context: object) -> None:
            observed.append(("construct", constants.SMOOTH_SCROLL))

        def run(self) -> None:
            observed.append(("run", constants.SMOOTH_SCROLL))

    monkeypatch.setattr(sys, "argv", ["aws-tui"])
    monkeypatch.delenv("AWS_TUI_DEMO", raising=False)
    monkeypatch.delenv(SMOOTH_SCROLL_ENV, raising=False)
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)
    monkeypatch.setattr(app_module, "build_app_context", fake_build_app_context)
    monkeypatch.setattr(app_module, "AwsTuiApp", FakeApp)

    app_module.main()

    assert observed[-1] == ("run", False)


def _parse(parser: XTermParser, data: str) -> list[object]:
    return list(parser.feed(data))


def test_stock_parser_negotiates_in_band_resize_while_smooth_scroll_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: the upstream branch this module exists to switch off."""
    monkeypatch.setattr("textual._xterm_parser.IS_ITERM", False)
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)

    tokens = _parse(XTermParser(), _MODE_2048_SUPPORTED_RESET)

    in_band = [token for token in tokens if isinstance(token, InBandWindowResize)]
    assert len(in_band) == 1
    assert in_band[0].supported is True


def test_the_default_stops_the_parser_accepting_the_2048_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: no ``InBandWindowResize``, so ``SIGWINCH`` survives.

    ``LinuxDriver.process_message`` sets ``_in_band_window_resize`` only from
    that message, and ``linux_driver.py:246-248`` skips ``send_size_event``
    only while that flag is set.
    """
    monkeypatch.setattr("textual._xterm_parser.IS_ITERM", False)
    monkeypatch.delenv(SMOOTH_SCROLL_ENV, raising=False)
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)

    prefer_sigwinch_resize()

    tokens = _parse(XTermParser(), _MODE_2048_SUPPORTED_RESET)

    assert [token for token in tokens if isinstance(token, InBandWindowResize)] == []


def _only_mouse_event(tokens: list[object]) -> events.MouseEvent:
    mouse = [token for token in tokens if isinstance(token, events.MouseEvent)]
    assert len(mouse) == 1, tokens
    return mouse[0]


def _feed_a_stale_in_band_session(parser: XTermParser) -> events.MouseEvent:
    """Drive the sequence a terminal with mode 2048 already set produces.

    The DECRPM reply says "supported and enabled", then the terminal
    volunteers a resize report of its own accord, then the user clicks.
    """
    assert _parse(parser, _MODE_2048_ALREADY_SET) == [], "the reply must be refused"
    _parse(parser, _IN_BAND_REPORT)
    return _only_mouse_event(_parse(parser, _SGR_PRESS_AT_CELL_100_20))


def test_stock_parser_divides_cell_coordinates_after_a_stale_in_band_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: the second upstream defect, reproduced against stock Textual.

    Refusing the DECRPM reply suppresses only the *acceptance* branch, and
    with it ``LinuxDriver._enable_mouse_pixels`` and its ``\x1b[?1016h``. The
    handler for an in-band *report* (``_xterm_parser.py:271-283``) is gated on
    nothing, so it sets ``mouse_pixels`` anyway and the parser starts dividing
    coordinates the terminal is still sending in cells.
    """
    monkeypatch.setattr("textual._xterm_parser.IS_ITERM", False)
    monkeypatch.delenv(SMOOTH_SCROLL_ENV, raising=False)
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)
    prefer_sigwinch_resize()

    parser = XTermParser()
    event = _feed_a_stale_in_band_session(parser)

    assert parser.mouse_pixels is True, "set behind our back by the report handler"
    # 8 px per cell each way: the click collapses towards the top-left corner.
    assert (event.x, event.y) == (12, 1)


def test_the_guarded_parser_reads_a_stale_in_band_session_in_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix: no pixel conversion, because 1016 was never negotiated."""
    monkeypatch.setattr("textual._xterm_parser.IS_ITERM", False)
    monkeypatch.delenv(SMOOTH_SCROLL_ENV, raising=False)
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)
    prefer_sigwinch_resize()

    event = _feed_a_stale_in_band_session(CellMouseXTermParser())

    assert (event.x, event.y) == (100, 20)


def test_the_guard_leaves_the_smooth_scroll_opt_in_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TEXTUAL_SMOOTH_SCROLL=1`` still gets sub-cell coordinates.

    There the DECRPM reply *is* accepted, so the driver really does send
    ``\x1b[?1016h`` and the terminal really is reporting pixels.
    """
    monkeypatch.setattr("textual._xterm_parser.IS_ITERM", False)
    monkeypatch.setenv(SMOOTH_SCROLL_ENV, "1")
    monkeypatch.setattr(constants, "SMOOTH_SCROLL", True)

    assert prefer_sigwinch_resize() is False

    parser = CellMouseXTermParser()
    accepted = _parse(parser, _MODE_2048_ALREADY_SET)
    assert [token for token in accepted if isinstance(token, InBandWindowResize)] != []
    _parse(parser, _IN_BAND_REPORT)
    event = _only_mouse_event(_parse(parser, _SGR_PRESS_AT_CELL_100_20))

    assert (event.x, event.y) == (12, 1)


def test_the_parser_the_drivers_actually_build_carries_the_guard() -> None:
    """The override is worthless unless it rides on the installed parser."""
    from aws_tui.ui.paste_guard import GuardedXTermParser

    assert issubclass(GuardedXTermParser, CellMouseXTermParser)


@_POSIX_ONLY
def test_the_sigwinch_handler_is_still_gated_on_the_in_band_flag() -> None:
    """Tripwire: this gate is the reason the default is worth having."""
    source = inspect.getsource(linux_driver)

    assert "def on_terminal_resize(signum, stack) -> None:" in source
    assert "if not self._in_band_window_resize:\n                send_size_event()" in source


def test_textual_still_reads_smooth_scroll_as_a_live_module_attribute() -> None:
    """Tripwire: a ``from constants import SMOOTH_SCROLL`` would defeat us.

    Rebinding ``constants.SMOOTH_SCROLL`` only works because the parser
    resolves the name through the module at parse time.
    """
    source = inspect.getsource(sys.modules["textual._xterm_parser"])

    assert "constants.SMOOTH_SCROLL" in source
    assert "from textual import constants" in source or "from . import constants" in source


def test_smooth_scroll_has_exactly_one_use_site_in_the_installed_package() -> None:
    """Tripwire: a use-site count, and nothing more than that.

    It pins that the flag is still read in exactly one place — the
    ``mode_id == "2048"`` guard — so :func:`prefer_sigwinch_resize` still
    switches off exactly what this module says it does, and a future Textual
    that reads the flag somewhere new fails here.

    It is **not** evidence that clearing the flag is free. The branch it gates
    already reaches scroll behaviour and mouse granularity one hop further on,
    through names this count cannot see; those are pinned by
    :func:`test_the_gated_branch_still_drives_smooth_scrolling_and_pixel_mouse`.
    """
    package = Path(next(iter(textual.__path__)))
    hits = {
        path.relative_to(package).as_posix(): path.read_text(encoding="utf-8").count(
            "SMOOTH_SCROLL"
        )
        for path in sorted(package.rglob("*.py"))
        if "SMOOTH_SCROLL" in path.read_text(encoding="utf-8")
    }

    # constants.py: the name and the environment variable on the definition
    # line, plus the variable again in its docstring. _xterm_parser.py: the
    # single ``mode_id == "2048"`` guard, and nothing else in the package.
    assert hits == {"constants.py": 3, "_xterm_parser.py": 1}


@_POSIX_ONLY
def test_the_gated_branch_still_drives_smooth_scrolling_and_pixel_mouse() -> None:
    """Tripwire: the two things refusing mode 2048 actually costs.

    Neither is reachable through the literal name ``SMOOTH_SCROLL``, so the
    use-site count above is blind to both. If a Textual bump moves either, the
    documented trade-off has to be re-measured instead of inherited.
    """
    app_source = inspect.getsource(textual_app)
    scrollbar_source = inspect.getsource(scrollbar)
    driver_source = inspect.getsource(linux_driver)

    # Written only from the message the refused branch would have produced ...
    assert app_source.count("self.supports_smooth_scrolling = message.enabled") == 1
    # ... and read only to decide whether a scrollbar drag animates or tracks.
    assert "animate=not self.app.supports_smooth_scrolling" in scrollbar_source

    # Pixel-precision mouse reporting is requested from that branch and
    # nowhere else, so refusing the branch keeps coordinates cell-granular.
    assert driver_source.count("self._enable_mouse_pixels()") == 1
    assert (
        "                    super().process_message(InBandWindowResize(True, True))\n"
        "                self._enable_mouse_pixels()\n" in driver_source
    )
    assert 'self.write("\\x1b[?1016h")' in driver_source


def test_the_in_band_report_handler_still_sets_mouse_pixels_ungated() -> None:
    """Tripwire: the reason :class:`CellMouseXTermParser` has to exist.

    Three occurrences and no more: the ``__init__`` default, the single
    ungated write in the in-band *report* handler, and the single read in
    ``parse_mouse_code``. The one read is what makes a one-line override
    total; if upstream gates the write on ``SMOOTH_SCROLL``, the override
    becomes redundant and should go.
    """
    source = inspect.getsource(sys.modules["textual._xterm_parser"])

    assert source.count("self.mouse_pixels") == 3
    assert source.count("self.mouse_pixels = True") == 1
