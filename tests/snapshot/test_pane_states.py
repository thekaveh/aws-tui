"""Snapshot tests for the six pane-state placeholders per spec §7.7.

Each state x theme produces one golden. Per the watchout, this is a sizable
test surface — we ship the high-signal 'empty' and 'auth_required' states
across the full built-in theme set. Other states are covered by a single
snapshot under Carbon to keep CI fast and goldens manageable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.snapshot.apps.pane_states import (
    make_auth_required_app,
    make_empty_app,
    make_error_app,
    make_forbidden_app,
    make_loading_app,
    make_unreachable_app,
)
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_pane_empty(theme: str, snap_compare) -> None:
    assert snap_compare(make_empty_app(theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_pane_auth_required(theme: str, snap_compare) -> None:
    assert snap_compare(make_auth_required_app(theme), terminal_size=TERMINAL_SIZE)


# The four less-frequent placeholders only snapshot under Carbon to keep
# the golden set lean. The .tcss themes are validated separately by the
# unit-tier ``test_themes.py`` suite.


def test_pane_loading_carbon(snap_compare) -> None:
    assert snap_compare(make_loading_app("carbon"), terminal_size=TERMINAL_SIZE)


def test_pane_forbidden_carbon(snap_compare) -> None:
    assert snap_compare(make_forbidden_app("carbon"), terminal_size=TERMINAL_SIZE)


def test_pane_unreachable_carbon(snap_compare) -> None:
    assert snap_compare(make_unreachable_app("carbon"), terminal_size=TERMINAL_SIZE)


def test_pane_error_carbon(snap_compare) -> None:
    assert snap_compare(make_error_app("carbon"), terminal_size=TERMINAL_SIZE)


# ── Content-presence guards (per PR #53 lesson) ───────────────────────────
#
# pytest-textual-snapshot's parity-match can pass a uniformly-blank render
# across all themes — every theme produces whitespace-only SVG and the
# comparison succeeds. These guards read each generated SVG off disk and
# assert the state-specific placeholder text is actually present.

_SNAPSHOT_DIR = Path(__file__).parent / "__snapshots__" / "test_pane_states"


def _assert_snapshot_has(stem: str, needles: list[str]) -> None:
    p = _SNAPSHOT_DIR / f"{stem}.raw"
    assert p.is_file(), (
        f"expected snapshot {p.name} on disk; the matching snap_compare "
        f"test should have generated it. Did the snapshot file path change?"
    )
    svg = p.read_text()
    for needle in needles:
        assert needle in svg, (
            f"snapshot {stem} missing required state-text {needle!r}; "
            f"the placeholder may have failed to render (parity-match trap)."
        )


@pytest.mark.parametrize("theme", THEMES)
def test_pane_empty_renders_empty_label(theme: str) -> None:
    _assert_snapshot_has(f"test_pane_empty[{theme}]", ["empty"])


@pytest.mark.parametrize("theme", THEMES)
def test_pane_auth_required_renders_press_prompt(theme: str) -> None:
    _assert_snapshot_has(f"test_pane_auth_required[{theme}]", ["press"])


def test_pane_loading_carbon_renders_loading_label() -> None:
    _assert_snapshot_has("test_pane_loading_carbon", ["loading"])


def test_pane_forbidden_carbon_renders_denied_label() -> None:
    _assert_snapshot_has("test_pane_forbidden_carbon", ["denied"])


def test_pane_unreachable_carbon_renders_unreachable_label() -> None:
    _assert_snapshot_has("test_pane_unreachable_carbon", ["unreachable"])


def test_pane_error_carbon_renders_error_label() -> None:
    _assert_snapshot_has("test_pane_error_carbon", ["error"])


# ── Zebra / border-glyph guards ─────────────────────────────────────────────
#
# The 2026-09-18 pass re-recorded 44 goldens at once (zebra striping plus the
# removal of the U+1F4CB border glyph). A diff that large can hide a genuine
# regression, so these guards assert the two properties directly off disk
# rather than trusting a parity match.

_THEME_DIR = Path(__file__).parents[2] / "src" / "aws_tui" / "ui" / "themes"
_DEMO_DIR = Path(__file__).parent / "__snapshots__" / "test_demo_mode"


def _zebra_token(theme: str) -> str:
    """The theme's own ``$bg-alt``, read from its stylesheet."""
    text = (_THEME_DIR / f"{theme}.tcss").read_text()
    match = re.search(r"^\s*\$bg-alt:\s*(#[0-9a-f]{6});", text, re.M)
    assert match, f"{theme}.tcss defines no $bg-alt"
    return match.group(1)


@pytest.mark.parametrize("theme", THEMES)
def test_rowless_pane_states_carry_no_zebra_stripe(theme: str) -> None:
    """A state with no rows must never paint the stripe.

    The stripe is scoped ``Pane .entry-row.-alt``. If it ever leaks onto a
    placeholder ``Static`` or a widget outside ``Pane``, it shows up here as
    the theme's own ``$bg-alt`` appearing in a golden that renders no rows.
    """
    stripe = _zebra_token(theme)
    for stem in (f"test_pane_empty[{theme}]", f"test_pane_auth_required[{theme}]"):
        svg = (_SNAPSHOT_DIR / f"{stem}.raw").read_text()
        assert stripe.lower() not in svg.lower(), (
            f"{stem} renders no rows yet contains the zebra colour {stripe}; "
            f"the `Pane .entry-row.-alt` rule has leaked."
        )


@pytest.mark.parametrize("theme", THEMES)
def test_demo_listing_actually_paints_the_zebra_stripe(theme: str) -> None:
    """The positive half: where rows exist, the stripe must be visible.

    Without this, deleting the rule entirely would still leave the negative
    guard above passing.
    """
    stripe = _zebra_token(theme)
    svg = (_DEMO_DIR / f"test_demo_mode_snapshot[{theme}].raw").read_text()
    assert stripe.lower() in svg.lower(), (
        f"the demo listing for {theme} paints no {stripe} stripe; either the "
        f"rows failed to render or the zebra rule was dropped."
    )


@pytest.mark.parametrize("theme", THEMES)
def test_pane_border_carries_no_clipboard_glyph(theme: str) -> None:
    """The border title is plain text; the copy affordance is a key and a
    palette command, not an emoji wedged into box-drawing chrome."""
    for stem in (f"test_pane_empty[{theme}]", f"test_pane_auth_required[{theme}]"):
        svg = (_SNAPSHOT_DIR / f"{stem}.raw").read_text()
        assert "\U0001f4cb" not in svg, f"{stem} still renders the clipboard glyph"
