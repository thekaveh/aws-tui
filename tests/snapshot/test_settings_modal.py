"""Snapshot tests for SettingsModal x 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.settings import (
    SettingsModalEmptyApp,
    SettingsModalPopulatedApp,
)

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
TERMINAL_SIZE = (120, 40)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_modal_empty(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsModalEmptyApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_modal_populated(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsModalPopulatedApp(theme=theme), terminal_size=TERMINAL_SIZE)
