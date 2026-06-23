"""Snapshot tests for modal overlays.

Pinned to ``(120, 40)`` terminal. Goldens live under
``tests/snapshot/__snapshots__/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.modals import (
    CommandPaletteApp,
    ConfirmModalApp,
    CopyConfirmModalApp,
    CrashModalApp,
    FirstRunModalApp,
    QuickLookApp,
    ResumeModalApp,
)
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_command_palette(theme: str, snap_compare) -> None:
    assert snap_compare(CommandPaletteApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_confirm_modal_danger(theme: str, snap_compare) -> None:
    assert snap_compare(ConfirmModalApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_confirm_modal_copy_paths(theme: str, snap_compare) -> None:
    assert snap_compare(CopyConfirmModalApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_quick_look(theme: str, snap_compare) -> None:
    assert snap_compare(QuickLookApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_crash_modal(theme: str, snap_compare) -> None:
    assert snap_compare(CrashModalApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_resume_modal(theme: str, snap_compare) -> None:
    assert snap_compare(ResumeModalApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_first_run_modal(theme: str, snap_compare) -> None:
    assert snap_compare(FirstRunModalApp(theme=theme), terminal_size=TERMINAL_SIZE)


# ── Content-presence guards (per PR #53 lesson) ───────────────────────────
#
# pytest-textual-snapshot's parity-match can pass a uniformly-blank
# rendering across all 10 themes — every theme produces the same SVG
# (whitespace) so the comparison succeeds even when the modal failed
# to render. These guards read each generated SVG off disk and assert
# that the modal-specific text actually appears. One catches drift on
# any single theme before the trap can hide it.

# Each entry: (snapshot stem, list of substrings that MUST be present)
_MODAL_GUARDS: list[tuple[str, list[str]]] = [
    ("test_command_palette", ["Palette"]),
    ("test_confirm_modal_danger", ["Delete", "Cancel"]),
    ("test_confirm_modal_copy_paths", ["Copy", "Cancel"]),
    ("test_quick_look", ["voidline"]),  # the seeded preview text lists theme names
    ("test_crash_modal", ["Traceback", "continue", "quit"]),
    ("test_resume_modal", ["Resume", "abort", "keep"]),
    ("test_first_run_modal", ["skip", "S3-compatible"]),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("stem", "needles"), _MODAL_GUARDS, ids=[g[0] for g in _MODAL_GUARDS])
def test_modal_renders_expected_text(stem: str, needles: list[str], theme: str) -> None:
    p = Path(__file__).parent / "__snapshots__" / "test_modals" / f"{stem}[{theme}].raw"
    assert p.is_file(), (
        f"expected snapshot {p.name} on disk; the matching snap_compare "
        f"test should have generated it. Did the snapshot file path change?"
    )
    svg = p.read_text()
    for needle in needles:
        assert needle in svg, (
            f"snapshot {stem}[{theme}] is missing required text {needle!r}; "
            f"the modal may have failed to render (parity-match trap)."
        )
