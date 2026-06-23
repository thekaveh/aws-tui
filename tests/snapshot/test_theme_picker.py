"""Snapshot tests for ThemePickerModal across all 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.theme_picker import ThemePickerSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_theme_picker(theme: str, snap_compare) -> None:
    assert snap_compare(ThemePickerSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_theme_picker_renders_theme_names(theme: str) -> None:
    """Content-presence guard for ``test_theme_picker``.

    Pure snapshot-match can pass a uniformly blank render across all
    themes (per PR #53 lesson). The picker must always list the
    canonical theme names; this guard reads the SVG off disk and
    asserts a sample of them is present.
    """
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_theme_picker"
        / f"test_theme_picker[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; did the snapshot file path change?"
    svg = p.read_text()
    assert "carbon" in svg, f"'carbon' theme name missing for theme {theme!r}"
    assert "voidline" in svg, f"'voidline' theme name missing for theme {theme!r}"
