from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import pytest

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderUnreachableError,
    ThrottledError,
)
from aws_tui.domain.query import QueryExecutionDetail, QueryExecutionRef, QueryState
from aws_tui.vm.athena.query_vm import _MAX_PENDING_CLEANUP_REFS
from tests.unit.vm.athena.test_query_vm import (
    InMemoryAthena,
    _detail,
    make_query_vm,
    seeded_athena,
)


class PollFailureAthena(InMemoryAthena):
    def __init__(self, error: Exception) -> None:
        super().__init__(
            executions=(
                (_detail("q-app-1", QueryState.RUNNING),),
                (_detail("q-app-2", QueryState.FAILED),),
            )
        )
        self.poll_error = error

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        if execution_id == "q-app-1":
            self.calls.append("poll")
            raise self.poll_error
        return await super().get_query_execution(execution_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        AuthRequiredError,
        PermissionDeniedError,
        ProviderUnreachableError,
        ThrottledError,
        RuntimeError,
    ],
)
async def test_poll_failure_keeps_original_query_stoppable(error_type: type[Exception]) -> None:
    client = PollFailureAthena(error_type("controlled poll failure"))
    vm = make_query_vm(client)
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()

        assert not vm.is_executing
        assert vm.owns_active_query
        assert vm.cancel_command.can_execute()

        await vm.cancel()

        assert client.stop_calls == ["q-app-1"]
        assert not vm.owns_active_query
        assert vm.state is QueryState.CANCELLED
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
async def test_rerun_waits_for_original_stop_before_replacing_query() -> None:
    client = PollFailureAthena(ProviderUnreachableError("controlled poll failure"))
    vm = make_query_vm(client)
    rerun: asyncio.Task[None] | None = None
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()
        original = vm.execution_ref
        client.block_stop = True

        rerun = asyncio.create_task(vm.execute())
        await asyncio.wait_for(client.stop_started.wait(), timeout=1)

        assert len(client.start_calls) == 1
        assert vm.execution_ref == original
        assert vm.owns_active_query
        client.release_stop.set()
        await rerun

        assert client.stop_calls == ["q-app-1"]
        assert client.calls == ["start", "poll", "stop", "start", "poll"]
        assert vm.execution_ref is not None
        assert vm.execution_ref.execution_id == "q-app-2"
        assert vm.state is QueryState.FAILED
        assert not vm.owns_active_query
    finally:
        client.release_stop.set()
        if rerun is not None:
            await asyncio.gather(rerun, return_exceptions=True)
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        AuthRequiredError,
        PermissionDeniedError,
        ProviderUnreachableError,
        ThrottledError,
        RuntimeError,
    ],
)
async def test_failed_rerun_stop_preserves_owner_without_another_start(
    error_type: type[Exception],
) -> None:
    client = PollFailureAthena(ProviderUnreachableError("controlled poll failure"))
    vm = make_query_vm(client)
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()
        original = vm.execution_ref
        client.stop_error = error_type("controlled stop failure")

        await vm.execute()

        assert len(client.start_calls) == 1
        assert client.stop_calls == ["q-app-1"]
        assert vm.execution_ref == original
        assert vm.owns_active_query
        assert vm.error_text
        assert vm.cancel_command.can_execute()

        client.stop_error = None
        await vm.cancel()
        assert client.stop_calls == ["q-app-1", "q-app-1"]
        assert not vm.owns_active_query
    finally:
        client.stop_error = None
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["context", "shutdown"])
async def test_poll_failure_lifecycle_stops_original_owned_query(transition: str) -> None:
    client = PollFailureAthena(ProviderUnreachableError("controlled poll failure"))
    vm = make_query_vm(client)
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()
        original = vm.execution_ref
        assert original is not None

        if transition == "context":
            await vm.set_context(replace(vm.context, database="another_database"))
        else:
            await vm.shutdown()

        assert client.stop_calls == [original.execution_id]
        assert not vm.owns_active_query
        assert not vm._pending_cleanup_refs
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
async def test_input_close_during_rerun_stop_prevents_new_submission() -> None:
    client = PollFailureAthena(ProviderUnreachableError("controlled poll failure"))
    vm = make_query_vm(client)
    rerun: asyncio.Task[None] | None = None
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()
        client.block_stop = True
        rerun = asyncio.create_task(vm.execute())
        await asyncio.wait_for(client.stop_started.wait(), timeout=1)

        vm.close_input_admission()
        client.release_stop.set()
        await rerun
        await vm.shutdown()

        assert len(client.start_calls) == 1
        assert client.stop_calls == ["q-app-1"]
        assert not vm.owns_active_query
    finally:
        client.release_stop.set()
        if rerun is not None:
            await asyncio.gather(rerun, return_exceptions=True)
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["stop", "cancel_task"])
async def test_interrupted_rerun_stop_never_submits_replacement(interruption: str) -> None:
    client = PollFailureAthena(ProviderUnreachableError("controlled poll failure"))
    vm = make_query_vm(client)
    rerun: asyncio.Task[None] | None = None
    stopping: asyncio.Task[None] | None = None
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()
        client.block_stop = True
        client.ignore_stop_cancellation = True
        rerun = asyncio.create_task(vm.execute())
        await asyncio.wait_for(client.stop_started.wait(), timeout=1)
        execution = vm._execution_task
        assert execution is not None

        if interruption == "stop":
            stopping = asyncio.create_task(vm.cancel())
            async with asyncio.timeout(1):
                while not vm.cancel_command.is_executing:
                    await asyncio.sleep(0)
        else:
            rerun.cancel()
            async with asyncio.timeout(1):
                while not execution.cancelling():
                    await asyncio.sleep(0)
        client.release_stop.set()
        await asyncio.gather(rerun, execution, return_exceptions=True)
        if stopping is not None:
            await stopping

        assert len(client.start_calls) == 1
        assert client.stop_calls == ["q-app-1"]
        assert not vm.owns_active_query
        assert vm.state is QueryState.CANCELLED
    finally:
        client.release_stop.set()
        await asyncio.gather(
            *(task for task in (rerun, stopping) if task is not None), return_exceptions=True
        )
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["context", "shutdown"])
async def test_lifecycle_during_rerun_retirement_prevents_replacement(
    transition: str,
) -> None:
    client = PollFailureAthena(ProviderUnreachableError("controlled poll failure"))
    client.executions[1] = [_detail("q-app-2", QueryState.RUNNING)]
    client.block_poll_for = "q-app-2"
    vm = make_query_vm(client)
    rerun: asyncio.Task[None] | None = None
    changing: asyncio.Task[None] | None = None
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()
        original = vm.execution_ref
        client.block_stop = True
        rerun = asyncio.create_task(vm.execute())
        await asyncio.wait_for(client.stop_started.wait(), timeout=1)
        transition_started = asyncio.Event()

        async def change_lifecycle() -> None:
            transition_started.set()
            if transition == "context":
                await vm.set_context(replace(vm.context, database="another_database"))
            else:
                await vm.shutdown()

        changing = asyncio.create_task(change_lifecycle())
        await asyncio.wait_for(transition_started.wait(), timeout=1)
        assert vm.execution_ref == original
        assert len(client.start_calls) == 1
        client.release_stop.set()
        await asyncio.wait_for(asyncio.gather(rerun, changing), timeout=2)

        assert len(client.start_calls) == 1
        assert client.stop_calls == ["q-app-1"]
        assert not vm.owns_active_query
        assert not vm._pending_cleanup_refs
    finally:
        client.release_stop.set()
        client.release_poll.set()
        await asyncio.gather(
            *(task for task in (rerun, changing) if task is not None), return_exceptions=True
        )
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
async def test_observer_cancelling_retired_rerun_prevents_replacement() -> None:
    client = PollFailureAthena(ProviderUnreachableError("controlled poll failure"))
    vm = make_query_vm(client)
    subscription = None
    try:
        vm.set_sql("SELECT 1")
        await vm.execute()

        def cancel_retired_rerun(property_name: str) -> None:
            if (
                property_name == "owns_active_query"
                and not vm.owns_active_query
                and vm.state is QueryState.CANCELLED
            ):
                vm.execute_command.cancel()

        subscription = vm.on_property_changed.subscribe(cancel_retired_rerun)
        await vm.execute()

        assert len(client.start_calls) == 1
        assert client.stop_calls == ["q-app-1"]
        assert not vm.owns_active_query
        assert vm.state is QueryState.CANCELLED
    finally:
        if subscription is not None:
            subscription.dispose()
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
async def test_restored_terminal_reference_does_not_gain_stop_authority_on_run() -> None:
    source = make_query_vm(seeded_athena([QueryState.FAILED]))
    client = seeded_athena([QueryState.FAILED])
    destination = make_query_vm(client)
    try:
        source.set_sql("SELECT 1")
        await source.execute()
        await destination.restore_snapshot(source.export_snapshot())
        await destination.cancel()
        await destination.execute()

        assert len(client.start_calls) == 1
        assert client.stop_calls == []
        assert not destination.owns_active_query
    finally:
        await source.shutdown()
        source.dispose()
        await destination.shutdown()
        destination.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["capacity", "permission_denied"])
async def test_deliberate_cleanup_abandonment_has_explicit_diagnostic(
    reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    client = seeded_athena([QueryState.FAILED])
    vm = make_query_vm(client)
    original = QueryExecutionRef(
        "q-original", vm.context.connection_name, vm.context.region, vm.context.workgroup
    )
    try:
        with caplog.at_level(logging.WARNING, logger="aws_tui.vm.athena.query_vm"):
            vm._retain_cleanup(original)
            if reason == "capacity":
                for index in range(_MAX_PENDING_CLEANUP_REFS):
                    vm._retain_cleanup(replace(original, execution_id=f"q-later-{index}"))
                assert len(vm._pending_cleanup_refs) == _MAX_PENDING_CLEANUP_REFS
            else:
                client.stop_error = PermissionDeniedError("controlled denied stop")
                await vm._stop_pending_cleanup(report_error=False)
            assert "q-original" not in vm._pending_cleanup_refs
            matching = [
                record
                for record in caplog.records
                if getattr(record, "execution_id", None) == original.execution_id
            ]
            assert matching
            assert getattr(matching[-1], "cleanup_reason", None) == reason
    finally:
        client.stop_error = None
        await vm.shutdown()
        vm.dispose()
