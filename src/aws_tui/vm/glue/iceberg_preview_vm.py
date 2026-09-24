"""Preview rows of one Iceberg table through the local DuckDB port.

Offloads with ``anyio.to_thread.run_sync`` -- never ``asyncio.to_thread``,
because this repo runs blocking work through anyio's limiter. The caller must
additionally reach ``load`` through ``_run_lifecycle_worker``: one offload
keeps the scan off the event loop, the other keeps it off the App's message
pump, and the pump is where the next keypress is dequeued
(``src/aws_tui/app.py:2386``). A local scan is CPU-bound and will freeze the
pump harder than a network wait.

MVVM: this view model publishes every state, rows, columns, limit and error
change on its own :class:`~aws_tui.vm._observable.ObserverSafeSubject`,
exposed as ``on_property_changed``. It never sends through the shared
``MessageHub`` -- unlike most sibling VMs in this package, which dual-publish
to both. The pane binds here directly; no parent pushes state into it.
"""

from __future__ import annotations

from functools import partial

import anyio
import reactivex as rx
from vmx import ComponentVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.iceberg_preview import (
    ROW_LIMIT_STEPS,
    iceberg_preview_sql,
    next_row_limit,
)
from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbPort
from aws_tui.vm._observable import ObserverSafeSubject
from aws_tui.vm.file_manager.pane_vm import PaneState

# Outcome -> (state, error_text). Exact strings; the view surfaces them
# verbatim and the tests assert on them.
_OUTCOME_TABLE: dict[DuckDbOutcome, tuple[PaneState, str | None]] = {
    DuckDbOutcome.OK: (PaneState.IDLE, None),
    DuckDbOutcome.CANCELLED: (PaneState.IDLE, None),
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


def _classify(outcome: DuckDbOutcome) -> tuple[PaneState, str | None]:
    return _OUTCOME_TABLE[outcome]


class IcebergPreviewVM:
    """Binds to one Iceberg table location and previews rows through DuckDB."""

    def __init__(
        self,
        port: DuckDbPort,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._port: DuckDbPort = port
        self._disposed: bool = False

        self._location: str | None = None
        self._profile: str | None = None
        self._region: str = ""

        self._state: PaneState = PaneState.EMPTY
        self._error_text: str | None = None
        self._columns: tuple[str, ...] = ()
        self._rows: tuple[tuple[str | None, ...], ...] = ()
        self._limit: int = ROW_LIMIT_STEPS[0]
        self._snapshot_id: int | None = None

        # Per-VM Observable -- the pane binds here, never to the shared hub.
        self._on_property_changed: ObserverSafeSubject[str] = ObserverSafeSubject[str]()
        # Non-modeled: this VM's domain state lives in plain attributes above
        # and is published exclusively through ``_on_property_changed``. The
        # inner ComponentVM supplies only lifecycle bookkeeping (construct /
        # dispose / status).
        self._inner: ComponentVM = (
            ComponentVM.builder().name("glue.iceberg_preview").services(hub, dispatcher).build()
        )

    # ── Lifecycle ───────────────────────────────────────────────────────────

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True only for a non-blank ``s3://`` location with a non-blank profile.

        A ``None`` profile is how the s3-compatible-connection non-goal is
        enforced: the pane must simply not appear. It is not an error state.
        """
        location = self._location
        profile = self._profile
        if location is None or profile is None:
            return False
        return bool(location.strip()) and location.startswith("s3://") and bool(profile.strip())

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    @property
    def columns(self) -> tuple[str, ...]:
        return self._columns

    @property
    def rows(self) -> tuple[tuple[str | None, ...], ...]:
        return self._rows

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
        return len(self._rows) >= self._limit and next_row_limit(self._limit) is not None

    @property
    def snapshot_id(self) -> int | None:
        return self._snapshot_id

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._on_property_changed

    # ── Behaviour ───────────────────────────────────────────────────────────

    def bind(self, location: str | None, *, profile: str | None, region: str) -> None:
        """Bind to a table location. Resets to an unloaded, empty pane."""
        self._location = location
        self._profile = profile
        self._region = region
        self._snapshot_id = None
        self._limit = ROW_LIMIT_STEPS[0]
        self._columns = ()
        self._rows = ()
        self._error_text = None
        self._set_state(PaneState.EMPTY)
        self._notify("snapshot_id")
        self._notify("limit")
        self._notify("columns")
        self._notify("rows")
        self._notify("error_text")

    async def load(self, snapshot_id: int | None = None) -> None:
        """Run (or re-run) the preview scan at the current row limit."""
        location = self._location
        profile = self._profile
        if location is None or profile is None:
            raise RuntimeError(
                "IcebergPreviewVM.load() requires bind() with a location and profile first"
            )
        self._snapshot_id = snapshot_id
        self._notify("snapshot_id")
        self._set_state(PaneState.LOADING)
        sql = iceberg_preview_sql(location, limit=self._limit, snapshot_id=snapshot_id)
        result = await anyio.to_thread.run_sync(
            partial(self._port.query, sql, profile=profile, region=self._region)
        )
        state, text = _classify(result.outcome)
        self._columns = result.columns
        self._rows = result.rows
        self._error_text = text
        self._notify("columns")
        self._notify("rows")
        self._notify("error_text")
        self._set_state(state)

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
        """Interrupt the running engine query, off the event loop."""
        await anyio.to_thread.run_sync(self._port.interrupt)

    # ── Internal ────────────────────────────────────────────────────────────

    def _set_state(self, state: PaneState) -> None:
        self._state = state
        self._notify("state")

    def _notify(self, property_name: str) -> None:
        if self._disposed:
            return
        self._on_property_changed.on_next(property_name)


__all__ = ["IcebergPreviewVM"]
