"""Snapshot tests for the EMR page across 10 themes.

Every parity snapshot is paired with a content-presence guard so a
uniformly-blank render across all themes can't pass (per PR #53 /
#63 lesson)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.emr import EmrPageApp, EmrPageEmptyApp
from tests.snapshot.conftest import THEMES

TERMINAL_SIZE = (120, 30)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_populated_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(EmrPageApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_empty_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(EmrPageEmptyApp(theme=theme), terminal_size=TERMINAL_SIZE)


# ── Content-presence guards ───────────────────────────────────────────────


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_populated_renders_expected_glyphs_and_labels(theme: str) -> None:
    """Content-presence guard for ``test_emr_page_populated_snapshot``.

    A uniformly-blank populated render would pass parity-match across all 10
    themes (per PR #53 lesson). The fixture seeds one application
    ``etl-pipeline-1``, a SUCCESS run ``nightly-2026-06-25``, and a detail
    with ``EmrJobRole`` in the execution role ARN; assert those strings
    survive the render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr"
        / f"test_emr_page_populated_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert "etl-pipeline-1" in svg, f"application name missing for theme {theme!r}"
    assert "nightly-2026-06-25" in svg, f"job run name missing for theme {theme!r}"
    assert "EmrJobRole" in svg, f"execution role ARN fragment missing for theme {theme!r}"


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_empty_renders_no_runs(theme: str) -> None:
    """Content-presence guard for ``test_emr_page_empty_snapshot``.

    The empty-state app seeds no applications and no job runs.  Assert that
    the application-name label we seed for the populated fixture
    (``etl-pipeline-1``) does NOT appear — it would only appear if the
    wrong fixture was used — and that the snapshot file itself exists and
    has content."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr"
        / f"test_emr_page_empty_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    # The file must be non-trivial (not just an empty SVG frame).
    assert len(svg) > 500, f"snapshot for theme {theme!r} appears to be empty/blank"
    # Textual SVG output encodes spaces as &#160; (non-breaking space).
    # The empty state must show the placeholder labels for both panes.
    assert "no&#160;runs" in svg, f"empty-state placeholder missing for theme {theme!r}"
    # The seeded application name must NOT appear in the empty rendering.
    assert "etl-pipeline-1" not in svg, (
        f"populated fixture name leaked into empty snapshot for theme {theme!r}"
    )
