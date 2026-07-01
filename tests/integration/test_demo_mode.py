"""Integration test for the demo-mode boot flow.

Boots ``AwsTuiApp(build_app_context(demo=True))`` headlessly and
walks the canonical demo journey: verifies the BrandBanner shows
the demo chip, confirms the 4 demo connections are configured, and
checks that the demo-dev InMemoryFS seed exposes ``etl-input`` at
the file-pane level.
"""

from __future__ import annotations

import contextlib

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.nav_row import NavRow
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot

pytestmark = pytest.mark.asyncio


async def test_demo_mode_boots_with_four_demo_connections(tmp_path) -> None:
    """End-to-end: demo=True wires DemoConnectionResolver +
    InMemoryFS factories so the app boots without touching real
    AWS or local config."""
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    # Sanity: the AppContext flag itself.
    assert ctx.demo is True
    # The connection resolver is the demo one.
    conns = ctx.connection_resolver.list()
    names = {c.name for c in conns}
    assert {"demo-dev", "demo-prod", "demo-shared", "demo-minio"}.issubset(names)
    # Boot the Textual app and verify the BrandBanner subtitle.
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            # Drain async workers then let the event loop settle.
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            await pilot.pause()

            banner = app.query_one(BrandBanner)
            assert "DEMO MODE" in banner.border_subtitle
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_mode_renders_s3_pane_with_demo_files(tmp_path) -> None:
    """The demo-dev connection's InMemoryFS seed exposes ``etl-input``
    in the S3 file pane.

    With the SSO-probe bypass in place (``ctx.demo`` short-circuits
    ``probe_token``), the natural boot chain reaches ``demo-dev``
    (first ``kind="aws"`` connection) and mounts its seeded InMemoryFS
    automatically — no explicit private-method pokes required.
    """
    from aws_tui.ui.widgets.pane import EntryRow

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            # Drain boot chain workers and let the event loop settle.
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            # Drain workers so DualPane.on_mount → VM setup() → InMemoryFS.list
            # completes and the EntryRow widgets are populated.
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            # File rows are EntryRow(Widget) — not Static — so query
            # the concrete class. str(rich.text.Text) yields plain text
            # which includes the name column padded to column width.
            all_text = " ".join(str(w.render()) for w in app.query(EntryRow))
            assert "etl-input" in all_text, (
                f"expected 'etl-input' bucket in the rendered EntryRow tree; got "
                f"first 200 chars: {all_text[:200]!r}"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_mode_shift_s_uses_seeded_connection_factory(tmp_path) -> None:
    """Shift+S must keep demo panes on seeded in-memory providers.

    The boot path starts on ``demo-dev``. Cycling once moves the focused
    pane to ``demo-prod``; if that path bypasses ``S3Service``'s demo
    factory it will try to construct a real boto-backed S3FS instead of
    rendering the seeded ``data-lake`` objects.
    """
    from aws_tui.ui.widgets.pane import EntryRow

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            await pilot.press("S")  # Shift+S
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            dual = ctx.root_vm.content_host.current
            focused = dual.focused_pane
            all_text = " ".join(str(w.render()) for w in app.query(EntryRow))

            assert focused.identity_label == "aws s3 · demo-prod · us-east-1"
            assert "data-lake" in all_text
            assert "etl-output" in all_text
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def test_demo_mode_launch_selects_menu_not_s3_panes(tmp_path) -> None:
    """Demo launch starts with the nav/menu pane visually selected.

    The active service is still S3, but neither S3 pane should show the
    focused border until the user explicitly tabs/enters into the service.
    """
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(context=ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            for _ in range(5):
                await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
                await pilot.pause()

            selected_nav = [row for row in app.query(NavRow) if "-selected" in row.classes]
            focused_panes = [pane for pane in app.query(Pane) if "-focused" in pane.classes]

            assert ctx.root_vm.services_menu.selected_id == "s3"
            assert len(list(app.query(Pane))) == 2
            assert ctx.focus_coordinator.focused_slot is FocusSlot.NAV_MENU
            assert isinstance(app.focused, NavMenu)
            assert "-rail-active" in app.screen.classes
            assert [row.descriptor_id for row in selected_nav] == ["s3"]
            assert focused_panes == []
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
