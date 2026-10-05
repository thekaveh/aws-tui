"""Crash-diagnostics journal for long-running transfers.

Each transfer owns one append-only JSONL file at
``<cache-dir>/transfers/<id>.jsonl``. The journal records:

- a ``begin`` line with source/destination URIs, total size, and an
  optional S3 multipart ``upload_id`` for future explicit-MPU flows,
- optional ``part`` lines when a transfer implementation supplies
  completed part metadata,
- an optional terminal ``finished`` or ``aborted`` marker for replay
  compatibility; current terminal operations purge the file immediately.

:meth:`TransferJournal.find_unfinished` can replay files left by an interrupted
process. Startup does not currently surface or resume them.

The journal is intentionally sync-only. Call disk operations in owned workers;
await begin/attempt durability before provider mutation, and terminal durability
before settling a batch. New safe descriptors support explicit recovery without
exposing legacy diagnostics or multipart identifiers in history summaries.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from aws_tui.domain.transfer_history import (
    FailureReason,
    HistoryStatus,
    TransferHistoryDescriptor,
    TransferHistoryRecord,
    TransferHistoryStore,
    descriptor_from_json,
    descriptor_to_json,
    ensure_private_directory,
    fsync_directory,
    open_regular_metadata,
    parse_metadata_json,
    read_metadata,
)


def _default_journal_dir() -> Path:
    # Pure-domain fallback: cross-platform cache resolution belongs to the
    # ``infra`` layer (``aws_tui.infra.paths.cache_home``). The composition
    # root passes ``base_dir`` explicitly so this default is only hit in
    # tests / direct-construction scenarios. Keeping ``Path.home()`` here
    # preserves the layer boundary (domain → infra is technically allowed
    # but cleaner to avoid for a fallback constant like this).
    return Path.home() / ".cache" / "aws-tui" / "transfers"


def _ensure_private_dir(path: Path) -> None:
    ensure_private_directory(path)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class TransferJournalEntry:
    """One transfer's replayed state, suitable for diagnostics."""

    transfer_id: str
    source_uri: str
    destination_uri: str
    upload_id: str | None
    bytes_total: int | None
    started_at: datetime
    last_progress: datetime
    completed_parts: tuple[int, ...] = field(default_factory=tuple)
    completed_etags: tuple[str, ...] = field(default_factory=tuple)
    finished: bool = False
    aborted: bool = False


class TransferJournal:
    """Append-only JSONL journal for interrupted-transfer diagnostics."""

    def __init__(
        self, *, base_dir: Path | None = None, history_store: TransferHistoryStore | None = None
    ) -> None:
        self._dir = base_dir if base_dir is not None else _default_journal_dir()
        # 0o700: transfer journals embed S3 source/destination URIs
        # and multipart upload IDs that shouldn't be readable by other
        # local users on shared systems. Match ConfigStore.save's
        # defense-in-depth.
        _ensure_private_dir(self._dir)
        self.history_store = (
            history_store
            if history_store is not None
            else TransferHistoryStore(self._dir / "history")
        )
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def begin(
        self,
        *,
        source_uri: str,
        destination_uri: str,
        bytes_total: int | None = None,
        upload_id: str | None = None,
        descriptor: TransferHistoryDescriptor | None = None,
    ) -> str:
        """Allocate a transfer id, write the ``begin`` line, return the id."""
        if descriptor is not None:
            descriptor = descriptor_from_json(descriptor_to_json(descriptor))
            if (
                source_uri != descriptor.source_uri
                or destination_uri != (descriptor.destination_uri or "")
                or bytes_total != descriptor.bytes_total
            ):
                raise ValueError("diagnostic begin fields must match safe descriptor")
        transfer_id = secrets.token_hex(8)  # 16 hex chars
        self._append(
            transfer_id,
            {
                "kind": "begin",
                "transfer_id": transfer_id,
                "source_uri": source_uri,
                "destination_uri": destination_uri,
                "bytes_total": bytes_total,
                "upload_id": upload_id,
                "ts": _now_iso(),
                **(
                    {"descriptor": descriptor_to_json(descriptor)} if descriptor is not None else {}
                ),
            },
        )
        return transfer_id

    def record_part(
        self,
        transfer_id: str,
        *,
        part_index: int,
        etag: str,
        bytes_written: int,
    ) -> None:
        """Append a completed-part marker."""
        self._append(
            transfer_id,
            {
                "kind": "part",
                "part_index": part_index,
                "etag": etag,
                "bytes_written": bytes_written,
                "ts": _now_iso(),
            },
        )

    def mark_finished(self, transfer_id: str) -> None:
        safe = self._safe_state(self._path_for(transfer_id))
        if safe is not None:
            self.mark_terminal(transfer_id, status="completed", bytes_total=safe.bytes_total)
            return
        self._append(transfer_id, {"kind": "finished", "ts": _now_iso()})
        self.purge(transfer_id)

    def mark_aborted(self, transfer_id: str) -> None:
        safe = self._safe_state(self._path_for(transfer_id))
        if safe is not None:
            self.mark_terminal(transfer_id, status="cancelled", bytes_total=safe.bytes_total)
            return
        self._append(transfer_id, {"kind": "aborted", "ts": _now_iso()})
        self.purge(transfer_id)

    def purge(self, transfer_id: str) -> None:
        """Remove the journal file for a transfer. Safe to call on missing."""
        target = self._path_for(transfer_id)
        if target.is_symlink():
            return
        target.unlink(missing_ok=True)

    def mark_attempted(self, transfer_id: str) -> None:
        """Durably write before any provider mutation; callers await worker completion."""
        with self._lock:
            if self._safe_state(self._path_for(transfer_id)) is None:
                raise ValueError("attempt requires a validated current descriptor")
            self._append(transfer_id, {"kind": "attempted", "ts": _now_iso()})

    def mark_terminal(
        self,
        transfer_id: str,
        *,
        status: HistoryStatus,
        bytes_done: int = 0,
        bytes_total: int | None = None,
        failure_reason: FailureReason | None = None,
    ) -> None:
        """Persist terminal summary before diagnostic append/purge.

        None means an unknown observed total; zero is preserved. A confirmed
        failed/cancelled status does not establish whether bytes were published.
        """
        with self._lock:
            safe = self._safe_state(self._path_for(transfer_id))
            if safe is None or status == "outcome_unknown":
                raise ValueError(
                    "terminal recording requires a current descriptor and terminal status"
                )
            now = datetime.now(UTC)
            publication = safe.publication
            if status == "completed":
                publication = "confirmed_terminal"
            if failure_reason is None:
                if status == "failed":
                    failure_reason = "provider_error"
                elif status == "cancelled":
                    failure_reason = "cancelled"
            record = TransferHistoryRecord(
                id=safe.id,
                operation=safe.operation,
                source_connection=safe.source_connection,
                destination_connection=safe.destination_connection,
                source_uri=safe.source_uri,
                destination_uri=safe.destination_uri,
                started_at=safe.started_at,
                updated_at=now,
                finished_at=now,
                bytes_done=bytes_done,
                bytes_total=bytes_total,
                status=status,
                publication=publication,
                failure_reason=failure_reason,
            )
            self.history_store.save(record)
            self._append(
                transfer_id,
                {
                    "kind": "finished" if status in {"completed", "skipped"} else "aborted",
                    "ts": now.isoformat(),
                },
            )
            self.purge(transfer_id)

    def load_history(self) -> tuple[TransferHistoryRecord, ...]:
        """Merge summaries and validated interruptions; summaries win duplicate ids."""
        with self._lock:
            records = {record.id: record for record in self.history_store.load()}
            for path in self._dir.glob("*.jsonl"):
                if path.stem in records:
                    continue
                record = self._safe_state(path)
                if record is not None:
                    records[record.id] = record
            return tuple(sorted(records.values(), key=lambda r: (r.updated_at, r.id), reverse=True))

    def clear_history(
        self,
        *,
        exclude_ids: frozenset[str] = frozenset(),
        exclude_interrupted: bool = False,
    ) -> None:
        """Clear validated owned metadata only, preserving reserved operations."""
        with self._lock:
            self.history_store.clear(exclude_ids=exclude_ids)
            if not exclude_interrupted:
                for path in self._dir.glob("*.jsonl"):
                    if path.stem in exclude_ids:
                        continue
                    record = self._safe_state(path)
                    if record is not None and self._safe_state(path) == record:
                        self.purge(record.id)
            fsync_directory(self._dir)

    def _safe_state(self, path: Path) -> TransferHistoryRecord | None:
        try:
            self._path_for(path.stem)
            raw = read_metadata(path)
            if not raw.endswith("\n"):
                return None
            lines = [parse_metadata_json(line) for line in raw.splitlines() if line.strip()]
            if not lines or not all(isinstance(line, dict) for line in lines):
                return None
            begin = lines[0]
            if begin.get("kind") != "begin" or begin.get("transfer_id") != path.stem:
                return None
            descriptor = descriptor_from_json(begin.get("descriptor"))
            if (
                begin.get("source_uri") != descriptor.source_uri
                or begin.get("destination_uri") != (descriptor.destination_uri or "")
                or begin.get("bytes_total") != descriptor.bytes_total
            ):
                return None
            started = datetime.fromisoformat(begin["ts"])
            updated = started
            attempted = False
            for line in lines[1:]:
                timestamp = datetime.fromisoformat(line["ts"])
                if timestamp.utcoffset() != started.utcoffset() or timestamp < updated:
                    return None
                updated = timestamp
                if line.get("kind") == "attempted":
                    attempted = True
                elif line.get("kind") in {"finished", "aborted"} or line.get("kind") != "part":
                    return None
            return TransferHistoryRecord(
                id=path.stem,
                operation=descriptor.operation,
                source_connection=descriptor.source_connection,
                destination_connection=descriptor.destination_connection,
                source_uri=descriptor.source_uri,
                destination_uri=descriptor.destination_uri,
                started_at=started,
                updated_at=updated,
                finished_at=None,
                bytes_done=0,
                bytes_total=descriptor.bytes_total,
                status="outcome_unknown",
                publication="possibly_published" if attempted else "never_attempted",
                failure_reason="interrupted",
            )
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return None

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def find_unfinished(self) -> list[TransferJournalEntry]:
        """Return every journal whose terminal state is neither finished nor aborted."""
        out: list[TransferJournalEntry] = []
        for path in sorted(self._dir.glob("*.jsonl")):
            try:
                entry = self._replay(path)
            except (
                _JournalReplayError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
                OSError,
                UnicodeError,
                RecursionError,
            ):
                # Corrupt, malformed or UNREADABLE journal — skip and let the
                # caller decide whether to purge it manually. `_iter_jsonl`
                # opens the file lazily during replay, so a journal removed by
                # a second instance between `glob()` and the read, or one that
                # fails with EACCES/EIO, raised out of this loop and aborted
                # the whole scan — the opposite of the skip-and-continue
                # contract this handler documents.
                continue
            if entry is None or entry.finished or entry.aborted:
                continue
            out.append(entry)
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path_for(self, transfer_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{16}", transfer_id) is None:
            raise ValueError("transfer_id must be exactly 16 lowercase hexadecimal characters")
        return self._dir / f"{transfer_id}.jsonl"

    def _append(self, transfer_id: str, record: dict[str, Any]) -> None:
        path = self._path_for(transfer_id)
        _ensure_private_dir(self._dir)
        if path.is_symlink():
            raise ValueError("refusing symlink journal")
        line = json.dumps(record, separators=(",", ":"))
        # A directory fsync makes the file's ENTRY durable, which only changes
        # when the file is created. Doing it on every append doubled the sync
        # cost of a batch: pre-registering the 1,000-entry cap performed ~2,000
        # fsyncs synchronously on the event loop before the first byte moved
        # (measured: 400 for 200 entries). The per-file fsync in
        # `_write_journal_line` still makes the CONTENT durable on every record.
        created = not path.exists()
        fd = _private_append_opener(str(path), os.O_APPEND | os.O_WRONLY)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            _write_journal_line(fh, line)
        if created:
            _fsync_directory(path.parent)

    def _replay(self, path: Path) -> TransferJournalEntry | None:
        filename_id = path.stem
        self._path_for(filename_id)
        lines = _iter_jsonl(path)
        try:
            begin = next(lines)
        except StopIteration:
            return None
        if begin.get("kind") != "begin":
            raise _JournalReplayError(f"{path}: first line is not 'begin'")
        if begin.get("transfer_id") != filename_id:
            raise _JournalReplayError(f"{path}: transfer_id does not match filename")

        completed_parts: list[int] = []
        completed_etags: list[str] = []
        last_progress = _parse_iso(str(begin["ts"]))
        finished = False
        aborted = False

        for record in lines:
            kind = record.get("kind")
            ts_raw = record.get("ts")
            if isinstance(ts_raw, str):
                last_progress = _parse_iso(ts_raw)
            if kind == "part":
                completed_parts.append(int(record["part_index"]))
                completed_etags.append(str(record["etag"]))
            elif kind == "finished":
                finished = True
            elif kind == "aborted":
                aborted = True

        return TransferJournalEntry(
            transfer_id=str(begin["transfer_id"]),
            source_uri=str(begin["source_uri"]),
            destination_uri=str(begin["destination_uri"]),
            upload_id=_optional_str(begin.get("upload_id")),
            bytes_total=_optional_int(begin.get("bytes_total")),
            started_at=_parse_iso(str(begin["ts"])),
            last_progress=last_progress,
            completed_parts=tuple(completed_parts),
            completed_etags=tuple(completed_etags),
            finished=finished,
            aborted=aborted,
        )


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    # Legacy diagnostics can contain many multipart lines. Preserve streaming
    # replay and its torn-final-line tolerance; new recovery uses bounded reads.
    fd = open_regular_metadata(path)
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                if not raw.endswith("\n"):
                    return
                raise
            if not isinstance(record, dict):
                raise _JournalReplayError(f"{path}: record is not a JSON object")
            yield record


def _optional_str(v: object) -> str | None:
    return None if v is None else str(v)


def _optional_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, int | str):
        return int(v)
    raise ValueError(f"cannot coerce to int: {v!r}")


class _JournalReplayError(Exception):
    """Raised when a journal file is malformed; caller skips it."""


def _private_append_opener(path: str, flags: int) -> int:
    return open_regular_metadata(Path(path), flags=flags | os.O_APPEND | os.O_WRONLY, create=True)


def _write_journal_line(fh: TextIO, line: str) -> None:
    if os.name == "posix":
        os.fchmod(fh.fileno(), 0o600)
    fh.write(line + "\n")
    # A normal close flushes stdio buffers but does not force filesystem
    # journal or metadata updates to disk. Make each diagnostic append durable
    # across power loss; the syscall cost is negligible beside transfer I/O.
    fh.flush()
    os.fsync(fh.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = ["TransferJournal", "TransferJournalEntry"]
