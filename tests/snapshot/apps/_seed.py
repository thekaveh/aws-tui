"""Seed helpers for snapshot test apps.

Builds an :class:`InMemoryFS` populated with a small, deterministic file
tree so the rendered SVG never depends on real-world data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from aws_tui.domain.filesystem import PathRef
from tests.unit.domain._in_memory_fs import InMemoryFS

# Hardcoded modified time so snapshots stay stable across runs.
_FIXED_MTIME = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


async def _astream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def seed_left() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.mkdir(PathRef(("data",)))
    await fs.write_stream(PathRef(("alpha.txt",)), _astream(b"alpha bytes"))
    await fs.write_stream(PathRef(("beta.json",)), _astream(b'{"x": 1}'))
    await fs.write_stream(PathRef(("gamma.csv",)), _astream(b"a,b,c\n1,2,3\n"))
    await fs.write_stream(PathRef(("delta.log",)), _astream(b"log line\n" * 8))
    # Pin mtimes so the snapshot stays stable.
    for p in fs._tree:
        fs._mtime[p] = _FIXED_MTIME
    return fs


async def seed_right() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.mkdir(PathRef(("Downloads",)))
    await fs.write_stream(PathRef(("report.pdf",)), _astream(b"\x00" * 4096))
    await fs.write_stream(PathRef(("notes.md",)), _astream(b"# notes"))
    for p in fs._tree:
        fs._mtime[p] = _FIXED_MTIME
    return fs


__all__ = ["seed_left", "seed_right"]
