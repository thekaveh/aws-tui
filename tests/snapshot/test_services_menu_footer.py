"""Snapshot tests for ServicesMenuFooter x 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.services_menu_footer import ServicesMenuFooterApp

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
TERMINAL_SIZE = (40, 6)


@pytest.mark.parametrize("theme", THEMES)
def test_services_menu_footer(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(ServicesMenuFooterApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_services_menu_footer_renders_visible_label(theme: str) -> None:
    """Belt-and-suspenders on the snapshot match: assert the rendered
    SVG actually contains the gear glyph and 'Settings' text.

    The Task 12 snapshots originally shipped with the footer rendering
    as a blank 1-row border-only band (the parent ``ServicesMenuFooter``
    had ``height: 1`` but the per-theme CSS added ``border-top`` which
    consumed the only row). All 10 themes rendered identically empty,
    so the parametric snapshot match passed — the bug only surfaced in
    the live app. This second test pins the actual button label so an
    empty rendering can never pass again.
    """
    snapshot_path = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_services_menu_footer"
        / f"test_services_menu_footer[{theme}].raw"
    )
    if not snapshot_path.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = snapshot_path.read_text()
    assert "⚙" in svg, (
        f"gear glyph missing from rendered SVG for theme {theme!r} — "
        "footer is rendering blank, regenerate snapshots and verify "
        "the button is visible in the running app"
    )
    assert "Settings" in svg, f"'Settings' label missing from rendered SVG for theme {theme!r}"
