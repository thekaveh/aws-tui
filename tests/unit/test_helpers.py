"""Coverage for the shared test helpers themselves."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
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


class _FocusTarget(Static, can_focus=True):
    pass


class _FocusApp(App[None]):
    AUTO_FOCUS = None

    def compose(self) -> ComposeResult:
        with Container(id="parent"):
            yield _FocusTarget("target", id="target")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "diagnostic"),
    [
        ("hidden", "visible=False"),
        ("undisplayed", "displayed=False"),
        ("parent-undisplayed", "displayed=False"),
        ("disabled", "disabled=True"),
        ("parent-disabled", "disabled=True"),
        ("loading", "loading=True"),
        ("not-focusable", "focusable=False"),
    ],
)
async def test_focus_ready_target_rejects_failed_precondition_promptly(
    state: str, diagnostic: str
) -> None:
    async with _FocusApp().run_test() as pilot:
        widget = pilot.app.query_one("#target", _FocusTarget)
        parent = pilot.app.query_one("#parent", Container)
        match state:
            case "hidden":
                widget.visible = False
            case "undisplayed":
                widget.display = False
            case "parent-undisplayed":
                parent.display = False
            case "disabled":
                widget.disabled = True
            case "parent-disabled":
                parent.disabled = True
            case "loading":
                widget.loading = True
            case "not-focusable":
                widget.can_focus = False

        with pytest.raises(AssertionError) as excinfo:
            await asyncio.wait_for(focus_and_settle(widget, require_focusable=True), timeout=0.5)

        assert "_FocusTarget(id='target')" in str(excinfo.value)
        assert diagnostic in str(excinfo.value)
        assert not widget.has_focus


@pytest.mark.asyncio
async def test_focus_ready_target_takes_focus() -> None:
    async with _FocusApp().run_test() as pilot:
        widget = pilot.app.query_one("#target", _FocusTarget)
        await focus_and_settle(widget, require_focusable=True)
        assert widget.has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute", ["display", "visible", "disabled", "loading"])
async def test_focus_waits_for_temporary_unavailability(attribute: str) -> None:
    async with _FocusApp().run_test() as pilot:
        widget = pilot.app.query_one("#target", _FocusTarget)
        unavailable = attribute in {"disabled", "loading"}
        setattr(widget, attribute, unavailable)
        pending = asyncio.create_task(focus_and_settle(widget, timeout=2.0))
        try:
            await pilot.pause()
            assert not pending.done(), "focus completed before the target became available"
            assert not widget.has_focus
            setattr(widget, attribute, not unavailable)
            await asyncio.wait_for(pending, timeout=0.5)
            assert widget.has_focus
        finally:
            if not pending.done():
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending


@pytest.mark.asyncio
async def test_focus_detached_target_rejects_before_request() -> None:
    widget = _FocusTarget("detached", id="detached")
    with pytest.raises(AssertionError, match="is_attached=False"):
        await asyncio.wait_for(focus_and_settle(widget), timeout=0.5)


@pytest.mark.asyncio
async def test_focus_removed_target_fails_while_waiting() -> None:
    async with _FocusApp().run_test() as pilot:
        widget = pilot.app.query_one("#target", _FocusTarget)
        widget.disabled = True
        pending = asyncio.create_task(focus_and_settle(widget))
        await asyncio.sleep(0)
        await widget.remove()
        with pytest.raises(AssertionError, match="is_attached=False"):
            await asyncio.wait_for(pending, timeout=0.5)


@pytest.mark.asyncio
async def test_focus_timeout_names_current_preconditions() -> None:
    async with _FocusApp().run_test() as pilot:
        widget = pilot.app.query_one("#target", _FocusTarget)
        widget.disabled = True
        with pytest.raises(AssertionError) as excinfo:
            await focus_and_settle(widget, timeout=0.02)
        assert "never took focus within 0.02s" in str(excinfo.value)
        assert "disabled=True" in str(excinfo.value)
        assert "focusable=False" in str(excinfo.value)


@pytest.mark.asyncio
async def test_focus_retries_a_dropped_ready_request(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _FocusApp().run_test() as pilot:
        widget = pilot.app.query_one("#target", _FocusTarget)
        original_focus = widget.focus
        attempts = 0

        def drop_first_request(*, scroll_visible: bool = True) -> _FocusTarget:
            nonlocal attempts
            attempts += 1
            if attempts > 1:
                original_focus(scroll_visible=scroll_visible)
            return widget

        monkeypatch.setattr(widget, "focus", drop_first_request)
        await focus_and_settle(widget)
        assert widget.has_focus
        assert attempts >= 2
