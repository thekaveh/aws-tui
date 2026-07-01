"""Unit tests for TransferJournal."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from aws_tui.domain.transfer_journal import TransferJournal

pytestmark = pytest.mark.unit


def test_begin_returns_unique_16_hex_id(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    a = j.begin(source_uri="src://a", destination_uri="dst://a")
    b = j.begin(source_uri="src://b", destination_uri="dst://b")
    assert len(a) == 16
    assert len(b) == 16
    assert a != b
    # Both are valid hex.
    int(a, 16)
    int(b, 16)


def test_journal_file_is_jsonl(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    tid = j.begin(source_uri="src://a", destination_uri="dst://a", bytes_total=42)
    j.record_part(tid, part_index=1, etag="e1", bytes_written=10)
    j.record_part(tid, part_index=2, etag="e2", bytes_written=20)
    j.record_part(tid, part_index=3, etag="e3", bytes_written=12)

    path = tmp_path / f"{tid}.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 4
    assert lines[0]["kind"] == "begin"
    assert [ln["kind"] for ln in lines[1:]] == ["part", "part", "part"]


def test_find_unfinished_returns_in_flight(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    tid = j.begin(source_uri="s", destination_uri="d", bytes_total=100)
    j.record_part(tid, part_index=1, etag="abc", bytes_written=50)
    unfinished = j.find_unfinished()
    assert len(unfinished) == 1
    entry = unfinished[0]
    assert entry.transfer_id == tid
    assert entry.bytes_total == 100
    assert entry.completed_parts == (1,)
    assert entry.completed_etags == ("abc",)
    assert not entry.finished
    assert not entry.aborted


def test_find_unfinished_excludes_finished(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    tid = j.begin(source_uri="s", destination_uri="d")
    j.record_part(tid, part_index=1, etag="e", bytes_written=10)
    j.mark_finished(tid)
    assert j.find_unfinished() == []


def test_find_unfinished_excludes_aborted(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    tid = j.begin(source_uri="s", destination_uri="d")
    j.mark_aborted(tid)
    assert j.find_unfinished() == []


def test_find_unfinished_mixed(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    a = j.begin(source_uri="sa", destination_uri="da")
    b = j.begin(source_uri="sb", destination_uri="db")
    c = j.begin(source_uri="sc", destination_uri="dc")
    j.mark_finished(a)
    # b is left in-flight
    j.record_part(b, part_index=1, etag="e1", bytes_written=4)
    j.mark_aborted(c)

    unfinished = j.find_unfinished()
    assert {e.transfer_id for e in unfinished} == {b}


def test_purge_removes_file(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    tid = j.begin(source_uri="s", destination_uri="d")
    path = tmp_path / f"{tid}.jsonl"
    assert path.is_file()
    j.purge(tid)
    assert not path.exists()
    # Idempotent.
    j.purge(tid)


def test_purge_missing_is_safe(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    # Seed one real entry so we can assert purge-of-unknown leaves it
    # untouched. Without the survivor a regression that purged
    # everything (e.g. mis-globbing the base_dir) would still pass a
    # "doesn't raise" test.
    survivor = j.begin(source_uri="s", destination_uri="d")
    pre = {e.transfer_id for e in j.find_unfinished()}
    j.purge("deadbeefcafebabe")  # never existed
    post = {e.transfer_id for e in j.find_unfinished()}
    assert pre == post
    assert survivor in post


def test_base_dir_is_created_when_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "down"
    j = TransferJournal(base_dir=nested)
    assert nested.is_dir()
    tid = j.begin(source_uri="s", destination_uri="d")
    assert (nested / f"{tid}.jsonl").is_file()


def test_base_dir_is_private_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX mode bits are not available on this platform")
    nested = tmp_path / "private"

    TransferJournal(base_dir=nested)

    assert stat.S_IMODE(nested.stat().st_mode) == 0o700


def test_corrupt_file_is_skipped(tmp_path: Path) -> None:
    """A malformed jsonl file should not blow up find_unfinished."""
    (tmp_path / "bogus.jsonl").write_text("not json at all\n", encoding="utf-8")
    j = TransferJournal(base_dir=tmp_path)
    # Should not raise.
    assert j.find_unfinished() == []


def test_replay_preserves_upload_id_and_bytes_total(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    tid = j.begin(
        source_uri="local:///tmp/a",
        destination_uri="s3://bkt/key",
        bytes_total=12345,
        upload_id="UP123",
    )
    [entry] = j.find_unfinished()
    assert entry.transfer_id == tid
    assert entry.upload_id == "UP123"
    assert entry.bytes_total == 12345
    assert entry.source_uri == "local:///tmp/a"
    assert entry.destination_uri == "s3://bkt/key"


def test_domain_transfer_journal_does_not_import_infra_layer() -> None:
    source = Path("src/aws_tui/domain/transfer_journal.py").read_text(encoding="utf-8")
    assert "from aws_tui.infra" not in source
    assert "import aws_tui.infra" not in source
