"""Live rendered guards and theme/viewport snapshots for faithful EMR cloning."""

from __future__ import annotations

from html import unescape
from itertools import product

import pytest
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Input

from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from tests.helpers import wait_until
from tests.snapshot.apps.emr_clone_modal import EmrCloneModalApp
from tests.snapshot.conftest import THEMES

TERMINAL_SIZE = (120, 40)
REVIEW_CASES = [
    pytest.param(theme, size, stage, id=f"{theme}-{size[0]}x{size[1]}-{stage}")
    for theme, size, stage in product(
        ("carbon", "github-light"), ((80, 24), (120, 40)), ("source", "changed", "inherited")
    )
]


def _rendered(pilot: Pilot) -> str:
    return unescape(pilot.app.export_screenshot()).replace("\xa0", " ")


async def _edit_guard(pilot: Pilot) -> None:
    await wait_until(lambda: "etl.py" in _rendered(pilot), what="visible clone edit fields")
    svg = _rendered(pilot)
    for label in ("Clone job run", "Execution role ARN", "Review", "Cancel", "etl.py"):
        assert label in svg
    modal = pilot.app.screen
    assert isinstance(modal, JobRunCloneModal)
    assert not modal.query_one("#clone-submit").display


async def _review_guard(pilot: Pilot, stage: str) -> None:
    modal = pilot.app.screen
    assert isinstance(modal, JobRunCloneModal)
    modal.query_one("#clone-name", Input).value = "nightly-revised"
    modal.action_review()
    scroll = modal.query_one("#clone-review-scroll", VerticalScroll)
    await wait_until(lambda: scroll.has_focus, what="clone review keyboard focus")
    markers = {
        "source": ("r-001", "analytics", "us-east-1", "00abc", "EmrJobRole"),
        "changed": (
            "Name — Changed",
            "Source:",
            "nightly-2026-06-25",
            "Proposed:",
            "nightly-revised",
        ),
        "inherited": ("Inherited", "unknown", "Hidden application defaults", "read-only outputs"),
    }[stage]
    if stage == "changed":
        scroll.scroll_to(y=7, animate=False, force=True)
    elif stage == "inherited":
        await pilot.press("end")
    await wait_until(
        lambda: all(marker in _rendered(pilot) for marker in markers),
        what=f"visible clone {stage} review",
    )
    svg = _rendered(pilot)
    for label in ("Cancel", "Back", "Submit"):
        assert label in svg
    for _ in range(5):
        if pilot.app.focused is modal.query_one("#clone-submit"):
            break
        await pilot.press("tab")
    assert pilot.app.focused is modal.query_one("#clone-submit")
    assert not modal.vm.submitted_id
    # Focus movement may request a deferred scroll. Check the final painted
    # viewport too, so the golden actually contains every promised marker.
    await pilot.pause()
    if stage == "changed":
        scroll.scroll_to(y=7, animate=False, force=True)
    elif stage == "source":
        scroll.scroll_home(animate=False, force=True)
    else:
        scroll.scroll_end(animate=False, force=True)
    await pilot.pause()
    for marker in (*markers, "Cancel", "Back", "Submit"):
        assert marker in _rendered(pilot)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_clone_modal_snapshot(theme: str, snap_compare) -> None:
    app = EmrCloneModalApp(theme=theme)
    try:
        assert snap_compare(app, terminal_size=TERMINAL_SIZE, run_before=_edit_guard)
    finally:
        app._vm.dispose()


@pytest.mark.parametrize(("theme", "size", "stage"), REVIEW_CASES)
def test_emr_clone_review_snapshot(
    theme: str, size: tuple[int, int], stage: str, snap_compare
) -> None:
    app = EmrCloneModalApp(theme=theme)

    async def prepare(pilot: Pilot) -> None:
        await _review_guard(pilot, stage)

    try:
        assert snap_compare(app, terminal_size=size, run_before=prepare)
    finally:
        app._vm.dispose()
