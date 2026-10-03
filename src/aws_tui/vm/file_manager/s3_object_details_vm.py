"""Read-only S3 details owned by the pane's current object revision."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum

import reactivex as rx
from reactivex.abc import DisposableBase
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import EntryKind, PathRef
from aws_tui.domain.s3_object_details import S3ObjectDetails, S3ObjectDetailsProvider
from aws_tui.infra.redaction import redact_text
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded


class S3ObjectDetailsState(StrEnum):
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ObjectDetailField:
    label: str
    value: str
    copy_value: str | None


@dataclass(frozen=True, slots=True)
class _Selection:
    # Provider identity is deliberately separate from equality: two providers
    # can represent the same source/path while owning different live clients.
    provider_id: int
    source: tuple[str, str] | None
    protocol: str
    pane_path: PathRef
    selected_path: PathRef | None
    kind: EntryKind | None
    parent: bool
    listing_revision: int
    status: ConstructionStatus
    pane_state: PaneState


def _field(label: str, value: str | None) -> ObjectDetailField:
    if value is None:
        return ObjectDetailField(label, "Unavailable", None)
    return ObjectDetailField(label, value if value else "(empty)", value)


def _collection(
    label: str, values: tuple[tuple[str, str], ...] | None, error: str | None = None
) -> ObjectDetailField:
    if error is not None:
        return ObjectDetailField(label, f"Unavailable: {redact_text(error) or 'read failed'}", None)
    # JSON preserves literal strings (including newlines/markup), and the
    # returned empty mapping remains distinct from an unavailable section.
    return _field(
        label, json.dumps(dict(values), ensure_ascii=False) if values is not None else None
    )


def _project(details: S3ObjectDetails) -> tuple[ObjectDetailField, ...]:
    size = (
        ObjectDetailField("Size", f"{details.size} bytes", str(details.size))
        if details.size is not None
        else _field("Size", None)
    )
    return (
        _field("Content type", details.content_type),
        _field("Content encoding", details.content_encoding),
        size,
        _field(
            "Last modified", details.modified.isoformat() if details.modified is not None else None
        ),
        _field("Storage class", details.storage_class),
        _field("ETag", details.etag),
        _field("Version ID", details.version_id),
        _collection("Encryption", details.encryption),
        _collection("User metadata", details.metadata),
        _collection("Tags", details.tags, details.tags_error),
        _collection("Checksums", details.checksums, details.checksums_error),
        _field("Checksum type", details.checksum_type),
        ObjectDetailField(
            "Checksum verification", "Not performed; values are reported by S3", None
        ),
    )


class S3ObjectDetailsVM:
    def __init__(self, *, pane: PaneVM, hub: MessageHub[Message], dispatcher: Dispatcher) -> None:
        self._pane = pane
        self._hub = hub
        self._inner = (
            ComponentVMOf[None]
            .builder()
            .name("s3.object_details")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._owner = OperationOwner()
        self._on_property_changed = ObserverSafeSubject[str]()
        self._subscription: DisposableBase | None = None
        self._selection: _Selection | None = None
        self._generation = 0
        self._started_generation: int | None = None
        self._state = S3ObjectDetailsState.UNAVAILABLE
        self._title = "S3 object details"
        self._fields: tuple[ObjectDetailField, ...] = ()
        self._closed = False
        self._disposed = False

    @property
    def request_generation(self) -> int:
        return self._generation

    @property
    def state(self) -> S3ObjectDetailsState:
        return self._state

    @property
    def title(self) -> str:
        return self._title

    @property
    def fields(self) -> tuple[ObjectDetailField, ...]:
        return self._fields

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    def construct(self) -> None:
        if self._closed or self._inner.is_constructed:
            return
        if self._pane.status is ConstructionStatus.DISPOSED:
            self.close()
            return
        self._inner.construct()
        if self._closed:
            return
        subscription = self._pane.on_property_changed.subscribe(
            on_next=lambda _: self._reconcile(), on_completed=self.close
        )
        if self._closed:
            subscription.dispose()
            return
        self._subscription = subscription
        self._reconcile()

    def _snapshot(self) -> _Selection:
        entry = self._pane.selected_entry
        return _Selection(
            provider_id=id(self._pane.provider),
            source=self._pane.current_connection_key,
            protocol=self._pane.path_protocol,
            pane_path=self._pane.path,
            selected_path=self._pane.path.join(entry.name) if entry is not None else None,
            kind=entry.kind if entry is not None else None,
            parent=entry.is_parent_link if entry is not None else False,
            listing_revision=self._pane.listing_revision,
            status=self._pane.status,
            pane_state=self._pane.state,
        )

    def _eligible(self, selection: _Selection) -> bool:
        return (
            selection.status is ConstructionStatus.CONSTRUCTED
            and selection.pane_state is PaneState.IDLE
            and selection.protocol == "s3:"
            and selection.source is not None
            and selection.source[0] in {"aws", "s3-compatible"}
            and selection.selected_path is not None
            and selection.kind is EntryKind.FILE
            and not selection.parent
            and isinstance(self._pane.provider, S3ObjectDetailsProvider)
        )

    def _reconcile(self) -> None:
        if self._closed or not self._inner.is_constructed:
            return
        selection = self._snapshot()
        if selection.status is ConstructionStatus.DISPOSED:
            self.close()
            return
        if selection == self._selection:
            return
        self._selection = selection
        self._generation += 1
        generation = self._generation
        self._owner.cancel()
        self._fields = ()
        self._title = (
            f"S3 object details: s3:/{selection.selected_path.as_posix()}"
            if selection.selected_path is not None
            else "S3 object details"
        )
        self._state = (
            S3ObjectDetailsState.LOADING
            if self._eligible(selection)
            else S3ObjectDetailsState.UNAVAILABLE
        )
        # Commit all projection values before notifying synchronous observers.
        # Any observer can change the pane again; cease the older publication.
        for prop in ("fields", "title", "state", "request_generation"):
            self._notify(prop, generation)

    def _current(self, generation: int, selection: _Selection) -> bool:
        self._reconcile()
        return (
            not self._closed
            and self._inner.is_constructed
            and generation == self._generation
            and selection == self._selection
            and selection == self._snapshot()
            and self._eligible(selection)
        )

    async def load_revision(self, generation: int) -> None:
        self._reconcile()
        selection = self._selection
        if (
            selection is None
            or not self._current(generation, selection)
            or self._started_generation == generation
        ):
            return
        provider = self._pane.provider
        if not isinstance(provider, S3ObjectDetailsProvider) or selection.selected_path is None:
            return
        path = selection.selected_path
        self._started_generation = generation

        async def read() -> tuple[S3ObjectDetailsState, tuple[ObjectDetailField, ...]] | None:
            # OperationOwner starts a separate task: the pane can change after
            # outer validation but before this coroutine reaches provider I/O.
            if not self._current(generation, selection):
                return None
            # Redact ordinary failures *inside* the owned coroutine, including
            # projection errors, so owner cleanup never logs raw domain text.
            try:
                result = await provider.read_object_details(path)
                return S3ObjectDetailsState.READY, _project(result)
            except Exception as error:
                safe = redact_text(str(error)) or "Object details read failed"
                return S3ObjectDetailsState.ERROR, (ObjectDetailField("Error", safe, None),)

        try:
            outcome = await self._owner.run(read)
        except OperationSuperseded:
            return
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            return
        if outcome is None or not self._current(generation, selection):
            return
        state, projected = outcome
        self._fields = projected
        self._state = state
        for prop in ("fields", "state"):
            self._notify(prop, generation)

    def _notify(self, prop: str, generation: int) -> None:
        # Pane lifecycle transitions need not emit pane-property events.
        # Check the actual snapshot at every synchronous publication boundary.
        self._reconcile()
        if self._closed or self._disposed or generation != self._generation:
            return
        send_value_free(self._hub, PropertyChangedMessage.create(self, self._inner.name, prop))
        self._reconcile()
        if not self._closed and not self._disposed and generation == self._generation:
            self._on_property_changed.on_next(prop)
            self._reconcile()

    def close(self) -> None:
        """Synchronously invalidate the target and cancel all owned reads."""
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._fields = ()
        self._state = S3ObjectDetailsState.CLOSED
        self._owner.close()
        if self._subscription is not None:
            self._subscription.dispose()
            self._subscription = None
        # A terminal transition is observable once. Reads and pane callbacks
        # cannot publish after this transition, including reentrant observers.
        for prop in ("fields", "state"):
            if self._disposed:
                break
            send_value_free(self._hub, PropertyChangedMessage.create(self, self._inner.name, prop))
            if not self._disposed:
                self._on_property_changed.on_next(prop)

    async def shutdown(self) -> None:
        self.close()
        await self._owner.cancel_and_drain()

    def dispose(self) -> None:
        if self._disposed:
            return
        self.close()
        self._disposed = True
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()


__all__ = ["ObjectDetailField", "S3ObjectDetailsState", "S3ObjectDetailsVM"]
