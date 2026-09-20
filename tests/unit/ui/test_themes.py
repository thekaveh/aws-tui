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
def test_builtin_theme_defines_zebra_token(name: str) -> None:
    """Every theme defines ``$bg-alt``, the zebra stripe surface.

    Token parity across the ten themes was asserted only in prose
    (``docs/theming.md`` §4) until this test; a theme that forgot the token
    would not fail to parse — Textual leaves the unresolved ``$bg-alt``
    reference in ``Pane .entry-row.-alt`` as an error on that one rule and
    the listing simply renders unstriped in that theme alone.

    The assertion goes through ``_theme_tokens`` rather than a substring
    check on purpose: that parser accepts ONLY a lowercase-or-uppercase
    6-digit hex literal terminated by a semicolon on its own line, so a
    3-digit value, a missing semicolon or a ``$bg-alt: $bg-elev;`` alias
    fails here instead of silently passing a ``"$bg-alt:" in content``
    check. The lowercase spelling is pinned separately below.
    """
    tokens = _theme_tokens(ThemeStore().load(name))

    assert "$bg-alt" in tokens, f"theme {name} missing token $bg-alt"
    value = tokens["$bg-alt"]
    assert value == value.lower(), f"theme {name}: $bg-alt must be lowercase hex, got {value}"
    # A stripe equal to the flat background is an invisible no-op; a stripe
    # equal to the cursor bar makes every other row look like the cursor.
    assert value != tokens["$bg"], f"theme {name}: $bg-alt duplicates $bg — no visible stripe"
    assert value != tokens["$bg-sel"], f"theme {name}: $bg-alt duplicates $bg-sel"


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


@pytest.mark.parametrize("name", ALL_THEMES)
def test_zebra_rule_precedes_the_selected_rule(name: str) -> None:
    """Source order is the ONLY thing keeping the cursor row off the stripe.

    ``Pane .entry-row.-alt`` and ``Pane .entry-row.-selected`` score the
    identical specificity ``(0, 2, 1)`` — a type selector plus two class
    components either way. ``Stylesheet.apply`` walks ``reversed(self.rules)``
    and resolves each property with ``max(..., key=itemgetter(0))``; Python's
    ``max`` returns the FIRST maximal element, and reversal means that is the
    rule declared LATER in the source. So on this exact tie the later rule
    wins, and moving the zebra block below ``.-selected`` would paint every
    odd cursor row with the stripe instead of the cursor bar — with no parse
    error, no warning, and nothing but a snapshot diff to show for it.

    This mirrors ``test_settings_navrow_has_no_specificity_clobber_on_selected_bg``:
    a structural guard on a CSS relationship that cannot defend itself.
    Comments are stripped first so a prose mention of either selector cannot
    satisfy the ordering.
    """
    content = re.sub(r"/\*.*?\*/", "", ThemeStore().load(name), flags=re.DOTALL)

    assert "Pane .entry-row.-alt" in content, f"theme {name} has no zebra rule"
    assert content.index("Pane .entry-row.-alt") < content.index("Pane .entry-row.-selected"), (
        f"theme {name}: the zebra rule is declared AFTER `Pane .entry-row.-selected`. "
        "Both selectors score (0, 2, 1), so the later rule wins the tie and the "
        "cursor row will render as a stripe."
    )
    # Constraint 12: the rule stays scoped to Pane. `NavRow` merges the
    # literal `entry-row` class (ui/widgets/nav_row.py) and is mounted with
    # no `Pane` ancestor, so a bare `.entry-row.-alt` would stripe the
    # services rail as well as the listing.
    assert re.search(r"^\s*\.entry-row\.-alt\b", content, re.MULTILINE) is None, (
        f"theme {name}: unscoped `.entry-row.-alt` selector would stripe the NavMenu rail"
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


def _cielab_lightness(hex_color: str) -> float:
    """CIELAB ``L*`` of an sRGB hex colour under the D65 white point.

    Written out here rather than pulled in from a colour-science library:
    the suite has no such dependency and ``L*`` is a dozen lines. It is a
    function of the CIE ``Y`` tristimulus alone, so only the ``Y`` row of
    the sRGB-to-XYZ matrix is needed; that row is normalised so ``Y == 1``
    for ``#ffffff``, which puts ``L*`` at exactly 0 for black and 100 for
    white.

    ``L*`` and not the WCAG relative luminance above, because the question
    the zebra stripe raises is "can a person see the difference between two
    large flat blocks of near-identical colour" -- a perceptual-uniformity
    question. The WCAG ratio answers a different one (is text legible on
    this background) and compresses to uselessness in the near-black region
    where six of the ten themes live: carbon's stripe moved from a 1.03:1
    to a 1.10:1 background ratio in the fix below -- indistinguishable from
    noise -- while its ``L*`` delta went 1.44 -> 4.80.
    """
    raw = hex_color.removeprefix("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(value: float) -> float:
        if value <= 0.04045:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = [linear(value) for value in channels]
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    epsilon = 216 / 24389
    kappa = 24389 / 27
    f_y = y ** (1 / 3) if y > epsilon else (kappa * y + 16) / 116
    return 116 * f_y - 16


def _lightness_delta(first: str, second: str) -> float:
    return abs(_cielab_lightness(first) - _cielab_lightness(second))


def test_cielab_lightness_conversion_matches_reference_values() -> None:
    """Pin the hand-rolled conversion the zebra guard below depends on.

    A silently wrong ``L*`` would make that guard assert nothing useful,
    so anchor it on values that do not depend on this implementation:
    ``L*`` is 0 at black and 100 at white by definition, and mid-grey
    ``#777777`` is the textbook "L* 50 sits near 18% reflectance, not at
    50% of the 8-bit range" example.
    """
    assert _cielab_lightness("#000000") == pytest.approx(0.0)
    assert _cielab_lightness("#ffffff") == pytest.approx(100.0)
    assert _cielab_lightness("#777777") == pytest.approx(50.03, abs=0.01)
    assert _cielab_lightness("#808080") == pytest.approx(53.59, abs=0.01)
    assert _lightness_delta("#000000", "#ffffff") == pytest.approx(100.0)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_selected_state_tokens_have_readable_contrast(name: str) -> None:
    content = ThemeStore().load(name)
    tokens = _theme_tokens(content)

    ratio = _contrast_ratio(tokens["$text"], tokens["$bg-sel"])

    assert ratio >= 4.5, f"theme {name}: $text on $bg-sel contrast is {ratio:.2f}:1"


@pytest.mark.parametrize("name", ALL_THEMES)
def test_muted_text_is_readable_on_both_content_backgrounds(name: str) -> None:
    """``$bg-alt`` joined the pair when zebra striping landed.

    The stripe is a third surface that body text sits on — half of every
    listing renders on it — so it is held to the same 4.5:1 floor as the
    flat background and the elevated chrome.
    """
    tokens = _theme_tokens(ThemeStore().load(name))

    for background_token in ("$bg", "$bg-alt", "$bg-elev"):
        ratio = _contrast_ratio(tokens["$text-muted"], tokens[background_token])
        assert ratio >= 4.5, (
            f"theme {name}: $text-muted on {background_token} contrast is {ratio:.2f}:1"
        )


ZEBRA_MIN_LIGHTNESS_DELTA = 3.5
ZEBRA_MAX_SHARE_OF_SELECTION = 0.55


@pytest.mark.parametrize("name", ALL_THEMES)
def test_zebra_stripe_is_visible_but_subordinate(name: str) -> None:
    """The stripe has to be SEEN, and still lose to the cursor row.

    The zebra striping shipped in #227 derived every ``$bg-alt`` as the
    midpoint between that theme's ``$bg`` and ``$bg-elev``. That construction
    guarantees the second half -- a midpoint can never out-shout ``$bg-sel``
    -- and says nothing at all about the first. It shipped, and the user
    rejected it: "the zebra pattern is way too subtle. I tried all supported
    themes and the alternate line background are just barely visible."

    They were right, and every test in this module passed anyway. Measured
    in CIELAB the ten stripes came out at ``L*`` deltas of 1.18 to 3.92
    (mean 2.22); a delta of ~1 is at or under the just-noticeable threshold
    for two large flat adjacent areas, so four themes were effectively
    invisible and none was comfortable. The user's own reference rendering
    measures 3.40, which is why the floor below sits just above it at 3.5
    rather than at some rounder number: it is calibrated against a stripe a
    human actually called visible.

    The ceiling is the other half of the constraint, and the half the old
    midpoint rule got right by accident. ``Pane .entry-row.-alt`` and
    ``Pane .entry-row.-selected`` are adjacent surfaces in the same listing;
    a stripe that approaches the cursor bar destroys the cursor as a cue.
    Holding it to 55% of the theme's own selection delta keeps the ordering
    unambiguous in every palette without hard-coding a lightness per theme,
    which would just re-encode the values this test exists to police.

    Both bounds are relative to the theme's own tokens, so a future palette
    change retunes the assertion instead of breaking it.
    """
    tokens = _theme_tokens(ThemeStore().load(name))

    stripe_delta = _lightness_delta(tokens["$bg"], tokens["$bg-alt"])
    selection_delta = _lightness_delta(tokens["$bg"], tokens["$bg-sel"])

    assert stripe_delta >= ZEBRA_MIN_LIGHTNESS_DELTA, (
        f"theme {name}: $bg-alt is only L* {stripe_delta:.2f} from $bg "
        f"({tokens['$bg-alt']} on {tokens['$bg']}) -- at or below the "
        "just-noticeable difference for large flat areas, which is exactly "
        "the invisible striping the user rejected."
    )
    ceiling = ZEBRA_MAX_SHARE_OF_SELECTION * selection_delta
    assert stripe_delta <= ceiling, (
        f"theme {name}: $bg-alt is L* {stripe_delta:.2f} from $bg, "
        f"{stripe_delta / selection_delta:.0%} of the {selection_delta:.2f} "
        "that $bg-sel travels -- the stripe rivals the cursor row and stops "
        "reading as background."
    )


@pytest.mark.parametrize("name", ALL_THEMES)
def test_brand_banner_titles_match_pane_border_chrome(name: str) -> None:
    """The banner's tagline and pedigree are passive chrome, like every Pane.

    These previously used ``$text``, which rendered them brighter than any
    other border text in the app and made the attribution line compete with
    the banner art. They now use ``$text-dim``, the same token ``Pane`` uses
    for its own border title and subtitle.

    This is a deliberate legibility trade: ``$text-dim`` sits between 2.47:1
    and 5.30:1 against ``$bg`` depending on theme, below the 4.5:1 that
    ``test_muted_text_is_readable_on_both_content_backgrounds`` enforces for
    body text. Border chrome is not body text, and the app already holds every
    pane title to exactly this contrast -- so the banner matching it is the
    consistent choice. Raising it is a theme-wide ``$text-dim`` decision, not
    a banner-only one.
    """
    banner = _bodies_for_selector(ThemeStore().load(name), "BrandBanner")
    pane = _bodies_for_selector(ThemeStore().load(name), "Pane")

    assert any("border-title-color: $text-dim;" in body for body in banner)
    assert any("border-subtitle-color: $text-dim;" in body for body in banner)
    # Pin the relationship, not just the literal: the banner tracks Pane.
    assert any("border-title-color: $text-dim;" in body for body in pane)


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
        # The EMR application picker is a bordered peer of the source
        # ContextPicker, so it takes the same treatment from the shared sheet.
        "ApplicationPicker": ("border: solid $rule-dim;",),
    }

    for selector, declarations in expected.items():
        bodies = _bodies_for_selector(shared, selector)
        assert bodies, f"shared stylesheet missing {selector}"
        assert any(all(declaration in body for declaration in declarations) for body in bodies)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_source_header_carries_no_page_scoped_override(name: str) -> None:
    """The source picker is styled uniformly on every service page.

    EMR used to declare ``EmrServerlessPage ServiceSourceHeader`` with a
    ``border-left`` because its source and application pickers shared one
    bordered box and needed a divider between them. That box is gone -- the
    two are now independent, equal-width bordered cells, matching GluePage
    and AthenaPage -- so neither a global nor a page-scoped override should
    remain. The shared ``ContextPicker`` rules supply the border.
    """
    content = _raw_builtin_theme(name)

    assert not _bodies_for_selector(content, "ServiceSourceHeader")
    assert not _bodies_for_selector(content, "EmrServerlessPage ServiceSourceHeader")


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


def test_service_context_layout_has_no_theme_owned_frame() -> None:
    shared = (
        resources.files("aws_tui.ui.themes")
        .joinpath("operational-panes.tcss")
        .read_text(encoding="utf-8")
    )

    assert not _bodies_for_selector(shared, "GluePage > #glue-context-pane")
    assert not _bodies_for_selector(shared, "GluePage > #glue-context-row")
    assert not _bodies_for_selector(shared, "AthenaPage > #athena-context-row")
    assert not _bodies_for_selector(shared, "AthenaPage > #athena-context-row:focus-within")


@pytest.mark.parametrize("name", ALL_THEMES)
def test_builtin_themes_do_not_duplicate_operational_structure(name: str) -> None:
    content = _raw_builtin_theme(name)
    assert "Glue / Athena operational pane hierarchy" not in content

    for selector in (
        "GluePage GlueIcebergView",
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
        "AthenaPage TextArea:focus",
        "AthenaPage DataTable:focus",
        "AthenaPage #athena-query-controls:focus-within",
        "AthenaPage #athena-query-detail:focus-within",
        "AthenaPage #athena-results-summary:focus-within",
    ):
        assert all(
            "border: solid $accent;" not in body for body in _bodies_for_selector(content, selector)
        )

    assert "AthenaPage > #athena-context-header" not in content


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
