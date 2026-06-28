"""Snapshot tests for NavMenu + content-presence guards x 10 themes.

Post-PR-#94 the rail has ONE mode (no collapse / expand, no
hamburger, no icons) and shows text labels — so the snapshot tier
has collapsed from two variants (collapsed + expanded) down to a
single canonical render.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.nav_menu import NavMenuApp

THEMES = [
    "carbon",
    "voidline",
    "lattice",
    "amber",
    "solarized-light",
    "github-light",
    "one-light",
    "nord",
    "dracula",
    "gruvbox-dark",
]
TERMINAL_SIZE = (40, 20)


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(NavMenuApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_renders_labels_and_selection_ribbon(theme: str) -> None:
    """Content-presence guard. A NavMenu render MUST surface:

    - the ``S3`` service label (top, in #menu-services);
    - the gear glyph ``⚙`` for the Settings row (bottom, docked in
      #menu-settings-rows). Post-PR-#98 the Settings row renders the
      descriptor's icon instead of the ``Settings`` word so the rail
      can stay narrow (10 cells fits the longest ``EMR`` label);
    - the ``▌`` ribbon glyph marking the currently-selected row
      (S3 in the fixture).

    Pure snapshot parity-match can pass a uniformly-blank render
    across all themes; this guard catches that. ``S3`` appearing
    BEFORE the gear glyph in document order pins the docked-bottom
    layout (Settings would otherwise float up if the layout broke).
    """
    p = Path(__file__).parent / "__snapshots__" / "test_nav_menu" / f"test_nav_menu[{theme}].raw"
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    # The Settings row renders ⚙ (U+2699 GEAR, possibly with the
    # U+FE0F variation selector for colour-emoji presentation). Match
    # the base codepoint — the optional VS-16 doesn't affect the
    # codepoint count in SVG text.
    assert "⚙" in svg, f"gear glyph ⚙ for Settings missing for theme {theme!r}"
    assert "S3" in svg, f"'S3' label missing for theme {theme!r}"
    assert svg.index("S3") < svg.index("⚙"), (
        f"Settings (gear) should be docked BELOW S3 in the rendered NavMenu for theme {theme!r}"
    )
    assert "▌" in svg, f"selected-service ribbon ▌ missing for theme {theme!r}"
    assert svg.index("▌") < svg.index("S3"), (
        f"ribbon should appear before the S3 label for theme {theme!r}"
    )
