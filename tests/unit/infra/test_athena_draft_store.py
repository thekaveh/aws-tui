"""Real-filesystem persistence, hostile input, and diagnostic privacy evidence."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aws_tui.infra import athena_draft_store as module
from aws_tui.infra.athena_draft_store import (
    AthenaDraftStore,
    DraftPermit,
    DraftStoreResult,
    draft_id,
)
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.infra.log_sink import LogSink
from tests.athena_drafts_helpers import CTX, STAMP, record, store_at


def _enabled(tmp_path):
    store = store_at(tmp_path)
    assert store.set_enabled(True).code is None
    return store


def _path(tmp_path, context=CTX):
    return tmp_path / "athena-drafts" / (draft_id(context) + ".json")


def _wire(context=CTX, sql="SELECT 1"):
    return {
        "schema_version": 1,
        "id": draft_id(context),
        "created_at": "2026-10-05T16:30:00.000000Z",
        "updated_at": "2026-10-05T16:30:00.000000Z",
        "context": dict(zip(module.CONTEXT_KEYS, context, strict=True)),
        "sql": sql,
    }


def _seed(tmp_path, body, context=CTX):
    directory = tmp_path / "athena-drafts"
    directory.mkdir(exist_ok=True)
    path = _path(tmp_path, context)
    path.write_bytes(body if isinstance(body, bytes) else json.dumps(body).encode())
    return path


def test_missing_setting_creates_no_draft_path(tmp_path, monkeypatch):
    store = store_at(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("disabled store touched draft metadata")

    monkeypatch.setattr(module, "_private_directory", forbidden)
    assert store.list() == DraftStoreResult(enabled=False)
    assert store.save(record(), permit=DraftPermit()).code == "disabled"
    assert not (tmp_path / "athena-drafts").exists()


def test_read_only_never_touches_drafts_even_when_setting_true(tmp_path, monkeypatch):
    config = ConfigStore(path=tmp_path / "config.toml")
    config.set_athena_sql_drafts(True)
    store = AthenaDraftStore(
        config=ConfigStore(path=config.path, read_only=True), directory=tmp_path / "athena-drafts"
    )

    def forbidden(*args, **kwargs):
        pytest.fail("read-only store touched draft metadata")

    monkeypatch.setattr(module, "_private_directory", forbidden)
    for result in (
        store.list(),
        store.save(record(), permit=DraftPermit()),
        store.delete(draft_id(CTX)),
        store.clear(),
        store.set_enabled(True),
        store.set_enabled(False),
    ):
        assert result.code == "read_only"
        assert result.enabled is False
    assert not (tmp_path / "athena-drafts").exists()
    assert config.load().athena_sql_drafts is True


def test_five_fields_round_trip_exact_schema_and_private_reprs(tmp_path):
    store = _enabled(tmp_path)
    expected = record(sql="SELECT 'DRAFT_SQL_SENTINEL'")
    result = store.save(expected, permit=DraftPermit())
    assert result.code is None
    assert result.enabled is True
    assert result.records == (expected,)
    assert result.skipped == 0
    assert store_at(tmp_path).list().records == (expected,)
    assert json.loads(_path(tmp_path).read_bytes()) == _wire(sql=expected.sql)
    assert str(expected) == repr(expected) == "SqlDraft()"
    for value in (result, store.list(), store):
        assert "DRAFT_SQL_SENTINEL" not in str(value)
        assert "DRAFT_SQL_SENTINEL" not in repr(value)


def test_separate_instances_preserve_overlapping_context_saves(tmp_path, monkeypatch):
    first, second = _enabled(tmp_path), store_at(tmp_path)
    other = ("analytics", "us-east-1", "other", "federated", "events")
    barrier = threading.Barrier(2)
    original = module._encode_record
    # The public save validation seam precedes transaction acquisition. Meet
    # exactly once per thread here, rather than deadlocking inside the lock.
    local = threading.local()

    def encode(value):
        if not getattr(local, "met", False):
            local.met = True
            barrier.wait(timeout=5)
        return original(value)

    monkeypatch.setattr(module, "_encode_record", encode)
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(first.save, record(), permit=DraftPermit())
        b = executor.submit(second.save, record(other, "SELECT 2"), permit=DraftPermit())
        assert a.result(timeout=5).code is None
        assert b.result(timeout=5).code is None
    assert {row.context for row in first.list().records} == {CTX, other}


def test_replacement_preserves_created_at_and_subtracts_old_budget(tmp_path):
    store = _enabled(tmp_path)
    for index in range(31):
        context = (*CTX[:4], str(index))
        assert (
            store.save(record(context, "x" * module.MAX_SQL_BYTES), permit=DraftPermit()).code
            is None
        )
    target = record((*CTX[:4], "0"), "y" * module.MAX_SQL_BYTES)
    later = replace(
        target, created_at=STAMP + timedelta(seconds=1), updated_at=STAMP + timedelta(seconds=2)
    )
    result = store.save(later, permit=DraftPermit())
    assert result.code is None
    assert len(result.records) == 31
    saved = next(row for row in result.records if row.id == target.id)
    assert saved.created_at == STAMP
    assert saved.updated_at == later.updated_at


def test_total_owned_bytes_limit_preserves_original_bytes(tmp_path):
    store = _enabled(tmp_path)
    for index in range(31):
        assert (
            store.save(
                record((*CTX[:4], str(index)), "x" * module.MAX_SQL_BYTES), permit=DraftPermit()
            ).code
            is None
        )
    before = {p.name: p.read_bytes() for p in (tmp_path / "athena-drafts").iterdir()}
    assert sum(map(len, before.values())) < module.MAX_TOTAL_BYTES
    result = store.save(record((*CTX[:4], "32"), "x" * module.MAX_SQL_BYTES), permit=DraftPermit())
    assert result.code == "limit"
    assert {p.name: p.read_bytes() for p in (tmp_path / "athena-drafts").iterdir()} == before


def test_owned_file_count_limit_includes_corrupt_and_unknown(tmp_path):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    for index in range(49):
        _seed(tmp_path, b"not json", (*CTX[:4], str(index)))
    assert store.list().skipped == 49
    before = _path(tmp_path).read_bytes()
    assert store.save(record((*CTX[:4], "new")), permit=DraftPermit()).code == "limit"
    assert len(tuple((tmp_path / "athena-drafts").iterdir())) == 50
    assert store.save(record(sql="SELECT 2"), permit=DraftPermit()).code is None
    assert _path(tmp_path).read_bytes() != before


@pytest.mark.parametrize("sql", ["é" * 131073, "x" * 262145, "\x00" * 50000])
def test_sql_and_encoded_record_limits_preserve_old_bytes(tmp_path, sql):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    old = _path(tmp_path).read_bytes()
    assert store.save(record(sql=sql), permit=DraftPermit()).code == "limit"
    assert _path(tmp_path).read_bytes() == old
    assert store.list().records == (record(),)


def test_exact_multibyte_sql_limit_is_accepted(tmp_path):
    store = _enabled(tmp_path)
    expected = record(sql="é" * 131072)
    assert store.save(expected, permit=DraftPermit()).code is None
    assert store.list().records == (expected,)


def test_corrupt_unknown_and_oversized_records_do_not_poison_valid_sibling(tmp_path):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    unknown = _wire((*CTX[:4], "unknown"))
    unknown["schema_version"] = 2
    paths = [
        _seed(tmp_path, unknown, (*CTX[:4], "unknown")),
        _seed(tmp_path, b"{", (*CTX[:4], "bad")),
        _seed(tmp_path, b"x" * (module.MAX_RECORD_BYTES + 1), (*CTX[:4], "large")),
    ]
    result = store.list()
    assert result.records == (record(),)
    assert result.skipped == 3
    assert all(path.exists() for path in paths)
    assert store.save(record((*CTX[:4], "unknown")), permit=DraftPermit()).code == "unsupported"


@pytest.mark.parametrize(
    "change",
    [
        {"extra": "credential"},
        {"schema_version": True},
        {"id": "0" * 64},
        {"sql": ""},
        {"sql": "   "},
        {"sql": 1},
        {"sql": "\ud800"},
        {"created_at": "2026-10-05T16:30:01.000000Z"},
        {"created_at": "2026-10-05T16:30:00Z"},
        {"created_at": "2026-10-05T16:30:00.000000+00:00"},
        {"created_at": 1},
        {"context": {}},
        {"context": {**dict(zip(module.CONTEXT_KEYS, CTX, strict=True)), "database": ""}},
        {"context": {**dict(zip(module.CONTEXT_KEYS, CTX, strict=True)), "database": "é" * 513}},
        {"context": {**dict(zip(module.CONTEXT_KEYS, CTX, strict=True)), "database": 2}},
        {"context": {**dict(zip(module.CONTEXT_KEYS, CTX, strict=True)), "extra": "credential"}},
    ],
)
def test_schema_rejects_invalid_owned_payload(tmp_path, change):
    store = _enabled(tmp_path)
    path = _seed(tmp_path, {**_wire(), **change})
    before = path.read_bytes()
    assert store.list().records == ()
    assert store.list().skipped == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"sql":NaN}',
        b'{"sql":Infinity}',
        b"\xff",
        b'{"schema_version":1,"schema_version":1}',
        b'{"context":{"database":"a","database":"b"}}',
    ],
)
def test_json_rejects_duplicates_constants_invalid_utf8(tmp_path, payload):
    store = _enabled(tmp_path)
    _seed(tmp_path, payload)
    assert store.list().skipped == 1
    assert store.list().records == ()


@pytest.mark.parametrize(
    "change",
    [
        {"id": "../escape"},
        {"context": (*CTX[:4], "")},
        {"context": (*CTX[:4], "\ud800")},
        {"sql": " "},
        {"created_at": STAMP + timedelta(seconds=1)},
        {"created_at": datetime(2026, 10, 5)},
    ],
)
def test_save_rejects_invalid_record_without_changing_bytes(tmp_path, change):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    before = _path(tmp_path).read_bytes()
    assert store.save(replace(record(), **change), permit=DraftPermit()).code == "invalid"
    assert _path(tmp_path).read_bytes() == before


def test_delete_clear_disable_return_current_listing_and_preserve_unowned(tmp_path):
    store = _enabled(tmp_path)
    second = record((*CTX[:4], "other"))
    assert store.save(record(), permit=DraftPermit()).code is None
    assert store.save(second, permit=DraftPermit()).code is None
    unrelated = tmp_path / "athena-drafts" / "keep.txt"
    unrelated.write_text("keep")
    directory = tmp_path / "athena-drafts" / "not-owned"
    directory.mkdir()
    temp = tmp_path / "athena-drafts" / ".draft-orphan.tmp"
    temp.write_text("old SQL")
    result = store.delete(draft_id(CTX))
    assert result == DraftStoreResult(records=(second,), enabled=True)
    assert not _path(tmp_path).exists()
    assert not temp.exists()
    assert store.delete("../keep.txt").code == "invalid"
    assert store.clear() == DraftStoreResult(enabled=True)
    assert unrelated.read_text() == "keep"
    assert directory.is_dir()
    assert store.save(record(), permit=DraftPermit()).code is None
    assert store.set_enabled(False) == DraftStoreResult(enabled=False)
    assert ConfigStore(path=tmp_path / "config.toml").load().athena_sql_drafts is False
    assert not _path(tmp_path).exists()
    assert store.save(record(), permit=DraftPermit()).code == "disabled"


def test_clear_removes_empty_directory_and_owned_symlink_only(tmp_path):
    store = _enabled(tmp_path)
    outside = tmp_path / "sentinel"
    outside.write_text("outside")
    directory = tmp_path / "athena-drafts"
    directory.mkdir(exist_ok=True)
    _path(tmp_path).symlink_to(outside)
    assert store.clear().code is None
    assert outside.read_text() == "outside"
    assert not directory.exists()


def test_disable_failure_reports_confirmed_off_and_can_retry(tmp_path, monkeypatch):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    original = Path.unlink

    def refuse(path, *args, **kwargs):
        if path == _path(tmp_path):
            raise OSError("DRAFT_SQL_SENTINEL")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    result = store.set_enabled(False)
    assert result.code == "io"
    assert result.enabled is False
    assert _path(tmp_path).exists()
    monkeypatch.setattr(Path, "unlink", original)
    assert store.set_enabled(False) == DraftStoreResult(enabled=False)
    assert not (tmp_path / "athena-drafts").exists()


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_enable_initialization_failure_rolls_back_or_reports_unknown(
    tmp_path, monkeypatch, rollback_fails
):
    config = ConfigStore(path=tmp_path / "config.toml")
    store = AthenaDraftStore(config=config, directory=tmp_path / "athena-drafts")
    original = config.set_athena_sql_drafts

    def setting(enabled):
        if not enabled and rollback_fails:
            raise OSError("DRAFT_SQL_SENTINEL")
        original(enabled)

    def fail(*args, **kwargs):
        raise OSError("DRAFT_SQL_SENTINEL")

    monkeypatch.setattr(config, "set_athena_sql_drafts", setting)
    monkeypatch.setattr(module, "_private_directory", fail)
    result = store.set_enabled(True)
    assert result.code == "io"
    # If rollback fails, rereading the setting confirms it remains true.
    assert result.enabled is rollback_fails
    assert config.load().athena_sql_drafts is rollback_fails


def test_enable_config_and_confirmation_failure_reports_unknown(tmp_path, monkeypatch):
    config = ConfigStore(path=tmp_path / "config.toml")
    store = AthenaDraftStore(config=config, directory=tmp_path / "athena-drafts")

    def fail(*args):
        raise OSError("DRAFT_SQL_SENTINEL")

    monkeypatch.setattr(config, "load", fail)
    assert store.set_enabled(True) == DraftStoreResult(code="io", enabled=None)


@pytest.mark.skipif(os.name != "posix", reason="POSIX private permissions")
def test_private_directory_record_temp_modes_and_flush_before_replace(tmp_path, monkeypatch):
    store = _enabled(tmp_path)
    original = os.replace
    observed = []

    def replace_record(src, dst):
        if str(src).endswith(".tmp") and Path(src).name.startswith(".draft-"):
            observed.append(stat.S_IMODE(Path(src).stat().st_mode))
            assert Path(src).read_bytes() == module._encode_record(record())
        return original(src, dst)

    monkeypatch.setattr(os, "replace", replace_record)
    assert store.save(record(), permit=DraftPermit()).code is None
    assert observed == [0o600]
    assert stat.S_IMODE((tmp_path / "athena-drafts").stat().st_mode) == 0o700
    assert stat.S_IMODE(_path(tmp_path).stat().st_mode) == 0o600
    _path(tmp_path).chmod(0o644)
    assert store.list().records == (record(),)
    assert stat.S_IMODE(_path(tmp_path).stat().st_mode) == 0o600


def test_failed_replace_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    before = _path(tmp_path).read_bytes()

    def fail(*args):
        raise OSError("DRAFT_SQL_SENTINEL")

    monkeypatch.setattr(os, "replace", fail)
    assert store.save(record(sql="SELECT 2"), permit=DraftPermit()).code == "io"
    assert _path(tmp_path).read_bytes() == before
    assert store.list().records == (record(),)
    assert not tuple((tmp_path / "athena-drafts").glob(".draft-*.tmp"))


@pytest.mark.parametrize(
    "phase",
    ["before-entry", "before-replace", "during-directory-validation", "inside-replace"],
)
def test_permit_cancellation_preserves_pre_replace_record(tmp_path, monkeypatch, phase):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    permit = DraftPermit()
    if phase == "before-entry":
        permit.cancel()
    elif phase == "before-replace":
        original = os.fsync

        def sync(fd):
            original(fd)
            permit.cancel()

        monkeypatch.setattr(os, "fsync", sync)
    elif phase == "during-directory-validation":
        original_directory_check = module._private_directory
        original_replace = os.replace
        replacements = []

        def validate_directory(directory, *, create):
            result = original_directory_check(directory, create=create)
            if tuple(directory.glob(".draft-*.tmp")):
                permit.cancel()
            return result

        def replace_record(src, dst):
            replacements.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(module, "_private_directory", validate_directory)
        monkeypatch.setattr(os, "replace", replace_record)
    else:
        original = os.replace

        def replace_entered(src, dst):
            # An already-entered replace cannot be rolled back by cancellation.
            permit.cancel()
            original(src, dst)

        monkeypatch.setattr(os, "replace", replace_entered)
    result = store.save(record(sql="SELECT 2"), permit=permit)
    assert permit.cancelled
    assert result.code == (None if phase == "inside-replace" else "cancelled")
    assert store.list().records == (
        record(sql="SELECT 2" if phase == "inside-replace" else "SELECT 1"),
    )
    assert not tuple((tmp_path / "athena-drafts").glob(".draft-*.tmp"))
    if phase == "during-directory-validation":
        assert replacements == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO/socket and no-follow flags")
def test_nonregular_owned_files_are_skipped_without_opening_and_counted(tmp_path, monkeypatch):
    store = _enabled(tmp_path)
    assert store.save(record(), permit=DraftPermit()).code is None
    outside = tmp_path / "sentinel"
    outside.write_text("outside")
    fifo = _path(tmp_path, (*CTX[:4], "fifo"))
    os.mkfifo(fifo)
    link = _path(tmp_path, (*CTX[:4], "link"))
    link.symlink_to(outside)
    sockpath = _path(tmp_path, (*CTX[:4], "socket"))
    sock = socket.socket(socket.AF_UNIX)
    # macOS AF_UNIX limits path text to 104 bytes; bind with a short relative
    # name and rename the socket itself into the canonical owned fixture.
    monkeypatch.chdir(tmp_path)
    sock.bind("draft.socket")
    (tmp_path / "draft.socket").replace(sockpath)
    original = os.open
    opened = []

    def open_record(path, flags, *args, **kwargs):
        if Path(path).parent.name == "athena-drafts":
            opened.append((Path(path), flags))
            assert Path(path) not in (fifo, link, sockpath)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_record)
    try:
        result = store.list()
        assert result.records == (record(),)
        assert result.skipped == 3
        assert opened
        for path, flags in opened:
            assert path == _path(tmp_path)
            if hasattr(os, "O_NONBLOCK"):
                assert flags & os.O_NONBLOCK
            if hasattr(os, "O_NOFOLLOW"):
                assert flags & os.O_NOFOLLOW
        assert outside.read_text() == "outside"
    finally:
        sock.close()


def test_symlink_directory_refused_before_private_dir_helper(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "sentinel").write_text("outside")
    (tmp_path / "athena-drafts").symlink_to(outside, target_is_directory=True)
    store = store_at(tmp_path)

    def forbidden(path):
        pytest.fail("directory link reached ensure_private_dir")

    monkeypatch.setattr(module, "ensure_private_dir", forbidden)
    assert store.set_enabled(True).code == "io"
    assert ConfigStore(path=tmp_path / "config.toml").load().athena_sql_drafts is False
    assert (outside / "sentinel").read_text() == "outside"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755


def _diagnostic_artifacts(result, tmp_path):
    # This fresh error is created after the result-only store discarded every
    # original exception; no SQL/raw payload is in this helper's locals.
    try:
        raise RuntimeError("Athena draft operation was not confirmed")
    except RuntimeError as error:
        sink = LogSink(base_dir=tmp_path / "log")
        sink.error("athena.drafts.failure", code=result.code, skipped=result.skipped)
        sink.close()
        crash = (
            CrashDump(base_dir=tmp_path / "crash").write(exc=error, log_path=sink.path).read_text()
        )
        trace = "".join(
            traceback.TracebackException.from_exception(error, capture_locals=True).format()
        )
        return error, trace, crash, sink.path.read_text()


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "malformed",
        "utf8",
        "surrogate",
        "unsupported",
        "hostile-context",
        "over-limit",
        "oserror",
    ],
)
def test_draft_diagnostics_never_include_payload(tmp_path, monkeypatch, case):
    sentinel = "DRAFT_SQL_SENTINEL"
    store = _enabled(tmp_path)
    if case == "valid":
        result = store.save(record(sql=sentinel), permit=DraftPermit())
        assert result.records[0].sql == sentinel
    elif case == "over-limit":
        result = store.save(record(sql=sentinel + "x" * module.MAX_SQL_BYTES), permit=DraftPermit())
        assert result.code == "limit"
    elif case == "oserror":

        def fail(*args):
            raise OSError("DRAFT_SQL_SENTINEL")

        monkeypatch.setattr(os, "replace", fail)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(store.save, record(sql=sentinel), permit=DraftPermit())
            result = future.result(timeout=5)
            assert future.exception() is None
            assert result.code == "io"
    else:
        body = _wire(sql=sentinel)
        if case == "malformed":
            body = b'{"sql":"DRAFT_SQL_SENTINEL"'
        elif case == "utf8":
            body = b"\xffDRAFT_SQL_SENTINEL"
        elif case == "surrogate":
            body["sql"] = sentinel + "\ud800"
        elif case == "unsupported":
            body["schema_version"] = 2
        elif case == "hostile-context":
            body["context"]["database"] = sentinel + "\ud800"
        _seed(tmp_path, body)
        result = store.list()
        assert result.records == ()
        assert result.skipped == 1
    assert {f.name for f in fields(result)} == {"records", "code", "skipped", "enabled"}
    assert not any(isinstance(getattr(result, f.name), BaseException) for f in fields(result))
    error, trace, crash, log = _diagnostic_artifacts(result, tmp_path)
    artifacts = [str(result), repr(result), str(error), repr(error), trace, crash, log]
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        artifacts.extend([str(current), repr(current), repr(getattr(current, "__notes__", ()))])
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(sentinel not in text for text in artifacts)
