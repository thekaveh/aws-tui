"""EmrServerlessPageVM — orchestration root for the EMR page.

Owns four child VMs (applications / job runs / detail / job run logs) and wires
the master-detail reactivity between them. The auto-refresh
pollers live in the widget layer (``EmrServerlessPage.on_mount``)
via Textual's ``set_interval`` — there's no domain-tier
``TickSource`` abstraction in PR-A."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_cloudwatch_logs import cloudwatch_location
from aws_tui.domain.emr_logs import EmrServerlessLogsClientProtocol, LogFilter
from aws_tui.domain.emr_serverless import (
    CANCELLABLE_JOB_RUN_STATES,
    CloudWatchLogConfiguration,
    EmrServerlessClientProtocol,
    JobRunState,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm._observable import send_value_free
from aws_tui.vm.chrome.confirm_vm import ConfirmPath, ConfirmRequest
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogSource
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded
from aws_tui.vm.service_source_vm import (
    SelectionScope,
    ServiceSelectionStore,
    ServiceSourceContext,
)


@dataclass(frozen=True, slots=True)
class EmrCredentialRecoverySnapshot:
    application_id: str | None
    job_run_id: str | None
    run_state_filter: frozenset[JobRunState]
    log_file_key: str | None
    log_filter: LogFilter
    log_source: LogSource | None = None
    cloudwatch_stream_name: str | None = None
    cloudwatch_configuration: CloudWatchLogConfiguration | None = None


@dataclass(frozen=True, slots=True)
class CancelJobRunResult:
    status: Literal[
        "requested",
        "dismissed",
        "busy",
        "inert",
        "stale",
        "superseded",
        "denied",
        "not_found",
        "throttled",
        "unreachable",
        "auth_required",
        "invalid",
        "error",
    ]
    message: str | None = None


@dataclass(frozen=True, slots=True)
class _CancelTarget:
    application_id: str
    job_run_id: str
    source: ServiceSourceContext
    state: JobRunState


class EmrServerlessPageVM:
    def __init__(
        self,
        *,
        client: EmrServerlessClientProtocol,
        logs_client: EmrServerlessLogsClientProtocol,
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
        self._cancel_busy = False
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
    def client(self) -> EmrServerlessClientProtocol:
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

    def can_clone_source(self, application_id: str, job_run_id: str) -> bool:
        detail = self.job_run_detail.detail
        return (
            not self._disposed
            and not self._shutdown_started
            and self.applications.selected_id == application_id
            and self.job_runs.application_id == application_id
            and self.job_runs.selected_id == job_run_id
            and detail is not None
            and detail.application_id == application_id
            and detail.job_run_id == job_run_id
        )

    @property
    def cancel_busy(self) -> bool:
        return self._cancel_busy

    def can_cancel_selected_run(self) -> bool:
        target = self._cancel_target()
        return (
            not self._disposed
            and not self._shutdown_started
            and self._operations.accepting
            and not self._cancel_busy
            and target is not None
            and target.state in CANCELLABLE_JOB_RUN_STATES
        )

    async def cancel_selected_run(
        self,
        ask: Callable[[ConfirmRequest], Awaitable[bool]],
        *,
        source_is_current: Callable[[], bool] | None = None,
    ) -> CancelJobRunResult:
        if not self._cancel_owner_current(source_is_current):
            return CancelJobRunResult("inert", "EMR page is unavailable; no cancellation was sent")
        if self._cancel_busy:
            return CancelJobRunResult("busy")
        target = self._cancel_target()
        if target is None:
            return CancelJobRunResult("inert", "select an EMR job run to request cancellation")
        if target.state not in CANCELLABLE_JOB_RUN_STATES:
            return CancelJobRunResult(
                "inert", f"job run is {target.state.value}; cancellation is unavailable"
            )
        client = self._client
        request_sent = False

        async def operation() -> CancelJobRunResult:
            nonlocal request_sent
            request = ConfirmRequest(
                title="Cancel EMR job run?",
                paths=(
                    ConfirmPath("Source", target.source.label),
                    ConfirmPath("Application", target.application_id),
                    ConfirmPath("Run", target.job_run_id),
                ),
                confirm_label="Request cancellation",
                cancel_label="Keep running",
                danger=True,
            )
            if not await ask(request):
                return CancelJobRunResult("dismissed")
            latest = self._cancel_target()
            if (
                not self._cancel_target_current(target, source_is_current)
                or latest is None
                or latest.state not in CANCELLABLE_JOB_RUN_STATES
            ):
                return CancelJobRunResult(
                    "stale", "selected source or job run changed; no cancellation was sent"
                )
            request_sent = True
            try:
                await client.cancel_job_run(target.application_id, target.job_run_id)
            except Exception as error:
                if not self._cancel_target_current(target, source_is_current):
                    return CancelJobRunResult("superseded")
                return self._cancel_error_result(error)
            if not self._cancel_target_current(target, source_is_current):
                return CancelJobRunResult("superseded")
            return CancelJobRunResult("requested", "cancellation requested")

        # Reserve before any await: queued UI work shares the same reservation
        # through both the confirmation and the single captured provider call.
        self._cancel_busy = True
        try:
            self._notify_cancel_busy()
            result = await self._operations.run(operation)
        except OperationSuperseded:
            result = CancelJobRunResult("superseded")
        finally:
            self._cancel_busy = False
            self._notify_cancel_busy()
        # The owned task's completion and synchronous hub observers can both
        # change ownership before this caller delivers the captured outcome.
        if request_sent and not self._cancel_target_current(target, source_is_current):
            return CancelJobRunResult("superseded")
        return result

    def _cancel_owner_current(self, source_is_current: Callable[[], bool] | None) -> bool:
        return (
            not self._disposed
            and not self._shutdown_started
            and self._operations.accepting
            and (source_is_current is None or source_is_current())
        )

    def _cancel_target_current(
        self, target: _CancelTarget, source_is_current: Callable[[], bool] | None
    ) -> bool:
        latest = self._cancel_target()
        return (
            self._cancel_owner_current(source_is_current)
            and latest is not None
            and (latest.source, latest.application_id, latest.job_run_id)
            == (target.source, target.application_id, target.job_run_id)
        )

    def _notify_cancel_busy(self) -> None:
        if not self._disposed:
            send_value_free(
                self._hub, PropertyChangedMessage.create(self, "emr.page", "cancel_busy")
            )

    @staticmethod
    def _cancel_error_result(error: Exception) -> CancelJobRunResult:
        if isinstance(error, PermissionDeniedError):
            return CancelJobRunResult(
                "denied",
                "CancelJobRun permission denied; check emr-serverless:CancelJobRun permission",
            )
        if isinstance(error, NotFoundError):
            return CancelJobRunResult(
                "not_found", "application or job run was not found; refresh the selected job run"
            )
        if isinstance(error, ThrottledError):
            return CancelJobRunResult(
                "throttled",
                "EMR Serverless throttled the cancellation request; wait before a deliberate retry",
            )
        if isinstance(error, ProviderUnreachableError):
            return CancelJobRunResult(
                "unreachable",
                "cancellation outcome is unconfirmed because of a network failure; refresh job state before a deliberate retry",
            )
        if isinstance(error, AuthRequiredError):
            return CancelJobRunResult(
                "auth_required",
                "authentication is required to request cancellation; refresh credentials before a deliberate retry",
            )
        if isinstance(error, ValidationError):
            return CancelJobRunResult(
                "invalid",
                "EMR Serverless rejected the cancellation request; refresh job state before a deliberate retry",
            )
        return CancelJobRunResult(
            "error", "cancellation request failed; refresh job state before a deliberate retry"
        )

    def _cancel_target(self) -> _CancelTarget | None:
        app_id = self.applications.selected_id
        run_id = self.job_runs.selected_id
        summary = next((run for run in self.job_runs.runs if run.job_run_id == run_id), None)
        if (
            summary is None
            or app_id is None
            or self.job_runs.application_id != app_id
            or summary.application_id != app_id
        ):
            return None
        state = summary.state
        detail = self.job_run_detail.detail
        if (
            detail is not None
            and detail.application_id == app_id
            and detail.job_run_id == summary.job_run_id
            and (
                detail.updated_at > summary.updated_at
                or (
                    detail.updated_at == summary.updated_at
                    and detail.state not in CANCELLABLE_JOB_RUN_STATES
                )
            )
        ):
            state = detail.state
        return _CancelTarget(app_id, summary.job_run_id, self._source, state)

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
        if app_id != self.job_run_logs.application_id:
            self.job_run_logs.set_target(app_id, None, None, metadata_known=False)
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
        self.job_run_logs.set_target(application_id, run_id, None, metadata_known=False)
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

    async def refresh_for_credential_recovery(
        self,
        focus: Literal["applications", "runs", "detail", "logs"],
    ) -> PaneState:
        """Refresh the focused read surface and return its terminal state."""
        if focus == "applications":
            await self.refresh_applications()
            return self.applications.state
        if focus == "runs":
            await self.refresh_job_runs()
            return self.job_runs.state
        if focus == "detail":
            await self.refresh_job_run_detail()
            return self.job_run_detail.state

        await self.job_run_logs.load(use_cache=False)
        return self.job_run_logs.credential_recovery_state

    def export_credential_recovery_snapshot(self) -> EmrCredentialRecoverySnapshot:
        current_file = self.job_run_logs.current_file
        return EmrCredentialRecoverySnapshot(
            application_id=self.applications.selected_id,
            job_run_id=self.job_runs.selected_id,
            run_state_filter=self.job_runs.state_filter,
            log_file_key=current_file.key if current_file is not None else None,
            log_filter=self.job_run_logs.filter,
            log_source=self.job_run_logs.selected_source,
            cloudwatch_stream_name=self.job_run_logs.current_stream.name
            if self.job_run_logs.current_stream
            else None,
            cloudwatch_configuration=self.job_run_logs.cloudwatch_configuration,
        )

    async def restore_and_refresh_for_credential_recovery(
        self,
        snapshot: EmrCredentialRecoverySnapshot,
        focus: Literal["applications", "runs", "detail", "logs"],
    ) -> PaneState:
        """Restore the exact live read target in an off-screen candidate."""
        self.job_runs.set_state_filter(snapshot.run_state_filter)
        await self.setup()
        if snapshot.application_id is not None:
            if not any(app.id == snapshot.application_id for app in self.applications.applications):
                return PaneState.ERROR
            if self.applications.selected_id != snapshot.application_id:
                await self.select_application(snapshot.application_id)
        if snapshot.job_run_id is not None:
            while (
                not any(run.job_run_id == snapshot.job_run_id for run in self.job_runs.runs)
                and self.job_runs.has_more
            ):
                await self.load_more_job_runs()
            if not any(run.job_run_id == snapshot.job_run_id for run in self.job_runs.runs):
                return PaneState.ERROR
            if self.job_runs.selected_id != snapshot.job_run_id:
                await self.select_job_run(snapshot.job_run_id)
        self.job_run_logs.set_filter(snapshot.log_filter)
        if focus == "logs" and snapshot.log_source is not None:
            if snapshot.log_source is LogSource.CLOUDWATCH:
                current = self.job_run_logs.cloudwatch_configuration
                original = snapshot.cloudwatch_configuration
                if (
                    current is None
                    or original is None
                    or snapshot.application_id is None
                    or snapshot.job_run_id is None
                ):
                    return PaneState.ERROR
                try:
                    if cloudwatch_location(
                        current, snapshot.application_id, snapshot.job_run_id
                    ) != cloudwatch_location(
                        original, snapshot.application_id, snapshot.job_run_id
                    ):
                        return PaneState.ERROR
                except ValidationError:
                    return PaneState.ERROR
            self.job_run_logs.select_source(snapshot.log_source)
            if self.job_run_logs.selected_source is not snapshot.log_source:
                return PaneState.ERROR
        if focus == "logs":
            await self.job_run_logs.load(
                use_cache=False,
                preferred_file_key=snapshot.log_file_key,
                preferred_cloudwatch_stream_name=snapshot.cloudwatch_stream_name,
            )
            if (
                snapshot.log_source is LogSource.CLOUDWATCH
                and snapshot.cloudwatch_stream_name is not None
                and (
                    self.job_run_logs.current_stream is None
                    or self.job_run_logs.current_stream.name != snapshot.cloudwatch_stream_name
                )
            ):
                return PaneState.ERROR
            if snapshot.log_file_key is not None and (
                self.job_run_logs.current_file is None
                or self.job_run_logs.current_file.key != snapshot.log_file_key
            ):
                return PaneState.ERROR
            return self.job_run_logs.credential_recovery_state
        return await self.refresh_for_credential_recovery(focus)

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
            cloudwatch=detail.cloudwatch_monitoring,
            run_created_at_ms=int(detail.created_at.timestamp() * 1000),
        )

    async def _run(self, operation: Callable[[], Coroutine[Any, Any, None]]) -> bool:
        try:
            await self._operations.run(operation)
        except OperationSuperseded:
            return False
        return not self._disposed and not self._shutdown_started


__all__ = ["CancelJobRunResult", "EmrCredentialRecoverySnapshot", "EmrServerlessPageVM"]
