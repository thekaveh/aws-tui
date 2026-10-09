"""Public persistence calls own raw descriptors until stream adoption succeeds."""

from __future__ import annotations

import errno
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aws_tui.domain.transfer_history import (
    TransferConnectionIdentity,
    TransferHistoryRecord,
    TransferHistoryStore,
)
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.athena_draft_store import AthenaDraftStore, DraftPermit, SqlDraft, draft_id
from aws_tui.infra.config_store import ConfigStore


@pytest.mark.parametrize(
    ("operation", "mode"),
    [
        ("history-read", "rb"),
        ("history-save", "wb"),
        ("journal-append", "a"),
        ("journal-read", "r"),
        ("draft-save", "wb"),
    ],
)
@pytest.mark.parametrize("interrupt", [False, True], ids=["allocation-error", "interruption"])
def test_public_persistence_closes_unadopted_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, mode: str, interrupt: bool
) -> None:
    now = datetime.now(UTC)
    identity = TransferConnectionIdentity("local", "fixture", "0" * 64)
    record = TransferHistoryRecord(
        "f" * 16,
        "copy",
        identity,
        identity,
        "/source",
        "/destination",
        now,
        now,
        now,
        1,
        1,
        "completed",
        "confirmed_terminal",
        None,
    )
    history = TransferHistoryStore(tmp_path / "history")
    history.save(record)
    journal = TransferJournal(base_dir=tmp_path / "journal")
    transfer_id = journal.begin(source_uri="/source", destination_uri="/destination", bytes_total=1)
    drafts = AthenaDraftStore(
        config=ConfigStore(path=tmp_path / "config.toml"), directory=tmp_path / "drafts"
    )
    assert drafts.set_enabled(True).code is None
    context = ("fixture", "us-east-1", "primary", "AwsDataCatalog", "fixture")
    draft = SqlDraft(draft_id(context), context, "select 1", now, now)
    assert drafts.save(draft, permit=DraftPermit()).code is None
    paths = [
        tmp_path / "history" / f"{record.id}.json",
        tmp_path / "journal" / f"{transfer_id}.jsonl",
        tmp_path / "drafts" / f"{draft.id}.json",
    ]
    before = [path.read_bytes() for path in paths]
    record = replace(record, bytes_done=2, bytes_total=2)
    draft = replace(draft, sql="select 2")
    original = os.fdopen
    captured: list[int] = []
    fault: BaseException = (
        KeyboardInterrupt("interrupted before descriptor adoption")
        if interrupt
        else OSError(errno.ENOMEM, "stream allocation failed")
    )

    def fail_adoption(fd: int, actual_mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if actual_mode != mode:
            return original(fd, actual_mode, *args, **kwargs)
        captured.append(fd)
        raise fault

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "fdopen", fail_adoption)
            if interrupt:
                with pytest.raises(KeyboardInterrupt) as error:
                    _invoke(operation, history, journal, transfer_id, record, drafts, draft)
                assert error.value is fault
            elif operation in {"history-save", "journal-append"}:
                with pytest.raises(OSError, match="stream allocation failed") as error:
                    _invoke(operation, history, journal, transfer_id, record, drafts, draft)
                assert error.value is fault
            else:
                result = _invoke(operation, history, journal, transfer_id, record, drafts, draft)
                if operation == "draft-save":
                    assert result.code == "io"
                else:
                    assert not result
        # Hold the original traceback throughout the assertion: GC must not
        # conceal an unadopted descriptor retained by the failing call.
        assert fault.__traceback__ is not None
        assert len(captured) == 1
        assert [path.read_bytes() for path in paths] == before
        assert not list(tmp_path.rglob("*.tmp"))
        with pytest.raises(OSError, match=r"(?i)bad file descriptor") as closed:
            os.fstat(captured[0])
        assert closed.value.errno == errno.EBADF
    finally:
        for fd in captured:
            try:
                os.fstat(fd)
            except OSError:
                continue
            os.close(fd)


def _invoke(
    operation: str,
    history: TransferHistoryStore,
    journal: TransferJournal,
    transfer_id: str,
    record: TransferHistoryRecord,
    drafts: AthenaDraftStore,
    draft: SqlDraft,
) -> Any:
    if operation == "history-read":
        return history.load()
    if operation == "history-save":
        return history.save(record)
    if operation == "journal-append":
        return journal.record_part(transfer_id, part_index=1, etag="fixture", bytes_written=1)
    if operation == "journal-read":
        return journal.find_unfinished()
    return drafts.save(draft, permit=DraftPermit())
