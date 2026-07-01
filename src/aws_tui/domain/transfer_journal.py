"""Crash-resume journal for long-running transfers.

Each transfer owns one append-only JSONL file at
``~/.cache/aws-tui/transfers/<id>.jsonl``. The journal records:

- a ``begin`` line with source/destination URIs, total size, and an
  optional S3 multipart ``upload_id`` for future explicit-MPU flows,
- optional ``part`` lines when a transfer implementation supplies
  completed part metadata,
- a terminal ``finished`` or ``aborted`` marker.

On startup, :meth:`TransferJournal.find_unfinished` scans the directory
and replays each file, returning the in-flight entries so future
startup resume machinery can pick them up.

The journal is intentionally sync-only — file writes are cheap and
fsync semantics are clearer without async indirection. Domain layer
operations can call it from anywhere.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _default_journal_dir() -> Path:
    # Pure-domain fallback: cross-platform cache resolution belongs to the
    # ``infra`` layer (``aws_tui.infra.paths.cache_home``). The composition
    # root passes ``base_dir`` explicitly so this default is only hit in
    # tests / direct-construction scenarios. Keeping ``Path.home()`` here
    # preserves the layer boundary (domain → infra is technically allowed
    # but cleaner to avoid for a fallback constant like this).
    return Path.home() / ".cache" / "aws-tui" / "transfers"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            path.chmod(0o700)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class TransferJournalEntry:
    """One transfer's replayed state, suitable for resume decisions."""

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
    """Append-only JSONL journal for resumable transfers."""

    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._dir = base_dir if base_dir is not None else _default_journal_dir()
        # 0o700: transfer journals embed S3 source/destination URIs
        # and multipart upload IDs that shouldn't be readable by other
        # local users on shared systems. Match ConfigStore.save's
        # defense-in-depth.
        _ensure_private_dir(self._dir)

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
    ) -> str:
        """Allocate a transfer id, write the ``begin`` line, return the id."""
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
        self._append(transfer_id, {"kind": "finished", "ts": _now_iso()})

    def mark_aborted(self, transfer_id: str) -> None:
        self._append(transfer_id, {"kind": "aborted", "ts": _now_iso()})

    def purge(self, transfer_id: str) -> None:
        """Remove the journal file for a transfer. Safe to call on missing."""
        target = self._path_for(transfer_id)
        target.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def find_unfinished(self) -> list[TransferJournalEntry]:
        """Return every journal whose terminal state is neither finished nor aborted."""
        out: list[TransferJournalEntry] = []
        for path in sorted(self._dir.glob("*.jsonl")):
            try:
                entry = self._replay(path)
            except (_JournalReplayError, json.JSONDecodeError, KeyError, ValueError):
                # Corrupt or malformed journal — skip and let the caller
                # decide whether to purge it manually.
                continue
            if entry is None or entry.finished or entry.aborted:
                continue
            out.append(entry)
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path_for(self, transfer_id: str) -> Path:
        return self._dir / f"{transfer_id}.jsonl"

    def _append(self, transfer_id: str, record: dict[str, Any]) -> None:
        path = self._path_for(transfer_id)
        line = json.dumps(record, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            # The module docstring promises "fsync semantics are
            # clearer without async indirection" — deliver them. A
            # natural file-close flushes stdio buffers but does NOT
            # force the FS journal/metadata to disk. On power loss
            # between an ``mark_completed`` write and the OS's
            # background flush (~30s), the journal would lose the
            # terminal marker and the resume-modal would replay the
            # whole transfer on next launch. fsync is the durability
            # primitive that closes that window. The cost is one
            # syscall per append; negligible against the network I/O
            # the surrounding multipart upload pays per part.
            fh.flush()
            os.fsync(fh.fileno())

    def _replay(self, path: Path) -> TransferJournalEntry | None:
        lines = _iter_jsonl(path)
        try:
            begin = next(lines)
        except StopIteration:
            return None
        if begin.get("kind") != "begin":
            raise _JournalReplayError(f"{path}: first line is not 'begin'")

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
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            record: dict[str, Any] = json.loads(raw)
            yield record


def _optional_str(v: object) -> str | None:
    return None if v is None else str(v)


def _optional_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, str)):
        return int(v)
    raise ValueError(f"cannot coerce to int: {v!r}")


class _JournalReplayError(Exception):
    """Raised when a journal file is malformed; caller skips it."""


__all__ = ["TransferJournal", "TransferJournalEntry"]
