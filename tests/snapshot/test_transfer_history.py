"""Actual history/recovery screen at normal and narrow terminal geometry."""

from __future__ import annotations

import os
from html import unescape
from pathlib import Path

import pytest

from aws_tui.ui.widgets.modal_button import ModalButton
from tests.helpers import drain_workers, wait_until
from tests.snapshot.apps.transfer_history import snapshot_history_app
from tests.transfer_history_helpers import NAME


@pytest.mark.parametrize("size", [(120, 40), (80, 24)], ids=["120x40", "80x24"])
@pytest.mark.parametrize("mode", ["history", "recovery", "conflict"])
def test_transfer_history_snapshot(tmp_path, snap_compare, size, mode):
    app = snapshot_history_app(tmp_path)

    async def prepare(pilot):
        await drain_workers(app)
        await pilot.press("ctrl+t")
        await wait_until(
            lambda: type(app.screen).__name__ == "TransferHistoryModal",
            what="snapshot history mounted",
        )
        if mode == "recovery":
            await pilot.press("r")
        await wait_until(
            lambda: NAME in app.screen.query_one("#transfer-history-details").text,
            what="literal details painted",
        )
        if mode == "conflict":
            from aws_tui.domain.filesystem import PathRef
            from aws_tui.domain.local_fs import LocalFS
            from aws_tui.ui.widgets.transfer_history_modal import RetryConflictModal
            from aws_tui.vm.file_manager.transfer_history_vm import RetryPlan

            record = app.app_ctx.transfer_history_vm.records[0]
            entry = await LocalFS(root=tmp_path / "source").stat(
                PathRef.from_posix(record.source_uri)
            )
            await app.push_screen(
                RetryConflictModal(
                    RetryPlan(
                        record.id,
                        record.source_connection,
                        record.destination_connection,
                        record.source_uri,
                        record.destination_uri,
                        entry,
                        None,
                    )
                )
            )
        await pilot.pause()
        svg = unescape(app.export_screenshot()).replace("\xa0", " ")
        required = (
            (
                "Refuse overwrite",
                "Skip",
                "Rename",
                "Overwrite",
                "Cancel",
                "fresh conflict decision",
                NAME,
            )
            if mode == "conflict"
            else (
                "History",
                "Recovery",
                "outcome unknown",
                "possibly published",
                "never attempted",
                "confirmed terminal",
                "Retry copy",
                "Clear history",
                "Recheck",
                "Close",
            )
        )
        for text in required:
            assert text in svg, f"snapshot missing {text}"
        for button in app.screen.query(ModalButton):
            assert button.region.bottom <= app.screen.size.height
            assert button.region.right <= app.screen.size.width
            assert button.content_region.width >= len(str(button.content))
        if directory := os.environ.get("AWS_TUI_HISTORY_CAPTURE_DIR"):
            output = Path(directory)
            output.mkdir(parents=True, exist_ok=True)
            (output / f"{mode}-{size[0]}x{size[1]}.svg").write_text(
                app.export_screenshot(), encoding="utf-8"
            )

    assert snap_compare(app, terminal_size=size, run_before=prepare)
