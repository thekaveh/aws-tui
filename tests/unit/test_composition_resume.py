"""Tests for ``apply_resume_decision`` in :mod:`aws_tui.composition`.

We avoid touching real AWS / boto3 here by passing a stub session whose
``client()`` returns an async context manager wrapping a small mock.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aws_tui.composition import apply_resume_decision
from aws_tui.domain.transfer_journal import TransferJournal, TransferJournalEntry
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.chrome.resume_vm import ResumeAction


def _entry(tid: str, dest: str = "s3://bucket/uploads/file.bin") -> TransferJournalEntry:
    return TransferJournalEntry(
        transfer_id=tid,
        source_uri="local:///tmp/file.bin",
        destination_uri=dest,
        upload_id=f"mpu-{tid}",
        bytes_total=4_000_000,
        started_at=datetime(2026, 6, 13, tzinfo=UTC),
        last_progress=datetime(2026, 6, 13, tzinfo=UTC),
        completed_parts=(1, 2),
        completed_etags=("e1", "e2"),
    )


class _FakeS3Client:
    def __init__(self) -> None:
        self.aborts: list[dict[str, Any]] = []

    async def abort_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.aborts.append(kwargs)
        return {}


class _FakeAwsSession:
    def __init__(self) -> None:
        self.client_obj = _FakeS3Client()

    async def client(self, connection: Connection, service: str) -> Any:
        client_obj = self.client_obj

        @asynccontextmanager
        async def _ctx():
            yield client_obj

        return _ctx()


def _connection() -> Connection:
    return Connection(
        name="kaveh-dev",
        kind="aws",
        source="config",
        profile="kaveh-dev",
        region="us-east-1",
    )


@pytest.mark.asyncio
async def test_keep_for_later_is_noop(tmp_path: Path) -> None:
    journal = TransferJournal(base_dir=tmp_path / "transfers")
    tid = journal.begin(source_uri="local:///x", destination_uri="s3://bucket/x")
    journal.record_part(tid, part_index=1, etag="abc", bytes_written=8)
    session = _FakeAwsSession()

    await apply_resume_decision(
        decision=ResumeAction.KEEP_FOR_LATER,
        entries=[_entry("k1")],
        journal=journal,
        aws_session=session,  # type: ignore[arg-type]
        connection=_connection(),
    )
    # No client called and the journal file still on disk
    assert session.client_obj.aborts == []
    assert any((tmp_path / "transfers").glob("*.jsonl"))


@pytest.mark.asyncio
async def test_resume_all_is_noop(tmp_path: Path) -> None:
    journal = TransferJournal(base_dir=tmp_path / "transfers")
    session = _FakeAwsSession()

    await apply_resume_decision(
        decision=ResumeAction.RESUME_ALL,
        entries=[_entry("r1")],
        journal=journal,
        aws_session=session,  # type: ignore[arg-type]
        connection=_connection(),
    )
    assert session.client_obj.aborts == []


@pytest.mark.asyncio
async def test_abort_all_aborts_and_purges(tmp_path: Path) -> None:
    journal = TransferJournal(base_dir=tmp_path / "transfers")
    # Two journal files on disk so purge has something to remove.
    tid_a = journal.begin(
        source_uri="local:///a", destination_uri="s3://bucket/uploads/a.bin", upload_id="mpu-a"
    )
    tid_b = journal.begin(
        source_uri="local:///b", destination_uri="s3://bucket/uploads/b.bin", upload_id="mpu-b"
    )
    entries = [
        TransferJournalEntry(
            transfer_id=tid_a,
            source_uri="local:///a",
            destination_uri="s3://bucket/uploads/a.bin",
            upload_id="mpu-a",
            bytes_total=4096,
            started_at=datetime(2026, 6, 13, tzinfo=UTC),
            last_progress=datetime(2026, 6, 13, tzinfo=UTC),
        ),
        TransferJournalEntry(
            transfer_id=tid_b,
            source_uri="local:///b",
            destination_uri="s3://bucket/uploads/b.bin",
            upload_id="mpu-b",
            bytes_total=4096,
            started_at=datetime(2026, 6, 13, tzinfo=UTC),
            last_progress=datetime(2026, 6, 13, tzinfo=UTC),
        ),
    ]
    session = _FakeAwsSession()
    await apply_resume_decision(
        decision=ResumeAction.ABORT_ALL,
        entries=entries,
        journal=journal,
        aws_session=session,  # type: ignore[arg-type]
        connection=_connection(),
    )
    assert len(session.client_obj.aborts) == 2
    assert {a["UploadId"] for a in session.client_obj.aborts} == {"mpu-a", "mpu-b"}
    # Journals purged.
    assert list((tmp_path / "transfers").glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_abort_all_without_connection_is_noop(tmp_path: Path) -> None:
    journal = TransferJournal(base_dir=tmp_path / "transfers")
    journal.begin(source_uri="local:///a", destination_uri="s3://bucket/a")
    session = _FakeAwsSession()

    await apply_resume_decision(
        decision=ResumeAction.ABORT_ALL,
        entries=[_entry("a1")],
        journal=journal,
        aws_session=session,  # type: ignore[arg-type]
        connection=None,
    )
    assert session.client_obj.aborts == []
