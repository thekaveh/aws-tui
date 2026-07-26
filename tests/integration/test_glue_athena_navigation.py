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
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena.service import AthenaService
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import OpenAthenaTableRequest, OpenGlueTableRequest


async def _wait_for_service_setup(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> None:
    await asyncio.wait_for(
        asyncio.gather(
            *(worker.wait() for worker in list(app.workers._workers)),
            return_exceptions=True,
        ),
        timeout=10,
    )
    while app._table_navigation_tasks:
        await asyncio.wait_for(
            asyncio.gather(
                *tuple(app._table_navigation_tasks),
                return_exceptions=True,
            ),
            timeout=10,
        )
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await asyncio.wait_for(setup_task, timeout=10)
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
@pytest.mark.parametrize("pause_stage", ["switch", "mount", "open"])
@pytest.mark.parametrize("newer_result", ["missing", "region-mismatch", "success"])
async def test_superseded_table_handoff_is_one_serialized_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pause_stage: str,
    newer_result: str,
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
    _add_cross_service_catalog(
        ctx,
        profile="demo-prod",
        workgroup="prod-reporting",
        database="prod_warehouse",
        table="prod_sales",
    )
    app = AwsTuiApp(ctx)
    release_pause = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            initial = await asyncio.wait_for(
                _open_service(ctx, app, pilot, "glue"),
                timeout=5,
            )
            assert isinstance(initial, GluePageVM)
            pause_started = asyncio.Event()
            original_switch = ctx.root_vm.switch_connection_and_service
            original_mount = app._mount_service_view
            original_open = AthenaPageVM.open_table

            async def pause_switch(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
            ) -> None:
                if (
                    pause_stage == "switch"
                    and service_id == "athena"
                    and connection.name == "demo-prod"
                ):
                    pause_started.set()
                    await release_pause.wait()
                await original_switch(connection, auth_state, service_id)

            async def pause_mount(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                if (
                    pause_stage == "mount"
                    and service_id == "athena"
                    and required_connection is not None
                    and required_connection.name == "demo-prod"
                ):
                    pause_started.set()
                    await release_pause.wait()
                return await original_mount(
                    service_id,
                    required_connection=required_connection,
                )

            async def pause_open(
                page: AthenaPageVM,
                table_ref: TableRef,
                snapshot_id: int | None = None,
            ) -> None:
                if pause_stage == "open" and table_ref.connection_name == "demo-prod":
                    pause_started.set()
                    await release_pause.wait()
                await original_open(page, table_ref, snapshot_id)

            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                pause_switch,
            )
            monkeypatch.setattr(app, "_mount_service_view", pause_mount)
            monkeypatch.setattr(AthenaPageVM, "open_table", pause_open)

            old_request = OpenAthenaTableRequest(
                TableRef(
                    "AwsDataCatalog",
                    "prod_warehouse",
                    "prod_sales",
                    "demo-prod",
                    "us-east-1",
                )
            )
            app._table_navigation_generation = 1
            old = asyncio.create_task(app._open_table_request(old_request, 1))
            await asyncio.wait_for(pause_started.wait(), timeout=2)

            if newer_result == "missing":
                newer_ref = TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events",
                    "missing-profile",
                    "us-east-1",
                )
            elif newer_result == "region-mismatch":
                newer_ref = TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events",
                    "demo-dev",
                    "us-west-2",
                )
            else:
                newer_ref = TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events",
                    "demo-dev",
                    "us-east-1",
                )
            app._table_navigation_generation = 2
            newer = asyncio.create_task(
                app._open_table_request(OpenAthenaTableRequest(newer_ref), 2)
            )
            release_pause.set()
            await asyncio.wait_for(
                asyncio.gather(old, newer, return_exceptions=True),
                timeout=15,
            )
            await asyncio.wait_for(
                _wait_for_service_setup(ctx, app, pilot),
                timeout=5,
            )

            current = ctx.root_vm.content_host.current
            assert ctx.root_vm.active_connection is not None
            if newer_result == "success":
                assert isinstance(current, AthenaPageVM)
                assert ctx.root_vm.active_connection.name == "demo-dev"
                assert current.context.connection_name == "demo-dev"
                assert current.query.sql.endswith(
                    '"AwsDataCatalog"."dev_analytics"."dev_events" LIMIT 100'
                )
            else:
                assert isinstance(current, GluePageVM)
                assert ctx.root_vm.active_connection.name == "demo-dev"
                assert current.catalog.selected_database_name == "dev_analytics"
                assert current.catalog.selected_table_name == "dev_events"
    finally:
        release_pause.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_table_handoff_rollback_survives_repeated_cancellation_and_restores_athena(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    release_open = asyncio.Event()
    release_rollback = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await asyncio.wait_for(
                _open_service(ctx, app, pilot, "athena"),
                timeout=5,
            )
            assert isinstance(page, AthenaPageVM)
            await page.select_view("saved")
            page.query.set_sql("SELECT prior_state_marker FROM events")
            prior_context = page.context

            open_started = asyncio.Event()
            rollback_started = asyncio.Event()
            original_open = GluePageVM.open_table
            original_switch = ctx.root_vm.switch_connection_and_service

            async def pause_open(target: GluePageVM, table_ref: TableRef) -> None:
                open_started.set()
                await release_open.wait()
                await original_open(target, table_ref)

            async def pause_rollback(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
            ) -> None:
                if service_id == "athena":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_switch(connection, auth_state, service_id)

            monkeypatch.setattr(GluePageVM, "open_table", pause_open)
            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                pause_rollback,
            )
            app._table_navigation_generation = 1
            navigation = asyncio.create_task(
                app._open_table_request(
                    OpenGlueTableRequest(
                        TableRef(
                            "AwsDataCatalog",
                            "dev_analytics",
                            "dev_events",
                            "demo-dev",
                            "us-east-1",
                        )
                    ),
                    1,
                )
            )
            await asyncio.wait_for(open_started.wait(), timeout=2)

            navigation.cancel()
            await asyncio.wait_for(rollback_started.wait(), timeout=2)
            navigation.cancel()
            navigation.cancel()
            release_rollback.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(navigation, timeout=3)
            await asyncio.wait_for(
                _wait_for_service_setup(ctx, app, pilot),
                timeout=5,
            )

            restored = ctx.root_vm.content_host.current
            assert isinstance(restored, AthenaPageVM)
            assert restored.context == prior_context
            assert restored.active_view == "saved"
            assert restored.query.sql == "SELECT prior_state_marker FROM events"
            assert app._table_handoff_rollbacks == set()
    finally:
        release_open.set()
        release_rollback.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_table_handoff_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    release_open = asyncio.Event()
    release_rollback = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(
                _open_service(ctx, app, pilot, "athena"),
                timeout=5,
            )
            open_started = asyncio.Event()
            rollback_started = asyncio.Event()
            original_switch = ctx.root_vm.switch_connection_and_service

            async def pause_open(target: GluePageVM, table_ref: TableRef) -> None:
                del target, table_ref
                open_started.set()
                await release_open.wait()

            async def pause_rollback(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
            ) -> None:
                if service_id == "athena":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_switch(connection, auth_state, service_id)

            monkeypatch.setattr(GluePageVM, "open_table", pause_open)
            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                pause_rollback,
            )
            ctx.hub.send(
                OpenGlueTableRequest(
                    TableRef(
                        "AwsDataCatalog",
                        "dev_analytics",
                        "dev_events",
                        "demo-dev",
                        "us-east-1",
                    )
                )
            )
            await asyncio.wait_for(open_started.wait(), timeout=2)
            navigation = next(iter(app._table_navigation_tasks))
            navigation.cancel()
            await asyncio.wait_for(rollback_started.wait(), timeout=2)

            shutdown = asyncio.create_task(app._aws_tui_shutdown())
            await asyncio.sleep(0)
            assert not shutdown.done()
            release_rollback.set()
            await asyncio.wait_for(shutdown, timeout=5)

            assert app._table_navigation_tasks == set()
            assert app._table_handoff_rollbacks == set()
    finally:
        release_open.set()
        release_rollback.set()
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
