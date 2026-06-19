"""Snapshot tests for TransfersOverlay across all 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.transfers import TransfersSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_transfers(theme: str, snap_compare) -> None:
    assert snap_compare(TransfersSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)
