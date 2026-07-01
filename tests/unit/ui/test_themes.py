"""Smoke tests for the built-in themes.

We don't assert content; we just ensure each ``.tcss`` parses without errors
through Textual's CSS parser. Snapshot tests (under ``tests/snapshot``)
provide the rendering-level coverage.
"""

from __future__ import annotations

import re

import pytest
from textual.css.parse import parse

from aws_tui.infra.theme_store import ThemeStore

ALL_THEMES = tuple(ThemeStore.BUILTIN_NAMES)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_builtin_theme_parses(name: str) -> None:
    """Each built-in theme is a valid Textual ``.tcss`` document."""
    store = ThemeStore()
    content = store.load(name)
    assert content, f"theme {name} loaded empty"
    rules = list(parse("", content, (f"test:{name}", f"test:{name}")))
    # Carbon's structure has ~60 rules; the others mirror it.
    assert len(rules) > 30


@pytest.mark.parametrize("name", ALL_THEMES)
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


@pytest.mark.parametrize("name", ALL_THEMES)
def test_builtin_theme_styles_widgets(name: str) -> None:
    """Every theme references the common production widget class names."""
    content = ThemeStore().load(name)
    for widget in (
        "Screen",
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
def test_builtin_theme_retains_status_bar_compatibility_styles(name: str) -> None:
    """The legacy StatusBar widget is not production chrome, but is retained."""
    content = ThemeStore().load(name)
    assert "StatusBar" in content, f"theme {name} missing retained StatusBar styles"


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


def _theme_tokens(content: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for line in content.splitlines():
        match = re.match(r"\s*(\$[\w-]+):\s*(#[0-9a-fA-F]{6});", line)
        if match:
            tokens[match.group(1)] = match.group(2)
    return tokens


def _relative_luminance(hex_color: str) -> float:
    raw = hex_color.removeprefix("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(value: float) -> float:
        if value <= 0.03928:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = [linear(value) for value in channels]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(foreground: str, background: str) -> float:
    fg = _relative_luminance(foreground)
    bg = _relative_luminance(background)
    lighter, darker = max(fg, bg), min(fg, bg)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_selected_state_tokens_have_readable_contrast(name: str) -> None:
    content = ThemeStore().load(name)
    tokens = _theme_tokens(content)

    ratio = _contrast_ratio(tokens["$text"], tokens["$bg-sel"])

    assert ratio >= 4.5, f"theme {name}: $text on $bg-sel contrast is {ratio:.2f}:1"


@pytest.mark.parametrize("name", ALL_THEMES)
def test_selected_state_background_is_perceptible(name: str) -> None:
    content = ThemeStore().load(name)
    tokens = _theme_tokens(content)

    ratio = _contrast_ratio(tokens["$bg"], tokens["$bg-sel"])

    assert ratio >= 1.25, f"theme {name}: $bg-sel vs $bg contrast is {ratio:.2f}:1"


@pytest.mark.parametrize("name", ALL_THEMES)
def test_selected_state_blocks_use_readable_text_token(name: str) -> None:
    content = ThemeStore().load(name)
    selected_bg_blocks = re.findall(r"[^{}]*(?:-selected|-active)[^{]*\{([^}]*)\}", content)
    offenders = [
        body.strip()
        for body in selected_bg_blocks
        if "background: $bg-sel" in body and "color: $accent-soft" in body
    ]

    assert not offenders, (
        f"theme {name}: selected/active blocks use low-contrast "
        f"$accent-soft on $bg-sel: {offenders!r}"
    )


@pytest.mark.parametrize("name", ALL_THEMES)
def test_command_palette_selectors_match_nested_widget_tree(name: str) -> None:
    content = ThemeStore().load(name)

    assert "CommandPalette > .palette-list" not in content
    assert "CommandPalette > .palette-prompt" not in content
    assert "CommandPalette > Input" not in content
    assert ".palette-category" not in content
    assert "CommandPalette .palette-list > .palette-item.-selected" in content


@pytest.mark.parametrize("name", ALL_THEMES)
def test_emr_logs_placeholder_selectors_match_nested_widget_tree(name: str) -> None:
    content = ThemeStore().load(name)

    assert "JobRunLogsPane > .logs-placeholder" not in content
    assert "JobRunLogsPane .logs-placeholder" in content


@pytest.mark.parametrize("name", ALL_THEMES)
@pytest.mark.parametrize(
    ("selector", "minimum"),
    [
        ("status-conn", 4.5),
        ("status-region", 4.5),
        ("status-auth-ok", 4.5),
        ("status-auth-warn", 4.5),
        ("status-auth-err", 4.5),
        ("status-transfers", 4.5),
    ],
)
def test_status_bar_text_tokens_have_readable_contrast(
    name: str,
    selector: str,
    minimum: float,
) -> None:
    content = ThemeStore().load(name)
    tokens = _theme_tokens(content)
    match = re.search(
        rf"StatusBar\s*>\s*\.{selector}\s*\{{[^}}]*color:\s*(\$[\w-]+);",
        content,
        re.MULTILINE,
    )
    assert match is not None, f"theme {name}: missing StatusBar .{selector} color rule"

    foreground = tokens[match.group(1)]
    background = tokens["$bg-elev"]
    ratio = _contrast_ratio(foreground, background)

    assert ratio >= minimum, (
        f"theme {name}: .{selector} contrast is {ratio:.2f}:1 for {foreground} on {background}"
    )
