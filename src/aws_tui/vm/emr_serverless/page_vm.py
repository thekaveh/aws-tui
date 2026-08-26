"""EmrServerlessPageVM — orchestration root for the EMR page.

Owns four child VMs (applications / job runs / detail / job run logs) and wires
the master-detail reactivity between them. The auto-refresh
pollers live in the widget layer (``EmrServerlessPage.on_mount``)
via Textual's ``set_interval`` — there's no domain-tier
``TickSource`` abstraction in PR-A."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Literal

from vmx import ComponentVMOf, Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_logs import EmrServerlessLogsClient
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded
from aws_tui.vm.service_source_vm import (
    SelectionScope,
    ServiceSelectionStore,
    ServiceSourceContext,
)


class EmrServerlessPageVM:
    def __init__(
        self,
        *,
        client: Any,
        logs_client: EmrServerlessLogsClient,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        connection: Connection,
        selection_store: ServiceSelectionStore | None = None,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._connection: Connection = connection
        self._source = ServiceSourceContext.from_connection(connection)
        self._selection_scope = SelectionScope(
            "emr-serverless", self._source.connection_name, self._source.region
        )
        self._selection_store = selection_store or ServiceSelectionStore()
        self._disposed: bool = False
        self._shutdown_started: bool = False
        self._operations = OperationOwner()
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.page")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self.applications: ApplicationsVM = ApplicationsVM(
            client=client, hub=hub, dispatcher=dispatcher
        )
        self.job_runs: JobRunsVM = JobRunsVM(client=client, hub=hub, dispatcher=dispatcher)
        self.job_run_detail: JobRunDetailVM = JobRunDetailVM(
            client=client, hub=hub, dispatcher=dispatcher
        )
        self.job_run_logs: JobRunLogsVM = JobRunLogsVM(
            client=logs_client,
            hub=hub,
            dispatcher=dispatcher,
        )

    @property
    def connection(self) -> Connection:
        return self._connection

    @property
    def source(self) -> ServiceSourceContext:
        return self._source

    @property
    def client(self) -> Any:
        """EMR Serverless client (``EmrServerlessClient`` or test
        fake). Public so the page widget can hand it to per-action
        VMs (e.g. ``JobRunCloneVM``) without re-piping through the
        composition root."""
        return self._client

    @property
    def dispatcher(self) -> Dispatcher:
        """Dispatcher service the page VM was built with. Public so
        per-action VMs (modals) can share the same dispatcher."""
        return self._dispatcher

    @property
    def hub(self) -> MessageHub[Message]:
        """Hub the page VM was built with. Public for the same reason
        as :attr:`dispatcher`."""
        return self._hub

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        self.applications.construct()
        self.job_runs.construct()
        self.job_run_detail.construct()
        self.job_run_logs.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._operations.close()
        self.job_run_logs.dispose()
        self.job_run_detail.dispose()
        self.job_runs.dispose()
        self.applications.dispose()
        self._inner.dispose()

    async def shutdown(self) -> None:
        self._shutdown_started = True
        self._operations.close()
        await self._operations.cancel_and_drain()
        await self.job_run_logs.shutdown()

    # ── Public surface ──────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Initial load — fetch applications and restore the stored
        selection when available, otherwise select the first one in
        user-facing sorted order so the LEFT pane has something to
        populate."""
        if not await self._run(self.applications.refresh):
            return
        await self._select_after_applications_load()

    async def refresh_applications(self) -> None:
        """Refresh applications and reconcile dependent page state.

        ``ApplicationsVM.refresh()`` only owns the picker list and selected id.
        The page VM owns the master/detail cascade, so if a refresh clears the
        current selection we either select the next sorted application or clear
        the dependent runs/detail/logs targets.
        """
        previous_selected = self.applications.selected_id
        if not await self._run(self.applications.refresh):
            return
        selected = self.applications.selected_id
        if selected is not None:
            if selected != previous_selected or self.job_runs.application_id != selected:
                await self.select_application(selected)
            return

        if self.applications.sorted_applications:
            await self._select_after_applications_load()
            return

        if previous_selected is not None or self.job_runs.application_id is not None:
            self.job_runs.set_application(None)
            self.job_run_detail.set_target(None, None)
            self.job_run_logs.set_target(None, None, None)

    async def select_application(self, app_id: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        self.applications.select(app_id)
        if self.applications.selected_id != app_id:
            return
        self._selection_store.set(self._selection_scope, "application_id", app_id)
        self.job_runs.set_application(app_id)
        if not await self._run(self.job_runs.refresh):
            return
        # Detail + logs follow the first run (if any) on application
        # switch. Without the explicit ``job_run_logs.set_target(None,
        # None, None)`` in the empty-runs branch, the logs pane keeps
        # showing the PRIOR app's lines while the picker, runs list,
        # and detail pane all flip — a visible cross-app stale read.
        runs = self.job_runs.runs
        if runs:
            await self.select_job_run(runs[0].job_run_id)
        else:
            self.job_run_detail.set_target(None, None)
            self.job_run_logs.set_target(None, None, None)

    async def cycle_application(self, direction: int) -> None:
        """Select the next (``direction=1``) or previous
        (``direction=-1``) application in the picker's user-facing
        order, wrapping at either end. Used by the EMR page's
        ``Shift+A`` binding ("switch app") so the keypress visibly
        moves to the next app — the explicit picker (``a``) stays
        around for long-list lookup. No-op if fewer than 2 apps
        exist.

        Cycle source = :attr:`ApplicationsVM.sorted_applications`,
        not the raw boto order. This keeps the dropdown listing and
        the Shift+A ring in lockstep — STARTED apps come first, then
        transitional / idle / terminated, alphabetical within each
        group. User feedback: "make sure this newly ordered list of
        applications is the source of truth through which switch app
        command cycles".
        """
        if self._disposed or self._shutdown_started:
            return
        apps = self.applications.sorted_applications
        if len(apps) < 2:
            return
        current_id = self.applications.selected_id
        try:
            idx = next(i for i, a in enumerate(apps) if a.id == current_id)
        except StopIteration:
            idx = -1
        next_idx = (idx + direction) % len(apps)
        await self.select_application(apps[next_idx].id)

    async def select_job_run(self, run_id: str) -> None:
        if self._disposed or self._shutdown_started:
            return
        self.job_runs.select(run_id)
        if self.job_runs.selected_id != run_id:
            return
        application_id = self.applications.selected_id
        self.job_run_detail.set_target(application_id, run_id)
        # Retarget immediately so a failed detail read cannot leave the
        # previously selected run's logs visible beside the new selection.
        self.job_run_logs.set_target(application_id, run_id, None)
        target = (application_id, run_id)
        if not await self._run(self.job_run_detail.refresh):
            return
        if (self.applications.selected_id, self.job_run_detail._job_run_id) != target:
            return
        # Update logs target — does NOT fetch (user has to press
        # Enter in the logs pane). Reads the s3 log uri off the
        # freshly-refreshed detail. If detail is None or has no
        # uri, the logs VM transitions to NO_LOG_CONFIG.
        self._sync_logs_target_from_detail()

    async def refresh_job_runs(self) -> None:
        """Refresh runs and reconcile detail/log targets with the result."""
        previous_selected = self.job_runs.selected_id
        if not await self._run(self.job_runs.refresh):
            return
        runs = self.job_runs.runs
        selected = self.job_runs.selected_id
        if selected is not None and any(run.job_run_id == selected for run in runs):
            return
        if runs:
            await self.select_job_run(runs[0].job_run_id)
            return
        if previous_selected is not None or self.job_run_detail.detail is not None:
            self.job_run_detail.set_target(None, None)
            self.job_run_logs.set_target(None, None, None)

    async def refresh_job_run_detail(self) -> None:
        """Refresh detail through the page's lifecycle operation owner."""
        if await self._run(self.job_run_detail.refresh):
            self._sync_logs_target_from_detail()

    async def load_more_job_runs(self) -> None:
        """Load the next runs page through the page's lifecycle owner."""
        await self._run(self.job_runs.load_more)

    async def refresh_focused(self, focus: Literal["applications", "runs", "detail"]) -> None:
        """Manual refresh — invoked by the ``r`` keybinding."""
        if focus == "applications":
            await self.refresh_applications()
        elif focus == "runs":
            await self.refresh_job_runs()
        else:
            await self.refresh_job_run_detail()

    async def _select_after_applications_load(self) -> None:
        if self.applications.selected_id is not None:
            return
        apps = self.applications.sorted_applications
        if not apps:
            return
        stored_id = self._selection_store.get(self._selection_scope, "application_id")
        if stored_id is not None and any(app.id == stored_id for app in apps):
            await self.select_application(stored_id)
            return
        await self.select_application(apps[0].id)

    def _sync_logs_target_from_detail(self) -> None:
        detail = self.job_run_detail.detail
        application_id = self.applications.selected_id
        run_id = self.job_runs.selected_id
        if detail is None or detail.application_id != application_id or detail.job_run_id != run_id:
            return
        self.job_run_logs.set_target(
            application_id,
            run_id,
            detail.s3_monitoring_log_uri,
        )

    async def _run(self, operation: Callable[[], Coroutine[Any, Any, None]]) -> bool:
        try:
            await self._operations.run(operation)
        except OperationSuperseded:
            return False
        return not self._disposed and not self._shutdown_started


__all__ = ["EmrServerlessPageVM"]
