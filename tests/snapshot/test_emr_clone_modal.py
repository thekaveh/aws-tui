"""Snapshot tests for the EMR clone-job-run modal across 10 themes.

Every parity snapshot is paired with a content-presence guard so a
uniformly-blank render across all themes can't pass (per PR #53 /
#63 lesson)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshot.apps.emr_clone_modal import EmrCloneModalApp
from tests.snapshot.conftest import THEMES

TERMINAL_SIZE = (120, 40)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_clone_modal_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(EmrCloneModalApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_clone_modal_renders_form_labels_and_buttons(theme: str) -> None:
    """Content-presence guard for ``test_emr_clone_modal_snapshot``.

    Pin the labels + button labels we expect the modal to show so a
    uniformly-blank render can't pass parity-match across all 10
    themes. The fixture seeds an entry-point of
    ``s3://my-bucket/jobs/etl.py``; assert that string survives the
    render."""
    p = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_emr_clone_modal"
        / f"test_emr_clone_modal_snapshot[{theme}].raw"
    )
    assert p.is_file(), f"expected snapshot {p.name} on disk; run --snapshot-update first"
    svg = p.read_text()
    assert "Clone&#160;job&#160;run" in svg, f"modal title missing for theme {theme!r}"
    assert "Submit" in svg, f"Submit button missing for theme {theme!r}"
    assert "Cancel" in svg, f"Cancel button missing for theme {theme!r}"
    assert "etl.py" in svg, f"pre-populated entry point missing for theme {theme!r}"
