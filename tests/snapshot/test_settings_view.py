"""Snapshot tests for SettingsView + content-presence guards x 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.settings_view import (
    SettingsViewEmptyApp,
    SettingsViewFormOpenApp,
    SettingsViewPopulatedApp,
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
TERMINAL_SIZE = (90, 40)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_empty(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsViewEmptyApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_populated(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsViewPopulatedApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_form_open(theme: str, snap_compare) -> None:  # type: ignore[no-untyped-def]
    assert snap_compare(SettingsViewFormOpenApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_empty_renders_title_and_section_header(theme: str) -> None:
    """Content-presence guard: SettingsView must render 'Settings' title
    and the 'S3-Compatible Connections' section header."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_settings_view"
        / f"test_settings_view_empty[{theme}].raw"
    )
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    # Normalise non-breaking spaces (U+00A0 / &#160;) before asserting.
    svg = p.read_text().replace("&#160;", " ")
    assert "Settings" in svg, f"title 'Settings' missing for theme {theme!r}"
    assert "S3-Compatible Connections" in svg, (
        f"Connections section header missing for theme {theme!r}"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_populated_renders_rows(theme: str) -> None:
    """Content-presence guard: populated SettingsView must show both
    seeded connection names."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_settings_view"
        / f"test_settings_view_populated[{theme}].raw"
    )
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    svg = p.read_text()
    # The seed data uses names "conn-0", "conn-1"
    assert "conn-0" in svg, f"row 'conn-0' missing for theme {theme!r}"
    assert "conn-1" in svg, f"row 'conn-1' missing for theme {theme!r}"


@pytest.mark.parametrize("theme", THEMES)
def test_settings_view_form_open_renders_input_labels(theme: str) -> None:
    """Content-presence guard: form-open SettingsView must show form
    input labels and a save button."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_settings_view"
        / f"test_settings_view_form_open[{theme}].raw"
    )
    if not p.is_file():
        pytest.skip(f"snapshot not yet generated for theme {theme!r}")
    # Normalise non-breaking spaces (U+00A0 / &#160;) before asserting.
    svg = p.read_text().replace("&#160;", " ")
    assert "Endpoint URL" in svg, f"form label 'Endpoint URL' missing for theme {theme!r}"
    assert "Access key ID" in svg, f"form label 'Access key ID' missing for theme {theme!r}"
    assert "save" in svg.lower(), f"Save button label missing for theme {theme!r}"
