"""Structural, contrast, and parser checks for the built-in themes.

Snapshot tests under ``tests/snapshot`` provide rendering-level coverage.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

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
def test_builtin_theme_does_not_retain_unmounted_status_bar_styles(name: str) -> None:
    content = ThemeStore().load(name)
    assert re.search(r"status[\s_-]*bar", content, re.IGNORECASE) is None, (
        f"theme {name} retains dead StatusBar styles"
    )


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


def _bodies_for_selector(content: str, selector: str) -> tuple[str, ...]:
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    return tuple(
        body
        for selector_list, body in re.findall(r"([^{}]+)\{([^}]*)\}", content)
        if selector in (candidate.strip() for candidate in selector_list.split(","))
    )


def _raw_builtin_theme(name: str) -> str:
    return resources.files("aws_tui.ui.themes").joinpath(f"{name}.tcss").read_text(encoding="utf-8")


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
def test_muted_text_is_readable_on_both_content_backgrounds(name: str) -> None:
    tokens = _theme_tokens(ThemeStore().load(name))

    for background_token in ("$bg", "$bg-elev"):
        ratio = _contrast_ratio(tokens["$text-muted"], tokens[background_token])
        assert ratio >= 4.5, (
            f"theme {name}: $text-muted on {background_token} contrast is {ratio:.2f}:1"
        )


@pytest.mark.parametrize("name", ALL_THEMES)
def test_brand_banner_titles_use_readable_text_token(name: str) -> None:
    bodies = _bodies_for_selector(ThemeStore().load(name), "BrandBanner")

    assert any("border-title-color: $text;" in body for body in bodies)
    assert any("border-subtitle-color: $text;" in body for body in bodies)


def test_docs_accent_meets_light_and_dark_theme_contrast() -> None:
    css = (Path(__file__).parents[3] / "docs/stylesheets/extra.css").read_text(encoding="utf-8")
    root = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    slate = re.search(r'\[data-md-color-scheme="slate"\]\s*\{([^}]*)\}', css, re.DOTALL)
    assert root is not None
    assert slate is not None

    light_accent = re.search(r"--md-accent-fg-color:\s*(#[0-9a-fA-F]{6})", root.group(1))
    dark_accent = re.search(r"--md-accent-fg-color:\s*(#[0-9a-fA-F]{6})", slate.group(1))
    assert light_accent is not None
    assert dark_accent is not None
    assert _contrast_ratio(light_accent.group(1), "#ffffff") >= 4.5
    assert _contrast_ratio(dark_accent.group(1), "#0b0f14") >= 4.5


@pytest.mark.parametrize("name", ALL_THEMES)
@pytest.mark.parametrize("token", ["$accent", "$success", "$warning", "$danger"])
def test_notification_tokens_have_readable_contrast(name: str, token: str) -> None:
    tokens = _theme_tokens(ThemeStore().load(name))
    ratio = _contrast_ratio(tokens[token], tokens["$bg-elev"])

    assert ratio >= 4.5, f"theme {name}: {token} on $bg-elev contrast is {ratio:.2f}:1"


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


def test_service_tab_strip_structure_is_shared_theme_owned() -> None:
    shared = (
        resources.files("aws_tui.ui.themes")
        .joinpath("operational-panes.tcss")
        .read_text(encoding="utf-8")
    )
    expected = {
        "ServiceTabStrip": (
            "background: $bg;",
            "color: $text-muted;",
            "border: solid $rule-dim;",
        ),
        "ServiceTabStrip > .service-tab": ("color: $text-muted;",),
        "ServiceTabStrip > .service-tab.-divided": ("border-left: solid $rule-dim;",),
        "ServiceTabStrip > .service-tab.-active": (
            "color: $accent;",
            "text-style: bold;",
        ),
        "ServiceTabStrip:focus > .service-tab.-active": (
            "background: $bg-sel;",
            "color: $text;",
        ),
    }

    for selector, declarations in expected.items():
        bodies = _bodies_for_selector(shared, selector)
        assert bodies, f"shared stylesheet missing {selector}"
        assert any(all(declaration in body for declaration in declarations) for body in bodies)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_source_header_edge_is_scoped_to_emr(name: str) -> None:
    content = _raw_builtin_theme(name)

    assert not _bodies_for_selector(content, "ServiceSourceHeader")
    bodies = _bodies_for_selector(content, "EmrServerlessPage ServiceSourceHeader")
    assert bodies
    assert any("border-left: solid $rule-dim;" in body for body in bodies)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_focused_service_tab_uses_contrast_safe_tokens(name: str) -> None:
    content = ThemeStore().load(name)
    tokens = _theme_tokens(content)
    bodies = _bodies_for_selector(
        content,
        "ServiceTabStrip:focus > .service-tab.-active",
    )

    assert bodies
    assert any("background: $bg-sel;" in body and "color: $text;" in body for body in bodies)
    ratio = _contrast_ratio(tokens["$text"], tokens["$bg-sel"])
    assert ratio >= 4.5, f"theme {name}: focused service tab contrast is {ratio:.2f}:1"


@pytest.mark.parametrize("name", ALL_THEMES)
def test_builtin_themes_do_not_retain_legacy_service_tab_selectors(name: str) -> None:
    content = _raw_builtin_theme(name)

    for selector in (
        "GluePage > #glue-view-tabs",
        "GluePage .glue-view-tab",
        "AthenaPage > #athena-view-tabs",
        "AthenaPage .athena-view-tab",
    ):
        assert selector not in content


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
def test_glue_pane_titles_use_readable_theme_tokens(name: str) -> None:
    content = ThemeStore().load(name)

    inactive = re.search(
        r"GluePage\s+ResourceListPane,\s*"
        r"GluePage\s+DetailRows\s*\{([^}]*)\}",
        content,
        re.MULTILINE,
    )
    focused = re.search(
        r"GluePage\s+ResourceListPane:focus-within,\s*"
        r"GluePage\s+DetailRows:focus-within\s*\{([^}]*)\}",
        content,
        re.MULTILINE,
    )

    assert inactive is not None
    assert "border-title-color: $text;" in inactive.group(1)
    assert focused is not None
    assert "border-title-color: $accent;" in focused.group(1)


def test_operational_pane_structure_is_shared_theme_owned() -> None:
    shared = (
        resources.files("aws_tui.ui.themes")
        .joinpath("operational-panes.tcss")
        .read_text(encoding="utf-8")
    )

    for content, selector in (
        (shared, "GluePage GlueIcebergView"),
        (shared, "AthenaPage > #athena-context-header"),
        (shared, "AthenaPage TextArea"),
        (shared, "AthenaPage #athena-query-controls"),
        (shared, "AthenaPage #athena-query-detail"),
        (shared, "AthenaPage #athena-results-summary"),
        (shared, "AthenaPage DataTable"),
    ):
        bodies = _bodies_for_selector(content, selector)
        assert bodies, f"shared stylesheet missing {selector}"
        assert any("border: solid $rule-dim;" in body for body in bodies)

    for content, selector in (
        (shared, "GluePage GlueIcebergView:focus-within"),
        (shared, "AthenaPage > #athena-context-header:focus-within"),
        (shared, "AthenaPage TextArea:focus"),
        (
            shared,
            "AthenaPage #athena-query-controls:focus-within",
        ),
        (
            shared,
            "AthenaPage #athena-query-detail:focus-within",
        ),
        (
            shared,
            "AthenaPage #athena-results-summary:focus-within",
        ),
        (shared, "AthenaPage DataTable:focus"),
    ):
        bodies = _bodies_for_selector(content, selector)
        assert bodies, f"shared stylesheet missing {selector}"
        assert any("border: solid $accent;" in body for body in bodies)


def test_glue_context_layout_has_no_theme_owned_frame() -> None:
    shared = (
        resources.files("aws_tui.ui.themes")
        .joinpath("operational-panes.tcss")
        .read_text(encoding="utf-8")
    )

    assert not _bodies_for_selector(shared, "GluePage > #glue-context-pane")
    assert not _bodies_for_selector(shared, "GluePage > #glue-context-row")
    assert _bodies_for_selector(shared, "AthenaPage > #athena-context-header")
    assert _bodies_for_selector(shared, "AthenaPage > #athena-context-header:focus-within")


@pytest.mark.parametrize("name", ALL_THEMES)
def test_builtin_themes_do_not_duplicate_operational_structure(name: str) -> None:
    content = _raw_builtin_theme(name)
    assert "Glue / Athena operational pane hierarchy" not in content

    for selector in (
        "GluePage GlueIcebergView",
        "AthenaPage > #athena-context-header",
        "AthenaPage TextArea",
        "AthenaPage #athena-query-controls",
        "AthenaPage #athena-query-detail",
        "AthenaPage #athena-results-summary",
        "AthenaPage DataTable",
    ):
        assert all(
            "border: solid $rule-dim;" not in body
            for body in _bodies_for_selector(content, selector)
        )

    for selector in (
        "GluePage GlueIcebergView:focus-within",
        "AthenaPage > #athena-context-header:focus-within",
        "AthenaPage TextArea:focus",
        "AthenaPage DataTable:focus",
        "AthenaPage #athena-query-controls:focus-within",
        "AthenaPage #athena-query-detail:focus-within",
        "AthenaPage #athena-results-summary:focus-within",
    ):
        assert all(
            "border: solid $accent;" not in body for body in _bodies_for_selector(content, selector)
        )


@pytest.mark.parametrize("name", ALL_THEMES)
def test_glue_list_placeholders_use_semantic_theme_tokens(name: str) -> None:
    content = ThemeStore().load(name)

    warning = re.search(
        r"GluePage\s+OptionList\.-warning\s*>\s*"
        r"\.option-list--option-disabled\s*\{([^}]*)\}",
        content,
        re.MULTILINE,
    )
    error = re.search(
        r"GluePage\s+OptionList\.-error\s*>\s*"
        r"\.option-list--option-disabled\s*\{([^}]*)\}",
        content,
        re.MULTILINE,
    )

    assert warning is not None
    assert "color: $warning;" in warning.group(1)
    assert error is not None
    assert "color: $danger;" in error.group(1)
