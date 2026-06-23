"""Snapshot tests for TransfersOverlay across all 10 themes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.transfers import TransfersSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_transfers(theme: str, snap_compare) -> None:
    assert snap_compare(TransfersSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_transfers_overlay_renders_rows(theme: str) -> None:
    """Content-presence guard for ``test_transfers``.

    A blank TransfersOverlay would pass parity-match across all 10
    themes (per PR #53 lesson). The fixture seeds transfers whose
    src→dst labels include the filenames ``2026-Q2.csv``,
    ``backup.zip``, and ``repo.tar.gz``; assert at least one survives
    the render and that an ``s3://`` URI is present too.
    """
    p = Path(__file__).parent / "__snapshots__" / "test_transfers" / f"test_transfers[{theme}].raw"
    assert p.is_file(), f"expected snapshot {p.name} on disk; did the snapshot file path change?"
    svg = p.read_text()
    has_filename = any(name in svg for name in ("2026-Q2.csv", "backup.zip", "repo.tar.gz"))
    assert has_filename, f"no seeded transfer filename rendered for theme {theme!r}"
    assert "s3" in svg, f"transfer URI 's3' missing for theme {theme!r}"
