"""Shipped local/demo preview identity and descriptor ownership."""

import asyncio
import os
import threading

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain import local_fs
from aws_tui.domain.filesystem import (
    BoundedPreviewProvider,
    PathRef,
    PreviewSourceChangedError,
    UnsupportedSourceError,
)
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.preview_limits import PreviewBudget

pytestmark = pytest.mark.unit


async def payload():
    yield b"a,b\n1,2\n"


@pytest.fixture(params=["demo", "local"])
async def provider(request, tmp_path):
    path = PathRef(("sample.csv",))
    fs = InMemoryFS() if request.param == "demo" else LocalFS(root=tmp_path)
    await fs.write_stream(path, payload())
    return fs, path


async def test_range_and_zero_read_accounting(provider):
    fs, path = provider
    assert isinstance(fs, BoundedPreviewProvider)
    budget = PreviewBudget.start()
    session = await fs.open_preview(path, budget=budget)
    assert session.snapshot.size == 8
    assert await session.read_range(0, 3) == b"a,b"
    assert await session.read_range(8, 0) == b""
    assert budget.requests == 2
    assert budget.bytes_requested == 3
    await session.validate()
    assert budget.requests == 3
    await session.aclose()
    await session.aclose()
    with pytest.raises(ValueError, match="closed"):
        await session.read_range(0, 1)


@pytest.mark.parametrize(("offset", "length"), [(-1, 1), (0, -1), (9, 0), (7, 2)])
async def test_invalid_spans_do_not_charge(provider, offset, length):
    fs, path = provider
    budget = PreviewBudget.start()
    session = await fs.open_preview(path, budget=budget)
    try:
        with pytest.raises(ValueError, match="range"):
            await session.read_range(offset, length)
        assert budget.requests == 1
    finally:
        await session.aclose()


async def test_demo_range_detects_revision_change():
    fs = InMemoryFS()
    path = PathRef(("sample.csv",))
    await fs.write_stream(path, payload())
    session = await fs.open_preview(path, budget=PreviewBudget.start())
    assert await session.read_range(0, 3) == b"a,b"
    await fs.write_stream(path, payload())
    with pytest.raises(PreviewSourceChangedError):
        await session.validate()
    with pytest.raises(PreviewSourceChangedError):
        await session.read_range(0, 3)
    await session.aclose()
    await session.aclose()


@pytest.mark.parametrize("mutation", ["replace", "inplace", "delete"])
async def test_local_final_identity_change(tmp_path, mutation):
    host = tmp_path / "sample.csv"
    host.write_bytes(b"a,b\n1,2\n")
    fs = LocalFS(root=tmp_path)
    session = await fs.open_preview(PathRef((host.name,)), budget=PreviewBudget.start())
    if mutation == "replace":
        other = tmp_path / "replacement"
        other.write_bytes(host.read_bytes())
        other.replace(host)
    elif mutation == "inplace":
        original = host.stat()
        host.write_bytes(b"c,d\n3,4\n")
        # Make the revision change observable on filesystems with coarse clocks.
        os.utime(host, ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000))
    else:
        host.unlink()
    try:
        with pytest.raises(PreviewSourceChangedError):
            await session.validate()
        if mutation == "inplace":
            with pytest.raises(PreviewSourceChangedError):
                await session.read_range(0, 1)
    finally:
        await session.aclose()


async def test_local_cancel_open_closes_descriptor(tmp_path, monkeypatch):
    (tmp_path / "sample").write_bytes(b"hello")
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    opened = []
    opener_name = "_windows_open" if local_fs._WINDOWS else "_rooted_open"
    original = getattr(local_fs, opener_name)

    def slow_open(*args):
        fd = original(*args)
        opened.append(fd)
        entered.set()
        release.wait(2)
        done.set()
        return fd

    monkeypatch.setattr(local_fs, opener_name, slow_open)
    task = asyncio.create_task(
        LocalFS(root=tmp_path).open_preview(PathRef(("sample",)), budget=PreviewBudget.start())
    )
    try:
        await asyncio.to_thread(entered.wait, 2)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        returned_fd = None
        if result and not isinstance(result[0], BaseException):
            # A missed interception returns a live session before the assertion
            # above fails; close it so the failure itself cannot leak its fd.
            returned_fd = result[0]._fd
            await result[0].aclose()
        if returned_fd is not None:
            with pytest.raises(OSError, match="Bad file descriptor"):
                os.fstat(returned_fd)
        if entered.is_set():
            assert await asyncio.to_thread(done.wait, 2)
            # The claim closes on the worker's return; join the worker hand-off.
            for _ in range(100):
                try:
                    os.fstat(opened[0])
                except OSError:
                    break
                await asyncio.sleep(0.001)
            with pytest.raises(OSError, match="Bad file descriptor"):
                os.fstat(opened[0])


async def test_local_cancel_read_retains_fd_until_worker_drained(tmp_path, monkeypatch):
    (tmp_path / "sample").write_bytes(b"hello")
    session = await LocalFS(root=tmp_path).open_preview(
        PathRef(("sample",)), budget=PreviewBudget.start()
    )
    entered, release = threading.Event(), threading.Event()
    observed = []
    original = os.read

    def slow_read(fd, length):
        observed.append(fd)
        entered.set()
        release.wait(2)
        return original(fd, length)

    monkeypatch.setattr(os, "read", slow_read)
    task = asyncio.create_task(session.read_range(0, 3))
    await asyncio.to_thread(entered.wait, 2)
    assert entered.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closing = asyncio.create_task(session.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    assert os.fstat(observed[0]).st_size == 5
    release.set()
    await closing
    await session.aclose()
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(observed[0])


async def test_local_preview_never_invokes_mutations(tmp_path, monkeypatch):
    (tmp_path / "sample").write_bytes(b"hello")
    fs = LocalFS(root=tmp_path)

    async def forbidden(*args, **kwargs):
        pytest.fail("preview called a mutation")

    for name in ("write_stream", "delete", "rename", "mkdir"):
        monkeypatch.setattr(fs, name, forbidden)
    session = await fs.open_preview(PathRef(("sample",)), budget=PreviewBudget.start())
    assert await session.read_range(0, 5) == b"hello"
    await session.validate()
    await session.aclose()


async def test_local_cancel_during_snapshot_keeps_worker_descriptor(tmp_path, monkeypatch):
    (tmp_path / "sample").write_bytes(b"hello")
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    observed = []
    original = os.fstat

    def slow_fstat(fd):
        observed.append(fd)
        entered.set()
        release.wait(2)
        try:
            return original(fd)
        finally:
            returned.set()

    monkeypatch.setattr(os, "fstat", slow_fstat)
    task = asyncio.create_task(
        LocalFS(root=tmp_path).open_preview(PathRef(("sample",)), budget=PreviewBudget.start())
    )
    await asyncio.to_thread(entered.wait, 2)
    assert entered.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        assert original(observed[0]).st_size == 5
    finally:
        release.set()
        await asyncio.to_thread(returned.wait, 2)
    for _ in range(100):
        try:
            original(observed[0])
        except OSError:
            break
        await asyncio.sleep(0.001)
    with pytest.raises(OSError, match="Bad file descriptor"):
        original(observed[0])


async def test_local_close_refuses_queued_read_after_cancel(tmp_path, monkeypatch):
    (tmp_path / "sample").write_bytes(b"hello")
    session = await LocalFS(root=tmp_path).open_preview(
        PathRef(("sample",)), budget=PreviewBudget.start()
    )
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = os.read

    def slow_read(fd, length):
        calls.append(length)
        entered.set()
        release.wait(2)
        return original(fd, length)

    monkeypatch.setattr(os, "read", slow_read)
    first = asyncio.create_task(session.read_range(0, 2))
    await asyncio.to_thread(entered.wait, 2)
    assert entered.is_set()
    second = asyncio.create_task(session.read_range(2, 2))
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    closing = asyncio.create_task(session.aclose())
    await asyncio.sleep(0)
    release.set()
    await first
    await closing
    assert calls == [2]


@pytest.mark.parametrize("kind", ["directory", *(["fifo"] if hasattr(os, "mkfifo") else [])])
async def test_local_rejects_nonregular_without_blocking(tmp_path, kind):
    host = tmp_path / "special"
    if kind == "directory":
        host.mkdir()
    else:
        os.mkfifo(host)
    task = asyncio.create_task(
        LocalFS(root=tmp_path).open_preview(PathRef(("special",)), budget=PreviewBudget.start())
    )
    blocked = False
    try:
        with pytest.raises(UnsupportedSourceError, match="not a regular file"):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
    except TimeoutError:
        blocked = True
        # Drain the defective opener so the RED test itself owns no orphan.
        writer = os.open(host, os.O_WRONLY | os.O_NONBLOCK)
        try:
            with pytest.raises(UnsupportedSourceError, match="not a regular file"):
                await task
        finally:
            os.close(writer)
    assert not blocked, "preview blocked opening a nonregular source"
