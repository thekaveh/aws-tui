"""``AppBlur`` must tear down the pointer state the app can no longer see.

Textual 8.2.8 handles ``AppBlur`` in ``App._watch_app_focus``
(``textual/app.py:4418-4448``, blur branch ``:4443-4448``) and tears down
*focus* only. Two pieces of pointer-driven state survive the blur, and both
were reported by a user doing exactly the gesture that produces one --
switching macOS Spaces or terminal tabs:

* the tooltip stays painted over the pane, because nothing on the blur path
  calls ``Screen._clear_tooltip``, which is what Textual itself uses from
  ``Screen._on_screen_suspend`` (``textual/screen.py:1502-1508``);
* ``App.mouse_captured`` keeps pointing at a scrollbar whose drag the blur
  interrupted, because the blur path is the only teardown in ``app.py`` that
  does not call ``capture_mouse(None)`` (compare ``:2868``, ``:2941``,
  ``:3017``).

The capture orphan is benign *today*: 0 B/s of output, 0.0% CPU, self-healing
on the next click at a cost of one swallowed click. The reason it is worth a
regression test is what sits under it. ``ScrollBar._on_mouse_capture``
(``textual/scrollbar.py:363``) calls ``App._realtime_animation_begin``, which
calls ``gc.disable()`` when ``PAUSE_GC_ON_SCROLL`` is true, and only a
``MouseRelease`` reaches the matching ``_realtime_animation_complete``.
Textual's class default is ``False`` (``textual/app.py:526``) and ``AwsTuiApp``
does not override it -- that, and nothing else, is what keeps the orphan
harmless. ``test_app_blur_balances_the_realtime_animation_count`` therefore
pins the balance itself rather than only the attribute, because the count is
the thing ``gc.enable()`` hangs off.

Harness notes, both of which cost real time to discover:

* Textual scrollbars are **not** in the query tree -- ``app.screen.query(
  ScrollBar)`` returns nothing. They are reached as
  ``widget.vertical_scrollbar``. ``test_the_scrollbar_is_not_in_the_query_tree``
  pins that so the next person does not repeat the search.
* At the 120x40 size the other integration tests use, the pane body does not
  overflow and there is no scrollbar to grab at all. These tests use 120x18.
* ``run_test`` disables tooltips by default (``app._disable_tooltips = not
  tooltips``), so the tooltip tests pass ``tooltips=True``. ``TOOLTIP_DELAY``
  is shortened rather than zeroed: ``Timer`` divides by its interval and a
  literal ``0.0`` raises ``ZeroDivisionError`` at shutdown.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from textual import events
from textual.containers import VerticalScroll
from textual.scrollbar import ScrollBar
from textual.widgets import Tooltip

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.pane import EntryRow, Pane
from tests.helpers import drain_workers
from tests.integration.conftest import AppContextBuilder

# Enough entries that the pane body overflows its 18-row viewport and Textual
# gives it a real vertical scrollbar. The listing content is irrelevant.
_ENTRY_COUNT = 60

# Short enough that a test is not waiting on the 0.5 s default, non-zero
# because ``Timer._run`` divides by the interval.
_FAST_TOOLTIP_DELAY = 0.01

# A screen small enough that the pane body overflows. 120x40 -- the size the
# rest of the integration tier uses -- does not, and the scrollbar assertions
# below then have nothing to grab.
_SMALL_SCREEN = (120, 18)


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    for index in range(_ENTRY_COUNT):
        await fs.write_stream(PathRef((f"file-{index:03d}.txt",)), _stream(b"x"))
    return fs


async def _settled_rows(app: AwsTuiApp, pilot) -> list[EntryRow]:  # type: ignore[no-untyped-def]
    """Let the app finish booting and return the mounted entry rows.

    Constraint 23: any Pane assertion needs two ``pilot.pause()`` calls, and
    the mount worker spawns descendants, so ``drain_workers`` (Constraint 27)
    has to run between them.
    """
    await pilot.pause()
    await drain_workers(app)
    await pilot.pause()
    await pilot.pause()
    rows = list(app.query(EntryRow))
    assert rows, "precondition: the panes must have mounted entry rows"
    return rows


def _pane_body(app: AwsTuiApp) -> VerticalScroll:
    return app.query(Pane).first().query_one("#pane-body", VerticalScroll)


async def _open_tooltip(app: AwsTuiApp, pilot, row: EntryRow) -> Tooltip:  # type: ignore[no-untyped-def]
    """Hover ``row`` through the real pointer path until the tooltip shows."""
    app.TOOLTIP_DELAY = _FAST_TOOLTIP_DELAY
    landed = await pilot.hover(row)
    assert landed, "precondition: the hover must land on the entry row"
    for _ in range(50):
        await pilot.pause()
        tooltip = app.screen.query_one(Tooltip)
        if tooltip.display:
            return tooltip
        await asyncio.sleep(0.01)
    raise AssertionError("precondition: the hover never opened a tooltip")


async def _grab_scrollbar(app: AwsTuiApp, pilot) -> ScrollBar:  # type: ignore[no-untyped-def]
    """Start a scrollbar drag the way a real mouse-down on one does.

    ``ScrollBar.action_grab`` is the scrollbar's own "begin capturing the
    mouse cursor" entry point (``textual/scrollbar.py:348-350``); it calls
    ``Widget.capture_mouse`` and so runs the whole real capture path,
    ``_on_mouse_capture`` and ``_realtime_animation_begin`` included.
    """
    scrollbar = _pane_body(app).vertical_scrollbar
    assert scrollbar.display, "precondition: the pane body must actually overflow"
    scrollbar.action_grab()
    await pilot.pause()
    assert app.mouse_captured is scrollbar, "precondition: the drag must hold the capture"
    return scrollbar


@pytest.mark.asyncio
async def test_app_blur_clears_a_showing_tooltip(
    app_context_factory: AppContextBuilder,
) -> None:
    """The reported symptom: a tooltip left painted over the pane.

    Without the handler the tooltip survives both ``AppBlur`` and the
    ``AppFocus`` that follows it, so the artefact is still there when the user
    comes back -- which is what makes it worth clearing rather than leaving to
    the next pointer move.
    """
    ctx = app_context_factory(fs=await _seed())
    app = AwsTuiApp(ctx)
    async with app.run_test(size=_SMALL_SCREEN, tooltips=True) as pilot:
        rows = await _settled_rows(app, pilot)
        tooltip = await _open_tooltip(app, pilot, rows[0])

        app.post_message(events.AppBlur())
        await pilot.pause()
        await pilot.pause()

        assert tooltip.display is False

        # And it stays gone across the return trip, rather than the blur
        # merely deferring it.
        app.post_message(events.AppFocus())
        await pilot.pause()
        await pilot.pause()
        assert tooltip.display is False


@pytest.mark.asyncio
async def test_app_blur_releases_an_orphaned_mouse_capture(
    app_context_factory: AppContextBuilder,
) -> None:
    """A scrollbar drag interrupted by the blur must not keep the capture.

    The user-visible cost is one swallowed click on return, because the next
    ``MouseDown`` is routed to the scrollbar that is still captured instead of
    to whatever was clicked.
    """
    ctx = app_context_factory(fs=await _seed())
    app = AwsTuiApp(ctx)
    async with app.run_test(size=_SMALL_SCREEN) as pilot:
        await _settled_rows(app, pilot)
        scrollbar = await _grab_scrollbar(app, pilot)

        app.post_message(events.AppBlur())
        await pilot.pause()
        await pilot.pause()

        assert app.mouse_captured is None
        # ``capture_mouse(None)`` posts the ``MouseRelease`` the interrupted
        # drag never got, so the scrollbar drops its grab too rather than
        # sitting in a half-dragged state.
        assert scrollbar.grabbed is None


@pytest.mark.asyncio
async def test_app_blur_balances_the_realtime_animation_count(
    app_context_factory: AppContextBuilder,
) -> None:
    """The reason the capture release is worth doing at all.

    ``_realtime_animation_count`` is what ``_realtime_animation_complete``
    tests before calling ``gc.enable()``. Leave it at 1 and an app that ever
    sets ``PAUSE_GC_ON_SCROLL = True`` runs with garbage collection disabled
    for the rest of the process -- the progressive "it got unusably slow after
    I switched away and back" this whole change came from. Asserting the count
    proves the balance without this test flipping real interpreter state.
    """
    ctx = app_context_factory(fs=await _seed())
    app = AwsTuiApp(ctx)
    async with app.run_test(size=_SMALL_SCREEN) as pilot:
        await _settled_rows(app, pilot)
        assert app.PAUSE_GC_ON_SCROLL is False, (
            "AwsTuiApp must not set PAUSE_GC_ON_SCROLL; it is the only reason "
            "an orphaned capture was ever harmless"
        )
        baseline = app._realtime_animation_count

        await _grab_scrollbar(app, pilot)
        assert app._realtime_animation_count == baseline + 1, (
            "precondition: grabbing the scrollbar must open a realtime animation"
        )

        app.post_message(events.AppBlur())
        await pilot.pause()
        await pilot.pause()

        assert app._realtime_animation_count == baseline


@pytest.mark.asyncio
async def test_app_blur_is_harmless_with_nothing_to_clean_up(
    app_context_factory: AppContextBuilder,
) -> None:
    """The handler runs on every blur, most of which have nothing to release.

    It also runs on a path where the screen stack can be in any state, so it
    must never raise: an exception here reaches ``App._handle_exception`` and
    would take the app down over a cosmetic cleanup. A crash would surface as
    the pilot re-raising on exit.
    """
    ctx = app_context_factory(fs=await _seed())
    app = AwsTuiApp(ctx)
    async with app.run_test(size=_SMALL_SCREEN) as pilot:
        await _settled_rows(app, pilot)
        assert app.mouse_captured is None

        for _ in range(3):
            app.post_message(events.AppBlur())
            app.post_message(events.AppFocus())
        await pilot.pause()
        await pilot.pause()

        assert app.is_running
        assert app.mouse_captured is None
        assert app._realtime_animation_count == 0


@pytest.mark.asyncio
async def test_the_scrollbar_is_not_in_the_query_tree(
    app_context_factory: AppContextBuilder,
) -> None:
    """Pin the harness fact the tests above depend on.

    ``ScrollBar`` widgets are attached outside the queryable children, so
    ``query(ScrollBar)`` finds none and the only handle is
    ``widget.vertical_scrollbar``. If a Textual upgrade ever puts them in the
    tree this fails loudly here instead of turning the capture tests into
    silent no-ops somewhere else.
    """
    ctx = app_context_factory(fs=await _seed())
    app = AwsTuiApp(ctx)
    async with app.run_test(size=_SMALL_SCREEN) as pilot:
        await _settled_rows(app, pilot)

        assert not list(app.screen.query(ScrollBar))
        assert isinstance(_pane_body(app).vertical_scrollbar, ScrollBar)
