"""Direct unit coverage for the deferred-focus guards.

Both predicates were previously pinned only through the three service-page
suites, so a mutation to either failed nine widget tests deep instead of here.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from aws_tui.ui.widgets._focus_guard import focus_rests_within, is_on_active_screen


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="outer"):
            yield Button("inner", id="inner")
        yield Static("sibling", id="sibling")


class _Modal(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        yield Button("modal", id="modal-button")


@pytest.mark.asyncio
async def test_focus_rests_within_covers_each_branch() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        outer = app.query_one("#outer", Vertical)
        inner = app.query_one("#inner", Button)
        sibling = app.query_one("#sibling", Static)

        # Nothing focused: there is no focus to rest anywhere.
        assert not focus_rests_within(outer, None)
        # The target itself. Folded in for symmetry; App.set_focus already
        # short-circuits this one.
        assert focus_rests_within(outer, outer)
        # A descendant -- the case the guard exists for.
        assert focus_rests_within(outer, inner)
        # An unrelated widget.
        assert not focus_rests_within(outer, sibling)
        # Reversed containment is not containment.
        assert not focus_rests_within(inner, outer)


@pytest.mark.asyncio
async def test_focus_rests_within_does_not_raise_for_a_detached_widget() -> None:
    """Mid-teardown, `focused` can already be off the tree.

    `ancestors_with_self` only walks `_parent`, so a detached widget yields
    just itself rather than raising -- and the guard then reports False, which
    lets the projection proceed. Pinned because a raise here would surface as
    an exception inside a deferred callback.
    """
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        outer = app.query_one("#outer", Vertical)
        inner = app.query_one("#inner", Button)
        await inner.remove()
        await pilot.pause()

        assert not focus_rests_within(outer, inner)


@pytest.mark.asyncio
async def test_is_on_active_screen_is_false_once_a_modal_is_pushed() -> None:
    """The other stale assumption a deferred projection can make.

    `App.set_focus` writes into `App.screen` -- the TOP screen -- without
    checking the widget belongs to it, so a projection landing after a modal
    push would park a base-screen widget in the modal's `focused` and the
    modal could never be escaped.
    """
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        outer = app.query_one("#outer", Vertical)
        assert is_on_active_screen(outer)

        app.push_screen(_Modal())
        await pilot.pause()

        assert not is_on_active_screen(outer)

        app.pop_screen()
        await pilot.pause()

        assert is_on_active_screen(outer)


@pytest.mark.asyncio
async def test_is_on_active_screen_is_false_for_a_detached_widget() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        inner = app.query_one("#inner", Button)
        await inner.remove()
        await pilot.pause()

        assert not is_on_active_screen(inner)


def test_is_on_active_screen_is_false_with_no_running_app() -> None:
    """`is_attached` swallows `NoActiveAppError` itself and returns False.

    That is what makes reading `widget.app` inside the guard safe without
    reaching into `textual._context`.
    """
    assert not is_on_active_screen(Static("orphan"))
