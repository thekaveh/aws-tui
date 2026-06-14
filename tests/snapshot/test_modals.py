"""Snapshot tests for modal overlays + transfers tray.

Pinned to ``(120, 40)`` terminal. Goldens live under
``tests/snapshot/__snapshots__/``.
"""

from __future__ import annotations

import pytest

from tests.snapshot.apps.modals import (
    CommandPaletteApp,
    ConfirmModalApp,
    QuickLookApp,
    TransfersTrayApp,
)
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_command_palette(theme: str, snap_compare) -> None:
    assert snap_compare(CommandPaletteApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_confirm_modal_danger(theme: str, snap_compare) -> None:
    assert snap_compare(ConfirmModalApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_quick_look(theme: str, snap_compare) -> None:
    assert snap_compare(QuickLookApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_transfers_tray(theme: str, snap_compare) -> None:
    assert snap_compare(TransfersTrayApp(theme=theme), terminal_size=TERMINAL_SIZE)
