"""Off-pump library CRUD with publication receipts independent of modal lifetime."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import reactivex as rx

from aws_tui.domain.navigation_workspace import Bookmark, Workspace, WorkspaceError
from aws_tui.domain.workspace_store import WorkspaceStore, WorkspaceWritePermit
from aws_tui.vm._observable import ObserverSafeSubject
from aws_tui.vm.file_manager.transfer_runtime import OwnedDiskOperations


@dataclass(frozen=True, slots=True)
class WorkspaceWriteNotice:
    message: str
    warning: bool = False


class WorkspaceLibraryVM:
    def __init__(self, *, store: WorkspaceStore, read_only: bool = False) -> None:
        self._store = store
        self.read_only = read_only
        self.items: tuple[Workspace, ...] = ()
        self.error_text: str | None = None
        self.busy = False
        self._closed = False
        self._disposed = False
        self._permit: WorkspaceWritePermit | None = None
        self._owners: set[asyncio.Task[Any]] = set()
        self._reads = OwnedDiskOperations()
        self._changed: ObserverSafeSubject[str] = ObserverSafeSubject[str]()
        self._completed: ObserverSafeSubject[WorkspaceWriteNotice] = ObserverSafeSubject()

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._changed

    @property
    def on_write_completed(self) -> rx.Observable[WorkspaceWriteNotice]:
        return self._completed

    def _notify(self) -> None:
        if not self._disposed:
            self._changed.on_next("library")

    def _begin(self, *, write: bool = False) -> asyncio.Task[Any] | None:
        if self._closed or self.busy:
            return None
        if write and self.read_only:
            self.error_text = "Workspace library is read-only in demo mode"
            self._notify()
            return None
        owner = asyncio.current_task()
        assert owner is not None
        if owner.cancelling():
            raise asyncio.CancelledError
        self._owners.add(owner)
        self.busy = True
        self.error_text = None
        self._notify()
        if self._closed or owner.cancelling():
            self._owners.discard(owner)
            self.busy = False
            if owner.cancelling():
                raise asyncio.CancelledError
            return None
        return owner

    def _end(self, owner: asyncio.Task[Any]) -> None:
        self._owners.discard(owner)
        self.busy = False
        self._notify()

    async def _reload(self) -> bool:
        try:
            records = await self._reads.call(self._store.list)
        except WorkspaceError as error:
            self.error_text = str(error)
            return False
        except Exception:
            self.error_text = "Workspace library could not be read"
            return False
        if not self._closed:
            self.items = records
        return True

    async def refresh(self) -> bool:
        owner = self._begin()
        if owner is None:
            return False
        try:
            return await self._reload()
        finally:
            self._end(owner)

    async def _write(self, operation: Callable[[WorkspaceWritePermit], None], message: str) -> bool:
        owner = self._begin(write=True)
        if owner is None:
            return False
        permit = self._permit = WorkspaceWritePermit()
        task = asyncio.create_task(asyncio.to_thread(operation, permit))
        canceled = False
        failure: BaseException | None = None
        warning = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    permit.cancel()
                    canceled = True
                except Exception:
                    break
            try:
                task.result()
            except BaseException as error:
                failure = error
            if failure is not None:
                self.error_text = (
                    str(failure)
                    if isinstance(failure, WorkspaceError)
                    else "Workspace operation could not be completed"
                )
                warning = permit.published
            if not canceled and not self._closed and permit.published:
                try:
                    warning = not await self._reload() or warning
                except asyncio.CancelledError:
                    canceled = True
            if canceled:
                raise asyncio.CancelledError
            return permit.published
        finally:
            # An admitted write may publish after its modal is dismissed. Its
            # app-owned subscriber must receive the actual receipt before drain.
            if permit.published:
                self._completed.on_next(
                    WorkspaceWriteNotice(
                        message
                        + (
                            "; library state could not be confirmed. Refresh to check."
                            if warning
                            else ""
                        ),
                        warning,
                    )
                )
            self._permit = None
            self._end(owner)

    async def save(self, record: Workspace) -> bool:
        return await self._write(
            lambda permit: self._store.save(record, permit=permit), "Workspace saved"
        )

    async def rename(self, record: Workspace, name: str) -> bool:
        return await self._write(
            lambda permit: self._store.rename(record.name, name, expected=record, permit=permit),
            "Workspace renamed",
        )

    async def delete(self, record: Workspace) -> bool:
        return await self._write(
            lambda permit: self._store.delete(record.name, expected=record, permit=permit),
            "Workspace deleted",
        )

    async def add_bookmark(self, record: Workspace, bookmark: Bookmark) -> bool:
        return await self._write(
            lambda permit: self._store.add_bookmark(
                record.name, bookmark, expected=record, permit=permit
            ),
            "Bookmark saved",
        )

    async def rename_bookmark(self, record: Workspace, bookmark: str, name: str) -> bool:
        return await self._write(
            lambda permit: self._store.rename_bookmark(
                record.name, bookmark, name, expected=record, permit=permit
            ),
            "Bookmark renamed",
        )

    async def delete_bookmark(self, record: Workspace, bookmark: str) -> bool:
        return await self._write(
            lambda permit: self._store.delete_bookmark(
                record.name, bookmark, expected=record, permit=permit
            ),
            "Bookmark deleted",
        )

    def close(self) -> None:
        self._closed = True
        if self._permit is not None:
            self._permit.cancel()
        current = asyncio.current_task()
        for owner in tuple(self._owners):
            if owner is not current and not owner.done() and not owner.cancelling():
                owner.cancel()

    async def shutdown(self) -> None:
        self.close()
        current = asyncio.current_task()
        canceled = False
        for owner in tuple(self._owners):
            if owner is current:
                continue
            while not owner.done():
                try:
                    await asyncio.shield(owner)
                except asyncio.CancelledError:
                    if not owner.done():
                        canceled = True
                except Exception:
                    break
            with contextlib.suppress(Exception, asyncio.CancelledError):
                owner.result()
        if not self._disposed:
            self._disposed = True
            self._changed.on_completed()
            self._changed.dispose()
            self._completed.on_completed()
            self._completed.dispose()
        if canceled:
            raise asyncio.CancelledError
