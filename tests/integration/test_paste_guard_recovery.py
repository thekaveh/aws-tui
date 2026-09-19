"""A split bracketed-paste close must not leave the app deaf.

Upstream defect, Textual 8.2.8 ``textual/_xterm_parser.py``: once
``bracketed_paste`` is set it is cleared only by an exact ``\\x1b[201~``, but
the inner escape loop abandons a partial sequence on ``ParseTimeout`` without
returning the consumed bytes. An ESC separated from its ``[201~`` by more than
``ESCAPE_DELAY`` -- an ordinary split over a laggy ssh or tmux link -- strands
the parser: the screen keeps repainting and every key, ``q`` and ``Ctrl+C``
included, is swallowed into the paste buffer.

These tests drive real terminal bytes through the parser the driver builds and
push the resulting tokens into a live app along the driver's own delivery path
(``Driver.process_message``), so the claim under test is the user-visible one:
after the wedge, does the app still answer a key?

``pilot.press`` cannot be used here -- it synthesises a ``Key`` event and
never touches the parser, which is exactly the layer that breaks.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import pytest
from textual import constants
from textual._xterm_parser import BRACKETED_PASTE_START, XTermParser
from textual.driver import Driver
from textual.message import Message

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.paste_guard import BracketedPasteGuard, GuardedXTermParser
from aws_tui.ui.widgets.help_modal import HelpModal

# ``?`` is the default binding for ``app.help`` (``KeymapStore.DEFAULT_BINDINGS``).
# It is the observable used throughout: a modal appearing proves a raw byte
# travelled parser -> driver -> app -> action.
_HELP_BYTE = "?"

_SPLIT_GAP_SECONDS = constants.ESCAPE_DELAY + 0.05
_TEST_IDLE_TIMEOUT = 0.4
_TEST_ABANDON_GRACE = 0.05
_SETTLE_TIMEOUT_SECONDS = 30.0


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.mkdir(PathRef(("alpha",)))
    return fs


def _pump(driver: Driver, tokens: Iterable[Message]) -> None:
    """Deliver parser tokens the way the real input thread does."""
    for token in tokens:
        driver.process_message(token)


async def _wedge(parser: XTermParser, driver: Driver, pilot) -> None:  # type: ignore[no-untyped-def]
    """Feed an open marker, content, then a closing marker split by a gap."""
    _pump(driver, parser.feed(f"{BRACKETED_PASTE_START}SELECT 1"))
    _pump(driver, parser.feed("\x1b"))
    await asyncio.sleep(_SPLIT_GAP_SECONDS)
    # The driver's select loop ticks at least every 0.1 s; ParseTimeout is
    # thrown into the parse generator here and the ESC is consumed alone.
    _pump(driver, parser.tick())
    _pump(driver, parser.feed("[201~"))
    await pilot.pause()


@pytest.mark.asyncio
async def test_app_goes_deaf_on_the_stock_parser(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    """The control. Without the guard the app never sees the key at all."""
    app = AwsTuiApp(app_context_factory(fs=await _seed()))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        driver = app._driver
        assert driver is not None
        parser = XTermParser()

        await _wedge(parser, driver, pilot)
        for _ in range(20):
            _pump(driver, parser.feed(_HELP_BYTE))
            _pump(driver, parser.tick())
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, HelpModal), (
            "upstream parser recovered on its own; the guard's premise is gone"
        )


@pytest.mark.asyncio
async def test_app_answers_a_key_after_the_guard_recovers(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory(fs=await _seed()))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        driver = app._driver
        assert driver is not None
        parser = GuardedXTermParser(
            guard=BracketedPasteGuard(
                idle_timeout=_TEST_IDLE_TIMEOUT,
                abandon_grace=_TEST_ABANDON_GRACE,
            )
        )

        await _wedge(parser, driver, pilot)
        # Still deaf at this point -- the guard has not fired yet.
        _pump(driver, parser.feed(_HELP_BYTE))
        await pilot.pause()
        assert not isinstance(app.screen, HelpModal)

        deadline = time.monotonic() + _SETTLE_TIMEOUT_SECONDS
        while time.monotonic() < deadline and not parser.guard.recoveries:
            _pump(driver, parser.tick())
            await pilot.pause(0.02)
        assert parser.guard.recoveries == 1, "the guard never recovered the parser"

        # The byte the user types after the recovery reaches the app.
        _pump(driver, parser.feed(_HELP_BYTE))
        async with asyncio.timeout(_SETTLE_TIMEOUT_SECONDS):
            while not isinstance(app.screen, HelpModal):
                await pilot.pause(0.01)

        assert isinstance(app.screen, HelpModal)


@pytest.mark.asyncio
async def test_the_shipped_app_installs_the_guard(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    """``get_driver_class`` is the wiring the two tests above stand in for.

    ``run_test`` runs headless and therefore never builds the platform driver,
    so the assertion is on the class the app resolved at construction -- which
    is what ``App._get_driver`` uses on every non-headless run.
    """
    app = AwsTuiApp(app_context_factory(fs=await _seed()))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        driver_class = app.driver_class

    assert driver_class.__name__.startswith("PasteGuarded")
    assert issubclass(driver_class, Driver)
