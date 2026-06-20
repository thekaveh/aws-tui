"""Snapshot tests for ServicesMenuFooter x 10 themes."""

from __future__ import annotations

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
