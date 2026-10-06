from __future__ import annotations

from html import unescape
from itertools import product
from pathlib import Path
from xml.etree import ElementTree

import pytest
from textual.widgets import Button

from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.snapshot.apps.athena_drafts import AthenaDraftsApp
from tests.snapshot.conftest import THEMES

QUERY_CASES = [
    pytest.param(state, theme, size, id=f"{state}-{theme}-{size[0]}x{size[1]}")
    for state, theme, size in product(
        ("pending", "saved"), ("carbon", "github-light"), ((80, 24), (120, 40))
    )
]
MODAL_CASES = [
    pytest.param(state, theme, size, id=f"{state}-{theme}-{size[0]}x{size[1]}")
    for state, theme, size in product(("manager", "stale-context"), THEMES, ((80, 24), (120, 40)))
]


async def settle(pilot):
    await drain_workers(pilot.app)
    await pilot.pause()
    if pilot.app.state in ("pending", "saved"):
        await focus_and_settle(pilot.app.query_one("#athena-drafts", Button))
        await pilot.pause()
        await wait_until(
            lambda: "draft_render_marker" in pilot.app.export_screenshot(),
            what="rendered draft SQL",
        )
    else:
        await wait_until(
            lambda: "Saved:" in pilot.app.export_screenshot(), what="rendered draft metadata"
        )


@pytest.mark.parametrize(("state", "theme", "size"), QUERY_CASES)
def test_draft_query_snapshot(state, theme, size, snap_compare):
    assert snap_compare(
        AthenaDraftsApp(theme=theme, state=state), terminal_size=size, run_before=settle
    )


@pytest.mark.parametrize(("state", "theme", "size"), MODAL_CASES)
def test_draft_manager_snapshot(state, theme, size, snap_compare):
    assert snap_compare(
        AthenaDraftsApp(theme=theme, state=state), terminal_size=size, run_before=settle
    )


def golden(name):
    return unescape(
        (Path(__file__).parent / "__snapshots__" / "test_athena_drafts" / f"{name}.raw").read_text()
    ).replace("\xa0", " ")


@pytest.mark.parametrize(("state", "theme", "size"), QUERY_CASES)
def test_draft_query_content_guard(state, theme, size):
    svg = golden(f"test_draft_query_snapshot[{state}-{theme}-{size[0]}x{size[1]}]")
    assert "query editor" in svg
    assert "SELECT" in svg
    assert "draft_render_marker" in svg
    assert f"Draft {state}" in svg
    terminal_labels = [
        "".join(node.itertext()).strip()
        for node in ElementTree.fromstring(
            (
                Path(__file__).parent
                / "__snapshots__"
                / "test_athena_drafts"
                / f"test_draft_query_snapshot[{state}-{theme}-{size[0]}x{size[1]}].raw"
            ).read_text()
        ).iter()
        if node.tag.endswith("}text") and node.get("clip-path")
    ]
    assert "Drafts" in terminal_labels


@pytest.mark.parametrize(("state", "theme", "size"), MODAL_CASES)
def test_draft_manager_content_guard(state, theme, size):
    svg = golden(f"test_draft_manager_snapshot[{state}-{theme}-{size[0]}x{size[1]}]")
    assert "Local Athena SQL drafts" in svg
    for text in (
        "Connection: analytics",
        "Region: us-west-2",
        "Workgroup: primary",
        "Catalog: AwsDataCatalog",
        "Database: default",
        "Saved:",
        "Restore",
        "Delete",
        "Clear all",
        "Keep current editor",
        "Close",
    ):
        assert text in svg
    assert "draft_render_marker" not in svg
    if state == "stale-context":
        # At 80x24 the bounded detail viewport scrolls the final warning line.
        assert "Draft context is unavailable or changed." in svg
