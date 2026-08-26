from __future__ import annotations

import asyncio
import hashlib
import traceback
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from vmx import NULL_DISPATCHER, AsyncRelayCommand, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.athena_runner import AthenaQueryRunner
from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import (
    AthenaQueryError,
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
    ResultPage,
)
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.vm.athena.query_vm import AthenaQueryVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_CONTEXT = QueryContext(
    "prod-west",
    "us-west-2",
    "analysts",
    "AwsDataCatalog",
    "sales",
)
_STATS = QueryStatistics(
    engine_ms=12,
    queue_ms=3,
    planning_ms=4,
    service_ms=1,
    bytes_scanned=0,
    reused_previous_result=False,
)
_EMPTY_STATS = QueryStatistics(None, None, None, None, None, False)
_COLUMN = ResultColumn("value", "varchar", "NULLABLE")


def _detail(
    execution_id: str,
    state: QueryState,
    *,
    context: QueryContext = _CONTEXT,
    statistics: QueryStatistics = _STATS,
    error: AthenaQueryError | None = None,
    state_reason: str | None = None,
) -> QueryExecutionDetail:
    ref = QueryExecutionRef(
        execution_id,
        context.connection_name,
        context.region,
        context.workgroup,
    )
    return QueryExecutionDetail(
        summary=QueryExecutionSummary(
            ref=ref,
            state=state,
            submitted_at=datetime(2026, 7, 25, tzinfo=UTC),
            completed_at=(
                datetime(2026, 7, 25, 0, 0, 1, tzinfo=UTC)
                if state in {QueryState.SUCCEEDED, QueryState.FAILED, QueryState.CANCELLED}
                else None
            ),
            statement_type="DML",
        ),
        state_reason=state_reason,
        context=context,
        statistics=statistics,
        output_location="s3://private-results/query.csv",
        engine_version="Athena engine version 3",
        error=error,
    )


class InMemoryAthena:
    def __init__(
        self,
        *,
        executions: Sequence[Sequence[QueryExecutionDetail]] = (),
        result_pages: dict[tuple[str, str | None], ResultPage] | None = None,
    ) -> None:
        self.executions = [list(states) for states in executions]
        self.result_pages = result_pages or {}
        self.calls: list[str] = []
        self.start_calls: list[tuple[str, QueryContext, str]] = []
        self.stop_calls: list[str] = []
        self.result_calls: list[tuple[str, str | None]] = []
        self._next_execution = 0
        self._states: dict[str, list[QueryExecutionDetail]] = {}
        self.start_started = asyncio.Event()
        self.release_start = asyncio.Event()
        self.block_start = False
        self.ignore_start_cancellation = False
        self.poll_started = asyncio.Event()
        self.release_poll = asyncio.Event()
        self.block_poll_for: str | None = None
        self.ignore_poll_cancellation = False
        self.results_started = asyncio.Event()
        self.release_results = asyncio.Event()
        self.block_results_for: str | None = None
        self.ignore_results_cancellation = False
        self.stop_started = asyncio.Event()
        self.release_stop = asyncio.Event()
        self.block_stop = False
        self.ignore_stop_cancellation = False
        self.start_error: Exception | None = None
        self.stop_error: Exception | None = None

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        self.calls.append("start")
        self.start_calls.append((sql, context, request_token))
        self.start_started.set()
        if self.block_start:
            try:
                await self.release_start.wait()
            except asyncio.CancelledError:
                if not self.ignore_start_cancellation:
                    raise
                await self.release_start.wait()
        if self.start_error is not None:
            raise self.start_error
        execution_id = f"q-app-{self._next_execution + 1}"
        states = self.executions[self._next_execution]
        self._next_execution += 1
        self._states[execution_id] = states
        return QueryExecutionRef(
            execution_id,
            context.connection_name,
            context.region,
            context.workgroup,
        )

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        self.calls.append("poll")
        self.poll_started.set()
        if self.block_poll_for == execution_id:
            try:
                await self.release_poll.wait()
            except asyncio.CancelledError:
                if not self.ignore_poll_cancellation:
                    raise
                await self.release_poll.wait()
        states = self._states[execution_id]
        if len(states) > 1:
            return states.pop(0)
        return states[0]

    async def stop_query(self, execution_id: str) -> None:
        self.calls.append("stop")
        self.stop_calls.append(execution_id)
        self.stop_started.set()
        if self.block_stop:
            try:
                await self.release_stop.wait()
            except asyncio.CancelledError:
                if not self.ignore_stop_cancellation:
                    raise
                await self.release_stop.wait()
        if self.stop_error is not None:
            raise self.stop_error

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        self.calls.append("results")
        self.result_calls.append((execution_id, start_token))
        self.results_started.set()
        if self.block_results_for == execution_id:
            try:
                await self.release_results.wait()
            except asyncio.CancelledError:
                if not self.ignore_results_cancellation:
                    raise
                await self.release_results.wait()
        return self.result_pages[(execution_id, start_token)]


class DetachedStartAthena(InMemoryAthena):
    """Model a transport request that outlives cancellation of its waiter."""

    def __init__(
        self,
        *,
        executions: Sequence[Sequence[QueryExecutionDetail]],
        start_error: Exception | None = None,
    ) -> None:
        super().__init__(executions=executions)
        self.start_error = start_error
        self.start_cancelled = asyncio.Event()
        self.start_accepted = asyncio.Event()
        self.remote_tasks: list[asyncio.Task[QueryExecutionRef]] = []

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        self.calls.append("start")
        self.start_calls.append((sql, context, request_token))
        self.start_started.set()
        remote = asyncio.create_task(self._finish_remote_start(context))
        self.remote_tasks.append(remote)
        try:
            return await asyncio.shield(remote)
        except asyncio.CancelledError:
            self.start_cancelled.set()
            raise

    async def _finish_remote_start(self, context: QueryContext) -> QueryExecutionRef:
        await self.release_start.wait()
        self.start_accepted.set()
        if self.start_error is not None:
            raise self.start_error
        execution_id = f"q-app-{self._next_execution + 1}"
        states = self.executions[self._next_execution]
        self._next_execution += 1
        self._states[execution_id] = states
        return QueryExecutionRef(
            execution_id,
            context.connection_name,
            context.region,
            context.workgroup,
        )

    async def drain_remote_tasks(self) -> None:
        await asyncio.gather(*self.remote_tasks, return_exceptions=True)


class DetachedWrongIdentityStartAthena(DetachedStartAthena):
    async def _finish_remote_start(self, context: QueryContext) -> QueryExecutionRef:
        ref = await super()._finish_remote_start(context)
        return QueryExecutionRef(
            ref.execution_id,
            "unexpected-connection",
            ref.region,
            ref.workgroup,
        )


def seeded_athena(
    states: Sequence[QueryState],
    *,
    rows: tuple[tuple[str | None, ...], ...] = (("1",),),
) -> InMemoryAthena:
    details = [_detail("q-app-1", state) for state in states]
    return InMemoryAthena(
        executions=(details,),
        result_pages={
            ("q-app-1", None): ResultPage((_COLUMN,), rows, None),
        },
    )


async def _no_sleep(_: float) -> None:
    return None


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=1)


def make_query_vm(
    fake: InMemoryAthena,
    *,
    context: QueryContext = _CONTEXT,
    sleep: Callable[[float], Awaitable[None]] = _no_sleep,
) -> AthenaQueryVM:
    hub: MessageHub[Message] = MessageHub()
    vm = AthenaQueryVM(
        client=fake,
        policy=ReadOnlySqlPolicy(),
        context=context,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        sleep=sleep,
    )
    vm.construct()
    return vm


async def _query_snapshot_failure_artifacts(
    vm: AthenaQueryVM,
    snapshot: object,
    crash_dir: Path,
) -> tuple[str, str, str]:
    try:
        await vm.restore_snapshot(snapshot)  # type: ignore[arg-type]
    except ValueError as error:
        snapshot = None
        rendered = "".join(
            traceback.TracebackException.from_exception(
                error,
                capture_locals=True,
            ).format()
        )
        crash_path = CrashDump(base_dir=crash_dir).write(exc=error)
        return str(error), rendered, crash_path.read_text(encoding="utf-8")
    raise AssertionError("hostile snapshot should fail closed")


def test_query_vm_owns_reusable_runner_when_not_injected() -> None:
    fake = seeded_athena([QueryState.SUCCEEDED])
    vm = make_query_vm(fake)

    assert isinstance(vm.runner, AthenaQueryRunner)


@pytest.mark.asyncio
async def test_query_vm_delegates_execution_operations_to_injected_runner() -> None:
    fake = seeded_athena([QueryState.SUCCEEDED])

    class TrackingRunner(AthenaQueryRunner):
        def __init__(self) -> None:
            super().__init__(fake, ReadOnlySqlPolicy(), sleep=_no_sleep)
            self.operations: list[str] = []

        async def start(
            self,
            sql: str,
            context: QueryContext,
            *,
            request_token: str,
        ) -> QueryExecutionRef:
            self.operations.append("start")
            return await super().start(
                sql,
                context,
                request_token=request_token,
            )

        async def detail(
            self,
            ref: QueryExecutionRef,
            context: QueryContext,
        ) -> QueryExecutionDetail:
            self.operations.append("detail")
            return await super().detail(ref, context)

    runner = TrackingRunner()
    hub: MessageHub[Message] = MessageHub()
    vm = AthenaQueryVM(
        client=fake,
        policy=ReadOnlySqlPolicy(),
        runner=runner,
        context=_CONTEXT,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        sleep=_no_sleep,
    )
    vm.construct()
    vm.set_sql("SELECT 1")

    await vm.execute()

    assert vm.runner is runner
    assert runner.operations == ["start", "detail"]


@pytest.mark.asyncio
async def test_execute_validates_before_sdk_dispatch() -> None:
    fake = InMemoryAthena()
    vm = make_query_vm(fake)
    vm.set_sql("DELETE FROM prod.events WHERE marker = 'SQL_SECRET'")

    await vm.execute()

    assert vm.validation_error is not None
    assert fake.calls == []
    assert "SQL_SECRET" not in vm.validation_error
    assert "SQL_SECRET" not in repr(vm)


@pytest.mark.asyncio
async def test_execute_rejects_incomplete_context_before_dispatch() -> None:
    fake = InMemoryAthena()
    context = QueryContext("prod-west", "us-west-2", "", "AwsDataCatalog", "sales")
    vm = make_query_vm(fake, context=context)
    vm.set_sql("SELECT 1")

    await vm.execute()

    assert vm.validation_error == "query context is incomplete"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_execute_polls_to_success_and_loads_first_result_page() -> None:
    fake = seeded_athena([QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED])
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")

    await vm.execute()

    assert vm.state is QueryState.SUCCEEDED
    assert vm.results.rows == (("1",),)
    assert vm.statistics.bytes_scanned == 0
    assert vm.execution_ref is not None
    assert vm.execution_ref.execution_id == "q-app-1"
    assert not vm.owns_active_query


@pytest.mark.asyncio
async def test_request_token_is_context_scoped_deterministic_and_not_sql_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = seeded_athena([QueryState.SUCCEEDED])
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 'SQL_SECRET_42'")
    monkeypatch.setattr(
        "aws_tui.vm.athena.query_vm.uuid4",
        lambda: UUID("12345678-1234-5678-1234-567812345678"),
    )

    await vm.execute()

    token = fake.start_calls[0][2]
    assert token == "3a9dabffcee3720f9954443c8a8f719111653bd6650f78c0e8c1de49bec9e370"
    assert "SQL_SECRET_42" not in token
    assert _CONTEXT.connection_name not in token


@pytest.mark.asyncio
async def test_execute_command_suppresses_double_submit() -> None:
    fake = seeded_athena([QueryState.SUCCEEDED])
    fake.block_start = True
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")

    first = asyncio.create_task(vm.execute())
    await fake.start_started.wait()
    await vm.execute()
    assert len(fake.start_calls) == 1

    fake.release_start.set()
    await first
    assert len(fake.start_calls) == 1


@pytest.mark.asyncio
async def test_polling_uses_bounded_exponential_backoff() -> None:
    states = [QueryState.RUNNING] * 7 + [QueryState.SUCCEEDED]
    fake = seeded_athena(states)
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    vm = make_query_vm(fake, sleep=record_sleep)
    vm.set_sql("SELECT 1")

    await vm.execute()

    assert delays == [0.25, 0.5, 1.0, 2.0, 4.0, 5.0, 5.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [QueryState.FAILED, QueryState.CANCELLED])
async def test_terminal_failure_states_do_not_load_results(terminal: QueryState) -> None:
    query_error = AthenaQueryError(2, 1001, False, "structured failure")
    detail = _detail(
        "q-app-1",
        terminal,
        error=query_error if terminal is QueryState.FAILED else None,
        state_reason="terminal reason",
    )
    fake = InMemoryAthena(executions=((detail,),))
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")

    await vm.execute()

    assert vm.state is terminal
    assert vm.query_error is (query_error if terminal is QueryState.FAILED else None)
    assert vm.state_reason == "terminal reason"
    assert vm.results.rows == ()
    assert fake.result_calls == []
    assert not vm.owns_active_query


@pytest.mark.asyncio
async def test_new_query_replaces_prior_execution_and_results() -> None:
    fake = InMemoryAthena(
        executions=(
            (_detail("q-app-1", QueryState.SUCCEEDED),),
            (_detail("q-app-2", QueryState.SUCCEEDED),),
        ),
        result_pages={
            ("q-app-1", None): ResultPage((_COLUMN,), (("first",),), None),
            ("q-app-2", None): ResultPage((_COLUMN,), (("second",),), None),
        },
    )
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    await vm.execute()
    vm.set_sql("SELECT 2")

    await vm.execute()

    assert vm.execution_ref is not None
    assert vm.execution_ref.execution_id == "q-app-2"
    assert vm.results.rows == (("second",),)
    assert len({call[2] for call in fake.start_calls}) == 2


@pytest.mark.asyncio
async def test_context_replacement_stops_owned_query_and_discards_stale_poll() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    fake.block_poll_for = "q-app-1"
    fake.ignore_poll_cancellation = True
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await fake.poll_started.wait()
    replacement = QueryContext(
        "prod-west",
        "us-west-2",
        "other-workgroup",
        "AwsDataCatalog",
        "other_database",
    )

    replacement_task = asyncio.create_task(vm.set_context(replacement))
    await asyncio.sleep(0)
    fake.release_poll.set()
    await replacement_task
    await execution

    assert fake.stop_calls == ["q-app-1"]
    assert vm.context == replacement
    assert vm.execution_ref is None
    assert vm.state is None
    assert vm.statistics == _EMPTY_STATS
    assert vm.results.rows == ()


@pytest.mark.asyncio
async def test_context_replacement_drains_stale_submit_and_clears_submitting() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    fake.block_start = True
    fake.ignore_start_cancellation = True
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await fake.start_started.wait()
    replacement = QueryContext(
        "prod-west",
        "us-west-2",
        "other-workgroup",
        "AwsDataCatalog",
        "other_database",
    )

    replacement_task = asyncio.create_task(vm.set_context(replacement))
    await asyncio.sleep(0)
    fake.release_start.set()
    await replacement_task
    await execution

    assert fake.stop_calls == ["q-app-1"]
    assert vm.context == replacement
    assert not vm.is_submitting
    assert not vm.is_executing


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["shutdown", "cancel", "context"])
async def test_lifecycle_transition_retains_detached_submission_until_remote_stop(
    transition: str,
) -> None:
    fake = DetachedStartAthena(executions=((_detail("q-app-1", QueryState.RUNNING),),))
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await fake.start_started.wait()
    replacement = QueryContext(
        "prod-west",
        "us-west-2",
        "other-workgroup",
        "AwsDataCatalog",
        "other_database",
    )

    if transition == "shutdown":
        lifecycle = asyncio.create_task(vm.shutdown())
    elif transition == "cancel":
        lifecycle = asyncio.create_task(vm.cancel())
    else:
        lifecycle = asyncio.create_task(vm.set_context(replacement))
    await asyncio.sleep(0)
    fake.release_start.set()
    await lifecycle
    await execution
    await fake.drain_remote_tasks()

    assert fake.start_accepted.is_set()
    assert not fake.start_cancelled.is_set()
    assert fake.stop_calls == ["q-app-1"]
    assert not vm.is_submitting
    assert not vm.is_executing
    if transition == "context":
        assert vm.context == replacement
    await vm.shutdown()
    vm.dispose()


@pytest.mark.asyncio
async def test_cancelled_context_transition_finishes_remote_stop_and_clears_busy_state() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    fake.block_poll_for = "q-app-1"
    fake.block_stop = True
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await asyncio.wait_for(fake.poll_started.wait(), timeout=1)
    replacement = QueryContext(
        "prod-west",
        "us-west-2",
        "other-workgroup",
        "AwsDataCatalog",
        "other_database",
    )
    transition = asyncio.create_task(vm.set_context(replacement))
    await asyncio.wait_for(fake.stop_started.wait(), timeout=1)

    transition.cancel()
    await asyncio.sleep(0)
    assert not transition.done()
    transition.cancel()
    transition.cancel()
    await asyncio.sleep(0)
    assert not transition.done()
    fake.release_stop.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(transition, timeout=1)
    await asyncio.wait_for(execution, timeout=1)

    assert vm.context == replacement
    assert not vm._lifecycle_transition
    assert vm._pending_cleanup_refs == {}
    assert vm.export_snapshot().context == replacement
    await vm.shutdown()
    vm.dispose()


@pytest.mark.asyncio
async def test_shutdown_awaits_detached_submission_error_without_publication() -> None:
    fake = DetachedStartAthena(
        executions=((_detail("q-app-1", QueryState.RUNNING),),),
        start_error=ProviderError("delayed SQL_SECRET submission failure"),
    )
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 'SQL_SECRET'")
    visible_errors: list[str] = []
    subscription = vm.on_property_changed.subscribe(
        lambda name: (
            visible_errors.append(vm.error_text)
            if name == "error_text" and vm.error_text is not None
            else None
        )
    )
    execution = asyncio.create_task(vm.execute())
    await fake.start_started.wait()

    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    shutdown_waited_for_transport = not shutdown.done()
    fake.release_start.set()
    await shutdown
    await execution
    await fake.drain_remote_tasks()

    assert shutdown_waited_for_transport
    assert not fake.start_cancelled.is_set()
    assert visible_errors == []
    assert vm.error_text is None
    assert not vm.is_submitting
    subscription.dispose()
    vm.dispose()


@pytest.mark.asyncio
async def test_shutdown_stops_detached_submission_with_mismatched_response_context() -> None:
    fake = DetachedWrongIdentityStartAthena(executions=((_detail("q-app-1", QueryState.RUNNING),),))
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await fake.start_started.wait()

    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    fake.release_start.set()
    await shutdown
    await execution
    await fake.drain_remote_tasks()

    assert fake.stop_calls == ["q-app-1"]
    assert vm.execution_ref is None
    assert vm.error_text is None
    vm.dispose()


@pytest.mark.asyncio
async def test_dispose_retains_detached_submission_until_silent_remote_stop() -> None:
    fake = DetachedStartAthena(executions=((_detail("q-app-1", QueryState.RUNNING),),))
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(notifications.append)
    execution = asyncio.create_task(vm.execute())
    await fake.start_started.wait()

    notifications.clear()
    vm.dispose()
    fake.release_start.set()
    await execution
    await fake.drain_remote_tasks()

    assert fake.start_accepted.is_set()
    assert not fake.start_cancelled.is_set()
    assert fake.stop_calls == ["q-app-1"]
    assert notifications == []
    assert not vm.execute_command.can_execute()
    assert not vm.cancel_command.can_execute()
    subscription.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_error", "expected_stops"),
    [
        (None, ["q-app-1"]),
        (ProviderError("delayed SQL_SECRET submission failure"), []),
    ],
)
async def test_cancel_then_dispose_keeps_submission_finalizer_without_stale_publication(
    start_error: Exception | None,
    expected_stops: list[str],
) -> None:
    fake = DetachedStartAthena(
        executions=((_detail("q-app-1", QueryState.RUNNING),),),
        start_error=start_error,
    )
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 'SQL_SECRET'")
    visible_errors: list[str] = []
    subscription = vm.on_property_changed.subscribe(
        lambda name: (
            visible_errors.append(vm.error_text)
            if name == "error_text" and vm.error_text is not None
            else None
        )
    )
    execution = asyncio.create_task(vm.execute())
    await asyncio.wait_for(fake.start_started.wait(), timeout=1)
    submission_task = vm._submission_task
    assert submission_task is not None

    cancellation = asyncio.create_task(vm.cancel())
    await _wait_until(
        lambda: vm._execution_task is not None and bool(vm._execution_task.cancelling())
    )
    await asyncio.sleep(0)
    vm.dispose()
    await asyncio.wait_for(
        asyncio.gather(cancellation, execution),
        timeout=1,
    )

    fake.release_start.set()
    await asyncio.wait_for(fake.drain_remote_tasks(), timeout=1)
    if start_error is None:
        await asyncio.wait_for(fake.stop_started.wait(), timeout=1)
    else:
        await _wait_until(lambda: submission_task.done() and not submission_task._log_traceback)
    exception_retrieved = start_error is None or (
        submission_task.done() and not submission_task._log_traceback
    )
    if start_error is not None and not exception_retrieved:
        submission_task.exception()

    assert fake.stop_calls == expected_stops
    assert exception_retrieved
    assert visible_errors == []
    assert vm.error_text is None
    subscription.dispose()


@pytest.mark.asyncio
async def test_stale_submit_cleanup_failure_cannot_publish_into_new_context() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    fake.block_start = True
    fake.ignore_start_cancellation = True
    fake.stop_error = ProviderError("stale cleanup failure")
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    visible_errors: list[str] = []

    def capture_error(property_name: str) -> None:
        if property_name == "error_text" and vm.error_text is not None:
            visible_errors.append(vm.error_text)

    subscription = vm.on_property_changed.subscribe(capture_error)
    execution = asyncio.create_task(vm.execute())
    await fake.start_started.wait()
    replacement = QueryContext(
        "prod-west",
        "us-west-2",
        "other-workgroup",
        "AwsDataCatalog",
        "other_database",
    )

    replacement_task = asyncio.create_task(vm.set_context(replacement))
    await asyncio.sleep(0)
    fake.release_start.set()
    await replacement_task
    await execution

    assert fake.stop_calls == ["q-app-1"]
    assert vm.context == replacement
    assert vm.error_text is None
    assert visible_errors == []
    subscription.dispose()


@pytest.mark.asyncio
async def test_mismatched_execution_identity_fails_closed_before_polling() -> None:
    class WrongIdentityAthena(InMemoryAthena):
        async def start_query(
            self,
            sql: str,
            context: QueryContext,
            *,
            request_token: str,
        ) -> QueryExecutionRef:
            await super().start_query(sql, context, request_token=request_token)
            return QueryExecutionRef(
                "q-app-1",
                "other-connection",
                context.region,
                context.workgroup,
            )

    fake = WrongIdentityAthena(executions=((_detail("q-app-1", QueryState.RUNNING),),))
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")

    await vm.execute()

    assert vm.error_text == "Athena returned a query outside the active context"
    assert fake.calls == ["start", "stop"]
    assert fake.stop_calls == ["q-app-1"]
    assert vm.execution_ref is None
    assert not vm.owns_active_query
    await vm.shutdown()
    assert fake.stop_calls == ["q-app-1"]


@pytest.mark.asyncio
async def test_stale_accepted_stop_failure_in_new_context_is_retried_by_shutdown() -> None:
    class WrongIdentityAthena(InMemoryAthena):
        async def start_query(
            self,
            sql: str,
            context: QueryContext,
            *,
            request_token: str,
        ) -> QueryExecutionRef:
            await super().start_query(sql, context, request_token=request_token)
            return QueryExecutionRef(
                "q-app-1",
                "unexpected-connection",
                context.region,
                context.workgroup,
            )

    replacement = QueryContext(
        "prod-west",
        "us-west-2",
        "other-workgroup",
        "AwsDataCatalog",
        "other_database",
    )
    fake = WrongIdentityAthena(executions=((_detail("q-app-1", QueryState.RUNNING),),))
    fake.block_stop = True
    fake.ignore_stop_cancellation = True
    fake.stop_error = ProviderError("stale SQL_SECRET stop failure")
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 'SQL_SECRET'")
    visible_errors: list[str] = []
    subscription = vm.on_property_changed.subscribe(
        lambda name: (
            visible_errors.append(vm.error_text)
            if name == "error_text" and vm.error_text is not None
            else None
        )
    )
    execution = asyncio.create_task(vm.execute())
    await asyncio.wait_for(fake.stop_started.wait(), timeout=1)

    replacement_task = asyncio.create_task(vm.set_context(replacement))
    await _wait_until(lambda: vm.context == replacement)
    fake.release_stop.set()
    await asyncio.wait_for(
        asyncio.gather(replacement_task, execution),
        timeout=1,
    )

    assert vm.context == replacement
    assert vm.execution_ref is None
    assert visible_errors == []
    assert vm.error_text is None

    fake.block_stop = False
    fake.stop_error = None
    attempts_before_shutdown = len(fake.stop_calls)
    await asyncio.wait_for(vm.shutdown(), timeout=1)
    await asyncio.wait_for(vm.shutdown(), timeout=1)

    assert len(fake.stop_calls) == attempts_before_shutdown + 1
    assert fake.stop_calls == ["q-app-1"] * len(fake.stop_calls)
    subscription.dispose()
    vm.dispose()


@pytest.mark.asyncio
async def test_mismatched_polled_context_never_replaces_active_state() -> None:
    other_context = QueryContext(
        "other-connection",
        "us-west-2",
        "analysts",
        "AwsDataCatalog",
        "sales",
    )
    fake = InMemoryAthena(
        executions=((_detail("q-app-1", QueryState.SUCCEEDED, context=other_context),),)
    )
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")

    await vm.execute()

    assert vm.error_text == "Athena returned a query outside the active context"
    assert vm.state is None
    assert vm.results.rows == ()
    assert fake.stop_calls == ["q-app-1"]


@pytest.mark.asyncio
async def test_shutdown_stops_only_active_app_started_query() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    sleep_started = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    vm = make_query_vm(fake, sleep=blocking_sleep)
    vm.set_sql("SELECT 1")
    task = asyncio.create_task(vm.execute())
    await sleep_started.wait()

    await vm.shutdown()
    vm.dispose()
    await task

    assert fake.stop_calls == ["q-app-1"]


@pytest.mark.asyncio
async def test_shutdown_does_not_stop_terminal_or_unowned_query() -> None:
    fake = seeded_athena([QueryState.SUCCEEDED])
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    await vm.execute()

    await vm.shutdown()
    await vm.shutdown()

    assert fake.stop_calls == []


@pytest.mark.asyncio
async def test_shutdown_stops_query_returned_after_cancelled_submit() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    fake.block_start = True
    fake.ignore_start_cancellation = True
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await fake.start_started.wait()

    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    fake.release_start.set()
    await shutdown
    await execution

    assert fake.stop_calls == ["q-app-1"]
    assert vm.execution_ref is None
    assert not vm.is_submitting
    assert not vm.is_executing


@pytest.mark.asyncio
async def test_shutdown_invalidates_result_page_that_ignores_cancellation() -> None:
    fake = seeded_athena([QueryState.SUCCEEDED], rows=(("STALE_RESULT",),))
    fake.block_results_for = "q-app-1"
    fake.ignore_results_cancellation = True
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await fake.results_started.wait()
    visible_rows: list[tuple[tuple[str | None, ...], ...]] = []

    def capture_rows(property_name: str) -> None:
        if property_name == "rows" and vm.results.rows:
            visible_rows.append(vm.results.rows)

    subscription = vm.results.on_property_changed.subscribe(capture_rows)
    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    fake.release_results.set()
    await shutdown
    await execution

    assert vm.results.rows == ()
    assert visible_rows == []
    subscription.dispose()


@pytest.mark.asyncio
async def test_explicit_cancel_stops_owned_query_and_marks_cancelled() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    sleep_started = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    vm = make_query_vm(fake, sleep=blocking_sleep)
    vm.set_sql("SELECT 1")
    execution = asyncio.create_task(vm.execute())
    await sleep_started.wait()

    await vm.cancel()
    await execution

    assert fake.stop_calls == ["q-app-1"]
    assert vm.state is QueryState.CANCELLED
    assert not vm.owns_active_query


@pytest.mark.asyncio
async def test_provider_failure_is_scoped_and_never_exposes_sql() -> None:
    fake = InMemoryAthena()
    fake.start_error = ProviderError("failed SELECT 'SQL_SECRET'")
    vm = make_query_vm(fake)
    vm.set_sql("SELECT 'SQL_SECRET'")

    await vm.execute()

    assert vm.pane_state is PaneState.ERROR
    assert vm.error_text == "Athena query request failed"
    assert "SQL_SECRET" not in vm.error_text
    assert "SQL_SECRET" not in repr(vm)


@pytest.mark.asyncio
async def test_stop_failure_is_reported_without_cancelling_poll_or_leaking_sql() -> None:
    fake = seeded_athena([QueryState.RUNNING])
    fake.stop_error = ProviderError("cannot stop SELECT 'SQL_SECRET'")
    sleep_started = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    vm = make_query_vm(fake, sleep=blocking_sleep)
    vm.set_sql("SELECT 'SQL_SECRET'")
    execution = asyncio.create_task(vm.execute())
    await sleep_started.wait()

    await vm.cancel()

    assert vm.error_text == "Athena query request failed"
    assert vm.owns_active_query
    assert not execution.done()

    await vm.shutdown()
    await execution

    assert execution.done()
    assert fake.stop_calls == ["q-app-1", "q-app-1"]
    assert "SQL_SECRET" not in (vm.error_text or "")


@pytest.mark.asyncio
async def test_context_replacement_installs_locally_when_old_stop_fails() -> None:
    replacement = QueryContext(
        "prod-west",
        "us-west-2",
        "other-workgroup",
        "AwsDataCatalog",
        "other_database",
    )
    fake = InMemoryAthena(
        executions=(
            (_detail("q-app-1", QueryState.RUNNING),),
            (
                _detail(
                    "q-app-2",
                    QueryState.SUCCEEDED,
                    context=replacement,
                ),
            ),
        ),
        result_pages={
            ("q-app-2", None): ResultPage((_COLUMN,), (("new-context",),), None),
        },
    )
    fake.stop_error = ProviderError("old context cleanup failed")
    sleep_started = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    vm = make_query_vm(fake, sleep=blocking_sleep)
    vm.set_sql("SELECT 1")
    old_execution = asyncio.create_task(vm.execute())
    await sleep_started.wait()

    await vm.set_context(replacement)
    vm.set_sql("SELECT 2")
    await vm.execute()

    assert vm.context == replacement
    assert vm.execution_ref is not None
    assert vm.execution_ref.execution_id == "q-app-2"
    assert vm.state is QueryState.SUCCEEDED
    assert vm.results.rows == (("new-context",),)
    assert vm.error_text is None

    fake.stop_error = None
    await vm.shutdown()
    await old_execution

    assert fake.stop_calls == ["q-app-1", "q-app-1"]
    vm.dispose()


@pytest.mark.parametrize(
    "active_attribute",
    [
        "_busy",
        "_is_submitting",
        "_owns_active_query",
        "_lifecycle_transition",
    ],
)
def test_query_snapshot_export_rejects_active_lifecycle(
    active_attribute: str,
) -> None:
    vm = make_query_vm(InMemoryAthena())
    setattr(vm, active_attribute, True)

    with pytest.raises(ValueError, match=r"^Athena query is busy$"):
        vm.export_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize("active_state", [QueryState.QUEUED, QueryState.RUNNING])
async def test_query_snapshot_restore_rejects_unowned_active_state(
    active_state: QueryState,
) -> None:
    vm = make_query_vm(InMemoryAthena())
    snapshot = vm.export_snapshot()
    hostile = replace(
        snapshot,
        execution_ref=QueryExecutionRef(
            "q-active",
            _CONTEXT.connection_name,
            _CONTEXT.region,
            _CONTEXT.workgroup,
        ),
        state=active_state,
    )

    with pytest.raises(ValueError, match=r"^Athena query snapshot is invalid$"):
        await vm.restore_snapshot(hostile)

    assert vm.execution_ref is None
    assert vm.state is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "changes"),
    [
        (QueryState.SUCCEEDED, {"pane_state": PaneState.LOADING}),
        (QueryState.SUCCEEDED, {"pane_state": PaneState.AUTH_REQUIRED}),
        (QueryState.SUCCEEDED, {"pane_state": PaneState.ERROR}),
        (
            QueryState.SUCCEEDED,
            {
                "query_error": AthenaQueryError(
                    category=1,
                    error_type=2,
                    retryable=False,
                    message="terminal error",
                )
            },
        ),
        (QueryState.SUCCEEDED, {"error_text": "terminal error"}),
        (QueryState.FAILED, {"pane_state": PaneState.LOADING}),
        (QueryState.FAILED, {"error_text": "ownerless error"}),
        (QueryState.CANCELLED, {"pane_state": PaneState.ERROR}),
        (
            QueryState.CANCELLED,
            {
                "query_error": AthenaQueryError(
                    category=1,
                    error_type=2,
                    retryable=False,
                    message="impossible cancellation error",
                )
            },
        ),
    ],
)
async def test_query_snapshot_rejects_incoherent_terminal_vm_states(
    state: QueryState,
    changes: dict[str, object],
) -> None:
    fake = InMemoryAthena(
        executions=((_detail("q-app-1", QueryState.SUCCEEDED),),),
        result_pages={
            ("q-app-1", None): ResultPage((_COLUMN,), (("row",),), None),
        },
    )
    source = make_query_vm(fake)
    source.set_sql("SELECT 1")
    await source.execute()
    snapshot = source.export_snapshot()
    if state is not QueryState.SUCCEEDED:
        snapshot = replace(
            snapshot,
            state=state,
            results=replace(
                snapshot.results,
                execution_id=None,
                columns=(),
                rows=(),
                next_token=None,
                state=PaneState.EMPTY,
            ),
        )
    hostile = replace(snapshot, **changes)
    destination = make_query_vm(InMemoryAthena())

    with pytest.raises(ValueError, match=r"^Athena query snapshot is invalid$"):
        await destination.restore_snapshot(hostile)

    assert destination.execution_ref is None
    assert destination.state is None
    assert destination.pane_state is PaneState.EMPTY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "error"),
    [
        (QueryState.SUCCEEDED, None),
        (
            QueryState.FAILED,
            AthenaQueryError(
                category=1,
                error_type=2,
                retryable=False,
                message="query failed",
            ),
        ),
        (QueryState.CANCELLED, None),
    ],
)
async def test_query_snapshot_preserves_valid_terminal_states(
    state: QueryState,
    error: AthenaQueryError | None,
) -> None:
    fake = InMemoryAthena(
        executions=((_detail("q-app-1", state, error=error),),),
        result_pages={
            ("q-app-1", None): ResultPage((_COLUMN,), (("row",),), None),
        },
    )
    source = make_query_vm(fake)
    source.set_sql("SELECT 1")
    await source.execute()
    snapshot = source.export_snapshot()
    destination = make_query_vm(InMemoryAthena())

    await destination.restore_snapshot(snapshot)

    assert destination.state is state
    assert destination.query_error == error
    assert destination.pane_state is PaneState.IDLE
    assert destination.error_text is None
    expected_rows = (("row",),) if state is QueryState.SUCCEEDED else ()
    assert destination.results.rows == expected_rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state",
    [QueryState.SUCCEEDED, QueryState.FAILED, QueryState.CANCELLED],
)
async def test_query_snapshot_preserves_terminal_result_with_new_editor_validation_error(
    terminal_state: QueryState,
) -> None:
    source_client = InMemoryAthena(
        executions=((_detail("q-app-1", terminal_state),),),
        result_pages={
            ("q-app-1", None): ResultPage((_COLUMN,), (("row",),), None),
        },
    )
    source = make_query_vm(source_client)
    source.set_sql("SELECT 1")
    await source.execute()
    source.set_sql("DELETE FROM sales")
    await source.execute()

    assert source.validation_error is not None
    assert source.state is terminal_state
    expected_rows = (("row",),) if terminal_state is QueryState.SUCCEEDED else ()
    assert source.results.rows == expected_rows
    snapshot = source.export_snapshot()

    destination_client = InMemoryAthena()
    destination = make_query_vm(destination_client)
    destination.set_sql("SELECT 'temporary'")
    await destination.restore_snapshot(snapshot)

    assert destination.export_snapshot() == snapshot
    assert destination.validation_error == source.validation_error
    assert destination.state is terminal_state
    assert destination.results.rows == expected_rows
    assert destination.pane_state is PaneState.IDLE
    assert not destination.is_executing
    assert not destination.is_submitting
    assert destination_client.calls == []
    assert destination_client.start_calls == []
    assert destination_client.result_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("statistics", _STATS),
        (
            "query_error",
            AthenaQueryError(
                category=1,
                error_type=2,
                retryable=False,
                message="stale error",
            ),
        ),
        ("state_reason", "stale reason"),
        ("output_location", "s3://stale/output"),
        ("engine_version", "stale engine"),
    ],
)
async def test_query_snapshot_without_execution_rejects_execution_only_state(
    field: str,
    value: object,
) -> None:
    vm = make_query_vm(InMemoryAthena())
    vm.set_sql("DELETE FROM sales")
    await vm.execute()
    snapshot = vm.export_snapshot()
    assert snapshot.validation_error is not None
    hostile = replace(snapshot, **{field: value})

    with pytest.raises(ValueError, match=r"^Athena query snapshot is invalid$"):
        await vm.restore_snapshot(hostile)

    assert vm.export_snapshot() == snapshot


@pytest.mark.asyncio
async def test_query_snapshot_rejection_is_value_free_for_arbitrary_and_exact_payloads(
    tmp_path: Path,
) -> None:
    marker = "QUERY_SNAPSHOT_PAYLOAD_SECRET"

    class HostileSnapshot:
        def __repr__(self) -> str:
            return marker

    source = make_query_vm(InMemoryAthena())
    exact = replace(
        source.export_snapshot(),
        sql=marker,
        pane_state=PaneState.LOADING,
        error_text=marker,
    )
    assert repr(exact) == "AthenaQuerySnapshot()"

    for index, payload in enumerate((HostileSnapshot(), exact)):
        destination = make_query_vm(InMemoryAthena())
        error_text, rendered, crash = await _query_snapshot_failure_artifacts(
            destination,
            payload,
            tmp_path / f"crash-{index}",
        )

        assert error_text == "Athena query snapshot is invalid"
        assert marker not in rendered
        assert marker not in crash


def test_commands_follow_vmx_gating_and_disposal() -> None:
    fake = InMemoryAthena()
    vm = make_query_vm(fake)

    assert isinstance(vm.execute_command, AsyncRelayCommand)
    assert isinstance(vm.cancel_command, AsyncRelayCommand)
    assert not vm.execute_command.can_execute()
    assert not vm.cancel_command.can_execute()

    vm.set_sql("SELECT 1")
    assert vm.execute_command.can_execute()

    vm.set_sql("DELETE FROM sales")
    assert not vm.execute_command.can_execute()

    vm.dispose()
    assert not vm.execute_command.can_execute()
    assert not vm.cancel_command.can_execute()


@pytest.mark.asyncio
async def test_execute_command_requires_a_complete_context() -> None:
    vm = make_query_vm(InMemoryAthena())
    vm.set_sql("SELECT 1")

    await vm.set_context(QueryContext("prod-west", "us-west-2", "analysts", "", ""))

    assert not vm.execute_command.can_execute()


def test_cancel_command_requires_an_owned_active_query() -> None:
    vm = make_query_vm(InMemoryAthena())
    ref = QueryExecutionRef(
        "q-owned",
        _CONTEXT.connection_name,
        _CONTEXT.region,
        _CONTEXT.workgroup,
    )
    vm._busy = True  # type: ignore[attr-defined]
    vm._execution_ref = ref  # type: ignore[attr-defined]

    assert not vm.cancel_command.can_execute()

    vm._owns_active_query = True  # type: ignore[attr-defined]
    assert vm.cancel_command.can_execute()

    vm._execution_ref = None  # type: ignore[attr-defined]
    assert not vm.cancel_command.can_execute()


def test_request_token_fixture_is_independently_derived() -> None:
    material = "\0".join(
        (
            "prod-west",
            "us-west-2",
            "analysts",
            "AwsDataCatalog",
            "sales",
            "12345678123456781234567812345678",
        )
    ).encode()
    assert hashlib.sha256(material).hexdigest() == (
        "3a9dabffcee3720f9954443c8a8f719111653bd6650f78c0e8c1de49bec9e370"
    )
