"""Owned disk work and the shared cancellable transfer execution path."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    TransferProgress,
)
from aws_tui.domain.transfer_history import FailureReason, HistoryStatus, TransferHistoryDescriptor
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.vm._observable import send_value_free
from aws_tui.vm.messages import (
    TransferCancelRequestedMessage,
    TransferProgressMessage,
    TransferState,
)

T = TypeVar("T")


class HistoryWriteError(ProviderError):
    """Fixed safe refusal before a transfer mutation."""


class _DiskCancelled(asyncio.CancelledError):
    def __init__(self, value: object) -> None:
        super().__init__()
        self.value = value


class OwnedDiskOperations:
    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[Any]] = set()

    async def call(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        task = asyncio.create_task(asyncio.to_thread(partial(function, *args, **kwargs)))
        self.tasks.add(task)
        cancelled = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            value = task.result()
            if cancelled:
                raise _DiskCancelled(value)
            return value
        finally:
            self.tasks.discard(task)

    async def drain(self) -> None:
        while self.tasks:
            for task in tuple(self.tasks):
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(task)
                if task.done():
                    self.tasks.discard(task)


def failure_reason(error: BaseException) -> FailureReason:
    if isinstance(error, PermissionDeniedError):
        return "permission_denied"
    if isinstance(error, NotFoundError):
        return "not_found"
    if isinstance(error, ConflictError):
        return "conflict"
    return "provider_error"


class TransferRuntime:
    """Shared journal ownership independent of immediately cancelled UI rows."""

    def __init__(self, journal: TransferJournal, hub: MessageHub[Message]) -> None:
        self.journal = journal
        self.hub = hub
        self.disk = OwnedDiskOperations()
        self.mutation_lock = asyncio.Lock()
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.owned_ids: set[str] = set()
        self._descriptors: set[str] = set()
        self._finished_ids: set[str] = set()
        self._running_ids: set[str] = set()
        self._totals: dict[str, tuple[int, int | None]] = {}
        self._observed_ids: set[str] = set()
        self._queued_cleanup: dict[str, asyncio.Task[None]] = {}
        self._owners: dict[str, asyncio.Task[Any]] = {}
        self._begin_tasks: set[asyncio.Task[Any]] = set()
        self.error_text: str | None = None
        self._subscription = hub.messages.subscribe(self._on_message)

    @property
    def begins_pending(self) -> bool:
        return bool(self._begin_tasks)

    def _on_message(self, message: Message) -> None:
        if isinstance(message, TransferCancelRequestedMessage):
            event = self.cancel_events.get(message.transfer_id)
            if event is not None:
                event.set()
                tid = message.transfer_id
                if tid not in self._running_ids and tid not in self._queued_cleanup:
                    total = self._totals.get(tid, (0, None))[1]
                    task = asyncio.create_task(self.finish(tid, "cancelled", 0, total))
                    self._queued_cleanup[tid] = task

    def _feedback(self) -> None:
        self.error_text = "Transfer history could not be saved."
        send_value_free(
            self.hub, PropertyChangedMessage.create(self, "transfer_history", "error_text")
        )

    async def begin(
        self,
        *,
        source_uri: str,
        destination_uri: str,
        bytes_total: int | None,
        descriptor: TransferHistoryDescriptor | None = None,
    ) -> str:
        owner = asyncio.current_task()
        assert owner is not None
        self._begin_tasks.add(owner)
        try:
            async with self.mutation_lock:
                return await self._begin_owned(
                    owner,
                    source_uri=source_uri,
                    destination_uri=destination_uri,
                    bytes_total=bytes_total,
                    descriptor=descriptor,
                )
        finally:
            self._begin_tasks.discard(owner)

    async def _begin_owned(
        self,
        owner: asyncio.Task[Any],
        *,
        source_uri: str,
        destination_uri: str,
        bytes_total: int | None,
        descriptor: TransferHistoryDescriptor | None,
    ) -> str:
        cancelled = False
        try:
            try:
                tid = await self.disk.call(
                    self.journal.begin,
                    source_uri=source_uri,
                    destination_uri=destination_uri,
                    bytes_total=bytes_total,
                    descriptor=descriptor,
                )
            except _DiskCancelled as exc:
                tid = str(exc.value)
                cancelled = True
            except Exception:
                self._feedback()
                raise HistoryWriteError("Transfer history could not be saved.") from None
            self.owned_ids.add(tid)
            self._owners[tid] = owner
            self.cancel_events[tid] = asyncio.Event()
            self._totals[tid] = (0, bytes_total)
            if descriptor is not None:
                self._descriptors.add(tid)
            if cancelled:
                try:
                    await self.finish(tid, "cancelled", 0, bytes_total)
                finally:
                    self.release(tid)
                raise asyncio.CancelledError
            return tid
        finally:
            self._begin_tasks.discard(owner)

    def release(self, tid: str) -> None:
        self.owned_ids.discard(tid)
        self._descriptors.discard(tid)
        self._finished_ids.discard(tid)
        self._running_ids.discard(tid)
        self._totals.pop(tid, None)
        self._observed_ids.discard(tid)
        self._queued_cleanup.pop(tid, None)
        self._owners.pop(tid, None)
        self.cancel_events.pop(tid, None)

    async def finish(
        self,
        tid: str,
        status: HistoryStatus,
        done: int,
        total: int | None,
        reason: FailureReason | None = None,
    ) -> None:
        if tid in self._finished_ids:
            return
        try:
            if tid in self._descriptors:
                await self.disk.call(
                    self.journal.mark_terminal,
                    tid,
                    status=status,
                    bytes_done=done,
                    bytes_total=total,
                    failure_reason=reason,
                )
            elif status == "completed":
                await self.disk.call(self.journal.mark_finished, tid)
            else:
                await self.disk.call(self.journal.mark_aborted, tid)
            self._finished_ids.add(tid)
        except _DiskCancelled:
            self._finished_ids.add(tid)
            raise
        except Exception:
            self._feedback()

    def progress(
        self,
        tid: str,
        state: TransferState,
        done: int,
        total: int | None,
        source: str = "",
        destination: str = "",
    ) -> None:
        self.hub.send(
            TransferProgressMessage(
                transfer_id=tid,
                bytes_transferred=done,
                bytes_total=total,
                state=state,
                source_label=source,
                destination_label=destination,
            )
        )

    async def complete(self, tid: str, done: int, total: int | None) -> None:
        # Provider outcome remains truthful even if saving its history fails.
        observed = self._totals.get(tid)
        if observed is not None and tid in self._observed_ids:
            done, total = observed
        self.progress(tid, TransferState.COMPLETED, done, total)
        await self.finish(tid, "completed", done, total)

    async def run_one(
        self,
        *,
        operation: Any,
        src_path: Any,
        dst_path: Any,
        on_conflict: ConflictResolution,
        transfer_id: str,
        bytes_total: int | None,
    ) -> bool:
        tid = transfer_id
        event = self.cancel_events.get(tid)
        if event is None:
            raise RuntimeError("cancel event missing for transfer")
        self._running_ids.add(tid)
        cleanup = self._queued_cleanup.get(tid)
        if cleanup is not None:
            await asyncio.shield(cleanup)
        done, total = 0, bytes_total

        async def terminal(status: HistoryStatus, reason: FailureReason | None = None) -> None:
            self.progress(tid, TransferState(status), done, total)
            await self.finish(tid, status, done, total, reason)

        if event.is_set():
            await terminal("cancelled")
            return False
        if tid in self._descriptors:
            try:
                await self.disk.call(self.journal.mark_attempted, tid)
            except asyncio.CancelledError:
                await terminal("cancelled")
                raise
            except Exception:
                self._feedback()
                await terminal("failed", "persistence_error")
                raise HistoryWriteError("Transfer history could not be saved.") from None
        if event.is_set():
            await terminal("cancelled")
            return False

        def progress(value: TransferProgress) -> None:
            nonlocal done, total
            done, total = value.bytes_transferred, value.bytes_total
            self._totals[tid] = (done, total)
            self._observed_ids.add(tid)
            self.progress(tid, TransferState.RUNNING, done, total)

        copy_task = asyncio.create_task(
            operation(src_path, dst_path, progress=progress, on_conflict=on_conflict)
        )
        cancel_task = asyncio.create_task(event.wait())

        async def settled() -> bool:
            if copy_task.cancelled():
                await terminal("cancelled")
                return False
            error = copy_task.exception()
            if error is not None:
                await terminal("failed", failure_reason(error))
                raise error
            if copy_task.result() is False:
                await terminal("skipped")
                return False
            return True

        try:
            try:
                await asyncio.wait({copy_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            except BaseException:
                if copy_task.done():
                    if await settled():
                        await self.complete(tid, done, total)
                    raise
                copy_task.cancel()
                while not copy_task.done():
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await asyncio.shield(copy_task)
                await terminal("cancelled")
                raise
            if copy_task.done():
                return await settled()
            copy_task.cancel()
            cancelled = False
            while not copy_task.done():
                try:
                    await asyncio.shield(copy_task)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():  # type: ignore[union-attr]
                        cancelled = True
                except Exception:
                    pass
            await terminal("cancelled")
            if cancelled:
                raise asyncio.CancelledError
            return False
        finally:
            cancel_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_task

    async def settle_batch(self, entries: list[tuple[str, int | None]], consumed: set[str]) -> None:
        """Drain all queued terminal writes despite repeated worker cancellation."""

        async def settle() -> None:
            for tid, total in entries:
                try:
                    cleanup = self._queued_cleanup.get(tid)
                    if cleanup is not None:
                        await cleanup
                    if tid not in consumed:
                        await self.finish(tid, "cancelled", 0, total)
                        self.progress(tid, TransferState.CANCELLED, 0, total)
                finally:
                    self.release(tid)

        task = asyncio.create_task(settle())
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def shutdown(self) -> None:
        current = asyncio.current_task()
        owners = set(self._owners.values()) | self._begin_tasks
        for event in self.cancel_events.values():
            event.set()
        for owner in owners:
            if owner is not current:
                owner.cancel()
        for owner in owners:
            if owner is current:
                continue
            while not owner.done():
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(owner)
        for task in tuple(self._queued_cleanup.values()):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(task)
        await self.disk.drain()

    def dispose(self) -> None:
        self._subscription.dispose()
