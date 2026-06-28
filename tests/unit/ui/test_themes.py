"""Smoke tests for the four built-in themes.

We don't assert content; we just ensure each ``.tcss`` parses without errors
through Textual's CSS parser. Snapshot tests (under ``tests/snapshot``)
provide the rendering-level coverage.
"""

from __future__ import annotations

import re

import pytest
from textual.css.parse import parse

from aws_tui.infra.theme_store import ThemeStore

THEMES = ("carbon", "voidline", "lattice", "amber")

# All 10 shipped themes — used by the cross-theme regression
# guards below (the four-theme list above predates the full set
# and is kept for the original smoke tests).
ALL_THEMES = (
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
)


@pytest.mark.parametrize("name", THEMES)
def test_builtin_theme_parses(name: str) -> None:
    """Each built-in theme is a valid Textual ``.tcss`` document."""
    store = ThemeStore()
    content = store.load(name)
    assert content, f"theme {name} loaded empty"
    rules = list(parse("", content, (f"test:{name}", f"test:{name}")))
    # Carbon's structure has ~60 rules; the others mirror it.
    assert len(rules) > 30


@pytest.mark.parametrize("name", THEMES)
def test_builtin_theme_defines_core_tokens(name: str) -> None:
    """Each theme defines the palette tokens referenced by every widget."""
    content = ThemeStore().load(name)
    for token in (
        "$bg:",
        "$text:",
        "$accent:",
        "$success:",
        "$danger:",
    ):
        assert token in content, f"theme {name} missing token {token}"


@pytest.mark.parametrize("name", THEMES)
def test_builtin_theme_styles_widgets(name: str) -> None:
    """Every theme references the common widget class names."""
    content = ThemeStore().load(name)
    for widget in (
        "Screen",
        "StatusBar",
        "Pane",
        "HintLegend",
        "CommandPalette",
        "ConfirmModal",
        "QuickLook",
        "ToastStack",
        "Toast",
        "BrandBanner",
        "TransfersOverlay",
    ):
        assert widget in content, f"theme {name} missing widget {widget}"


@pytest.mark.parametrize("name", ALL_THEMES)
def test_settings_navrow_has_no_specificity_clobber_on_selected_bg(name: str) -> None:
    """Regression: the Settings NavRow MUST be allowed to inherit the
    ``NavRow.-selected { background: $bg-sel; ... }`` highlight.

    Pre-PR-#105, every theme shipped a higher-specificity rule

        ``NavMenu > #menu-settings-rows > NavRow { background: transparent; }``

    that clobbered the ``-selected`` background on the Settings row
    (the user reported: "the gear icon representing the settings in
    the menu doesn't have the same selected item styling applied to
    it as the rest of the menu items: its background is the same as
    any unselected item"). The override was redundant with the base
    ``NavRow { background: transparent; }`` rule.

    This guard fails if anyone re-adds the offending selector with a
    ``background:`` declaration that would block the
    ``NavRow.-selected`` background. Other declarations on the same
    selector are fine (it stays available for future Settings-row-
    specific styling that DOESN'T touch background).
    """
    content = ThemeStore().load(name)
    # Find any block whose selector targets the Settings NavRow
    # directly. Be permissive on whitespace / quoting around `>`.
    pattern = re.compile(
        r"NavMenu\s*>\s*#menu-settings-rows\s*>\s*NavRow\s*\{([^}]*)\}",
        re.MULTILINE,
    )
    for body in pattern.findall(content):
        assert "background" not in body, (
            f"theme {name}: `NavMenu > #menu-settings-rows > NavRow` "
            "block declares a `background` — this selector has higher "
            "specificity than `NavRow.-selected` and will clobber the "
            "Settings row's selected-state highlight."
        )
