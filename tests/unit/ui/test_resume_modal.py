"""Smoke tests for the ResumeModal widget."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.domain.transfer_journal import TransferJournalEntry
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.resume_modal import ResumeModal
from aws_tui.vm.chrome.resume_vm import ResumeVM


def _entries(n: int = 2) -> list[TransferJournalEntry]:
    return [
        TransferJournalEntry(
            transfer_id=f"t{i}",
            source_uri=f"local:///tmp/t{i}.bin",
            destination_uri=f"s3://bucket/uploads/t{i}.bin",
            upload_id=f"mpu-{i}",
            bytes_total=1_500_000,
            started_at=datetime(2026, 6, 13, tzinfo=UTC),
            last_progress=datetime(2026, 6, 13, tzinfo=UTC),
            completed_parts=(1, 2),
            completed_etags=("e1", "e2"),
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_resume_modal_renders_entries() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = ResumeVM(_entries(3), hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(ResumeModal(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ResumeModal)
            buttons = modal.query(ModalButton)
            assert {b.button_id for b in buttons} == {
                "resume-resume-btn",
                "resume-abort-btn",
                "resume-decide-btn",
                "resume-keep-btn",
            }
    finally:
        vm.dispose()
        hub.dispose()
