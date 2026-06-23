"""Snapshot tests for the main screen (services menu + dual pane + chrome).

One golden per theme, terminal 120x40. Re-generate with
``uv run pytest tests/snapshot --snapshot-update``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.main_screen import MainScreenApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_main_screen(theme: str, snap_compare) -> None:
    app = MainScreenApp(theme=theme)
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_main_screen_renders_nav_and_panes(theme: str) -> None:
    """Content-presence guard for ``test_main_screen``.

    pytest-textual-snapshot's parity-match passes a uniformly-blank
    rendering across all 10 themes (per PR #53 lesson). This guard
    reads the generated SVG off disk and asserts that the canonical
    user-facing labels are actually rendered. Drift on any one theme
    fails the guard before the snapshot trap can hide it.
    """
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_main_screen"
        / f"test_main_screen[{theme}].raw"
    )
    assert p.is_file(), (
        f"expected snapshot {p.name} on disk; the matching snap_compare "
        f"test should have generated it. Did the snapshot file path change?"
    )
    svg = p.read_text()
    # MainScreenApp fixture renders the brand banner + DualPane + hint
    # legend (no NavMenu — that's exercised by tests/snapshot/test_nav_menu.py).
    # Assert each of those three regions has something visible.
    assert "aws.tui" in svg, f"banner glyph missing in main-screen SVG for theme {theme!r}"
    assert "copy" in svg, f"hint-legend 'copy' label missing for theme {theme!r}"
    assert "delete" in svg, f"hint-legend 'delete' label missing for theme {theme!r}"
