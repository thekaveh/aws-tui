from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.demo.seeds import seeded_demo_athena
from aws_tui.domain.filesystem import PermissionDeniedError, ValidationError
from aws_tui.domain.query import QueryContext, QueryState
from aws_tui.domain.sql_policy import QueryRejectedError

DEV_CONTEXT = QueryContext(
    "demo-dev",
    "us-east-1",
    "dev-analytics",
    "DevDataCatalog",
    "dev_events",
)


@pytest.mark.asyncio
async def test_demo_profiles_have_disjoint_athena_resources() -> None:
    dev = seeded_demo_athena("demo-dev")
    prod = seeded_demo_athena("demo-prod")

    dev_workgroups, _ = await dev.list_workgroups_page()
    prod_workgroups, _ = await prod.list_workgroups_page()
    dev_catalogs, _ = await dev.list_catalogs_page(workgroup="dev-analytics")
    prod_catalogs, _ = await prod.list_catalogs_page(workgroup="prod-reporting")
    dev_databases, _ = await dev.list_databases_page(
        "DevDataCatalog",
        workgroup="dev-analytics",
    )
    prod_databases, _ = await prod.list_databases_page(
        "ProdDataCatalog",
        workgroup="prod-reporting",
    )
    dev_history, _ = await dev.list_query_executions_page("dev-analytics")
    prod_history, _ = await prod.list_query_executions_page("prod-reporting")
    dev_named_ids, _ = await dev.list_named_queries_page("dev-analytics")
    prod_named_ids, _ = await prod.list_named_queries_page("prod-reporting")
    dev_prepared, _ = await dev.list_prepared_statements_page("dev-analytics")
    prod_prepared, _ = await prod.list_prepared_statements_page("prod-reporting")

    assert {row.name for row in dev_workgroups} == {"dev-analytics", "dev-empty"}
    assert {row.name for row in prod_workgroups} == {"prod-reporting"}
    assert {row.name for row in dev_catalogs} == {"DevDataCatalog"}
    assert {row.name for row in prod_catalogs} == {"ProdDataCatalog"}
    assert {row.ref.database_name for row in dev_databases} == {"dev_events"}
    assert {row.ref.database_name for row in prod_databases} == {"prod_sales"}
    assert {row.execution_id for row in dev_history}.isdisjoint(
        row.execution_id for row in prod_history
    )
    assert set(dev_named_ids).isdisjoint(prod_named_ids)
    assert {row.name for row in dev_prepared}.isdisjoint(row.name for row in prod_prepared)


@pytest.mark.asyncio
async def test_seeded_demo_athena_returns_fresh_profile_local_instances() -> None:
    first = seeded_demo_athena("demo-dev")
    second = seeded_demo_athena("demo-dev")

    await first.start_query("SELECT 1", DEV_CONTEXT, request_token="token-one")

    first_history, _ = await first.list_query_executions_page("dev-analytics")
    second_history, _ = await second.list_query_executions_page("dev-analytics")

    assert first is not second
    assert len(first_history) == len(second_history) + 1
    assert all(not row.execution_id.startswith("demo-dev-app-") for row in second_history)


@pytest.mark.asyncio
async def test_demo_query_walks_to_success_deterministically() -> None:
    fake = seeded_demo_athena("demo-dev")

    ref = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token="deterministic-token",
    )
    states = [(await fake.get_query_execution(ref.execution_id)).summary.state for _ in range(3)]

    assert states == [QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED]
    assert ref.connection_name == "demo-dev"
    assert ref.region == "us-east-1"
    assert ref.workgroup == "dev-analytics"


@pytest.mark.asyncio
async def test_only_app_started_queries_advance_and_tokens_are_idempotent() -> None:
    fake = seeded_demo_athena("demo-dev")
    historical_before = await fake.get_query_execution("q-dev-running")
    historical_after = await fake.get_query_execution("q-dev-running")

    first = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token="same-token",
    )
    second = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token="same-token",
    )

    assert historical_before == historical_after
    assert historical_after.summary.state is QueryState.RUNNING
    assert first == second
    history, _ = await fake.list_query_executions_page("dev-analytics")
    assert sum(row.execution_id == first.execution_id for row in history) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "context", "output_location"),
    [
        ("SELECT 2", DEV_CONTEXT, None),
        (
            "SELECT 1",
            replace(DEV_CONTEXT, catalog="OtherCatalog"),
            None,
        ),
        (
            "SELECT 1",
            replace(DEV_CONTEXT, database="other_database"),
            None,
        ),
        (
            "SELECT 1",
            replace(DEV_CONTEXT, workgroup="dev-empty"),
            None,
        ),
        ("SELECT 1", DEV_CONTEXT, "s3://caller-results/OUTPUT_SECRET/"),
    ],
)
async def test_request_token_reuse_rejects_any_changed_request_parameter_safely(
    sql: str,
    context: QueryContext,
    output_location: str | None,
) -> None:
    fake = seeded_demo_athena("demo-dev")
    token = "TOKEN_SECRET"
    await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token=token,
    )

    with pytest.raises(ValidationError) as captured:
        await fake.start_query(
            sql,
            context,
            request_token=token,
            output_location=output_location,
        )

    rendered = f"{captured.value!r}\n{fake!r}"
    assert "TOKEN_SECRET" not in rendered
    assert "OUTPUT_SECRET" not in rendered
    assert "SELECT 1" not in rendered
    assert "SELECT 2" not in rendered


@pytest.mark.asyncio
async def test_start_query_rejects_unsafe_sql_before_creating_execution() -> None:
    fake = seeded_demo_athena("demo-dev")
    before = set(fake.query_executions)

    with pytest.raises(QueryRejectedError):
        await fake.start_query(
            "DROP TABLE sensitive_events",
            DEV_CONTEXT,
            request_token="unsafe-token",
        )

    assert set(fake.query_executions) == before


@pytest.mark.asyncio
async def test_enforced_workgroup_output_cannot_be_overridden_by_caller() -> None:
    fake = seeded_demo_athena("demo-dev")

    ref = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token="enforced-output",
        output_location="s3://caller-results/OUTPUT_SECRET/",
    )
    detail = fake.query_executions[ref.execution_id]

    assert detail.output_location == (f"s3://athena-results/dev/{ref.execution_id}.csv")
    assert "OUTPUT_SECRET" not in repr(fake)


@pytest.mark.asyncio
async def test_non_enforced_workgroup_accepts_caller_output_configuration() -> None:
    fake = InMemoryAthena(connection_name="demo-dev", region="us-east-1")
    fake.add_workgroup(
        "caller-configured",
        output_location="s3://workgroup-results/default/",
        enforce_workgroup_configuration=False,
    )
    context = replace(DEV_CONTEXT, workgroup="caller-configured")

    ref = await fake.start_query(
        "SELECT 1",
        context,
        request_token="caller-output",
        output_location="s3://caller-results/explicit/",
    )

    assert fake.query_executions[ref.execution_id].output_location == (
        f"s3://caller-results/explicit/{ref.execution_id}.csv"
    )


@pytest.mark.asyncio
async def test_fake_exposes_terminal_empty_denied_and_missing_output_scenarios() -> None:
    fake = seeded_demo_athena("demo-dev")

    succeeded = await fake.get_query_execution("q-dev-succeeded")
    running = await fake.get_query_execution("q-dev-running")
    failed = await fake.get_query_execution("q-dev-failed")
    empty = await fake.get_results_page("q-dev-empty")
    missing_output = await fake.get_query_execution("q-dev-missing-output")

    assert succeeded.summary.state is QueryState.SUCCEEDED
    assert running.summary.state is QueryState.RUNNING
    assert failed.summary.state is QueryState.FAILED
    assert failed.error is not None
    assert empty.rows == ()
    assert missing_output.output_location is None
    with pytest.raises(PermissionDeniedError, match="result access denied"):
        await fake.get_results_page("q-dev-access-denied")

    prod = seeded_demo_athena("demo-prod")
    cancelled = await prod.get_query_execution("q-prod-cancelled")
    assert cancelled.summary.state is QueryState.CANCELLED

    shared = seeded_demo_athena("demo-shared")
    with pytest.raises(PermissionDeniedError, match="Athena access denied"):
        await shared.list_workgroups_page()


@pytest.mark.asyncio
async def test_empty_workgroup_has_no_history_or_saved_queries() -> None:
    fake = seeded_demo_athena("demo-dev")

    history, history_token = await fake.list_query_executions_page("dev-empty")
    named, named_token = await fake.list_named_queries_page("dev-empty")
    prepared, prepared_token = await fake.list_prepared_statements_page("dev-empty")

    assert (history, history_token) == ([], None)
    assert (named, named_token) == ([], None)
    assert (prepared, prepared_token) == ([], None)


@pytest.mark.asyncio
async def test_stop_is_limited_to_active_app_started_queries() -> None:
    fake = seeded_demo_athena("demo-dev")
    ref = await fake.start_query("SELECT 1", DEV_CONTEXT, request_token="stop-token")

    await fake.stop_query(ref.execution_id)

    detail = await fake.get_query_execution(ref.execution_id)
    assert detail.summary.state is QueryState.CANCELLED
    with pytest.raises(ValidationError, match="active app-started"):
        await fake.stop_query("q-dev-running")
    with pytest.raises(ValidationError, match="active app-started"):
        await fake.stop_query(ref.execution_id)


@pytest.mark.asyncio
async def test_fake_records_all_calls_without_repr_leaking_sql_results_or_tokens() -> None:
    fake = seeded_demo_athena("demo-dev")
    sql_secret = "SELECT 'SQL_SECRET'"
    token_secret = "TOKEN_SECRET"

    ref = await fake.start_query(
        sql_secret,
        DEV_CONTEXT,
        request_token=token_secret,
    )
    await fake.get_query_execution(ref.execution_id)
    await fake.get_results_page("q-dev-succeeded")

    assert [call.method for call in fake.calls[-3:]] == [
        "start_query",
        "get_query_execution",
        "get_results_page",
    ]
    start_call = fake.calls[-3]
    assert start_call.arguments[0] == sql_secret
    assert start_call.arguments[2] == token_secret
    rendered = repr(fake)
    assert sql_secret not in rendered
    assert token_secret not in rendered
    assert "Ada" not in rendered


@pytest.mark.asyncio
async def test_fake_creates_no_background_tasks() -> None:
    fake = seeded_demo_athena("demo-dev")
    current = asyncio.current_task()
    before = {task for task in asyncio.all_tasks() if task is not current}

    ref = await fake.start_query("SELECT 1", DEV_CONTEXT, request_token="task-check")
    await fake.get_query_execution(ref.execution_id)
    await fake.get_results_page("q-dev-succeeded")

    after = {task for task in asyncio.all_tasks() if task is not current}
    assert after == before


def test_in_memory_athena_has_no_module_global_mutable_instances() -> None:
    first = InMemoryAthena(connection_name="one", region="us-east-1")
    second = InMemoryAthena(connection_name="two", region="us-west-2")

    assert first.calls is not second.calls
    assert first.query_executions is not second.query_executions
