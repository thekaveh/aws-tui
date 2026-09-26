"""Preview rows of one Iceberg table through the local DuckDB port.

Built on :class:`vmx.AsyncResourceVM` -- "component viewmodel for one
cancellable asynchronously acquired value", which is exactly what a preview
scan is. The earlier version hand-rolled that state machine over a bare
``ComponentVM`` and shipped two defects the artifact exists to prevent:

* An in-flight scan for table A wrote its rows into the VM after it had been
  re-bound to table B, so A's data appeared under B's name. ``AsyncResourceVM``
  stamps every operation with an identity and discards a superseded one's value
  (``_is_operation_current``).
* Cancelling wiped a loaded pane to "0 rows", because the cancelled outcome
  overwrote columns and rows with empty tuples. An ordinary database or table
  switch triggers this through
  ``IcebergVM.cancel_metadata_loads_and_drain_silently``. ``AsyncResourceVM``
  restores the pre-load baseline on cancel, and
  ``AsyncResourceRetention.RETAIN_PREVIOUS`` keeps the loaded rows on screen
  while a reload runs and under an error.

Offloads with ``anyio.to_thread.run_sync`` -- never ``asyncio.to_thread``,
because this repo runs blocking work through anyio's limiter. The caller must
additionally reach ``load`` through ``_run_lifecycle_worker``: one offload
keeps the scan off the event loop, the other keeps it off the App's message
pump, and the pump is where the next keypress is dequeued
(``src/aws_tui/app.py:2386``). A local scan is CPU-bound and will freeze the
pump harder than a network wait.

MVVM: the pane binds to this VM's own ``on_property_changed`` observable and
nothing pushes state into it. Unlike most sibling VMs in this package, nothing
here reaches the shared ``MessageHub``: the inner resource is given a private
hub of its own, because ``_ComponentVMBase._notify_property_changed``
dual-publishes every state change and two tests depend on this subtree staying
silent on the shared hub --
``test_no_state_change_is_published_through_the_shared_hub`` here, and
``test_hostile_hub_subscriber_is_value_free_and_does_not_interrupt_state`` in
``test_iceberg_vm.py``, which proves a hostile subscriber's exception text
cannot reach the logs. A view therefore cannot be tempted to filter the hub
instead of binding to ``on_property_changed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import anyio
import reactivex as rx
from reactivex import operators as ops
from vmx import (
    AsyncResourceErrorWithValue,
    AsyncResourceLoadingWithValue,
    AsyncResourceReady,
    AsyncResourceRetention,
    AsyncResourceState,
    AsyncResourceStatus,
    AsyncResourceVM,
    Message,
    MessageHub,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.iceberg_preview import (
    ROW_LIMIT_STEPS,
    iceberg_preview_sql,
    next_row_limit,
)
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbPort
from aws_tui.vm._observable import ObserverSafeSubject
from aws_tui.vm.file_manager.pane_vm import PaneState

# Outcome -> (state, error_text). Exact strings; the view surfaces them
# verbatim and the tests assert on them. OK is absent because it returns a page
# rather than raising, and CANCELLED is absent because a cancelled scan is not a
# failure -- ``_resting_state`` handles it.
_OUTCOME_TABLE: dict[DuckDbOutcome, tuple[PaneState, str | None]] = {
    DuckDbOutcome.FORBIDDEN: (PaneState.FORBIDDEN, "S3 access is forbidden for this table"),
    DuckDbOutcome.NOT_FOUND: (PaneState.ERROR, "table location not found"),
    DuckDbOutcome.AUTH_REQUIRED: (PaneState.AUTH_REQUIRED, "AWS authentication is required"),
    DuckDbOutcome.NOT_ICEBERG: (PaneState.ERROR, "not a readable Iceberg table"),
    DuckDbOutcome.ENGINE_MISSING: (
        PaneState.ERROR,
        "local preview needs DuckDB: pip install aws-tui[duckdb]",
    ),
    DuckDbOutcome.FAILED: (PaneState.ERROR, "local preview failed"),
}


@dataclass(frozen=True, slots=True)
class IcebergPreviewPage:
    """One loaded page of preview rows."""

    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str | None, ...], ...] = ()


class IcebergPreviewFailure(Exception):
    """A scan that failed with a classified engine outcome.

    ``AsyncResourceVM`` speaks exceptions while the port reports outcomes, so
    the loader translates. Carrying the outcome rather than a message keeps the
    engine's own text -- which echoes the S3 path -- out of the VM entirely.
    """

    def __init__(self, outcome: DuckDbOutcome) -> None:
        super().__init__(outcome.value)
        self.outcome = outcome


def _classify(outcome: DuckDbOutcome) -> tuple[PaneState, str | None]:
    return _OUTCOME_TABLE.get(outcome, _OUTCOME_TABLE[DuckDbOutcome.FAILED])


def _outcome_of(error: BaseException) -> DuckDbOutcome:
    if isinstance(error, IcebergPreviewFailure):
        return error.outcome
    return DuckDbOutcome.FAILED


class IcebergPreviewVM:
    """Binds to one Iceberg table location and previews rows through DuckDB."""

    def __init__(
        self,
        port: DuckDbPort,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        del hub  # the shared hub is intentionally unused; see above
        self._port: DuckDbPort = port
        # Deliberately NOT the shared hub -- see the module docstring. The
        # resource is this VM's private implementation detail, not something the
        # app addresses by name, so its property and lifecycle messages have no
        # shared-hub audience.
        self._hub: MessageHub[Message] = MessageHub()
        self._dispatcher: Dispatcher = dispatcher
        self._disposed: bool = False
        self._constructed: bool = False

        self._location: str | None = None
        self._profile: str | None = None
        self._region: str = ""
        self._limit: int = ROW_LIMIT_STEPS[0]
        self._snapshot_id: int | None = None

        # Per-VM Observable -- the pane binds here, never to the shared hub.
        self._on_property_changed: ObserverSafeSubject[str] = ObserverSafeSubject[str]()
        self._resource_subscription: rx.abc.DisposableBase | None = None
        self._resource: AsyncResourceVM[IcebergPreviewPage] = self._new_resource()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    @property
    def status(self) -> ConstructionStatus:
        return self._resource.status

    def construct(self) -> None:
        self._constructed = True
        self._resource.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._release_resource()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()

    def _new_resource(self) -> AsyncResourceVM[IcebergPreviewPage]:
        resource = AsyncResourceVM[IcebergPreviewPage](
            name="glue.iceberg_preview",
            loader=self._load_page,
            hub=self._hub,
            dispatcher=self._dispatcher,
            retention=AsyncResourceRetention.RETAIN_PREVIOUS,
        )
        # The resource publishes only "state"; the pane binds to four separate
        # property names, so fan that one notification back out.
        self._resource_subscription = resource.property_changed.subscribe(
            on_next=self._on_resource_property_changed
        )
        return resource

    def _release_resource(self) -> None:
        subscription = self._resource_subscription
        self._resource_subscription = None
        if subscription is not None:
            subscription.dispose()
        self._resource.dispose()

    def _on_resource_property_changed(self, property_name: str) -> None:
        if property_name != "state":
            return
        for name in ("columns", "rows", "error_text", "state"):
            self._notify(name)

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True only for a valid ``s3://`` location with a non-blank profile.

        A ``None`` profile is how the s3-compatible-connection non-goal is
        enforced: the pane must simply not appear. It is not an error state.
        """
        location = self._location
        profile = self._profile
        if location is None or profile is None or not profile.strip():
            return False
        # The same validator the SQL generator uses, so `available` cannot be
        # True for a location that would be rejected at query time.
        return parse_s3_uri(location) is not None

    @property
    def state(self) -> PaneState:
        resource_state = self._resource.state
        status = resource_state.status
        if status is AsyncResourceStatus.IDLE:
            return PaneState.EMPTY
        if status is AsyncResourceStatus.LOADING:
            return PaneState.LOADING
        if status is AsyncResourceStatus.READY:
            return PaneState.IDLE
        outcome = _outcome_of(_error_of(resource_state))
        if outcome is DuckDbOutcome.CANCELLED:
            return self._resting_state()
        return _classify(outcome)[0]

    @property
    def error_text(self) -> str | None:
        resource_state = self._resource.state
        if resource_state.status is not AsyncResourceStatus.ERROR:
            return None
        outcome = _outcome_of(_error_of(resource_state))
        if outcome is DuckDbOutcome.CANCELLED:
            return None
        return _classify(outcome)[1]

    @property
    def columns(self) -> tuple[str, ...]:
        return self._page().columns

    @property
    def rows(self) -> tuple[tuple[str | None, ...], ...]:
        return self._page().rows

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def has_more(self) -> bool:
        """Honest paging signal: a full page MAY have more; the ceiling is real.

        The sibling Iceberg panes fake this by widening a local window over
        already-capped rows -- this does not: ``load_more`` re-runs a
        genuinely new query at the next row-limit step.
        """
        return len(self.rows) >= self._limit and next_row_limit(self._limit) is not None

    @property
    def snapshot_id(self) -> int | None:
        return self._snapshot_id

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        """Sealed so a subscriber cannot ``on_next`` or dispose the stream.

        VMx seals its own equivalent for the same reason (VMX-013,
        ``vmx/components/base.py``): an external ``dispose`` on the subject
        corrupts every other subscriber.
        """
        return self._on_property_changed.pipe(ops.as_observable())

    # ── Behaviour ───────────────────────────────────────────────────────────

    def bind(self, location: str | None, *, profile: str | None, region: str) -> None:
        """Bind to a table location. Resets to an unloaded, empty pane.

        The resource is replaced rather than reset because ``AsyncResourceVM``
        has no reset: ``cancel`` restores the baseline, which for an already
        loaded pane is the previous table's rows. Rebuilding guarantees no
        retained value and no in-flight operation survives a re-bind, which is
        the other half of the stale-rows defect.
        """
        self._location = location
        self._profile = profile
        self._region = region
        self._snapshot_id = None
        self._limit = ROW_LIMIT_STEPS[0]
        if not self._disposed:
            self._release_resource()
            self._resource = self._new_resource()
            if self._constructed:
                self._resource.construct()
        self._notify("snapshot_id")
        self._notify("limit")
        self._notify("columns")
        self._notify("rows")
        self._notify("error_text")
        self._notify("state")

    async def load(self, snapshot_id: int | None = None) -> None:
        """Run (or re-run) the preview scan at the current row limit.

        A call while unbound is a no-op that leaves the pane EMPTY rather than
        raising: this runs inside a Textual worker, where an escaping exception
        becomes an unhandled worker error instead of a state the user can see.
        The pane guards on `available`, so reaching here unbound is a caller
        bug, not a user-visible failure.
        """
        if self._location is None or self._profile is None:
            self._notify("state")
            return
        self._snapshot_id = snapshot_id
        self._notify("snapshot_id")
        if self._resource.state.status is AsyncResourceStatus.IDLE:
            await self._resource.load()
        else:
            await self._resource.reload()

    async def load_more(self, snapshot_id: int | None = None) -> bool:
        """Re-run the scan at the next row-limit step.

        A real second query, not a widened local window over already-capped
        rows. Returns ``False`` when already at the top step.
        """
        next_limit = next_row_limit(self._limit)
        if next_limit is None:
            return False
        self._limit = next_limit
        self._notify("limit")
        await self.load(snapshot_id)
        return True

    async def cancel(self) -> None:
        """Interrupt the running engine query, then roll the pane back.

        Order matters: the interrupt unblocks the worker thread, and the
        resource's ``cancel`` restores the state the load started from -- which
        is what keeps already-loaded rows on screen.
        """
        await anyio.to_thread.run_sync(self._port.interrupt)
        self._resource.cancel()

    # ── Internal ────────────────────────────────────────────────────────────

    async def _load_page(self) -> IcebergPreviewPage:
        """The resource's loader. Returns a page or raises a classified failure."""
        location = self._location
        profile = self._profile
        if location is None or profile is None:
            return IcebergPreviewPage()
        sql = iceberg_preview_sql(location, limit=self._limit, snapshot_id=self._snapshot_id)
        result = await anyio.to_thread.run_sync(
            partial(self._port.query, sql, profile=profile, region=self._region)
        )
        if result.outcome is not DuckDbOutcome.OK:
            raise IcebergPreviewFailure(result.outcome)
        return IcebergPreviewPage(columns=result.columns, rows=result.rows)

    def _resting_state(self) -> PaneState:
        """Where a cancelled scan leaves the pane.

        Cancelling is not a failure, so it must not show an error. With rows
        retained the pane is simply loaded again; with none it never left EMPTY.
        Reporting IDLE for an empty pane would claim the table has no rows.
        """
        return PaneState.IDLE if self._page().rows else PaneState.EMPTY

    def _page(self) -> IcebergPreviewPage:
        resource_state = self._resource.state
        if isinstance(
            resource_state,
            AsyncResourceReady | AsyncResourceLoadingWithValue | AsyncResourceErrorWithValue,
        ):
            return resource_state.value
        return IcebergPreviewPage()

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._on_property_changed.on_next(property_name)


def _error_of(resource_state: AsyncResourceState[IcebergPreviewPage]) -> BaseException:
    error = getattr(resource_state, "error", None)
    return error if isinstance(error, BaseException) else RuntimeError("unknown preview failure")


__all__ = ["IcebergPreviewFailure", "IcebergPreviewPage", "IcebergPreviewVM"]
