"""Durable, bounded transfer metadata; no transfer bytes are owned here."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aws_tui.domain.transfer_history import (
    MAX_METADATA_BYTES,
    TransferConnectionIdentity,
    TransferHistoryRecord,
    TransferHistoryStore,
    read_metadata,
)

pytestmark = pytest.mark.unit


def history_record(index: int = 1, **changes: object) -> TransferHistoryRecord:
    moment = datetime(2026, 10, 4, tzinfo=UTC) + timedelta(seconds=index)
    record = TransferHistoryRecord(
        id=f"{index:016x}",
        operation="copy",
        source_connection=TransferConnectionIdentity("local", "Local", "a" * 64),
        destination_connection=TransferConnectionIdentity("aws", "Production", "b" * 64),
        source_uri="file:///tmp/a [literal] name",
        destination_uri="s3://bucket/a [literal] name",
        started_at=moment,
        updated_at=moment,
        finished_at=moment,
        bytes_done=0,
        bytes_total=None,
        status="completed",
        publication="confirmed_terminal",
        failure_reason=None,
    )
    return replace(record, **changes)


def test_newest_records_survive_reload(tmp_path: Path) -> None:
    store = TransferHistoryStore(base_dir=tmp_path, retention_limit=2)
    for index in range(3):
        store.save(history_record(index))
    assert [r.id for r in TransferHistoryStore(base_dir=tmp_path).load()] == [
        "0000000000000002",
        "0000000000000001",
    ]


@pytest.mark.parametrize("status", ["completed", "skipped", "failed", "cancelled"])
def test_all_terminal_fields_survive_restart(tmp_path: Path, status: str) -> None:
    record = history_record(status=status, bytes_total=0, failure_reason="provider_error")
    TransferHistoryStore(base_dir=tmp_path).save(record)
    assert TransferHistoryStore(base_dir=tmp_path).load() == (record,)
    with pytest.raises(FrozenInstanceError):
        record.status = "failed"  # type: ignore[misc]


def test_unknown_zero_and_empty_name_are_distinct(tmp_path: Path) -> None:
    unknown = history_record(1)
    zero = history_record(
        2, bytes_total=0, destination_connection=TransferConnectionIdentity("aws", "", "b" * 64)
    )
    store = TransferHistoryStore(base_dir=tmp_path)
    store.save(unknown)
    store.save(zero)
    assert store.load() == (zero, unknown)


def test_ties_order_by_id_and_instances_preserve_unrelated_records(tmp_path: Path) -> None:
    first = TransferHistoryStore(base_dir=tmp_path)
    second = TransferHistoryStore(base_dir=tmp_path)
    older = history_record(1)
    newer = history_record(
        2, updated_at=older.updated_at, started_at=older.started_at, finished_at=older.finished_at
    )
    first.save(older)
    second.save(newer)
    assert first.load() == (newer, older)


def test_default_retention_is_100(tmp_path: Path) -> None:
    store = TransferHistoryStore(base_dir=tmp_path)
    for index in range(101):
        store.save(history_record(index))
    assert len(store.load()) == 100
    assert store.load()[-1].id == "0000000000000001"


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../outside"},
        {"operation": "upload"},
        {"bytes_done": True},
        {"bytes_done": -1},
        {"bytes_total": "0"},
        {"status": "pending"},
        {"publication": "safe"},
        {"failure_reason": "secret exception at https://endpoint"},
        {"started_at": datetime(2026, 10, 4)},
        {"updated_at": "2026-10-04"},
        {"destination_uri": "https://user:password@host/path"},
        {"destination_uri": "https://bucket.example/key?X-Amz-Credential=secret"},
        {"destination_uri": "s3://user:password@bucket/key"},
    ],
)
def test_invalid_metadata_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        history_record(**changes)


def test_identity_requires_opaque_hash() -> None:
    with pytest.raises(ValueError, match="SHA256"):
        TransferConnectionIdentity("aws", "prod", "https://secret-endpoint")


def test_atomic_failed_replace_preserves_old_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TransferHistoryStore(base_dir=tmp_path)
    previous = history_record()
    store.save(previous)

    def fail_replace(*args: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        store.save(replace(previous, bytes_done=10))
    assert store.load() == (previous,)
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_skips_bad_files_and_clear_preserves_unowned_bytes(tmp_path: Path) -> None:
    store = TransferHistoryStore(base_dir=tmp_path)
    record = history_record()
    store.save(record)
    good = tmp_path / f"{record.id}.json"
    bad_data = ["{", "null", '{"schema_version":0}', "x" * (MAX_METADATA_BYTES + 1)]
    for index, data in enumerate(bad_data, 10):
        (tmp_path / f"{index:016x}.json").write_text(data)
    mismatched = json.loads(good.read_text())
    (tmp_path / "ffffffffffffffff.json").write_text(json.dumps(mismatched))
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"source bytes")
    destination.write_bytes(b"destination bytes")
    unowned = tmp_path / "unrelated.json"
    unowned.write_text("{}")
    unreadable = tmp_path / "eeeeeeeeeeeeeeee.json"
    unreadable.write_text(good.read_text())
    unreadable.chmod(0)
    link = tmp_path / "dddddddddddddddd.json"
    link.symlink_to(good)
    try:
        assert store.load() == (record,)
        store.clear()
        assert store.load() == ()
        assert not good.exists()
        assert source.read_bytes() == b"source bytes"
        assert destination.read_bytes() == b"destination bytes"
        assert unowned.exists()
        assert unreadable.exists()
        assert link.is_symlink()
        assert (tmp_path / "000000000000000a.json").exists()
    finally:
        unreadable.chmod(0o600)


def test_private_permissions_and_no_symlink_write(tmp_path: Path) -> None:
    store = TransferHistoryStore(base_dir=tmp_path / "history")
    record = history_record()
    store.save(record)
    target = tmp_path / "history" / f"{record.id}.json"
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    target.unlink()
    outside = tmp_path / "outside"
    outside.write_text("preserve")
    target.symlink_to(outside)
    with pytest.raises((OSError, ValueError)):
        store.save(record)
    assert outside.read_text() == "preserve"


def test_symlink_directory_is_refused(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        TransferHistoryStore(base_dir=linked)


def test_summary_contains_only_whitelisted_fields(tmp_path: Path) -> None:
    store = TransferHistoryStore(base_dir=tmp_path)
    store.save(history_record())
    text = (tmp_path / "0000000000000001.json").read_text()
    for secret in ("aws_secret_access_key", "upload_id", "endpoint_url", "headers", "client"):
        assert secret not in text


def test_concurrent_instances_do_not_lose_other_records(tmp_path: Path) -> None:
    stores = [TransferHistoryStore(tmp_path) for _ in range(4)]
    readiness = threading.Barrier(4)

    def write(index: int) -> None:
        readiness.wait(timeout=3)
        stores[index].save(history_record(index))

    with ThreadPoolExecutor(max_workers=4) as workers:
        completed = [workers.submit(write, index) for index in range(4)]
        for operation in completed:
            operation.result(timeout=3)
    assert [record.id for record in stores[0].load()] == [
        f"{index:016x}" for index in reversed(range(4))
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("bytes_done", True),
        ("bytes_total", "0"),
        ("started_at", "2026-10-04"),
        ("finished_at", None),
        ("extra_secret", "aws_secret_access_key"),
    ],
)
def test_strict_disk_schema_skips_invalid_summary(
    tmp_path: Path, field: str, value: object
) -> None:
    store = TransferHistoryStore(tmp_path)
    record = history_record()
    store.save(record)
    path = tmp_path / f"{record.id}.json"
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    assert store.load() == ()
    store.clear()
    assert path.exists()


def test_deep_or_duplicate_json_does_not_hide_healthy_summary(tmp_path: Path) -> None:
    store = TransferHistoryStore(tmp_path)
    record = history_record()
    store.save(record)
    (tmp_path / "0000000000000002.json").write_text("[" * 30000 + "0" + "]" * 30000)
    original = (tmp_path / f"{record.id}.json").read_text()
    duplicate = original.replace(
        '"id":"0000000000000001"', '"id":"0000000000000002","id":"0000000000000003"'
    )
    (tmp_path / "0000000000000003.json").write_text(duplicate)
    assert store.load() == (record,)


def test_clear_reports_disk_failure_without_claiming_record_was_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TransferHistoryStore(tmp_path)
    record = history_record()
    store.save(record)
    original_unlink = Path.unlink
    target = tmp_path / f"{record.id}.json"

    def fail_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == target:
            raise OSError("clear disk failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError, match="clear disk failure"):
        store.clear()
    assert store.load() == (record,)


@pytest.mark.parametrize("prefix", ["/tmp/", "file:///tmp/", "local:///tmp/"])
def test_literal_path_punctuation_round_trips_without_url_transformation(
    tmp_path: Path, prefix: str
) -> None:
    literal = "a?question#hash%2F%25 [brackets] spaces.txt"
    record = history_record(source_uri=prefix + literal, destination_uri="s3://bucket/" + literal)
    store = TransferHistoryStore(tmp_path)
    store.save(record)
    assert TransferHistoryStore(tmp_path).load() == (record,)
    assert record.source_uri == prefix + literal
    assert record.destination_uri == "s3://bucket/" + literal


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes unavailable")
def test_fifo_summary_is_skipped_with_a_bounded_scan(tmp_path: Path) -> None:
    store = TransferHistoryStore(tmp_path)
    record = history_record()
    store.save(record)
    fifo = tmp_path / "0000000000000002.json"
    os.mkfifo(fifo)
    script = """
import sys
from pathlib import Path
from aws_tui.domain.transfer_history import TransferHistoryStore
store = TransferHistoryStore(Path(sys.argv[1]))
assert [record.id for record in store.load()] == [sys.argv[2]]
store.clear()
assert store.load() == ()
assert (Path(sys.argv[1]) / '0000000000000002.json').exists()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), record.id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ | {"PYTHONPATH": str(Path("src").absolute())},
    )
    scan_deadline_seconds = 3
    try:
        stdout, stderr = child.communicate(timeout=scan_deadline_seconds)
        assert child.returncode == 0, stdout + stderr
    except subprocess.TimeoutExpired:
        pytest.fail("regular-file summary scan blocked on FIFO")
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=3)


@pytest.mark.parametrize("node_kind", ["directory", "symlink", "fifo"])
def test_portable_read_precheck_refuses_nonregular_without_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, node_kind: str
) -> None:
    target = tmp_path / "metadata"
    if node_kind == "directory":
        target.mkdir()
    elif node_kind == "symlink":
        regular = tmp_path / "regular"
        regular.write_text("{}")
        target.symlink_to(regular)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("named pipes unavailable")
        os.mkfifo(target)
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    def unexpected_open(*args: object) -> int:
        pytest.fail("portable regular-file precheck opened a nonregular entry")

    monkeypatch.setattr(os, "open", unexpected_open)
    with pytest.raises(ValueError, match="nonregular"):
        read_metadata(target)


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
    reason="POSIX FIFO flags unavailable",
)
def test_reader_rechecks_descriptor_when_regular_file_is_replaced_by_fifo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "metadata"
    target.write_text("{}")
    real_open = os.open

    def replace_before_open(path: str | Path, flags: int, mode: int = 0o777) -> int:
        assert flags & os.O_NONBLOCK, "would block after regular-file precheck"
        if hasattr(os, "O_NOFOLLOW"):
            assert flags & os.O_NOFOLLOW
        target.unlink()
        os.mkfifo(target)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(ValueError, match="nonregular"):
        read_metadata(target)
