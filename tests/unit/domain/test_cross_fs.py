"""Unit tests for CrossFsCopy / CrossFsMove across provider pairs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aioboto3
import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileEntry,
    FileSystemProvider,
    NotFoundError,
    PathRef,
    ProviderError,
    TransferProgress,
)
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS

# moto_server + s3_endpoint fixtures come from tests/unit/domain/conftest.py.

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _agen(blobs: list[bytes]) -> AsyncIterator[bytes]:
    for b in blobs:
        yield b


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def _put_file(fs: FileSystemProvider, path: PathRef, data: bytes) -> None:
    await fs.write_stream(path, _agen([data]))


async def _read_file(fs: FileSystemProvider, path: PathRef) -> bytes:
    return await _drain(await fs.read_stream(path))


def _s3_session() -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def _is_stage_payload(path: PathRef) -> bool:
    return path.name == "payload" and ".aws-tui-stage-" in path.parent().name


async def _make_s3fs(endpoint: str, bucket: str) -> S3FS:
    async with _s3_session().client("s3", endpoint_url=endpoint) as s3:
        await s3.create_bucket(Bucket=bucket)
    return S3FS(
        session=_s3_session(),
        bucket=bucket,
        endpoint_url=endpoint,
        force_path_style=True,
    )


# ---------------------------------------------------------------------------
# InMemory ↔ InMemory
# ---------------------------------------------------------------------------


async def test_inmem_to_inmem_file_copy() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a.txt"), b"hello")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/a.txt"), PathRef.from_posix("/a.txt"))
    assert await _read_file(dst, PathRef.from_posix("/a.txt")) == b"hello"


async def test_inmem_directory_copy_recursive() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/d"))
    await src.mkdir(PathRef.from_posix("/d/sub"))
    await _put_file(src, PathRef.from_posix("/d/a"), b"A")
    await _put_file(src, PathRef.from_posix("/d/sub/b"), b"B")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/d"), PathRef.from_posix("/d"))
    assert await _read_file(dst, PathRef.from_posix("/d/a")) == b"A"
    assert await _read_file(dst, PathRef.from_posix("/d/sub/b")) == b"B"


# ---------------------------------------------------------------------------
# LocalFS ↔ LocalFS
# ---------------------------------------------------------------------------


async def test_local_to_local_roundtrip(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    src_root.mkdir()
    dst_root.mkdir()
    (src_root / "hi.txt").write_bytes(b"hello")

    src = LocalFS(root=src_root)
    dst = LocalFS(root=dst_root)
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/hi.txt"), PathRef.from_posix("/hi.txt"))
    assert (dst_root / "hi.txt").read_bytes() == b"hello"


async def test_local_copy_rejects_destination_inside_source_across_overlapping_roots(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source-root"
    source_dir = source_root / "nested"
    destination_root = source_dir
    source_dir.mkdir(parents=True)
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ConflictError, match="inside source"):
        await asyncio.wait_for(
            CrossFsCopy(
                source=LocalFS(root=source_root),
                destination=LocalFS(root=destination_root),
            ).copy(PathRef.from_posix("/nested"), PathRef.from_posix("/child")),
            timeout=1,
        )

    assert not (source_dir / "child").exists()


# ---------------------------------------------------------------------------
# LocalFS ↔ S3FS (moto)
# ---------------------------------------------------------------------------


async def test_local_to_s3_roundtrip(s3_endpoint: str, tmp_path: Path) -> None:
    local = LocalFS(root=tmp_path)
    (tmp_path / "x.txt").write_bytes(b"hello-s3")
    s3 = await _make_s3fs(s3_endpoint, "destbucket")
    copier = CrossFsCopy(source=local, destination=s3)
    await copier.copy(PathRef.from_posix("/x.txt"), PathRef.from_posix("/x.txt"))
    assert await _read_file(s3, PathRef.from_posix("/x.txt")) == b"hello-s3"


async def test_s3_to_local_roundtrip(s3_endpoint: str, tmp_path: Path) -> None:
    s3 = await _make_s3fs(s3_endpoint, "srcbucket")
    await _put_file(s3, PathRef.from_posix("/y.txt"), b"hello-local")
    local = LocalFS(root=tmp_path)
    copier = CrossFsCopy(source=s3, destination=local)
    await copier.copy(PathRef.from_posix("/y.txt"), PathRef.from_posix("/y.txt"))
    assert (tmp_path / "y.txt").read_bytes() == b"hello-local"


async def test_s3_to_s3_roundtrip(s3_endpoint: str) -> None:
    src = await _make_s3fs(s3_endpoint, "fromb")
    dst = await _make_s3fs(s3_endpoint, "tob")
    await _put_file(src, PathRef.from_posix("/k"), b"cross-bucket")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(PathRef.from_posix("/k"), PathRef.from_posix("/k"))
    assert await _read_file(dst, PathRef.from_posix("/k")) == b"cross-bucket"


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


async def test_conflict_error_raises() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"src")
    await _put_file(dst, PathRef.from_posix("/a"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    with pytest.raises(ConflictError):
        await copier.copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.ERROR,
        )


async def test_conflict_overwrite_replaces() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"src")
    await _put_file(dst, PathRef.from_posix("/a"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(
        PathRef.from_posix("/a"),
        PathRef.from_posix("/a"),
        on_conflict=ConflictResolution.OVERWRITE,
    )
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"src"


async def test_file_overwrite_rejects_existing_directory() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")
    await dst.mkdir(PathRef.from_posix("/target"))

    with pytest.raises(ConflictError, match="non-file destination"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"),
            PathRef.from_posix("/target"),
            on_conflict=ConflictResolution.OVERWRITE,
        )


async def test_committed_overwrite_surfaces_backup_cleanup_failure() -> None:
    class _CleanupFailingFS(InMemoryFS):
        atomic_write_replaces = False

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            if ".aws-tui-backup-" in path.name:
                raise OSError("cleanup failed")
            await super().delete(path, expected_etag=expected_etag)

    src = InMemoryFS()
    dst = _CleanupFailingFS()
    await _put_file(src, PathRef.from_posix("/a"), b"replacement")
    await _put_file(dst, PathRef.from_posix("/a"), b"original")

    with pytest.raises(ProviderError, match="backup cleanup failed"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.OVERWRITE,
        )

    assert await _read_file(dst, PathRef.from_posix("/a")) == b"replacement"
    assert any(".aws-tui-backup-" in entry.name for entry in await dst.list(PathRef(())))


async def test_overwrite_surfaces_failed_restore_and_preserves_backup() -> None:
    class _DoubleFailingFS(InMemoryFS):
        atomic_write_replaces = False

        async def atomic_publish_no_replace(
            self, src: PathRef, dst: PathRef, *, expected_source_revision: str
        ) -> str:
            if _is_stage_payload(src) and dst.name == "a":
                raise RuntimeError("commit failed")
            if ".aws-tui-backup-" in src.name and dst.name == "a":
                raise RuntimeError("restore failed")
            return await super().atomic_publish_no_replace(
                src, dst, expected_source_revision=expected_source_revision
            )

    src = InMemoryFS()
    dst = _DoubleFailingFS()
    await _put_file(src, PathRef.from_posix("/a"), b"replacement")
    await _put_file(dst, PathRef.from_posix("/a"), b"original")

    with pytest.raises(ProviderError, match=r"rollback was incomplete.*restore failed"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.OVERWRITE,
        )

    entries = await dst.list(PathRef(()))
    backup = next(entry for entry in entries if ".aws-tui-backup-" in entry.name)
    assert await _read_file(dst, PathRef.from_posix(f"/{backup.name}")) == b"original"


async def test_conflict_overwrite_failure_preserves_existing_destination() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"replacement")
    await _put_file(dst, PathRef.from_posix("/a"), b"original")

    async def failing_stream(
        _path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        del chunk_size

        async def chunks() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("source failed")

        return chunks()

    src.read_stream = failing_stream  # type: ignore[method-assign]
    copier = CrossFsCopy(source=src, destination=dst)

    with pytest.raises(RuntimeError, match="source failed"):
        await copier.copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.OVERWRITE,
        )

    assert await _read_file(dst, PathRef.from_posix("/a")) == b"original"


async def test_repeated_cancellation_durably_restores_overwrite_backup() -> None:
    class _BlockingRestoreFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.restore_started = asyncio.Event()
            self.release_restore = asyncio.Event()

        async def atomic_publish_no_replace(
            self, src: PathRef, dst: PathRef, *, expected_source_revision: str
        ) -> str:
            if _is_stage_payload(src) and dst.name == "a":
                raise RuntimeError("commit failed")
            if ".aws-tui-backup-" in src.name:
                self.restore_started.set()
                await self.release_restore.wait()
            return await super().atomic_publish_no_replace(
                src, dst, expected_source_revision=expected_source_revision
            )

    src = InMemoryFS()
    dst = _BlockingRestoreFS()
    await _put_file(src, PathRef.from_posix("/a"), b"replacement")
    await _put_file(dst, PathRef.from_posix("/a"), b"original")
    copy = asyncio.create_task(
        CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.OVERWRITE,
        )
    )
    await dst.restore_started.wait()

    copy.cancel()
    copy.cancel()
    await asyncio.sleep(0)
    assert not copy.done()
    dst.release_restore.set()
    with pytest.raises(asyncio.CancelledError):
        await copy

    assert await _read_file(dst, PathRef.from_posix("/a")) == b"original"
    assert {entry.name for entry in await dst.list(PathRef(()))} == {"a"}


async def test_cancellation_after_backup_rename_restores_original_destination() -> None:
    class _CommittedBackupFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.backup_committed = asyncio.Event()
            self.release_backup = asyncio.Event()

        async def atomic_publish_no_replace(
            self, src: PathRef, dst: PathRef, *, expected_source_revision: str
        ) -> str:
            revision = await super().atomic_publish_no_replace(
                src, dst, expected_source_revision=expected_source_revision
            )
            if src.name == "a" and ".aws-tui-backup-" in dst.name:
                self.backup_committed.set()
                await self.release_backup.wait()
            return revision

    src = InMemoryFS()
    dst = _CommittedBackupFS()
    await _put_file(src, PathRef.from_posix("/a"), b"replacement")
    await _put_file(dst, PathRef.from_posix("/a"), b"original")
    copy = asyncio.create_task(
        CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.OVERWRITE,
        )
    )
    await dst.backup_committed.wait()

    copy.cancel()
    dst.release_backup.set()
    with pytest.raises(asyncio.CancelledError):
        await copy

    assert await _read_file(dst, PathRef.from_posix("/a")) == b"original"
    assert {entry.name for entry in await dst.list(PathRef(()))} == {"a"}


async def test_overwrite_retries_backup_setup_across_disappear_appear_races() -> None:
    class _RacingBackupFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.disappeared = False
            self.appeared = False
            self.cleaned_backups: list[bytes] = []

        async def atomic_publish_no_replace(
            self,
            staged: PathRef,
            destination: PathRef,
            *,
            expected_source_revision: str,
        ) -> str:
            if staged.name == "a" and ".aws-tui-backup-" in destination.name:
                if not self.disappeared:
                    self.disappeared = True
                    await InMemoryFS.delete(self, staged, expected_etag=expected_source_revision)
                    raise NotFoundError(staged.as_posix())
            elif _is_stage_payload(staged) and destination.name == "a" and not self.appeared:
                self.appeared = True
                await InMemoryFS.write_stream(self, destination, _agen([b"concurrent"]))
            return await super().atomic_publish_no_replace(
                staged,
                destination,
                expected_source_revision=expected_source_revision,
            )

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            if ".aws-tui-backup-" in path.name:
                node = self._tree[path]
                assert isinstance(node, bytes)
                self.cleaned_backups.append(node)
            await super().delete(path, expected_etag=expected_etag)

    src = InMemoryFS()
    dst = _RacingBackupFS()
    await _put_file(src, PathRef.from_posix("/source"), b"replacement")
    await _put_file(dst, PathRef.from_posix("/a"), b"original")

    assert await CrossFsCopy(source=src, destination=dst).copy(
        PathRef.from_posix("/source"),
        PathRef.from_posix("/a"),
        on_conflict=ConflictResolution.OVERWRITE,
    )

    assert dst.disappeared
    assert dst.appeared
    assert dst.cleaned_backups == [b"concurrent"]
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"replacement"
    assert {entry.name for entry in await dst.list(PathRef(()))} == {"a"}


async def test_atomic_overwrite_failure_never_deletes_existing_destination() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    dst.atomic_write_replaces = True  # type: ignore[attr-defined]
    await _put_file(src, PathRef.from_posix("/a"), b"replacement")
    await _put_file(dst, PathRef.from_posix("/a"), b"original")
    original_write_stream = dst.write_stream

    async def failing_write(
        path: PathRef,
        data: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: object | None = None,
        overwrite: bool = True,
    ) -> None:
        del path, data, total_size, progress, overwrite
        raise RuntimeError("atomic upload failed")

    dst.write_stream = failing_write  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="atomic upload failed"):
            await CrossFsCopy(source=src, destination=dst).copy(
                PathRef.from_posix("/a"),
                PathRef.from_posix("/a"),
                on_conflict=ConflictResolution.OVERWRITE,
            )
    finally:
        dst.write_stream = original_write_stream  # type: ignore[method-assign]

    assert await _read_file(dst, PathRef.from_posix("/a")) == b"original"


async def test_atomic_failed_new_write_does_not_delete_concurrent_destination() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    dst.atomic_write_replaces = True  # type: ignore[attr-defined]
    await _put_file(src, PathRef.from_posix("/a"), b"source")
    original_write = dst.write_stream

    async def failing_write(
        path: PathRef,
        data: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: object | None = None,
        overwrite: bool = True,
    ) -> None:
        del data, total_size, progress, overwrite
        await original_write(path, _agen([b"concurrent"]))
        raise RuntimeError("upload failed")

    dst.write_stream = failing_write  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="upload failed"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/a"), PathRef.from_posix("/a")
        )

    assert await _read_file(dst, PathRef.from_posix("/a")) == b"concurrent"


@pytest.mark.parametrize(
    ("policy", "expected_result", "expected_paths"),
    [
        (ConflictResolution.ERROR, ConflictError, {"target"}),
        (ConflictResolution.SKIP, False, {"target"}),
        (ConflictResolution.RENAME, True, {"target", "target (1)"}),
        (ConflictResolution.OVERWRITE, True, {"target"}),
    ],
)
async def test_non_atomic_file_publish_preserves_conflict_policy_races(
    policy: ConflictResolution,
    expected_result: bool | type[ConflictError],
    expected_paths: set[str],
) -> None:
    class _RacingPublishFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.publish_calls = 0

        async def atomic_publish_no_replace(
            self,
            staged: PathRef,
            destination: PathRef,
            *,
            expected_source_revision: str,
        ) -> str:
            self.publish_calls += 1
            if self.publish_calls == 1:
                await InMemoryFS.write_stream(self, destination, _agen([b"concurrent"]))
            return await InMemoryFS.atomic_publish_no_replace(
                self,
                staged,
                destination,
                expected_source_revision=expected_source_revision,
            )

    src = InMemoryFS()
    dst = _RacingPublishFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")

    copy = CrossFsCopy(source=src, destination=dst).copy(
        PathRef.from_posix("/source"),
        PathRef.from_posix("/target"),
        on_conflict=policy,
    )
    if expected_result is ConflictError:
        with pytest.raises(ConflictError):
            await copy
    else:
        assert await copy is expected_result

    assert dst.publish_calls >= 1
    assert {entry.name for entry in await dst.list(PathRef(()))} == expected_paths
    if policy in {ConflictResolution.ERROR, ConflictResolution.SKIP}:
        assert await _read_file(dst, PathRef.from_posix("/target")) == b"concurrent"
    elif policy == ConflictResolution.RENAME:
        assert await _read_file(dst, PathRef.from_posix("/target")) == b"concurrent"
        assert await _read_file(dst, PathRef.from_posix("/target (1)")) == b"source"
    else:
        assert await _read_file(dst, PathRef.from_posix("/target")) == b"source"


async def test_non_atomic_file_publish_does_not_trust_replace_on_rename_provider() -> None:
    class _ReplaceOnRenameFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.rename_calls = 0

        async def rename(self, src: PathRef, dst: PathRef) -> None:
            self.rename_calls += 1
            if _is_stage_payload(src):
                await InMemoryFS.write_stream(self, dst, _agen([b"concurrent"]))
            if dst in self._tree:
                await InMemoryFS.delete(self, dst)
            await InMemoryFS.rename(self, src, dst)

    src = InMemoryFS()
    dst = _ReplaceOnRenameFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")
    await _put_file(dst, PathRef.from_posix("/target"), b"existing")

    assert await CrossFsCopy(source=src, destination=dst).copy(
        PathRef.from_posix("/source"),
        PathRef.from_posix("/target"),
        on_conflict=ConflictResolution.OVERWRITE,
    )

    assert dst.rename_calls == 0
    assert await _read_file(dst, PathRef.from_posix("/target")) == b"source"


async def test_competing_file_stage_claim_is_not_cleaned_as_owned() -> None:
    class _CompetingStageFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.competitor: PathRef | None = None

        async def claim_directory(self, path: PathRef) -> str:
            if self.competitor is None:
                self.competitor = path
                await InMemoryFS.claim_directory(self, path)
                raise ConflictError(path.as_posix())
            return await super().claim_directory(path)

    src = InMemoryFS()
    dst = _CompetingStageFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")

    assert await CrossFsCopy(source=src, destination=dst).copy(
        PathRef.from_posix("/source"), PathRef.from_posix("/target")
    )

    assert dst.competitor is not None
    assert (await dst.stat(dst.competitor)).kind == EntryKind.DIRECTORY
    assert await _read_file(dst, PathRef.from_posix("/target")) == b"source"


async def test_replaced_file_stage_is_preserved_and_not_published() -> None:
    class _ReplacingStageFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.replaced_stage: PathRef | None = None

        async def atomic_publish_no_replace(
            self,
            staged: PathRef,
            destination: PathRef,
            *,
            expected_source_revision: str,
        ) -> str:
            if self.replaced_stage is None and _is_stage_payload(staged):
                original = self._tree[staged]
                assert isinstance(original, bytes)
                await InMemoryFS.delete(self, staged)
                await InMemoryFS.write_stream(self, staged, _agen([original]))
                self.replaced_stage = staged
            return await super().atomic_publish_no_replace(
                staged,
                destination,
                expected_source_revision=expected_source_revision,
            )

    src = InMemoryFS()
    dst = _ReplacingStageFS()
    await _put_file(src, PathRef.from_posix("/source"), b"same-content")

    with pytest.raises(ProviderError, match="stage"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert dst.replaced_stage is not None
    assert await _read_file(dst, dst.replaced_stage) == b"same-content"
    with pytest.raises(NotFoundError):
        await dst.stat(PathRef.from_posix("/target"))


async def test_cancelled_file_publish_failure_keeps_cancellation_primary() -> None:
    class _FailingPublishFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.publish_started = asyncio.Event()
            self.release_publish = asyncio.Event()

        async def atomic_publish_no_replace(
            self,
            staged: PathRef,
            destination: PathRef,
            *,
            expected_source_revision: str,
        ) -> str:
            del staged, destination
            del expected_source_revision
            self.publish_started.set()
            await self.release_publish.wait()
            raise RuntimeError("commit failed")

    src = InMemoryFS()
    dst = _FailingPublishFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")
    copy = asyncio.create_task(
        CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )
    )
    await asyncio.wait_for(dst.publish_started.wait(), timeout=1)

    copy.cancel()
    copy.cancel()
    dst.release_publish.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await copy

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert any("commit failed" in note for note in raised.value.__notes__)
    assert await dst.list(PathRef(())) == []


async def test_cancelled_stage_cleanup_failure_keeps_cancellation_primary() -> None:
    class _CleanupFailingFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.cleanup_started = asyncio.Event()
            self.release_cleanup = asyncio.Event()

        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            del total_size, progress, overwrite
            self._tree[path] = b"partial"
            self._touch(path)
            async for _chunk in source:
                pass

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            del path, expected_etag
            self.cleanup_started.set()
            await self.release_cleanup.wait()
            raise OSError("cleanup failed")

    src = InMemoryFS()
    dst = _CleanupFailingFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")

    async def failing_stream(
        _path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        del chunk_size

        async def chunks() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("source failed")

        return chunks()

    src.read_stream = failing_stream  # type: ignore[method-assign]
    copy = asyncio.create_task(
        CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )
    )
    await asyncio.wait_for(dst.cleanup_started.wait(), timeout=1)

    copy.cancel()
    copy.cancel()
    dst.release_cleanup.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await copy

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert "source failed" in str(raised.value.__cause__)
    assert any("cleanup failed" in note for note in raised.value.__notes__)


async def test_self_cancelled_cleanup_is_primary_over_original_failure() -> None:
    class _SelfCancellingCleanupFS(InMemoryFS):
        atomic_write_replaces = False

        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            del total_size, progress, overwrite
            self._tree[path] = b"partial"
            self._touch(path)
            async for _chunk in source:
                pass

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            del path, expected_etag
            raise asyncio.CancelledError("cleanup self-cancelled")

    src = InMemoryFS()
    dst = _SelfCancellingCleanupFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")

    async def failing_stream(
        _path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        del chunk_size

        async def chunks() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("source failed")

        return chunks()

    src.read_stream = failing_stream  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError) as raised:
        await CrossFsMove(source=src, destination=dst).move(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert any("source failed" in note for note in raised.value.__notes__)


async def test_repeated_cancellation_durably_removes_failed_file_write() -> None:
    class _BlockingCleanupFS(InMemoryFS):
        atomic_write_replaces = False

        def __init__(self) -> None:
            super().__init__()
            self.cleanup_started = asyncio.Event()
            self.release_cleanup = asyncio.Event()

        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            del source, total_size, progress, overwrite
            self._tree[path] = b"partial"
            self._touch(path)
            raise RuntimeError("write failed")

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            self.cleanup_started.set()
            await self.release_cleanup.wait()
            await super().delete(path, expected_etag=expected_etag)

    src = InMemoryFS()
    dst = _BlockingCleanupFS()
    await _put_file(src, PathRef.from_posix("/a"), b"source")
    copy = asyncio.create_task(
        CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/a"), PathRef.from_posix("/a")
        )
    )
    await dst.cleanup_started.wait()

    copy.cancel()
    copy.cancel()
    await asyncio.sleep(0)
    assert not copy.done()
    dst.release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await copy

    with pytest.raises(NotFoundError):
        await dst.stat(PathRef.from_posix("/a"))


async def test_directory_overwrite_failure_preserves_existing_tree() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/good"), b"new")
    await _put_file(src, PathRef.from_posix("/source/bad"), b"fail")
    await dst.mkdir(PathRef.from_posix("/target"))
    await _put_file(dst, PathRef.from_posix("/target/original"), b"preserved")
    original_read_stream = src.read_stream

    async def failing_stream(
        path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        if path.name == "bad":

            async def chunks() -> AsyncIterator[bytes]:
                yield b"partial"
                raise RuntimeError("source failed")

            return chunks()
        return await original_read_stream(path, chunk_size=chunk_size)

    src.read_stream = failing_stream  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="source failed"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"),
            PathRef.from_posix("/target"),
            on_conflict=ConflictResolution.OVERWRITE,
        )

    assert await _read_file(dst, PathRef.from_posix("/target/original")) == b"preserved"
    assert {entry.name for entry in await dst.list(PathRef(()))} == {"target"}


async def test_absent_directory_second_child_failure_never_exposes_final_tree() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/a"), b"first")
    await _put_file(src, PathRef.from_posix("/source/b"), b"second")
    original_read_stream = src.read_stream

    async def failing_second_child(
        path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        if path.name == "b":

            async def chunks() -> AsyncIterator[bytes]:
                yield b"partial"
                raise RuntimeError("second child failed")

            return chunks()
        return await original_read_stream(path, chunk_size=chunk_size)

    src.read_stream = failing_second_child  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="second child failed"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    with pytest.raises(NotFoundError):
        await dst.stat(PathRef.from_posix("/target"))
    assert await dst.list(PathRef(())) == []


async def test_renamed_directory_second_child_failure_never_exposes_final_tree() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/a"), b"first")
    await _put_file(src, PathRef.from_posix("/source/b"), b"second")
    await _put_file(dst, PathRef.from_posix("/target"), b"existing")
    original_read_stream = src.read_stream

    async def failing_second_child(
        path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        if path.name == "b":

            async def chunks() -> AsyncIterator[bytes]:
                raise RuntimeError("second child failed")
                yield b""  # pragma: no cover

            return chunks()
        return await original_read_stream(path, chunk_size=chunk_size)

    src.read_stream = failing_second_child  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="second child failed"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"),
            PathRef.from_posix("/target"),
            on_conflict=ConflictResolution.RENAME,
        )

    assert await _read_file(dst, PathRef.from_posix("/target")) == b"existing"
    assert {entry.name for entry in await dst.list(PathRef(()))} == {"target"}


async def test_competing_directory_stage_claim_is_not_cleaned_as_owned() -> None:
    class _CompetingClaimFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.competitor: PathRef | None = None

        async def claim_directory(self, path: PathRef) -> str:
            if self.competitor is None:
                self.competitor = path
                await InMemoryFS.mkdir(self, path)
                raise ConflictError(path.as_posix())
            if path in self._tree:
                raise ConflictError(path.as_posix())
            if path.parent() not in self._tree:
                raise NotFoundError(path.parent().as_posix())
            self._tree[path] = None
            self._touch(path)
            return str(self._revision[path])

    src = InMemoryFS()
    dst = _CompetingClaimFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/a"), b"source")

    assert await CrossFsCopy(source=src, destination=dst).copy(
        PathRef.from_posix("/source"), PathRef.from_posix("/target")
    )

    assert dst.competitor is not None
    assert (await dst.stat(dst.competitor)).kind.value == "directory"
    assert await _read_file(dst, PathRef.from_posix("/target/a")) == b"source"


async def test_replaced_directory_stage_is_preserved_and_not_published() -> None:
    class _ReplacingClaimFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.replaced_stage: PathRef | None = None

        async def claim_directory(self, path: PathRef) -> str:
            claimed_revision = await super().claim_directory(path)
            if self.replaced_stage is None:
                await InMemoryFS.delete(self, path, expected_etag=claimed_revision)
                await InMemoryFS.claim_directory(self, path)
                self.replaced_stage = path
            return claimed_revision

    src = InMemoryFS()
    dst = _ReplacingClaimFS()
    await src.mkdir(PathRef.from_posix("/source"))

    with pytest.raises(ProviderError, match="stage"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert dst.replaced_stage is not None
    assert (await dst.stat(dst.replaced_stage)).kind == EntryKind.DIRECTORY
    with pytest.raises(NotFoundError):
        await dst.stat(PathRef.from_posix("/target"))


@pytest.mark.parametrize("tamper", ["inject", "replace"])
async def test_directory_manifest_rejects_changed_child_before_publish(tamper: str) -> None:
    class _TamperedStageFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.tampered_stage: PathRef | None = None

        async def atomic_publish_directory_no_replace(
            self,
            staged: PathRef,
            destination: PathRef,
            *,
            expected_manifest: object,
        ) -> str:
            if self.tampered_stage is None:
                if tamper == "inject":
                    await InMemoryFS.write_stream(
                        self, staged.join("injected"), _agen([b"unknown"])
                    )
                else:
                    child = staged.join("child")
                    await InMemoryFS.delete(self, child)
                    await InMemoryFS.write_stream(self, child, _agen([b"source"]))
                self.tampered_stage = staged
            return await super().atomic_publish_directory_no_replace(
                staged,
                destination,
                expected_manifest=expected_manifest,  # type: ignore[arg-type]
            )

    src = InMemoryFS()
    dst = _TamperedStageFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/child"), b"source")

    with pytest.raises(ProviderError, match="stage manifest"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert dst.tampered_stage is not None
    if tamper == "inject":
        assert await _read_file(dst, dst.tampered_stage.join("injected")) == b"unknown"
        with pytest.raises(NotFoundError):
            await dst.stat(dst.tampered_stage.join("child"))
    else:
        assert await _read_file(dst, dst.tampered_stage.join("child")) == b"source"
    with pytest.raises(NotFoundError):
        await dst.stat(PathRef.from_posix("/target"))


@pytest.mark.parametrize("tamper", ["inject", "replace"])
async def test_directory_manifest_cleanup_preserves_changed_child(tamper: str) -> None:
    class _CleanupTamperedFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.stage_payload: PathRef | None = None

        async def capture_stage_revision(self, path: PathRef) -> str:
            revision = await super().capture_stage_revision(path)
            if path.name == "first" and self.stage_payload is None:
                self.stage_payload = path.parent()
                if tamper == "inject":
                    await InMemoryFS.write_stream(
                        self, self.stage_payload.join("injected"), _agen([b"unknown"])
                    )
                else:
                    await InMemoryFS.delete(self, path)
                    await InMemoryFS.write_stream(self, path, _agen([b"first"]))
            return revision

    src = InMemoryFS()
    dst = _CleanupTamperedFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/first"), b"first")
    await _put_file(src, PathRef.from_posix("/source/second"), b"second")
    original_read_stream = src.read_stream

    async def fail_second(
        path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        if path.name == "second":
            raise RuntimeError("second child failed")
        return await original_read_stream(path, chunk_size=chunk_size)

    src.read_stream = fail_second  # type: ignore[method-assign]

    with pytest.raises(ProviderError, match="cleanup incomplete") as raised:
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert dst.stage_payload is not None
    preserved = "injected" if tamper == "inject" else "first"
    assert await _read_file(dst, dst.stage_payload.join(preserved))
    with pytest.raises(NotFoundError):
        await dst.stat(PathRef.from_posix("/target"))


async def test_existing_directory_merge_preflights_mixed_tree_before_mutation() -> None:
    class _FileFirstSourceFS(InMemoryFS):
        async def list(self, path: PathRef) -> list[FileEntry]:
            entries = await super().list(path)
            if path.name == "source":
                entries.sort(key=lambda entry: entry.name)
            return entries

    class _S3ShapedDestinationFS(InMemoryFS):
        atomic_write_replaces = True

        def __init__(self) -> None:
            super().__init__()
            self.mutations: list[PathRef] = []

        @staticmethod
        def supports_atomic_publish(kind: EntryKind) -> bool:
            return kind != EntryKind.DIRECTORY

        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            self.mutations.append(path)
            await super().write_stream(
                path,
                source,
                total_size=total_size,
                progress=progress,  # type: ignore[arg-type]
                overwrite=overwrite,
            )

    src = _FileFirstSourceFS()
    dst = _S3ShapedDestinationFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/a-file"), b"file")
    await src.mkdir(PathRef.from_posix("/source/z-directory"))
    await _put_file(src, PathRef.from_posix("/source/z-directory/child"), b"child")
    await dst.mkdir(PathRef.from_posix("/target"))
    dst.mutations.clear()
    source_before = dict(src._tree)
    destination_before = dict(dst._tree)

    with pytest.raises(ProviderError, match="transactional directory publication"):
        await CrossFsMove(source=src, destination=dst).move(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert dst.mutations == []
    assert src._tree == source_before
    assert dst._tree == destination_before


async def test_absent_directory_requires_transactional_publish_before_mutation() -> None:
    class _NoDirectoryTransactionsFS:
        def __init__(self) -> None:
            self.inner = InMemoryFS()
            self.mutations: list[str] = []

        async def list(self, path: PathRef) -> list[FileEntry]:
            return await self.inner.list(path)

        async def stat(self, path: PathRef) -> FileEntry:
            return await self.inner.stat(path)

        async def mkdir(self, path: PathRef) -> None:
            self.mutations.append("mkdir")
            await self.inner.mkdir(path)

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            self.mutations.append("delete")
            await self.inner.delete(path, expected_etag=expected_etag)

        async def delete_empty_directory(self, path: PathRef) -> None:
            self.mutations.append("delete_empty_directory")
            await self.inner.delete_empty_directory(path)

        async def rename(self, src: PathRef, dst: PathRef) -> None:
            self.mutations.append("rename")
            await self.inner.rename(src, dst)

        async def read_stream(
            self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
        ) -> AsyncIterator[bytes]:
            return await self.inner.read_stream(path, chunk_size=chunk_size)

        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            self.mutations.append("write")
            await self.inner.write_stream(
                path,
                source,
                total_size=total_size,
                progress=progress,  # type: ignore[arg-type]
                overwrite=overwrite,
            )

    src = InMemoryFS()
    dst = _NoDirectoryTransactionsFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/a"), b"source")

    with pytest.raises(ProviderError, match="transactional directory publication"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert dst.mutations == []


async def test_repeated_cancellation_durably_removes_failed_directory_stage() -> None:
    class _BlockingStageCleanupFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.cleanup_started = asyncio.Event()
            self.release_cleanup = asyncio.Event()

        async def delete_empty_directory(self, path: PathRef) -> None:
            if ".aws-tui-stage-" in path.name:
                self.cleanup_started.set()
                await self.release_cleanup.wait()
            await super().delete_empty_directory(path)

    src = InMemoryFS()
    dst = _BlockingStageCleanupFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await _put_file(src, PathRef.from_posix("/source/bad"), b"new")
    await dst.mkdir(PathRef.from_posix("/target"))
    await _put_file(dst, PathRef.from_posix("/target/original"), b"preserved")

    async def failing_stream(
        _path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        del chunk_size

        async def chunks() -> AsyncIterator[bytes]:
            raise RuntimeError("source failed")
            yield b""  # pragma: no cover

        return chunks()

    src.read_stream = failing_stream  # type: ignore[method-assign]
    copy = asyncio.create_task(
        CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"),
            PathRef.from_posix("/target"),
            on_conflict=ConflictResolution.OVERWRITE,
        )
    )
    await dst.cleanup_started.wait()

    copy.cancel()
    copy.cancel()
    await asyncio.sleep(0)
    assert not copy.done()
    dst.release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await copy

    assert await _read_file(dst, PathRef.from_posix("/target/original")) == b"preserved"
    assert {entry.name for entry in await dst.list(PathRef(()))} == {"target"}


async def test_conflict_skip_no_op() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"src")
    await _put_file(dst, PathRef.from_posix("/a"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(
        PathRef.from_posix("/a"),
        PathRef.from_posix("/a"),
        on_conflict=ConflictResolution.SKIP,
    )
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"dst"


async def test_conflict_rename_appends_suffix() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a.txt"), b"src")
    await _put_file(dst, PathRef.from_posix("/a.txt"), b"dst")
    copier = CrossFsCopy(source=src, destination=dst)
    await copier.copy(
        PathRef.from_posix("/a.txt"),
        PathRef.from_posix("/a.txt"),
        on_conflict=ConflictResolution.RENAME,
    )
    assert await _read_file(dst, PathRef.from_posix("/a.txt")) == b"dst"
    assert await _read_file(dst, PathRef.from_posix("/a (1).txt")) == b"src"


async def test_error_policy_preserves_destination_created_after_conflict_check() -> None:
    class _RacingFS(InMemoryFS):
        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            if not overwrite and path not in self._tree:
                await super().write_stream(path, _agen([b"concurrent"]))
            await super().write_stream(
                path,
                source,
                total_size=total_size,
                progress=progress,  # type: ignore[arg-type]
                overwrite=overwrite,
            )

    src = InMemoryFS()
    dst = _RacingFS()
    await _put_file(src, PathRef.from_posix("/a"), b"source")

    with pytest.raises(ConflictError):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/a"), PathRef.from_posix("/a")
        )

    assert await _read_file(dst, PathRef.from_posix("/a")) == b"concurrent"


async def test_rename_policy_retries_destination_created_after_conflict_check() -> None:
    class _RacingFS(InMemoryFS):
        raced = False

        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            if not overwrite and not self.raced:
                self.raced = True
                await super().write_stream(path, _agen([b"concurrent"]))
            await super().write_stream(
                path,
                source,
                total_size=total_size,
                progress=progress,  # type: ignore[arg-type]
                overwrite=overwrite,
            )

    src = InMemoryFS()
    dst = _RacingFS()
    await _put_file(src, PathRef.from_posix("/a.txt"), b"source")

    assert await CrossFsCopy(source=src, destination=dst).copy(
        PathRef.from_posix("/a.txt"),
        PathRef.from_posix("/a.txt"),
        on_conflict=ConflictResolution.RENAME,
    )
    assert await _read_file(dst, PathRef.from_posix("/a.txt")) == b"concurrent"
    assert await _read_file(dst, PathRef.from_posix("/a (1).txt")) == b"source"


# ---------------------------------------------------------------------------
# Progress + Move
# ---------------------------------------------------------------------------


async def test_progress_monotonic() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/big"), b"x" * 1024)
    copier = CrossFsCopy(source=src, destination=dst)
    seen: list[int] = []

    def cb(p: TransferProgress) -> None:
        seen.append(p.bytes_transferred)

    await copier.copy(PathRef.from_posix("/big"), PathRef.from_posix("/big"), progress=cb)
    assert seen, "progress should fire"
    assert seen == sorted(seen)  # monotonic non-decreasing


async def test_move_deletes_source() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"hi")
    mover = CrossFsMove(source=src, destination=dst)
    await mover.move(PathRef.from_posix("/a"), PathRef.from_posix("/a"))
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"hi"
    with pytest.raises(NotFoundError):
        await src.stat(PathRef.from_posix("/a"))


async def test_cross_fs_passes_opaque_move_revision_to_source_preflight() -> None:
    opaque_revision = 's3:v1:{"version_id":"not-cross-fs-data"}'

    class _OpaqueRevisionFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.preflight_revision: str | None = None
            self.delete_revision: str | None = None

        async def stat(self, path: PathRef) -> FileEntry:
            entry = await super().stat(path)
            return FileEntry(
                name=entry.name,
                kind=entry.kind,
                size=entry.size,
                modified=entry.modified,
                etag=opaque_revision,
            )

        async def preflight_move_revision(self, path: PathRef, revision: str | None) -> None:
            del path
            self.preflight_revision = revision

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            self.delete_revision = expected_etag
            await super().delete(path)

    src = _OpaqueRevisionFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/source"), b"source")

    assert await CrossFsMove(source=src, destination=dst).move(
        PathRef.from_posix("/source"), PathRef.from_posix("/target")
    )

    assert src.preflight_revision == opaque_revision
    assert src.delete_revision == opaque_revision
    assert await _read_file(dst, PathRef.from_posix("/target")) == b"source"


async def test_actual_versioned_s3_move_fails_before_destination_mutation(
    s3_endpoint: str,
) -> None:
    class _MutationTrackingFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.mutations: list[str] = []

        async def write_stream(
            self,
            path: PathRef,
            source: AsyncIterator[bytes],
            *,
            total_size: int | None = None,
            progress: object | None = None,
            overwrite: bool = True,
        ) -> None:
            self.mutations.append("write")
            await super().write_stream(
                path,
                source,
                total_size=total_size,
                progress=progress,  # type: ignore[arg-type]
                overwrite=overwrite,
            )

    source = await _make_s3fs(s3_endpoint, "versioned-source")
    async with _s3_session().client("s3", endpoint_url=s3_endpoint) as s3:
        await s3.put_bucket_versioning(
            Bucket="versioned-source", VersioningConfiguration={"Status": "Enabled"}
        )
    await _put_file(source, PathRef.from_posix("/source"), b"source")
    destination = _MutationTrackingFS()

    with pytest.raises(ProviderError, match="versioned S3 object"):
        await CrossFsMove(source=source, destination=destination).move(
            PathRef.from_posix("/source"), PathRef.from_posix("/target")
        )

    assert destination.mutations == []
    assert await _read_file(source, PathRef.from_posix("/source")) == b"source"


async def test_local_directory_move_deletes_empty_source(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "folder").mkdir()
    (source_root / "folder" / "a.txt").write_bytes(b"payload")
    src = LocalFS(root=source_root)
    dst = InMemoryFS()

    await CrossFsMove(source=src, destination=dst).move(
        PathRef.from_posix("/folder"), PathRef.from_posix("/folder")
    )

    assert not (source_root / "folder").exists()
    assert await _read_file(dst, PathRef.from_posix("/folder/a.txt")) == b"payload"


async def test_directory_move_preserves_child_created_before_final_delete() -> None:
    class _LateChildFS(InMemoryFS):
        async def delete_empty_directory(self, path: PathRef) -> None:
            await self.write_stream(path.join("late.txt"), _agen([b"late"]))
            await super().delete_empty_directory(path)

    src = _LateChildFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/folder"))
    await _put_file(src, PathRef.from_posix("/folder/original.txt"), b"original")

    with pytest.raises(ConflictError, match="not empty"):
        await CrossFsMove(source=src, destination=dst).move(
            PathRef.from_posix("/folder"), PathRef.from_posix("/folder")
        )

    assert await _read_file(src, PathRef.from_posix("/folder/late.txt")) == b"late"
    assert await _read_file(dst, PathRef.from_posix("/folder/original.txt")) == b"original"


async def test_move_does_not_delete_source_replaced_after_copy() -> None:
    class _ReplacingSourceFS(InMemoryFS):
        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            if expected_etag is not None and path.name == "a":
                self._tree[path] = b"new-generation"
                self._touch(path)
            await super().delete(path, expected_etag=expected_etag)

    src = _ReplacingSourceFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"original")

    with pytest.raises(ConflictError, match="source changed"):
        await CrossFsMove(source=src, destination=dst).move(
            PathRef.from_posix("/a"), PathRef.from_posix("/a")
        )

    assert await _read_file(src, PathRef.from_posix("/a")) == b"new-generation"
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"original"


async def test_directory_move_restats_children_before_conditional_delete() -> None:
    class _RestattedSourceFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.observed_revision: str | None = None

        async def list(self, path: PathRef) -> list[FileEntry]:
            entries = await super().list(path)
            if path.name == "folder":
                return [
                    FileEntry(
                        name=entry.name,
                        kind=entry.kind,
                        size=entry.size,
                        modified=entry.modified,
                        etag="listing-revision",
                    )
                    for entry in entries
                ]
            return entries

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            if path.name == "a":
                self.observed_revision = expected_etag
            await super().delete(path, expected_etag=expected_etag)

    src = _RestattedSourceFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/folder"))
    await _put_file(src, PathRef.from_posix("/folder/a"), b"payload")
    await dst.mkdir(PathRef.from_posix("/folder"))
    await _put_file(dst, PathRef.from_posix("/folder/old"), b"old")
    strong_revision = (await src.stat(PathRef.from_posix("/folder/a"))).etag

    await CrossFsMove(source=src, destination=dst).move(
        PathRef.from_posix("/folder"),
        PathRef.from_posix("/folder"),
        on_conflict=ConflictResolution.OVERWRITE,
    )

    assert src.observed_revision == strong_revision


async def test_move_does_not_delete_on_conflict() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"hi")
    await _put_file(dst, PathRef.from_posix("/a"), b"existing")
    mover = CrossFsMove(source=src, destination=dst)
    with pytest.raises(ConflictError):
        await mover.move(PathRef.from_posix("/a"), PathRef.from_posix("/a"))
    # Source must still exist because the conflict raised before delete.
    assert (await src.stat(PathRef.from_posix("/a"))).size == 2


async def test_move_skip_keeps_source_file() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await _put_file(src, PathRef.from_posix("/a"), b"source")
    await _put_file(dst, PathRef.from_posix("/a"), b"existing")

    mover = CrossFsMove(source=src, destination=dst)
    moved = await mover.move(
        PathRef.from_posix("/a"),
        PathRef.from_posix("/a"),
        on_conflict=ConflictResolution.SKIP,
    )

    assert moved is False
    assert await _read_file(src, PathRef.from_posix("/a")) == b"source"
    assert await _read_file(dst, PathRef.from_posix("/a")) == b"existing"


async def test_recursive_move_deletes_copied_children_but_keeps_skipped_children() -> None:
    src = InMemoryFS()
    dst = InMemoryFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await dst.mkdir(PathRef.from_posix("/destination"))
    await _put_file(src, PathRef.from_posix("/source/copied"), b"copied")
    await _put_file(src, PathRef.from_posix("/source/skipped"), b"source")
    await _put_file(dst, PathRef.from_posix("/destination/skipped"), b"existing")

    mover = CrossFsMove(source=src, destination=dst)
    await mover.move(
        PathRef.from_posix("/source"),
        PathRef.from_posix("/destination"),
        on_conflict=ConflictResolution.SKIP,
    )

    with pytest.raises(NotFoundError):
        await src.stat(PathRef.from_posix("/source/copied"))
    assert await _read_file(src, PathRef.from_posix("/source/skipped")) == b"source"
    assert await _read_file(dst, PathRef.from_posix("/destination/copied")) == b"copied"
    assert await _read_file(dst, PathRef.from_posix("/destination/skipped")) == b"existing"


async def test_move_rejects_same_storage_path() -> None:
    fs = InMemoryFS()
    await _put_file(fs, PathRef.from_posix("/a"), b"keep")

    with pytest.raises(ConflictError, match="same path"):
        await CrossFsMove(source=fs, destination=fs).move(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.OVERWRITE,
        )

    assert await _read_file(fs, PathRef.from_posix("/a")) == b"keep"


async def test_move_rejects_same_local_path_across_equivalent_providers(tmp_path: Path) -> None:
    path = PathRef.from_posix("/a")
    (tmp_path / "a").write_bytes(b"keep")

    with pytest.raises(ConflictError, match="same path"):
        await CrossFsMove(
            source=LocalFS(root=tmp_path),
            destination=LocalFS(root=tmp_path),
        ).move(path, path, on_conflict=ConflictResolution.OVERWRITE)

    assert (tmp_path / "a").read_bytes() == b"keep"


async def test_copy_rejects_same_storage_path() -> None:
    fs = InMemoryFS()
    await _put_file(fs, PathRef.from_posix("/a"), b"keep")

    with pytest.raises(ConflictError, match="same path"):
        await CrossFsCopy(source=fs, destination=fs).copy(
            PathRef.from_posix("/a"),
            PathRef.from_posix("/a"),
            on_conflict=ConflictResolution.OVERWRITE,
        )

    assert await _read_file(fs, PathRef.from_posix("/a")) == b"keep"


async def test_copy_rejects_same_storage_descendant_directory() -> None:
    fs = InMemoryFS()
    await fs.mkdir(PathRef.from_posix("/source"))
    await _put_file(fs, PathRef.from_posix("/source/a"), b"keep")

    with pytest.raises(ConflictError, match="inside source"):
        await asyncio.wait_for(
            CrossFsCopy(source=fs, destination=fs).copy(
                PathRef.from_posix("/source"),
                PathRef.from_posix("/source/child"),
            ),
            timeout=1,
        )

    assert await _read_file(fs, PathRef.from_posix("/source/a")) == b"keep"


async def test_equivalent_s3_providers_have_same_storage_identity() -> None:
    left = S3FS(
        session=_s3_session(),
        bucket="bucket",
        prefix="prefix",
        endpoint_url="https://s3.example.test",
    )
    right = S3FS(
        session=_s3_session(),
        bucket="bucket",
        prefix="prefix",
        endpoint_url="https://s3.example.test",
    )

    assert left.storage_identity == right.storage_identity


async def test_directory_overwrite_rejects_provider_without_atomic_tree_replace() -> None:
    class _NonAtomicTreeFS(InMemoryFS):
        @staticmethod
        def supports_atomic_publish(kind: EntryKind) -> bool:
            return kind != EntryKind.DIRECTORY

    src = InMemoryFS()
    dst = _NonAtomicTreeFS()
    await src.mkdir(PathRef.from_posix("/source"))
    await dst.mkdir(PathRef.from_posix("/target"))

    with pytest.raises(ProviderError, match="transactional directory publication"):
        await CrossFsCopy(source=src, destination=dst).copy(
            PathRef.from_posix("/source"),
            PathRef.from_posix("/target"),
            on_conflict=ConflictResolution.OVERWRITE,
        )
