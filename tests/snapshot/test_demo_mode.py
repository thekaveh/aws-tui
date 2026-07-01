"""Per-theme snapshot of demo-mode boot + content-presence guard."""

from __future__ import annotations

import asyncio
import contextlib
import html as html_lib
from pathlib import Path

import pytest

from aws_tui.ui.widgets.pane import Pane
from tests.snapshot.apps.demo_mode import DemoModeApp
from tests.snapshot.conftest import THEMES

# Narrower than conftest.TERMINAL_SIZE (120, 40): the extra 10 rows push
# the boot toast below the captured frame, hiding the "Demo mode active"
# advisory that our content-presence guard checks for.  30 rows keeps
# both the BrandBanner "DEMO MODE" chip and the toast overlay in frame.
TERMINAL_SIZE = (120, 30)


async def _drain_workers(pilot) -> None:  # type: ignore[no-untyped-def]
    """Wait for all async boot workers AND their downstream reactive
    updates to settle before snapping.

    Boot chain has two layers that need to settle before the snapshot:

    Layer 1 — Textual ``run_worker`` workers
        ``_initial_mount_worker`` drives the connection attempt and
        resolves when the LEFT pane reaches a terminal state (IDLE,
        EMPTY, UNREACHABLE, etc.).  ``workers.wait_for_complete``
        catches these.

    Layer 2 — plain asyncio task (``ContentHostVM._setup_task``)
        ``ContentHostVM.set_content()`` dispatches ``DualPaneVM.setup()``
        as a raw ``asyncio.create_task``, NOT a Textual worker.
        ``workers.wait_for_complete`` is blind to this task.
        We await it directly via
        ``app._app_ctx.root_vm.content_host._setup_task`` so we know
        the InMemoryFS listing has finished before we force-refresh
        the chrome.

    Layer 3 — direct chrome refresh
        After setup completes, ``_notify("viewmodel")`` has fired but
        the downstream call_after_refresh → InvokeLater → screen
        callback chain is non-deterministic across the 10 sequential
        test runs (different asyncio scheduling pressure per theme).
        Rather than polling the async queue, we call ``_refresh_chrome``
        directly on every mounted ``Pane`` widget.  This is safe because:
        (a) setup has completed, so ``pane._vm.viewmodel.summary`` already
            holds the settled value ("2 obj · 0 B", "21 obj · 94 B", etc.),
        (b) ``_refresh_chrome`` is a pure synchronous read-and-update —
            it does not re-trigger the same reactive chain — and
        (c) it is exactly what the reactive chain would have called
            once settled; forcing it here just removes the timing
            uncertainty from the snapshot.

    Two ``pilot.pause()`` calls after the force-refresh drain CSS
    transitions and focus-ring animations before the snapshot is taken.
    ``wait_for_scheduled_animations`` at the end catches any stragglers.

    The hard cap of 20 iterations in the worker loop handles nested
    second-order workers.
    """
    # Layer 1: drain all Textual run_worker workers.
    for _ in range(20):
        workers = list(pilot.app.workers._workers)  # type: ignore[attr-defined]
        if not workers:
            break
        await pilot.app.workers.wait_for_complete(workers)
        await pilot.pause()

    # Layer 2: await the ContentHostVM._setup_task directly.
    # This is the plain asyncio task that run_worker misses.
    with contextlib.suppress(Exception):
        setup_task: asyncio.Task[None] | None = pilot.app._app_ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
        if setup_task is not None and not setup_task.done():
            await setup_task

    # Layer 3: force-sync every Pane's chrome (header + footer) from
    # the live VM state.  After setup_task is done, the VM is settled;
    # calling _refresh_chrome directly bypasses the non-deterministic
    # call_after_refresh → InvokeLater → screen-callback chain.
    for pane in pilot.app.query(Pane):
        with contextlib.suppress(Exception):
            pane._refresh_chrome()  # type: ignore[attr-defined]

    # Two final ticks drain any pending animations / CSS transitions
    # (focus rings, toast entrance) before the screenshot is taken.
    await pilot.pause()
    await pilot.pause()
    await pilot.wait_for_scheduled_animations()


@pytest.mark.parametrize("theme", THEMES)
def test_demo_mode_snapshot(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(
        DemoModeApp(theme=theme),
        terminal_size=TERMINAL_SIZE,
        run_before=_drain_workers,
    )


@pytest.mark.parametrize("theme", THEMES)
def test_demo_mode_renders_chip_and_seed_data(theme: str) -> None:
    """Content-presence guard. ``DEMO MODE`` text MUST appear in the
    snapshot (BrandBanner subtitle) and at least one seeded demo
    artifact MUST be rendered (proves the demo wiring actually
    landed, not just an empty shell)."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_demo_mode"
        / f"test_demo_mode_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name}; run --snapshot-update first"
    svg = p.read_text()
    # The snapshot SVG uses HTML entity encoding (e.g., &#160; for
    # non-breaking spaces in text nodes). Decode entities and normalise
    # non-breaking spaces so plain substring checks work reliably.
    svg_plain = html_lib.unescape(svg).replace("\xa0", " ")
    # The DEMO affordance appears as either:
    # - "DEMO MODE" in the BrandBanner border_subtitle (when no overlay
    #   covers it), or
    # - "Demo mode active" in the startup advisory toast (which the app
    #   raises in on_mount when demo=True; this toast can overlap the
    #   BrandBanner bottom border in the snapshot depending on z-order).
    # Both prove the user sees a clear "you are in demo mode" signal.
    assert "DEMO MODE" in svg_plain or "Demo mode active" in svg_plain, (
        f"no DEMO affordance visible in {theme}"
    )
    assert "etl-input/" in svg_plain, f"no demo seed artifact rendered in {theme}"
    assert "2 obj" in svg_plain, f"no settled demo pane summary rendered in {theme}"
