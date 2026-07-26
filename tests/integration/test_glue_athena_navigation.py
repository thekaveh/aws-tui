from __future__ import annotations

import asyncio
import contextlib
import inspect
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.domain.data_catalog import TableRef
from aws_tui.services.athena.service import AthenaService
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import OpenAthenaTableRequest


async def _wait_for_service_setup(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> None:
    await asyncio.gather(
        *(worker.wait() for worker in list(app.workers._workers)),
        return_exceptions=True,
    )
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


def _athena_client(ctx: AppContext, profile: str) -> InMemoryAthena:
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    factory = service._client_factory
    assert factory is not None
    client = factory(ctx.connection_resolver.resolve(profile))
    assert isinstance(client, InMemoryAthena)
    return client


def _add_cross_service_catalog(
    ctx: AppContext,
    *,
    profile: str,
    workgroup: str,
    database: str,
    table: str,
) -> InMemoryAthena:
    client = _athena_client(ctx, profile)
    if not any(row.name == "AwsDataCatalog" for row in client.catalogs[workgroup]):
        client.add_catalog(workgroup, "AwsDataCatalog")
    if not any(
        row.ref.database_name == database
        for row in client.databases.get((workgroup, "AwsDataCatalog"), [])
    ):
        client.add_database(workgroup, "AwsDataCatalog", database)
    if not any(
        row.ref.table_name == table
        for row in client.tables.get((workgroup, "AwsDataCatalog", database), [])
    ):
        client.add_table(workgroup, "AwsDataCatalog", database, table)
    return client


async def _open_service(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
    service_id: str,
) -> object:
    await _wait_for_service_setup(ctx, app, pilot)
    ctx.root_vm.services_menu.switch_service_command.execute(service_id)
    await _wait_for_service_setup(ctx, app, pilot)
    current = ctx.root_vm.content_host.current
    assert current is not None
    return current


async def _invoke(app: AwsTuiApp, action_id: str) -> None:
    result = app.action_dispatch(action_id)
    if inspect.isawaitable(result):
        await result


@pytest.mark.asyncio
async def test_glue_to_athena_preserves_identity_and_prefills_without_running(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    client = _add_cross_service_catalog(
        ctx,
        profile="demo-dev",
        workgroup="dev-analytics",
        database="dev_analytics",
        table="dev_events",
    )
    client.page_size = 1
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            assert glue.catalog.selected_table_name == "dev_events"
            client.calls.clear()

            await _invoke(app, "glue.query_in_athena")
            await _wait_for_service_setup(ctx, app, pilot)

            page = ctx.root_vm.content_host.current
            assert isinstance(page, AthenaPageVM)
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            assert page.context.connection_name == "demo-dev"
            assert page.context.region == "us-east-1"
            assert page.context.workgroup == "dev-analytics"
            assert page.context.catalog == "AwsDataCatalog"
            assert page.context.database == "dev_analytics"
            assert page.query.sql == (
                'SELECT * FROM "AwsDataCatalog"."dev_analytics"."dev_events" LIMIT 100'
            )
            assert page.query.execution_ref is None
            assert not any(call.method == "start_query" for call in client.calls)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_athena_to_glue_opens_one_unambiguous_visible_table(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    _add_cross_service_catalog(
        ctx,
        profile="demo-dev",
        workgroup="dev-analytics",
        database="dev_analytics",
        table="dev_events",
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            assert glue.catalog.query_in_athena()
            await _wait_for_service_setup(ctx, app, pilot)
            athena = ctx.root_vm.content_host.current
            assert isinstance(athena, AthenaPageVM)

            await _invoke(app, "athena.open_in_glue")
            await _wait_for_service_setup(ctx, app, pilot)

            destination = ctx.root_vm.content_host.current
            assert isinstance(destination, GluePageVM)
            assert destination.active_view == "catalog"
            assert destination.catalog.selected_database_name == "dev_analytics"
            assert destination.catalog.selected_table_name == "dev_events"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_athena_direct_glue_navigation_is_disabled_for_multiple_tables(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(page, AthenaPageVM)
            page.query.set_sql("SELECT * FROM events JOIN users USING (id)")

            await _invoke(app, "athena.open_in_glue")
            await _wait_for_service_setup(ctx, app, pilot)

            assert ctx.root_vm.content_host.current is page
            assert ctx.root_vm.content_host.current_id == "athena"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ref", "toast_id"),
    [
        (
            TableRef(
                "AwsDataCatalog",
                "dev_analytics",
                "dev_events",
                "deleted-profile",
                "us-east-1",
            ),
            "table-handoff-connection-missing",
        ),
        (
            TableRef(
                "AwsDataCatalog",
                "dev_analytics",
                "dev_events",
                "demo-dev",
                "us-west-2",
            ),
            "table-handoff-region-mismatch",
        ),
    ],
)
async def test_missing_or_mismatched_connection_leaves_current_page_unchanged(
    tmp_path: Path,
    ref: TableRef,
    toast_id: str,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_service(ctx, app, pilot, "glue")
            before_connection = ctx.root_vm.active_connection

            ctx.hub.send(OpenAthenaTableRequest(ref))
            await _wait_for_service_setup(ctx, app, pilot)

            assert ctx.root_vm.content_host.current is page
            assert ctx.root_vm.active_connection is before_connection
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == toast_id
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_missing_destination_table_rolls_back_service_and_selection(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(page, GluePageVM)

            ctx.hub.send(
                OpenAthenaTableRequest(
                    TableRef(
                        "MissingCatalog",
                        "dev_analytics",
                        "dev_events",
                        "demo-dev",
                        "us-east-1",
                    )
                )
            )
            await _wait_for_service_setup(ctx, app, pilot)

            restored = ctx.root_vm.content_host.current
            assert isinstance(restored, GluePageVM)
            assert restored.catalog.selected_database_name == "dev_analytics"
            assert restored.catalog.selected_table_name == "dev_events"
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
                "table-handoff-destination-failed"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_latest_cross_navigation_request_wins_without_auto_execution(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    dev_client = _add_cross_service_catalog(
        ctx,
        profile="demo-dev",
        workgroup="dev-analytics",
        database="dev_analytics",
        table="dev_events",
    )
    prod_client = _add_cross_service_catalog(
        ctx,
        profile="demo-prod",
        workgroup="prod-reporting",
        database="prod_warehouse",
        table="prod_sales",
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_service(ctx, app, pilot, "glue")

            ctx.hub.send(
                OpenAthenaTableRequest(
                    TableRef(
                        "AwsDataCatalog",
                        "dev_analytics",
                        "dev_events",
                        "demo-dev",
                        "us-east-1",
                    )
                )
            )
            ctx.hub.send(
                OpenAthenaTableRequest(
                    TableRef(
                        "AwsDataCatalog",
                        "prod_warehouse",
                        "prod_sales",
                        "demo-prod",
                        "us-east-1",
                    ),
                    snapshot_id=77,
                )
            )
            await _wait_for_service_setup(ctx, app, pilot)

            page = ctx.root_vm.content_host.current
            assert isinstance(page, AthenaPageVM)
            assert page.context.connection_name == "demo-prod"
            assert page.context.workgroup == "prod-reporting"
            assert page.query.sql.endswith(
                '"AwsDataCatalog"."prod_warehouse"."prod_sales" FOR VERSION AS OF 77 LIMIT 100'
            )
            assert not any(call.method == "start_query" for call in dev_client.calls)
            assert not any(call.method == "start_query" for call in prod_client.calls)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_cross_navigation_logs_never_include_full_sql(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    marker = "sensitive_sql_marker"
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(page, AthenaPageVM)
            page.query.set_sql(f"SELECT * FROM events WHERE secret = '{marker}'")

            await _invoke(app, "athena.open_in_glue")
            await _wait_for_service_setup(ctx, app, pilot)

            ctx.log_sink.flush()
            assert marker not in ctx.log_sink.path.read_text(encoding="utf-8")
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
