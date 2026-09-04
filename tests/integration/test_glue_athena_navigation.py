from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.widgets import Static, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.query import QueryState, ResultColumn
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena.service import AthenaService
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import (
    OpenAthenaTableRequest,
    OpenGlueTableRequest,
    OpenS3LocationRequest,
)
from aws_tui.vm.nav_menu_vm import SETTINGS_NAV_ID
from tests.helpers import drain_workers

SERVICE_SETUP_TIMEOUT_SECONDS = 30


async def _wait_for_service_setup(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> None:
    await drain_workers(app, timeout=SERVICE_SETUP_TIMEOUT_SECONDS)
    while app._table_navigation_tasks:
        await asyncio.wait_for(
            asyncio.gather(
                *tuple(app._table_navigation_tasks),
                return_exceptions=True,
            ),
            timeout=SERVICE_SETUP_TIMEOUT_SECONDS,
        )
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await asyncio.wait_for(setup_task, timeout=SERVICE_SETUP_TIMEOUT_SECONDS)
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


async def _wait_for_static_text(
    app: AwsTuiApp,
    pilot: object,
    selector: str,
    expected: str,
) -> None:
    deadline = asyncio.get_running_loop().time() + 1
    actual = ""
    while asyncio.get_running_loop().time() < deadline:
        actual = str(app.query_one(selector, Static).render())
        if actual == expected:
            return
        await pilot.pause(0.01)  # type: ignore[attr-defined]
    assert actual == expected


async def _activate_handoff(pilot: object, *, key: str | None, label: str) -> None:
    if key is not None:
        await pilot.press(key)  # type: ignore[attr-defined]
        return
    await pilot.press("colon")  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    assert isinstance(pilot.app.screen, CommandPalette)  # type: ignore[attr-defined]
    await pilot.press(*label)  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    assert tuple(
        entry.label
        for entry in pilot.app._app_ctx.command_palette_vm.filtered_entries  # type: ignore[attr-defined]
    ) == (label,)
    await pilot.press("enter")  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table_name", "snapshot_id", "key", "palette_label", "expected_sql"),
    [
        pytest.param(
            "dev_events",
            None,
            "Q",
            "Query table in Athena",
            'SELECT * FROM "dev_analytics"."dev_events" LIMIT 5',
            id="table-key-Q",
        ),
        pytest.param(
            "dev_events",
            None,
            None,
            "Query table in Athena",
            'SELECT * FROM "dev_analytics"."dev_events" LIMIT 5',
            id="table-palette",
        ),
        pytest.param(
            "dev_events_iceberg",
            4201,
            "V",
            "Query Iceberg snapshot in Athena",
            ('SELECT * FROM "dev_analytics"."dev_events_iceberg" FOR VERSION AS OF 4201 LIMIT 5'),
            id="snapshot-key-V",
        ),
        pytest.param(
            "dev_events_iceberg",
            4201,
            None,
            "Query Iceberg snapshot in Athena",
            ('SELECT * FROM "dev_analytics"."dev_events_iceberg" FOR VERSION AS OF 4201 LIMIT 5'),
            id="snapshot-palette",
        ),
    ],
)
async def test_glue_handoff_surfaces_preserve_source_and_prefill_without_execution(
    tmp_path: Path,
    table_name: str,
    snapshot_id: int | None,
    key: str | None,
    palette_label: str,
    expected_sql: str,
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
            await glue.open_table(
                TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    table_name,
                    "demo-dev",
                    "us-east-1",
                )
            )
            if snapshot_id is not None:
                assert await glue.catalog.iceberg.select_view("snapshots")
                assert glue.catalog.iceberg.select_snapshot(snapshot_id)
            await pilot.pause()
            client = _athena_client(ctx, "demo-dev")
            client.calls.clear()

            await _activate_handoff(pilot, key=key, label=palette_label)
            await _wait_for_service_setup(ctx, app, pilot)

            athena = ctx.root_vm.content_host.current
            assert isinstance(athena, AthenaPageVM)
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            assert ctx.root_vm.active_connection.region == "us-east-1"
            assert athena.context.connection_name == "demo-dev"
            assert athena.context.region == "us-east-1"
            assert athena.context.workgroup == "dev-analytics"
            assert athena.context.catalog == "AwsDataCatalog"
            assert athena.context.database == "dev_analytics"
            editor = app.query_one("#athena-editor", TextArea)
            assert athena.query.sql == expected_sql
            assert editor.text == expected_sql
            assert athena.active_view == "query"
            assert athena.query.execute_command.can_execute()
            assert athena.query.execution_ref is None
            assert athena.results.rows == ()
            assert not any(call.method == "start_query" for call in client.calls)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


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
            expected_sql = 'SELECT * FROM "dev_analytics"."dev_events" LIMIT 5'
            editor = app.query_one("#athena-editor", TextArea)
            assert page.query.sql == expected_sql
            assert editor.text == expected_sql
            assert page.query.execution_ref is None
            assert not any(call.method == "start_query" for call in client.calls)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_glue_prefills_before_athena_setup_completes(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    client = _athena_client(ctx, "demo-dev")
    original_list_workgroups = client.list_workgroups_page
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()

    async def blocked_list_workgroups(
        *,
        start_token: str | None = None,
    ) -> object:
        setup_started.set()
        await release_setup.wait()
        return await original_list_workgroups(start_token=start_token)

    client.list_workgroups_page = blocked_list_workgroups  # type: ignore[method-assign]
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                glue = await _open_service(ctx, app, pilot, "glue")
                assert isinstance(glue, GluePageVM)
                client.calls.clear()

                await _invoke(app, "glue.query_in_athena")
                await asyncio.wait_for(setup_started.wait(), timeout=1)
                await pilot.pause()

                page = ctx.root_vm.content_host.current
                expected_sql = 'SELECT * FROM "dev_analytics"."dev_events" LIMIT 5'
                assert isinstance(page, AthenaPageVM)
                assert page.active_view == "query"
                assert page.query.sql == expected_sql
                assert app.query_one("#athena-editor", TextArea).text == expected_sql
                assert page.query.is_context_resolving
                assert not page.query.execute_command.can_execute()
                await _wait_for_static_text(
                    app,
                    pilot,
                    "#athena-query-status",
                    "RESOLVING TABLE CONTEXT",
                )
                assert not any(call.method == "start_query" for call in client.calls)

                release_setup.set()
                await _wait_for_service_setup(ctx, app, pilot)

                assert page.context.catalog == "AwsDataCatalog"
                assert page.context.database == "dev_analytics"
                assert page.query.sql == expected_sql
                assert app.query_one("#athena-editor", TextArea).text == expected_sql
                assert not page.query.is_context_resolving
                assert page.query.execute_command.can_execute()
                assert not any(call.method == "start_query" for call in client.calls)
            finally:
                release_setup.set()
    finally:
        release_setup.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_glue_handoff_leaves_athena_catalog_picker_interactive_without_execution(
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
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            client = _athena_client(ctx, "demo-dev")
            client.calls.clear()

            await _activate_handoff(
                pilot,
                key="Q",
                label="Query table in Athena",
            )
            await _wait_for_service_setup(ctx, app, pilot)

            page = ctx.root_vm.content_host.current
            assert isinstance(page, AthenaPageVM)
            expected_sql = 'SELECT * FROM "dev_analytics"."dev_events" LIMIT 5'
            editor = app.query_one("#athena-editor", TextArea)
            context_before = page.context
            assert page.query.sql == expected_sql
            assert editor.text == expected_sql

            await _invoke(app, "athena.choose_catalog")
            await pilot.pause()

            picker = app.query_one("#athena-catalog", ContextPicker)
            assert picker.is_open
            await pilot.press("enter")
            await _wait_for_service_setup(ctx, app, pilot)

            assert not picker.is_open
            assert page.context == context_before
            assert page.query.sql == expected_sql
            assert editor.text == expected_sql
            assert page.query.execution_ref is None
            assert not any(call.method == "start_query" for call in client.calls)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_glue_handoff_explicitly_projects_starter_sql_after_revisiting_athena(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transaction must not rely solely on a reactive editor notification."""
    monkeypatch.setattr(AthenaQueryView, "_on_vm_changed", lambda *_args: None)
    monkeypatch.setattr(AthenaPage, "_on_page_changed", lambda *_args: None)
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            first_athena = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(first_athena, AthenaPageVM)
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)

            await pilot.press("Q")
            await _wait_for_service_setup(ctx, app, pilot)

            page = ctx.root_vm.content_host.current
            expected_sql = 'SELECT * FROM "dev_analytics"."dev_events" LIMIT 5'
            assert isinstance(page, AthenaPageVM)
            assert page.query.sql == expected_sql
            assert app.query_one("#athena-editor", TextArea).text == expected_sql
            assert page.query.execution_ref is None
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_clicking_glue_athena_hint_uses_the_registered_handoff_action(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(180, 44)) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            legend = app.query_one(HintLegend)
            chip = next(
                candidate
                for candidate in legend.query(".hint-chip")
                if candidate.action.action_id == "glue.query_in_athena"
            )

            await pilot.click(chip)
            await _wait_for_service_setup(ctx, app, pilot)

            page = ctx.root_vm.content_host.current
            expected_sql = 'SELECT * FROM "dev_analytics"."dev_events" LIMIT 5'
            assert isinstance(page, AthenaPageVM)
            assert page.query.sql == expected_sql
            assert app.query_one("#athena-editor", TextArea).text == expected_sql
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("system_clipboard_fails", [False, True])
async def test_glue_copy_updates_typed_clipboard_and_system_clipboard_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system_clipboard_fails: bool,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    copied: list[str] = []

    def copy_to_clipboard(_app: AwsTuiApp, value: str) -> None:
        if system_clipboard_fails:
            raise RuntimeError("OSC 52 unavailable")
        copied.append(value)

    monkeypatch.setattr(AwsTuiApp, "copy_to_clipboard", copy_to_clipboard)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)

            await _invoke(app, "glue.copy_table_ref")
            await pilot.pause()

            payload = ctx.table_clipboard_vm.copied_table
            assert payload is not None
            assert payload.table_ref == TableRef(
                "AwsDataCatalog",
                "dev_analytics",
                "dev_events",
                "demo-dev",
                "us-east-1",
            )
            assert payload.sql_identifier == ('"AwsDataCatalog"."dev_analytics"."dev_events"')
            assert copied == ([] if system_clipboard_fails else [payload.sql_identifier])
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
                "glue-table-reference-copied"
            )
            assert ctx.root_vm.content_host.current is glue
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_athena_insert_empty_clipboard_is_non_mutating(
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
            editor = app.query_one("#athena-editor", TextArea)
            editor.text = "SELECT 1"
            await pilot.pause()

            await _invoke(app, "athena.insert_table_ref")
            await pilot.pause()

            assert editor.text == "SELECT 1"
            assert page.query.sql == "SELECT 1"
            assert page.query.execution_ref is None
            assert ctx.table_clipboard_vm.copied_table is None
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
                "athena-table-reference-empty"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_athena_insert_refuses_source_mismatch_without_mutation(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    ctx.table_clipboard_vm.copy_command.execute(
        TableRef(
            "AwsDataCatalog",
            "prod_analytics",
            "events",
            "demo-prod",
            "us-west-2",
        )
    )
    copied_before = ctx.table_clipboard_vm.copied_table
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_service(ctx, app, pilot, "athena")
            assert isinstance(page, AthenaPageVM)
            editor = app.query_one("#athena-editor", TextArea)
            editor.text = "SELECT 1"
            await pilot.pause()

            await _invoke(app, "athena.insert_table_ref")
            await pilot.pause()

            assert editor.text == "SELECT 1"
            assert page.query.sql == "SELECT 1"
            assert page.query.execution_ref is None
            assert ctx.table_clipboard_vm.copied_table is copied_before
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "athena-table-reference-source-mismatch"
            assert "demo-prod (us-west-2)" in toast.text
            assert "demo-dev (us-east-1)" in toast.text
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_athena_insert_matching_source_switches_view_and_never_executes(
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
            client = _athena_client(ctx, "demo-dev")
            ctx.table_clipboard_vm.copy_command.execute(
                TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events",
                    "demo-dev",
                    "us-east-1",
                )
            )
            editor = app.query_one("#athena-editor", TextArea)
            editor.text = "SELECT  LIMIT 10"
            editor.selection = type(editor.selection).cursor((0, 7))
            await pilot.pause()
            assert page.query.sql == "SELECT  LIMIT 10"
            await page.select_view("history")
            await pilot.pause()
            client.calls.clear()

            await _invoke(app, "athena.insert_table_ref")
            await pilot.pause()

            expected = 'SELECT "AwsDataCatalog"."dev_analytics"."dev_events" LIMIT 10'
            assert page.active_view == "query"
            assert editor.text == expected
            assert page.query.sql == expected
            assert page.query.execution_ref is None
            assert not any(call.method == "start_query" for call in client.calls)
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
                "athena-table-reference-inserted"
            )
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
        ("glue.copy_table_ref", "glue-table-reference-unavailable"),
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
            elif action_id in {
                "glue.copy_table_ref",
                "glue.query_in_athena",
            }:
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
                '"prod_warehouse"."prod_sales" FOR VERSION AS OF 77 LIMIT 5'
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
                *,
                prepare_vm: Callable[[object], None] | None = None,
            ) -> None:
                if (
                    pause_stage == "switch"
                    and service_id == "athena"
                    and connection.name == "demo-prod"
                ):
                    pause_started.set()
                    await release_pause.wait()
                await original_switch(
                    connection,
                    auth_state,
                    service_id,
                    prepare_vm=prepare_vm,
                )

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
                assert current.query.sql.endswith('"dev_analytics"."dev_events" LIMIT 5')
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
                *,
                prepare_vm: Callable[[object], None] | None = None,
            ) -> None:
                if pause_stage == "switch" and service_id == "athena":
                    pause_started.set()
                    await release_pause.wait()
                await original_switch(
                    connection,
                    auth_state,
                    service_id,
                    prepare_vm=prepare_vm,
                )

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
                *,
                prepare_vm: Callable[[object], None] | None = None,
            ) -> None:
                switch_calls.append(service_id)
                await original_switch(
                    connection,
                    auth_state,
                    service_id,
                    prepare_vm=prepare_vm,
                )

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
            external_navigation_started = asyncio.Event()
            rollback_active = False
            original_switch = ctx.root_vm.switch_connection_and_service
            original_mount = app._mount_service_view
            original_external_navigation = app._mount_external_navigation
            original_restore = AthenaPageVM.restore_snapshot

            async def pause_open(target: GluePageVM, table_ref: TableRef) -> None:
                del target, table_ref
                open_started.set()
                await release_open.wait()

            async def pause_switch(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
                *,
                prepare_vm: Callable[[object], None] | None = None,
            ) -> None:
                if rollback_active and rollback_stage == "switch" and service_id == "athena":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_switch(
                    connection,
                    auth_state,
                    service_id,
                    prepare_vm=prepare_vm,
                )

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

            async def signal_external_navigation(selected: str, generation: int) -> None:
                external_navigation_started.set()
                await original_external_navigation(selected, generation)

            monkeypatch.setattr(GluePageVM, "open_table", pause_open)
            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                pause_switch,
            )
            monkeypatch.setattr(app, "_mount_service_view", pause_mount)
            monkeypatch.setattr(
                app,
                "_mount_external_navigation",
                signal_external_navigation,
            )
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
            await asyncio.wait_for(external_navigation_started.wait(), timeout=3)
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
async def test_table_handoff_rollback_restores_glue_iceberg_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    table_ref = TableRef(
        "AwsDataCatalog",
        "dev_analytics",
        "dev_events_iceberg",
        "demo-dev",
        "us-east-1",
    )
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            await glue.open_table(table_ref)
            assert await glue.catalog.iceberg.select_view("files")

            async def fail_open(
                target: AthenaPageVM,
                requested: TableRef,
                snapshot_id: int | None = None,
            ) -> None:
                del target, requested, snapshot_id
                raise RuntimeError("safe Athena open failure")

            monkeypatch.setattr(AthenaPageVM, "open_table", fail_open)
            ctx.hub.send(OpenAthenaTableRequest(table_ref))
            await _wait_for_service_setup(ctx, app, pilot)

            restored = ctx.root_vm.content_host.current
            assert isinstance(restored, GluePageVM)
            assert restored.active_view == "catalog"
            assert restored.catalog.selected_database_name == "dev_analytics"
            assert restored.catalog.selected_table_name == "dev_events_iceberg"
            assert restored.catalog.iceberg.active_view == "files"
    finally:
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
            await pilot.pause()
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
                *,
                prepare_vm: Callable[[object], None] | None = None,
            ) -> None:
                if service_id == "athena":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_switch(
                    connection,
                    auth_state,
                    service_id,
                    prepare_vm=prepare_vm,
                )

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
                *,
                prepare_vm: Callable[[object], None] | None = None,
            ) -> None:
                if service_id == "athena":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_switch(
                    connection,
                    auth_state,
                    service_id,
                    prepare_vm=prepare_vm,
                )

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
