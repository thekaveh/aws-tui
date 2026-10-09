"""Production input-router coverage for the Glue service page."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from textual.widgets import OptionList

from aws_tui.app import AwsTuiApp
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.services.glue.service import GlueService
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import focus_and_settle, wait_until
from tests.integration.test_glue_athena_navigation import _wait_for_service_setup
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
    ctx.config_store.path.write_text(
        '[defaults]\nconnection = "analytics-dev"\n\n'
        '[connections.analytics-dev]\nkind = "aws"\n'
        'profile = "analytics-dev"\nregion = "us-east-1"\n'
    )
    ctx.registry.register(
        GlueService(
            hub=ctx.hub,
            dispatcher=ctx.dispatcher,
            aws_session=ctx.aws_session,
            glue_client_factory=lambda _connection: fake,
        )
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            ctx.root_vm.services_menu.switch_service_command.execute("glue")
            await _wait_for_service_setup(ctx, app, pilot)
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, GluePageVM)
            assert ctx.root_vm.services_menu.selected_id == "glue"
            assert app.query_one(GluePage).vm is vm
            yield app, vm, fake, pilot
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_production_router_tabs_between_glue_controls(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, _vm, _fake, pilot):
        databases = app.query_one("#glue-databases-pane-options", OptionList)
        tables = app.query_one("#glue-tables-pane-options", OptionList)
        await focus_and_settle(databases)

        await pilot.press("tab")
        await wait_until(
            lambda: tables.has_focus,
            what="Tab to focus Glue tables",
        )
        assert tables.has_focus

        await pilot.press("shift+tab")
        await wait_until(
            lambda: databases.has_focus,
            what="reverse Tab to focus Glue databases",
        )
        assert databases.has_focus


@pytest.mark.asyncio
async def test_production_router_activates_focused_glue_tabs(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, vm, _fake, pilot):
        tabs = app.query_one("#glue-view-tabs", ServiceTabStrip)
        await focus_and_settle(tabs)
        tabs._highlighted = "jobs"
        await pilot.press("enter")
        await wait_until(
            lambda: vm.active_view == "jobs",
            what="Enter to activate Glue jobs",
        )
        assert vm.active_view == "jobs"

        tabs._highlighted = "crawlers"
        await pilot.press("space")
        await wait_until(
            lambda: vm.active_view == "crawlers",
            what="Space to activate Glue crawlers",
        )
        assert vm.active_view == "crawlers"


@pytest.mark.asyncio
async def test_production_router_navigates_glue_lists_and_filters(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, vm, _fake, pilot):
        tables = app.query_one("#glue-tables-pane-options", OptionList)
        await focus_and_settle(tables)
        assert tables.highlighted == 0

        await pilot.press("down")
        await wait_until(
            lambda: tables.highlighted == 1,
            what="Down to advance the Glue table cursor",
        )
        assert tables.highlighted == 1

        await pilot.press("2")
        await pilot.pause()
        jobs = app.query_one("#glue-jobs-pane-options", OptionList)
        runs = app.query_one("#glue-runs-pane-options", OptionList)
        run_filter = app.query_one("#glue-run-state-filter", ContextPicker)
        await focus_and_settle(jobs)
        await pilot.press("tab")
        await wait_until(
            lambda: runs.has_focus,
            what="Tab to focus Glue job runs",
        )
        assert runs.has_focus

        await focus_and_settle(run_filter)
        await pilot.press("down")
        await wait_until(
            lambda: (run_filter.is_open) and (app.focused is run_filter.query_one(OptionList)),
            what="run-state picker to open and focus its option list",
        )
        assert run_filter.is_open
        assert app.focused is run_filter.query_one(OptionList)

        await pilot.press("down")
        await pilot.press("enter")
        await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
        await wait_until(
            lambda: (
                (run_filter.value == "RUNNING")
                and (vm.jobs.run_state_filter == frozenset({"RUNNING"}))
            ),
            what="selected run state to reach the picker and jobs model",
        )
        assert run_filter.value == "RUNNING"
        assert vm.jobs.run_state_filter == frozenset({"RUNNING"})


@pytest.mark.asyncio
async def test_production_router_refreshes_active_glue_view(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    async with _mounted_glue_app(app_context_factory) as (app, _vm, fake, pilot):
        before = len(fake.database_tokens)
        app.query_one("#glue-databases-pane-options", OptionList).focus()

        await pilot.press("r")
        await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
        await wait_until(
            lambda: (
                (len(fake.database_tokens) == before + 1)
                and (fake.job_tokens == [])
                and (fake.crawler_requests == [])
            ),
            what="refresh shortcut to request another database page",
        )

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
        await focus_and_settle(tables)

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
    async with _mounted_glue_app(app_context_factory) as (app, vm, fake, _pilot):
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
        # The hint is recomputed reactively, off a hub PropertyChangedMessage
        # (app.py:4525), so the settled value is what this asserts -- not
        # whatever one scheduler cycle happens to have reached. A single
        # `pilot.pause()` yields one cycle without advancing the clock, so it is
        # not a wait: any change that adds a hop on this path turns the
        # assertion red on the slower runners while macOS stays green.
        await wait_until(
            lambda: not copy_enabled(),
            what="the copy hint to disable for a database with no tables",
        )

        # `select_database` clears the table selection and never restores it
        # (`catalog_vm.select_database` sets `_selected_table_name = None`), so the
        # hint can only re-enable once a table is selected again. Relying on the
        # tables pane re-highlighting row 0 to do that makes the assertion a race
        # against a repaint -- which is why this leg failed under coverage
        # instrumentation while passing on the faster unit legs. Selecting the
        # table is the thing the hint is supposed to track, so do that and assert
        # the tracking.
        await vm.select_database("analytics")
        await vm.select_table("events")
        await wait_until(copy_enabled, what="the copy hint to re-enable once a table is selected")

        await vm.select_view("jobs")
        await wait_until(
            lambda: not copy_enabled(),
            what="the copy hint to disable outside the catalog view",
        )

        await vm.select_view("catalog")
        await wait_until(
            lambda: copy_enabled(),
            what="catalog view to enable the copy hint",
        )
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
        await wait_until(
            lambda: vm.active_view == "jobs",
            what="rebound jobs shortcut to activate jobs",
        )
        assert vm.active_view == "jobs"

        await pilot.press("7")
        await wait_until(
            lambda: vm.active_view == "catalog",
            what="rebound catalog shortcut to activate catalog",
        )
        assert vm.active_view == "catalog"

        await pilot.press("2")
        # Deliver the old shortcut before checking that the view stays unchanged.
        await pilot.pause()
        assert vm.active_view == "catalog"

        await pilot.press("9")
        await wait_until(
            lambda: vm.active_view == "crawlers",
            what="rebound crawler shortcut to activate crawlers",
        )
        assert vm.active_view == "crawlers"
