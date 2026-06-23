"""Snapshot tests for ToastStack across all 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.toast import ToastSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_toast_stack(theme: str, snap_compare) -> None:
    assert snap_compare(ToastSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_toast_stack_renders_toast_text(theme: str) -> None:
    """Content-presence guard for ``test_toast_stack``.

    A blank ToastStack would pass parity-match across all 10 themes
    (per PR #53 lesson). The fixture seeds an info-level toast whose
    message contains the word "authenticate"; assert it survives the
    render.
    """
    p = Path(__file__).parent / "__snapshots__" / "test_toast" / f"test_toast_stack[{theme}].raw"
    assert p.is_file(), f"expected snapshot {p.name} on disk; did the snapshot file path change?"
    svg = p.read_text()
    assert "authenticate" in svg, f"toast text 'authenticate' missing for theme {theme!r}"
