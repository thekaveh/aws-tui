"""Read-only history and explicit endpoint-bound new-copy recovery."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from vmx import Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy
from aws_tui.domain.filesystem import FileEntry, FileSystemProvider, NotFoundError, PathRef
from aws_tui.domain.transfer_history import (
    FailureReason,
    TransferConnectionIdentity,
    TransferHistoryDescriptor,
    TransferHistoryRecord,
)
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm._observable import send_value_free
from aws_tui.vm.file_manager.transfer_runtime import (
    HistoryWriteError,
    TransferRuntime,
    failure_reason,
)
from aws_tui.vm.messages import TransferState


class RecoveryRefused(Exception):
    """A fixed safe message and stable category; contains no provider details."""

    def __init__(self, reason: FailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ResolvedTransferEndpoint:
    provider: FileSystemProvider
    identity: TransferConnectionIdentity


@dataclass(frozen=True, slots=True)
class EndpointInspection:
    record_id: str
    source_uri: str
    destination_uri: str | None
    source: FileEntry | None
    destination: FileEntry | None
    retry_eligible: bool


@dataclass(frozen=True, slots=True)
class RetryPlan:
    record_id: str
    source_connection: TransferConnectionIdentity
    destination_connection: TransferConnectionIdentity
    source_uri: str
    destination_uri: str
    source: FileEntry
    destination: FileEntry | None
    default_conflict: ConflictResolution = ConflictResolution.ERROR


ResolveEndpoint = Callable[[TransferConnectionIdentity], Awaitable[ResolvedTransferEndpoint]]
DecideConflict = Callable[[RetryPlan], Awaitable[ConflictResolution | None]]


def original_path(uri: str) -> PathRef:
    """Parse only the known label prefix. ?, # and % are literal path bytes."""
    for prefix in ("s3://", "local://", "file://"):
        if uri.startswith(prefix):
            uri = uri[len(prefix) :]
            break
    return PathRef.from_posix(uri)


class TransferHistoryVM:
    def __init__(
        self,
        journal: TransferJournal,
        resolve_endpoint: ResolveEndpoint,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        *,
        runtime: TransferRuntime | None = None,
    ) -> None:
        self._journal, self._resolve_endpoint, self._hub = journal, resolve_endpoint, hub
        self._dispatcher = dispatcher
        self.runtime = runtime or TransferRuntime(journal, hub)
        self.records: tuple[TransferHistoryRecord, ...] = ()
        self.load_state: Literal["idle", "loading", "ready", "error"] = "idle"
        self.error_text: str | None = None
        self._eligibility: dict[str, EndpointInspection] = {}
        self._generation = 0
        self._closed = False
        self._operations: dict[asyncio.Task[object], int] = {}
        self._metadata_lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def _owned_operation(self) -> AsyncIterator[None]:
        if self._closed:
            raise RecoveryRefused("cancelled", "Transfer recovery is closed.")
        task = asyncio.current_task()
        assert task is not None
        self._operations[task] = self._operations.get(task, 0) + 1
        try:
            yield
        finally:
            remaining = self._operations[task] - 1
            if remaining:
                self._operations[task] = remaining
            else:
                del self._operations[task]

    async def load(self) -> None:
        if self._closed:
            return
        async with self._owned_operation():
            await self._load()

    async def clear(self) -> None:
        async with self._owned_operation():
            await self._clear()

    async def recheck(self, tid: str) -> EndpointInspection:
        async with self._owned_operation():
            return await self._recheck(tid)

    async def retry(self, tid: str, decide_conflict: DecideConflict) -> str | None:
        async with self._owned_operation():
            return await self._retry(tid, decide_conflict)

    def _notify(self, property_name: str) -> None:
        send_value_free(
            self._hub, PropertyChangedMessage.create(self, "transfer_history", property_name)
        )

    def _visible(
        self, records: tuple[TransferHistoryRecord, ...]
    ) -> tuple[TransferHistoryRecord, ...]:
        return tuple(
            record
            for record in records
            if record.id not in self.runtime.owned_ids
            and not (self.runtime.begins_pending and record.finished_at is None)
        )

    async def _load(self) -> None:
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        self.load_state, self.error_text = "loading", None
        self._notify("load_state")
        owned = frozenset(self.runtime.owned_ids)
        pending = self.runtime.begins_pending
        try:
            async with self._metadata_lock:
                records = await self.runtime.disk.call(self._journal.load_history)
        except Exception:
            if generation == self._generation and not self._closed:
                self.load_state, self.error_text = "error", "Transfer history could not be loaded."
                self._notify("error_text")
                self._notify("load_state")
            return
        if generation == self._generation and not self._closed:
            self.records = tuple(
                record
                for record in self._visible(records)
                if record.id not in owned and not (pending and record.finished_at is None)
            )
            self.load_state = "ready"
            self._notify("records")
            self._notify("load_state")

    async def _clear(self) -> None:
        if self._closed:
            return
        async with self._metadata_lock, self.runtime.mutation_lock:
            try:
                await self.runtime.disk.call(
                    self._journal.clear_history,
                    exclude_ids=frozenset(self.runtime.owned_ids),
                    exclude_interrupted=self.runtime.begins_pending,
                )
            except Exception:
                self.error_text = "Transfer history could not be cleared."
                self._notify("error_text")
                raise RecoveryRefused("persistence_error", self.error_text) from None
            self._eligibility.clear()
        await self.load()

    def _record(self, tid: str) -> TransferHistoryRecord:
        record = next((record for record in self.records if record.id == tid), None)
        if record is None or tid in self.runtime.owned_ids:
            raise RecoveryRefused("invalid_request", "This transfer is unavailable for recovery.")
        return record

    async def _resolve(self, identity: TransferConnectionIdentity) -> ResolvedTransferEndpoint:
        try:
            endpoint = await self._resolve_endpoint(identity)
            if not isinstance(endpoint, ResolvedTransferEndpoint) or endpoint.identity != identity:
                raise RecoveryRefused(
                    "connection_changed", "The original connection is unavailable or changed."
                )
            return endpoint
        except RecoveryRefused:
            raise
        except Exception:
            raise RecoveryRefused(
                "connection_changed", "The original connection is unavailable or changed."
            ) from None

    async def _stat(
        self, provider: FileSystemProvider, uri: str, *, source: bool
    ) -> FileEntry | None:
        try:
            return await provider.stat(original_path(uri))
        except NotFoundError:
            if not source:
                return None
            raise RecoveryRefused("not_found", "The original source no longer exists.") from None
        except Exception as exc:
            reason = failure_reason(exc)
            message = (
                "The original endpoint could not be read because permission was denied."
                if reason == "permission_denied"
                else "The original endpoint could not be read."
            )
            raise RecoveryRefused(reason, message) from None

    async def _inspect(
        self, record: TransferHistoryRecord
    ) -> tuple[EndpointInspection, ResolvedTransferEndpoint, ResolvedTransferEndpoint | None]:
        source = await self._resolve(record.source_connection)
        destination = (
            await self._resolve(record.destination_connection)
            if record.destination_connection is not None
            else None
        )
        source_entry = await self._stat(source.provider, record.source_uri, source=True)
        destination_entry = (
            await self._stat(destination.provider, record.destination_uri, source=False)
            if destination is not None and record.destination_uri is not None
            else None
        )
        eligible = (
            record.operation == "copy"
            and record.status not in {"completed", "skipped"}
            and (record.publication == "never_attempted" or destination_entry is None)
        )
        return (
            EndpointInspection(
                record.id,
                record.source_uri,
                record.destination_uri,
                source_entry,
                destination_entry,
                eligible,
            ),
            source,
            destination,
        )

    async def _recheck(self, tid: str) -> EndpointInspection:
        record = self._record(tid)
        self._eligibility.pop(tid, None)
        inspection, _, _ = await self._inspect(record)
        if not self._closed:
            self._eligibility[tid] = inspection
            self._notify("inspection")
        return inspection

    async def _retry(self, tid: str, decide_conflict: DecideConflict) -> str | None:
        record = self._record(tid)
        if record.operation != "copy" or record.status in {"completed", "skipped"}:
            raise RecoveryRefused("invalid_request", "Only unfinished copies can be retried.")
        evidence = self._eligibility.get(tid)
        if record.publication != "never_attempted" and (
            evidence is None or not evidence.retry_eligible
        ):
            raise RecoveryRefused(
                "invalid_request",
                "Recheck the original endpoints before retrying this uncertain copy.",
            )
        initial, _, _ = await self._inspect(record)
        if evidence is not None and evidence != initial:
            self._eligibility.pop(tid, None)
            raise RecoveryRefused(
                "conflict", "The original endpoints changed. Recheck before retrying."
            )
        if record.publication != "never_attempted" and initial.destination is not None:
            raise RecoveryRefused(
                "conflict", "The uncertain destination still exists. Retry is unavailable."
            )
        assert record.destination_connection is not None
        assert record.destination_uri is not None
        assert initial.source is not None
        plan = RetryPlan(
            tid,
            record.source_connection,
            record.destination_connection,
            record.source_uri,
            record.destination_uri,
            initial.source,
            initial.destination,
        )
        decision = await decide_conflict(plan)
        if decision is None:
            return None
        if not isinstance(decision, ConflictResolution):
            raise RecoveryRefused("invalid_request", "A fresh conflict decision is required.")
        if self._closed:
            raise RecoveryRefused("cancelled", "Transfer recovery is closed.")
        if self._record(tid) != record:
            raise RecoveryRefused(
                "invalid_request", "This transfer changed while awaiting confirmation."
            )
        current, source, destination = await self._inspect(record)
        if current != initial:
            self._eligibility.pop(tid, None)
            raise RecoveryRefused(
                "conflict", "The original endpoints changed. Recheck before retrying."
            )
        assert destination is not None
        descriptor = TransferHistoryDescriptor(
            "copy",
            record.source_connection,
            record.destination_connection,
            record.source_uri,
            record.destination_uri,
            initial.source.size,
        )
        new_id = None
        try:
            new_id = await self.runtime.begin(
                source_uri=record.source_uri,
                destination_uri=record.destination_uri,
                bytes_total=initial.source.size,
                descriptor=descriptor,
            )
            self.runtime.progress(
                new_id,
                TransferState.PENDING,
                0,
                initial.source.size,
                record.source_uri,
                record.destination_uri,
            )
            copier = CrossFsCopy(source=source.provider, destination=destination.provider)
            if await self.runtime.run_one(
                operation=copier.copy,
                src_path=original_path(record.source_uri),
                dst_path=original_path(record.destination_uri),
                on_conflict=decision,
                transfer_id=new_id,
                bytes_total=initial.source.size,
            ):
                await self.runtime.complete(new_id, initial.source.size or 0, initial.source.size)
            self._eligibility.pop(tid, None)
        except Exception as error:
            reason = (
                "persistence_error"
                if isinstance(error, HistoryWriteError)
                else failure_reason(error)
            )
            message = (
                "Transfer history could not be saved."
                if reason == "persistence_error"
                else "The new copy could not be completed."
            )
            raise RecoveryRefused(reason, message) from None
        finally:
            if new_id is not None:
                self.runtime.release(new_id)
        await self.load()
        return new_id

    def dispose(self) -> None:
        """Release observers; the app awaits shutdown before normal disposal."""
        self._closed = True
        self._generation += 1
        for operation in tuple(self._operations):
            operation.cancel()
        self.runtime.dispose()

    async def shutdown(self) -> None:
        self._closed = True
        self._generation += 1
        operations = tuple(self._operations)
        for operation in operations:
            operation.cancel()
        await self.runtime.shutdown()
        for operation in operations:
            while not operation.done():
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(operation)
        self.runtime.dispose()
