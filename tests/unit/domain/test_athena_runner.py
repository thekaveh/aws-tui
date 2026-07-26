"""Tests for reusable bounded Athena query execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from aws_tui.domain.athena_runner import (
    AthenaQueryCancelledError,
    AthenaQueryFailedError,
    AthenaQueryRunner,
    AthenaResultShapeError,
)
from aws_tui.domain.filesystem import ValidationError
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
