"""Snapshot tests for ToastStack across all 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.toast import ToastSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_toast_stack(theme: str, snap_compare) -> None:
    assert snap_compare(ToastSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)
