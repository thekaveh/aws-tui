"""Snapshot tests for NavMenu + content-presence guards x 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.nav_menu import NavMenuCollapsedApp, NavMenuExpandedApp

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
def test_nav_menu_expanded(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(NavMenuExpandedApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_collapsed(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(NavMenuCollapsedApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_expanded_renders_visible_settings_label(theme: str) -> None:
    """Content-presence guard: an expanded NavMenu MUST render both
    the 'S3' service item and the 'Settings' label, with Settings
    visually below S3 (docked at the bottom).

    Pure snapshot-match can pass a uniformly blank render across all
    themes; this catches that. The S3-below-Settings ordering check
    catches a regression where the dock:bottom rule on #menu-pinned
    gets dropped or overridden."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_nav_menu"
        / f"test_nav_menu_expanded[{theme}].raw"
    )
    # FAIL (don't skip) when the snapshot is absent. The snapshot
    # test in the same suite always produces it; absence means the
    # guard has silently no-op'd — exactly the failure mode this
    # guard was added to prevent.
    assert p.is_file(), (
        f"expected snapshot {p.name} on disk; the matching snap_compare "
        f"test should have generated it. Did the snapshot file path or "
        f"name change?"
    )
    svg = p.read_text()
    assert "Settings" in svg, (
        f"'Settings' label missing from expanded NavMenu SVG for theme {theme!r}"
    )
    assert "S3" in svg, f"'S3' service label missing for theme {theme!r}"
    # SVG text elements appear in document order — top-to-bottom. So
    # the first occurrence of "Settings" must come AFTER the first
    # occurrence of "S3" iff Settings is docked at the bottom.
    assert svg.index("S3") < svg.index("Settings"), (
        f"Settings should appear below S3 in the rendered NavMenu "
        f"(docked-bottom layout) but came first in SVG for theme {theme!r}"
    )
    # The inline hamburger glyph at the top must be visible — that's the
    # always-visible affordance for collapse/expand. Either '+' (when
    # collapsed) or '-' (when expanded) is present; for this expanded
    # snapshot we expect '-'.
    assert "-" in svg, f"hamburger glyph missing from expanded NavMenu SVG for theme {theme!r}"


@pytest.mark.parametrize("theme", THEMES)
def test_nav_menu_collapsed_renders_visible_settings_icon(theme: str) -> None:
    """Content-presence guard: a collapsed NavMenu MUST render the
    gear glyph for Settings."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_nav_menu"
        / f"test_nav_menu_collapsed[{theme}].raw"
    )
    assert p.is_file(), (
        f"expected snapshot {p.name} on disk; the matching snap_compare "
        f"test should have generated it. Did the snapshot file path or "
        f"name change?"
    )
    svg = p.read_text()
    assert "⚙" in svg, f"gear glyph missing from collapsed NavMenu SVG for theme {theme!r}"
    # The hamburger glyph at the top must be '+' in the collapsed state
    # (signalling: click to expand).
    assert "+" in svg, f"hamburger '+' missing from collapsed NavMenu SVG for theme {theme!r}"
