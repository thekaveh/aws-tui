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
    """Content-presence guard: an expanded NavMenu MUST render the
    'Settings' label text. Pure snapshot-match can pass a uniformly
    blank render across all themes; this catches that."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_nav_menu"
        / f"test_nav_menu_expanded[{theme}].raw"
    )
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    assert "Settings" in svg, (
        f"'Settings' label missing from expanded NavMenu SVG for theme {theme!r}"
    )
    assert "menu" in svg, f"'menu' header missing for theme {theme!r}"


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
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    assert "⚙" in svg, f"gear glyph missing from collapsed NavMenu SVG for theme {theme!r}"
