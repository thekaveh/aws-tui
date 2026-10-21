"""Reviewed file revisions are validated on the opened native descriptor."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import replace

import pytest

from aws_tui.domain.filesystem import EntryKind, PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.recursive_upload import UploadSourceChangedError

pytestmark = pytest.mark.unit
FILE = PathRef(("file",))


@pytest.fixture
def source(tmp_path):
    (tmp_path / "file").write_bytes(b"abcdefgh")
    return LocalFS(root=tmp_path)


async def contents(stream):
    return b"".join([chunk async for chunk in stream])


@pytest.mark.parametrize("payload", [b"abcdefgh", b""])
async def test_checked_stream_reads_exact_reviewed_revision_and_empty_file(
    source, tmp_path, payload
):
    (tmp_path / "file").write_bytes(payload)
    expected = await source.inspect_upload_entry(FILE)
    stream = await source.read_upload_stream(FILE, expected=expected, chunk_size=3)
    assert await contents(stream) == payload


async def test_file_replaced_after_plan_before_lazy_open_is_rejected(source, tmp_path):
    expected = await source.inspect_upload_entry(FILE)
    stream = await source.read_upload_stream(FILE, expected=expected)
    (tmp_path / "new").write_bytes(b"abcdefgh")
    (tmp_path / "new").replace(tmp_path / "file")
    with pytest.raises(UploadSourceChangedError):
        await anext(stream)


@pytest.mark.parametrize("operation", ["rewrite", "grow", "truncate", "replace", "delete"])
async def test_change_after_first_chunk_is_detected_before_more_payload(
    source, tmp_path, operation
):
    expected = await source.inspect_upload_entry(FILE)
    stream = await source.read_upload_stream(FILE, expected=expected, chunk_size=3)
    assert await anext(stream) == b"abc"
    if operation == "rewrite":
        (tmp_path / "file").write_bytes(b"XYZdefgh")
    elif operation == "grow":
        with (tmp_path / "file").open("ab") as handle:
            handle.write(b"more")
    elif operation == "truncate":
        (tmp_path / "file").write_bytes(b"a")
    elif operation == "replace":
        (tmp_path / "replacement").write_bytes(b"abcdefgh")
        (tmp_path / "replacement").replace(tmp_path / "file")
    else:
        (tmp_path / "file").unlink()
    with pytest.raises(UploadSourceChangedError):
        await anext(stream)


async def test_final_eof_validates_even_when_last_payload_was_already_yielded(source, tmp_path):
    expected = await source.inspect_upload_entry(FILE)
    stream = await source.read_upload_stream(FILE, expected=expected)
    assert await anext(stream) == b"abcdefgh"
    (tmp_path / "file").write_bytes(b"changed!")
    with pytest.raises(UploadSourceChangedError):
        await anext(stream)


async def test_empty_file_replaced_before_first_eof_cannot_publish(source, tmp_path):
    (tmp_path / "file").write_bytes(b"")
    expected = await source.inspect_upload_entry(FILE)
    stream = await source.read_upload_stream(FILE, expected=expected)
    (tmp_path / "file").write_bytes(b"new")
    with pytest.raises(UploadSourceChangedError):
        await anext(stream)


async def test_reviewed_stream_refuses_symlink_replacement(source, tmp_path):
    expected = await source.inspect_upload_entry(FILE)
    (tmp_path / "other").write_bytes(b"abcdefgh")
    (tmp_path / "file").unlink()
    (tmp_path / "file").symlink_to(tmp_path / "other")
    stream = await source.read_upload_stream(FILE, expected=expected)
    with pytest.raises(UploadSourceChangedError):
        await contents(stream)


async def test_missing_file_before_lazy_open_is_named_source_change(source, tmp_path):
    expected = await source.inspect_upload_entry(FILE)
    stream = await source.read_upload_stream(FILE, expected=expected)
    (tmp_path / "file").unlink()
    with pytest.raises(UploadSourceChangedError):
        await contents(stream)


@pytest.mark.parametrize("change", [{"etag": None}, {"size": None}, {"kind": EntryKind.DIRECTORY}])
async def test_invalid_reviewed_snapshot_refused(source, change):
    expected = replace(await source.inspect_upload_entry(FILE), **change)
    with pytest.raises(UploadSourceChangedError):
        await source.read_upload_stream(FILE, expected=expected)


async def test_read_cancellation_is_not_flattened_to_a_source_error(source, monkeypatch):
    expected = await source.inspect_upload_entry(FILE)
    original = source.inspect_upload_entry
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(path):
        entered.set()
        await release.wait()
        return await original(path)

    monkeypatch.setattr(source, "inspect_upload_entry", held)
    stream = await source.read_upload_stream(FILE, expected=expected)
    task = asyncio.create_task(anext(stream))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stream.aclose()


async def test_repeated_cancellation_drains_descriptor_validation_before_close(source, monkeypatch):
    import aws_tui.domain.local_fs as module

    expected = await source.inspect_upload_entry(FILE)
    original = module._open_file_revision
    entered, release = threading.Event(), threading.Event()
    descriptors = []

    def held(fd, filename):
        descriptors.append(fd)
        entered.set()
        assert release.wait(3), "test did not release metadata worker"
        return original(fd, filename)

    monkeypatch.setattr(module, "_open_file_revision", held)
    stream = await source.read_upload_stream(FILE, expected=expected)
    task = asyncio.create_task(anext(stream))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), "descriptor closed while metadata worker still owns it"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptors[0])
