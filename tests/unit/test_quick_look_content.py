"""Unit tests for the Quick Look content builder helpers in ``aws_tui.app``."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.app import _build_quick_look_content, _first_bytes
from aws_tui.domain.filesystem import EntryKind, FileEntry


async def _gen(*chunks: bytes) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


class _FakeProvider:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def read_stream(self, path: object, *, chunk_size: int) -> AsyncIterator[bytes]:
        # Matches the real provider idiom: ``async def`` returning an iterator
        # (callers ``await`` it, then ``async for`` over the result).
        return _gen(*self._chunks)


def _file(name: str) -> FileEntry:
    return FileEntry(name=name, kind=EntryKind.FILE, size=10, modified=None)


@pytest.mark.asyncio
async def test_first_bytes_caps_at_limit() -> None:
    out = b"".join([c async for c in _first_bytes(_gen(b"a" * 40000, b"b" * 40000), 64 * 1024)])
    assert len(out) == 64 * 1024


@pytest.mark.asyncio
async def test_first_bytes_passes_through_when_under_limit() -> None:
    out = b"".join([c async for c in _first_bytes(_gen(b"hello"), 64 * 1024)])
    assert out == b"hello"


def test_build_content_sets_title_and_mime() -> None:
    content = _build_quick_look_content(_file("notes.txt"), _FakeProvider(b"x"), path="notes.txt")
    assert content.title == "notes.txt"
    assert content.mime == "text/plain"
    assert content.chunks is not None


def test_build_content_unknown_mime_defaults_octet_stream() -> None:
    content = _build_quick_look_content(_file("blob.zzz"), _FakeProvider(b"x"), path="blob.zzz")
    assert content.mime == "application/octet-stream"


@pytest.mark.asyncio
async def test_build_content_stream_is_capped() -> None:
    provider = _FakeProvider(b"a" * 40000, b"b" * 40000)
    content = _build_quick_look_content(_file("big.bin"), provider, path="big.bin")
    assert content.chunks is not None
    total = b"".join([c async for c in content.chunks])
    assert len(total) == 64 * 1024
