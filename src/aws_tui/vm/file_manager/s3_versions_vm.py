"""Owned exact-key history and immutable, explicitly reviewed transfer requests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, TypeVar
from urllib.parse import quote, urlencode

from reactivex import Observable
from reactivex.abc import DisposableBase
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileSystemProvider,
    PathRef,
    ProviderError,
)
from aws_tui.domain.s3_object_details import S3ObjectDetails
from aws_tui.domain.s3_versions import (
    S3ObjectVersion,
    S3RecoveryDestination,
    S3VersionListing,
    S3VersionProvider,
)
from aws_tui.infra.redaction import redact_text
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded

T = TypeVar("T")


def _binding(pane: PaneVM) -> tuple[object, ...]:
    return (
        id(pane.provider),
        pane.current_connection_key,
        pane.path,
        pane.listing_revision,
        pane.status,
        pane.state,
        pane.path_protocol,
    )


@dataclass(frozen=True, slots=True)
class VersionTransferRequest:
    version: S3ObjectVersion
    source: S3VersionProvider
    destination: FileSystemProvider
    destination_path: PathRef
    destination_label: str
    kind: Literal["download", "recovery"]

    @property
    def source_uri(self) -> str:
        return f"s3://{self.version.bucket}/{quote(self.version.key, safe='/')}?{urlencode({'versionId': self.version.version_id})}"

    @property
    def warning(self) -> str | None:
        if not isinstance(self.destination, S3RecoveryDestination):
            return None
        outcome = self.destination.publication
        warnings = []
        if outcome.published:
            warnings.append(
                f"Recovery destination was published and preserved: {self.destination_label}"
            )
        elif outcome.uncertain:
            warnings.append(
                f"Recovery destination may exist; inspect before retrying: {self.destination_label}"
            )
        if outcome.cleanup_error:
            warnings.append(outcome.cleanup_error)
        return redact_text("\n".join(warnings)) if warnings else None


@dataclass(frozen=True, slots=True)
class PreparedVersionTransfer:
    request: VersionTransferRequest
    generation: int
    destination_pane: PaneVM | None = None
    destination_binding: tuple[object, ...] | None = None


class S3VersionsVM:
    def __init__(self, *, pane: PaneVM, hub: MessageHub[Message], dispatcher: Dispatcher) -> None:
        provider = pane.provider
        if not isinstance(provider, S3VersionProvider):
            raise ProviderError("This source does not support exact S3 versions")
        provider.version_location(pane.path)  # Service root without a bucket is ineligible.
        self._pane, self._provider, self._hub = pane, provider, hub
        self._binding = _binding(pane)
        self._prefix = pane.path
        self._inner = (
            ComponentVMOf[None]
            .builder()
            .name("s3.versions")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        entry = pane.selected_entry
        self._key = (
            entry.name
            if entry is not None and entry.kind is EntryKind.FILE and not entry.is_parent_link
            else ""
        )
        self._owner = OperationOwner()
        self._changes = ObserverSafeSubject[str]()
        self._subscription: DisposableBase | None = None
        self._generation = 0
        self._closed = self._disposed = self._busy = False
        self._state = "idle"
        self.listing: S3VersionListing | None = None
        self.selected: S3ObjectVersion | None = None
        self.details: S3ObjectDetails | None = None
        self.error_text = ""
        self.detail_error = ""

    @property
    def key(self) -> str:
        return self._key

    @property
    def prefix_label(self) -> str:
        bucket, key = self._provider.version_location(self._prefix)
        return f"s3://{bucket}/{key + '/' if key else ''}"

    @property
    def state(self) -> str:
        self._reconcile()
        return self._state

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def on_property_changed(self) -> Observable[str]:
        return self._changes

    def construct(self) -> None:
        if self._closed or self._inner.is_constructed:
            return
        self._inner.construct()
        self._subscription = self._pane.on_property_changed.subscribe(
            self._pane_changed, on_completed=self.close
        )
        self._reconcile()

    def _pane_changed(self, _property: str) -> None:
        self._reconcile()

    def _reconcile(self) -> bool:
        if not self._closed and (
            _binding(self._pane) != self._binding
            or self._pane.status is not ConstructionStatus.CONSTRUCTED
            or self._pane.state not in {PaneState.IDLE, PaneState.EMPTY}
        ):
            self.close()
        return not self._closed

    def _current(self, generation: int) -> bool:
        return self._reconcile() and self._inner.is_constructed and generation == self._generation

    def _notify(self) -> None:
        if self._disposed:
            return
        generation = self._generation
        send_value_free(self._hub, PropertyChangedMessage.create(self, "s3.versions", "state"))
        if not self._closed:
            self._reconcile()
        if generation == self._generation and not self._disposed:
            self._changes.on_next("state")
            if not self._closed:
                self._reconcile()

    def _retire(self) -> None:
        self._generation += 1
        self._owner.cancel()
        self._busy = False
        self.details = None
        self.detail_error = ""

    def set_key(self, key: str) -> None:
        if not self._reconcile() or key == self._key:
            return
        self._retire()
        self._key = key
        self.listing = self.selected = None
        self._state, self.error_text = "idle", ""
        self._notify()

    async def _read(
        self, generation: int, operation: Callable[[], Coroutine[Any, Any, T]]
    ) -> tuple[T | None, str | None]:
        async def protected() -> tuple[T | None, str | None]:
            if not self._current(generation):
                return None, None
            try:
                return await operation(), None
            except Exception as error:
                return None, redact_text(str(error)) or "S3 version read failed"

        try:
            result = await self._owner.run(protected)
        except OperationSuperseded:
            return None, None
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                if self._current(generation):
                    self._busy = False
                    if self._state == "loading":
                        self._state = "idle"
                    self._notify()
                raise
            return None, None
        return result if self._current(generation) else (None, None)

    async def load(self) -> None:
        if not self._reconcile():
            return
        self._retire()
        generation = self._generation
        self.listing = self.selected = None
        self.error_text = ""
        if not self._key:
            self._state, self.error_text = "error", "Enter an exact key relative to this prefix"
            self._notify()
            return
        path = PathRef(self._prefix.segments + tuple(self._key.split("/")))
        self._state, self._busy = "loading", True
        self._notify()
        result, error = await self._read(generation, lambda: self._provider.list_versions(path))
        if not self._current(generation):
            return
        self._busy = False
        if error:
            self._state, self.error_text = "error", error
        elif result is not None:
            self.listing = result
            self.selected = result.versions[0] if result.versions else None
            self._state = "ready" if result.versions else "empty"
        self._notify()

    def select(self, index: int) -> None:
        if (
            not self._reconcile()
            or self.listing is None
            or not 0 <= index < len(self.listing.versions)
        ):
            return
        version = self.listing.versions[index]
        if version == self.selected:
            return
        self._retire()
        self.selected = version
        self._notify()

    async def inspect(self) -> None:
        if not self._reconcile() or self.selected is None or self.selected.is_delete_marker:
            return
        version = self.selected
        self._retire()
        generation = self._generation
        self._busy = True
        self._notify()
        result, error = await self._read(
            generation, lambda: self._provider.read_version_details(version)
        )
        if self._current(generation):
            self.details, self.detail_error, self._busy = result, error or "", False
            self._notify()

    def _transfer_version(self) -> S3ObjectVersion:
        if not self._reconcile() or self.selected is None:
            raise ConflictError("Select a historical file version first")
        if self.selected.is_delete_marker:
            raise ConflictError("An S3 delete marker has no file content")
        if self._busy:
            raise ConflictError("Wait for the current version read to finish")
        return self.selected

    def prepare_download(self, destination: PaneVM, filename: str) -> PreparedVersionTransfer:
        version = self._transfer_version()
        if destination.path_protocol not in {"", "file:", "local:"} or destination.state not in {
            PaneState.IDLE,
            PaneState.EMPTY,
        }:
            raise ConflictError("Download requires a ready local destination pane")
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ConflictError("Enter one local filename in the destination pane directory")
        path = destination.path.join(filename)
        request = VersionTransferRequest(
            version,
            self._provider,
            destination.provider,
            path,
            destination.viewmodel.border_title.rstrip("/") + "/" + filename,
            "download",
        )
        return PreparedVersionTransfer(
            request, self._generation, destination, _binding(destination)
        )

    async def prepare_recovery(self, key: str) -> PreparedVersionTransfer:
        version = self._transfer_version()
        if not key:
            raise ConflictError("Enter a different exact recovery key")
        path = PathRef(self._prefix.segments + tuple(key.split("/")))
        generation = self._generation
        self._busy = True
        self._notify()
        target, error = await self._read(
            generation, lambda: self._provider.recovery_destination(version, path)
        )
        if not self._current(generation) or target is None:
            if self._current(generation):
                self._busy = False
                self._notify()
            raise ConflictError(error or "Version selection changed; review it again")
        self._busy = False
        self._notify()
        bucket, raw_key = self._provider.version_location(path)
        request = VersionTransferRequest(
            version, self._provider, target, path, f"s3://{bucket}/{raw_key}", "recovery"
        )
        return PreparedVersionTransfer(request, generation)

    def accepts(self, prepared: PreparedVersionTransfer) -> bool:
        return (
            self._current(prepared.generation)
            and prepared.request.source is self._provider
            and prepared.request.version == self.selected
            and (
                prepared.destination_pane is None
                or (
                    _binding(prepared.destination_pane) == prepared.destination_binding
                    and prepared.destination_pane.status is ConstructionStatus.CONSTRUCTED
                )
            )
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._owner.close()
        self._state, self._busy = "closed", False
        self.listing = self.selected = self.details = None
        self._notify()

    async def shutdown(self) -> None:
        self.close()
        await self._owner.cancel_and_drain()

    def dispose(self) -> None:
        if self._disposed:
            return
        self.close()
        self._disposed = True
        if self._subscription is not None:
            self._subscription.dispose()
        self._changes.on_completed()
        self._changes.dispose()
        self._inner.dispose()
