"""Per-theme snapshot of demo-mode boot + content-presence guard."""

from __future__ import annotations

import html as html_lib
from pathlib import Path

import pytest

from tests.snapshot.apps.demo_mode import DemoModeApp
from tests.snapshot.conftest import THEMES

TERMINAL_SIZE = (120, 30)


async def _drain_workers(pilot) -> None:  # type: ignore[no-untyped-def]
    """Wait for all async boot workers to complete before snapping.

    Without draining, the snapshot can capture the pane in a transient
    "loading…" state (non-deterministic render). The integration tests
    use this same pattern (see ``tests/integration/test_demo_mode.py``).
    Five drain+pause cycles cover the initial mount worker, the S3
    file-listing worker, any queued post-mount refresh, and scheduled
    animations; ``wait_for_scheduled_animations`` ensures CSS
    transitions (e.g. light-theme focus rings) have settled before
    the screenshot is taken.
    """
    for _ in range(5):
        await pilot.app.workers.wait_for_complete(
            list(pilot.app.workers._workers)  # type: ignore[attr-defined]
        )
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
    # Either a bucket name or the demo connection name proves seeding.
    assert "demo-dev" in svg_plain or "etl-input" in svg_plain, (
        f"no demo seed artifact rendered in {theme}"
    )
