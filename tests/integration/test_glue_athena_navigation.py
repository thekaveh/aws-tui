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
from aws_tui.domain.query import QueryState, ResultColumn
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena.service import AthenaService
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import (
    OpenAthenaTableRequest,
    OpenGlueTableRequest,
    OpenS3LocationRequest,
)
from aws_tui.vm.nav_menu_vm import SETTINGS_NAV_ID


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
@pytest.mark.parametrize(
    ("action_id", "request_type"),
    [
        ("glue.open_s3_location", OpenS3LocationRequest),
        ("glue.query_in_athena", OpenAthenaTableRequest),
        ("glue.time_travel_in_athena", OpenAthenaTableRequest),
    ],
)
async def test_glue_app_actions_are_silent_after_page_shutdown(
    tmp_path: Path,
    action_id: str,
    request_type: type[object],
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            await glue.shutdown()
            requests: list[object] = []
            subscription = glue.hub.messages.subscribe(
                on_next=lambda message: (
                    requests.append(message) if isinstance(message, request_type) else None
                )
            )
            toasts_before = tuple(toast.model.id for toast in ctx.root_vm.chrome.toast_stack.toasts)

            await _invoke(app, action_id)
            await _wait_for_service_setup(ctx, app, pilot)

            assert requests == []
            assert (
                tuple(toast.model.id for toast in ctx.root_vm.chrome.toast_stack.toasts)
                == toasts_before
            )
            assert ctx.root_vm.content_host.current is glue
            assert ctx.root_vm.content_host.current_id == "glue"
            subscription.dispose()
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_id", "toast_id"),
    [
        ("glue.open_s3_location", "glue-s3-location-invalid"),
        ("glue.query_in_athena", "glue-athena-table-unavailable"),
        ("glue.time_travel_in_athena", "glue-athena-snapshot-unavailable"),
    ],
)
async def test_live_glue_invalid_selection_actions_retain_advisory_toast(
    tmp_path: Path,
    action_id: str,
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
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            if action_id == "glue.open_s3_location":
                glue.catalog._table_detail = None  # type: ignore[attr-defined]
            elif action_id == "glue.query_in_athena":
                glue.catalog._selected_table_name = None  # type: ignore[attr-defined]
            toast_count = len(ctx.root_vm.chrome.toast_stack.toasts)

            await _invoke(app, action_id)
            await _wait_for_service_setup(ctx, app, pilot)

            assert len(ctx.root_vm.chrome.toast_stack.toasts) == toast_count + 1
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == toast_id
            assert ctx.root_vm.content_host.current is glue
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
@pytest.mark.parametrize("pause_stage", ["switch", "mount", "open"])
@pytest.mark.parametrize("selected_service", ["s3", SETTINGS_NAV_ID, "emr-serverless"])
async def test_user_navigation_supersedes_inflight_table_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pause_stage: str,
    selected_service: str,
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
    release_pause = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            initial = await _open_service(ctx, app, pilot, "glue")
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
                if pause_stage == "switch" and service_id == "athena":
                    pause_started.set()
                    await release_pause.wait()
                await original_switch(connection, auth_state, service_id)

            async def pause_mount(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                if pause_stage == "mount" and service_id == "athena":
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
                if pause_stage == "open":
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
            await asyncio.wait_for(pause_started.wait(), timeout=2)

            ctx.root_vm.services_menu.switch_service_command.execute(selected_service)
            release_pause.set()
            await asyncio.wait_for(
                _wait_for_service_setup(ctx, app, pilot),
                timeout=15,
            )
            await pilot.pause()  # type: ignore[attr-defined]

            assert ctx.root_vm.services_menu.selected_id == selected_service
            assert ctx.root_vm.content_host.current_id == selected_service
            assert app._table_navigation_tasks == set()
            assert app._table_handoff_rollbacks == set()
    finally:
        release_pause.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("active_operation", ["query", "results"])
async def test_table_handoff_preflight_rejects_active_athena_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_operation: str,
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
    page: AthenaPageVM | None = None
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            current = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(current, AthenaPageVM)
            page = current
            switch_calls: list[str] = []
            original_switch = ctx.root_vm.switch_connection_and_service

            async def record_switch(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
            ) -> None:
                switch_calls.append(service_id)
                await original_switch(connection, auth_state, service_id)

            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                record_switch,
            )
            if active_operation == "query":
                page.query._busy = True  # type: ignore[attr-defined]
            else:
                page.results._is_loading_more = True  # type: ignore[attr-defined]

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
            await asyncio.wait_for(
                _wait_for_service_setup(ctx, app, pilot),
                timeout=10,
            )

            assert ctx.root_vm.content_host.current is page
            assert ctx.root_vm.content_host.current_id == "athena"
            assert ctx.root_vm.services_menu.selected_id == "athena"
            assert switch_calls == []
            assert app._table_navigation_tasks == set()
    finally:
        if page is not None:
            page.query._busy = False  # type: ignore[attr-defined]
            page.results._is_loading_more = False  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_stage", ["switch", "mount", "restore"])
@pytest.mark.parametrize("selected_service", ["s3", SETTINGS_NAV_ID, "emr-serverless"])
async def test_user_navigation_claim_during_table_rollback_always_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rollback_stage: str,
    selected_service: str,
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
            initial = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(initial, AthenaPageVM)
            initial.query.set_sql("SELECT stable_base")

            open_started = asyncio.Event()
            rollback_started = asyncio.Event()
            rollback_active = False
            original_switch = ctx.root_vm.switch_connection_and_service
            original_mount = app._mount_service_view
            original_restore = AthenaPageVM.restore_snapshot

            async def pause_open(target: GluePageVM, table_ref: TableRef) -> None:
                del target, table_ref
                open_started.set()
                await release_open.wait()

            async def pause_switch(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
            ) -> None:
                if rollback_active and rollback_stage == "switch" and service_id == "athena":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_switch(connection, auth_state, service_id)

            async def pause_mount(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                if rollback_active and rollback_stage == "mount" and service_id == "athena":
                    rollback_started.set()
                    await release_rollback.wait()
                return await original_mount(
                    service_id,
                    required_connection=required_connection,
                )

            async def pause_restore(
                target: AthenaPageVM,
                snapshot: object,
            ) -> None:
                if rollback_active and rollback_stage == "restore":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_restore(target, snapshot)  # type: ignore[arg-type]

            monkeypatch.setattr(GluePageVM, "open_table", pause_open)
            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                pause_switch,
            )
            monkeypatch.setattr(app, "_mount_service_view", pause_mount)
            monkeypatch.setattr(AthenaPageVM, "restore_snapshot", pause_restore)

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
            await asyncio.wait_for(open_started.wait(), timeout=3)
            navigation = next(iter(app._table_navigation_tasks))
            rollback_active = True
            navigation.cancel()
            await asyncio.wait_for(rollback_started.wait(), timeout=5)

            ctx.root_vm.services_menu.switch_service_command.execute(selected_service)
            owner = app._service_navigation_owner
            assert owner is not None
            assert owner[0] == "external"
            await asyncio.sleep(0.05)
            release_rollback.set()
            await asyncio.wait_for(
                _wait_for_service_setup(ctx, app, pilot),
                timeout=15,
            )

            assert ctx.root_vm.services_menu.selected_id == selected_service
            assert ctx.root_vm.content_host.current_id == selected_service
            assert app._table_navigation_tasks == set()
            assert app._table_handoff_rollbacks == set()
    finally:
        release_open.set()
        release_rollback.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_shutdown_closes_navigation_intake_before_first_drain_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    release_drain = asyncio.Event()
    release_late_navigation = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_for_service_setup(ctx, app, pilot)
            first_drain_complete = asyncio.Event()
            late_navigation_started = asyncio.Event()
            original_drain = app._drain_table_navigation

            async def pause_after_first_drain() -> None:
                await original_drain()
                first_drain_complete.set()
                await release_drain.wait()

            async def block_late_navigation(
                request: OpenAthenaTableRequest | OpenGlueTableRequest,
                generation: int,
            ) -> None:
                del request, generation
                late_navigation_started.set()
                await release_late_navigation.wait()

            monkeypatch.setattr(app, "_drain_table_navigation", pause_after_first_drain)
            monkeypatch.setattr(app, "_open_table_request", block_late_navigation)
            request = OpenGlueTableRequest(
                TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events",
                    "demo-dev",
                    "us-east-1",
                )
            )

            shutdown = asyncio.create_task(app._aws_tui_shutdown())
            await asyncio.wait_for(first_drain_complete.wait(), timeout=2)
            ctx.hub.send(request)
            app._on_service_navigation_message(request)
            await asyncio.sleep(0)
            release_drain.set()
            await asyncio.wait_for(shutdown, timeout=10)

            assert not late_navigation_started.is_set()
            assert app._table_navigation_tasks == set()
            assert app._table_handoff_rollbacks == set()
            assert app._service_navigation_sub is None
    finally:
        release_drain.set()
        release_late_navigation.set()
        tasks = tuple(app._table_navigation_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "cancelled", "superseded"])
async def test_table_handoff_rollback_restores_complete_athena_result_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    release_open = asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(page, AthenaPageVM)
            await page.select_view("saved")
            await page.select_named_query("nq-dev-events")
            await page.select_view("history")
            await page.select_history_execution("q-dev-succeeded")
            client = _athena_client(ctx, "demo-dev")
            assert page.context is not None
            client.add_query_result(
                "SELECT 'TABLE_HANDOFF_SQL_SECRET'",
                page.context,
                columns=(ResultColumn("_col0", "integer", "NULLABLE"),),
                rows=(("1",),),
            )
            page.query.set_sql("SELECT 'TABLE_HANDOFF_SQL_SECRET'")
            await page.select_view("query")
            await page.query.execute()
            await page.select_view("results")
            assert page.query.execution_ref is not None
            assert page.query.state is QueryState.SUCCEEDED
            assert page.results.rows == (("1",),)

            execution_id = page.query.execution_ref.execution_id
            before_context = page.context
            before_query_detail = (
                page.query.state,
                page.query.statistics,
                page.query.query_error,
                page.query.state_reason,
                page.query.output_location,
                page.query.engine_version,
                page.query.pane_state,
                page.query.error_text,
                page.query.validation_error,
            )
            before_columns = page.results.columns
            before_rows = page.results.rows
            before_result_state = page.results.state
            before_result_error = page.results.error_text
            before_result_loading = page.results.is_loading_more
            before_history_selection = page.history.selected_execution_id
            before_saved_kind = page.saved.selected_kind
            before_saved_selection = page.saved.selected_query_id
            start_count = sum(call.method == "start_query" for call in client.calls)
            history_before = tuple(client.history["dev-analytics"])
            open_started = asyncio.Event()

            if outcome == "failed":

                async def fail_open(target: GluePageVM, table_ref: TableRef) -> None:
                    del target, table_ref
                    raise RuntimeError("safe table open failure")

                monkeypatch.setattr(GluePageVM, "open_table", fail_open)
            else:
                original_open = GluePageVM.open_table

                async def pause_open(target: GluePageVM, table_ref: TableRef) -> None:
                    open_started.set()
                    await release_open.wait()
                    await original_open(target, table_ref)

                monkeypatch.setattr(GluePageVM, "open_table", pause_open)

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
            if outcome != "failed":
                await asyncio.wait_for(open_started.wait(), timeout=2)
                if outcome == "cancelled":
                    navigation = next(iter(app._table_navigation_tasks))
                    navigation.cancel()
                else:
                    ctx.hub.send(
                        OpenAthenaTableRequest(
                            TableRef(
                                "AwsDataCatalog",
                                "dev_analytics",
                                "dev_events",
                                "missing-profile",
                                "us-east-1",
                            )
                        )
                    )
                release_open.set()

            await asyncio.wait_for(
                _wait_for_service_setup(ctx, app, pilot),
                timeout=15,
            )

            restored = ctx.root_vm.content_host.current
            assert isinstance(restored, AthenaPageVM)
            assert restored.context == before_context
            assert restored.active_view == "results"
            assert restored.query.sql == "SELECT 'TABLE_HANDOFF_SQL_SECRET'"
            assert restored.query.execution_ref is not None
            assert restored.query.execution_ref.execution_id == execution_id
            assert (
                restored.query.state,
                restored.query.statistics,
                restored.query.query_error,
                restored.query.state_reason,
                restored.query.output_location,
                restored.query.engine_version,
                restored.query.pane_state,
                restored.query.error_text,
                restored.query.validation_error,
            ) == before_query_detail
            assert restored.results.execution_id == execution_id
            assert restored.results.columns == before_columns
            assert restored.results.rows == before_rows
            assert restored.results.state is before_result_state
            assert restored.results.error_text == before_result_error
            assert restored.results.is_loading_more is before_result_loading
            assert restored.history.selected_execution_id == before_history_selection
            assert restored.saved.selected_kind is before_saved_kind
            assert restored.saved.selected_query_id == before_saved_selection
            assert tuple(client.history["dev-analytics"]) == history_before
            assert sum(call.method == "start_query" for call in client.calls) == start_count
    finally:
        release_open.set()
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
