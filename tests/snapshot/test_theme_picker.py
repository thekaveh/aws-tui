"""Snapshot tests for ThemePickerModal across all 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.theme_picker import ThemePickerSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_theme_picker(theme: str, snap_compare) -> None:
    assert snap_compare(ThemePickerSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)
