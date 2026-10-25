from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aws_tui.domain.emr_dashboard import DashboardLink
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.vm.emr_serverless.dashboard_vm import DashboardTarget, DashboardVM
from aws_tui.vm.service_source_vm import ServiceSourceContext

URL = "https://private-host.amazonaws.com/private-path?private-query=secret#private-fragment"


def target():
    return DashboardTarget(
        ServiceSourceContext("prod", "prod", "us-east-1", True), "app1", "run1", "etl", "SPARK", 1
    )


def setup_vm(**kwargs):
    current = [target()]
    client = SimpleNamespace(get_dashboard_for_job_run=AsyncMock(return_value=DashboardLink(URL)))
    vm = DashboardVM(client=client, target=lambda: current[0], **kwargs)
    browser = Mock(open=Mock(return_value=True))
    copy = AsyncMock()
    return vm, current, client, browser, copy


async def perform(vm, current, browser, copy, **kwargs):
    return await vm.perform(
        current[0], mode=kwargs.pop("mode", "open"), browser=browser, copy_link=copy, **kwargs
    )


async def test_open_fresh_each_time_exact_options_and_no_url_state(caplog):
    vm, current, client, browser, copy = setup_vm()
    for options in ({}, {"attempt": 2, "access_system_profile_logs": True}):
        result = await perform(vm, current, browser, copy, **options)
        assert result.status == "opened"
        assert "request" in result.message.lower()
    assert client.get_dashboard_for_job_run.await_count == 2
    assert client.get_dashboard_for_job_run.await_args_list[0].kwargs == {
        "attempt": None,
        "access_system_profile_logs": False,
    }
    assert client.get_dashboard_for_job_run.await_args_list[1].kwargs == {
        "attempt": 2,
        "access_system_profile_logs": True,
    }
    browser.open.assert_called_with(URL)
    copy.assert_not_awaited()
    assert not vm.busy
    assert URL not in repr(vars(vm)) + repr(result) + str(asdict(result)) + caplog.text
    await vm.aclose()
    assert vm.outcome == result


@pytest.mark.parametrize(
    ("mode", "browser_result"), [("copy", True), ("open", False), ("open", RuntimeError(URL))]
)
async def test_copy_and_browser_fallback_report_request_only(mode, browser_result):
    vm, current, _client, browser, copy = setup_vm()
    if isinstance(browser_result, Exception):
        browser.open.side_effect = browser_result
    else:
        browser.open.return_value = browser_result
    result = await perform(vm, current, browser, copy, mode=mode)
    assert result.status == "copy_requested"
    assert "check" in result.message.lower()
    assert "copied" not in result.message.lower()
    copy.assert_awaited_once_with(URL)
    assert browser.open.call_count == (mode == "open")
    await vm.aclose()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AuthRequiredError, "auth_required"),
        (PermissionDeniedError, "denied"),
        (NotFoundError, "missing"),
        (ValidationError, "invalid"),
        (ThrottledError, "throttled"),
        (ProviderUnreachableError, "unreachable"),
        (RuntimeError, "error"),
    ],
)
async def test_errors_are_fixed_and_private(error, status, caplog):
    vm, current, client, browser, copy = setup_vm()
    client.get_dashboard_for_job_run.side_effect = error(URL)
    result = await perform(vm, current, browser, copy)
    assert result.status == status
    exposed = repr(result) + str(asdict(result)) + repr(vars(vm)) + caplog.text
    for secret in (URL, "private-host", "private-path", "private-query", "private-fragment"):
        assert secret not in exposed
    browser.open.assert_not_called()
    copy.assert_not_awaited()


@pytest.mark.parametrize("response", [None, object(), SimpleNamespace(url=URL), "corrupt-link"])
async def test_malformed_provider_reply_is_invalid(response):
    vm, current, client, browser, copy = setup_vm()
    if response == "corrupt-link":
        response = DashboardLink(URL)
        object.__setattr__(response, "url", "https://evil.test/x")
    client.get_dashboard_for_job_run.return_value = response
    assert (await perform(vm, current, browser, copy)).status == "invalid"
    browser.open.assert_not_called()
    copy.assert_not_awaited()


@pytest.mark.parametrize(
    "options",
    [
        {"attempt": True},
        {"attempt": 0},
        {"attempt": 1.0},
        {"access_system_profile_logs": 1},
        {"mode": "other"},
    ],
)
async def test_bad_options_rejected_before_provider(options):
    vm, current, client, browser, copy = setup_vm()
    assert (await perform(vm, current, browser, copy, **options)).status == "invalid"
    client.get_dashboard_for_job_run.assert_not_awaited()


async def test_stale_review_and_ineligible_rejected_before_provider():
    vm, current, client, browser, copy = setup_vm()
    reviewed = current[0]
    current[0] = replace(reviewed, revision=3)
    assert not vm.available(reviewed)
    assert (
        await vm.perform(reviewed, mode="open", browser=browser, copy_link=copy)
    ).status == "stale"
    current[0] = replace(current[0], application_type="FLINK")
    assert (await perform(vm, current, browser, copy)).status == "invalid"
    client.get_dashboard_for_job_run.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "region",
        "application_id",
        "job_run_id",
        "revision",
        "ownership",
        "cancel",
        "invalidate",
        "close",
    ],
)
async def test_pending_provider_ignoring_cancel_cannot_deliver(change):
    alive = [True]
    vm, current, client, browser, copy = setup_vm(is_current=lambda: alive[0])
    started, release = asyncio.Event(), asyncio.Event()
    cancelled = asyncio.Event()

    async def pending(*args, **kwargs):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return DashboardLink(URL)

    client.get_dashboard_for_job_run.side_effect = pending
    task = asyncio.create_task(perform(vm, current, browser, copy))
    await started.wait()
    assert vm.busy
    duplicate = await perform(vm, current, browser, copy)
    assert duplicate.status == "busy"
    assert vm.busy
    close = None
    if change == "source":
        current[0] = replace(current[0], source=replace(current[0].source, connection_name="other"))
    elif change == "region":
        current[0] = replace(current[0], source=replace(current[0].source, region="eu-west-1"))
    elif change in {"application_id", "job_run_id", "revision"}:
        current[0] = replace(current[0], **{change: 3 if change == "revision" else "different"})
    elif change == "ownership":
        alive[0] = False
    elif change == "close":
        close = asyncio.create_task(vm.aclose())
        await cancelled.wait()
        assert not close.done()
    else:
        getattr(vm, change)()
        await cancelled.wait()
    release.set()
    assert (await task).status in {"cancelled", "stale"}
    if close:
        await close
    assert not vm.busy
    browser.open.assert_not_called()
    copy.assert_not_awaited()


@pytest.mark.parametrize("browser_result", [True, False])
async def test_cancel_after_browser_admission_drains_actual_result_without_fallback(browser_result):
    vm, current, _client, browser, copy = setup_vm()
    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), threading.Event()

    def blocking_open(url):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return browser_result

    browser.open.side_effect = blocking_open
    task = asyncio.create_task(perform(vm, current, browser, copy))
    await started.wait()
    close = asyncio.create_task(vm.aclose())
    await asyncio.sleep(0)
    assert not close.done()
    assert vm.busy
    release.set()
    result = await task
    await close
    assert result.status == ("opened" if browser_result else "cancelled")
    assert vm.outcome == result
    copy.assert_not_awaited()


async def test_cancel_before_thread_admission_prevents_browser(monkeypatch):
    vm, current, _client, browser, copy = setup_vm()
    started, release = asyncio.Event(), asyncio.Event()
    original = asyncio.to_thread

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", delayed)
    task = asyncio.create_task(perform(vm, current, browser, copy))
    await started.wait()
    vm.cancel()
    release.set()
    assert (await task).status == "cancelled"
    browser.open.assert_not_called()
    copy.assert_not_awaited()


async def test_idle_cancel_and_reentrant_notify_preserve_actual_result():
    vm, current, _client, browser, copy = setup_vm()
    notifications = []

    def notify():
        notifications.append(vm.busy)
        if not vm.busy:
            vm.cancel()

    vm._notify = notify
    result = await perform(vm, current, browser, copy)
    vm.cancel()
    await vm.aclose()
    assert result.status == "opened"
    assert vm.outcome == result
    assert notifications == [True, False]


async def test_caller_cancellation_drains_owned_provider():
    vm, current, client, browser, copy = setup_vm()
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def pending(*args, **kwargs):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return DashboardLink(URL)

    client.get_dashboard_for_job_run.side_effect = pending
    task = asyncio.create_task(perform(vm, current, browser, copy))
    await started.wait()
    task.cancel()
    await cancelled.wait()
    assert not task.done()
    release.set()
    assert (await task).status == "cancelled"
    assert not vm.busy
    browser.open.assert_not_called()


async def test_copy_admission_drains_and_reports_request_on_close():
    vm, current, _client, browser, copy = setup_vm()
    started, release = asyncio.Event(), asyncio.Event()

    async def deliver(url):
        started.set()
        await release.wait()

    copy.side_effect = deliver
    task = asyncio.create_task(perform(vm, current, browser, copy, mode="copy"))
    await started.wait()
    close = asyncio.create_task(vm.aclose())
    await asyncio.sleep(0)
    assert vm.busy
    assert not close.done()
    release.set()
    result = await task
    await close
    assert result.status == "copy_requested"
    assert vm.outcome == result
    assert not vm.busy


async def test_copy_failure_never_exposes_payload(caplog):
    vm, current, _client, browser, copy = setup_vm()
    copy.side_effect = RuntimeError(URL)
    result = await perform(vm, current, browser, copy, mode="copy")
    assert result.status == "error"
    assert URL not in repr(result) + repr(vars(vm)) + caplog.text


async def test_queued_thread_rechecks_ownership_on_event_loop(monkeypatch):
    vm, current, _client, browser, copy = setup_vm()
    started, release = asyncio.Event(), asyncio.Event()
    original = asyncio.to_thread
    loop_thread = threading.get_ident()

    def get_target():
        assert threading.get_ident() == loop_thread
        return current[0]

    vm._target = get_target

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", delayed)
    reviewed = current[0]
    task = asyncio.create_task(perform(vm, current, browser, copy))
    await started.wait()
    current[0] = replace(reviewed, revision=3)  # A→B→A keeps identity, changes revision.
    release.set()
    result = await task
    assert result.status == "stale"
    assert result.target == reviewed
    browser.open.assert_not_called()
    copy.assert_not_awaited()


async def test_reentrant_busy_cancel_never_calls_provider():
    vm, current, client, browser, copy = setup_vm()
    vm._notify = vm.cancel
    result = await perform(vm, current, browser, copy)
    assert result.status == "cancelled"
    assert not vm.busy
    client.get_dashboard_for_job_run.assert_not_awaited()


async def test_hive_supported_and_close_fences_future_requests():
    vm, current, client, browser, copy = setup_vm()
    current[0] = replace(current[0], application_type="HIVE")
    assert vm.available(current[0])
    result = await perform(vm, current, browser, copy)
    assert result.status == "opened"
    await vm.aclose()
    assert not vm.available(current[0])
    assert (await perform(vm, current, browser, copy)).status == "stale"
    assert client.get_dashboard_for_job_run.await_count == 1
    assert vm.outcome == result
