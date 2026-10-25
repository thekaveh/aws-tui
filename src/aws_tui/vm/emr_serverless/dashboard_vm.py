"""Source-bound, single-flight dashboard delivery without retained URLs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock
from typing import Literal

from aws_tui.domain.emr_dashboard import DashboardLink, validate_dashboard_url
from aws_tui.domain.emr_serverless import EmrServerlessClientProtocol
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.infra.browser import BrowserPort
from aws_tui.vm.service_source_vm import ServiceSourceContext


@dataclass(frozen=True, slots=True)
class DashboardTarget:
    source: ServiceSourceContext
    application_id: str
    job_run_id: str
    application_name: str
    application_type: str
    revision: int


@dataclass(frozen=True, slots=True)
class DashboardOutcome:
    status: str
    message: str
    target: DashboardTarget | None = None


_MESSAGES = {
    "opened": "Browser open request sent for the reviewed run.",
    "copy_requested": "Link delivery requested; check the clipboard outcome.",
    "cancelled": "Dashboard request cancelled.",
    "stale": "The reviewed run is no longer current. Review the selected run again.",
    "busy": "A dashboard request is already in progress.",
    "invalid": "Dashboard request or link is invalid. Review the run and options.",
    "auth_required": "Sign in to the selected AWS connection and retry.",
    "denied": "Dashboard access denied. Check emr-serverless:GetDashboardForJobRun permission.",
    "missing": "The application or job run was not found. Refresh and select it again.",
    "throttled": "AWS throttled the dashboard request. Wait and retry.",
    "unreachable": "AWS could not be reached. Check the connection and retry.",
    "error": "Dashboard delivery failed. Retry the reviewed request.",
}


def _outcome(status: str, target: DashboardTarget) -> DashboardOutcome:
    return DashboardOutcome(status, _MESSAGES[status], target)


def _error_status(error: Exception) -> str:
    for kind, status in (
        (AuthRequiredError, "auth_required"),
        (PermissionDeniedError, "denied"),
        (NotFoundError, "missing"),
        (ValidationError, "invalid"),
        (ThrottledError, "throttled"),
        (ProviderUnreachableError, "unreachable"),
    ):
        if isinstance(error, kind):
            return status
    return "error"


class _DispatchPermit:
    """Linearize cancellation with admission; never hold the lock over I/O."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def admit(self) -> bool:
        with self._lock:
            return not self._cancelled


class DashboardVM:
    def __init__(
        self,
        *,
        client: EmrServerlessClientProtocol,
        target: Callable[[], DashboardTarget | None],
        is_current: Callable[[], bool] = lambda: True,
        notify: Callable[[], None] = lambda: None,
    ) -> None:
        self._client = client
        self._target = target
        self._is_current = is_current
        self._notify = notify
        self._closed = False
        self._task: asyncio.Task[DashboardOutcome] | None = None
        self._provider: asyncio.Task[DashboardLink] | None = None
        self._permit: _DispatchPermit | None = None
        self._outcome: DashboardOutcome | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None

    @property
    def status(self) -> str:
        if self.busy:
            return "busy"
        return self._outcome.status if self._outcome else "idle"

    @property
    def outcome(self) -> DashboardOutcome | None:
        return self._outcome

    def available(self, target: DashboardTarget) -> bool:
        return (
            not self._closed
            and self._is_current()
            and self._target() == target
            and target.application_type in {"SPARK", "HIVE"}
            and bool(target.application_id)
            and bool(target.job_run_id)
        )

    async def perform(
        self,
        target: DashboardTarget,
        *,
        mode: Literal["open", "copy"],
        attempt: int | None = None,
        access_system_profile_logs: bool = False,
        browser: BrowserPort,
        copy_link: Callable[[str], Awaitable[None]],
    ) -> DashboardOutcome:
        if self.busy:
            return _outcome("busy", target)
        if (
            mode not in {"open", "copy"}
            or (attempt is not None and (type(attempt) is not int or attempt < 1))
            or type(access_system_profile_logs) is not bool
            or target.application_type not in {"SPARK", "HIVE"}
            or not target.application_id
            or not target.job_run_id
        ):
            return _outcome("invalid", target)
        if not self.available(target):
            return _outcome("stale", target)
        permit = _DispatchPermit()
        self._permit = permit
        task = asyncio.create_task(
            self._run(target, mode, attempt, access_system_profile_logs, browser, copy_link, permit)
        )
        self._task = task
        self._outcome = None
        self._notify()
        return await self._drain(task)

    def cancel(self) -> None:
        if self._permit is not None:
            self._permit.cancel()
        # Cancel retrieval, but never detach a running browser thread or an
        # admitted clipboard pipeline. They must report their actual outcome.
        if self._provider is not None and not self._provider.done():
            self._provider.cancel()

    def invalidate(self) -> None:
        self.cancel()

    async def aclose(self) -> None:
        self._closed = True
        self.cancel()
        if self._task is not None:
            await self._drain(self._task)

    async def _drain(self, task: asyncio.Task[DashboardOutcome]) -> DashboardOutcome:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                self.cancel()

    def _blocked(self, target: DashboardTarget, permit: _DispatchPermit) -> str | None:
        if permit.cancelled:
            return "cancelled"
        if not self.available(target):
            return "stale"
        return None

    async def _run(
        self,
        target: DashboardTarget,
        mode: Literal["open", "copy"],
        attempt: int | None,
        access_system_profile_logs: bool,
        browser: BrowserPort,
        copy_link: Callable[[str], Awaitable[None]],
        permit: _DispatchPermit,
    ) -> DashboardOutcome:
        url = ""
        link: DashboardLink | None = None
        result = _outcome("cancelled", target)
        try:
            blocked = self._blocked(target, permit)
            if blocked:
                result = _outcome(blocked, target)
                return result
            self._provider = asyncio.create_task(
                self._client.get_dashboard_for_job_run(
                    target.application_id,
                    target.job_run_id,
                    attempt=attempt,
                    access_system_profile_logs=access_system_profile_logs,
                )
            )
            try:
                link = await self._provider
            finally:
                self._provider = None
            blocked = self._blocked(target, permit)
            if blocked:
                result = _outcome(blocked, target)
                return result
            if not isinstance(link, DashboardLink):
                result = _outcome("invalid", target)
                return result
            url = validate_dashboard_url(link.url)
            link = None
            if mode == "open":
                opened = await asyncio.to_thread(
                    self._dispatch, asyncio.get_running_loop(), target, permit, browser, url
                )
                if opened:
                    result = _outcome("opened", target)
                    return result
            blocked = self._blocked(target, permit)
            if blocked:
                result = _outcome(blocked, target)
                return result
            # No suspension between this ownership check and invoking the
            # callback. Once admitted, drain its delivery even on cancellation.
            await copy_link(url)
            result = _outcome("copy_requested", target)
            return result
        except asyncio.CancelledError:
            return result
        except Exception as error:
            result = _outcome(_error_status(error), target)
            return result
        finally:
            url = ""
            link = None
            self._provider = None
            self._permit = None
            self._task = None
            self._outcome = result
            # Clear owners before publishing idle: a subscriber may cancel or
            # close reentrantly, and must not clobber the completed outcome.
            self._notify()

    def _dispatch(
        self,
        loop: asyncio.AbstractEventLoop,
        target: DashboardTarget,
        permit: _DispatchPermit,
        browser: BrowserPort,
        url: str,
    ) -> bool:
        admission: Future[bool] = Future()

        def admit_on_loop() -> None:
            # Target callbacks belong to the UI loop. Recheck here, after the
            # executor queue, immediately before granting thread admission.
            try:
                allowed = self._blocked(target, permit) is None and permit.admit()
            except Exception:
                allowed = False
            admission.set_result(allowed)

        try:
            loop.call_soon_threadsafe(admit_on_loop)
            if not admission.result():
                return False
            try:
                return browser.open(url) is True
            except Exception:
                return False
        finally:
            url = ""


__all__ = ["DashboardOutcome", "DashboardTarget", "DashboardVM"]
