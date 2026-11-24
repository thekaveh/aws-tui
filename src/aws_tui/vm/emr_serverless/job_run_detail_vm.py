"""Retain the selected EMR job run's detail through same-run polls."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

import reactivex as rx
from vmx import (
    NULL_MESSAGE_HUB,
    AsyncResourceError,
    AsyncResourceErrorWithValue,
    AsyncResourceLoadingWithValue,
    AsyncResourceReady,
    AsyncResourceRetention,
    AsyncResourceState,
    AsyncResourceStatus,
    AsyncResourceVM,
    ComponentVMOf,
    ConstructionStatus,
    Message,
    MessageHub,
    PropertyChangedMessage,
)
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import (
    EmrServerlessClientProtocol,
    JobRunDetail,
    JobRunState,
    JobRunSummary,
)
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm._errors import map_unexpected_error
from aws_tui.vm._observable import ObserverSafeSubject, send_value_free
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

_TERMINAL_STATES: frozenset[JobRunState] = frozenset(
    {JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED}
)


@dataclass
class _ReadCancellation:
    outcome: tuple[AsyncResourceState[JobRunDetail], asyncio.CancelledError] | None = None


_READ_CANCELLATION: ContextVar[_ReadCancellation | None] = ContextVar(
    "emr_job_run_detail_read_cancellation", default=None
)


def _classify_error(error: BaseException) -> tuple[PaneState, str | None]:
    if isinstance(error, ProviderError):
        return map_provider_error(error)
    return map_unexpected_error(error)


class JobRunDetailVM:
    def __init__(
        self,
        *,
        client: EmrServerlessClientProtocol,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher = dispatcher
        self._application_id: str | None = None
        self._job_run_id: str | None = None
        self._summary: JobRunSummary | None = None
        self._disposed: bool = False
        self._provider_tasks: set[asyncio.Task[JobRunDetail]] = set()
        self._reported_failure: (
            AsyncResourceError[JobRunDetail] | AsyncResourceErrorWithValue[JobRunDetail] | None
        ) = None
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.job_run_detail")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        # Per-VM Observable (round-3 §9.bis.11 / PR #103 retirement
        # path): fires the name of the property that just changed,
        # scoped to THIS VM instance. The detail-pane view subscribes
        # here instead of filtering shared MessageHub events by
        # ``sender_object``.
        self._on_property_changed = ObserverSafeSubject[str]()
        self._resource_subscription: rx.abc.DisposableBase
        self._resource = self._new_resource()

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def detail(self) -> JobRunDetail | None:
        current = self._resource.state
        if isinstance(
            current,
            AsyncResourceReady | AsyncResourceLoadingWithValue | AsyncResourceErrorWithValue,
        ):
            return current.value
        return None

    @property
    def summary(self) -> JobRunSummary | None:
        return self._summary

    def set_summary(self, summary: JobRunSummary | None) -> None:
        """Retain only already-loaded metadata for this exact target."""
        if self._disposed:
            return
        if summary is not None and (summary.application_id, summary.job_run_id) != (
            self._application_id,
            self._job_run_id,
        ):
            summary = None
        if self._summary != summary:
            self._summary = summary
            self._notify("summary")

    @property
    def state(self) -> PaneState:
        if self._application_id is None or self._job_run_id is None:
            return PaneState.EMPTY
        current = self._resource.state
        if current.status is AsyncResourceStatus.READY:
            return PaneState.IDLE
        if current.status is AsyncResourceStatus.ERROR:
            return _classify_error(current.error)[0]
        return PaneState.LOADING

    @property
    def error_text(self) -> str | None:
        current = self._resource.state
        if current.status is AsyncResourceStatus.ERROR:
            return _classify_error(current.error)[1]
        return None

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        """Per-VM-instance Observable scoped to THIS detail VM. PR
        #103 retirement path — Views subscribing here are immune to
        cross-VM `state` collisions on the shared hub."""
        return self._on_property_changed

    def set_target(self, application_id: str | None, job_run_id: str | None) -> None:
        """Re-point the detail view at a new run and clear the previous run."""
        if self._disposed or (application_id, job_run_id) == (
            self._application_id,
            self._job_run_id,
        ):
            return
        before = self.state
        self._application_id = application_id
        self._job_run_id = job_run_id
        self._summary = None
        # VMx cancel restores the previous baseline. Replacing a non-idle
        # resource discards that baseline and severs an in-flight read's ownership.
        if self._resource.state.status is not AsyncResourceStatus.IDLE:
            self._release_resource()
            self._resource = self._new_resource()
            if self._inner.status is ConstructionStatus.CONSTRUCTED:
                self._resource.construct()
        if self.state != before:
            self._notify("state")
        self._notify("detail")

    def is_terminal_state(self) -> bool:
        detail = self.detail
        return detail is not None and detail.state in _TERMINAL_STATES

    async def refresh(self) -> None:
        if self._disposed or self._application_id is None or self._job_run_id is None:
            return
        resource = self._resource
        cancellation = _ReadCancellation()
        token = _READ_CANCELLATION.set(cancellation)
        try:
            if resource.state.status is AsyncResourceStatus.IDLE:
                await resource.load()
            else:
                await resource.reload()
        finally:
            _READ_CANCELLATION.reset(token)

        # VMx 3.23 settles caller cancellation itself. When its loader
        # cancels independently, VMx returns with the operation still Loading.
        if cancellation.outcome is not None:
            loading, error = cancellation.outcome
            if not self._disposed and resource is self._resource and resource.state is loading:
                resource.cancel()
            raise error

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        self._resource.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._release_resource()
        self._on_property_changed.on_completed()
        self._on_property_changed.dispose()
        self._inner.dispose()

    async def shutdown(self) -> None:
        """Drain provider cleanup after the page has cancelled its refresh calls."""
        while self._provider_tasks:
            await asyncio.wait(tuple(self._provider_tasks))

    # ── Internal ────────────────────────────────────────────────────────────

    def _new_resource(self) -> AsyncResourceVM[JobRunDetail]:
        resource = AsyncResourceVM[JobRunDetail](
            name="emr.job_run_detail.resource",
            loader=self._load_detail,
            hub=NULL_MESSAGE_HUB,
            dispatcher=self._dispatcher,
            retention=AsyncResourceRetention.RETAIN_PREVIOUS,
        )
        self._resource_subscription = resource.property_changed.subscribe(
            on_next=self._on_resource_property_changed
        )
        return resource

    def _release_resource(self) -> None:
        self._resource_subscription.dispose()
        self._reported_failure = None
        self._resource.dispose()

    def _on_resource_property_changed(self, property_name: str) -> None:
        if property_name != "state":
            return
        current = self._resource.state
        if current.status is AsyncResourceStatus.ERROR and not isinstance(
            current.error, ProviderError
        ):
            if current is not self._reported_failure:
                self._reported_failure = current
                report_unexpected_service_error(
                    self._hub,
                    service="emr-serverless",
                    operation="get_job_run",
                    error=current.error,
                )
        elif current.status is not AsyncResourceStatus.LOADING:
            self._reported_failure = None
        self._notify("detail")
        self._notify("state")

    async def _load_detail(self) -> JobRunDetail:
        application_id, job_run_id = self._application_id, self._job_run_id
        if application_id is None or job_run_id is None:
            raise RuntimeError("no detail target")
        resource = self._resource
        loading = resource.state
        # The loader Task inherits its refresh's context when VMx starts it.
        cancellation = _READ_CANCELLATION.get()
        provider = asyncio.create_task(self._client.get_job_run(application_id, job_run_id))
        self._provider_tasks.add(provider)
        provider.add_done_callback(self._provider_done)
        try:
            return await asyncio.shield(provider)
        except asyncio.CancelledError:
            if (
                provider.cancelled()
                and cancellation is not None
                and not self._disposed
                and resource is self._resource
                and resource.state is loading
            ):
                # The finished task also records cancel-and-return, and its
                # result preserves a cancellation message shield can omit.
                try:
                    provider.result()
                except asyncio.CancelledError as error:
                    cancellation.outcome = (loading, error)
            elif not provider.done() and not provider.cancelling():
                # VMx can cancel its loader while the provider is already
                # unwinding a cancellation. Leave that cleanup running.
                provider.cancel()
            raise

    def _provider_done(self, task: asyncio.Task[JobRunDetail]) -> None:
        self._provider_tasks.discard(task)
        if not task.cancelled():
            # Retrieve failures even when the loader left before the provider.
            task.exception()

    def _notify(self, prop: str) -> None:
        """Emit a PropertyChanged event on BOTH the shared hub AND
        the per-VM-instance Observable (round-3 / PR #103 retirement
        path). Mirrors the helper on the other EMR VMs."""
        if self._disposed:
            # The Athena and Glue VMs have always guarded this; these four
            # did not, so a late callback could publish a property change
            # for a disposed view model.
            return
        send_value_free(self._hub, PropertyChangedMessage.create(self, "emr.job_run_detail", prop))
        self._on_property_changed.on_next(prop)


__all__ = ["JobRunDetailVM"]
