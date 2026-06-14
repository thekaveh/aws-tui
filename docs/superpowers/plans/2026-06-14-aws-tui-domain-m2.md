# aws-tui M2 (Domain) Implementation Plan

> **For agentic workers:** Compact-plan format. Each task lists files + acceptance criteria + key contract details. Follow TDD within each task using the design spec at `docs/superpowers/specs/2026-06-13-aws-tui-design.md` (§2, §6, §7) for behavioural detail.

**Goal:** Land the Norton-Commander unifier — `domain/filesystem.py` Protocol + `LocalFS` + `S3FS` providers + `CrossFsCopy/Move` streaming + `TransferJournal` for crash-resume. All async, all strict-mypy clean. Unit tests with `InMemoryFS` fake, integration tests against `moto` and `testcontainers-python` MinIO.

**Architecture:** One Protocol, two concrete providers (LocalFS via anyio+aiofiles, S3FS via aioboto3), one cross-fs streamer that reads from any provider and writes to any provider, one journal for resumable transfers. The dual-pane UI (M3+) will consume the same Protocol from both sides.

**Tech Stack:** `anyio` (filesystem ops on threadpool), `aiofiles` (streaming reads/writes), `aioboto3` (S3), `moto` (in-process AWS mock for unit-tier integration tests), `testcontainers-python` with `minio/minio:RELEASE.*` (real S3-compat for the strict tier).

---

## Task 1: `domain/filesystem.py` — Protocol + types

**Files:**
- Create: `src/aws_tui/domain/filesystem.py`
- Create: `tests/unit/domain/__init__.py`
- Create: `tests/unit/domain/test_filesystem_types.py`

**Contract:**

```python
from __future__ import annotations
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import AsyncIterator, Protocol

class EntryKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"

@dataclass(frozen=True, slots=True)
class FileEntry:
    name: str
    kind: EntryKind
    size: int | None        # None for directories
    modified: datetime | None
    etag: str | None = None # for S3 objects; None otherwise

@dataclass(frozen=True, slots=True)
class PathRef:
    """An immutable, provider-agnostic path. Always uses forward slashes."""
    segments: tuple[str, ...]

    @property
    def is_root(self) -> bool: ...
    def join(self, *parts: str) -> PathRef: ...
    def parent(self) -> PathRef: ...
    def as_posix(self) -> str: ...
    @classmethod
    def from_posix(cls, posix: str) -> PathRef: ...

@dataclass(frozen=True, slots=True)
class TransferProgress:
    bytes_transferred: int
    bytes_total: int | None      # None if unknown (e.g., streaming source)
    part_index: int | None = None  # for multipart uploads
    part_count: int | None = None

ProgressCallback = Callable[[TransferProgress], None]   # sync; provider invokes from its own task

class FileSystemProvider(Protocol):
    async def list(self, path: PathRef) -> list[FileEntry]: ...
    async def stat(self, path: PathRef) -> FileEntry: ...
    async def mkdir(self, path: PathRef) -> None: ...                  # may be no-op for S3
    async def delete(self, path: PathRef) -> None: ...                 # recursive for directories
    async def rename(self, src: PathRef, dst: PathRef) -> None: ...    # within same provider
    async def read_stream(self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024) -> AsyncIterator[bytes]: ...
    async def write_stream(self, path: PathRef, source: AsyncIterator[bytes], *, total_size: int | None = None, progress: ProgressCallback | None = None) -> None: ...

class ProviderError(Exception): ...
class NotFoundError(ProviderError): ...
class PermissionDeniedError(ProviderError): ...
class ConflictError(ProviderError): ...           # destination exists, etc.
class ProviderUnreachableError(ProviderError): ...
```

Also define an `InMemoryFS` test fake here (NOT exported as part of the public API — kept in a separate `domain/_in_memory_fs.py` or under `tests/`).

**InMemoryFS** sketch:
```python
class InMemoryFS:
    """Dict-backed FileSystemProvider — used by VM tests in M3+ and integration tests here."""
    def __init__(self) -> None:
        self._tree: dict[PathRef, bytes | None] = {PathRef(()): None}  # root is a dir (None = dir, bytes = file)
        self._meta: dict[PathRef, datetime] = {PathRef(()): datetime.now(UTC)}
    ...
```

Put it at `tests/unit/domain/_in_memory_fs.py` so it's reusable by other test modules.

**Acceptance:**
- `PathRef` round-trips: `from_posix("/a/b/c").as_posix() == "/a/b/c"`; `from_posix("/").is_root == True`; parent walks correctly.
- Protocol can be implemented (write a sanity `class Stub(FileSystemProvider)` in the test).
- `InMemoryFS` passes a generic "provider contract" smoke test (list, stat, mkdir, delete, rename, roundtrip read/write).
- Strict mypy clean.

---

## Task 2: `domain/local_fs.py` — Local filesystem provider

**Files:**
- Create: `src/aws_tui/domain/local_fs.py`
- Create: `tests/unit/domain/test_local_fs.py`

**Contract:**

```python
class LocalFS:
    """FileSystemProvider for the local OS filesystem. Anchored at a root path (or unanchored)."""

    def __init__(self, *, root: Path | None = None) -> None:
        """If `root` is None, paths are absolute. If set, all PathRefs are interpreted relative to root."""
        ...
```

Implementation:
- `list` → `anyio.Path(path).iterdir()` + `stat()` on each, build `FileEntry`. Run via `anyio.to_thread.run_sync` if `iterdir` is blocking.
- `stat` → `anyio.Path(path).stat()` (async).
- `mkdir` → `anyio.Path(path).mkdir(parents=True, exist_ok=True)`.
- `delete` → recursive via `shutil.rmtree` on threadpool (anyio.to_thread.run_sync). For files, `unlink`.
- `rename` → `anyio.Path(src).rename(dst)`. ConflictError if dst exists and is a file.
- `read_stream` → use `aiofiles.open(path, "rb")` and yield chunks of `chunk_size`.
- `write_stream` → consume `source` async iterator, write via `aiofiles.open(path, "wb")`; call `progress` per chunk.

Map OS errors to `ProviderError` subclasses: `FileNotFoundError` → `NotFoundError`; `PermissionError` → `PermissionDeniedError`; `FileExistsError` → `ConflictError`.

**Acceptance:**
- `list(PathRef.from_posix("/"))` on `tmp_path` returns the expected entries.
- Roundtrip: write a 16-MB stream, read it back, byte-equal.
- `delete` recursively removes a directory tree.
- `rename` moves a file across subdirectories.
- Symlinks reported with `kind=EntryKind.SYMLINK`.
- Permission-denied (chmod 0) raises `PermissionDeniedError`. (Use tmp_path + chmod.)
- Missing path raises `NotFoundError`.
- Strict mypy clean.

---

## Task 3: `domain/s3_fs.py` — S3 provider

**Files:**
- Create: `src/aws_tui/domain/s3_fs.py`
- Create: `tests/unit/domain/test_s3_fs_with_moto.py` (uses `moto` in-process — UNIT tier despite the name; it's purely in-process)

**Contract:**

```python
class S3FS:
    """FileSystemProvider over an S3 bucket (or namespace prefix). aioboto3-based."""

    def __init__(self, *, session: aioboto3.Session, bucket: str | None, prefix: str = "", endpoint_url: str | None = None, force_path_style: bool = False) -> None:
        """If bucket is None, the FS is rooted at the SERVICE root and `list(PathRef.root)` returns buckets."""
        ...
```

Semantics:
- **Bucket = None at root**: `list(PathRef(()))` returns one `FileEntry(kind=DIRECTORY)` per accessible bucket (via `s3.list_buckets()`).
- **Bucket = set, prefix = ""**: standard object listing with `list_objects_v2` and `Delimiter="/"`.
- **Directories**: S3 has no real directories. Treat "common prefixes" (returned by `Delimiter="/"`) as directories. `mkdir(path)` is a no-op (or creates an empty marker object with key ending `/` — choose marker for visibility).
- **read_stream**: `s3.get_object(...)["Body"]` → async iter. Use the `aiobotocore` streaming body.
- **write_stream**: use `aioboto3`'s `upload_fileobj` (auto-multipart) with a custom file-like that reads from the async iterator. OR implement manual multipart for proper progress reporting.
- **rename**: server-side copy + delete (CopyObject + DeleteObject).
- **delete**: for an object, `DeleteObject`. For a "prefix" (directory), enumerate all objects under the prefix and `DeleteObjects` in batches of 1000.

Map S3 errors:
- `NoSuchKey` → `NotFoundError`
- `NoSuchBucket` → `NotFoundError`
- `AccessDenied` → `PermissionDeniedError`
- `EndpointConnectionError` → `ProviderUnreachableError`

**Acceptance (against moto):**
- `list_buckets()` returns expected buckets.
- After `put_object`, `list(...)` returns the new entry with size + last-modified.
- Roundtrip: `write_stream` (small 1KB), `read_stream` byte-equal.
- Multipart roundtrip: write 16MB, read 16MB, byte-equal.
- `delete` on a prefix containing 3 keys deletes all 3.
- `rename` (copy+delete) preserves content + ETag.
- `NoSuchKey` → `NotFoundError`.
- Strict mypy clean.

---

## Task 4: `domain/cross_fs.py` — CrossFsCopy/Move

**Files:**
- Create: `src/aws_tui/domain/cross_fs.py`
- Create: `tests/unit/domain/test_cross_fs.py`

**Contract:**

```python
class CrossFsCopy:
    """Stream from one provider to another. Symmetric across any provider pair."""

    def __init__(self, *, source: FileSystemProvider, destination: FileSystemProvider) -> None: ...

    async def copy(self, src: PathRef, dst: PathRef, *, progress: ProgressCallback | None = None, on_conflict: ConflictResolution = ConflictResolution.ERROR) -> None: ...

class CrossFsMove(CrossFsCopy):
    async def move(self, src: PathRef, dst: PathRef, *, progress: ProgressCallback | None = None, on_conflict: ConflictResolution = ConflictResolution.ERROR) -> None: ...

class ConflictResolution(StrEnum):
    ERROR = "error"          # raise ConflictError if dst exists
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"        # appends "(1)", "(2)", etc.
```

Implementation:
- `copy` opens `source.read_stream(src)` and pipes to `destination.write_stream(dst)`.
- Pre-check `destination.stat(dst)`; if it exists, apply `on_conflict`.
- `move` = copy + `source.delete(src)` (only after destination write fully completes).
- For dir-copy, recurse: list source, then per entry, recursive call.

**Acceptance:**
- LocalFS → LocalFS roundtrip (tmp_path).
- InMemoryFS → InMemoryFS roundtrip.
- LocalFS → S3FS via moto (write 16MB, read back via S3FS, byte-equal).
- S3FS → LocalFS via moto.
- S3FS → S3FS via moto (two different buckets).
- Conflict resolution: ERROR raises, OVERWRITE replaces, SKIP no-ops, RENAME appends suffix.
- Progress callback invoked with non-decreasing `bytes_transferred`.
- Strict mypy clean.

---

## Task 5: `domain/transfer_journal.py` — Crash-resume journal

**Files:**
- Create: `src/aws_tui/domain/transfer_journal.py`
- Create: `tests/unit/domain/test_transfer_journal.py`

**Contract:**

```python
@dataclass(frozen=True, slots=True)
class TransferJournalEntry:
    transfer_id: str
    source_uri: str        # e.g. "local:///path/to/file" or "s3://bucket/prefix/key"
    destination_uri: str
    upload_id: str | None  # for S3 multipart
    bytes_total: int | None
    completed_parts: tuple[int, ...] = ()
    completed_etags: tuple[str, ...] = ()
    started_at: datetime
    last_progress: datetime
    finished: bool = False
    aborted: bool = False

class TransferJournal:
    """One JSONL file per transfer at ~/.cache/aws-tui/transfers/<id>.jsonl."""

    def __init__(self, *, base_dir: Path | None = None) -> None: ...
    def begin(self, *, source_uri: str, destination_uri: str, bytes_total: int | None = None, upload_id: str | None = None) -> str: ...    # returns transfer_id
    def record_part(self, transfer_id: str, *, part_index: int, etag: str, bytes_written: int) -> None: ...
    def mark_finished(self, transfer_id: str) -> None: ...
    def mark_aborted(self, transfer_id: str) -> None: ...
    def find_unfinished(self) -> list[TransferJournalEntry]: ...
    def purge(self, transfer_id: str) -> None: ...
```

Each append is one JSON line. `find_unfinished` reads all jsonl files in base_dir, replays each into a `TransferJournalEntry`, filters out `finished` or `aborted`.

**Acceptance:**
- `begin` returns a unique 16-hex-char id.
- After `record_part` × 3, the journal file has 3 part lines + 1 begin line.
- `mark_finished` appends a finished marker; `find_unfinished` excludes it.
- `find_unfinished` correctly reconstructs entries from journal files.
- `purge` deletes the file.
- Strict mypy clean.

---

## Task 6: integration tests with MinIO testcontainer

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py` (session-scoped MinIO container fixture)
- Create: `tests/integration/test_s3_fs_minio.py`
- Create: `tests/integration/test_cross_fs_minio.py`

Add `testcontainers[minio]>=4` to dev deps in `pyproject.toml`.

**Conftest fixture:**
```python
@pytest.fixture(scope="session")
def minio_endpoint() -> Iterator[tuple[str, str, str]]:
    """Returns (endpoint_url, access_key, secret_key)."""
    from testcontainers.minio import MinioContainer
    with MinioContainer() as minio:
        yield (
            f"http://{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}",
            minio.access_key,
            minio.secret_key,
        )
```

Mark integration tests with `@pytest.mark.integration` and add to `[tool.pytest.ini_options].markers` (already declared in M0's pyproject).

Acceptance:
- Tests run with `uv run pytest -m integration -v` (must be opted-in).
- Tests skip cleanly when Docker isn't available (`pytest.skip` if the container fixture errors).
- S3FS roundtrip works against MinIO with `force_path_style=True`.
- Cross-FS LocalFS↔S3FS(MinIO) works.

Wire integration into CI: add a new `integration` job in `.github/workflows/ci.yml` that runs on `ubuntu-22.04` (Docker available) with `uv run pytest -m integration -v`.

---

## Task 7: commit, push, tag v0.3.0

- One commit per task (1-6), then a `chore: bump changelog for v0.3.0` commit.
- `uv run pytest -v` (all tiers) green locally.
- `./scripts/check-layers.sh` clean.
- Push, watch CI green (the unit + new integration matrix should both pass).
- Tag `v0.3.0` ("v0.3.0 — domain (M2)"), push tag, create GH release.
- Update `CHANGELOG.md` `## [Unreleased]` and add `## [0.3.0] - 2026-06-14`.

**Acceptance:** CI green across unit (matrix), integration (new), lint+type, pkg. Tag and release published.
