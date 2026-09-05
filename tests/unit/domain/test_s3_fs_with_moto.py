"""Unit tests for S3FS, against an in-process moto threaded server.

The shared ``moto_server`` + ``s3_endpoint`` fixtures live in
``tests/unit/domain/conftest.py`` so the three moto-backed suites
(this one, ``test_s3_fs_bucketless_ops.py``, ``test_cross_fs.py``)
share one moto process per pytest run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import aioboto3
import pytest

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    NotFoundError,
    PathRef,
    ProviderError,
    TransferProgress,
)
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit


def _session() -> aioboto3.Session:
    return aioboto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def _fs(endpoint: str, *, bucket: str | None) -> S3FS:
    return S3FS(
        session=_session(),
        bucket=bucket,
        endpoint_url=endpoint,
        force_path_style=True,
    )


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


async def _agen(blobs: list[bytes]) -> AsyncIterator[bytes]:
    for b in blobs:
        yield b


async def _make_bucket(endpoint: str, name: str) -> None:
    async with _session().client("s3", endpoint_url=endpoint) as s3:
        await s3.create_bucket(Bucket=name)


async def _put(endpoint: str, bucket: str, key: str, body: bytes) -> None:
    async with _session().client("s3", endpoint_url=endpoint) as s3:
        await s3.put_object(Bucket=bucket, Key=key, Body=body)


# ---------------------------------------------------------------------------
# Service root: list buckets
# ---------------------------------------------------------------------------


async def test_list_buckets_at_service_root(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "alpha-bucket")
    await _make_bucket(s3_endpoint, "beta-bucket")
    fs = _fs(s3_endpoint, bucket=None)
    entries = await fs.list(PathRef(()))
    names = [e.name for e in entries]
    assert names == ["alpha-bucket", "beta-bucket"]
    assert all(e.kind == EntryKind.DIRECTORY for e in entries)


# ---------------------------------------------------------------------------
# Listing within a bucket
# ---------------------------------------------------------------------------


async def test_list_empty_bucket(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    assert await fs.list(PathRef(())) == []


async def test_list_with_objects_and_common_prefixes(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "a.txt", b"a")
    await _put(s3_endpoint, "mybkt", "d1/inside.txt", b"x")
    await _put(s3_endpoint, "mybkt", "d1/inside2.txt", b"y")
    await _put(s3_endpoint, "mybkt", "d2/inside.txt", b"z")
    fs = _fs(s3_endpoint, bucket="mybkt")
    entries = await fs.list(PathRef(()))
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"a.txt", "d1", "d2"}
    assert by_name["a.txt"].kind == EntryKind.FILE
    assert by_name["a.txt"].size == 1
    assert by_name["d1"].kind == EntryKind.DIRECTORY


async def test_list_subprefix(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "d/x.txt", b"x")
    await _put(s3_endpoint, "mybkt", "d/y.txt", b"y")
    fs = _fs(s3_endpoint, bucket="mybkt")
    entries = await fs.list(PathRef.from_posix("/d"))
    assert sorted(e.name for e in entries) == ["x.txt", "y.txt"]


async def test_delete_empty_directory_removes_only_marker(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "empty/", b"")
    fs = _fs(s3_endpoint, bucket="mybkt")

    await fs.delete_empty_directory(PathRef.from_posix("/empty"))

    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/empty"))


async def test_delete_empty_directory_preserves_late_child(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "folder/", b"")
    await _put(s3_endpoint, "mybkt", "folder/late.txt", b"keep")
    fs = _fs(s3_endpoint, bucket="mybkt")

    with pytest.raises(ConflictError, match="not empty"):
        await fs.delete_empty_directory(PathRef.from_posix("/folder"))

    assert await _drain(await fs.read_stream(PathRef.from_posix("/folder/late.txt"))) == b"keep"


# ---------------------------------------------------------------------------
# stat
# ---------------------------------------------------------------------------


async def test_stat_object(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "k", b"hello")
    fs = _fs(s3_endpoint, bucket="mybkt")
    entry = await fs.stat(PathRef.from_posix("/k"))
    assert entry.size == 5
    assert entry.kind == EntryKind.FILE
    assert entry.etag is not None


async def test_stat_missing_raises(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/missing"))


# ---------------------------------------------------------------------------
# Roundtrip read/write
# ---------------------------------------------------------------------------


async def test_write_then_read_small(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.write_stream(PathRef.from_posix("/k"), _agen([b"hello ", b"world"]))
    out = await _drain(await fs.read_stream(PathRef.from_posix("/k")))
    assert out == b"hello world"


async def test_write_then_read_16mb(s3_endpoint: str) -> None:
    """A 16 MiB round-trip exercises the explicit multipart path."""
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    payload = os.urandom(16 * 1024 * 1024)

    async def src() -> AsyncIterator[bytes]:
        for i in range(0, len(payload), 1 << 20):
            yield payload[i : i + (1 << 20)]

    await fs.write_stream(PathRef.from_posix("/big.bin"), src(), total_size=len(payload))
    out = await _drain(
        await fs.read_stream(PathRef.from_posix("/big.bin"), chunk_size=4 * 1024 * 1024)
    )
    assert out == payload


async def test_progress_callback_invoked(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    seen: list[int] = []

    def cb(p: TransferProgress) -> None:
        seen.append(p.bytes_transferred)

    await fs.write_stream(
        PathRef.from_posix("/k"),
        _agen([b"abcdefgh"]),
        total_size=8,
        progress=cb,
    )
    assert seen, "progress callback should fire at least once"
    assert seen[-1] == 8


# ---------------------------------------------------------------------------
# mkdir / delete / rename
# ---------------------------------------------------------------------------


async def test_mkdir_creates_marker(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.mkdir(PathRef.from_posix("/folder"))
    entries = await fs.list(PathRef(()))
    names = [e.name for e in entries]
    assert "folder" in names


async def test_delete_object(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "k", b"x")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.delete(PathRef.from_posix("/k"))
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/k"))


async def test_delete_prefix_batches(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    for i in range(3):
        await _put(s3_endpoint, "mybkt", f"d/x{i}", b"x")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.delete(PathRef.from_posix("/d"))
    async with _session().client("s3", endpoint_url=s3_endpoint) as s3:
        resp = await s3.list_objects_v2(Bucket="mybkt", Prefix="d/")
        assert resp.get("KeyCount", 0) == 0


async def test_delete_missing_raises(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    with pytest.raises(NotFoundError):
        await fs.delete(PathRef.from_posix("/nope"))


@pytest.mark.parametrize(("bucket", "prefix"), [("mybkt", ""), ("mybkt", "production"), (None, "")])
async def test_delete_rejects_provider_root(bucket: str | None, prefix: str) -> None:
    fs = S3FS(session=_session(), bucket=bucket, prefix=prefix)
    with pytest.raises(ProviderError, match="provider root"):
        await fs.delete(PathRef(()))


async def test_bucketless_delete_rejects_bucket_root_with_configured_prefix() -> None:
    fs = S3FS(session=_session(), bucket=None, prefix="production")

    with pytest.raises(ProviderError, match="cannot delete a bucket"):
        await fs.delete(PathRef(("mybkt",)))


async def test_rename_preserves_content(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "a", b"hello")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.rename(PathRef.from_posix("/a"), PathRef.from_posix("/b"))
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef.from_posix("/a"))
    out = await _drain(await fs.read_stream(PathRef.from_posix("/b")))
    assert out == b"hello"


async def test_rename_directory_is_rejected_explicitly(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "folder/a.txt", b"hello")
    fs = _fs(s3_endpoint, bucket="mybkt")

    with pytest.raises(ProviderError, match="directory rename"):
        await fs.rename(
            PathRef.from_posix("/folder"),
            PathRef.from_posix("/renamed"),
        )


async def test_rename_file_rejects_existing_virtual_directory(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "source", b"source")
    await _put(s3_endpoint, "mybkt", "target/child", b"child")
    fs = _fs(s3_endpoint, bucket="mybkt")

    with pytest.raises(ConflictError):
        await fs.rename(PathRef.from_posix("/source"), PathRef.from_posix("/target"))

    assert await _drain(await fs.read_stream(PathRef.from_posix("/source"))) == b"source"


async def test_delete_refuses_a_name_that_is_both_an_object_and_a_prefix(
    s3_endpoint: str,
) -> None:
    """S3 allows ``logs`` and ``logs/`` to coexist; a delete cannot tell them apart.

    ``list`` surfaces both as separate rows with the same name, and the pane
    renders them as two independently markable entries. ``PathRef`` carries no
    kind, so the head_object probe in ``delete`` always resolved to the OBJECT:
    marking the folder and pressing delete destroyed the same-named file and
    left the folder standing. Wrong-target data loss, so refuse instead.
    """
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "logs", b"twelve bytes")
    await _put(s3_endpoint, "mybkt", "logs/app.log", b"inside the folder")
    fs = _fs(s3_endpoint, bucket="mybkt")

    listed = await fs.list(PathRef())
    assert sorted((entry.kind, entry.name) for entry in listed) == [
        (EntryKind.DIRECTORY, "logs"),
        (EntryKind.FILE, "logs"),
    ], "both rows must still be listed; the ambiguity is real, not hidden"

    with pytest.raises(ProviderError, match="ambiguous S3 name"):
        await fs.delete(PathRef.from_posix("/logs"))

    # Nothing was removed.
    assert await _drain(await fs.read_stream(PathRef.from_posix("/logs"))) == b"twelve bytes"
    assert (
        await _drain(await fs.read_stream(PathRef.from_posix("/logs/app.log")))
        == b"inside the folder"
    )


async def test_recursive_delete_does_not_reach_siblings_sharing_a_name_prefix(
    s3_endpoint: str,
) -> None:
    """Deleting folder ``a/b`` must not touch ``a/bar.txt`` or ``a/backup/``.

    The enumeration uses ``Prefix`` with NO ``Delimiter``, so the trailing slash
    is the only thing separating a folder from its string-prefix siblings.
    Removing the ``not`` from ``prefix = f"{key}/" if not key.endswith("/")``
    left the whole repo suite green while a single folder delete also destroyed
    every sibling key whose name starts with the same characters.
    """
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "a/b/inside.txt", b"delete me")
    await _put(s3_endpoint, "mybkt", "a/b/nested/deep.txt", b"delete me too")
    # Siblings that share "a/b" as a plain string prefix but are NOT inside it.
    await _put(s3_endpoint, "mybkt", "a/bar.txt", b"keep")
    await _put(s3_endpoint, "mybkt", "a/b-old.txt", b"keep")
    await _put(s3_endpoint, "mybkt", "a/backup/x.txt", b"keep")
    fs = _fs(s3_endpoint, bucket="mybkt")

    await fs.delete(PathRef.from_posix("/a/b"))

    survivors = sorted(entry.name for entry in await fs.list(PathRef.from_posix("/a")))
    assert survivors == ["b-old.txt", "backup", "bar.txt"], (
        "recursive delete reached siblings that merely share a name prefix"
    )
    assert await _drain(await fs.read_stream(PathRef.from_posix("/a/bar.txt"))) == b"keep"
    assert await _drain(await fs.read_stream(PathRef.from_posix("/a/backup/x.txt"))) == b"keep"


async def test_delete_still_removes_unambiguous_files_and_directories(
    s3_endpoint: str,
) -> None:
    """The ambiguity guard must not cost the ordinary delete paths."""
    await _make_bucket(s3_endpoint, "mybkt")
    await _put(s3_endpoint, "mybkt", "plain.txt", b"unambiguous")
    await _put(s3_endpoint, "mybkt", "folder/child", b"in folder")
    fs = _fs(s3_endpoint, bucket="mybkt")

    await fs.delete(PathRef.from_posix("/plain.txt"))
    await fs.delete(PathRef.from_posix("/folder"))

    assert list(await fs.list(PathRef())) == []


# ---------------------------------------------------------------------------
# Error code mapping
# ---------------------------------------------------------------------------


async def test_get_missing_raises_not_found(s3_endpoint: str) -> None:
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    with pytest.raises(NotFoundError):
        await _drain(await fs.read_stream(PathRef.from_posix("/nope")))


async def test_listing_a_directory_never_shows_its_own_marker_as_a_row(
    s3_endpoint: str,
) -> None:
    """`mkdir` creates a zero-length `a/` marker; listing `a/` must skip it.

    The `name.endswith("/")`-or-empty suppression was survivable: without it,
    every aws-tui-created S3 folder showed a phantom `FileEntry(name="",
    kind=FILE, size=0)` inside itself, and acting on that nameless row targets
    the directory marker object.
    """
    await _make_bucket(s3_endpoint, "mybkt")
    fs = _fs(s3_endpoint, bucket="mybkt")
    await fs.mkdir(PathRef.from_posix("/made"))
    await fs.write_stream(PathRef.from_posix("/made/real.txt"), _agen([b"content"]))

    entries = await fs.list(PathRef.from_posix("/made"))

    names = [entry.name for entry in entries]
    assert "" not in names, "the directory's own marker rendered as a nameless row"
    assert names == ["real.txt"]
