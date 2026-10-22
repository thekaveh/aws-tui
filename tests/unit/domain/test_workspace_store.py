"""Only versioned navigation intent belongs in the named workspace library."""

import json
import os
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aws_tui.domain.navigation_workspace import Bookmark, FileLocation, NavigationTarget, Workspace
from aws_tui.domain.workspace_store import WorkspaceError, WorkspaceStore, WorkspaceWritePermit


def workspace(name="Research"):
    athena = NavigationTarget(
        service="athena",
        connection="analytics",
        region="us-east-1",
        selectors=(("workgroup", "reports"), ("catalog", "AwsDataCatalog"), ("database", "lake")),
    )
    glue = NavigationTarget(
        service="glue",
        connection="analytics",
        region="us-east-1",
        selectors=(("database", "lake"), ("table", "events")),
    )
    emr = NavigationTarget(
        service="emr-serverless",
        connection="compute",
        region="eu-west-1",
        selectors=(("application", "app-123"),),
    )
    remote = NavigationTarget(
        service="s3",
        connection="storage",
        region="us-west-2",
        location=FileLocation(s3=("bucket", "literal", "", "daily")),
    )
    local = NavigationTarget(service="s3", location=FileLocation(local="/tmp/workspace-data"))
    return Workspace(
        name=name,
        active_service="athena",
        connection="analytics",
        region="us-east-1",
        file_panes=(remote, local),
        scopes=(athena, glue, emr),
        bookmarks=(Bookmark("Events", glue), Bookmark("Daily prefix", remote)),
    )


def test_second_store_reads_full_workspace_and_nested_bookmarks(tmp_path):
    path = tmp_path / "workspaces.json"
    first = WorkspaceStore(path=path)
    record = workspace()
    first.save(record)
    assert WorkspaceStore(path=path).list() == (record,)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_serialization_has_only_versioned_allowlisted_navigation_fields(tmp_path):
    path = tmp_path / "workspaces.json"
    WorkspaceStore(path=path).save(workspace())
    data = json.loads(path.read_text())
    assert set(data) == {"schema_version", "workspaces"}
    assert type(data["schema_version"]) is int
    assert data["schema_version"] == 1
    record = data["workspaces"][0]
    assert set(record) == {
        "name",
        "active_service",
        "connection",
        "region",
        "file_panes",
        "scopes",
        "bookmarks",
    }
    assert set(record["scopes"][0]) == {"service", "connection", "region", "selectors"}
    assert all(set(row) == {"name", "destination"} for row in record["bookmarks"])
    assert record["file_panes"][0]["location"] == {"s3": ["bucket", "literal", "", "daily"]}
    assert record["file_panes"][1]["location"] == {"local": "/tmp/workspace-data"}
    forbidden = {
        "sql",
        "query",
        "result_rows",
        "credentials",
        "access_key_id",
        "provider",
        "jobs",
        "transfers",
        "history_execution_id",
    }

    def check(value):
        if isinstance(value, dict):
            assert not set(value) & forbidden
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(data)


def test_duplicate_rename_delete_preserve_other_records(tmp_path):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path=path)
    a, b = workspace("A"), workspace("B")
    store.save(a)
    store.save(b)
    with pytest.raises(WorkspaceError, match="already exists"):
        store.save(workspace("a"))
    with pytest.raises(WorkspaceError, match="already exists"):
        store.rename("A", "b")
    assert store.list() == (a, b)
    store.rename("A", "C")
    assert WorkspaceStore(path=path).list() == (b, replace(a, name="C"))
    store.delete("C")
    assert WorkspaceStore(path=path).list() == (b,)


def test_add_bookmark_preserves_scopes_and_other_workspaces(tmp_path):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path=path)
    a, b = workspace("A"), workspace("B")
    store.save(a)
    store.save(b)
    bookmark = Bookmark(
        "Compute", next(target for target in a.scopes if target.service == "emr-serverless")
    )
    store.add_bookmark("A", bookmark)
    saved = WorkspaceStore(path=path).list()
    assert saved == (replace(a, bookmarks=(*a.bookmarks, bookmark)), b)
    with pytest.raises(WorkspaceError, match="already exists"):
        store.add_bookmark("A", replace(bookmark, name="compute"))
    assert WorkspaceStore(path=path).list() == saved


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b'{"schema_version":2,"workspaces":[]}',
        b'{"schema_version":1,"schema_version":1,"workspaces":[]}',
        b'{"schema_version":1,"workspaces":[],"sql":"must not load"}',
    ],
)
def test_malformed_unknown_or_extra_fields_are_preserved_and_never_overwritten(tmp_path, payload):
    path = tmp_path / "workspaces.json"
    path.write_bytes(payload)
    store = WorkspaceStore(path=path)
    for operation in (
        store.list,
        lambda: store.save(workspace()),
        lambda: store.rename("A", "B"),
        lambda: store.delete("A"),
    ):
        with pytest.raises(WorkspaceError):
            operation()
        assert path.read_bytes() == payload


@pytest.mark.parametrize(
    "field",
    [
        "sql",
        "history_execution_id",
        "saved_query_id",
        "active_view",
        "job_run_id",
        "output_location",
    ],
)
def test_out_of_scope_selector_cannot_enter_a_record(field):
    with pytest.raises(WorkspaceError):
        replace(workspace().scopes[0], selectors=((field, "forbidden"),))


def test_symlink_or_fifo_library_is_refused_without_reading_or_replacing(tmp_path):
    target = tmp_path / "unrelated.json"
    target.write_text("preserve me")
    path = tmp_path / "workspaces.json"
    path.symlink_to(target)
    with pytest.raises(WorkspaceError):
        WorkspaceStore(path=path).save(workspace())
    assert path.is_symlink()
    assert target.read_text() == "preserve me"
    path.unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(path)
        with pytest.raises(WorkspaceError):
            WorkspaceStore(path=path).list()


@pytest.mark.parametrize("value", [["athena"], {"service": "athena"}, True, 42])
def test_wrong_service_types_are_named_validation_errors(value):
    with pytest.raises(WorkspaceError):
        replace(workspace(), active_service=value)


def test_stale_rename_delete_and_bookmark_mutations_preserve_newer_record(tmp_path):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path=path)
    a, b = workspace("A"), workspace("B")
    store.save(a)
    store.save(b)
    extra = Bookmark("New", a.scopes[0])
    store.add_bookmark("A", extra, expected=a)
    before = path.read_bytes()
    for operation in (
        lambda: store.rename("A", "C", expected=a),
        lambda: store.delete("A", expected=a),
        lambda: store.add_bookmark("A", replace(extra, name="Other"), expected=a),
    ):
        with pytest.raises(WorkspaceError, match="changed"):
            operation()
        assert path.read_bytes() == before


def test_bookmark_rename_delete_and_duplicates_preserve_siblings(tmp_path):
    store = WorkspaceStore(path=tmp_path / "workspaces.json")
    original, other = workspace("A"), workspace("B")
    store.save(original)
    store.save(other)
    with pytest.raises(WorkspaceError, match="already exists"):
        store.rename_bookmark("A", "Events", "daily prefix", expected=original)
    store.rename_bookmark("A", "Events", "Iceberg", expected=original)
    current = store.list()[0]
    assert current.bookmarks == (
        replace(original.bookmarks[0], name="Iceberg"),
        original.bookmarks[1],
    )
    assert store.list()[1] == other
    store.delete_bookmark("A", "Iceberg", expected=current)
    assert store.list()[0].bookmarks == (original.bookmarks[1],)
    assert store.list()[1] == other


def test_cancellation_before_admission_leaves_library_and_owned_temps_unchanged(
    tmp_path, monkeypatch
):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path=path)
    store.save(workspace("A"))
    before = path.read_bytes()
    permit = WorkspaceWritePermit()
    real_fsync = os.fsync

    def cancel_during_stage(fd):
        real_fsync(fd)
        permit.cancel()

    monkeypatch.setattr(os, "fsync", cancel_during_stage)
    with pytest.raises(WorkspaceError, match="canceled"):
        store.save(workspace("B"), permit=permit)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".workspace-*"))


def test_admitted_write_drains_and_cancel_does_not_wait_for_filesystem_io(tmp_path, monkeypatch):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path=path)
    permit = WorkspaceWritePermit()
    entered, release = threading.Event(), threading.Event()
    real_replace = os.replace

    def held_replace(source, destination, **kwargs):
        if Path(destination).name == path.name:
            entered.set()
            assert release.wait(3)
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", held_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(store.save, workspace(), permit=permit)
        try:
            assert entered.wait(3)
            assert pool.submit(permit.cancel).result(timeout=1) is False
        finally:
            release.set()
        writing.result(timeout=3)
    assert permit.published
    assert store.list() == (workspace(),)


def test_error_after_actual_replace_acknowledges_owned_publication(tmp_path, monkeypatch):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path=path)
    permit = WorkspaceWritePermit()
    real_replace = os.replace

    def replace_then_error(source, destination, **kwargs):
        real_replace(source, destination, **kwargs)
        raise OSError("controlled post-publication failure")

    monkeypatch.setattr(os, "replace", replace_then_error)
    store.save(workspace(), permit=permit)
    assert store.list() == (workspace(),)
    assert permit.published
    assert not list(tmp_path.glob(".workspace-*"))


def test_true_replace_error_preserves_bytes_and_unpublished_ack(tmp_path, monkeypatch):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path=path)
    store.save(workspace("A"))
    before = path.read_bytes()
    permit = WorkspaceWritePermit()

    def refuse_replace(source, destination, **kwargs):
        raise OSError("controlled pre-publication failure")

    monkeypatch.setattr(os, "replace", refuse_replace)
    with pytest.raises(WorkspaceError):
        store.save(workspace("B"), permit=permit)
    assert path.read_bytes() == before
    assert not permit.published
    assert not list(tmp_path.glob(".workspace-*"))


def test_parent_link_is_refused_before_lock_or_permission_changes(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    path = tmp_path / "config"
    path.symlink_to(outside, target_is_directory=True)
    store = WorkspaceStore(path=path / "workspaces.json")
    for operation in (store.list, lambda: store.save(workspace())):
        with pytest.raises(WorkspaceError):
            operation()
    assert not list(outside.iterdir())
    if os.name != "nt":
        assert outside.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor ownership")
def test_parent_swap_refuses_publication_and_cleans_original_stage(tmp_path, monkeypatch):
    parent = tmp_path / "config"
    parent.mkdir()
    store = WorkspaceStore(path=parent / "workspaces.json")
    store.save(workspace("A"))
    before = store.path.read_bytes()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / store.path.name).write_bytes(before)
    backup = tmp_path / "moved-config"
    real_fsync = os.fsync

    def swap_parent(fd):
        real_fsync(fd)
        parent.rename(backup)
        parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(os, "fsync", swap_parent)
    permit = WorkspaceWritePermit()
    with pytest.raises(WorkspaceError):
        store.save(workspace("B"), permit=permit)
    assert (outside / store.path.name).read_bytes() == before
    assert not list(backup.glob(".workspace-*"))
    assert not list(outside.glob(".workspace-*"))
    assert not permit.published


def test_canceled_attempt_does_not_create_storage_directory(tmp_path):
    parent = tmp_path / "absent"
    permit = WorkspaceWritePermit()
    assert permit.cancel()
    with pytest.raises(WorkspaceError, match="canceled"):
        WorkspaceStore(path=parent / "workspaces.json").save(workspace(), permit=permit)
    assert not parent.exists()
    assert not permit.published


def test_equal_old_content_is_not_evidence_of_publication(tmp_path, monkeypatch):
    store = WorkspaceStore(path=tmp_path / "workspaces.json")
    store.save(workspace("A"))
    before = store.path.read_bytes()
    permit = WorkspaceWritePermit()

    def refuse_replace(source, destination, **kwargs):
        raise OSError("replacement never happened")

    monkeypatch.setattr(os, "replace", refuse_replace)
    with pytest.raises(WorkspaceError):
        store.rename("A", "A", permit=permit)
    assert store.path.read_bytes() == before
    assert not permit.published


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor ownership")
def test_stage_name_collision_does_not_delete_unowned_file(tmp_path, monkeypatch):
    import aws_tui.domain.workspace_store as module

    stage = tmp_path / ".workspace-collision"
    stage.write_bytes(b"unrelated")
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: "collision")
    with pytest.raises(WorkspaceError):
        WorkspaceStore(path=tmp_path / "workspaces.json").save(workspace())
    assert stage.read_bytes() == b"unrelated"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor ownership")
def test_substituted_stage_is_not_deleted_as_owned_cleanup(tmp_path, monkeypatch):
    real_fsync = os.fsync

    def replace_stage_before_failure(fd):
        real_fsync(fd)
        stage = next(tmp_path.glob(".workspace-*"))
        stage.rename(tmp_path / "moved-owned-stage")
        stage.write_bytes(b"unrelated replacement")
        raise OSError("controlled staging failure")

    monkeypatch.setattr(os, "fsync", replace_stage_before_failure)
    with pytest.raises(WorkspaceError):
        WorkspaceStore(path=tmp_path / "workspaces.json").save(workspace())
    assert [path.read_bytes() for path in tmp_path.glob(".workspace-*")] == [
        b"unrelated replacement"
    ]


def test_directory_reparse_attribute_is_refused_before_lock_or_chmod(tmp_path, monkeypatch):
    real_lstat = Path.lstat

    def reparse_lstat(path):
        info = real_lstat(path)
        if path == tmp_path:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    store = WorkspaceStore(path=tmp_path / "workspaces.json")
    for operation in (store.list, lambda: store.save(workspace())):
        with pytest.raises(WorkspaceError):
            operation()
    assert not list(tmp_path.iterdir())


def test_regular_leaf_with_reparse_attribute_is_not_read_or_overwritten(tmp_path, monkeypatch):
    store = WorkspaceStore(path=tmp_path / "workspaces.json")
    store.save(workspace("A"))
    before = store.path.read_bytes()
    real_stat = os.stat

    def reparse_stat(path, **kwargs):
        info = real_stat(path, **kwargs)
        if Path(path).name == store.path.name:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_size=info.st_size,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    monkeypatch.setattr(os, "stat", reparse_stat)
    for operation in (store.list, lambda: store.save(workspace("B"))):
        with pytest.raises(WorkspaceError):
            operation()
    assert store.path.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor ownership")
def test_parent_swap_at_lock_admission_does_not_mutate_alias(tmp_path, monkeypatch):
    from aws_tui.infra.config_store import ConfigStore

    parent = tmp_path / "config"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    backup = tmp_path / "moved-config"
    real_transaction = ConfigStore.transaction

    @contextmanager
    def swap_before_lock(config, *args, **kwargs):
        parent.rename(backup)
        parent.symlink_to(outside, target_is_directory=True)
        with real_transaction(config, *args, **kwargs):
            yield

    monkeypatch.setattr(ConfigStore, "transaction", swap_before_lock)
    with pytest.raises(WorkspaceError):
        WorkspaceStore(path=parent / "workspaces.json").save(workspace())
    assert not list(outside.iterdir())
    assert outside.stat().st_mode & 0o777 == 0o755
    assert not list(backup.glob(".workspace-*"))


@pytest.mark.parametrize("same_name", [False, True])
def test_two_process_saves_are_locked_and_preserve_other_records(tmp_path, same_name):
    script = """
import json,sys,time,traceback
from pathlib import Path
from aws_tui.domain.navigation_workspace import NavigationTarget,Workspace
from aws_tui.domain.workspace_store import WorkspaceError,WorkspaceStore
directory=Path(sys.argv[1]); slot=sys.argv[2]; name=sys.argv[3]
(directory/('ready-'+slot)).touch()
deadline=time.monotonic()+8
while not (directory/'go').exists():
    if time.monotonic()>deadline: raise RuntimeError('fixture barrier expired')
    time.sleep(.005)
target=NavigationTarget('glue','analytics','us-east-1',(('database','lake'),))
try:
    WorkspaceStore(path=directory/'workspaces.json').save(Workspace(name,'glue','analytics','us-east-1',scopes=(target,)))
    result='saved'; failure=None
except WorkspaceError as exc:
    result=exc.code
    cause=exc.__context__
    failure=[]
    while cause is not None:
        failure.append({'type':type(cause).__name__,'errno':getattr(cause,'errno',None),
            'frames':[(frame.name,frame.lineno) for frame in traceback.extract_tb(cause.__traceback__)]})
        cause=cause.__context__
print(json.dumps({'result':result,'failure':failure,'pid':__import__('os').getpid()}))
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(tmp_path),
                str(index),
                "Same" if same_name else f"Record {index}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    try:
        deadline = time.monotonic() + 8
        while not all((tmp_path / f"ready-{index}").exists() for index in range(2)):
            assert time.monotonic() < deadline
            time.sleep(0.005)
        (tmp_path / "go").touch()
        results = []
        for process in processes:
            output, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
            results.append(json.loads(output))
        assert results[0]["pid"] != results[1]["pid"]
        assert sorted(row["result"] for row in results) == (
            ["exists", "saved"] if same_name else ["saved", "saved"]
        ), results
        records = WorkspaceStore(path=tmp_path / "workspaces.json").list()
        assert len(records) == (1 if same_name else 2)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
