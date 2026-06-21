"""Smoke tests for the four built-in themes.

We don't assert content; we just ensure each ``.tcss`` parses without errors
through Textual's CSS parser. Snapshot tests (under ``tests/snapshot``)
provide the rendering-level coverage.
"""

from __future__ import annotations

import pytest
from textual.css.parse import parse

from aws_tui.infra.theme_store import ThemeStore

THEMES = ("carbon", "voidline", "lattice", "amber")


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
