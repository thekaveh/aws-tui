"""Documented directory-copy contracts over real LocalFS/S3FS loopback HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import aioboto3
import pytest

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy
from aws_tui.domain.filesystem import ConflictError, PathRef, ProviderError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit
_MERGE_POLICIES = [ConflictResolution.ERROR, ConflictResolution.SKIP, ConflictResolution.RENAME]
_SOURCE = PathRef.from_posix("/source")
_TARGET = PathRef.from_posix("/target")
_BUCKET = "directory-merge-contract"


class _ObservedLocalFS(LocalFS):
    bytes_read = 0

    async def read_stream(self, path, *, chunk_size=8 * 1024 * 1024):
        original = await super().read_stream(path, chunk_size=chunk_size)

        async def counted() -> AsyncIterator[bytes]:
            try:
                async for chunk in original:
                    self.bytes_read += len(chunk)
                    yield chunk
            finally:
                await original.aclose()

        return counted()


class _ObservedS3FS(S3FS):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.write_attempts = []
        self.stop_on = None
        self.stop_kind = "error"
        self.stopped = asyncio.Event()

    async def write_stream(self, path, source, **kwargs):
        self.write_attempts.append(path)
        if path == self.stop_on:
            self.stopped.set()
            if self.stop_kind == "error":
                raise ProviderError("controlled later-child failure")
            await asyncio.Future()  # Cancel the real copy task after the first commit.
        await super().write_stream(path, source, **kwargs)


async def setup_copy(endpoint, tmp_path, files, objects):
    assert urlsplit(endpoint).hostname == "127.0.0.1"
    session = aioboto3.Session(
        region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    async with session.client("s3", endpoint_url=endpoint) as client:
        await client.create_bucket(Bucket=_BUCKET)
        for key, payload in objects.items():
            await client.put_object(Bucket=_BUCKET, Key=key, Body=payload)
    (tmp_path / "source").mkdir()
    for name, payload in files.items():
        path = tmp_path / "source" / name
        if payload is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    local = _ObservedLocalFS(root=tmp_path)
    remote = _ObservedS3FS(
        session=session, bucket=_BUCKET, endpoint_url=endpoint, force_path_style=True
    )
    return local, remote, session


async def remote_objects(session, endpoint):
    async with session.client("s3", endpoint_url=endpoint) as client:
        listed = await client.list_objects_v2(Bucket=_BUCKET)
        result = {}
        for item in listed.get("Contents", []):
            response = await client.get_object(Bucket=_BUCKET, Key=item["Key"])
            async with response["Body"] as body:
                result[item["Key"]] = await body.read()
        assert not (await client.list_multipart_uploads(Bucket=_BUCKET)).get("Uploads")
        return result


@pytest.mark.parametrize("policy", _MERGE_POLICIES)
async def test_existing_prefix_and_matching_subdirectories_merge(s3_endpoint, tmp_path, policy):
    before = {"target/": b"", "target/sub/": b"", "target/keep.txt": b"keep"}
    local, remote, session = await setup_copy(
        s3_endpoint, tmp_path, {"one.txt": b"one", "sub/two.txt": b"two"}, before
    )
    assert await CrossFsCopy(source=local, destination=remote).copy(
        _SOURCE, _TARGET, on_conflict=policy
    )
    assert await remote_objects(session, s3_endpoint) == {
        **before,
        "target/one.txt": b"one",
        "target/sub/two.txt": b"two",
    }
    assert local.bytes_read == 6


@pytest.mark.parametrize("policy", _MERGE_POLICIES)
@pytest.mark.parametrize("occupied_by_directory", [False, True])
async def test_merge_conflicts_are_applied_per_file_not_to_matching_directories(
    s3_endpoint, tmp_path, policy, occupied_by_directory
):
    before = (
        {"target/": b"", "target/one.txt/": b"", "target/sub/two.txt/": b""}
        if occupied_by_directory
        else {"target/": b"", "target/one.txt": b"old", "target/sub/two.txt": b"older"}
    )
    local, remote, session = await setup_copy(
        s3_endpoint,
        tmp_path,
        {"one.txt": b"one", "sub/two.txt": b"two", "fresh.txt": b"fresh"},
        before,
    )
    copier = CrossFsCopy(source=local, destination=remote)
    if policy == ConflictResolution.ERROR:
        with pytest.raises(ConflictError):
            await copier.copy(_SOURCE, _TARGET, on_conflict=policy)
        assert local.bytes_read == 0
        assert remote.write_attempts == []
        expected = before
    else:
        copied_all = await copier.copy(_SOURCE, _TARGET, on_conflict=policy)
        assert copied_all is (policy == ConflictResolution.RENAME)
        expected = {**before, "target/fresh.txt": b"fresh"}
        if policy == ConflictResolution.RENAME:
            expected.update({"target/one (1).txt": b"one", "target/sub/two (1).txt": b"two"})
        assert local.bytes_read == (11 if policy == ConflictResolution.RENAME else 5)
    assert await remote_objects(session, s3_endpoint) == expected


@pytest.mark.parametrize("policy", list(ConflictResolution))
async def test_absent_root_refuses_before_payload_or_remote_writes(s3_endpoint, tmp_path, policy):
    before = {"unrelated.txt": b"keep"}
    local, remote, session = await setup_copy(s3_endpoint, tmp_path, {"one.txt": b"one"}, before)
    with pytest.raises(ProviderError, match="transactional directory publication"):
        await CrossFsCopy(source=local, destination=remote).copy(
            _SOURCE, _TARGET, on_conflict=policy
        )
    assert local.bytes_read == 0
    assert remote.write_attempts == []
    assert await remote_objects(session, s3_endpoint) == before


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("policy", _MERGE_POLICIES)
async def test_absent_nested_subtree_refuses_the_entire_merge_before_payload(
    s3_endpoint, tmp_path, policy, empty
):
    before = {"target/": b"", "target/a-existing/": b""}
    files = {"a-existing/first.txt": b"first", "z-absent/": None}
    if not empty:
        files["z-absent/last.txt"] = b"last"
    local, remote, session = await setup_copy(s3_endpoint, tmp_path, files, before)
    # Real LocalFS orders matching a-existing before absent z-absent. Without
    # full preflight, its first child would publish before the later refusal.
    assert (await local.list(_SOURCE))[0].name == "a-existing"
    with pytest.raises(ProviderError, match="transactional directory publication"):
        await CrossFsCopy(source=local, destination=remote).copy(
            _SOURCE, _TARGET, on_conflict=policy
        )
    assert local.bytes_read == 0
    assert remote.write_attempts == []
    assert await remote_objects(session, s3_endpoint) == before


@pytest.mark.parametrize("policy", _MERGE_POLICIES)
async def test_source_subdirectory_blocked_by_destination_file(s3_endpoint, tmp_path, policy):
    before = {"target/": b"", "target/blocked": b"existing file"}
    local, remote, session = await setup_copy(
        s3_endpoint, tmp_path, {"blocked/child.txt": b"child", "fresh.txt": b"fresh"}, before
    )
    copier = CrossFsCopy(source=local, destination=remote)
    if policy == ConflictResolution.SKIP:
        assert not await copier.copy(_SOURCE, _TARGET, on_conflict=policy)
        assert local.bytes_read == 5
        expected = {**before, "target/fresh.txt": b"fresh"}
    else:
        error = ConflictError if policy == ConflictResolution.ERROR else ProviderError
        with pytest.raises(error):
            await copier.copy(_SOURCE, _TARGET, on_conflict=policy)
        assert local.bytes_read == 0
        assert remote.write_attempts == []
        expected = before
    assert await remote_objects(session, s3_endpoint) == expected


async def test_overwrite_is_unsupported_whole_tree_replacement(s3_endpoint, tmp_path):
    before = {"target/": b"", "target/old.txt": b"old"}
    local, remote, session = await setup_copy(s3_endpoint, tmp_path, {"new.txt": b"new"}, before)
    with pytest.raises(ProviderError, match="transactional directory publication"):
        await CrossFsCopy(source=local, destination=remote).copy(
            _SOURCE, _TARGET, on_conflict=ConflictResolution.OVERWRITE
        )
    assert local.bytes_read == 0
    assert remote.write_attempts == []
    assert await remote_objects(session, s3_endpoint) == before


@pytest.mark.parametrize("stop_kind", ["error", "cancel"])
async def test_later_failure_or_cancellation_can_leave_earlier_published_children(
    s3_endpoint, tmp_path, stop_kind
):
    before = {"target/": b""}
    local, remote, session = await setup_copy(
        s3_endpoint, tmp_path, {"a-first.txt": b"first", "z-last.txt": b"last"}, before
    )
    remote.stop_on = _TARGET.join("z-last.txt")
    remote.stop_kind = stop_kind
    operation = asyncio.create_task(
        CrossFsCopy(source=local, destination=remote).copy(_SOURCE, _TARGET)
    )
    await asyncio.wait_for(remote.stopped.wait(), timeout=5)
    if stop_kind == "cancel":
        operation.cancel()
    with pytest.raises(ProviderError if stop_kind == "error" else asyncio.CancelledError):
        await operation
    assert await remote_objects(session, s3_endpoint) == {**before, "target/a-first.txt": b"first"}
    assert local.bytes_read == 5
    assert (tmp_path / "source" / "a-first.txt").read_bytes() == b"first"
    assert (tmp_path / "source" / "z-last.txt").read_bytes() == b"last"
