"""Local SQL file boundaries use actual temporary files, no AWS/config state."""

from __future__ import annotations

import importlib
import importlib.util
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def api():
    assert importlib.util.find_spec("aws_tui.infra.sql_authoring_store") is not None, (
        "Bounded local SQL file storage is not implemented"
    )
    return importlib.import_module("aws_tui.infra.sql_authoring_store")


def test_new_file_round_trip_retains_utf8_and_line_endings(tmp_path):
    m = api()
    path = tmp_path / "query.sql"
    sql = "SELECT '🪿';\r\n-- unchanged  \r\n"
    saved = m.SqlFileStore().write(path, sql)
    loaded = m.SqlFileStore().read(path)
    assert loaded == saved
    assert loaded.sql == sql
    assert loaded.path == path.absolute()
    assert path.read_bytes() == sql.encode("utf-8")
    assert "SELECT" not in repr(loaded)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_overwrite_requires_exact_acknowledged_file_version(tmp_path):
    m = api()
    path = tmp_path / "query.sql"
    store = m.SqlFileStore()
    first = store.write(path, "SELECT 1")
    with pytest.raises(m.SqlStorageError, match="exists"):
        store.write(path, "SELECT 2")
    assert path.read_text() == "SELECT 1"
    saved = store.write(path, "SELECT 2", expected=first.version)
    assert saved.sql == "SELECT 2"
    with pytest.raises(m.SqlStorageError, match="changed"):
        store.write(path, "SELECT 3", expected=first.version)
    assert path.read_text() == "SELECT 2"


@pytest.mark.parametrize("payload", [b"x" * 1_048_577, b"\xff\xfe"])
def test_read_limit_and_invalid_utf8_are_recoverable(tmp_path, payload):
    m = api()
    path = tmp_path / "private-query.sql"
    path.write_bytes(payload)
    with pytest.raises(m.SqlStorageError) as error:
        m.SqlFileStore().read(path)
    assert error.value.code in {"limit", "encoding"}
    assert "private-query" not in str(error.value)


def test_exact_read_and_write_byte_limit(tmp_path):
    m = api()
    path = tmp_path / "query.sql"
    sql = "🪿" * (1_048_576 // 4)
    saved = m.SqlFileStore().write(path, sql)
    assert m.SqlFileStore().read(path).sql == sql
    with pytest.raises(m.SqlStorageError, match="limit"):
        m.SqlFileStore().write(path, sql + "x", expected=saved.version)
    assert path.read_bytes() == sql.encode()


@pytest.mark.parametrize("name", ["query.txt", "query", "query.sql.json"])
def test_only_sql_paths_are_accepted(tmp_path, name):
    m = api()
    with pytest.raises(m.SqlStorageError, match=r"\.sql"):
        m.SqlFileStore().write(tmp_path / name, "SELECT 1")
    assert list(tmp_path.iterdir()) == []


def test_uppercase_sql_extension_and_relative_paths_work(tmp_path, monkeypatch):
    m = api()
    monkeypatch.chdir(tmp_path)
    saved = m.SqlFileStore().write(Path("query.SQL"), "SELECT 1")
    assert saved.path == tmp_path / "query.SQL"


def test_missing_file_and_directory_are_fixed_recoverable_errors(tmp_path):
    m = api()
    with pytest.raises(m.SqlStorageError) as missing:
        m.SqlFileStore().read(tmp_path / "missing.sql")
    assert missing.value.code == "missing"
    (tmp_path / "dir.sql").mkdir()
    with pytest.raises(m.SqlStorageError) as directory:
        m.SqlFileStore().read(tmp_path / "dir.sql")
    assert directory.value.code == "path"


def test_symlinks_are_not_followed_for_read_or_save(tmp_path):
    m = api()
    path = tmp_path / "real.sql"
    path.write_text("SELECT 1")
    link = tmp_path / "link.sql"
    try:
        link.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("Native symlink creation unavailable")
    for operation in (
        lambda: m.SqlFileStore().read(link),
        lambda: m.SqlFileStore().write(link, "SELECT 2"),
    ):
        with pytest.raises(m.SqlStorageError):
            operation()
    assert path.read_text() == "SELECT 1"


def test_read_permission_error_is_redacted(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "private.sql"
    path.write_text("SELECT 'private-value'")
    original = m.os.open

    def deny_open(target, *args, **kwargs):
        if Path(target) == path:
            raise PermissionError("private-value private.sql")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(m.os, "open", deny_open)
    with pytest.raises(m.SqlStorageError) as error:
        m.SqlFileStore().read(path)
    assert error.value.code == "permission"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("failure", ["mkstemp", "fsync", "replace"])
def test_write_failure_preserves_original_and_cleans_stage(tmp_path, monkeypatch, failure):
    m = api()
    store = m.SqlFileStore()
    path = tmp_path / "query.sql"
    saved = store.write(path, "SELECT 1")

    def fail(*args, **kwargs):
        raise PermissionError("private SQL or path must not escape")

    monkeypatch.setattr(m.tempfile if failure == "mkstemp" else m.os, failure, fail)
    with pytest.raises(m.SqlStorageError) as error:
        store.write(path, "SELECT 'private'", expected=saved.version)
    assert error.value.code == "permission"
    assert path.read_text() == "SELECT 1"
    assert list(tmp_path.iterdir()) == [path]
    assert "private" not in str(error.value)


def test_cancelled_write_never_publishes(tmp_path):
    m = api()
    permit = m.SqlWritePermit()
    permit.cancel()
    with pytest.raises(m.SqlStorageError, match="cancelled"):
        m.SqlFileStore().write(tmp_path / "query.sql", "SELECT 1", permit=permit)
    assert list(tmp_path.iterdir()) == []


def test_cancellation_after_stage_write_prevents_publication(tmp_path, monkeypatch):
    m = api()
    permit = m.SqlWritePermit()
    original = m.os.fsync

    def fsync_and_cancel(fd):
        original(fd)
        permit.cancel()

    monkeypatch.setattr(m.os, "fsync", fsync_and_cancel)
    with pytest.raises(m.SqlStorageError, match="cancelled"):
        m.SqlFileStore().write(tmp_path / "query.sql", "SELECT 1", permit=permit)
    assert list(tmp_path.iterdir()) == []


def test_new_target_created_during_save_is_never_overwritten(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "query.sql"
    original = m.os.link

    def competing_file(source, target, *args, **kwargs):
        path.write_text("external writer")
        return original(source, target, *args, **kwargs)

    monkeypatch.setattr(m.os, "link", competing_file)
    with pytest.raises(m.SqlStorageError):
        m.SqlFileStore().write(path, "SELECT 1")
    assert path.read_text() == "external writer"
    assert list(tmp_path.iterdir()) == [path]


def test_stale_version_after_external_edit_does_not_overwrite(tmp_path):
    m = api()
    store = m.SqlFileStore()
    path = tmp_path / "query.sql"
    saved = store.write(path, "SELECT 1")
    path.write_text("SELECT 9")
    with pytest.raises(m.SqlStorageError, match="changed"):
        store.write(path, "SELECT 2", expected=saved.version)
    assert path.read_text() == "SELECT 9"


@pytest.mark.parametrize("existing", [False, True])
def test_cancellation_during_final_version_read_blocks_publication(tmp_path, monkeypatch, existing):
    m = api()
    store = m.SqlFileStore()
    path = tmp_path / "query.sql"
    saved = store.write(path, "SELECT 1") if existing else None
    entered, release = threading.Event(), threading.Event()
    original = m._check_target
    calls = 0

    def blocked_final_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(m, "_check_target", blocked_final_check)
    permit = m.SqlWritePermit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            store.write, path, "SELECT 2", expected=saved.version if saved else None, permit=permit
        )
        try:
            assert entered.wait(3)
            permit.cancel()
        finally:
            release.set()
        with pytest.raises(m.SqlStorageError, match="cancelled"):
            future.result(timeout=3)
    assert path.read_text() == "SELECT 1" if existing else not path.exists()
    assert not list(tmp_path.glob(".sql-*"))


@pytest.mark.parametrize("operation", ["read", "write"])
def test_nul_path_is_a_fixed_recoverable_path_error(operation):
    m = api()
    store = m.SqlFileStore()
    action = store.read if operation == "read" else lambda path: store.write(path, "SELECT 1")
    with pytest.raises(m.SqlStorageError) as error:
        action(Path("private\x00.sql"))
    assert error.value.code == "path"
    assert "private" not in str(error.value)


def test_post_publication_stat_failure_is_not_reported_as_a_failed_write(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "query.sql"
    original = Path.stat

    def deny_stat(target, *args, **kwargs):
        if target == path and kwargs.get("follow_symlinks", True):
            raise PermissionError("post-commit metadata unavailable")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_stat)
    saved = m.SqlFileStore().write(path, "SELECT 1")
    assert saved.sql == "SELECT 1"
    assert saved.version is None
    assert path.read_text() == "SELECT 1"


def test_retried_stage_cleanup_does_not_return_stale_version(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "query.sql"
    original = Path.unlink
    attempts = 0

    def transient_stage_failure(target, *args, **kwargs):
        nonlocal attempts
        if target.name.startswith(".sql-"):
            attempts += 1
            if attempts == 1:
                raise PermissionError("retry cleanup")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", transient_stage_failure)
    saved = m.SqlFileStore().write(path, "SELECT 1")
    assert saved.version is None or saved.version == m.SqlFileStore().read(path).version
    assert path.read_text() == "SELECT 1"
    assert not list(tmp_path.glob(".sql-*"))


def test_cancelling_admitted_publication_does_not_block_caller(tmp_path, monkeypatch):
    m = api()
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    original = m.os.link

    def gated_publish(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(m.os, "link", gated_publish)
    permit = m.SqlWritePermit()
    path = tmp_path / "query.sql"

    def cancel():
        permit.cancel()
        cancelled.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(m.SqlFileStore().write, path, "SELECT 1", permit=permit)
        try:
            assert entered.wait(3)
            cancelling = pool.submit(cancel)
            assert cancelled.wait(1), "Cancellation blocked behind filesystem I/O"
        finally:
            release.set()
        result = writing.result(timeout=3)
        cancelling.result(timeout=3)
    assert permit.publication_admitted
    assert result.sql == path.read_text() == "SELECT 1"
