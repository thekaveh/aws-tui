"""Controlled normal/narrow snapshots of literal properties and partial reads."""

from __future__ import annotations

from html import unescape

import pytest
from botocore.exceptions import ClientError
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import DataTable, TextArea
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.s3_fs import S3FS
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.s3_object_details import S3ObjectDetailsModal
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.s3_object_details_vm import S3ObjectDetailsState, S3ObjectDetailsVM
from tests.helpers import wait_until
from tests.s3_object_details_support import RecordingSession


class DetailsSnapshotApp(App[None]):
    def __init__(self, state: str) -> None:
        super().__init__()
        self.CSS = ThemeStore().load("carbon")
        hub = MessageHub()
        session = RecordingSession()
        session.s3.queue("list_objects_v2", {"Contents": [{"Key": "a.txt", "Size": 42}]})
        session.s3.queue(
            "head_object",
            {
                "ContentType": '[bold]雪[/bold] "quoted"\n' + "full literal property " * 25,
                "ContentLength": 42,
                "ETag": '"opaque-2"',
                "Metadata": {"note": "[red]literal[/red]"},
            },
        )
        session.s3.queue(
            "get_object_tagging",
            ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Tags read denied"}},
                "GetObjectTagging",
            )
            if state == "partial"
            else {"TagSet": [{"Key": "owner", "Value": "[blue]literal[/blue]"}]},
        )
        self.pane = PaneVM(
            provider=S3FS(session=session, bucket="bucket"),
            path_protocol="s3:",
            connection_key=("s3-compatible", "fixture"),
            hub=hub,
            dispatcher=NULL_DISPATCHER,
        )
        self.pane.construct()
        self.vm = S3ObjectDetailsVM(pane=self.pane, hub=hub, dispatcher=NULL_DISPATCHER)
        self.copied: list[str] = []
        self.state = state

    async def on_mount(self) -> None:
        await self.pane.setup()
        self.vm.construct()
        self.push_screen(S3ObjectDetailsModal(self.vm, copy_value=self.copy_detail))

    async def copy_detail(self, value: str) -> None:
        self.copied.append(value)


@pytest.mark.parametrize("state", ["ready", "partial"])
@pytest.mark.parametrize("size", [(120, 40), (80, 24)], ids=["normal", "narrow"])
def test_s3_details_literal_snapshot(state, size, snap_compare):
    app = DetailsSnapshotApp(state)

    async def prepare(pilot: Pilot) -> None:
        await wait_until(
            lambda: app.vm.state is S3ObjectDetailsState.READY, what="snapshot details"
        )
        modal = app.screen
        assert isinstance(modal, S3ObjectDetailsModal)
        table = modal.query_one(DataTable)
        assert table.row_count == 13
        if state == "partial":
            table.move_cursor(row=9)
            await pilot.pause()
            assert modal.query_one(TextArea).text.startswith("Unavailable:")
        else:
            assert "[bold]雪[/bold]" in modal.query_one(TextArea).text
            await pilot.press("ctrl+c")
            await wait_until(lambda: bool(app.copied), what="snapshot copy full value")
            assert app.copied[-1] == app.vm.fields[0].copy_value
        await pilot.pause()
        rendered = unescape(app.export_screenshot()).replace("\xa0", " ")
        for value in ("Full value", "Copy", "Close"):
            assert value in rendered
        assert "Unavailable" in rendered

    try:
        assert snap_compare(app, terminal_size=size, run_before=prepare)
    finally:
        app.vm.dispose()
        app.pane.dispose()
