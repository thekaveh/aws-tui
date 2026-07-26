"""Tests for reusable bounded Athena query execution."""

from __future__ import annotations

import asyncio
import traceback
from datetime import UTC, datetime
from pathlib import Path
from traceback import TracebackException
from typing import Any

import pytest

from aws_tui.domain.athena import ResultConfigurationRequiredError
from aws_tui.domain.athena_runner import (
    AthenaQueryCancelledError,
    AthenaQueryFailedError,
    AthenaQueryRunner,
    AthenaResultShapeError,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
    ResultPage,
)
from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.vm.athena._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState

pytestmark = pytest.mark.unit

CONTEXT = QueryContext(
    "dev",
    "us-east-1",
    "primary",
    "AwsDataCatalog",
    "analytics",
)
COLUMN = ResultColumn("snapshot_id", "bigint", "NULLABLE")
EMPTY_STATS = QueryStatistics(None, None, None, None, None, False)


def _detail(
    state: QueryState,
    *,
    context: QueryContext = CONTEXT,
    execution_id: str = "query-1",
) -> QueryExecutionDetail:
    ref = QueryExecutionRef(
        execution_id,
        context.connection_name,
        context.region,
        context.workgroup,
    )
    return QueryExecutionDetail(
        QueryExecutionSummary(
            ref,
            state,
            datetime(2026, 7, 26, tzinfo=UTC),
            datetime(2026, 7, 26, 0, 0, 1, tzinfo=UTC)
            if state in {QueryState.SUCCEEDED, QueryState.FAILED, QueryState.CANCELLED}
            else None,
            "DML",
        ),
        "provider detail must remain private",
        context,
        EMPTY_STATS,
        "s3://private-results/query-1.csv",
        "Athena engine version 3",
        None,
    )


class RunnerClient:
    def __init__(
        self,
        *,
        states: tuple[QueryExecutionDetail, ...],
        pages: dict[str | None, ResultPage] | None = None,
    ) -> None:
        self._states = list(states)
        self._pages = pages or {}
        self.start_calls: list[tuple[str, QueryContext, str]] = []
        self.poll_calls: list[str] = []
        self.stop_calls: list[str] = []
        self.result_calls: list[tuple[str, str | None]] = []

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        self.start_calls.append((sql, context, request_token))
        return QueryExecutionRef(
            "query-1",
            context.connection_name,
            context.region,
            context.workgroup,
        )

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        self.poll_calls.append(execution_id)
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]

    async def stop_query(self, execution_id: str) -> None:
        self.stop_calls.append(execution_id)

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        self.result_calls.append((execution_id, start_token))
        return self._pages[start_token]


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_runner_polls_and_stops_at_row_limit() -> None:
    client = RunnerClient(
        states=(
            _detail(QueryState.QUEUED),
            _detail(QueryState.RUNNING),
            _detail(QueryState.SUCCEEDED),
        ),
        pages={
            None: ResultPage(
                (COLUMN,),
                (("1",), ("2",), ("3",)),
                "page-2",
            ),
        },
    )
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    result = await runner.run(
        "SELECT snapshot_id FROM x LIMIT 100",
        CONTEXT,
        request_token="metadata-1",
        max_rows=2,
    )

    assert result.detail.summary.state is QueryState.SUCCEEDED
    assert result.columns == (COLUMN,)
    assert result.rows == (("1",), ("2",))
    assert client.result_calls == [("query-1", None)]
    assert len(client.start_calls) == 1


@pytest.mark.asyncio
async def test_runner_pages_only_until_bound_and_preserves_columns() -> None:
    client = RunnerClient(
        states=(_detail(QueryState.SUCCEEDED),),
        pages={
            None: ResultPage((COLUMN,), (("1",), ("2",)), "page-2"),
            "page-2": ResultPage((COLUMN,), (("3",), ("4",)), "page-3"),
            "page-3": ResultPage((COLUMN,), (("5",),), None),
        },
    )
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    result = await runner.run(
        "SELECT snapshot_id FROM x LIMIT 100",
        CONTEXT,
        request_token="metadata-2",
        max_rows=3,
    )

    assert result.rows == (("1",), ("2",), ("3",))
    assert client.result_calls == [
        ("query-1", None),
        ("query-1", "page-2"),
    ]


@pytest.mark.asyncio
async def test_runner_rejects_inconsistent_result_columns() -> None:
    other = ResultColumn("other", "varchar", "NULLABLE")
    client = RunnerClient(
        states=(_detail(QueryState.SUCCEEDED),),
        pages={
            None: ResultPage((COLUMN,), (("1",),), "page-2"),
            "page-2": ResultPage((other,), (("2",),), None),
        },
    )
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(AthenaResultShapeError, match="columns changed"):
        await runner.run(
            "SELECT snapshot_id FROM x LIMIT 100",
            CONTEXT,
            request_token="metadata-3",
            max_rows=2,
        )

    assert client.stop_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        (QueryState.FAILED, AthenaQueryFailedError),
        (QueryState.CANCELLED, AthenaQueryCancelledError),
    ],
)
async def test_runner_surfaces_terminal_failure_without_fetching_results(
    state: QueryState,
    error_type: type[Exception],
) -> None:
    client = RunnerClient(states=(_detail(state),))
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(error_type):
        await runner.run(
            "SELECT snapshot_id FROM x LIMIT 100",
            CONTEXT,
            request_token="metadata-4",
            max_rows=100,
        )

    assert client.result_calls == []


@pytest.mark.asyncio
async def test_runner_rejects_invalid_bounds_and_sql_before_dispatch() -> None:
    client = RunnerClient(states=(_detail(QueryState.SUCCEEDED),))
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(ValidationError, match="positive"):
        await runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-5",
            max_rows=0,
        )
    with pytest.raises(QueryRejectedError):
        await runner.run(
            "DELETE FROM x",
            CONTEXT,
            request_token="metadata-6",
            max_rows=1,
        )

    assert client.start_calls == []


@pytest.mark.asyncio
async def test_runner_rejects_execution_identity_mismatch_and_stops_query() -> None:
    wrong_context = QueryContext(
        "other",
        CONTEXT.region,
        CONTEXT.workgroup,
        CONTEXT.catalog,
        CONTEXT.database,
    )
    client = RunnerClient(states=(_detail(QueryState.RUNNING, context=wrong_context),))
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(ValidationError, match="active context"):
        await runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-7",
            max_rows=1,
        )

    assert client.stop_calls == ["query-1"]


class BlockingPollClient(RunnerClient):
    def __init__(self) -> None:
        super().__init__(states=(_detail(QueryState.RUNNING),))
        self.poll_started = asyncio.Event()

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        self.poll_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_runner_cancellation_stops_started_query_without_resubmission() -> None:
    client = BlockingPollClient()
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)
    task = asyncio.create_task(
        runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-8",
            max_rows=1,
        )
    )
    await client.poll_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(client.start_calls) == 1
    assert client.stop_calls == ["query-1"]


@pytest.mark.asyncio
async def test_bounded_result_repr_excludes_rows() -> None:
    client = RunnerClient(
        states=(_detail(QueryState.SUCCEEDED),),
        pages={
            None: ResultPage(
                (COLUMN,),
                (("RESULT_ROW_SECRET_7F4C2A9D",),),
                None,
            ),
        },
    )
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    result = await runner.run(
        "SELECT snapshot_id FROM x LIMIT 1",
        CONTEXT,
        request_token="metadata-9",
        max_rows=1,
    )

    assert "RESULT_ROW_SECRET_7F4C2A9D" not in repr(result)


class DelayedSubmissionClient(RunnerClient):
    def __init__(self) -> None:
        super().__init__(states=(_detail(QueryState.RUNNING),))
        self.accepted = asyncio.Event()
        self.release = asyncio.Event()

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        self.start_calls.append((sql, context, request_token))
        self.accepted.set()
        await self.release.wait()
        return QueryExecutionRef(
            "query-1",
            context.connection_name,
            context.region,
            context.workgroup,
        )


@pytest.mark.asyncio
async def test_runner_cancellation_finalizes_accepted_submission_and_stops_query() -> None:
    client = DelayedSubmissionClient()
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)
    task = asyncio.create_task(
        runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-delayed",
            max_rows=1,
        )
    )
    await client.accepted.wait()

    task.cancel()
    await asyncio.sleep(0)
    client.release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.stop_calls == ["query-1"]
    assert len(client.start_calls) == 1


class RepeatedCancellationClient(DelayedSubmissionClient):
    def __init__(self) -> None:
        super().__init__()
        self.stop_started = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        self.start_calls.append((sql, context, request_token))
        self.accepted.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                continue
        return QueryExecutionRef(
            "query-1",
            context.connection_name,
            context.region,
            context.workgroup,
        )

    async def stop_query(self, execution_id: str) -> None:
        self.stop_calls.append(execution_id)
        self.stop_started.set()
        while not self.release_stop.is_set():
            try:
                await self.release_stop.wait()
            except asyncio.CancelledError:
                continue


@pytest.mark.asyncio
async def test_runner_repeated_cancellation_waits_for_owned_submission_cleanup() -> None:
    client = RepeatedCancellationClient()
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)
    task = asyncio.create_task(
        runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-repeated-cancel",
            max_rows=1,
        )
    )
    await asyncio.wait_for(client.accepted.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    client.release.set()
    await asyncio.wait_for(client.stop_started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    assert len(client.start_calls) == 1
    assert client.stop_calls == ["query-1"]

    client.release_stop.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert not runner._submission_finalizers


@pytest.mark.asyncio
async def test_runner_shutdown_style_finalizer_cancellation_cannot_orphan_query() -> None:
    client = RepeatedCancellationClient()
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)
    task = asyncio.create_task(
        runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-loop-shutdown",
            max_rows=1,
        )
    )
    await asyncio.wait_for(client.accepted.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)
    assert len(runner._submission_finalizers) == 1
    finalizer = next(iter(runner._submission_finalizers))
    submission = next(
        candidate
        for candidate in asyncio.all_tasks()
        if "RepeatedCancellationClient.start_query"
        in getattr(candidate.get_coro(), "__qualname__", "")
    )
    submission.cancel()
    finalizer.cancel()
    client.release.set()
    await asyncio.wait_for(client.stop_started.wait(), timeout=1)
    for candidate in tuple(asyncio.all_tasks()):
        if "AthenaQueryRunner._best_effort_stop" in getattr(
            candidate.get_coro(),
            "__qualname__",
            "",
        ):
            candidate.cancel()
    next(iter(runner._submission_finalizers)).cancel()
    await asyncio.sleep(0)

    assert not task.done()
    assert client.stop_calls == ["query-1"]

    client.release_stop.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert finalizer.done()
    assert not runner._submission_finalizers


class PollFailureClient(RunnerClient):
    def __init__(self, error: Exception) -> None:
        super().__init__(states=(_detail(QueryState.RUNNING),))
        self.error = error

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        self.poll_calls.append(execution_id)
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderError("provider poll secret"),
        RuntimeError("unexpected poll secret"),
    ],
)
async def test_runner_best_effort_stops_nonterminal_query_after_poll_failure(
    error: Exception,
) -> None:
    client = PollFailureClient(error)
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(ProviderError, match="status request failed"):
        await runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-poll-failure",
            max_rows=1,
        )

    assert client.stop_calls == ["query-1"]


@pytest.mark.asyncio
async def test_runner_rejects_nonprogressing_continuation_page() -> None:
    client = RunnerClient(
        states=(_detail(QueryState.SUCCEEDED),),
        pages={
            None: ResultPage((COLUMN,), (), "unique-page-2"),
        },
    )
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(AthenaResultShapeError, match="did not advance"):
        await runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-empty-page",
            max_rows=2,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page",
    [
        object(),
        ResultPage([], (), None),  # type: ignore[arg-type]
        ResultPage((object(),), (), None),  # type: ignore[arg-type]
        ResultPage((COLUMN,), [], None),  # type: ignore[arg-type]
        ResultPage((COLUMN,), ([None],), None),  # type: ignore[arg-type]
        ResultPage((COLUMN,), ((7,),), None),  # type: ignore[arg-type]
        ResultPage((COLUMN,), (), 7),  # type: ignore[arg-type]
    ],
)
async def test_runner_rejects_malformed_result_pages_without_incidental_errors(
    page: Any,
) -> None:
    client = RunnerClient(
        states=(_detail(QueryState.SUCCEEDED),),
        pages={None: page},
    )
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(AthenaResultShapeError):
        await runner.run(
            "SELECT 1",
            CONTEXT,
            request_token="metadata-malformed-page",
            max_rows=2,
        )


class FailurePhaseClient(RunnerClient):
    def __init__(self, phase: str, marker: str) -> None:
        state = QueryState.FAILED if phase == "terminal" else QueryState.SUCCEEDED
        super().__init__(
            states=(_detail(state),),
            pages={
                None: ResultPage(
                    (COLUMN,),
                    ((marker,),),
                    marker,
                )
            },
        )
        self.phase = phase
        self.marker = marker

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        if self.phase == "start":
            raise ProviderError(self.marker)
        return await super().start_query(sql, context, request_token=request_token)

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        if self.phase == "poll":
            raise ProviderError(self.marker)
        if self.phase == "terminal":
            detail = _detail(QueryState.FAILED)
            object.__setattr__(detail, "state_reason", self.marker)
            object.__setattr__(detail, "output_location", f"s3://private/{self.marker}")
            return detail
        return await super().get_query_execution(execution_id)

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        if self.phase == "fetch":
            raise ProviderError(self.marker)
        if self.phase == "shape":
            return ResultPage(
                (COLUMN,),
                ((self.marker, "wrong-width"),),
                self.marker,
            )
        return await super().get_results_page(execution_id, start_token=start_token)


def _assert_runner_failure_is_private(
    error: BaseException,
    *,
    crash_dir: Path,
    secrets: tuple[str, ...],
) -> None:
    production_traceback = error.__traceback__
    while (
        production_traceback is not None
        and "/src/aws_tui/" not in production_traceback.tb_frame.f_code.co_filename
    ):
        production_traceback = production_traceback.tb_next
    error = error.with_traceback(production_traceback)

    exception_graph: list[BaseException] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        exception_graph.append(current)
        pending.extend(
            linked for linked in (current.__context__, current.__cause__) if linked is not None
        )
        pending.extend(item for item in current.args if isinstance(item, BaseException))

    rendered_with_locals = "".join(
        TracebackException.from_exception(error, capture_locals=True).format()
    )
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    crash_path = CrashDump(base_dir=crash_dir).write(exc=error)
    visible = "\n".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            repr(exception_graph),
            rendered_with_locals,
            rendered,
            crash_path.read_text(encoding="utf-8"),
        )
    )
    for secret in secrets:
        assert secret not in visible


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "poll", "terminal", "fetch", "shape"])
async def test_runner_failure_boundaries_exclude_sql_results_and_provider_values(
    phase: str,
    tmp_path: Path,
) -> None:
    sql_secret = "private_fixture_value"
    provider_secret = "provider_fixture_value"
    client = FailurePhaseClient(phase, provider_secret)
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises((ProviderError, AthenaResultShapeError)) as raised:
        await runner.run(
            f"SELECT '{sql_secret}'",
            CONTEXT,
            request_token="metadata-private-failure",
            max_rows=2,
        )

    _assert_runner_failure_is_private(
        raised.value,
        crash_dir=tmp_path / phase,
        secrets=(sql_secret, provider_secret),
    )


class ProviderTypedFailureClient(RunnerClient):
    def __init__(self, phase: str, error: Exception) -> None:
        super().__init__(
            states=(_detail(QueryState.SUCCEEDED),),
            pages={None: ResultPage((COLUMN,), (("1",),), None)},
        )
        self.phase = phase
        self.error = error

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
    ) -> QueryExecutionRef:
        if self.phase == "start":
            raise self.error
        return await super().start_query(sql, context, request_token=request_token)

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        if self.phase == "poll":
            raise self.error
        return await super().get_query_execution(execution_id)

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        if self.phase == "results":
            raise self.error
        return await super().get_results_page(execution_id, start_token=start_token)


class ProviderValidationLookalike(ValidationError):
    """Provider-owned validation subtype that must not be trusted."""


class ProviderShapeLookalike(AthenaResultShapeError):
    """Provider-owned shape subtype that must not be trusted."""


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "poll", "results"])
@pytest.mark.parametrize(
    ("provider_error_type", "public_error_type"),
    [
        (ValidationError, ValidationError),
        (ProviderValidationLookalike, ValidationError),
        (AthenaResultShapeError, AthenaResultShapeError),
        (ProviderShapeLookalike, AthenaResultShapeError),
    ],
)
async def test_provider_validation_and_shape_errors_use_owned_safe_messages(
    phase: str,
    provider_error_type: type[ProviderError],
    public_error_type: type[ProviderError],
    tmp_path: Path,
) -> None:
    sql_secret = "SQL_PROVIDER_TYPED_SECRET_7F4C2A9D"
    provider_secret = "PROVIDER_TYPED_SECRET_7F4C2A9D"
    provider_error = provider_error_type(provider_secret)
    provider_error.__cause__ = RuntimeError(provider_secret)
    client = ProviderTypedFailureClient(phase, provider_error)
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(public_error_type) as raised:
        await runner.run(
            f"SELECT '{sql_secret}'",
            CONTEXT,
            request_token="metadata-provider-typed",
            max_rows=1,
        )

    expected_operation = {
        "start": "query start",
        "poll": "query status request",
        "results": "results request",
    }[phase]
    assert expected_operation in str(raised.value)
    _assert_runner_failure_is_private(
        raised.value,
        crash_dir=tmp_path / f"{phase}-{provider_error_type.__name__}",
        secrets=(sql_secret, provider_secret),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "poll", "results"])
@pytest.mark.parametrize(
    ("error_type", "expected_state"),
    [
        (AuthRequiredError, PaneState.AUTH_REQUIRED),
        (PermissionDeniedError, PaneState.FORBIDDEN),
        (ThrottledError, PaneState.ERROR),
        (ProviderUnreachableError, PaneState.UNREACHABLE),
        (ResultConfigurationRequiredError, PaneState.ERROR),
        (NotFoundError, PaneState.ERROR),
    ],
)
async def test_runner_preserves_provider_taxonomy_with_private_phase_errors(
    phase: str,
    error_type: type[ProviderError],
    expected_state: PaneState,
    tmp_path: Path,
) -> None:
    sql_secret = "SQL_TAXONOMY_SECRET_7F4C2A9D"
    provider_secret = "PROVIDER_TAXONOMY_SECRET_7F4C2A9D"
    client = ProviderTypedFailureClient(phase, error_type(provider_secret))
    runner = AthenaQueryRunner(client, ReadOnlySqlPolicy(), sleep=_no_sleep)

    with pytest.raises(error_type) as raised:
        await runner.run(
            f"SELECT '{sql_secret}'",
            CONTEXT,
            request_token="metadata-provider-taxonomy",
            max_rows=1,
        )

    state, _ = map_provider_error(raised.value, fallback="fallback")
    assert state is expected_state
    expected_operation = {
        "start": "query start",
        "poll": "query status request",
        "results": "results request",
    }[phase]
    assert expected_operation in str(raised.value)
    _assert_runner_failure_is_private(
        raised.value,
        crash_dir=tmp_path / f"{phase}-{error_type.__name__}",
        secrets=(sql_secret, provider_secret),
    )
