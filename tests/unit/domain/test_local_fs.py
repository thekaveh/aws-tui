"""Unit tests for LocalFS provider."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProviderError,
    StageManifestEntry,
    TransferProgress,
)
from aws_tui.domain.local_fs import (
    LocalFS,
    _validate_windows_relative,
    _windows_drive_entries,
    _windows_path_is_contained,
    _windows_path_ref,
)

pytestmark = pytest.mark.unit


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def _agen(blobs: list[bytes]) -> AsyncIterator[bytes]:
    for b in blobs:
        yield b


def _make_fs(tmp_path: Path) -> LocalFS:
    return LocalFS(root=tmp_path)


def _host_path_ref(path: Path) -> PathRef:
    windows_path = PureWindowsPath(path)
    return PathRef((windows_path.drive, *windows_path.relative_to(windows_path.anchor).parts))


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if sys.platform == "win32" and exc.winerror == 1314:
            pytest.skip("Windows symlink creation privilege is unavailable")
        raise


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (PathRef(("C:",)), PureWindowsPath("C:/")),
        (PathRef(("D:", "data", "file.txt")), PureWindowsPath("D:/data/file.txt")),
    ],
)
def test_windows_drive_paths_map_to_native_absolute_paths(
    path: PathRef,
    expected: PureWindowsPath,
) -> None:
    assert _windows_path_ref(path) == expected


def test_windows_unrooted_path_requires_drive_segment() -> None:
    with pytest.raises(ProviderError, match="must start with a drive"):
        _windows_path_ref(PathRef(("Users", "me")))


@pytest.mark.parametrize(
    "segment",
    [
        "..",
        "file:stream",
        "CON.txt",
        "COM¹.txt",
        "com².log",
        "Com³.tar.gz",
        "LPT¹.txt",
        "lpt².log",
        "Lpt³.tar.gz",
        "trailing.",
    ],
)
def test_windows_relative_paths_reject_aliasing_segments(segment: str) -> None:
    with pytest.raises(ProviderError, match="unsafe Windows segment"):
        _validate_windows_relative(PathRef((segment,)))


def test_windows_handle_paths_must_stay_on_component_boundary() -> None:
    assert _windows_path_is_contained(r"\\?\C:\root\child", r"\\?\C:\root")
    assert not _windows_path_is_contained(r"\\?\C:\rooted", r"\\?\C:\root")
    assert not _windows_path_is_contained(r"\\?\D:\root\child", r"\\?\C:\root")


def test_windows_extended_drive_path_normalization_removes_namespace_prefix() -> None:
    from aws_tui.domain import local_fs

    assert local_fs._normalize_windows_final_path(r"\\?\C:\root\file.txt") == (r"c:\root\file.txt")


def test_windows_final_path_falls_back_to_opened_name_on_access_denied() -> None:
    from aws_tui.domain import local_fs

    calls: list[int] = []

    def query(flags: int) -> str:
        calls.append(flags)
        if flags == 0:
            raise PermissionError(errno.EACCES, "SMB normalized-name query denied")
        return r"\\server\share\root\file.txt"

    result = local_fs._windows_final_path_with_fallback(query)

    assert result == r"\\server\share\root\file.txt"
    assert calls == [0, local_fs._WINDOWS_FILE_NAME_OPENED]


def test_windows_final_path_does_not_fallback_for_other_errors() -> None:
    from aws_tui.domain import local_fs

    calls: list[int] = []

    def query(flags: int) -> str:
        calls.append(flags)
        raise FileNotFoundError(errno.ENOENT, "gone")

    with pytest.raises(FileNotFoundError):
        local_fs._windows_final_path_with_fallback(query)

    assert calls == [0]


def test_windows_revision_uses_change_time_not_creation_time() -> None:
    from aws_tui.domain import local_fs

    file_information = SimpleNamespace(
        dwVolumeSerialNumber=7,
        nFileIndexHigh=0,
        nFileIndexLow=9,
        nFileSizeHigh=0,
        nFileSizeLow=11,
    )
    basic_information = SimpleNamespace(
        CreationTime=999,
        LastWriteTime=13,
        ChangeTime=17,
    )

    revision = local_fs._windows_revision(file_information, basic_information)

    assert revision == "windows:7:9:11:13:17"
    assert "999" not in revision


def test_windows_rename_handle_uses_complete_relative_buffer() -> None:
    from aws_tui.domain import local_fs

    captured: dict[str, object] = {}

    def set_file_information(
        handle: int,
        _io_status: object,
        buffer: object,
        buffer_size: int,
        information_class: int,
    ) -> int:
        raw = ctypes.string_at(buffer, buffer_size)
        information = ctypes.cast(
            buffer,
            ctypes.POINTER(local_fs._WindowsRenameInformation),
        ).contents
        name_offset = local_fs._WindowsRenameInformation.FileName.offset
        captured.update(
            handle=handle,
            information_class=information_class,
            buffer_size=buffer_size,
            root=information.RootDirectory,
            name=raw[name_offset : name_offset + information.FileNameLength].decode("utf-16-le"),
        )
        return 1

    api = object.__new__(local_fs._WindowsAPI)
    api._ntdll = SimpleNamespace(
        NtSetInformationFile=set_file_information,
        RtlNtStatusToDosError=lambda status: status,
    )
    api.rename_handle(22, 11, "published.txt", r"C:\source\stage.txt")

    assert captured == {
        "handle": 22,
        "information_class": local_fs._WINDOWS_FILE_RENAME_INFORMATION,
        "buffer_size": ctypes.sizeof(local_fs._WindowsRenameInformation)
        + len("published.txt".encode("utf-16-le")),
        "root": 11,
        "name": "published.txt",
    }


def test_windows_directory_handles_share_write_but_not_delete() -> None:
    from aws_tui.domain import local_fs

    shares: list[int] = []

    def create_file(
        _path: str,
        _access: int,
        share_mode: int,
        *_args: object,
    ) -> int:
        shares.append(share_mode)
        return 22

    api = object.__new__(local_fs._WindowsAPI)
    api._dll = SimpleNamespace(CreateFileW=create_file)

    api.open(
        r"C:\locked",
        access=(local_fs._WINDOWS_FILE_LIST_DIRECTORY | local_fs._WINDOWS_FILE_READ_ATTRIBUTES),
        disposition=local_fs._WINDOWS_OPEN_EXISTING,
    )
    api.open(
        r"C:\locked\file.txt",
        access=local_fs._WINDOWS_FILE_READ_ATTRIBUTES,
        disposition=local_fs._WINDOWS_OPEN_EXISTING,
    )

    assert shares == [
        local_fs._WINDOWS_FILE_SHARE_READ | local_fs._WINDOWS_FILE_SHARE_WRITE,
        local_fs._WINDOWS_FILE_SHARE_READ,
    ]


def test_windows_delete_claims_and_removes_the_same_open_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import local_fs

    events: list[tuple[object, ...]] = []

    class FakeAPI:
        def open(self, path: str, *, access: int, disposition: int) -> int:
            events.append(("open", path, access, disposition))
            return 22

        def revision(self, handle: int, path: str) -> str:
            events.append(("revision", handle, path))
            return "change-time-revision"

        def attributes(self, _handle: int, _path: str) -> int:
            return 0

        def final_path(self, _handle: int, path: str) -> str:
            return path

        def rename_handle(self, handle: int, parent_handle: int, name: str, path: str) -> None:
            events.append(("rename", handle, parent_handle, name, path))

        def close(self, handle: int) -> None:
            events.append(("close", handle))

    @contextmanager
    def locked_parent(_root: Path | None, _path: PathRef) -> Iterator[tuple[str, int, str, str]]:
        yield r"C:\root", 11, "file.txt", r"c:\root"

    def remove_tree(handle: int, path: str, anchor: str) -> None:
        events.append(("remove", handle, path, anchor))

    monkeypatch.setattr(local_fs, "_windows_api", lambda: FakeAPI())
    monkeypatch.setattr(local_fs, "_windows_locked_parent", locked_parent)
    monkeypatch.setattr(local_fs, "_windows_remove_tree_handle", remove_tree, raising=False)

    local_fs._windows_delete(
        None,
        PathRef(("C:", "file.txt")),
        "change-time-revision",
    )

    open_event = events[0]
    assert open_event[0] == "open"
    access = open_event[2]
    assert isinstance(access, int)
    assert access & local_fs._WINDOWS_DELETE_ACCESS
    assert not access & local_fs._WINDOWS_GENERIC_WRITE
    assert events[1] == ("revision", 22, r"C:\root\file.txt")
    assert events[2][0:3] == ("rename", 22, 11)
    assert str(events[2][3]).startswith(".file.txt.aws-tui-delete-")
    assert events[3][0] == "remove"
    assert events[3][1] == 22
    assert events[-1] == ("close", 22)


def test_windows_publish_validates_and_renames_the_same_open_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import local_fs

    events: list[tuple[object, ...]] = []
    revisions = iter(("expected-revision", "published-revision"))

    class FakeAPI:
        def open(self, path: str, *, access: int, disposition: int) -> int:
            events.append(("open", path, access, disposition))
            return 22

        def revision(self, handle: int, path: str) -> str:
            events.append(("revision", handle, path))
            return next(revisions)

        def attributes(self, _handle: int, _path: str) -> int:
            return 0

        def final_path(self, _handle: int, path: str) -> str:
            return path

        def rename_handle(self, handle: int, parent_handle: int, name: str, path: str) -> None:
            events.append(("rename", handle, parent_handle, name, path))

        def close(self, handle: int) -> None:
            events.append(("close", handle))

    @contextmanager
    def locked_parent(_root: Path | None, path: PathRef) -> Iterator[tuple[str, int, str, str]]:
        if path.name == "stage.txt":
            yield r"C:\source", 11, path.name, r"c:\source"
        else:
            yield r"C:\destination", 12, path.name, r"c:\destination"

    monkeypatch.setattr(local_fs, "_windows_api", lambda: FakeAPI())
    monkeypatch.setattr(local_fs, "_windows_locked_parent", locked_parent)

    revision = local_fs._windows_atomic_publish_no_replace(
        None,
        PathRef(("C:", "source", "stage.txt")),
        PathRef(("C:", "destination", "published.txt")),
        "expected-revision",
    )

    assert revision == "published-revision"
    open_event = events[0]
    assert open_event[0] == "open"
    access = open_event[2]
    assert isinstance(access, int)
    assert access & local_fs._WINDOWS_DELETE_ACCESS
    assert access & local_fs._WINDOWS_FILE_READ_ATTRIBUTES
    assert events[1] == ("revision", 22, r"C:\source\stage.txt")
    assert events[2] == ("rename", 22, 12, "published.txt", r"C:\source\stage.txt")
    assert events[3] == ("revision", 22, r"C:\destination\published.txt")
    assert events[-1] == ("close", 22)


@pytest.mark.parametrize("tamper", ["changed", "unknown"])
async def test_windows_directory_publish_rejects_manifest_tamper_before_primitive_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    from aws_tui.domain import local_fs

    fs = LocalFS(root=tmp_path)
    staged = PathRef(("stage",))
    destination = PathRef(("published",))
    expected = (
        StageManifestEntry(PathRef(()), EntryKind.DIRECTORY, "root-revision"),
        StageManifestEntry(PathRef(("child.txt",)), EntryKind.FILE, "owned-child-revision"),
    )
    state = {
        "child_revision": "owned-child-revision",
        "unknown_child": False,
        "stage_exists": True,
        "destination_exists": False,
    }
    events: list[tuple[object, ...]] = []

    class FakeChild:
        def __init__(self, name: str) -> None:
            self.name = name

        def __str__(self) -> str:
            return rf"C:\source\stage\{self.name}"

    class FakePath:
        def __init__(self, path: str) -> None:
            self.path = path

        def iterdir(self) -> Iterator[FakeChild]:
            assert self.path == r"C:\source\stage"
            children = [FakeChild("child.txt")]
            if state["unknown_child"]:
                children.append(FakeChild("unknown.txt"))
            return iter(children)

    class FakeAPI:
        def open(self, path: str, *, access: int, disposition: int) -> int:
            events.append(("open", path, access, disposition))
            if path.endswith("child.txt"):
                return 23
            if path.endswith("unknown.txt"):
                return 24
            return 22

        def revision(self, handle: int, path: str) -> str:
            events.append(("revision", handle, path))
            if handle == 23:
                return str(state["child_revision"])
            if handle == 24:
                return "unknown-child-revision"
            return "published-root-revision" if "published" in path else "root-revision"

        def attributes(self, handle: int, _path: str) -> int:
            return local_fs._WINDOWS_FILE_ATTRIBUTE_DIRECTORY if handle == 22 else 0

        def final_path(self, _handle: int, path: str) -> str:
            return path

        def rename_handle(self, handle: int, parent_handle: int, name: str, path: str) -> None:
            events.append(("rename", handle, parent_handle, name, path))
            state["stage_exists"] = False
            state["destination_exists"] = True

        def close(self, handle: int) -> None:
            events.append(("close", handle))

    @contextmanager
    def locked_parent(_root: Path | None, path: PathRef) -> Iterator[tuple[str, int, str, str]]:
        if path == staged:
            yield r"C:\source", 11, "stage", r"c:\source"
        else:
            yield r"C:\destination", 12, "published", r"c:\destination"

    async def stale_async_scan(
        _root: PathRef, _path: PathRef | None = None
    ) -> tuple[StageManifestEntry, ...]:
        return expected

    real_run_sync = local_fs.anyio.to_thread.run_sync

    async def replace_before_primitive(function: object, *args: object) -> object:
        if tamper == "changed":
            state["child_revision"] = "replacement-child-revision"
        else:
            state["unknown_child"] = True
        return await real_run_sync(function, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(local_fs, "_WINDOWS", True)
    monkeypatch.setattr(local_fs, "Path", FakePath)
    monkeypatch.setattr(local_fs, "_windows_api", lambda: FakeAPI())
    monkeypatch.setattr(local_fs, "_windows_locked_parent", locked_parent)
    monkeypatch.setattr(fs, "_capture_stage_manifest", stale_async_scan, raising=False)
    monkeypatch.setattr(local_fs.anyio.to_thread, "run_sync", replace_before_primitive)

    with pytest.raises(ConflictError, match="stage manifest changed"):
        await fs.atomic_publish_directory_no_replace(
            staged,
            destination,
            expected_manifest=expected,
        )

    assert state["stage_exists"] is True
    assert state["destination_exists"] is False
    assert any(event[0] == "open" and str(event[1]).endswith("child.txt") for event in events)
    assert not any(event[0] == "rename" for event in events)


@pytest.mark.parametrize("tamper", ["child_revision", "child_kind", "root_revision"])
def test_windows_directory_publish_revalidates_retained_tree_before_rename(
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    from aws_tui.domain import local_fs

    staged = PathRef(("stage",))
    destination = PathRef(("published",))
    expected = (
        StageManifestEntry(PathRef(()), EntryKind.DIRECTORY, "root-revision"),
        StageManifestEntry(PathRef(("child.txt",)), EntryKind.FILE, "child-revision"),
    )
    events: list[tuple[object, ...]] = []
    revision_calls: dict[int, int] = {22: 0, 23: 0}
    attribute_calls: dict[int, int] = {22: 0, 23: 0}

    class FakeChild:
        name = "child.txt"

        def __str__(self) -> str:
            return r"C:\source\stage\child.txt"

    class FakePath:
        def __init__(self, path: str) -> None:
            self.path = path

        def iterdir(self) -> Iterator[FakeChild]:
            assert self.path == r"C:\source\stage"
            return iter([FakeChild()])

    class FakeAPI:
        def open(self, path: str, *, access: int, disposition: int) -> int:
            del access, disposition
            handle = 23 if path.endswith("child.txt") else 22
            events.append(("open", handle, path))
            return handle

        def revision(self, handle: int, _path: str) -> str:
            revision_calls[handle] += 1
            if handle == 23:
                if tamper == "child_revision" and revision_calls[handle] > 1:
                    return "replacement-revision"
                return "child-revision"
            if tamper == "root_revision" and revision_calls[handle] > 1:
                return "changed-root-revision"
            return "root-revision"

        def attributes(self, handle: int, _path: str) -> int:
            attribute_calls[handle] += 1
            if handle == 22:
                return local_fs._WINDOWS_FILE_ATTRIBUTE_DIRECTORY
            if tamper == "child_kind" and attribute_calls[handle] > 1:
                return local_fs._WINDOWS_FILE_ATTRIBUTE_DIRECTORY
            return 0

        def final_path(self, _handle: int, path: str) -> str:
            return path

        def rename_handle(self, *args: object) -> None:
            events.append(("rename", *args))

        def close(self, handle: int) -> None:
            events.append(("close", handle))

    @contextmanager
    def locked_parent(_root: Path | None, path: PathRef) -> Iterator[tuple[str, int, str, str]]:
        if path == staged:
            yield r"C:\source", 11, "stage", r"c:\source"
        else:
            yield r"C:\destination", 12, "published", r"c:\destination"

    monkeypatch.setattr(local_fs, "Path", FakePath)
    monkeypatch.setattr(local_fs, "_windows_api", lambda: FakeAPI())
    monkeypatch.setattr(local_fs, "_windows_locked_parent", locked_parent)

    with pytest.raises(ConflictError, match="stage manifest changed"):
        local_fs._windows_atomic_publish_directory_no_replace(
            None,
            staged,
            destination,
            expected,
        )

    assert not any(event[0] == "rename" for event in events)
    assert ("close", 23) in events
    assert ("close", 22) in events


def test_windows_claim_reads_created_identity_before_parent_walk_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import local_fs

    events: list[tuple[object, ...]] = []

    class FakeAPI:
        def mkdir(self, path: str) -> None:
            events.append(("mkdir", path))

        def open(self, path: str, *, access: int, disposition: int) -> int:
            events.append(("open", path, access, disposition))
            return 22

        def revision(self, handle: int, path: str) -> str:
            events.append(("revision", handle, path))
            return "windows:7:9:0:13:17"

        def attributes(self, _handle: int, _path: str) -> int:
            return local_fs._WINDOWS_FILE_ATTRIBUTE_DIRECTORY

        def final_path(self, _handle: int, path: str) -> str:
            return path

        def close(self, handle: int) -> None:
            events.append(("close", handle))

    @contextmanager
    def locked_parent(_root: Path | None, path: PathRef) -> Iterator[tuple[str, int, str, str]]:
        events.append(("parent-enter",))
        yield r"C:\root", 11, path.name, r"c:\root"
        events.append(("parent-exit",))

    monkeypatch.setattr(local_fs, "_windows_api", lambda: FakeAPI())
    monkeypatch.setattr(local_fs, "_windows_locked_parent", locked_parent)

    identity = local_fs._windows_claim_directory(None, PathRef(("C:", "claimed")))

    assert identity == "windows:7:9:0:13:17"
    assert events.index(("revision", 22, r"C:\root\claimed")) < events.index(("parent-exit",))
    assert events.index(("close", 22)) < events.index(("parent-exit",))


@pytest.mark.asyncio
async def test_windows_virtual_root_lists_all_drives(monkeypatch: pytest.MonkeyPatch) -> None:
    from aws_tui.domain import local_fs

    monkeypatch.setattr(local_fs, "_WINDOWS", True)
    monkeypatch.setattr(
        local_fs.os,  # type: ignore[attr-defined]
        "listdrives",
        lambda: ["C:\\", "D:\\"],
        raising=False,
    )

    entries = await LocalFS().list(PathRef())

    assert [(entry.name, entry.kind) for entry in entries] == [
        ("C:", EntryKind.DIRECTORY),
        ("D:", EntryKind.DIRECTORY),
    ]


async def test_windows_rooted_list_uses_handle_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    expected = tmp_path.stat()
    called: list[tuple[Path | None, PathRef]] = []

    def windows_list(root: Path | None, path: PathRef) -> list[tuple[str, os.stat_result, str]]:
        called.append((root, path))
        return [("safe.txt", expected, "change-time-revision")]

    def posix_list(_root: Path, _path: PathRef) -> list[tuple[str, os.stat_result]]:
        raise AssertionError("Windows rooted list reached the POSIX dir_fd strategy")

    monkeypatch.setattr(local_fs, "_WINDOWS", True)
    monkeypatch.setattr(local_fs, "_windows_list", windows_list, raising=False)
    monkeypatch.setattr(local_fs, "_rooted_list", posix_list)

    entries = await LocalFS(root=tmp_path).list(PathRef())

    assert called == [(tmp_path.resolve(), PathRef())]
    assert [entry.name for entry in entries] == ["safe.txt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific LocalFS contract")
@pytest.mark.parametrize("rooted", [True, False], ids=["rooted", "unrooted"])
async def test_windows_provider_supports_full_file_contract(tmp_path: Path, rooted: bool) -> None:
    fs = LocalFS(root=tmp_path) if rooted else LocalFS()
    base = PathRef() if rooted else _host_path_ref(tmp_path)

    def child(*segments: str) -> PathRef:
        return PathRef((*base.segments, *segments))

    await fs.mkdir(child("nested", "child"))
    await fs.write_stream(child("nested", "source.txt"), _agen([b"windows-safe"]))

    entries = await fs.list(child("nested"))
    entry = await fs.stat(child("nested", "source.txt"))
    payload = await _drain(await fs.read_stream(child("nested", "source.txt")))

    assert [(item.name, item.kind) for item in entries] == [
        ("child", EntryKind.DIRECTORY),
        ("source.txt", EntryKind.FILE),
    ]
    assert entry.size == len(b"windows-safe")
    assert payload == b"windows-safe"

    await fs.rename(child("nested", "source.txt"), child("nested", "renamed.txt"))
    await fs.delete_empty_directory(child("nested", "child"))
    await fs.delete(child("nested"))

    assert not (tmp_path / "nested").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific reparse containment")
async def test_windows_rooted_provider_rejects_intermediate_reparse_points(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(b"outside")
    _symlink_or_skip(root / "jump", outside, target_is_directory=True)
    inside = root / "inside.txt"
    inside.write_bytes(b"inside")
    fs = LocalFS(root=root)

    async def read_secret() -> None:
        await _drain(await fs.read_stream(PathRef(("jump", "secret.txt"))))

    operations = [
        fs.list(PathRef(("jump",))),
        fs.stat(PathRef(("jump", "secret.txt"))),
        read_secret(),
        fs.write_stream(PathRef(("jump", "new.txt")), _agen([b"escaped"])),
        fs.mkdir(PathRef(("jump", "nested"))),
        fs.rename(PathRef(("inside.txt",)), PathRef(("jump", "moved.txt"))),
        fs.delete(PathRef(("jump", "secret.txt"))),
    ]
    for operation in operations:
        with pytest.raises(ConflictError, match="reparse point"):
            await operation

    assert secret.read_bytes() == b"outside"
    assert not (outside / "new.txt").exists()
    assert inside.read_bytes() == b"inside"


def test_windows_drive_discovery_falls_back_before_python_312(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import local_fs

    monkeypatch.delattr(local_fs.os, "listdrives", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        local_fs.Path,  # type: ignore[attr-defined]
        "exists",
        lambda path: str(path) in {"C:\\", "D:\\"},
    )

    assert [entry.name for entry in _windows_drive_entries()] == ["C:", "D:"]


# ---------------------------------------------------------------------------
# list / stat
# ---------------------------------------------------------------------------


async def test_list_empty_dir(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    assert await fs.list(PathRef(())) == []


async def test_list_with_file_and_dir(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_bytes(b"abc")
    (tmp_path / "d").mkdir()
    fs = _make_fs(tmp_path)
    entries = await fs.list(PathRef(()))
    names = sorted(e.name for e in entries)
    assert names == ["d", "f.txt"]
    kinds = {e.name: e.kind for e in entries}
    assert kinds["d"] == EntryKind.DIRECTORY
    assert kinds["f.txt"] == EntryKind.FILE
    sizes = {e.name: e.size for e in entries}
    assert sizes["f.txt"] == 3
    assert sizes["d"] is None


async def test_list_missing_dir_raises_not_found(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await fs.list(PathRef.from_posix("/missing"))


async def test_stat_file(tmp_path: Path) -> None:
    (tmp_path / "x").write_bytes(b"x")
    fs = _make_fs(tmp_path)
    entry = await fs.stat(PathRef.from_posix("/x"))
    assert entry.kind == EntryKind.FILE
    assert entry.size == 1
    assert entry.modified is not None


async def test_stat_missing_raises(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/nope"))


async def test_stat_reports_leaf_symlink_without_following_it(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"target")
    _symlink_or_skip(tmp_path / "link.txt", target)

    entry = await _make_fs(tmp_path).stat(PathRef.from_posix("/link.txt"))

    assert entry.kind == EntryKind.SYMLINK


# ---------------------------------------------------------------------------
# mkdir / delete / rename
# ---------------------------------------------------------------------------


async def test_mkdir_creates_nested(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    await fs.mkdir(PathRef.from_posix("/a/b/c"))
    assert (tmp_path / "a" / "b" / "c").is_dir()


async def test_mkdir_idempotent_for_dirs(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    p = PathRef.from_posix("/x")
    await fs.mkdir(p)
    await fs.mkdir(p)  # exist_ok=True ⇒ no raise


async def test_mkdir_conflicts_with_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    fs = _make_fs(tmp_path)
    with pytest.raises(ConflictError):
        await fs.mkdir(PathRef.from_posix("/f"))


@pytest.mark.parametrize("rooted", [True, False], ids=["rooted", "unrooted"])
async def test_claim_directory_returns_created_identity(tmp_path: Path, rooted: bool) -> None:

    fs = LocalFS(root=tmp_path) if rooted else LocalFS()
    path = (
        PathRef.from_posix("/claimed")
        if rooted
        else PathRef.from_posix((tmp_path / "claimed").as_posix())
    )

    identity = await fs.claim_directory(path)
    created = await fs.stat(path)

    assert created.etag is not None
    assert identity == created.etag


async def test_delete_file(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_bytes(b"x")
    fs = _make_fs(tmp_path)
    await fs.delete(PathRef.from_posix("/f"))
    assert not p.exists()


async def test_conditional_delete_removes_unchanged_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"original")
    fs = _make_fs(tmp_path)
    observed = await fs.stat(PathRef.from_posix("/file.txt"))

    await fs.delete(PathRef.from_posix("/file.txt"), expected_etag=observed.etag)

    assert not target.exists()


async def test_conditional_delete_preserves_changed_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"original")
    fs = _make_fs(tmp_path)
    observed = await fs.stat(PathRef.from_posix("/file.txt"))
    target.write_bytes(b"changed-content")

    with pytest.raises(ConflictError, match="source changed"):
        await fs.delete(PathRef.from_posix("/file.txt"), expected_etag=observed.etag)

    assert target.read_bytes() == b"changed-content"


@pytest.mark.skipif(os.name != "nt", reason="Windows ChangeTime contract")
async def test_windows_conditional_delete_detects_metadata_only_change(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"unchanged-content")
    fs = _make_fs(tmp_path)
    observed = await fs.stat(PathRef.from_posix("/file.txt"))

    original = target.stat()
    os.utime(
        target,
        ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000),
    )
    with pytest.raises(ConflictError, match="source changed"):
        await fs.delete(PathRef.from_posix("/file.txt"), expected_etag=observed.etag)

    assert target.read_bytes() == b"unchanged-content"


async def test_delete_empty_directory_refuses_children(tmp_path: Path) -> None:
    directory = tmp_path / "folder"
    directory.mkdir()
    (directory / "late.txt").write_bytes(b"keep")

    with pytest.raises(ConflictError, match="not empty"):
        await _make_fs(tmp_path).delete_empty_directory(PathRef.from_posix("/folder"))

    assert (directory / "late.txt").read_bytes() == b"keep"


async def test_delete_empty_directory_removes_empty_directory(tmp_path: Path) -> None:
    directory = tmp_path / "folder"
    directory.mkdir()

    await _make_fs(tmp_path).delete_empty_directory(PathRef.from_posix("/folder"))

    assert not directory.exists()


async def test_delete_directory_recursive(tmp_path: Path) -> None:
    (tmp_path / "d" / "e").mkdir(parents=True)
    (tmp_path / "d" / "x.txt").write_bytes(b"x")
    (tmp_path / "d" / "e" / "y.txt").write_bytes(b"y")
    fs = _make_fs(tmp_path)
    await fs.delete(PathRef.from_posix("/d"))
    assert not (tmp_path / "d").exists()


async def test_delete_rejects_provider_root(tmp_path: Path) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ProviderError, match="provider root"):
        await _make_fs(tmp_path).delete(PathRef(()))

    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("segments", [(".",), ("tmp", "..")])
async def test_unrestricted_delete_rejects_root_aliases(segments: tuple[str, ...]) -> None:
    with pytest.raises(ProviderError, match="dot segment"):
        await LocalFS().delete(PathRef(segments))


async def test_delete_missing_raises(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await fs.delete(PathRef.from_posix("/nope"))


async def test_rename_file(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x")
    fs = _make_fs(tmp_path)
    await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))
    assert (tmp_path / "b").read_bytes() == b"x"
    assert not (tmp_path / "a").exists()


async def test_rename_into_subdir(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    fs = _make_fs(tmp_path)
    await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/sub/b"))
    assert (tmp_path / "sub" / "b").read_bytes() == b"x"


async def test_rename_conflict(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x")
    (tmp_path / "b").write_bytes(b"y")
    fs = _make_fs(tmp_path)
    with pytest.raises(ConflictError):
        await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd race injection")
async def test_rename_atomically_rejects_destination_created_at_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    (tmp_path / "a").write_bytes(b"source")
    real_rename = local_fs._rename_no_replace_at

    def race(src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666, dir_fd=dst_fd)
        os.write(fd, b"concurrent")
        os.close(fd)
        real_rename(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(local_fs, "_rename_no_replace_at", race)

    with pytest.raises(ConflictError):
        await _make_fs(tmp_path).rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))

    assert (tmp_path / "a").read_bytes() == b"source"
    assert (tmp_path / "b").read_bytes() == b"concurrent"


@pytest.mark.parametrize(
    ("src", "dst"),
    [(PathRef(()), PathRef.from_posix("/moved")), (PathRef.from_posix("/a"), PathRef(()))],
)
async def test_rename_rejects_provider_root(tmp_path: Path, src: PathRef, dst: PathRef) -> None:
    (tmp_path / "a").write_text("keep", encoding="utf-8")

    with pytest.raises(ProviderError, match="provider root"):
        await _make_fs(tmp_path).rename(src, dst)

    assert (tmp_path / "a").read_text(encoding="utf-8") == "keep"


async def test_unrestricted_rename_rejects_dot_segment_alias() -> None:
    with pytest.raises(ProviderError, match="dot segment"):
        await LocalFS().rename(PathRef((".",)), PathRef.from_posix("/moved"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX publication primitive")
@pytest.mark.parametrize("rooted", [True, False], ids=["rooted", "unrooted"])
async def test_publish_returns_destination_revision(tmp_path: Path, rooted: bool) -> None:
    stage = tmp_path / "stage.txt"
    destination = tmp_path / "published.txt"
    stage.write_bytes(b"owned")
    fs = LocalFS(root=tmp_path) if rooted else LocalFS()
    staged = PathRef.from_posix("/stage.txt") if rooted else PathRef.from_posix(stage.as_posix())
    published = (
        PathRef.from_posix("/published.txt")
        if rooted
        else PathRef.from_posix(destination.as_posix())
    )
    expected = await fs.capture_stage_revision(staged)

    revision = await fs.atomic_publish_no_replace(
        staged,
        published,
        expected_source_revision=expected,
    )

    assert revision == (await fs.stat(published)).etag
    assert destination.read_bytes() == b"owned"
    assert not stage.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX publication primitive")
async def test_rooted_publish_rejects_stage_replaced_before_primitive_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    stage = tmp_path / "stage.txt"
    destination = tmp_path / "published.txt"
    stage.write_bytes(b"owned")
    fs = LocalFS(root=tmp_path)
    staged = PathRef.from_posix("/stage.txt")
    expected = await fs.capture_stage_revision(staged)
    real_publish = local_fs._rooted_atomic_publish_no_replace

    def replace_then_publish(
        root: Path,
        source: PathRef,
        target: PathRef,
        expected_revision: str,
    ) -> str:
        stage.unlink()
        stage.write_bytes(b"replacement")
        return real_publish(root, source, target, expected_revision)

    monkeypatch.setattr(local_fs, "_rooted_atomic_publish_no_replace", replace_then_publish)

    with pytest.raises(ConflictError, match="stage changed"):
        await fs.atomic_publish_no_replace(
            staged,
            PathRef.from_posix("/published.txt"),
            expected_source_revision=expected,
        )

    assert stage.read_bytes() == b"replacement"
    assert not destination.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX publication primitive")
async def test_unrooted_publish_rejects_stage_replaced_before_primitive_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    stage = tmp_path / "stage.txt"
    destination = tmp_path / "published.txt"
    stage.write_bytes(b"owned")
    fs = LocalFS()
    staged = PathRef.from_posix(stage.as_posix())
    published = PathRef.from_posix(destination.as_posix())
    expected = await fs.capture_stage_revision(staged)
    real_publish = local_fs._unrooted_atomic_publish_no_replace

    def replace_then_publish(source: str, target: str, expected_revision: str) -> str:
        stage.unlink()
        stage.write_bytes(b"replacement")
        return real_publish(source, target, expected_revision)

    monkeypatch.setattr(local_fs, "_unrooted_atomic_publish_no_replace", replace_then_publish)

    with pytest.raises(ConflictError, match="stage changed"):
        await fs.atomic_publish_no_replace(
            staged,
            published,
            expected_source_revision=expected,
        )

    assert stage.read_bytes() == b"replacement"
    assert not destination.exists()


# ---------------------------------------------------------------------------
# Streaming I/O
# ---------------------------------------------------------------------------


async def test_write_then_read_small(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    await fs.write_stream(PathRef.from_posix("/h.txt"), _agen([b"hi"]))
    out = await _drain(await fs.read_stream(PathRef.from_posix("/h.txt")))
    assert out == b"hi"


async def test_write_then_read_roundtrip_16mb(tmp_path: Path) -> None:
    """16 MiB round-trip — exercises multi-chunk read path."""
    fs = _make_fs(tmp_path)
    payload = os.urandom(16 * 1024 * 1024)

    async def src() -> AsyncIterator[bytes]:
        for i in range(0, len(payload), 1 << 20):
            yield payload[i : i + (1 << 20)]

    await fs.write_stream(PathRef.from_posix("/big.bin"), src(), total_size=len(payload))
    out = await _drain(
        await fs.read_stream(PathRef.from_posix("/big.bin"), chunk_size=4 * 1024 * 1024)
    )
    assert out == payload


async def test_progress_callback_called(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    seen: list[int] = []

    def cb(p: TransferProgress) -> None:
        seen.append(p.bytes_transferred)

    await fs.write_stream(
        PathRef.from_posix("/p"),
        _agen([b"abc", b"def", b"gh"]),
        total_size=8,
        progress=cb,
    )
    assert seen == [3, 6, 8]


async def test_read_missing_raises(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(NotFoundError):
        await _drain(await fs.read_stream(PathRef.from_posix("/nope")))


async def test_read_stream_does_not_open_until_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import local_fs

    (tmp_path / "payload.txt").write_bytes(b"payload")
    calls = 0
    real_open = local_fs._rooted_open

    def _recording_open(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(local_fs, "_rooted_open", _recording_open)
    stream = await LocalFS(root=tmp_path).read_stream(PathRef.from_posix("/payload.txt"))

    assert calls == 0
    await stream.aclose()
    assert calls == 0


async def test_read_stream_closes_descriptor_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain import local_fs

    (tmp_path / "payload.txt").write_bytes(b"payload")
    closed: list[int] = []
    open_name = "_windows_open" if os.name == "nt" else "_rooted_open"
    monkeypatch.setattr(local_fs, open_name, lambda *_args, **_kwargs: 777)
    monkeypatch.setattr(local_fs.os, "fstat", lambda _fd: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(local_fs.os, "close", closed.append)

    stream = await LocalFS(root=tmp_path).read_stream(PathRef.from_posix("/payload.txt"))
    with pytest.raises(ProviderError):
        await _drain(stream)

    assert closed == [777]


# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------


async def test_symlink_reported_with_symlink_kind(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"x")
    link = tmp_path / "link.txt"
    _symlink_or_skip(link, target)
    fs = _make_fs(tmp_path)
    entries = await fs.list(PathRef(()))
    by_name = {e.name: e for e in entries}
    assert by_name["link.txt"].kind == EntryKind.SYMLINK


async def test_delete_symlink_removes_link_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"keep")
    link = tmp_path / "link.txt"
    _symlink_or_skip(link, target)

    await _make_fs(tmp_path).delete(PathRef.from_posix("/link.txt"))

    assert target.read_bytes() == b"keep"
    assert not link.exists()
    assert not link.is_symlink()


async def test_delete_symlink_to_outside_sandbox_removes_link(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_bytes(b"keep")
    link = tmp_path / "outside-link.txt"
    _symlink_or_skip(link, outside)
    try:
        await _make_fs(tmp_path).delete(PathRef.from_posix("/outside-link.txt"))

        assert outside.read_bytes() == b"keep"
        assert not link.is_symlink()
    finally:
        outside.unlink(missing_ok=True)


async def test_rename_symlink_moves_link_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"keep")
    link = tmp_path / "link.txt"
    _symlink_or_skip(link, target)

    await _make_fs(tmp_path).rename(
        PathRef.from_posix("/link.txt"), PathRef.from_posix("/renamed.txt")
    )

    renamed = tmp_path / "renamed.txt"
    assert not link.is_symlink()
    assert renamed.is_symlink()
    assert renamed.read_bytes() == b"keep"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
async def test_read_refuses_leaf_replaced_with_external_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    root = tmp_path / "root"
    root.mkdir()
    leaf = root / "file"
    leaf.write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    real_open = local_fs._open_nofollow

    def race(path: str, flags: int, **kwargs: int | None) -> int:
        leaf.unlink()
        leaf.symlink_to(outside)
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(local_fs, "_open_nofollow", race)

    with pytest.raises(ConflictError, match="refusing symlink"):
        await _drain(await LocalFS(root=root).read_stream(PathRef.from_posix("/file")))

    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
async def test_write_refuses_leaf_replaced_with_external_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    root = tmp_path / "root"
    root.mkdir()
    leaf = root / "file"
    leaf.write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    real_open = local_fs._open_nofollow

    def race(path: str, flags: int, **kwargs: int | None) -> int:
        leaf.unlink()
        leaf.symlink_to(outside)
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(local_fs, "_open_nofollow", race)

    with pytest.raises(ConflictError, match="refusing symlink"):
        await LocalFS(root=root).write_stream(PathRef.from_posix("/file"), _agen([b"replacement"]))

    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
async def test_read_cannot_escape_when_intermediate_directory_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    root = tmp_path / "root"
    parent = root / "parent"
    parent.mkdir(parents=True)
    (parent / "file").write_bytes(b"inside")
    displaced = root / "displaced"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_bytes(b"outside")
    real_open = local_fs._open_nofollow
    raced = False

    def race(path: str, flags: int, **kwargs: int | None) -> int:
        nonlocal raced
        if not raced:
            raced = True
            parent.rename(displaced)
            parent.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(local_fs, "_open_nofollow", race)

    try:
        stream = await LocalFS(root=root).read_stream(PathRef.from_posix("/parent/file"))
        result = await _drain(stream)
    except (ConflictError, ProviderError):
        result = b"inside"

    assert result == b"inside"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
async def test_write_cannot_escape_when_intermediate_directory_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    root = tmp_path / "root"
    parent = root / "parent"
    parent.mkdir(parents=True)
    (parent / "file").write_bytes(b"inside")
    displaced = root / "displaced"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "file"
    outside_file.write_bytes(b"outside")
    real_open = local_fs._open_nofollow
    raced = False

    def race(path: str, flags: int, **kwargs: int | None) -> int:
        nonlocal raced
        if not raced:
            raced = True
            parent.rename(displaced)
            parent.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(local_fs, "_open_nofollow", race)

    with suppress(ConflictError, ProviderError):
        await LocalFS(root=root).write_stream(
            PathRef.from_posix("/parent/file"), _agen([b"replacement"])
        )

    assert outside_file.read_bytes() == b"outside"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd capability contract")
async def test_rooted_read_fails_when_secure_relative_traversal_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    (tmp_path / "file").write_bytes(b"inside")
    monkeypatch.setattr(local_fs, "_supports_secure_dir_fd", lambda: False, raising=False)

    with pytest.raises(ProviderError, match="secure relative filesystem operations"):
        await _drain(await LocalFS(root=tmp_path).read_stream(PathRef.from_posix("/file")))


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow capability contract")
async def test_unrooted_read_fails_when_no_follow_open_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"inside")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    with pytest.raises(ProviderError, match="no-follow file opens"):
        await _drain(await LocalFS().read_stream(PathRef.from_posix(target.as_posix())))


async def test_listing_rejects_results_beyond_safety_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aws_tui.domain import local_fs

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    monkeypatch.setattr(local_fs, "_MAX_LISTING_ENTRIES", 1)

    with pytest.raises(ProviderError, match="listing safety limit"):
        await LocalFS(root=tmp_path).list(PathRef(()))


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="root bypasses permission bits; Windows perms differ",
)
async def test_permission_denied_on_unreadable_dir(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "inside.txt").write_bytes(b"x")
    secret.chmod(0)
    try:
        fs = _make_fs(tmp_path)
        with pytest.raises(PermissionDeniedError):
            await fs.list(PathRef.from_posix("/secret"))
    finally:
        # Restore so pytest can clean up tmp_path.
        secret.chmod(stat.S_IRWXU)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
@pytest.mark.asyncio
async def test_failed_etag_delete_restores_the_quarantined_original(tmp_path: Path) -> None:
    """A failed delete must not leave the entry renamed to its hidden claim.

    The ``expected_etag`` path renames the target to
    ``.<name>.aws-tui-delete-<uuid>`` to claim it. If the removal then fails
    partway, returning without restoring left the caller's directory gone from
    the listing and present only as a dotfile, while the operation reported an
    error. Reachable in production through ``CrossFsMove``, whose source delete
    passes ``expected_etag``.
    """
    payload = tmp_path / "payload"
    (payload / "sub").mkdir(parents=True)
    (payload / "sub" / "f.txt").write_text("x", encoding="utf-8")
    (payload / "sub").chmod(0o500)  # unwritable: rmtree fails inside the tree
    fs = LocalFS()
    ref = PathRef((str(payload),))
    entry = await fs.stat(ref)

    try:
        with pytest.raises(PermissionDeniedError):
            await fs.delete(ref, expected_etag=entry.etag)

        assert payload.is_dir(), "the original must be restored"
        assert [p.name for p in tmp_path.iterdir()] == ["payload"]
    finally:
        (payload / "sub").chmod(0o700)


# ── Unrooted LocalFS: the only production configuration ──────────────────────
#
# `composition.py` builds `S3Service` without a `local_root`, and nothing in
# `src/` ever sets one, so `LocalFS()` with no root is what every local-pane
# operation runs against. A census of the whole suite found unrooted `mkdir`,
# `write_stream` and `delete_empty_directory` were never called at all, and no
# unrooted `delete` ever SUCCEEDED anywhere — the calls that existed were two
# rejections and one induced-failure restore. The tests below exercise the
# unrooted success paths and the guards that protect them.


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_delete_removes_a_file_and_a_directory(tmp_path: Path) -> None:
    """No unrooted delete succeeded anywhere in the suite before this.

    `local_fs.py:416` routes directories to `shutil.rmtree` and everything else
    to `unlink`. All four operand mutations of that branch survived: inverting
    it broke directory deletion entirely (or file deletion entirely) while the
    only existing test still passed, because it asserts `PermissionDeniedError`
    and gets one either way.
    """
    fs = LocalFS()
    victim_file = tmp_path / "gone.txt"
    victim_file.write_bytes(b"bye")
    victim_dir = tmp_path / "tree"
    (victim_dir / "nested").mkdir(parents=True)
    (victim_dir / "nested" / "leaf.txt").write_bytes(b"leaf")

    await fs.delete(PathRef.from_posix(str(victim_file)))
    assert not victim_file.exists(), "unrooted file delete did not remove the file"

    await fs.delete(PathRef.from_posix(str(victim_dir)))
    assert not victim_dir.exists(), "unrooted directory delete did not remove the tree"


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_conditional_delete_refuses_a_changed_source(tmp_path: Path) -> None:
    """`CrossFsMove` deletes the source only after a revision match.

    That check is the sole protection against deleting a source the user
    modified after the copy read it. Neutralising any of its three lines let the
    delete proceed, destroying the newer content while the destination kept the
    older bytes.
    """
    fs = LocalFS()
    target = tmp_path / "f.txt"
    target.write_bytes(b"ORIGINAL")
    ref = PathRef.from_posix(str(target))
    stale = (await fs.stat(ref)).etag

    target.write_bytes(b"NEWER USER DATA")

    with pytest.raises(ConflictError):
        await fs.delete(ref, expected_etag=stale)

    assert target.read_bytes() == b"NEWER USER DATA", "newer content was destroyed"

    # Positive control: the same call succeeds against a current revision.
    current = (await fs.stat(ref)).etag
    await fs.delete(ref, expected_etag=current)
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_delete_empty_directory_refuses_a_non_empty_one(tmp_path: Path) -> None:
    """A non-empty directory must survive, visibly.

    Two mutations made this report success while renaming the directory to a
    hidden `.<name>.aws-tui-rmdir-<uuid>` quarantine — so the user's directory
    and everything in it appeared to vanish while the operation claimed to have
    worked.
    """
    fs = LocalFS()
    occupied = tmp_path / "busy"
    occupied.mkdir()
    (occupied / "child.txt").write_bytes(b"still here")

    with pytest.raises(ConflictError):
        await fs.delete_empty_directory(PathRef.from_posix(str(occupied)))

    assert occupied.is_dir(), "the directory was not restored after the refusal"
    assert (occupied / "child.txt").read_bytes() == b"still here"
    hidden = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert hidden == [], f"the directory was left quarantined under {hidden}"

    # Positive control: an actually-empty directory is removed.
    empty = tmp_path / "empty"
    empty.mkdir()
    await fs.delete_empty_directory(PathRef.from_posix(str(empty)))
    assert not empty.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_rename_refuses_to_replace_an_existing_destination(
    tmp_path: Path,
) -> None:
    """`_rename_no_replace` must not clobber a destination the user never named.

    Forcing its Windows branch made it fall through to `Path.rename`, which
    silently replaces. Nothing caught it: unrooted rename had exactly one call
    in the suite, a dot-segment rejection that never reaches this code.
    """
    fs = LocalFS()
    source = tmp_path / "src.txt"
    source.write_bytes(b"source")
    occupied = tmp_path / "dst.txt"
    occupied.write_bytes(b"PRE-EXISTING")

    with pytest.raises(ConflictError):
        await fs.rename(PathRef.from_posix(str(source)), PathRef.from_posix(str(occupied)))

    assert occupied.read_bytes() == b"PRE-EXISTING", "rename replaced the destination"
    assert source.read_bytes() == b"source", "rename consumed the source anyway"

    # Positive control: renaming onto a free name works.
    free = tmp_path / "free.txt"
    await fs.rename(PathRef.from_posix(str(source)), PathRef.from_posix(str(free)))
    assert free.read_bytes() == b"source"
    assert not source.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_mkdir_and_write_stream_actually_create(tmp_path: Path) -> None:
    """Neither had a single unrooted call anywhere in the suite.

    Both bodies could be replaced with `pass` while reporting success.
    """
    fs = LocalFS()
    made = tmp_path / "made"
    await fs.mkdir(PathRef.from_posix(str(made)))
    assert made.is_dir(), "unrooted mkdir reported success without creating anything"

    written = made / "out.txt"
    await fs.write_stream(PathRef.from_posix(str(written)), _agen([b"payload"]))
    assert written.read_bytes() == b"payload", "unrooted write_stream wrote nothing"


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_directory_publish_validates_its_manifest(tmp_path: Path) -> None:
    """A staged tree that changed after staging must be refused, not published.

    `_validate_stage_manifest` closes the TOCTOU window between cross_fs's own
    verification and the rename. Every one of its mutations survived — the
    unrooted directory-publish path had ZERO test calls — so a tree tampered
    with after staging published as if intact, and the transactional guarantee
    failed open.
    """
    fs = LocalFS()
    staged = tmp_path / "stage"
    staged.mkdir()
    (staged / "expected.txt").write_bytes(b"expected")
    destination = tmp_path / "published"
    staged_ref = PathRef.from_posix(str(staged))
    manifest = tuple(
        __import__(
            "aws_tui.domain.local_fs", fromlist=["_unrooted_stage_manifest"]
        )._unrooted_stage_manifest(staged)
    )

    # Tamper AFTER the manifest was captured.
    (staged / "smuggled.txt").write_bytes(b"tampered in")

    with pytest.raises(ProviderError, match="stage manifest changed"):
        await fs.atomic_publish_directory_no_replace(
            staged_ref,
            PathRef.from_posix(str(destination)),
            expected_manifest=manifest,
        )

    assert not destination.exists(), "a tampered stage was published anyway"


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_directory_publish_returns_the_published_revision(
    tmp_path: Path,
) -> None:
    """Positive control, and F12's pin: the publish lands and reports a revision.

    Dropping the final `return _local_etag(...)` made publish return None; in
    the OVERWRITE path the missing revision left the destination MISSING.
    """
    fs = LocalFS()
    staged = tmp_path / "stage"
    staged.mkdir()
    (staged / "file.txt").write_bytes(b"content")
    destination = tmp_path / "published"
    manifest = tuple(
        __import__(
            "aws_tui.domain.local_fs", fromlist=["_unrooted_stage_manifest"]
        )._unrooted_stage_manifest(staged)
    )

    revision = await fs.atomic_publish_directory_no_replace(
        PathRef.from_posix(str(staged)),
        PathRef.from_posix(str(destination)),
        expected_manifest=manifest,
    )

    assert revision, "publish reported no revision"
    assert (destination / "file.txt").read_bytes() == b"content"
    assert not staged.exists(), "the stage was left behind after publishing"


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrooted contract")
async def test_unrooted_claimed_stage_directory_is_owner_only(tmp_path: Path) -> None:
    """The stage container's 0o700 is the isolation the publish path relies on.

    Staged payloads pass through this directory before publication; at 0o701
    (or looser) another local user can traverse into a stage mid-transfer. The
    mode constant was mutable with the suite green — unrooted `claim_directory`
    had one caller in tests and none asserted the mode.
    """
    import stat as stat_module

    fs = LocalFS()
    claimed = tmp_path / "stage-container"

    await fs.claim_directory(PathRef.from_posix(str(claimed)))

    assert claimed.is_dir()
    mode = stat_module.S_IMODE(claimed.stat().st_mode)
    assert mode == 0o700, f"stage container is {mode:o}, not owner-only"
