"""Deterministic real app, isolated metadata and endpoint fixtures."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aws_tui.domain.transfer_history import TransferConnectionIdentity
from tests.snapshot.apps.demo_mode import _normalise_svg
from tests.transfer_history_helpers import history_app


def snapshot_history_app(tmp_path: Path):
    app, tid, *_ = history_app(tmp_path)
    journal = app.app_ctx.transfer_journal
    record = journal.load_history()[0]
    journal.purge(tid)
    fixed = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    record = replace(
        record,
        source_connection=TransferConnectionIdentity("local", "Original source", "1" * 64),
        destination_connection=TransferConnectionIdentity(
            "local", "Original destination", "2" * 64
        ),
        started_at=fixed,
        updated_at=fixed,
    )
    for index, (status, publication, total) in enumerate(
        (
            ("outcome_unknown", "possibly_published", None),
            ("cancelled", "never_attempted", 0),
            ("completed", "confirmed_terminal", 7),
        )
    ):
        journal.history_store.save(
            replace(
                record,
                id=f"{3 - index:016x}",
                status=status,
                publication=publication,
                bytes_done=7 if status == "completed" else 0,
                bytes_total=total,
                finished_at=fixed if status != "outcome_unknown" else None,
                failure_reason="interrupted"
                if status == "outcome_unknown"
                else "cancelled"
                if status == "cancelled"
                else None,
            )
        )
    original = app.export_screenshot
    app.export_screenshot = lambda **kwargs: _normalise_svg(original(**kwargs))
    return app
