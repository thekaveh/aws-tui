"""Read-only upload reviews and single-use admission bound to captured panes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from reactivex import Observable
from reactivex.abc import DisposableBase
from vmx import Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import ConflictError, EntryKind, PathRef
from aws_tui.domain.recursive_upload import UploadDestination, UploadPlan, UploadSource, plan_upload
from aws_tui.domain.upload_batch import (
    UploadBatch,
    UploadOutcome,
    UploadSnapshot,
    UploadStatus,
    replan_failed,
)
from aws_tui.infra.redaction import redact_text
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from aws_tui.vm.file_manager.recursive_upload_transfer import execute_upload_transfer
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded


def _binding(pane: PaneVM) -> tuple[object, ...]:
    return id(pane.provider), pane.current_connection_key, pane.path, pane.path_protocol


@dataclass(frozen=True, slots=True)
class PreparedUpload:
    plan: UploadPlan
    generation: int


class RecursiveUploadVM:
    def __init__(self, *, source: PaneVM, destination: PaneVM, hub: MessageHub[Message]):
        entry = source.selected_entry
        if (
            source.path_protocol not in {"", "file:", "local:"}
            or entry is None
            or entry.is_parent_link
            or entry.kind is not EntryKind.DIRECTORY
            or destination.path_protocol != "s3:"
            or destination.current_connection_key is None
            or not destination.path.segments
        ):
            raise ConflictError("Choose a local folder and a ready S3 destination inside a bucket")
        for provider, methods in (
            (
                source.provider,
                ("inspect_upload_entry", "list_upload_directory", "read_upload_stream"),
            ),
            (
                destination.provider,
                ("upload_location", "inspect_upload_destination", "write_upload_stream"),
            ),
        ):
            if any(not callable(getattr(provider, method, None)) for method in methods):
                raise ConflictError("These providers do not support reviewed recursive uploads")
        self.source_pane, self.destination_pane = source, destination
        self._source, self._destination = source.provider, destination.provider
        self._bindings = _binding(source), _binding(destination)
        self._selection = entry
        self._revisions = source.listing_revision, destination.listing_revision
        self.source_root = source.path.join(entry.name)
        self.destination_prefix = PathRef((*destination.path.segments, entry.name))
        self.source_label = source.viewmodel.border_title.rstrip("/") + "/" + entry.name
        self.source_identity = source.identity_label or "Local filesystem"
        self.destination_identity = destination.identity_label or " · ".join(
            destination.current_connection_key
        )
        bucket, key = cast(UploadDestination, self._destination).upload_location(
            self.destination_prefix
        )
        self.destination_label = f"s3://{bucket}/{key}/"
        self._hub, self._owner = hub, OperationOwner()
        self._changes = ObserverSafeSubject[str]()
        self._subscriptions: list[DisposableBase] = []
        self._generation = 0
        self._closed = self._disposed = self._busy = self._ever_accepted = False
        self._constructed = False
        self._state = "idle"
        self.policy = ConflictResolution.ERROR
        self.plan: UploadPlan | None = None
        self._prepared: PreparedUpload | None = None
        self._accepted: PreparedUpload | None = None
        self._executing = False
        self._cancel_requested = False
        self._batch: UploadBatch | None = None
        self._outcomes: dict[PathRef, UploadOutcome] = {}
        self._known_bytes = 0
        self.error_text = ""

    @property
    def on_property_changed(self) -> Observable[str]:
        return self._changes

    @property
    def state(self) -> str:
        self._reconcile()
        return self._state

    @property
    def busy(self) -> bool:
        return self._busy or self._accepted is not None

    def construct(self) -> None:
        if self._constructed or self._closed:
            return
        self._constructed = True
        for pane in (self.source_pane, self.destination_pane):
            self._subscriptions.append(
                pane.on_property_changed.subscribe(self._pane_changed, on_completed=self.close)
            )
        self._reconcile()

    def _pane_changed(self, _property: str) -> None:
        if self._reconcile() and self._ever_accepted:
            self._notify()

    def _ready(self) -> bool:
        return all(
            pane.status is ConstructionStatus.CONSTRUCTED
            and pane.state in {PaneState.IDLE, PaneState.EMPTY}
            for pane in (self.source_pane, self.destination_pane)
        )

    def _reconcile(self) -> bool:
        if not self._closed and (
            (_binding(self.source_pane), _binding(self.destination_pane)) != self._bindings
            or any(
                pane.status is not ConstructionStatus.CONSTRUCTED
                for pane in (self.source_pane, self.destination_pane)
            )
            or (
                not self._ever_accepted
                and (
                    not self._ready()
                    or self.source_pane.selected_entry is not self._selection
                    or (self.source_pane.listing_revision, self.destination_pane.listing_revision)
                    != self._revisions
                )
            )
        ):
            self.close()
        elif (
            not self._closed
            and self._ever_accepted
            and self._accepted is None
            and self._state in {"planning", "ready"}
            and (self.source_pane.listing_revision, self.destination_pane.listing_revision)
            != self._revisions
        ):
            self._retire()
            self._state = "idle"
            self.error_text = "Pane listing changed; review remaining uploads again"
            self._notify()
        return not self._closed

    def _current(self, generation: int) -> bool:
        return self._reconcile() and self._constructed and generation == self._generation

    def _notify(self) -> None:
        if self._disposed:
            return
        generation = self._generation
        send_value_free(self._hub, PropertyChangedMessage.create(self, "s3.upload", "state"))
        self._reconcile()
        if generation == self._generation and not self._disposed:
            self._changes.on_next("state")
            self._reconcile()

    def _retire(self) -> None:
        self._generation += 1
        self._owner.cancel()
        self.plan = self._prepared = None
        self._busy = False

    def set_policy(self, policy: ConflictResolution) -> None:
        if not isinstance(policy, ConflictResolution):
            raise ConflictError("Unknown upload conflict policy")
        if not self._reconcile() or self._accepted is not None or policy == self.policy:
            return
        self._retire()
        self.policy, self._state, self.error_text = policy, "idle", ""
        self._notify()

    async def load(self) -> None:
        if not self._reconcile() or not self._ready() or self._accepted is not None:
            return
        self._retire()
        generation = self._generation
        self._revisions = self.source_pane.listing_revision, self.destination_pane.listing_revision
        self._state, self._busy, self.error_text = "planning", True, ""
        self._notify()

        async def read() -> UploadPlan | None:
            if not self._current(generation):
                return None
            return await self._build_plan()

        try:
            plan = await self._owner.run(read)
        except OperationSuperseded:
            return
        except asyncio.CancelledError:
            if self._current(generation):
                self._busy, self._state = False, "idle"
                self._notify()
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            return
        except Exception as exc:
            if self._current(generation):
                self._state, self._busy = "error", False
                self.error_text = redact_text(str(exc)) or "Upload planning failed"
                self._notify()
            return
        if not self._current(generation) or plan is None:
            return
        self._install_plan(plan)
        self._notify()

    async def _build_plan(self) -> UploadPlan:
        if self._batch is not None:
            return await replan_failed(self._batch, policy=self.policy)
        return await plan_upload(
            cast(UploadSource, self._source),
            cast(UploadDestination, self._destination),
            self.source_root,
            self.destination_prefix,
            policy=self.policy,
        )

    def _install_plan(self, plan: UploadPlan) -> None:
        self.plan, self._state, self._busy = plan, "ready", False
        if not self._ever_accepted:
            self._known_bytes = plan.known_bytes
            self._outcomes = {
                row.source: UploadOutcome(
                    row, row.destination, UploadStatus.PENDING, False, 0, False, False, None, None
                )
                for row in plan.objects
            }
        else:
            reviewed_sizes = {row.source: row.source_entry.size or 0 for row in plan.objects}
            self._known_bytes = sum(
                reviewed_sizes.get(path, row.item.source_entry.size or 0)
                for path, row in self._outcomes.items()
            )

    def prepare(self) -> PreparedUpload:
        if (
            not self._reconcile()
            or not self._ready()
            or self.busy
            or self._state != "ready"
            or self.plan is None
            or not self.plan.objects
        ):
            raise ConflictError("Review a nonempty current upload plan first")
        self._prepared = PreparedUpload(self.plan, self._generation)
        return self._prepared

    def accepts(self, prepared: PreparedUpload) -> bool:
        return (
            self._current(prepared.generation)
            and self._ready()
            and not self.busy
            and self._state == "ready"
            and prepared is self._prepared
            and prepared.plan is self.plan
            and prepared.plan.source is cast(object, self._source)
            and prepared.plan.destination is cast(object, self._destination)
        )

    def accept(self, prepared: PreparedUpload) -> None:
        if not self.accepts(prepared):
            raise ConflictError("Upload review changed; review and confirm again")
        self._accepted, self._prepared = prepared, None
        self._batch, self._cancel_requested = None, False
        self._ever_accepted, self._state = True, "queued"
        self._notify()

    async def execute(self, prepared: PreparedUpload, runtime: TransferRuntime) -> None:
        if self._accepted is not prepared or self._executing:
            raise ConflictError("Upload must be explicitly accepted once before execution")
        self._executing = True
        if not self._closed:
            self._state = "running"

        def changed(batch: UploadBatch) -> None:
            self._batch = batch
            if self._cancel_requested:
                batch.cancel()
            self._notify()  # Value-free; the view pulls a snapshot on its own cadence.

        try:
            await execute_upload_transfer(
                prepared.plan,
                runtime,
                source_uri=self.source_label,
                destination_uri=self.destination_label,
                on_change=changed,
            )
        except Exception as exc:
            self.error_text = redact_text(str(exc)) or "Upload failed"
            raise
        finally:
            if self._batch is not None:
                self._outcomes.update(
                    (row.item.source, row) for row in self._batch.snapshot.outcomes
                )
            self._accepted, self._executing = None, False
            if not self._closed:
                self._state = "results"
            self._notify()

    @property
    def snapshot(self) -> UploadSnapshot:
        rows = self._outcomes.copy()
        active = self._batch.snapshot if self._batch is not None else None
        if active is not None:
            rows.update((row.item.source, row) for row in active.outcomes)
        outcomes = tuple(rows.values())
        finished = self._ever_accepted and self._accepted is None
        if self._accepted is not None:
            status = UploadStatus.RUNNING
        elif any(
            row.status == UploadStatus.FAILED or row.uncertain or row.cleanup_error
            for row in outcomes
        ):
            status = UploadStatus.FAILED
        elif any(row.status == UploadStatus.CANCELED for row in outcomes):
            status = UploadStatus.CANCELED
        elif finished:
            status = UploadStatus.SUCCEEDED
        else:
            status = UploadStatus.PENDING
        return UploadSnapshot(outcomes, status, finished, self._known_bytes)

    def destination_location(self, path: PathRef) -> tuple[str, str]:
        """Pure mapping from the captured provider, including fixed bucket/prefix."""
        return cast(UploadDestination, self._destination).upload_location(path)

    @property
    def can_retry(self) -> bool:
        return (
            self._reconcile()
            and self._ready()
            and not self.busy
            and self._batch is not None
            and self._batch.snapshot.finished
            and any(row.retryable for row in self._batch.snapshot.outcomes)
        )

    def cancel(self) -> None:
        if self._accepted is not None:
            self._cancel_requested = True
            if self._batch is not None:
                self._batch.cancel()

    def close(self) -> None:
        if self._closed:
            return
        self._closed, self._state = True, "closed"
        self._retire()
        self._owner.close()
        self._notify()

    async def shutdown(self) -> None:
        self.close()
        await self._owner.cancel_and_drain()

    def dispose(self) -> None:
        if self._disposed:
            return
        self.close()
        self._disposed = True
        for subscription in self._subscriptions:
            subscription.dispose()
        self._changes.on_completed()
        self._changes.dispose()
