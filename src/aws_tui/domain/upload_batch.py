"""Owned execution of reviewed objects, with explicit partial outcomes and retry."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, cast

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import (
    ConflictError,
    FileEntry,
    PathRef,
    ProgressCallback,
    TransferProgress,
)
from aws_tui.domain.recursive_upload import (
    UploadObject,
    UploadPlan,
    UploadPlanError,
    UploadSourceChangedError,
    checked_upload_location,
    renamed_upload_path,
    replan_upload_objects,
)
from aws_tui.domain.s3_upload import UploadPublication


class UploadStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELED = "canceled"


class _Source(Protocol):
    async def inspect_upload_entry(self, path: PathRef) -> FileEntry: ...

    async def read_upload_stream(
        self, path: PathRef, *, expected: FileEntry
    ) -> AsyncIterator[bytes]: ...


class _Destination(Protocol):
    def upload_location(self, path: PathRef) -> tuple[str, str]: ...

    async def inspect_upload_destination(self, path: PathRef) -> FileEntry | None: ...

    async def write_upload_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int,
        publication: UploadPublication,
        reviewed: FileEntry | None = None,
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    item: UploadObject
    destination: PathRef
    status: UploadStatus
    started: bool
    bytes_done: int
    published: bool
    uncertain: bool
    error: BaseException | None
    cleanup_error: str | None

    @property
    def retryable(self) -> bool:
        return (
            self.status == UploadStatus.FAILED
            or (not self.started and self.status in {UploadStatus.PENDING, UploadStatus.CANCELED})
        ) and not (self.published or self.uncertain)


@dataclass(frozen=True, slots=True)
class UploadSnapshot:
    outcomes: tuple[UploadOutcome, ...]
    status: UploadStatus
    finished: bool
    bytes_total: int

    @property
    def bytes_done(self) -> int:
        return sum(row.bytes_done for row in self.outcomes)


@dataclass(slots=True)
class _Row:
    item: UploadObject
    destination: PathRef
    status: UploadStatus = UploadStatus.PENDING
    started: bool = False
    bytes_done: int = 0
    publication: UploadPublication | None = None
    error: BaseException | None = None
    close_error: str | None = None

    def snapshot(self) -> UploadOutcome:
        receipt = self.publication or UploadPublication()
        return UploadOutcome(
            self.item,
            self.destination,
            self.status,
            self.started,
            self.bytes_done,
            receipt.published,
            receipt.uncertain,
            self.error,
            receipt.cleanup_error or self.close_error,
        )


class UploadBatch:
    """At most four worker tasks; plan-bound providers never come from live panes."""

    def __init__(self, plan: UploadPlan, *, on_change: Callable[[], None] | None = None):
        self.plan = plan
        self.source = cast(_Source, plan.source)
        self.destination = cast(_Destination, plan.destination)
        if not callable(getattr(self.source, "read_upload_stream", None)) or not callable(
            getattr(self.destination, "write_upload_stream", None)
        ):
            raise UploadPlanError("Providers do not support owned reviewed upload execution")
        self._rows = [_Row(item, item.destination) for item in plan.objects]
        self._on_change = on_change
        self._started = self._finished = self._cancel_requested = False
        self._workers: list[asyncio.Task[None]] = []
        self._next = 0
        self._bytes_done = 0
        self._bytes_total = plan.known_bytes
        self.observer_error: Exception | None = None
        self._reserved = {item.location for item in plan.objects} | {
            self.destination.upload_location(item.requested_destination) for item in plan.objects
        }
        self._reserved.update(
            self.destination.upload_location(path) for path in plan.reserved_paths
        )
        self._rename_lock = asyncio.Lock()

    @property
    def snapshot(self) -> UploadSnapshot:
        outcomes = tuple(row.snapshot() for row in self._rows)
        if not self._started:
            status = UploadStatus.PENDING
        elif not self._finished:
            status = UploadStatus.RUNNING
        elif any(
            row.status == UploadStatus.FAILED or row.uncertain or row.cleanup_error
            for row in outcomes
        ):
            status = UploadStatus.FAILED
        elif any(row.status == UploadStatus.CANCELED for row in outcomes) or (
            not outcomes and self._cancel_requested
        ):
            status = UploadStatus.CANCELED
        else:
            status = UploadStatus.SUCCEEDED
        return UploadSnapshot(outcomes, status, self._finished, self._bytes_total)

    @property
    def bytes_done(self) -> int:
        return self._bytes_done

    @property
    def bytes_total(self) -> int:
        return self._bytes_total

    def _set_bytes(self, row: _Row, value: int) -> None:
        self._bytes_done += value - row.bytes_done
        row.bytes_done = value

    def _emit(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception as exc:
                self.observer_error = exc
                self._on_change = None

    def cancel(self) -> None:
        self._cancel_requested = True
        for task in self._workers:
            if not task.done() and not task.cancelling():
                task.cancel()

    async def run(self) -> UploadSnapshot:
        if self._started:
            raise UploadPlanError("An upload batch can only run once; retry needs a new review")
        self._emit()
        self._started = True
        try:
            if not self._cancel_requested:
                root = await self.source.inspect_upload_entry(self.plan.source_root)
                if root != self.plan.source_root_entry:
                    raise UploadSourceChangedError("Upload source root changed after review")
                for row in self._rows:
                    if self.destination.upload_location(row.destination) != row.item.location:
                        raise UploadPlanError("Upload destination mapping changed after review")
                self._workers = [
                    asyncio.create_task(self._worker()) for _ in range(min(4, len(self._rows)))
                ]
                results = await asyncio.shield(
                    asyncio.gather(*self._workers, return_exceptions=True)
                )
                for result in results:
                    if isinstance(result, Exception):
                        raise result
        except BaseException as exc:
            self.cancel()
            drainage = asyncio.gather(*self._workers, return_exceptions=True)
            while not drainage.done():
                try:
                    await asyncio.shield(drainage)
                except asyncio.CancelledError:
                    continue
            for row in self._rows:
                if row.status in {UploadStatus.PENDING, UploadStatus.RUNNING}:
                    row.status = (
                        UploadStatus.CANCELED
                        if isinstance(exc, asyncio.CancelledError)
                        else UploadStatus.FAILED
                    )
                    row.error = exc
            raise
        finally:
            for row in self._rows:
                if row.status == UploadStatus.PENDING:
                    row.status = UploadStatus.CANCELED
            self._finished = True
            self._emit()
        return self.snapshot

    async def _worker(self) -> None:
        while not self._cancel_requested and self._next < len(self._rows):
            row = self._rows[self._next]
            self._next += 1
            row.status = UploadStatus.RUNNING
            row.started = True
            self._emit()
            try:
                if self._cancel_requested:
                    raise asyncio.CancelledError
                await self._upload(row)
            except asyncio.CancelledError as exc:
                row.status = (
                    UploadStatus.FAILED
                    if row.publication and row.publication.uncertain
                    else UploadStatus.CANCELED
                )
                row.error = exc
            except Exception as exc:
                row.status, row.error = UploadStatus.FAILED, exc
            finally:
                if row.publication and row.publication.published:
                    row.status = UploadStatus.SUCCEEDED
                    self._set_bytes(row, row.item.source_entry.size or 0)
                self._emit()

    async def _upload(self, row: _Row) -> None:
        item, policy = row.item, self.plan.policy
        observed = await self.source.inspect_upload_entry(item.source)
        if observed != item.source_entry:
            raise UploadSourceChangedError(f"Upload source changed: {item.source.as_posix()}")
        current = await self.destination.inspect_upload_destination(row.destination)
        if current is not None:
            if policy == ConflictResolution.SKIP:
                row.status = UploadStatus.SKIPPED
                return
            if policy == ConflictResolution.ERROR:
                raise ConflictError("Upload destination already exists")
            if policy == ConflictResolution.RENAME:
                await self._rename(row)
            elif current != item.destination_entry:
                raise ConflictError("Upload destination changed after review; review a new plan")
        elif item.destination_entry is not None and policy == ConflictResolution.OVERWRITE:
            raise ConflictError("Reviewed overwrite destination disappeared; review a new plan")
        for _ in range(1000):
            if self._cancel_requested:
                raise asyncio.CancelledError
            stream = await self.source.read_upload_stream(item.source, expected=item.source_entry)
            close = getattr(stream, "aclose", None)
            if not callable(close):
                raise UploadPlanError("Upload source stream does not support owned closure")
            receipt = row.publication = UploadPublication()

            def progress(value: TransferProgress) -> None:
                self._set_bytes(
                    row,
                    max(row.bytes_done, min(value.bytes_transferred, item.source_entry.size or 0)),
                )
                self._emit()

            conflict = None
            try:
                await self.destination.write_upload_stream(
                    row.destination,
                    stream,
                    total_size=item.source_entry.size or 0,
                    publication=receipt,
                    reviewed=item.destination_entry
                    if policy == ConflictResolution.OVERWRITE
                    else None,
                    overwrite=policy == ConflictResolution.OVERWRITE,
                    progress=progress,
                )
            except ConflictError as exc:
                if receipt.published or receipt.uncertain:
                    raise
                conflict = exc
            finally:
                closing = asyncio.create_task(close())
                canceled = False
                while not closing.done():
                    try:
                        await asyncio.shield(closing)
                    except asyncio.CancelledError:
                        canceled = True
                    except Exception:
                        break
                try:
                    if closing.cancelled():
                        row.close_error = (
                            "Upload source cleanup was canceled before closure was confirmed"
                        )
                        raise UploadPlanError(row.close_error)
                    closing.result()
                except Exception as exc:
                    row.close_error = str(exc) or "Upload source cleanup failed"
                    raise
                if canceled:
                    raise asyncio.CancelledError
            if conflict is None:
                if not receipt.published:
                    raise UploadPlanError("Upload provider returned without a publication outcome")
                row.status = UploadStatus.SUCCEEDED
                return
            if policy == ConflictResolution.SKIP:
                row.status = UploadStatus.SKIPPED
                return
            if policy != ConflictResolution.RENAME:
                raise conflict
            await self._rename(row)
        raise ConflictError("Upload rename conflict budget exhausted")

    async def _rename(self, row: _Row) -> None:
        async with self._rename_lock:
            for attempt in range(1, 1001):
                if self._cancel_requested:
                    raise asyncio.CancelledError
                candidate = renamed_upload_path(row.item.requested_destination, attempt)
                identity = checked_upload_location(self.plan.destination, candidate)
                if identity in self._reserved:
                    continue
                if await self.destination.inspect_upload_destination(candidate) is None:
                    self._reserved.add(identity)
                    row.destination = candidate
                    return
        raise ConflictError("No available upload rename destination")


async def replan_failed(
    batch: UploadBatch, *, policy: ConflictResolution | None = None
) -> UploadPlan:
    """A fresh review subset; committed/unknown objects are never replayed."""
    snapshot = batch.snapshot
    if not snapshot.finished:
        raise UploadPlanError("Wait for the current upload batch to drain before retry planning")
    selected = tuple(row.item.source for row in snapshot.outcomes if row.retryable)
    reserved = tuple(row.destination for row in snapshot.outcomes if not row.retryable)
    if policy is not None and not isinstance(policy, ConflictResolution):
        raise UploadPlanError("Unknown upload retry conflict policy")
    plan = batch.plan if policy is None else replace(batch.plan, policy=policy)
    return await replan_upload_objects(plan, selected, reserved_paths=reserved)
