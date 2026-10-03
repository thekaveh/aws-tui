"""All-theme first-run snapshots with visible-content guards at 120x40."""

import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

import pytest
from textual.widgets import Input

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.modal_button import ModalButton
from tests.helpers import wait_until
from tests.snapshot.apps.first_run import FirstRunApp


async def settle(pilot):  # type: ignore[no-untyped-def]
    target = (
        pilot.app.query_one("#form-name", Input)
        if pilot.app.open_form
        else next(b for b in pilot.app.query(ModalButton) if b.button_id == "first-run-add")
    )
    await wait_until(lambda: pilot.app.focused is target, what="setup snapshot focus")
    if pilot.app.open_form:
        for value in ("local", "http://localhost:9000", "us-east-1", "ACCESS", "SECRET"):
            await pilot.press(*value, "tab")
        await pilot.press("tab", "tab")
        save = next(b for b in pilot.app.query(ModalButton) if b.button_id == "form-save-btn")
        assert pilot.app.focused is save
        await pilot.pause()
        assert save.region.bottom <= pilot.app.query_one("#content-first-run").content_region.bottom


@pytest.mark.parametrize("theme", ThemeStore.BUILTIN_NAMES)
@pytest.mark.parametrize("case", ["actions", "choices", "form"])
def test_first_run(theme, case, snap_compare):  # type: ignore[no-untyped-def]
    assert snap_compare(
        FirstRunApp(theme=theme, case=case), terminal_size=(120, 40), run_before=settle
    )


@pytest.mark.parametrize("theme", ThemeStore.BUILTIN_NAMES)
@pytest.mark.parametrize("case", ["actions", "choices", "form"])
def test_first_run_visible_content(theme, case):  # type: ignore[no-untyped-def]
    path = (
        Path(__file__).parent
        / "__snapshots__"
        / "test_first_run"
        / f"test_first_run[{case}-{theme}].raw"
    )
    assert path.is_file(), "New first-run goldens must exist before semantic verification"
    root = ET.fromstring(path.read_text())
    visible = unescape(
        " ".join("".join(node.itertext()) for node in root.iter() if node.tag.endswith("}text"))
    ).replace("\xa0", " ")
    if case != "actions":
        for label in ("config", "auto-aws-profile", "demo"):
            assert label in visible, (case, theme, label)
    if case in {"actions", "choices"}:
        for label in (
            "Add S3-compatible connection",
            "AWS profile setup",
            "Retry discovery",
            "Connection guide:",
            "https://thekaveh.github.io/aws-tui/connections/",
        ):
            assert label in visible, (case, theme, label)
        status = "No AWS profiles" if case == "actions" else "Select a connection to open it"
        assert status in visible, (case, theme, status)
    if case == "form":
        for label in (
            "Name",
            "Endpoint URL",
            "Access key ID",
            "Secret access key",
            "Session token",
            "Save and open",
            "cancel",
            "http://localhost:9000",
            "us-east-1",
            "ACCESS",
        ):
            assert label in visible, (case, theme, label)
