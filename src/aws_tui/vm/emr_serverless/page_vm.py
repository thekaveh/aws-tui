"""EmrServerlessPageVM — orchestration root for the EMR page.

Owns three child VMs (applications / job runs / detail) and wires
the master-detail reactivity between them. The auto-refresh
pollers live in the widget layer (``EmrServerlessPage.on_mount``)
via Textual's ``set_interval`` — there's no domain-tier
``TickSource`` abstraction in PR-A."""

from __future__ import annotations

from typing import Any, Literal

from reactivex.abc import DisposableBase
from vmx import ComponentVMOf, Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
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
        # Wire master-detail.
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self.job_run_detail.dispose()
        self.job_runs.dispose()
        self.applications.dispose()
        self._inner.dispose()

    # ── Public surface ──────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Initial load — fetch applications and auto-select the
        first one so the LEFT pane has something to populate."""
        await self.applications.refresh()
        apps = self.applications.applications
        if apps and self.applications.selected_id is None:
            await self.select_application(apps[0].id)

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

    async def select_job_run(self, run_id: str) -> None:
        self.job_runs.select(run_id)
        self.job_run_detail.set_target(self.applications.selected_id, run_id)
        await self.job_run_detail.refresh()

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
