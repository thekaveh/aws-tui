"""EmrServerlessPageVM — orchestration root for the EMR page.

Owns four child VMs (applications / job runs / detail / job run logs) and wires
the master-detail reactivity between them. The auto-refresh
pollers live in the widget layer (``EmrServerlessPage.on_mount``)
via Textual's ``set_interval`` — there's no domain-tier
``TickSource`` abstraction in PR-A."""

from __future__ import annotations

from typing import Any, Literal

from reactivex.abc import DisposableBase
from vmx import ComponentVMOf, Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_logs import EmrServerlessLogsClient
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM


class EmrServerlessPageVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        connection: Connection,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._connection: Connection = connection
        self._disposed: bool = False
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
        # Construct the logs client wrapper; note: client._session and
        # client._region_name are read as opaque values (no aioboto3 type annotation
        # in this file). The aioboto3-typed surface lives in the dataclass (domain/).
        logs_client = EmrServerlessLogsClient(
            session=client._session,
            region_name=client._region_name,
        )
        self.job_run_logs: JobRunLogsVM = JobRunLogsVM(
            client=logs_client,
            hub=hub,
            dispatcher=dispatcher,
        )
        self._sub: DisposableBase | None = None

    @property
    def connection(self) -> Connection:
        return self._connection

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
        # Wire master-detail.
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self.job_run_logs.dispose()
        self.job_run_detail.dispose()
        self.job_runs.dispose()
        self.applications.dispose()
        self._inner.dispose()

    # ── Public surface ──────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Initial load — fetch applications and auto-select the
        first one (per the user-facing sorted order, so the picker
        dropdown, Shift+S cycle, and the auto-selection all share
        one source of truth — STARTED apps come first) so the LEFT
        pane has something to populate."""
        await self.applications.refresh()
        sorted_apps = self.applications.sorted_applications
        if sorted_apps and self.applications.selected_id is None:
            await self.select_application(sorted_apps[0].id)

    async def select_application(self, app_id: str) -> None:
        self.applications.select(app_id)
        self.job_runs.set_application(app_id)
        await self.job_runs.refresh()
        # Detail follows the first run (if any) on application switch.
        runs = self.job_runs.runs
        if runs:
            await self.select_job_run(runs[0].job_run_id)
        else:
            self.job_run_detail.set_target(None, None)

    async def cycle_application(self, direction: int) -> None:
        """Select the next (``direction=1``) or previous
        (``direction=-1``) application in the picker's user-facing
        order, wrapping at either end. Used by the EMR page's
        ``Shift+S`` binding ("switch app") so the keypress visibly
        moves to the next app — the explicit picker (``a``) stays
        around for long-list lookup. No-op if fewer than 2 apps
        exist.

        Cycle source = :attr:`ApplicationsVM.sorted_applications`,
        not the raw boto order. This keeps the dropdown listing and
        the Shift+S ring in lockstep — STARTED apps come first, then
        transitional / idle / terminated, alphabetical within each
        group. User feedback: "make sure this newly ordered list of
        applications is the source of truth through which switch app
        command cycles".
        """
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
        self.job_runs.select(run_id)
        self.job_run_detail.set_target(self.applications.selected_id, run_id)
        await self.job_run_detail.refresh()
        # Update logs target — does NOT fetch (user has to press
        # Enter in the logs pane). Reads the s3 log uri off the
        # freshly-refreshed detail. If detail is None or has no
        # uri, the logs VM transitions to NO_LOG_CONFIG.
        detail = self.job_run_detail.detail
        self.job_run_logs.set_target(
            self.applications.selected_id,
            run_id,
            detail.s3_monitoring_log_uri if detail is not None else None,
        )

    async def refresh_focused(self, focus: Literal["applications", "runs", "detail"]) -> None:
        """Manual refresh — invoked by the ``r`` keybinding."""
        if focus == "applications":
            await self.applications.refresh()
        elif focus == "runs":
            await self.job_runs.refresh()
        else:
            await self.job_run_detail.refresh()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        # Reserved for future-tier subscriptions (PR-B wires log-state
        # observation here). PR-A has no hub-driven side effects.
        return


__all__ = ["EmrServerlessPageVM"]
