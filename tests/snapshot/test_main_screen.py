"""Snapshot tests for the main screen (services menu + dual pane + chrome).

One golden per theme, terminal 120x40. Re-generate with
``uv run pytest tests/snapshot --snapshot-update``.
"""

from __future__ import annotations

import pytest

from tests.snapshot.apps.main_screen import MainScreenApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_main_screen(theme: str, snap_compare) -> None:
    app = MainScreenApp(theme=theme)
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)
