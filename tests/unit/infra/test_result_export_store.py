"""Local-only contracts for bounded, guarded result export staging."""

from __future__ import annotations

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from aws_tui.infra import result_export_store as module
from aws_tui.infra.result_export_store import ExportPermit, ExportStorageError, ResultExportStore


def test_new_export_streams_private_chunks_and_cleans_stage(tmp_path):
    store = ResultExportStore()
    destination = store.inspect(tmp_path / "result.json")
    assert destination.version is None
    stage = store.begin(destination)
    staged = list(tmp_path.iterdir())
    assert len(staged) == 1
    assert stat.S_IMODE(staged[0].stat().st_mode) == 0o600
    for _ in range(64):
        stage.write(b"x" * 65536)
    assert not destination.path.exists()
    assert stage.publish() == 64 * 65536
    assert stage.published
    assert destination.path.read_bytes() == b"x" * (64 * 65536)
    stage.abort()
    assert list(tmp_path.iterdir()) == [destination.path]


def test_approved_existing_version_is_replaced(tmp_path):
    target = tmp_path / "result.csv"
    target.write_bytes(b"old")
    store = ResultExportStore()
    destination = store.inspect(target)
    stage = store.begin(destination)
    stage.write(b"new")
    assert target.read_bytes() == b"old"
    assert stage.publish() == 3
    assert target.read_bytes() == b"new"


def test_existing_target_without_acknowledged_version_is_refused(tmp_path):
    target = tmp_path / "result.csv"
    target.write_bytes(b"old")
    store = ResultExportStore()
    destination = replace(store.inspect(target), version=None)
    with pytest.raises(ExportStorageError, match="confirm") as caught:
        store.begin(destination)
    assert caught.value.code == "exists"
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("when", ["begin", "publish"])
def test_changed_regular_target_is_refused(tmp_path, when):
    target = tmp_path / "result.csv"
    target.write_bytes(b"old")
    store = ResultExportStore()
    destination = store.inspect(target)
    stage = store.begin(destination) if when == "publish" else None
    if stage is not None:
        stage.write(b"new")
    attempt = (lambda: store.begin(destination)) if stage is None else stage.publish
    target.write_bytes(b"changed")
    with pytest.raises(ExportStorageError) as caught:
        attempt()
    assert caught.value.code == "changed"
    assert target.read_bytes() == b"changed"
    assert list(tmp_path.iterdir()) == [target]


def test_competing_create_at_publication_is_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "result.csv"
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    stage.write(b"ours")
    real_link = os.link

    def competing_link(*args, **kwargs):
        target.write_bytes(b"theirs")
        return real_link(*args, **kwargs)

    monkeypatch.setattr(module.os, "link", competing_link)
    with pytest.raises(ExportStorageError) as caught:
        stage.publish()
    assert caught.value.code == "exists"
    assert target.read_bytes() == b"theirs"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_nonregular_targets_are_refused(tmp_path, kind):
    target = tmp_path / "sensitive-name"
    if kind == "symlink":
        target.symlink_to(tmp_path / "missing")
    elif kind == "directory":
        target.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is POSIX-only")
        os.mkfifo(target)
    with pytest.raises(ExportStorageError) as caught:
        ResultExportStore().inspect(target)
    assert caught.value.code == "path"
    assert str(target) not in str(caught.value)


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_parent_is_refused_without_mkdir(tmp_path, kind):
    parent = tmp_path / "parent"
    if kind == "file":
        parent.write_bytes(b"not a directory")
    with pytest.raises(ExportStorageError) as caught:
        ResultExportStore().inspect(parent / "result")
    assert caught.value.code == "path"
    assert parent.exists() is (kind == "file")


def test_parent_alias_is_resolved_once_but_filename_is_not_followed(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    store = ResultExportStore()
    destination = store.inspect(alias / "result")
    assert destination.path == real / "result"
    alias.unlink()
    alias.mkdir()
    stage = store.begin(destination)
    stage.write(b"captured")
    stage.publish()
    assert (real / "result").read_bytes() == b"captured"
    assert not (alias / "result").exists()


@pytest.mark.parametrize("when", ["begin", "publish"])
def test_parent_rebinding_refuses_publication_and_cleans_owned_stage(tmp_path, when):
    parent = tmp_path / "parent"
    parent.mkdir()
    store = ResultExportStore()
    destination = store.inspect(parent / "result")
    stage = store.begin(destination) if when == "publish" else None
    old_parent = tmp_path / "old-parent"
    parent.rename(old_parent)
    parent.mkdir()
    attempt = (lambda: store.begin(destination)) if stage is None else stage.publish
    with pytest.raises(ExportStorageError) as caught:
        attempt()
    assert caught.value.code == "changed"
    assert list(parent.iterdir()) == []
    if os.unlink in os.supports_dir_fd:
        assert list(old_parent.iterdir()) == []


@pytest.mark.parametrize("operation", ["write", "fsync", "link", "replace"])
def test_io_failures_preserve_old_destination_and_cleanup(tmp_path, monkeypatch, operation):
    target = tmp_path / "sensitive-result"
    if operation == "replace":
        target.write_bytes(b"old")
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    if operation != "write":
        stage.write(b"new")

    def fail(*args, **kwargs):
        raise OSError("sensitive-result and private cell contents")

    monkeypatch.setattr(module.os, operation, fail)
    attempt = (lambda: stage.write(b"new")) if operation == "write" else stage.publish
    with pytest.raises(ExportStorageError) as caught:
        attempt()
    assert caught.value.code == "io"
    assert "sensitive" not in str(caught.value)
    stage.abort()
    assert list(tmp_path.iterdir()) == ([target] if operation == "replace" else [])
    if operation == "replace":
        assert target.read_bytes() == b"old"


def test_cancel_before_publication_preserves_old_bytes_and_cleans(tmp_path):
    target = tmp_path / "result"
    target.write_bytes(b"old")
    permit = ExportPermit()
    store = ResultExportStore()
    stage = store.begin(store.inspect(target), permit=permit)
    stage.write(b"new")
    permit.cancel()
    with pytest.raises(ExportStorageError) as caught:
        stage.publish()
    assert caught.value.code == "cancelled"
    assert not permit.publication_admitted
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]


def test_cancel_during_admitted_publication_is_nonblocking_and_reports_success(
    tmp_path, monkeypatch
):
    target = tmp_path / "result"
    entered = threading.Event()
    release = threading.Event()
    real_link = os.link

    def slow_link(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_link(*args, **kwargs)

    monkeypatch.setattr(module.os, "link", slow_link)
    permit = ExportPermit()
    store = ResultExportStore()
    stage = store.begin(store.inspect(target), permit=permit)
    stage.write(b"complete")
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(stage.publish)
        try:
            assert entered.wait(5)
            assert permit.publication_admitted
            executor.submit(permit.cancel).result(timeout=1)
        finally:
            release.set()
        assert worker.result(timeout=5) == 8
    assert stage.published
    assert target.read_bytes() == b"complete"
    assert list(tmp_path.iterdir()) == [target]


def test_metadata_repr_hides_destination_and_identity(tmp_path):
    target = tmp_path / "sensitive-result"
    target.write_bytes(b"private")
    destination = ResultExportStore().inspect(target)
    assert repr(destination) == "ExportDestination()"
    assert repr(destination.version) == "ExportFileVersion()"
    assert "sensitive" not in repr(destination)


def test_abort_is_idempotent_and_never_removes_final_target(tmp_path):
    store = ResultExportStore()
    target = tmp_path / "result"
    stage = store.begin(store.inspect(target))
    stage.write(b"discard")
    stage.abort()
    stage.abort()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ExportStorageError):
        stage.publish()
    stage = store.begin(store.inspect(target))
    stage.publish()
    stage.abort()
    stage.abort()
    assert target.read_bytes() == b""


def test_short_writes_are_completed_and_counted_exactly(tmp_path, monkeypatch):
    target = tmp_path / "result"
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    real_write = os.write

    def short_write(fd, chunk):
        return real_write(fd, chunk[:3])

    monkeypatch.setattr(module.os, "write", short_write)
    stage.write(b"0123456789")
    assert stage.publish() == 10
    assert target.read_bytes() == b"0123456789"


def test_success_is_preserved_when_cleanup_fails(tmp_path, monkeypatch):
    target = tmp_path / "result"
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    stage.write(b"complete")
    real_unlink = os.unlink

    def fail_unlink(*args, **kwargs):
        raise PermissionError("private filesystem details")

    monkeypatch.setattr(module.os, "unlink", fail_unlink)
    assert stage.publish() == 8
    assert stage.published
    assert target.read_bytes() == b"complete"
    monkeypatch.setattr(module.os, "unlink", real_unlink)
    stage.abort()
    assert list(tmp_path.iterdir()) == [target]


def test_inspection_uses_metadata_without_reading_existing_contents(tmp_path, monkeypatch):
    target = tmp_path / "result"
    with target.open("wb") as output:
        output.truncate(1_000_000_000)

    def no_read(*args, **kwargs):
        pytest.fail("Inspect must not read file contents")

    monkeypatch.setattr(Path, "open", no_read)
    destination = ResultExportStore().inspect(target)
    assert destination.version is not None
    assert destination.version.size == 1_000_000_000


@pytest.mark.parametrize("kind", ["symlink", "directory", "deleted"])
def test_replaced_target_after_staging_refuses_publication(tmp_path, kind):
    target = tmp_path / "result"
    target.write_bytes(b"old")
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    stage.write(b"new")
    target.unlink()
    if kind == "symlink":
        target.symlink_to(tmp_path / "missing")
    elif kind == "directory":
        target.mkdir()
    with pytest.raises(ExportStorageError) as caught:
        stage.publish()
    assert caught.value.code == ("changed" if kind == "deleted" else "path")
    assert list(tmp_path.iterdir()) == ([] if kind == "deleted" else [target])


def test_cancel_during_fsync_prevents_admission(tmp_path, monkeypatch):
    target = tmp_path / "result"
    permit = ExportPermit()
    store = ResultExportStore()
    stage = store.begin(store.inspect(target), permit=permit)
    stage.write(b"new")
    real_fsync = os.fsync

    def cancel_fsync(fd):
        permit.cancel()
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", cancel_fsync)
    with pytest.raises(ExportStorageError) as caught:
        stage.publish()
    assert caught.value.code == "cancelled"
    assert not permit.publication_admitted
    assert list(tmp_path.iterdir()) == []


def test_cancel_after_admission_acknowledges_actual_publication_failure(tmp_path, monkeypatch):
    target = tmp_path / "result"
    permit = ExportPermit()
    store = ResultExportStore()
    stage = store.begin(store.inspect(target), permit=permit)
    stage.write(b"new")

    def failing_link(*args, **kwargs):
        permit.cancel()
        raise PermissionError("sensitive detail")

    monkeypatch.setattr(module.os, "link", failing_link)
    with pytest.raises(ExportStorageError) as caught:
        stage.publish()
    assert caught.value.code == "permission"
    assert permit.publication_admitted
    assert not stage.published
    assert list(tmp_path.iterdir()) == []


def test_precancelled_begin_does_not_create_a_stage(tmp_path):
    permit = ExportPermit()
    permit.cancel()
    store = ResultExportStore()
    with pytest.raises(ExportStorageError) as caught:
        store.begin(store.inspect(tmp_path / "result"), permit=permit)
    assert caught.value.code == "cancelled"
    assert list(tmp_path.iterdir()) == []


def test_two_replacements_cannot_reuse_the_same_acknowledgement(tmp_path):
    target = tmp_path / "result"
    target.write_bytes(b"old")
    store = ResultExportStore()
    destination = store.inspect(target)
    first = store.begin(destination)
    second = store.begin(destination)
    first.write(b"first")
    second.write(b"second")
    first.publish()
    with pytest.raises(ExportStorageError) as caught:
        second.publish()
    assert caught.value.code == "changed"
    assert target.read_bytes() == b"first"
    assert list(tmp_path.iterdir()) == [target]


def test_substituted_stage_cannot_publish_or_delete_unowned_file(tmp_path):
    target = tmp_path / "result"
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    stage.write(b"ours")
    staged_path = next(tmp_path.iterdir())
    staged_path.unlink()
    staged_path.write_bytes(b"theirs")
    with pytest.raises(ExportStorageError) as caught:
        stage.publish()
    assert caught.value.code == "changed"
    assert not target.exists()
    assert staged_path.read_bytes() == b"theirs"
    stage.abort()
    assert staged_path.read_bytes() == b"theirs"


@pytest.mark.parametrize("existing", [False, True])
def test_portable_fallback_publishes_and_cleans(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(module, "_USE_DIR_FD", False)
    target = tmp_path / "result"
    if existing:
        target.write_bytes(b"old")
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    stage.write(b"complete")
    assert stage.publish() == 8
    stage.abort()
    assert target.read_bytes() == b"complete"
    assert list(tmp_path.iterdir()) == [target]


def test_portable_fallback_refuses_rebound_parent_without_deleting_substitutes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(module, "_USE_DIR_FD", False)
    parent = tmp_path / "parent"
    parent.mkdir()
    store = ResultExportStore()
    stage = store.begin(store.inspect(parent / "result"))
    stage.write(b"ours")
    stage_name = next(parent.iterdir()).name
    moved = tmp_path / "moved"
    parent.rename(moved)
    parent.mkdir()
    substitute = parent / stage_name
    substitute.write_bytes(b"theirs")
    with pytest.raises(ExportStorageError) as caught:
        stage.publish()
    assert caught.value.code == "changed"
    assert substitute.read_bytes() == b"theirs"
    assert list(parent.iterdir()) == [substitute]
    # With no directory descriptors the old path cannot be safely found after
    # rename. Its private temporary file is deliberately left for manual removal.
    assert (moved / stage_name).read_bytes() == b"ours"


def test_postpublication_metadata_failure_does_not_hide_success(tmp_path, monkeypatch):
    target = tmp_path / "result"
    store = ResultExportStore()
    stage = store.begin(store.inspect(target))
    stage.write(b"complete")
    real_link = os.link
    real_stat = os.stat

    def fail_stat(*args, **kwargs):
        raise PermissionError("private details")

    def linked_then_unreadable(*args, **kwargs):
        real_link(*args, **kwargs)
        monkeypatch.setattr(module.os, "stat", fail_stat)

    monkeypatch.setattr(module.os, "link", linked_then_unreadable)
    try:
        assert stage.publish() == 8
        assert stage.published
    finally:
        monkeypatch.setattr(module.os, "stat", real_stat)
    assert target.read_bytes() == b"complete"
    stage.abort()
    assert list(tmp_path.iterdir()) == [target]
