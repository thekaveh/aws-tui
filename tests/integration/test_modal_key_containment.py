"""A modal must swallow the keys the App binds with ``priority=True``.

Textual dispatches App-level ``priority=True`` bindings BEFORE the active
screen's own, so ``AwsTuiApp`` hand-forwards Enter and the arrow keys to
the modal on top of the stack. That forwarding used to be conditional on
the modal implementing a matching handler; when it did not, control fell
through to the pane or service page *behind* the overlay. These tests pin
the containment guarantee and the scrolling that makes it usable.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from textual.containers import VerticalScroll

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.ui.widgets.quick_look import QuickLook


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    # A directory first so the initial cursor sits on something Enter
    # would descend into if the keystroke escaped the overlay.
    await fs.mkdir(PathRef(("alpha",)))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"beta"))
    await fs.write_stream(PathRef(("gamma.txt",)), _stream(b"gamma"))
    return fs


async def _await_cursor(app: AwsTuiApp, pilot) -> None:  # type: ignore[no-untyped-def]
    """Wait for the focused pane to actually have a cursor entry.

    Rendered ``EntryRow`` widgets are not the precondition these tests need:
    ``action_delete`` and ``action_quick_look`` both return silently when
    ``focused_pane.selected_entry`` is ``None``, so pressing the key on a pane
    that has painted its rows but not yet settled its cursor is a no-op and the
    modal never opens. That is order- and load-dependent -- it passes alone and
    fails in a full run.
    """
    async with asyncio.timeout(30.0):
        while True:
            dual = app._dual_pane()  # type: ignore[attr-defined]
            pane = getattr(dual, "focused_pane", None) if dual is not None else None
            selected = getattr(pane, "selected_entry", None) if pane is not None else None
            if selected is not None and not selected.is_parent_link:
                return
            await pilot.pause(0.01)


async def _await_screen(app: AwsTuiApp, pilot, screen_type: type) -> None:  # type: ignore[no-untyped-def]
    """Wait for ``screen_type`` to reach the top of the screen stack.

    Opening a modal runs through an action and a ``push_screen`` that Textual
    resolves on mount, so a single ``pilot.pause()`` after the keypress asserts
    on a race rather than on the app.
    """
    async with asyncio.timeout(30.0):
        while not isinstance(app.screen, screen_type):
            await pilot.pause(0.01)


@pytest.mark.asyncio
async def test_enter_behind_help_modal_does_not_navigate_pane(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    """``HelpModal`` implements none of the six confirm-style handlers
    ``action_descend`` probes. Enter must still stop at the overlay."""
    app = AwsTuiApp(app_context_factory(fs=await _seed()))
    async with app.run_test(size=(120, 40)) as pilot:
        await _await_cursor(app, pilot)
        pane = app._dual_pane().focused_pane  # type: ignore[attr-defined,union-attr]
        before = str(pane.path)

        await pilot.press("question_mark")
        await _await_screen(app, pilot, HelpModal)

        await pilot.press("enter")
        await pilot.pause()

        assert str(pane.path) == before
        assert isinstance(app.screen, HelpModal)


@pytest.mark.asyncio
async def test_arrows_behind_confirm_modal_do_not_move_pane_cursor(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    """``ConfirmModal`` has no ``action_move_up``/``action_move_down``, so
    ↑/↓ used to move a pane cursor hidden behind the dialog."""
    app = AwsTuiApp(app_context_factory(fs=await _seed()))
    async with app.run_test(size=(120, 40)) as pilot:
        await _await_cursor(app, pilot)
        pane = app._dual_pane().focused_pane  # type: ignore[attr-defined,union-attr]

        await pilot.press("d")
        await _await_screen(app, pilot, ConfirmModal)

        selected = pane.selected_entry
        before = selected.entry.name if selected else None

        await pilot.press("down")
        await pilot.press("up")
        await pilot.press("down")
        await pilot.pause()

        selected = pane.selected_entry
        assert (selected.entry.name if selected else None) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (100, 24)])
async def test_help_body_scrolls_with_arrow_keys(
    app_context_factory,  # type: ignore[no-untyped-def]
    size: tuple[int, int],
) -> None:
    """The help body overflows at every supported size; without arrow
    scrolling the App section and docs links below the fold are only
    reachable with PageDown/End, which the footer does not advertise."""
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=size) as pilot:
        await pilot.press("question_mark")
        await _await_screen(app, pilot, HelpModal)

        body = app.screen.query_one(VerticalScroll)
        assert body.max_scroll_y > 0, "help body does not overflow — assertion below is vacuous"

        for _ in range(3):
            await pilot.press("down")
        await pilot.pause()
        assert body.scroll_offset.y > 0

        for _ in range(5):
            await pilot.press("up")
        await pilot.pause()
        assert body.scroll_offset.y == 0


@pytest.mark.asyncio
async def test_quick_look_preview_scrolls_with_arrow_keys(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    fs = InMemoryFS()
    body_text = "\n".join(f"line {n:04d}" for n in range(400)).encode()
    await fs.write_stream(PathRef(("long.txt",)), _stream(body_text))

    ctx = app_context_factory(fs=fs)
    # Quick Look previews the *remote* pane's entry, so the pane needs a
    # configured connection; without one the action is a no-op.
    ctx.config_store.path.write_text(
        '[defaults]\nconnection = "test"\n\n'
        "[connections.test]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9000"\n'
        'credentials = "static"\n'
        'access_key_id = "k"\n'
        'secret_access_key = "s"\n'
        'region = "us-east-1"\n'
    )
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _await_cursor(app, pilot)

        await pilot.press("space")
        await _await_screen(app, pilot, QuickLook)

        scroll = app.screen.query_one("#quicklook-body-scroll", VerticalScroll)
        async with asyncio.timeout(10.0):
            while scroll.max_scroll_y <= 0:
                await pilot.pause(0.01)

        for _ in range(3):
            await pilot.press("down")
        await pilot.pause()
        assert scroll.scroll_offset.y > 0

        for _ in range(5):
            await pilot.press("up")
        await pilot.pause()
        assert scroll.scroll_offset.y == 0
