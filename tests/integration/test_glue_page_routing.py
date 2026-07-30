"""Production input-router coverage for the Glue service page."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from textual.containers import Container
from textual.widgets import OptionList
from vmx import NULL_DISPATCHER

from aws_tui.app import AwsTuiApp
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


@asynccontextmanager
async def _mounted_glue_app(
    app_context_factory: object,
    *,
    keymap: KeymapStore | None = None,
) -> AsyncIterator[tuple[AwsTuiApp, GluePageVM, InMemoryGlue, object]]:
    ctx = app_context_factory()  # type: ignore[operator]
    if keymap is not None:
        ctx.keymap_store = keymap
    fake = seeded_glue()
    vm = GluePageVM(
        client=fake,
        connection=Connection(
            name="analytics-dev",
            kind="aws",
            region="us-east-1",
            source="test",
            profile="analytics-dev",
        ),
        hub=ctx.hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    await vm.setup()
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            host = app.query_one("#content-host", Container)
            await host.remove_children()
            await host.mount(
                GluePage(
                    vm,
                    hub=ctx.hub,
                    focus_coordinator=ctx.focus_coordinator,
                    id="content-glue-page",
                )
            )
            await pilot.pause()
            yield app, vm, fake, pilot
    finally:
        vm.dispose()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_production_router_tabs_between_glue_controls(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, _vm, _fake, pilot):
        databases = app.query_one("#glue-databases-pane-options", OptionList)
        tables = app.query_one("#glue-tables-pane-options", OptionList)
        databases.focus()

        await pilot.press("tab")
        await pilot.pause()
        assert tables.has_focus

        await pilot.press("shift+tab")
        await pilot.pause()
        assert databases.has_focus


@pytest.mark.asyncio
async def test_production_router_activates_focused_glue_tabs(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, vm, _fake, pilot):
        tabs = app.query_one("#glue-view-tabs", ServiceTabStrip)
        tabs.focus()
        tabs._highlighted = "jobs"
        await pilot.press("enter")
        await pilot.pause()
        assert vm.active_view == "jobs"

        tabs._highlighted = "crawlers"
        await pilot.press("space")
        await pilot.pause()
        assert vm.active_view == "crawlers"


@pytest.mark.asyncio
async def test_production_router_navigates_glue_lists_and_filters(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, vm, _fake, pilot):
        tables = app.query_one("#glue-tables-pane-options", OptionList)
        tables.focus()
        assert tables.highlighted == 0

        await pilot.press("down")
        await pilot.pause()
        assert tables.highlighted == 1

        await pilot.press("2")
        await pilot.pause()
        jobs = app.query_one("#glue-jobs-pane-options", OptionList)
        runs = app.query_one("#glue-runs-pane-options", OptionList)
        run_filter = app.query_one("#glue-run-state-filter", ContextPicker)
        jobs.focus()
        await pilot.press("tab")
        await pilot.pause()
        assert runs.has_focus

        run_filter.focus()
        await pilot.press("down")
        await pilot.pause()
        assert run_filter.is_open
        assert app.focused is run_filter.query_one(OptionList)

        await pilot.press("down")
        await pilot.press("enter")
        await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
        await pilot.pause()
        assert run_filter.value == "RUNNING"
        assert vm.jobs.run_state_filter == frozenset({"RUNNING"})


@pytest.mark.asyncio
async def test_production_router_refreshes_active_glue_view(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, _vm, fake, pilot):
        before = len(fake.database_tokens)
        app.query_one("#glue-databases-pane-options", OptionList).focus()

        await pilot.press("r")
        await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
        await pilot.pause()

        assert len(fake.database_tokens) == before + 1
        assert fake.job_tokens == []
        assert fake.crawler_requests == []


@pytest.mark.asyncio
async def test_runtime_y_copies_exact_table_from_focused_glue_list(
    app_context_factory,
) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, _vm, _fake, pilot):
        tables = app.query_one("#glue-tables-pane-options", OptionList)
        selected = next(
            row.ref
            for row in _vm.catalog.tables
            if row.ref.table_name == _vm.catalog.selected_table_name
        )
        tables.focus()

        await pilot.press("y")
        await pilot.pause()

        copied = app.app_ctx.table_clipboard_vm.copied_table
        assert copied is not None
        assert copied.table_ref is selected
        assert copied.sql_identifier == '"AwsDataCatalog"."analytics"."events"'


@pytest.mark.asyncio
async def test_glue_copy_hint_tracks_selected_table_reactively(
    app_context_factory,
) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, vm, fake, pilot):
        legend = app.app_ctx.root_vm.chrome.hint_legend
        legend.set_current_service("glue")
        app._recompute_hint_disables()

        def copy_enabled() -> bool:
            return next(
                hint.enabled for hint in legend.actions if hint.action_id == "glue.copy_table_ref"
            )

        assert copy_enabled()

        fake.add_database("empty")
        await vm.catalog.refresh_databases()
        await vm.select_database("empty")
        await pilot.pause()
        assert not copy_enabled()

        await vm.select_database("analytics")
        await pilot.pause()
        assert copy_enabled()

        await vm.select_view("jobs")
        await pilot.pause()
        assert not copy_enabled()

        await vm.select_view("catalog")
        await pilot.pause()
        assert copy_enabled()


@pytest.mark.asyncio
async def test_production_router_honors_glue_view_rebindings_without_old_defaults(
    app_context_factory,
) -> None:  # type: ignore[no-untyped-def]
    keymap = KeymapStore(
        overlay={
            "glue.catalog": "7",
            "glue.jobs": "8",
            "glue.crawlers": "9",
        }
    )
    async with _mounted_glue_app(
        app_context_factory,
        keymap=keymap,
    ) as (_app, vm, _fake, pilot):
        await pilot.press("8")
        await pilot.pause()
        assert vm.active_view == "jobs"

        await pilot.press("7")
        await pilot.pause()
        assert vm.active_view == "catalog"

        await pilot.press("2")
        await pilot.pause()
        assert vm.active_view == "catalog"

        await pilot.press("9")
        await pilot.pause()
        assert vm.active_view == "crawlers"
