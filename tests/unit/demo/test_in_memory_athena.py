from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.demo.seeds import seeded_demo_athena, seeded_demo_fs
from aws_tui.domain.athena_runner import AthenaQueryRunner
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import (
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ValidationError,
)
from aws_tui.domain.iceberg import IcebergInspector
from aws_tui.domain.query import QueryContext, QueryState, ResultColumn
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy
from aws_tui.infra.crash_dump import CrashDump

DEV_CONTEXT = QueryContext(
    "demo-dev",
    "us-east-1",
    "dev-analytics",
    "DevDataCatalog",
    "dev_events",
)


async def _read_bytes(fake, path: PathRef) -> bytes:  # type: ignore[no-untyped-def]
    chunks = await fake.read_stream(path)
    return b"".join([chunk async for chunk in chunks])


async def _advance_to_success(fake: InMemoryAthena, execution_id: str) -> None:
    states = [(await fake.get_query_execution(execution_id)).summary.state for _ in range(3)]
    assert states == [QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED]


async def _no_sleep(_: float) -> None:
    return None


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
    assert {row.name for row in dev_catalogs} == {"DevDataCatalog", "AwsDataCatalog"}
    assert {row.name for row in prod_catalogs} == {"ProdDataCatalog", "AwsDataCatalog"}
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
    fake.add_catalog("caller-configured", context.catalog)
    fake.add_database("caller-configured", context.catalog, context.database)
    fake.add_query_result(
        "SELECT 1",
        context,
        columns=(ResultColumn("_col0", "integer", "NULLABLE"),),
        rows=(("1",),),
    )

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
    shared_workgroups, _ = await shared.list_workgroups_page()
    assert [(row.name, row.state) for row in shared_workgroups] == [
        ("shared-retired", "DISABLED"),
        ("shared-insights", "ENABLED"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "region", "workgroup", "database", "table", "snapshot_id", "rows"),
    [
        (
            "demo-dev",
            "us-east-1",
            "dev-analytics",
            "dev_analytics",
            "dev_events_iceberg",
            4201,
            (
                ("2026-07-24T12:00:00Z", "dev-checkout", "17"),
                ("2026-07-24T12:05:00Z", "dev-search", "9"),
            ),
        ),
        (
            "demo-prod",
            "us-east-1",
            "prod-reporting",
            "prod_warehouse",
            "prod_sales_iceberg",
            7701,
            (
                ("2026-07-25", "us-east-1", "1048576.25"),
                ("2026-07-25", "eu-west-1", "524288.50"),
            ),
        ),
        (
            "demo-shared",
            "us-west-2",
            "shared-insights",
            "shared_lake",
            "shared_metrics_iceberg",
            9901,
            (
                ("platform", "query_latency_ms", "84.0"),
                ("platform", "freshness_minutes", "6.0"),
            ),
        ),
    ],
)
async def test_seeded_iceberg_time_travel_query_returns_exact_profile_rows(
    profile: str,
    region: str,
    workgroup: str,
    database: str,
    table: str,
    snapshot_id: int,
    rows: tuple[tuple[str, str, str], ...],
) -> None:
    fake = seeded_demo_athena(profile)
    context = QueryContext(
        profile,
        region,
        workgroup,
        "AwsDataCatalog",
        database,
    )
    sql = (
        f'SELECT * FROM "AwsDataCatalog"."{database}"."{table}" '
        f"FOR VERSION AS OF {snapshot_id} LIMIT 100"
    )

    ref = await fake.start_query(
        sql,
        context,
        request_token="iceberg-time-travel",
    )
    for _ in range(3):
        detail = await fake.get_query_execution(ref.execution_id)
    page = await fake.get_results_page(ref.execution_id)

    assert detail.summary.state is QueryState.SUCCEEDED
    assert page.rows == rows
    assert detail.output_location is not None
    assert detail.output_location.startswith(
        {
            "demo-dev": "s3://athena-results/dev/",
            "demo-prod": "s3://athena-results/prod/",
            "demo-shared": "s3://athena-results/shared/",
        }[profile]
    )


@pytest.mark.asyncio
async def test_success_artifact_serializes_every_result_page_as_exact_csv() -> None:
    store = seeded_demo_fs("demo-dev")
    fake = seeded_demo_athena("demo-dev", result_store=store)
    fake.page_size = 1
    sql = "SELECT metric, metric, note FROM artifact_fixture"
    columns = (
        ResultColumn("metric", "varchar", "NULLABLE"),
        ResultColumn("metric", "varchar", "NULLABLE"),
        ResultColumn("note", "varchar", "NULLABLE"),
    )
    rows = (
        ("alpha", None, 'comma, quote " and newline\nkept'),
        ("beta", "", "plain"),
    )
    fake.add_query_result(sql, DEV_CONTEXT, columns=columns, rows=rows)

    ref = await fake.start_query(sql, DEV_CONTEXT, request_token="artifact-pages")
    await _advance_to_success(fake, ref.execution_id)
    first = await fake.get_results_page(ref.execution_id)
    assert first.rows == rows[:1]
    assert first.next_token is not None
    second = await fake.get_results_page(
        ref.execution_id,
        start_token=first.next_token,
    )
    assert second.rows == rows[1:]
    assert second.next_token is None

    path = PathRef.from_posix(
        f"/athena-results/dev/{ref.execution_id}.csv",
    )
    assert await _read_bytes(store, path) == (
        b'metric,metric,note\r\nalpha,,"comma, quote "" and newline\nkept"\r\nbeta,,plain\r\n'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "execution_id", "path", "expected"),
    [
        (
            "demo-dev",
            "q-dev-succeeded",
            "/athena-results/dev/q-dev-succeeded.csv",
            b"value,metric\r\nAda,42\r\nLin,\r\n",
        ),
        (
            "demo-dev",
            "q-dev-empty",
            "/athena-results/dev/q-dev-empty.csv",
            b"value,metric\r\n",
        ),
        (
            "demo-prod",
            "q-prod-succeeded",
            "/athena-results/prod/q-prod-succeeded.csv",
            b"value,metric\r\n2026-07-25,1048576\r\n",
        ),
    ],
)
async def test_seeded_success_artifact_matches_historical_result_page(
    profile: str,
    execution_id: str,
    path: str,
    expected: bytes,
) -> None:
    store = seeded_demo_fs(profile)
    fake = seeded_demo_athena(profile, result_store=store)
    result_path = PathRef.from_posix(path)

    assert await _read_bytes(store, result_path) == expected
    detail = await fake.get_query_execution(execution_id)
    assert detail.summary.state is QueryState.SUCCEEDED
    assert await _read_bytes(store, result_path) == expected


@pytest.mark.asyncio
async def test_result_tokens_are_exact_bounded_and_execution_owned() -> None:
    fake = seeded_demo_athena("demo-dev")
    fake.page_size = 1
    columns = (ResultColumn("value", "varchar", "NULLABLE"),)
    fake.add_query_result(
        "SELECT value FROM token_fixture_one",
        DEV_CONTEXT,
        columns=columns,
        rows=(("one",), ("two",)),
    )
    fake.add_query_result(
        "SELECT value FROM token_fixture_two",
        DEV_CONTEXT,
        columns=columns,
        rows=(("three",), ("four",)),
    )
    first_ref = await fake.start_query(
        "SELECT value FROM token_fixture_one",
        DEV_CONTEXT,
        request_token="token-owner-one",
    )
    second_ref = await fake.start_query(
        "SELECT value FROM token_fixture_two",
        DEV_CONTEXT,
        request_token="token-owner-two",
    )
    first_page = await fake.get_results_page(first_ref.execution_id)
    second_page = await fake.get_results_page(second_ref.execution_id)
    assert first_page.next_token is not None
    assert second_page.next_token is not None
    assert first_page.next_token != second_page.next_token
    assert (
        await fake.get_results_page(
            first_ref.execution_id,
            start_token=first_page.next_token,
        )
    ).rows == (("two",),)

    invalid_tokens = (
        "",
        "0",
        "1",
        "-1",
        "9" * 500,
        second_page.next_token,
        cast(str, True),
        cast(str, 1),
        cast(str, object()),
    )
    for token in invalid_tokens:
        with pytest.raises(ValidationError, match="pagination token"):
            await fake.get_results_page(first_ref.execution_id, start_token=token)
    with pytest.raises(ValidationError, match="execution"):
        await fake.get_results_page(cast(str, []))


@pytest.mark.asyncio
async def test_start_query_rejects_non_string_sql_without_python_type_error() -> None:
    fake = seeded_demo_athena("demo-dev")
    before = set(fake.query_executions)

    with pytest.raises(ValidationError, match="SQL"):
        await fake.start_query(
            cast(str, object()),
            DEV_CONTEXT,
            request_token="malformed-sql",
        )

    assert set(fake.query_executions) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "context"),
    [
        ("SELECT 404", DEV_CONTEXT),
        ("SELECT 1", replace(DEV_CONTEXT, workgroup="")),
        ("SELECT 1", replace(DEV_CONTEXT, catalog="")),
        ("SELECT 1", replace(DEV_CONTEXT, database="")),
        ("SELECT 1", replace(DEV_CONTEXT, workgroup="dev-empty")),
        ("SELECT 1", replace(DEV_CONTEXT, catalog="ProdDataCatalog")),
        ("SELECT 1", replace(DEV_CONTEXT, database="prod_warehouse")),
        (
            'SELECT * FROM "AwsDataCatalog"."prod_warehouse"."prod_sales_iceberg" '
            "FOR VERSION AS OF 7701 LIMIT 100",
            replace(
                DEV_CONTEXT,
                catalog="AwsDataCatalog",
                database="prod_warehouse",
            ),
        ),
        ("SELECT 1", replace(DEV_CONTEXT, database=cast(str, object()))),
    ],
)
async def test_unknown_query_context_fails_closed_without_execution_or_artifact(
    tmp_path: Path,
    sql: str,
    context: QueryContext,
) -> None:
    store = seeded_demo_fs("demo-dev")
    fake = seeded_demo_athena("demo-dev", result_store=store)
    before_executions = set(fake.query_executions)
    before_results = set(fake.result_pages)
    before_tree = set(store._tree)

    with pytest.raises((NotFoundError, ValidationError)) as captured:
        await fake.start_query(
            sql,
            context,
            request_token="PRIVATE_REQUEST_TOKEN",
        )

    assert set(fake.query_executions) == before_executions
    assert set(fake.result_pages) == before_results
    assert set(store._tree) == before_tree
    rendered = f"{captured.value!r}\n{fake!r}"
    assert "PRIVATE_REQUEST_TOKEN" not in rendered
    assert sql not in rendered
    crash_path = CrashDump(base_dir=tmp_path / "crash").write(exc=captured.value)
    crash = crash_path.read_text(encoding="utf-8")
    assert "PRIVATE_REQUEST_TOKEN" not in crash
    assert sql not in crash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "profile",
        "alias",
        "region",
        "workgroup",
        "database",
        "table",
        "profile_marker",
    ),
    [
        (
            "demo-dev",
            "runtime-dev",
            "us-east-1",
            "dev-analytics",
            "dev_analytics",
            "dev_events_iceberg",
            "demo-dev",
        ),
        (
            "demo-shared",
            "runtime-shared",
            "us-west-2",
            "shared-insights",
            "shared_lake",
            "shared_metrics_iceberg",
            "demo-shared",
        ),
    ],
)
async def test_runtime_alias_keeps_runtime_identity_but_uses_seeded_storage_namespace(
    profile: str,
    alias: str,
    region: str,
    workgroup: str,
    database: str,
    table: str,
    profile_marker: str,
) -> None:
    store = seeded_demo_fs(profile)
    fake = seeded_demo_athena(
        profile,
        connection_name=alias,
        region=region,
        result_store=store,
    )
    context = QueryContext(
        alias,
        region,
        workgroup,
        "AwsDataCatalog",
        database,
    )
    table_ref = TableRef(
        "AwsDataCatalog",
        database,
        table,
        alias,
        region,
    )
    inspector = IcebergInspector(
        runner=AthenaQueryRunner(
            fake,
            ReadOnlySqlPolicy(),
            sleep=_no_sleep,
        ),
        context=context,
    )

    snapshots = await inspector.list_snapshots(table_ref)
    manifests = await inspector.list_manifests(table_ref)
    files = await inspector.list_files(table_ref)
    storage_uris = (
        *(snapshot.manifest_list for snapshot in snapshots),
        *(manifest.path for manifest in manifests),
        *(data_file.file_path for data_file in files),
        *(
            value
            for seeded in fake._seeded_query_results.values()
            for page in seeded.pages
            for row in page.rows
            for value in row
            if value is not None and value.startswith("s3://")
        ),
    )
    result_uris = (
        *(
            detail.output_location
            for detail in fake.query_executions.values()
            if detail.summary.ref.execution_id.startswith(f"{alias}-app-")
            and detail.output_location is not None
        ),
    )
    uris = (*storage_uris, *result_uris)

    assert uris
    assert result_uris
    assert all(profile_marker in uri for uri in storage_uris)
    assert all(alias not in uri for uri in storage_uris)
    assert all(table_ref.connection_name == alias for _ in uris)
    for uri in uris:
        location = parse_s3_uri(uri)
        assert location is not None
        path = PathRef(
            (
                location.bucket,
                *location.path.removeprefix("/").split("/"),
            )
        )
        await store.stat(path)


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

    with pytest.raises(ValidationError):
        await fake.start_query(
            sql_secret,
            DEV_CONTEXT,
            request_token=token_secret,
        )
    await fake.get_results_page("q-dev-succeeded")

    assert [call.method for call in fake.calls[-2:]] == [
        "start_query",
        "get_results_page",
    ]
    start_call = fake.calls[-2]
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
