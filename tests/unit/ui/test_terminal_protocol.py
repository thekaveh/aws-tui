"""The in-band-resize default, and the upstream facts it is built on.

``test_stock_parser_negotiates_in_band_resize_while_smooth_scroll_is_set`` is
the control: it exercises the Textual 8.2.8 branch this module exists to
switch off, so the default below is measured against reproduced upstream
behaviour rather than against an assumption. The three source tripwires at
the bottom fail loudly if a Textual bump moves the branch, renames the
attribute, or starts reading it by value — any of which would silently turn
:func:`prefer_sigwinch_resize` into a no-op.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
import textual
from textual import constants
from textual._xterm_parser import XTermParser
from textual.drivers import linux_driver
from textual.messages import InBandWindowResize

from aws_tui.ui.terminal_protocol import SMOOTH_SCROLL_ENV, prefer_sigwinch_resize

# DECRPM reply for "mode 2048 is supported but currently reset" — what a
# Ghostty / WezTerm / kitty class terminal answers to ``ESC [ ? 2048 $ p``.
_MODE_2048_SUPPORTED_RESET = "\x1b[?2048;2$y"

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


def test_smooth_scroll_gates_nothing_but_the_in_band_branch() -> None:
    """Tripwire: it is why clearing the flag costs no rendering quality.

    If a future Textual wires ``SMOOTH_SCROLL`` to real scroll rendering,
    this fails and the trade-off has to be re-argued instead of inherited.
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
