"""Snapshot tests for the EMR page across 10 themes.

Every parity snapshot is paired with a content-presence guard so a
uniformly-blank render across all themes can't pass (per PR #53 /
#63 lesson)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.emr import (
    EmrPageApp,
    EmrPageEmptyApp,
    EmrPageOpenPickerApp,
    EmrPageOpenSourcePickerApp,
)
from tests.snapshot.conftest import THEMES

TERMINAL_SIZE = (120, 30)
RESPONSIVE_SIZES = ((100, 30), (80, 24))


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_populated_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(EmrPageApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_empty_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(EmrPageEmptyApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("terminal_size", RESPONSIVE_SIZES)
@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_populated_responsive_snapshot(
    theme: str, terminal_size: tuple[int, int], snap_compare
) -> None:
    assert snap_compare(EmrPageApp(theme=theme), terminal_size=terminal_size)


@pytest.mark.parametrize("terminal_size", RESPONSIVE_SIZES)
@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_empty_responsive_snapshot(
    theme: str, terminal_size: tuple[int, int], snap_compare
) -> None:
    assert snap_compare(EmrPageEmptyApp(theme=theme), terminal_size=terminal_size)


@pytest.mark.parametrize("terminal_size", RESPONSIVE_SIZES)
@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_open_picker_responsive_snapshot(
    theme: str, terminal_size: tuple[int, int], snap_compare
) -> None:
    app = EmrPageOpenPickerApp(theme=theme)
    assert snap_compare(
        app,
        terminal_size=terminal_size,
        run_before=app.open_picker_with_geometry_check,
    )


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_open_source_picker_narrow_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(EmrPageOpenSourcePickerApp(theme=theme), terminal_size=(80, 24))


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_open_source_picker_wide_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(EmrPageOpenSourcePickerApp(theme=theme), terminal_size=(100, 30))


# ── Content-presence guards ───────────────────────────────────────────────


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_populated_renders_expected_glyphs_and_labels(theme: str) -> None:
    """Content-presence guard for ``test_emr_page_populated_snapshot``.

    A uniformly-blank populated render would pass parity-match across all 10
    themes (per PR #53 lesson). The fixture seeds one application
    ``etl-pipeline-1``, a SUCCESS run ``nightly-2026-06-25``, and a detail
    with ``EmrJobRole`` in the execution role ARN; assert those strings
    survive the render. The fixed-width source trigger is deliberately
    ellipsized at this size, so source identity is covered by the fixture's
    live widget state instead of raw SVG text.

    The job-run NAME column is 1fr of a narrow LEFT pane, so a long
    run name like ``nightly-2026-06-25`` ellipsizes to
    ``nightly-2026-06…`` in the runs list — match the prefix (still
    uniquely from the seeded fixture). The full name still appears
    verbatim in the RIGHT detail pane."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr"
        / f"test_emr_page_populated_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert "etl-pipelin" in svg, f"application name missing for theme {theme!r}"
    # The job-run NAME column is 1fr of a narrow LEFT pane (now 2/7
    # of total width post-PR-batch-7items), so the long fixture
    # name ``nightly-2026-06-25`` ellipsizes to ``nightly-2…`` in
    # the runs list — match the smallest meaningful prefix that's
    # still uniquely the seeded run name.
    assert "nightly-2" in svg, f"job run name (prefix) missing for theme {theme!r}"
    assert "EmrJobRole" in svg, f"execution role ARN fragment missing for theme {theme!r}"
    # Pin the date+time column so a regression to time-only would
    # fail. SVG encodes the space between date and time as
    # ``&#160;``.
    assert "2026-06-25&#160;12:00" in svg, f"job run date+time missing for theme {theme!r}"


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


@pytest.mark.parametrize("terminal_index", range(len(RESPONSIVE_SIZES)))
@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_responsive_states_keep_context_visible(theme: str, terminal_index: int) -> None:
    """Guard the narrow context row and expanded application list."""
    root = Path(__file__).parent / "__snapshots__" / "test_emr"
    populated = root / (
        f"test_emr_page_populated_responsive_snapshot[{theme}-terminal_size{terminal_index}].raw"
    )
    empty = root / (
        f"test_emr_page_empty_responsive_snapshot[{theme}-terminal_size{terminal_index}].raw"
    )
    opened = root / (
        f"test_emr_page_open_picker_responsive_snapshot[{theme}-terminal_size{terminal_index}].raw"
    )
    for snapshot in (populated, empty, opened):
        assert snapshot.is_file(), f"expected snapshot {snapshot.name} on disk"
        assert len(snapshot.read_text()) > 500, f"snapshot {snapshot.name} appears blank"

    populated_svg = populated.read_text()
    assert "etl-" in populated_svg
    assert "EmrJobRole" in populated_svg

    empty_svg = empty.read_text()
    assert "no&#160;runs" in empty_svg
    assert "etl-" not in empty_svg

    opened_svg = opened.read_text()
    assert "etl-" in opened_svg
    assert "START" in opened_svg
    assert "EmrJobRole" in opened_svg


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_open_source_picker_keeps_profiles_distinguishable(theme: str) -> None:
    root = Path(__file__).parent / "__snapshots__" / "test_emr"
    for width in ("narrow", "wide"):
        snapshot = root / f"test_emr_page_open_source_picker_{width}_snapshot[{theme}].raw"
        assert snapshot.is_file(), f"expected snapshot {snapshot.name} on disk"
        svg = snapshot.read_text()
        if width == "narrow":
            assert "demo-prod-e" in svg
            assert "demo-prod-w" in svg
        else:
            assert "-east-1" in svg
            assert "demo-prod-west·us" in svg
            assert "-west-2" in svg
            assert "demo-prod·us-east" in svg
