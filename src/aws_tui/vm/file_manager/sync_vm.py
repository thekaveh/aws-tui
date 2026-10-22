"""Reviewed sync selections and comparisons, separate from upload receipts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from vmx import Message, MessageHub

from aws_tui.domain.cross_fs import ConflictResolution
from aws_tui.domain.filesystem import ConflictError, PathRef
from aws_tui.domain.recursive_upload import (
    UploadDestination,
    UploadPlan,
    UploadSource,
    UploadSourceChangedError,
)
from aws_tui.domain.sync_plan import (
    SyncLabel,
    SyncPlan,
    SyncRow,
    SyncSettings,
    plan_sync,
    replan_sync,
    select_sync_operations,
)
from aws_tui.domain.upload_batch import UploadOutcome
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.recursive_upload_vm import PreparedUpload, RecursiveUploadVM
from aws_tui.vm.operation_owner import OperationSuperseded


@dataclass(frozen=True, slots=True)
class SyncViewRow:
    comparison: SyncRow
    outcome: UploadOutcome | None
    state: str
    selected: bool


class SyncVM(RecursiveUploadVM):
    def __init__(self, *, source: PaneVM, destination: PaneVM, hub: MessageHub[Message]):
        super().__init__(source=source, destination=destination, hub=hub)
        self.policy = ConflictResolution.OVERWRITE
        self.settings = SyncSettings()
        self.review: SyncPlan | None = None
        self._pending_review: SyncPlan | None = None
        self._baseline: SyncPlan | None = None
        self._records: dict[PathRef, SyncRow] = {}
        self._omitted: set[PathRef] = set()
        self._resolved: set[PathRef] = set()

    def _retire(self) -> None:
        super()._retire()
        self.review = self._pending_review = None

    def invalidate_settings(self) -> None:
        """Retire admission synchronously, including edits whose final values match."""
        if not self._reconcile() or self._accepted is not None:
            return
        self._retire()
        self._resolved.clear()
        self._state, self.error_text = "idle", ""
        self._notify()

    def set_settings(self, settings: SyncSettings) -> None:
        if not isinstance(settings, SyncSettings):
            raise ConflictError("Unknown sync settings")
        if settings == self.settings or self._accepted is not None or not self._reconcile():
            return
        self.settings = settings
        self.invalidate_settings()

    def _retry_sources(self) -> tuple[PathRef, ...]:
        return tuple(
            row.item.source
            for row in self.snapshot.outcomes
            if row.retryable
            and row.item.source not in self._omitted | self._resolved
            and self.settings.includes(
                "/".join(row.item.source.segments[len(self.source_root.segments) :])
            )
        )

    async def _build_plan(self) -> UploadPlan:
        generation = self._generation
        if self._baseline is None:
            review = await plan_sync(
                cast(UploadSource, self._source),
                cast(UploadDestination, self._destination),
                self.source_root,
                self.destination_prefix,
                settings=self.settings,
            )
        else:
            review = await replan_sync(
                replace(self._baseline, settings=self.settings), self._retry_sources()
            )
        if not self._current(generation):
            raise OperationSuperseded()
        self._pending_review = review
        return review.execution

    def _install_plan(self, plan: UploadPlan) -> None:
        review = self._pending_review
        if review is None or review.execution is not plan:
            raise ConflictError("Sync plan lost its complete review")
        self.review, self._pending_review = review, None
        if not self._ever_accepted:
            self._records.clear()
            self._resolved.clear()
        self._resolved.difference_update(row.source for row in review.rows)
        self._records.update((row.source, row) for row in review.rows)
        self._resolved.update(row.source for row in review.rows if row.label == SyncLabel.UNCHANGED)
        super()._install_plan(plan)

    def toggle_operation(self, source: PathRef) -> None:
        if not self._reconcile() or self.busy or self._state != "ready" or self.review is None:
            raise ConflictError("Review a current sync plan before changing selection")
        omitted = set(self.review.omitted)
        if source in omitted:
            omitted.remove(source)
        else:
            omitted.add(source)
        review = select_sync_operations(self.review, excluded=tuple(omitted))
        self._retire()
        self._pending_review = review
        self._install_plan(review.execution)
        self._notify()

    def accept(self, prepared: PreparedUpload) -> None:
        if not self.accepts(prepared) or self.review is None:
            raise ConflictError("Sync review changed; review and confirm again")
        # Install before the base emits reentrant notifications. The first accepted
        # selection bounds every retry; later attempts can only shrink that set.
        if self._baseline is None:
            self._baseline = self.review
        self._omitted.update(self.review.omitted)
        super().accept(prepared)

    @property
    def rows(self) -> tuple[SyncViewRow, ...]:
        outcomes = {row.item.source: row for row in self.snapshot.outcomes}
        selected = {item.source for item in self.plan.objects} if self.plan is not None else set()
        omitted = self._omitted | (set(self.review.omitted) if self.review is not None else set())
        rows = []
        for comparison in sorted(self._records.values(), key=lambda row: row.relative):
            path = comparison.source
            outcome = outcomes.get(path)
            if outcome is not None and outcome.uncertain:
                state = "uncertain"
            elif outcome is not None and outcome.published:
                state = "succeeded"
            elif path in omitted:
                state = "omitted"
            elif not self.settings.includes(comparison.relative):
                state = "excluded"
            elif path in self._resolved:
                state = "unchanged"
            elif self._state == "ready" and self.review is not None:
                state = comparison.label.value
            elif outcome is not None:
                state = (
                    "conflict"
                    if isinstance(outcome.error, (ConflictError, UploadSourceChangedError))
                    else outcome.status.value
                )
            else:
                state = comparison.label.value
            rows.append(SyncViewRow(comparison, outcome, state, path in selected))
        return tuple(rows)

    @property
    def selected_count(self) -> int:
        return len(self.plan.objects) if self.plan is not None else 0

    @property
    def selected_bytes(self) -> int:
        return self.plan.known_bytes if self.plan is not None else 0

    @property
    def remaining_count(self) -> int:
        return sum(
            row.state not in {"omitted", "excluded", "unchanged", "succeeded", "skipped"}
            for row in self.rows
        )

    @property
    def can_retry(self) -> bool:
        return (
            self._reconcile()
            and self._ready()
            and not self.busy
            and self._ever_accepted
            and bool(self._retry_sources())
        )
