"""A deferred focus projection must never land in a modal's ``focused``.

``App.set_focus`` delegates to ``App.screen`` — the TOP screen — and never
checks that the widget belongs to it (Textual 8.2.8,
``textual/app.py:3150-3157``). Every service page projects focus through a
``call_after_refresh`` callback, so a modal pushed between the schedule and
the callback used to get a *base-screen* widget written into its
``focused``. ``Screen._binding_chain`` (``textual/screen.py:408-424``) is
then built from ``focused.ancestors_with_self`` — an ancestry that does not
contain the modal — so the modal's own ``escape`` binding is never even
looked at and the overlay cannot be dismissed.

**Risk 19 from the plan:** this reproduced 11/12 headless and 0/13 in a real
pty, because the real driver drains the pending callback before any modal
push. A green pty run therefore proves nothing; the headless shape below is
the only honest regression signal, so it drives the projection explicitly
rather than racing it.
"""

from __future__ import annotations

import asyncio
import contextlib
from functools import partial
from pathlib import Path

import pytest
from textual.dom import NoScreen
from textual.screen import Screen
from textual.widget import Widget

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot

_PAGES: tuple[tuple[str, str, type[Widget], FocusSlot], ...] = (
    ("glue", "#content-glue-page", GluePage, FocusSlot.GLUE_PRIMARY),
    ("athena", "#content-athena-page", AthenaPage, FocusSlot.ATHENA_PRIMARY),
    (
        "emr-serverless",
        "#content-emr-page",
        EmrServerlessPage,
        FocusSlot.EMR_RUNS,
    ),
)


async def _open_service(ctx: AppContext, pilot, service_id: str) -> None:  # type: ignore[no-untyped-def]
    """Swap the content host to ``service_id`` and settle its mount."""
    app = pilot.app
    await app.workers.wait_for_complete(list(app.workers._workers))
    await pilot.pause()
    ctx.root_vm.services_menu.switch_service_command.execute(service_id)
    await app.workers.wait_for_complete(list(app.workers._workers))
    setup_task = ctx.root_vm.content_host._setup_task
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()


async def _await_screen(app: AwsTuiApp, pilot, screen_type: type) -> None:  # type: ignore[no-untyped-def]
    """Wait for ``screen_type`` to reach the top of the stack.

    ``push_screen`` resolves on mount, so a single pause after the keypress
    would assert on a race rather than on the app.
    """
    async with asyncio.timeout(30.0):
        while not isinstance(app.screen, screen_type):
            await pilot.pause(0.01)


def _screen_of(widget: Widget | None) -> Screen[object] | None:
    if widget is None:
        return None
    try:
        return widget.screen
    except NoScreen:
        return None


def _assert_modal_owns_its_focus(app: AwsTuiApp) -> None:
    """The active modal's ``focused`` must belong to the modal itself.

    ``focused = None`` is fine: ``Screen._modal_binding_chain`` falls back to
    ``[(screen, ...)]`` and the modal's ``escape`` binding is still reachable.
    A foreign widget is the wedge.
    """
    screen = app.screen
    focused = screen.focused
    assert _screen_of(focused) in (None, screen), (
        f"modal {type(screen).__name__} has a foreign widget in `focused`: {focused!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("service_id", "selector", "page_type", "slot"), _PAGES)
async def test_projection_landing_behind_a_modal_leaves_it_escapable(
    tmp_path: Path,
    service_id: str,
    selector: str,
    page_type: type[Widget],
    slot: FocusSlot,
) -> None:
    """Drive the page's focus projection while a modal is on top.

    This is the deferred callback's payload, invoked at exactly the moment
    the callback would have fired had a modal been pushed first. Before the
    screen-identity guard it parked the page's widget in ``HelpModal.focused``
    and ``escape`` went dead.
    """
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, service_id)
            page = app.query_one(selector, page_type)

            await pilot.press("question_mark")
            await _await_screen(app, pilot, HelpModal)
            modal = app.screen

            page.project_focus_slot(slot)  # type: ignore[attr-defined]
            await pilot.pause()

            assert app.screen is modal
            _assert_modal_owns_its_focus(app)
            assert not page.has_focus_within

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpModal)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_pending_deferred_projection_does_not_wedge_a_modal(tmp_path: Path) -> None:
    """The real race: schedule the projection, then push the modal.

    ``call_after_refresh`` is queued here and the modal is pushed in the same
    tick, so the callback lands with ``HelpModal`` already on top — the
    ordering the headless driver reproduced 11/12 times.
    """
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, pilot, "glue")
            page = app.query_one("#content-glue-page", GluePage)

            page.call_after_refresh(partial(page.project_focus_slot, FocusSlot.GLUE_PRIMARY))
            app.push_screen(HelpModal(keymap=ctx.keymap_store))
            await _await_screen(app, pilot, HelpModal)
            await pilot.pause()
            await pilot.pause()

            _assert_modal_owns_its_focus(app)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpModal)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
