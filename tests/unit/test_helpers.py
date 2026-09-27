"""Coverage for the shared test helpers themselves."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from tests.helpers import WAIT_UNTIL_TIMEOUT_SECONDS, focus_and_settle, wait_until


@pytest.mark.asyncio
async def test_wait_until_names_the_condition_that_never_settled() -> None:
    """The diagnostic is the reason this helper exists rather than a sleep.

    Mutation testing covered the waiting but not the bound, so the failure
    path shipped untested.
    """
    with pytest.raises(AssertionError) as excinfo:
        await wait_until(lambda: False, what="the thing never happened", timeout=0.05)

    assert "the thing never happened" in str(excinfo.value)
    assert "0.05" in str(excinfo.value)


@pytest.mark.asyncio
async def test_wait_until_returns_immediately_when_already_satisfied() -> None:
    await wait_until(lambda: True, what="already true", timeout=0.05)


def test_wait_until_default_leaves_room_for_two_waits_under_pytests_kill() -> None:
    """Two sequential waits must not outlast pytest's own per-test timeout.

    `pyproject.toml` sets `timeout = 60`. A 30s default meant a test that waits
    twice lost the race to pytest's kill, and the caller got a bare
    `Failed: Timeout` instead of the named condition above.
    """
    assert 2 * WAIT_UNTIL_TIMEOUT_SECONDS < 60


@pytest.mark.asyncio
async def test_focus_and_settle_reissues_a_focus_request_that_was_dropped() -> None:
    """#276: a focus request made while the widget cannot take focus is dropped.

    `Widget.focus()` defers through `call_later`, and by the time that deferred
    call runs `App.set_focus` is a no-op if the widget is not focusable yet. A
    single `focus()` followed by polling therefore waits out the entire timeout
    for a request that will never be honoured. Re-issuing each cycle means the
    first cycle in which the widget can take focus is the one that lands it.
    """

    class _LateFocusable(Static, can_focus=False):
        """Refuses focus until `allow()` is called, like a not-yet-ready widget."""

        def allow(self) -> None:
            self.can_focus = True

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield _LateFocusable("late", id="late")

    app = _App()
    async with app.run_test() as pilot:
        widget = app.query_one("#late", _LateFocusable)
        assert not widget.has_focus

        async def _allow_shortly() -> None:
            # Long enough that the first focus() is certainly dropped.
            await asyncio.sleep(0.15)
            widget.allow()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_allow_shortly())
            tasks.create_task(focus_and_settle(widget, timeout=5.0))

        assert widget.has_focus, "the dropped focus request was never re-issued"
        await pilot.pause()
