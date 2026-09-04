"""Demo providers preserve the important live-provider boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.demo.in_memory_glue import _page
from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, LogFileKind
from aws_tui.domain.filesystem import ConflictError, PathRef, ValidationError


async def _bytes(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


@pytest.mark.asyncio
async def test_demo_emr_log_stream_respects_max_bytes() -> None:
    emr = InMemoryEmr()
    first = "ERROR first"
    log_file = emr.add_log_file(
        application_id="app-1",
        job_run_id="run-1",
        kind=LogFileKind.DRIVER_STDERR,
        lines=(first, "ERROR second"),
    )

    chunks = [
        chunk
        async for chunk in emr.stream(
            log_file=log_file,
            bucket="demo",
            max_bytes=len(first.encode()) + 1,
            filter_=DEFAULT_LOG_FILTER,
        )
    ]

    assert chunks[0].lines == (first,)
    assert chunks[0].lines_scanned == 1
    assert chunks[0].truncated


@pytest.mark.asyncio
async def test_demo_publish_rejects_file_parent() -> None:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("parent",)), _bytes(b"file"))
    await fs.write_stream(PathRef(("stage",)), _bytes(b"payload"))
    revision = (await fs.stat(PathRef(("stage",)))).etag

    with pytest.raises(ConflictError, match="parent is not a directory"):
        await fs.atomic_publish_no_replace(
            PathRef(("stage",)),
            PathRef(("parent", "child")),
            expected_source_revision=revision,
        )


@pytest.mark.parametrize("token", ["-1", "1.5", "opaque", "+1", " 1"])
def test_demo_glue_page_rejects_invalid_tokens(token: str) -> None:
    with pytest.raises(ValidationError, match="invalid Glue page token"):
        _page([1, 2], token, 1)


def test_demo_glue_page_rejects_nonpositive_page_size() -> None:
    with pytest.raises(ValidationError, match="page size must be positive"):
        _page([1, 2], None, 0)
