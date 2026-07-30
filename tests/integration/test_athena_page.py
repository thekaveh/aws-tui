from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Static, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import QueryExecutionRef, QueryState, ResultColumn, ResultPage
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.services.athena import AthenaService
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.athena.page_vm import AthenaPageVM
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_page_vm import PageClient


@asynccontextmanager
async def _mounted_athena_app(
    tmp_path: Path,
    *,
    keymap: KeymapStore | None = None,
) -> AsyncIterator[tuple[AwsTuiApp, AppContext, AthenaPageVM, PageClient, object]]:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    if keymap is not None:
        ctx.keymap_store = keymap
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    client = PageClient(connection_name="demo-dev", region="us-east-1")
    service._client_factory = lambda _connection: client  # type: ignore[attr-defined]
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await open_service(ctx, pilot, "athena")
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, AthenaPageVM)
            await pilot.pause()
            yield app, ctx, vm, client, pilot
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


def test_athena_is_registered_after_glue_and_is_hidden_from_minio(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    try:
        assert [service.descriptor.id for service in ctx.registry.all()] == [
            "s3",
            "emr-serverless",
            "glue",
            "athena",
        ]
        minio = next(
            connection
            for connection in ctx.connection_resolver.list()
            if connection.kind == "s3-compatible"
        )
        assert not ctx.registry.get("athena").supports(minio)
    finally:
        ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_real_app_mounts_editor_results_and_explicit_entry_focuses_editor(
    tmp_path: Path,
) -> None:
    async with _mounted_athena_app(tmp_path) as (app, ctx, vm, _client, pilot):
        page = app.query_one("#content-athena-page", AthenaPage)
        app.focus_active_service_pane()
        await pilot.pause()

        assert ctx.root_vm.content_host.current_id == "athena"
        assert app.focused is page.query_one("#athena-editor", TextArea)
        editor = page.query_one("#athena-editor", TextArea)
        editor.text = "SELECT 1"
        await pilot.pause()
        await pilot.press("tab")
        assert page.query_one("#athena-execute", Button).has_focus
        await pilot.press("shift+tab")
        assert editor.has_focus
        await page.action_select_view("results")
        assert page.query_one(DataTable)
        assert vm.active_view == "results"


@pytest.mark.asyncio
async def test_results_retry_keeps_button_error_visible_until_success(
    tmp_path: Path,
) -> None:
    async with _mounted_athena_app(tmp_path) as (app, _ctx, vm, client, pilot):
        first_page = ResultPage(
            (ResultColumn("value", "varchar", "NULLABLE"),),
            (("one",),),
            "results-next",
        )
        second_page = ResultPage(
            (ResultColumn("value", "varchar", "NULLABLE"),),
            (("two",),),
            None,
        )
        should_fail = True
        block_request = False
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        async def get_results_page(
            _execution_id: str,
            *,
            start_token: str | None = None,
        ) -> ResultPage:
            if start_token is None:
                return first_page
            request_started.set()
            if block_request:
                await release_request.wait()
            if should_fail:
                raise ProviderError("temporary failure")
            return second_page

        client.get_results_page = get_results_page  # type: ignore[method-assign]
        await vm.results.load("q-results")
        await vm.select_view("results")
        await pilot.pause()
        button = app.query_one("#athena-more-results", Button)

        await vm.results.load_more()
        await pilot.pause()
        assert vm.results.error_text == "Athena results request failed"
        assert button.has_class("-error")
        assert button.tooltip == "Athena results request failed"

        block_request = True
        request_started.clear()
        retry = asyncio.create_task(vm.results.load_more())
        await request_started.wait()
        await pilot.pause()

        assert vm.results.is_loading_more
        assert vm.results.error_text == "Athena results request failed"
        assert button.has_class("-error")
        assert button.tooltip == "Athena results request failed"

        release_request.set()
        await retry
        await pilot.pause()

        assert not vm.results.is_loading_more
        assert vm.results.error_text == "Athena results request failed"
        assert button.has_class("-error")
        assert button.tooltip == "Athena results request failed"

        should_fail = False
        release_request.clear()
        request_started.clear()
        retry = asyncio.create_task(vm.results.load_more())
        await request_started.wait()
        await pilot.pause()

        assert vm.results.is_loading_more
        assert vm.results.error_text == "Athena results request failed"
        assert button.has_class("-error")
        assert button.tooltip == "Athena results request failed"

        release_request.set()
        await retry
        await pilot.pause()

        assert vm.results.error_text is None
        assert not button.has_class("-error")
        assert button.tooltip == "Load more result rows"


@pytest.mark.asyncio
async def test_printable_app_bindings_do_not_shadow_sql_editor_input(tmp_path: Path) -> None:
    async with _mounted_athena_app(tmp_path) as (app, _ctx, vm, _client, pilot):
        editor = app.query_one("#athena-editor", TextArea)
        editor.focus()

        await pilot.press(*tuple("select"))
        await pilot.press("space", "1", "comma", "space", "2")
        await pilot.pause()

        assert editor.text == "select 1, 2"
        assert vm.query.sql == "select 1, 2"
        assert vm.active_view == "query"
        await pilot.press("enter")
        assert editor.text == "select 1, 2\n"
        await pilot.press("backspace")
        assert editor.text == "select 1, 2"


@pytest.mark.asyncio
async def test_real_app_routes_tabs_execute_cancel_and_lazy_views(tmp_path: Path) -> None:
    async with _mounted_athena_app(tmp_path) as (app, _ctx, vm, client, pilot):
        editor = app.query_one("#athena-editor", TextArea)
        editor.text = "SELECT 1"
        editor.focus()
        await pilot.pause()

        await pilot.press("ctrl+enter")
        await pilot.pause()
        assert client.start_calls

        app.query_one("#athena-view-tabs", ServiceTabStrip).focus()
        await pilot.press("2")
        await pilot.pause()
        assert vm.active_view == "history"
        assert client.history_calls == [("primary", None)]

        await pilot.press("4")
        await pilot.pause()
        assert vm.active_view == "saved"
        assert client.named_calls == [("primary", None)]
        assert client.prepared_calls == [("primary", None)]

        vm.query._execution_ref = QueryExecutionRef(  # type: ignore[attr-defined]
            "owned-running",
            "demo-dev",
            "us-east-1",
            "primary",
        )
        vm.query._state = QueryState.RUNNING  # type: ignore[attr-defined]
        vm.query._busy = True  # type: ignore[attr-defined]
        vm.query._owns_active_query = True  # type: ignore[attr-defined]
        app.query_one("#athena-view-tabs", ServiceTabStrip).focus()
        await pilot.press("escape")
        await pilot.pause()
        assert vm.query.owns_active_query is False
        assert client.stop_calls == ["owned-running"]


@pytest.mark.asyncio
async def test_athena_command_hints_follow_live_command_and_pager_state(
    tmp_path: Path,
) -> None:
    async with _mounted_athena_app(tmp_path) as (app, ctx, vm, _client, pilot):

        def hint_enabled(action_id: str) -> bool:
            return next(
                hint.enabled
                for hint in ctx.root_vm.chrome.hint_legend.actions
                if hint.action_id == action_id
            )

        assert not hint_enabled("athena.execute")
        assert not hint_enabled("athena.cancel")
        assert not hint_enabled("athena.load_more")

        editor = app.query_one("#athena-editor", TextArea)
        editor.text = "SELECT 1"
        await pilot.pause()
        assert hint_enabled("athena.execute")

        editor.text = "DELETE FROM events"
        await pilot.pause()
        assert not hint_enabled("athena.execute")

        vm.query._execution_ref = QueryExecutionRef(  # type: ignore[attr-defined]
            "owned-running",
            "demo-dev",
            "us-east-1",
            "primary",
        )
        vm.query._busy = True  # type: ignore[attr-defined]
        vm.query._owns_active_query = True  # type: ignore[attr-defined]
        vm.query._notify("is_executing")  # type: ignore[attr-defined]
        vm.query._notify("owns_active_query")  # type: ignore[attr-defined]
        await pilot.pause()
        assert not hint_enabled("athena.execute")
        assert hint_enabled("athena.cancel")

        vm.query._busy = False  # type: ignore[attr-defined]
        vm.query._owns_active_query = False  # type: ignore[attr-defined]
        vm.query._execution_ref = None  # type: ignore[attr-defined]
        vm.query._notify("is_executing")  # type: ignore[attr-defined]
        await pilot.pause()
        assert not hint_enabled("athena.cancel")

        vm._workgroup_pager._current_token = "workgroups-next"  # type: ignore[attr-defined]
        vm._notify_context_lists()  # type: ignore[attr-defined]
        app.query_one("#athena-more-workgroups").focus()
        await pilot.pause()
        assert hint_enabled("athena.load_more")

        vm._set_loading_more("workgroups", True)  # type: ignore[attr-defined]
        await pilot.pause()
        assert not hint_enabled("athena.load_more")


@pytest.mark.asyncio
async def test_query_refresh_recovers_detail_without_remounting_athena_page(
    tmp_path: Path,
) -> None:
    async with _mounted_athena_app(tmp_path) as (app, ctx, vm, client, pilot):
        page = app.query_one("#content-athena-page", AthenaPage)
        source_header = page.query_one("#athena-source-header")
        client.workgroup_detail_error = ProviderError("temporary failure")

        await vm.select_workgroup("analysts")

        assert vm.context.workgroup == "analysts"
        assert vm.context.catalog == ""
        assert vm.workgroup_detail_state.name == "ERROR"

        client.workgroup_detail_error = None
        await app.action_refresh()
        await pilot.pause()

        assert ctx.root_vm.content_host.current is vm
        assert app.query_one("#content-athena-page", AthenaPage) is page
        assert page.query_one("#athena-source-header") is source_header
        assert vm.context.workgroup == "analysts"
        assert vm.context.catalog == "AwsDataCatalog"
        assert vm.context.database == "events"
        assert client.workgroup_detail_calls[-2:] == ["analysts", "analysts"]


@pytest.mark.asyncio
async def test_recovered_context_pager_clears_error_styling_tooltip_and_hint(
    tmp_path: Path,
) -> None:
    async with _mounted_athena_app(tmp_path) as (app, ctx, vm, client, pilot):
        page = app.query_one("#content-athena-page", AthenaPage)
        button = app.query_one("#athena-more-workgroups", Button)

        def hint_enabled(action_id: str) -> bool:
            return next(
                hint.enabled
                for hint in ctx.root_vm.chrome.hint_legend.actions
                if hint.action_id == action_id
            )

        vm._workgroup_pager._current_token = "workgroups-next"  # type: ignore[attr-defined]
        vm._notify_context_lists()  # type: ignore[attr-defined]
        client.workgroup_error = ProviderError("temporary failure")
        button.focus()
        await page.action_load_more()
        await pilot.pause()

        assert button.has_class("-error")
        assert button.tooltip == "Athena context request failed"

        client.workgroup_error = None

        async def retry_page(
            *,
            start_token: str | None = None,
        ) -> tuple[list[object], str | None]:
            assert start_token == "workgroups-next"
            return list(client.workgroups), "workgroups-next"

        client.list_workgroups_page = retry_page  # type: ignore[method-assign]
        await page.action_load_more()
        await pilot.pause()

        assert vm.workgroups_state.name == "IDLE"
        assert vm.workgroups_error_text is None
        assert not button.has_class("-error")
        assert button.tooltip == "Load more workgroups"
        assert hint_enabled("athena.load_more")


@pytest.mark.asyncio
async def test_configured_athena_rebindings_replace_defaults(tmp_path: Path) -> None:
    keymap = KeymapStore(
        overlay={
            "athena.query": "7",
            "athena.history": "8",
            "athena.results": "9",
            "athena.saved": "0",
            "athena.execute": "ctrl+x",
            "athena.cancel": "ctrl+g",
            "athena.load_more": "ctrl+l",
        }
    )
    async with _mounted_athena_app(
        tmp_path,
        keymap=keymap,
    ) as (app, _ctx, vm, client, pilot):
        assert tuple(
            str(app.query_one(f"#athena-tab-{view}", Static).render())
            for view in ("query", "history", "results", "saved")
        ) == ("7 query", "8 history", "9 results", "0 saved")

        app.query_one("#athena-view-tabs", ServiceTabStrip).focus()
        await pilot.press("8")
        await pilot.pause()
        assert vm.active_view == "history"

        await pilot.press("2")
        await pilot.pause()
        assert vm.active_view == "history"

        await pilot.press("7")
        app.query_one("#athena-editor", TextArea).text = "SELECT 7"
        await pilot.press("ctrl+x")
        await pilot.pause()
        assert client.start_calls[-1][0] == "SELECT 7"

        load_calls = 0

        async def load_more() -> None:
            nonlocal load_calls
            load_calls += 1

        vm.history.load_more = load_more  # type: ignore[method-assign]
        await app.query_one(AthenaPage).action_select_view("history")
        app.query_one("#athena-more-history").focus()
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert load_calls == 1
        await pilot.press("l")
        await pilot.pause()
        assert load_calls == 1


@pytest.mark.asyncio
async def test_real_app_shutdown_awaits_athena_before_dispose(tmp_path: Path) -> None:
    async with _mounted_athena_app(tmp_path) as (_app, ctx, vm, _client, _pilot):
        events: list[str] = []
        original_shutdown = vm.shutdown
        original_dispose = vm.dispose

        async def shutdown() -> None:
            events.append("shutdown-start")
            await original_shutdown()
            events.append("shutdown-end")

        def dispose() -> None:
            events.append("dispose")
            original_dispose()

        vm.shutdown = shutdown  # type: ignore[method-assign]
        vm.dispose = dispose  # type: ignore[method-assign]

        await ctx.root_vm.content_host.set_content(None, service_id=None)

        assert events == ["shutdown-start", "shutdown-end", "dispose"]
