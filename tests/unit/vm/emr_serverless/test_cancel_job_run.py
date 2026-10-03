"""Exact-target cancellation through the real page and recording provider."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from vmx import NULL_DISPATCHER, MessageHub, PropertyChangedMessage

from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.domain.emr_serverless import CANCELLABLE_JOB_RUN_STATES, JobRunState
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.chrome.confirm_vm import ConfirmPath, ConfirmRequest
from aws_tui.vm.emr_serverless.page_vm import CancelJobRunResult, EmrServerlessPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_source_vm import ServiceSourceContext


@pytest.fixture
async def page_with_state():
    owners = []

    async def make(state, *, fake=None, detail=True):
        fake = fake or InMemoryEmr()
        fake.add_application(app_id="a1", name="etl")
        fake.add_job_run(application_id="a1", job_run_id="r1", state=state)
        if detail:
            fake.add_job_run_detail(application_id="a1", job_run_id="r1")
        page = EmrServerlessPageVM(
            client=fake,
            logs_client=fake.make_logs_client(),
            hub=MessageHub(),
            dispatcher=NULL_DISPATCHER,
            connection=Connection(
                name="active", profile="profile", region="us-east-1", kind="aws", source="config"
            ),
        )
        page.construct()
        await page.setup()
        fake.calls.clear()
        owners.append((page, fake))
        return page, fake

    yield make
    for page, fake in owners:
        await page.shutdown()
        page.dispose()
        await fake.aclose()


def cancel_calls(fake):
    return [args for name, args in fake.calls if name == "cancel_job_run"]


class RecordingEmr(InMemoryEmr):
    """Separate provider records, request barriers, and later read observations."""

    def __init__(self):
        super().__init__()
        self.request_started = asyncio.Event()
        self.release_request = asyncio.Event()
        self.release_request.set()
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.release_cleanup.set()
        self.cleanup_finished = asyncio.Event()
        self.cancel_error = None
        self.poll_state = None
        self.deny_detail = False

    async def cancel_job_run(self, application_id, job_run_id):
        self.calls.append(("cancel_job_run", (application_id, job_run_id)))
        self.request_started.set()
        try:
            await self.release_request.wait()
            if self.cancel_error is not None:
                raise self.cancel_error
        finally:
            self.cleanup_started.set()
            await self.release_cleanup.wait()
            self.cleanup_finished.set()

    async def get_job_run(self, application_id, job_run_id):
        if self.deny_detail:
            self.calls.append(("get_job_run", (application_id, job_run_id)))
            raise PermissionDeniedError("GetJobRun unavailable")
        detail = await super().get_job_run(application_id, job_run_id)
        if self.poll_state is not None and self.request_started.is_set():
            return replace(
                detail, state=self.poll_state, updated_at=detail.updated_at + timedelta(seconds=1)
            )
        return detail

    async def list_job_runs_page(self, application_id, *, start_token=None, states=None):
        runs, token = await super().list_job_runs_page(
            application_id, start_token=start_token, states=states
        )
        if self.poll_state is not None and self.request_started.is_set():
            runs = [
                replace(
                    run, state=self.poll_state, updated_at=run.updated_at + timedelta(seconds=1)
                )
                for run in runs
            ]
        return runs, token


@pytest.mark.parametrize("state", list(JobRunState))
async def test_cancel_eligibility_for_every_state(page_with_state, state):
    page, fake = await page_with_state(state)
    ask = AsyncMock(return_value=True)
    assert page.can_cancel_selected_run() is (state in CANCELLABLE_JOB_RUN_STATES)
    result = await page.cancel_selected_run(ask)
    if state in CANCELLABLE_JOB_RUN_STATES:
        assert result.status == "requested"
        assert result.message == "cancellation requested"
        assert cancel_calls(fake) == [("a1", "r1")]
        ask.assert_awaited_once()
    else:
        assert result.status == "inert"
        assert result.message
        assert state.value in result.message
        assert cancel_calls(fake) == []
        ask.assert_not_awaited()
    assert page.cancel_busy is False


async def test_confirmation_snapshots_identity_and_dismissal_changes_nothing(page_with_state):
    page, fake = await page_with_state(JobRunState.RUNNING)
    summary = page.job_runs.runs[0]
    detail = page.job_run_detail.detail
    backend_summary = fake._runs["a1"]["r1"]
    backend_detail = fake._details[("a1", "r1")]
    ask = AsyncMock(return_value=False)
    result = await page.cancel_selected_run(ask)
    request = ask.call_args.args[0]
    assert request == ConfirmRequest(
        title="Cancel EMR job run?",
        paths=(
            ConfirmPath("Source", "active · profile · us-east-1"),
            ConfirmPath("Application", "a1"),
            ConfirmPath("Run", "r1"),
        ),
        confirm_label="Request cancellation",
        cancel_label="Keep running",
        danger=True,
    )
    assert result.status == "dismissed"
    assert result.message is None
    assert cancel_calls(fake) == []
    assert page.job_runs.runs[0] is summary
    assert page.job_run_detail.detail is detail
    assert fake._runs["a1"]["r1"] is backend_summary
    assert fake._details[("a1", "r1")] is backend_detail


async def test_acknowledgement_preserves_cached_states_until_refresh(page_with_state):
    page, fake = await page_with_state(JobRunState.RUNNING)
    result = await page.cancel_selected_run(AsyncMock(return_value=True))
    assert result.status == "requested"
    assert page.job_runs.runs[0].state is JobRunState.RUNNING
    assert page.job_run_detail.detail.state is JobRunState.RUNNING
    assert fake._runs["a1"]["r1"].state is JobRunState.CANCELLED
    await page.refresh_job_runs()
    await page.refresh_job_run_detail()
    assert page.job_runs.runs[0].state is JobRunState.CANCELLED
    assert page.job_run_detail.detail.state is JobRunState.CANCELLED
    assert page.job_run_detail.is_terminal_state()
    assert cancel_calls(fake) == [("a1", "r1")]


@pytest.mark.parametrize(
    ("summary_state", "detail_state", "delta", "eligible"),
    [
        (JobRunState.RUNNING, JobRunState.SUCCESS, 1, False),
        (JobRunState.SUCCESS, JobRunState.RUNNING, 1, True),
        (JobRunState.RUNNING, JobRunState.SUCCESS, -1, True),
        (JobRunState.SUCCESS, JobRunState.RUNNING, -1, False),
        (JobRunState.RUNNING, JobRunState.SUCCESS, 0, False),
        (JobRunState.SUCCESS, JobRunState.RUNNING, 0, False),
        (JobRunState.RUNNING, JobRunState.CANCELLING, 0, False),
        (JobRunState.CANCELLED, JobRunState.RUNNING, 0, False),
    ],
)
async def test_latest_matching_observation_controls_eligibility(
    page_with_state, summary_state, detail_state, delta, eligible
):
    page, fake = await page_with_state(summary_state, fake=RecordingEmr())
    original = fake._details[("a1", "r1")]
    fake._details[("a1", "r1")] = replace(
        original, state=detail_state, updated_at=original.updated_at + timedelta(seconds=delta)
    )
    await page.refresh_job_run_detail()
    ask = AsyncMock(return_value=True)
    assert page.can_cancel_selected_run() is eligible
    result = await page.cancel_selected_run(ask)
    assert result.status == ("requested" if eligible else "inert")
    assert cancel_calls(fake) == ([("a1", "r1")] if eligible else [])
    assert ask.await_count == int(eligible)


@pytest.mark.parametrize("identity", ["application_id", "job_run_id"])
async def test_unrelated_detail_never_overrides_summary(page_with_state, identity):
    page, fake = await page_with_state(JobRunState.RUNNING)
    original = fake._details[("a1", "r1")]
    fake._details[("a1", "r1")] = replace(
        original,
        **{identity: "unrelated"},
        state=JobRunState.SUCCESS,
        updated_at=original.updated_at + timedelta(seconds=1),
    )
    await page.refresh_job_run_detail()
    assert page.can_cancel_selected_run()
    result = await page.cancel_selected_run(AsyncMock(return_value=True))
    assert result.status == "requested"
    assert cancel_calls(fake) == [("a1", "r1")]


async def test_missing_detail_does_not_require_get_job_run_permission(page_with_state):
    fake = RecordingEmr()
    fake.deny_detail = True
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake, detail=False)
    assert page.job_run_detail.detail is None
    assert page.job_run_detail.state is PaneState.FORBIDDEN
    pane_error = page.job_run_detail.error_text
    fake.calls.clear()
    assert page.can_cancel_selected_run()
    result = await page.cancel_selected_run(AsyncMock(return_value=True))
    assert result.status == "requested"
    assert list(fake.calls) == [("cancel_job_run", ("a1", "r1"))]
    assert page.job_run_detail.state is PaneState.FORBIDDEN
    assert page.job_run_detail.error_text == pane_error


@pytest.mark.parametrize("missing", ["selection", "visible", "picker_match", "summary_match"])
async def test_missing_or_mismatched_visible_target_is_inert(page_with_state, missing):
    page, fake = await page_with_state(JobRunState.RUNNING)
    if missing == "selection":
        page.job_runs.set_application(None)
    elif missing == "visible":
        page.job_runs.set_state_filter(frozenset({JobRunState.SUCCESS}))
    elif missing == "picker_match":
        fake.add_application(app_id="a2", name="other")
        await page.applications.refresh()
        page.applications.select("a2")
    else:
        fake._runs["a1"]["r1"] = replace(fake._runs["a1"]["r1"], application_id="a2")
        await page.refresh_job_runs()
    ask = AsyncMock(return_value=True)
    assert not page.can_cancel_selected_run()
    result = await page.cancel_selected_run(ask)
    assert result.status == "inert"
    assert result.message
    assert cancel_calls(fake) == []
    ask.assert_not_awaited()


@pytest.mark.parametrize("closed", ["disposed", "shutdown", "owner", "source"])
async def test_closed_owner_or_unmounted_source_is_inert(page_with_state, closed):
    page, fake = await page_with_state(JobRunState.RUNNING)
    if closed == "disposed":
        page.dispose()
    elif closed == "shutdown":
        await page.shutdown()
    elif closed == "owner":
        page._operations.close()
    ask = AsyncMock(return_value=True)
    result = await page.cancel_selected_run(ask, source_is_current=lambda: closed != "source")
    assert result.status == "inert"
    assert result.message
    assert cancel_calls(fake) == []
    ask.assert_not_awaited()
    assert not page.cancel_busy


async def change_target(page, fake, change):
    if change == "application":
        fake.add_application(app_id="a2", name="other")
        fake.add_job_run(application_id="a2", job_run_id="r2", state=JobRunState.RUNNING)
        fake.add_job_run_detail(application_id="a2", job_run_id="r2")
        await page.applications.refresh()
        await page.select_application("a2")
    elif change == "run":
        fake.add_job_run(application_id="a1", job_run_id="r2", state=JobRunState.RUNNING)
        fake.add_job_run_detail(application_id="a1", job_run_id="r2")
        await page.refresh_job_runs()
        await page.select_job_run("r2")
    elif change == "source":
        page._source = ServiceSourceContext("other", "other-profile", "us-west-2")
    elif change == "disappeared":
        del fake._runs["a1"]["r1"]
        await page.refresh_job_runs()
    elif change == "terminal":
        original = fake._details[("a1", "r1")]
        fake._details[("a1", "r1")] = replace(
            original,
            state=JobRunState.SUCCESS,
            updated_at=original.updated_at + timedelta(seconds=1),
        )
        await page.refresh_job_run_detail()


@pytest.mark.parametrize(
    "change", ["application", "run", "source", "guard", "disappeared", "terminal"]
)
async def test_target_change_during_confirmation_sends_no_substitute(page_with_state, change):
    page, fake = await page_with_state(JobRunState.RUNNING)
    asking = asyncio.Event()
    accept = asyncio.Event()
    current = True

    async def ask(request):
        asking.set()
        await accept.wait()
        return True

    task = asyncio.create_task(page.cancel_selected_run(ask, source_is_current=lambda: current))
    await asking.wait()
    try:
        if change == "guard":
            current = False
        else:
            await change_target(page, fake, change)
        accept.set()
        result = await task
        assert result.status == "stale"
        assert result.message == "selected source or job run changed; no cancellation was sent"
        assert cancel_calls(fake) == []
        assert not page.cancel_busy
    finally:
        accept.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_reservation_deduplicates_confirmation_and_request_and_notifies_safely(
    page_with_state, monkeypatch, caplog
):
    fake = RecordingEmr()
    fake.release_request.clear()
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake)
    asking = asyncio.Event()
    accept = asyncio.Event()
    asks = []
    busy_notifications = []
    send = page.hub.send

    def record(message):
        if isinstance(message, PropertyChangedMessage) and message.sender_object is page:
            assert message.property_name == "cancel_busy"
            busy_notifications.append(page.cancel_busy)
        send(message)

    monkeypatch.setattr(page.hub, "send", record)

    def faulty_observer(message):
        if isinstance(message, PropertyChangedMessage) and message.sender_object is page:
            raise RuntimeError("planted-subscriber-secret")

    subscription = page.hub.messages.subscribe(on_next=faulty_observer)

    async def ask(request):
        asks.append(request)
        asking.set()
        await accept.wait()
        return True

    task = asyncio.create_task(page.cancel_selected_run(ask))
    await asking.wait()
    try:
        assert page.cancel_busy
        assert not page.can_cancel_selected_run()
        duplicates = await asyncio.gather(*(page.cancel_selected_run(ask) for _ in range(5)))
        assert all(result.status == "busy" and result.message is None for result in duplicates)
        accept.set()
        await fake.request_started.wait()
        assert page.cancel_busy
        duplicates = await asyncio.gather(*(page.cancel_selected_run(ask) for _ in range(5)))
        assert all(result.status == "busy" and result.message is None for result in duplicates)
        assert len(asks) == 1
        assert cancel_calls(fake) == [("a1", "r1")]
        fake.release_request.set()
        assert (await task).status == "requested"
        assert not page.cancel_busy
        assert busy_notifications == [True, False]
        assert "planted-subscriber-secret" not in caplog.text
    finally:
        subscription.dispose()
        accept.set()
        fake.release_request.set()
        await asyncio.gather(task, return_exceptions=True)


ERROR_CASES = [
    (
        PermissionDeniedError,
        "denied",
        "CancelJobRun permission denied; check emr-serverless:CancelJobRun permission",
    ),
    (
        NotFoundError,
        "not_found",
        "application or job run was not found; refresh the selected job run",
    ),
    (
        ThrottledError,
        "throttled",
        "EMR Serverless throttled the cancellation request; wait before a deliberate retry",
    ),
    (
        ProviderUnreachableError,
        "unreachable",
        "cancellation outcome is unconfirmed because of a network failure; refresh job state before a deliberate retry",
    ),
    (
        AuthRequiredError,
        "auth_required",
        "authentication is required to request cancellation; refresh credentials before a deliberate retry",
    ),
    (
        ValidationError,
        "invalid",
        "EMR Serverless rejected the cancellation request; refresh job state before a deliberate retry",
    ),
    (
        ProviderError,
        "error",
        "cancellation request failed; refresh job state before a deliberate retry",
    ),
    (
        RuntimeError,
        "error",
        "cancellation request failed; refresh job state before a deliberate retry",
    ),
]


@pytest.mark.parametrize(("error_type", "status", "message"), ERROR_CASES)
async def test_safe_category_specific_errors_do_not_retry_or_reset_panes(
    page_with_state, error_type, status, message, caplog, monkeypatch
):
    fake = RecordingEmr()
    fake.cancel_error = error_type("planted-secret-AKIA-not-for-display")
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake)
    page.job_run_detail._error_text = "existing detail context"
    snapshot = (
        page.job_runs.runs,
        page.job_run_detail.detail,
        page.job_run_detail.state,
        page.job_run_logs.state,
    )
    messages = []
    send = page.hub.send

    def record(event):
        messages.append(event)
        send(event)

    monkeypatch.setattr(page.hub, "send", record)
    result = await page.cancel_selected_run(AsyncMock(return_value=True))
    assert result.status == status
    assert result.message == message
    assert "planted-secret" not in repr(result) + caplog.text + repr(messages)
    assert cancel_calls(fake) == [("a1", "r1")]
    assert snapshot == (
        page.job_runs.runs,
        page.job_run_detail.detail,
        page.job_run_detail.state,
        page.job_run_logs.state,
    )
    assert page.job_run_detail.error_text == "existing detail context"
    assert all(
        isinstance(event, PropertyChangedMessage) and event.sender_object is page
        for event in messages
    )
    assert not page.cancel_busy


@pytest.mark.parametrize("change", ["application", "run", "source", "guard", "disappeared"])
@pytest.mark.parametrize("error_type", [None, *[case[0] for case in ERROR_CASES]])
async def test_sent_request_outcomes_are_silent_when_target_changes(
    page_with_state, change, error_type
):
    fake = RecordingEmr()
    fake.release_request.clear()
    fake.cancel_error = error_type("planted-secret") if error_type else None
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake)
    current = True
    task = asyncio.create_task(
        page.cancel_selected_run(AsyncMock(return_value=True), source_is_current=lambda: current)
    )
    await fake.request_started.wait()
    try:
        if change == "guard":
            current = False
        else:
            await change_target(page, fake, change)
        snapshot = (
            page.job_runs.runs,
            page.job_run_detail.detail,
            page.job_run_detail.state,
            page.job_run_logs.state,
        )
        fake.release_request.set()
        result = await task
        assert result.status == "superseded"
        assert result.message is None
        assert snapshot == (
            page.job_runs.runs,
            page.job_run_detail.detail,
            page.job_run_detail.state,
            page.job_run_logs.state,
        )
        assert cancel_calls(fake) == [("a1", "r1")]
        assert not page.cancel_busy
    finally:
        fake.release_request.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_polling_observes_cancelling_then_cancelled_without_new_request(page_with_state):
    fake = RecordingEmr()
    fake.poll_state = JobRunState.CANCELLING
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake)
    result = await page.cancel_selected_run(AsyncMock(return_value=True))
    assert result.status == "requested"
    assert page.job_runs.runs[0].state is JobRunState.RUNNING
    assert page.job_run_detail.detail.state is JobRunState.RUNNING
    await page.refresh_job_runs()
    await page.refresh_job_run_detail()
    assert page.job_runs.runs[0].state is JobRunState.CANCELLING
    assert page.job_run_detail.detail.state is JobRunState.CANCELLING
    assert not page.job_run_detail.is_terminal_state()
    assert not page.can_cancel_selected_run()
    ask = AsyncMock(return_value=True)
    assert (await page.cancel_selected_run(ask)).status == "inert"
    ask.assert_not_awaited()
    fake.poll_state = JobRunState.CANCELLED
    await page.refresh_job_runs()
    await page.refresh_job_run_detail()
    assert page.job_runs.runs[0].state is JobRunState.CANCELLED
    assert page.job_run_detail.detail.state is JobRunState.CANCELLED
    assert page.job_run_detail.is_terminal_state()
    assert cancel_calls(fake) == [("a1", "r1")]


@pytest.mark.parametrize("phase", ["confirmation", "request"])
@pytest.mark.parametrize("stop", ["caller", "shutdown", "dispose"])
async def test_lifecycle_owns_waits_and_releases_busy_after_cleanup(
    page_with_state, phase, stop, monkeypatch
):
    fake = RecordingEmr()
    fake.release_request.clear()
    fake.release_cleanup.clear()
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake)
    asking = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    drain_started = asyncio.Event()
    drain = page._operations.cancel_and_drain

    async def observed_drain():
        drain_started.set()
        await drain()

    monkeypatch.setattr(page._operations, "cancel_and_drain", observed_drain)

    async def ask(request):
        asking.set()
        if phase == "request":
            return True
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()

    task = asyncio.create_task(page.cancel_selected_run(ask))
    await (asking if phase == "confirmation" else fake.request_started).wait()
    shutdown = None
    try:
        assert len(page._operations.tasks) == 1
        assert page.cancel_busy
        if stop == "caller":
            task.cancel()
        else:
            if stop == "dispose":
                page.dispose()
            shutdown = asyncio.create_task(page.shutdown())
        await (cleanup_started if phase == "confirmation" else fake.cleanup_started).wait()
        if shutdown is not None:
            await drain_started.wait()
            assert not shutdown.done()
        assert not task.done()
        assert page.cancel_busy
        release_cleanup.set()
        fake.release_cleanup.set()
        if stop == "caller":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await shutdown
            result = await task
            assert result.status == "superseded"
            assert result.message is None
        assert (cleanup_finished if phase == "confirmation" else fake.cleanup_finished).is_set()
        assert not page.cancel_busy
        assert not page._operations.tasks
        assert cancel_calls(fake) == ([] if phase == "confirmation" else [("a1", "r1")])
    finally:
        release_cleanup.set()
        fake.release_cleanup.set()
        fake.release_request.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, *([shutdown] if shutdown else []), return_exceptions=True)


async def test_cancelled_shutdown_durably_drains_request_cleanup(page_with_state):
    fake = RecordingEmr()
    fake.release_request.clear()
    fake.release_cleanup.clear()
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake)
    task = asyncio.create_task(page.cancel_selected_run(AsyncMock(return_value=True)))
    await fake.request_started.wait()
    shutdown = None
    try:
        assert len(page._operations.tasks) == 1
        shutdown = asyncio.create_task(page.shutdown())
        await fake.cleanup_started.wait()
        shutdown.cancel()
        assert not shutdown.done()
        assert not fake.cleanup_finished.is_set()
        assert page.cancel_busy
        fake.release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        assert fake.cleanup_finished.is_set()
        assert (await task).status == "superseded"
        assert not page.cancel_busy
        assert not page._operations.tasks
    finally:
        fake.release_cleanup.set()
        fake.release_request.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, *([shutdown] if shutdown else []), return_exceptions=True)


@pytest.mark.parametrize("state", [JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED])
async def test_independently_observed_terminal_state_is_preserved_after_acknowledgement(
    page_with_state, state
):
    fake = RecordingEmr()
    fake.release_request.clear()
    page, fake = await page_with_state(JobRunState.RUNNING, fake=fake)
    task = asyncio.create_task(page.cancel_selected_run(AsyncMock(return_value=True)))
    await fake.request_started.wait()
    try:
        fake.poll_state = state
        await page.refresh_job_runs()
        await page.refresh_job_run_detail()
        fake.release_request.set()
        result = await task
        assert result.status == "requested"
        assert page.job_runs.runs[0].state is state
        assert page.job_run_detail.detail.state is state
        assert page.job_run_detail.is_terminal_state()
        assert cancel_calls(fake) == [("a1", "r1")]
    finally:
        fake.release_request.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_public_result_is_immutable_and_busy_is_read_only(page_with_state):
    page, _fake = await page_with_state(JobRunState.RUNNING)
    result = await page.cancel_selected_run(AsyncMock(return_value=False))
    assert isinstance(result, CancelJobRunResult)
    with pytest.raises(FrozenInstanceError):
        result.status = "requested"
    with pytest.raises(AttributeError):
        page.cancel_busy = True
