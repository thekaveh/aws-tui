"""JobRunDetailVM tests — target tracking + refresh contract."""

from __future__ import annotations

import asyncio

import pytest
from vmx import NULL_DISPATCHER, Message, MessageHub

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState


def _make() -> tuple[JobRunDetailVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunDetailVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, fake


@pytest.mark.parametrize("foreign_target", [("a1", "other"), ("other", "r1"), None])
def test_summary_fallback_is_owned_by_the_exact_current_target(foreign_target) -> None:
    vm, fake = _make()
    summary = fake.add_job_run(application_id="a1", job_run_id="r1", name="current")
    vm.set_target("a1", "r1")
    vm.set_summary(summary)
    assert vm.summary is summary
    if foreign_target is not None:
        foreign = fake.add_job_run(application_id=foreign_target[0], job_run_id=foreign_target[1])
    else:
        foreign = None
    vm.set_summary(foreign)
    assert vm.summary is None
    vm.set_summary(summary)
    vm.set_target("a1", "other")
    assert vm.summary is None
    vm.set_summary(summary)
    assert vm.summary is None
    vm.set_target(None, None)
    assert vm.summary is None
    vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["detail", "provider-error", "unexpected-error"])
@pytest.mark.parametrize("switch_target", [False, True])
async def test_newer_run_read_drops_its_older_inflight_response(
    outcome: str, switch_target: bool
) -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    old = fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="old.py")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    current = fake.add_job_run_detail(
        application_id="a1", job_run_id="r1", entry_point="current.py"
    )
    started = asyncio.Event()
    released = asyncio.Event()
    calls = 0

    async def read(_app_id, _run_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await released.wait()
            if outcome == "provider-error":
                raise ProviderError("old read failed")
            if outcome == "unexpected-error":
                raise RuntimeError("old client failed")
            return old
        return current

    fake.get_job_run = read
    vm.set_target("a1", "r1")
    pending = asyncio.create_task(vm.refresh())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        if switch_target:
            vm.set_target("a1", "r2")
            vm.set_target("a1", "r1")
        await vm.refresh()
        assert vm.detail is current
        released.set()
        await pending
        assert vm.detail is current
        assert vm.state is PaneState.IDLE
        assert vm.error_text is None
        assert vm.is_terminal_state()
    finally:
        released.set()
        await pending
        vm.dispose()


@pytest.mark.asyncio
async def test_refresh_with_target_loads_detail() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert vm.detail is not None
    assert vm.detail.entry_point == "s3://b/x.py"
    assert vm.state is PaneState.IDLE


@pytest.mark.asyncio
async def test_set_target_to_none_clears_detail() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    vm.set_target(None, None)
    assert vm.detail is None
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_is_terminal_state_returns_true_on_success() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert vm.is_terminal_state()


@pytest.mark.asyncio
async def test_is_terminal_state_returns_false_on_running() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert not vm.is_terminal_state()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [ProviderError("access denied"), RuntimeError("unexpected client failure")],
)
async def test_a_target_switch_mid_flight_drops_the_previous_runs_error(
    failure: Exception,
) -> None:
    """Run A's slow failure must not be written into run B's pane.

    ``refresh()`` captures the target before awaiting ``get_job_run`` and
    re-checks it in all three exits. The SUCCESS exit is pinned by an existing
    test; neither error exit was. Dropping the target comparison from either
    ``except`` arm survived the whole repo suite, while a slow "permission
    denied" for the run the user just navigated away from landed in the pane as
    the CURRENT run's error.
    """
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    started = asyncio.Event()
    released = asyncio.Event()

    async def slow_failure(_app_id: str, _run_id: str) -> object:
        started.set()
        await released.wait()
        raise failure

    fake.get_job_run = slow_failure  # type: ignore[method-assign]
    vm.set_target("a1", "r1")
    refresh_task = asyncio.create_task(vm.refresh())

    await asyncio.wait_for(started.wait(), timeout=2)
    vm.set_target("a1", "r2")  # user navigates away while r1 is still in flight
    released.set()
    await asyncio.wait_for(refresh_task, timeout=2)

    assert vm.error_text is None, "r1's error was applied to r2's pane"
    assert vm.state is not PaneState.ERROR


@pytest.mark.asyncio
async def test_failed_poll_keeps_the_loaded_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    from aws_tui.domain.emr_serverless import JobRunDetail
    from aws_tui.domain.filesystem import ProviderUnreachableError

    vm, fake = _make()
    summary = fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    original = fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    vm.set_summary(summary)
    try:
        await vm.refresh()
        assert vm.detail is original

        async def fail_poll(app_id: str, run_id: str) -> JobRunDetail:
            assert (app_id, run_id) == ("a1", "r1")
            raise ProviderUnreachableError("network blip")

        with monkeypatch.context() as patch:
            patch.setattr(fake, "get_job_run", fail_poll)
            await vm.refresh()

        assert vm.detail is original
        assert vm.summary is summary
        assert vm.is_terminal_state()
        assert vm.state is PaneState.UNREACHABLE
        assert vm.error_text == "network blip"

        current = fake.add_job_run_detail(
            application_id="a1", job_run_id="r1", entry_point="new.py"
        )
        await vm.refresh()
        assert vm.detail is current
        assert vm.state is PaneState.IDLE
        assert vm.error_text is None
    finally:
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_failure", [False, True])
async def test_retarget_during_a_reload_clears_the_detail(
    monkeypatch: pytest.MonkeyPatch, late_failure: bool
) -> None:
    from aws_tui.domain.emr_serverless import JobRunDetail
    from aws_tui.vm.service_diagnostics import capture_service_diagnostics

    vm, fake = _make()
    summary = fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    original = fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    fake.add_job_run(application_id="a1", job_run_id="r2")
    current = fake.add_job_run_detail(application_id="a1", job_run_id="r2")
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    vm.set_target("a1", "r1")
    vm.set_summary(summary)
    await vm.refresh()
    late = fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="late.py")

    async def read(app_id: str, run_id: str) -> JobRunDetail:
        if (app_id, run_id) == ("a1", "r2"):
            return current
        assert (app_id, run_id) == ("a1", "r1")
        started.set()
        try:
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            if late_failure:
                raise RuntimeError("late read failed")
            return late
        finally:
            finished.set()

    monkeypatch.setattr(fake, "get_job_run", read)
    with capture_service_diagnostics() as diagnostics:
        reload_task = asyncio.create_task(vm.refresh())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            assert vm.state is PaneState.LOADING
            assert vm.detail is original
            assert vm.is_terminal_state()

            vm.set_target("a1", "r2")
            assert vm.detail is None
            assert vm.summary is None
            assert vm.error_text is None
            assert vm.state is PaneState.LOADING
            assert not vm.is_terminal_state()
            await vm.refresh()
            assert cancelled.is_set()
            assert not finished.is_set()

            release.set()
            await asyncio.wait_for(finished.wait(), timeout=1)
            await asyncio.wait_for(reload_task, timeout=1)
            assert vm.detail is current
            assert vm.state is PaneState.IDLE
            assert vm.error_text is None
            assert diagnostics == []
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=1)
            await asyncio.wait_for(reload_task, timeout=1)
            vm.dispose()


@pytest.mark.asyncio
async def test_unexpected_failure_reports_once_after_a_cancelled_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui.domain.emr_serverless import JobRunDetail
    from aws_tui.vm.service_diagnostics import (
        CapturedServiceDiagnostic,
        capture_service_diagnostics,
    )

    vm, fake = _make()
    vm.set_target("a1", "r1")
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def fail(_app_id: str, _run_id: str) -> JobRunDetail:
        raise RuntimeError("unexpected client failure")

    async def retry(_app_id: str, _run_id: str) -> JobRunDetail:
        started.set()
        try:
            await release.wait()
            raise AssertionError("retry should be cancelled")
        finally:
            finished.set()

    monkeypatch.setattr(fake, "get_job_run", fail)
    with capture_service_diagnostics() as diagnostics:
        await vm.refresh()
        expected = [CapturedServiceDiagnostic("emr-serverless", "get_job_run", "RuntimeError")]
        assert diagnostics == expected
        error_text = "unexpected error: unexpected client failure"
        assert vm.state is PaneState.ERROR
        assert vm.error_text == error_text

        monkeypatch.setattr(fake, "get_job_run", retry)
        retry_task = asyncio.create_task(vm.refresh())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            assert vm.state is PaneState.LOADING
            assert vm.error_text is None
            retry_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await retry_task

            assert vm.state is PaneState.ERROR
            assert vm.error_text == error_text
            assert diagnostics == expected
        finally:
            if not retry_task.done():
                retry_task.cancel()
            release.set()
            await asyncio.gather(retry_task, return_exceptions=True)
            await asyncio.wait_for(finished.wait(), timeout=1)
            vm.dispose()


@pytest.mark.asyncio
async def test_resource_never_sends_to_the_shared_hub() -> None:
    from vmx import AsyncResourceVM, ConstructionStatus, PropertyChangedMessage

    fake = _InMemoryEmr()
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunDetailVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    messages: list[Message] = []
    changes: list[str] = []
    hub_subscription = hub.messages.subscribe(on_next=messages.append)
    local_subscription = vm.on_property_changed.subscribe(on_next=changes.append)
    try:
        vm.construct()
        original = getattr(vm, "_resource", None)
        assert isinstance(original, AsyncResourceVM)
        assert original.status is ConstructionStatus.CONSTRUCTED
        vm.set_target("a1", "r1")
        assert vm._resource is original
        await vm.refresh()
        vm.set_target("a1", "r1")
        assert vm._resource is original
        await vm.refresh()
        vm.set_target("a1", None)
        replacement = vm._resource
        assert replacement is not original
        assert original.status is ConstructionStatus.DISPOSED
        assert replacement.status is ConstructionStatus.CONSTRUCTED
        await vm.refresh()
        assert vm.state is PaneState.EMPTY
        assert vm.detail is None
        assert [args for name, args in fake.calls if name == "get_job_run"] == [
            ("a1", "r1"),
            ("a1", "r1"),
        ]

        vm.dispose()
        assert replacement.status is ConstructionStatus.DISPOSED
        assert all(message.sender_object in (vm, vm._inner) for message in messages)
        property_messages = [m for m in messages if isinstance(m, PropertyChangedMessage)]
        assert [(m.sender_object, m.sender_name) for m in property_messages] == [
            (vm, "emr.job_run_detail")
        ] * len(changes)
        assert [m.property_name for m in property_messages] == changes
        assert changes == ["state", "detail"] + ["detail", "state"] * 4 + ["state", "detail"]
    finally:
        local_subscription.dispose()
        hub_subscription.dispose()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["idle", "ready", "error"])
@pytest.mark.parametrize("cancel_and_return", [False, True], ids=["suspend", "return"])
async def test_provider_self_cancel_restores_the_baseline(
    monkeypatch: pytest.MonkeyPatch, baseline: str, cancel_and_return: bool
) -> None:
    from vmx import AsyncResourceStatus

    from aws_tui.domain.emr_serverless import JobRunDetail
    from aws_tui.vm.service_diagnostics import (
        CapturedServiceDiagnostic,
        capture_service_diagnostics,
    )

    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1")
    loaded = fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")

    async def fail(_app_id: str, _run_id: str) -> JobRunDetail:
        raise RuntimeError("original error")

    async def self_cancel(_app_id: str, _run_id: str) -> JobRunDetail:
        task = asyncio.current_task()
        assert task is not None
        task.cancel("provider stopped")
        if cancel_and_return:
            return loaded
        await asyncio.sleep(0)
        raise AssertionError("provider should be cancelled")

    try:
        with capture_service_diagnostics() as diagnostics:
            if baseline != "idle":
                await vm.refresh()
            if baseline == "error":
                with monkeypatch.context() as patch:
                    patch.setattr(fake, "get_job_run", fail)
                    await vm.refresh()

            with monkeypatch.context() as patch:
                patch.setattr(fake, "get_job_run", self_cancel)
                with pytest.raises(asyncio.CancelledError, match="provider stopped"):
                    await vm.refresh()

            expected_state, expected_status = {
                "idle": (PaneState.LOADING, AsyncResourceStatus.IDLE),
                "ready": (PaneState.IDLE, AsyncResourceStatus.READY),
                "error": (PaneState.ERROR, AsyncResourceStatus.ERROR),
            }[baseline]
            assert vm.state is expected_state
            assert vm._resource.state.status is expected_status
            assert vm.detail is (None if baseline == "idle" else loaded)
            assert vm.error_text == (
                "unexpected error: original error" if baseline == "error" else None
            )
            assert diagnostics == (
                [CapturedServiceDiagnostic("emr-serverless", "get_job_run", "RuntimeError")]
                if baseline == "error"
                else []
            )

            await vm.refresh()
            assert vm.detail is loaded
            assert vm.state is PaneState.IDLE
            assert vm.error_text is None
    finally:
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("already_cancelling", [False, True])
async def test_provider_self_cancel_in_a_superseded_read_leaves_the_new_read_loading(
    monkeypatch: pytest.MonkeyPatch, already_cancelling: bool
) -> None:
    from aws_tui.domain.emr_serverless import JobRunDetail

    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1")
    current = fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    old_started = asyncio.Event()
    old_cleaning = asyncio.Event()
    old_cleaned = asyncio.Event()
    old_release = asyncio.Event()
    old_finished = asyncio.Event()
    new_started = asyncio.Event()
    new_release = asyncio.Event()

    async def old_read(_app_id: str, _run_id: str) -> JobRunDetail:
        old_started.set()
        try:
            if already_cancelling:
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
            await old_release.wait()
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            old_cleaning.set()
            await old_release.wait()
            old_cleaned.set()
            raise
        finally:
            old_finished.set()

    async def new_read(_app_id: str, _run_id: str) -> JobRunDetail:
        new_started.set()
        await new_release.wait()
        return current

    vm.set_target("a1", "r1")
    monkeypatch.setattr(fake, "get_job_run", old_read)
    previous = asyncio.create_task(vm.refresh())
    latest = None
    try:
        await asyncio.wait_for(old_started.wait(), timeout=1)
        if already_cancelling:
            await asyncio.wait_for(old_cleaning.wait(), timeout=1)
        monkeypatch.setattr(fake, "get_job_run", new_read)
        latest = asyncio.create_task(vm.refresh())
        await asyncio.wait_for(new_started.wait(), timeout=1)
        assert old_cleaning.is_set()
        await asyncio.wait_for(asyncio.shield(previous), timeout=1)
        assert not old_finished.is_set()

        old_release.set()
        await asyncio.wait_for(old_finished.wait(), timeout=1)
        assert old_cleaned.is_set()
        assert vm.state is PaneState.LOADING
        assert vm.detail is None
        assert not latest.done()

        new_release.set()
        await asyncio.wait_for(latest, timeout=1)
        assert vm.detail is current
        assert vm.state is PaneState.IDLE
    finally:
        old_release.set()
        new_release.set()
        await asyncio.gather(
            previous, *([latest] if latest is not None else []), return_exceptions=True
        )
        await asyncio.wait_for(old_finished.wait(), timeout=1)
        vm.dispose()
