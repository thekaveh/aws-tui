"""Actual production overview: empty, denied and capped at two terminal sizes."""

import os
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.ui.widgets.modal_button import ModalButton
from tests.helpers import drain_workers, wait_until
from tests.integration.test_scoped_overview import (
    choose_scope,
    install_services,
    open_overview,
    wire_session,
)
from tests.integration.test_workspace_manager import focus_with_tabs, make_workspace_app


@pytest.mark.parametrize("size", [(80, 24), (120, 40)], ids=["80x24", "120x40"])
@pytest.mark.parametrize("state", ["empty", "denied", "capped"])
def test_scoped_overview_snapshot(tmp_path, monkeypatch, snap_compare, state, size):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    _athena, _glue, emr = install_services(ctx)
    session = wire_session()
    if state == "denied":
        session.responses["list_query_executions"] = PermissionDeniedError("synthetic denial")
    elif state == "capped":
        session.responses["list_query_executions"]["NextToken"] = "not-followed"
    monkeypatch.setattr(ctx.aws_session, "client", session.client)

    async def prepare(pilot):
        await drain_workers(app)
        await choose_scope(app, ctx, pilot, "athena")
        await open_overview(app, pilot)
        vm = app.screen.vm
        vm._clock = lambda: datetime(2026, 1, 2, tzinfo=UTC)
        if state != "empty":
            await focus_with_tabs(app, pilot, "overview-add")
            await pilot.press("enter")
            await focus_with_tabs(app, pilot, "overview-refresh")
            await pilot.press("enter")
            await wait_until(
                lambda: vm.sections[0].state in {"ready", "denied"}, what="snapshot section"
            )
            await focus_with_tabs(app, pilot, "overview-list")
            if state == "capped":
                await pilot.press("down")
        await pilot.pause()
        svg = app.export_screenshot()
        if destination := os.environ.get("AWS_TUI_OVERVIEW_CAPTURE_DIR"):
            output = Path(destination)
            output.mkdir(parents=True, exist_ok=True)
            (output / f"{state}-{size[0]}x{size[1]}.svg").write_text(svg)
        literal = unescape(svg).replace("\xa0", " ")
        for label in (
            "Scoped overview",
            "Manual refresh",
            "Add current scope",
            "Refresh all",
            "Retry source",
            "Open detail",
            "Remove scope",
            "Close",
        ):
            assert label in literal
        if state == "empty":
            assert "No watched scopes" in literal
            assert session.calls == []
        elif state == "denied":
            assert "denied" in literal
            assert "Access denied" in literal
        else:
            assert "history-analysts" in literal
            assert "FAILED" in literal
            assert "First page only" in literal
        for button in app.screen.query(ModalButton):
            assert button.region.bottom <= size[1]
            assert button.region.right <= size[0]
            assert button.content_region.width >= len(str(button.content))

    try:
        assert snap_compare(app, terminal_size=size, run_before=prepare)
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()
        emr.dispose()
