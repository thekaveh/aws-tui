"""Snapshot tests for modal overlays.

Pinned to ``(120, 40)`` terminal. Goldens live under
``tests/snapshot/__snapshots__/``.
"""

from __future__ import annotations

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
