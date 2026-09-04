"""Unit tests for TransferJournal."""

from __future__ import annotations

import json
import os
import shutil
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


def test_rejects_invalid_transfer_id_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="16 lowercase hexadecimal"):
        TransferJournal(base_dir=tmp_path).purge("../../outside")


def test_replay_skips_embedded_id_that_does_not_match_filename(tmp_path: Path) -> None:
    path = tmp_path / "0123456789abcdef.jsonl"
    path.write_text(
        json.dumps(
            {
                "kind": "begin",
                "transfer_id": "fedcba9876543210",
                "source_uri": "s",
                "destination_uri": "d",
                "ts": "2026-01-01T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert TransferJournal(base_dir=tmp_path).find_unfinished() == []


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


def test_journal_file_is_private_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX mode bits are not available on this platform")
    j = TransferJournal(base_dir=tmp_path)

    tid = j.begin(source_uri="src://a", destination_uri="dst://a")

    path = tmp_path / f"{tid}.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_journal_file_is_created_with_private_mode_on_posix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX mode bits are not available on this platform")
    modes: list[int] = []
    real_open = os.open

    def recording_open(path: str, flags: int, mode: int = 0o777) -> int:
        modes.append(mode)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", recording_open)
    j = TransferJournal(base_dir=tmp_path)

    j.begin(source_uri="src://a", destination_uri="dst://a")

    assert 0o600 in modes


def test_corrupt_file_is_skipped(tmp_path: Path) -> None:
    """A malformed jsonl file should not blow up find_unfinished."""
    (tmp_path / "bogus.jsonl").write_text("not json at all\n", encoding="utf-8")
    j = TransferJournal(base_dir=tmp_path)
    # Should not raise.
    assert j.find_unfinished() == []


@pytest.mark.parametrize("record", [None, [], "text", 42])
def test_valid_json_non_object_record_is_skipped(tmp_path: Path, record: object) -> None:
    (tmp_path / "0123456789abcdef.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )

    assert TransferJournal(base_dir=tmp_path).find_unfinished() == []


def test_malformed_part_field_type_is_skipped(tmp_path: Path) -> None:
    journal = TransferJournal(base_dir=tmp_path)
    transfer_id = journal.begin(source_uri="s", destination_uri="d")
    path = tmp_path / f"{transfer_id}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "part", "part_index": [], "etag": "e"}) + "\n")

    assert journal.find_unfinished() == []


def test_replay_preserves_valid_records_before_torn_trailing_line(tmp_path: Path) -> None:
    j = TransferJournal(base_dir=tmp_path)
    tid = j.begin(source_uri="s", destination_uri="d", bytes_total=10)
    path = tmp_path / f"{tid}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"kind":"part"')

    [entry] = j.find_unfinished()

    assert entry.transfer_id == tid
    assert entry.bytes_total == 10


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


def test_find_unfinished_skips_an_unreadable_journal(tmp_path: Path) -> None:
    """An unreadable journal must not abort the whole scan.

    `_iter_jsonl` opens each file lazily during replay, so a journal removed by
    a second instance between `glob()` and the read — or one failing with
    EACCES/EIO — raised out of the loop. That contradicts the handler's own
    documented contract of skipping a bad journal and letting the caller decide.
    """
    journal = TransferJournal(base_dir=tmp_path)
    good = journal.begin(
        source_uri="s3://bucket/object",
        destination_uri="file:///tmp/object",
        bytes_total=1,
    )
    unreadable = tmp_path / f"{good[:-4]}beef.jsonl"
    shutil.copy(tmp_path / f"{good}.jsonl", unreadable)
    unreadable.chmod(0o000)

    try:
        entries = journal.find_unfinished()
    finally:
        unreadable.chmod(0o600)

    assert [entry.transfer_id for entry in entries] == [good]


def _append_marker_without_purging(journal: TransferJournal, transfer_id: str, kind: str) -> None:
    """Reproduce the crash window: the terminal marker is on disk, the purge
    never ran. ``mark_finished``/``mark_aborted`` append and then immediately
    purge, so tests that call them assert on an EMPTY directory and the replay
    path they are named for never executes."""
    journal._append(transfer_id, {"kind": kind, "ts": "2026-09-04T00:00:00+00:00"})


def test_replay_skips_a_journal_whose_finished_marker_survived_a_crash(
    tmp_path: Path,
) -> None:
    """The on-disk `finished` marker must be honoured by replay.

    This is the crash-safety contract the write-then-unlink ordering exists
    for: a crash between the append and the purge leaves the file behind, and
    replay must read the marker and NOT offer the transfer as resumable.
    Neutralising either the `finished = True` assignment or the filter's
    `entry.finished` operand survived the whole suite, because the existing
    exclusion tests call `mark_finished`, which purges — they assert on an
    empty directory.
    """
    journal = TransferJournal(base_dir=tmp_path)
    transfer_id = journal.begin(source_uri="s", destination_uri="d")
    journal.record_part(transfer_id, part_index=1, etag="e", bytes_written=10)
    _append_marker_without_purging(journal, transfer_id, "finished")
    assert journal._path_for(transfer_id).exists(), "precondition: the file survived"

    assert journal.find_unfinished() == [], (
        "a transfer whose finished marker is on disk was offered for resume"
    )


def test_replay_skips_a_journal_whose_aborted_marker_survived_a_crash(
    tmp_path: Path,
) -> None:
    journal = TransferJournal(base_dir=tmp_path)
    transfer_id = journal.begin(source_uri="s", destination_uri="d")
    _append_marker_without_purging(journal, transfer_id, "aborted")

    assert journal.find_unfinished() == []


def test_replay_still_surfaces_a_genuinely_unfinished_journal(tmp_path: Path) -> None:
    """Positive control: no terminal marker on disk means resumable."""
    journal = TransferJournal(base_dir=tmp_path)
    transfer_id = journal.begin(source_uri="s", destination_uri="d")
    journal.record_part(transfer_id, part_index=1, etag="e", bytes_written=10)

    unfinished = journal.find_unfinished()
    assert [entry.transfer_id for entry in unfinished] == [transfer_id]
    assert unfinished[0].completed_parts == (1,)


def test_a_corrupt_line_mid_journal_does_not_hide_a_later_terminal_marker(
    tmp_path: Path,
) -> None:
    """Only a torn FINAL line is tolerated; corruption mid-file must not
    truncate replay.

    With the tolerance widened to any corrupt line, a journal holding a torn
    `part` record followed by a good `aborted` marker replayed as UNFINISHED —
    offering a resume for a transfer the user aborted. The current behaviour
    skips the unreadable file entirely (the earlier skip-unreadable fix), which
    surfaces nothing rather than something wrong.
    """
    journal = TransferJournal(base_dir=tmp_path)
    transfer_id = journal.begin(source_uri="s", destination_uri="d")
    _append_marker_without_purging(journal, transfer_id, "aborted")
    path = journal._path_for(transfer_id)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    # Corrupt the middle: tear the part record but KEEP its newline.
    lines.insert(1, '{"kind":"part","part_index":1,"etag":"TORN\n')
    path.write_text("".join(lines), encoding="utf-8")

    assert journal.find_unfinished() == [], (
        "a corrupt mid-file line hid the aborted marker and offered a resume"
    )


def test_directory_fsync_happens_on_creation_and_only_on_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves of `created = not path.exists()` matter.

    The directory entry only changes when the file is created, so the dir
    fsync must fire exactly once per journal — on `begin`. Inverting the flag
    survived: the new journal's directory entry was never made durable (a crash
    could lose the file's very existence), and every later append re-paid the
    fsync the comment above it says was deliberately removed (measured at 400
    for 200 entries before that fix).
    """
    from aws_tui.domain import transfer_journal as tj_module

    directory_syncs: list[Path] = []
    real = tj_module._fsync_directory

    def recording(path: Path) -> None:
        directory_syncs.append(path)
        real(path)

    monkeypatch.setattr(tj_module, "_fsync_directory", recording)

    journal = TransferJournal(base_dir=tmp_path)
    transfer_id = journal.begin(source_uri="s", destination_uri="d")
    assert len(directory_syncs) == 1, (
        "the new journal's directory entry was not made durable on creation"
    )

    journal.record_part(transfer_id, part_index=1, etag="e", bytes_written=10)
    journal.record_part(transfer_id, part_index=2, etag="f", bytes_written=10)
    assert len(directory_syncs) == 1, (
        f"{len(directory_syncs) - 1} extra directory fsyncs on appends — the "
        "per-append cost the creation flag exists to avoid is back"
    )
