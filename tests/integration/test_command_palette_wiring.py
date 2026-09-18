"""Integration: `:` / `Ctrl+K` open the command palette; entries dispatch."""

from __future__ import annotations

from dataclasses import replace

import pytest
from textual.containers import Container
from vmx import NULL_DISPATCHER

from aws_tui.app import AwsTuiApp
from aws_tui.domain.data_catalog import TableFormat
from aws_tui.infra.clipboard import InMemoryClipboard
from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import drain_workers
from tests.unit.vm.glue._fake_glue import seeded_glue
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector

_GLOBAL = {"Theme picker", "Cycle theme", "Settings", "Help", "Quit"}
_SOURCE = {"Switch source"}
# Scoped to the file manager: ``pane.copy_entry_path`` / ``pane.copy_path``
# resolve through ``_focused_file_pane()``, and only the S3 service hosts a
# ``DualPaneVM``, so they are inert on every other page.
_PANE = {"Copy cursor entry path", "Copy pane path"}
_GLUE = {
    "Glue catalog",
    "Glue jobs",
    "Glue crawlers",
    "Choose Glue run state",
    "Choose Glue crawler state",
    "Copy Glue table reference",
    "Open table location in S3",
    "Query table in Athena",
    "Load more Glue rows",
    "Query Iceberg snapshot in Athena",
}
_EMR = {"Next EMR application"}
_ATHENA = {
    "Athena query",
    "Athena history",
    "Athena results",
    "Athena saved queries",
    "Choose Athena workgroup",
    "Choose Athena catalog",
    "Choose Athena database",
    "Insert copied table reference",
    "Execute Athena query",
    "Cancel Athena query",
    "Load more Athena rows",
    "Open Athena result in S3",
    "Open query table in Glue",
}


@pytest.mark.asyncio
async def test_palette_projects_only_global_and_active_service_commands(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._populate_command_palette()
        vm = app._app_ctx.command_palette_vm

        vm.set_active_service("glue")
        assert {entry.label for entry in vm.filtered_entries} == _GLOBAL | _SOURCE | _GLUE

        vm.set_active_service("athena")
        assert {entry.label for entry in vm.filtered_entries} == _GLOBAL | _SOURCE | _ATHENA

        vm.set_active_service("s3")
        assert {entry.label for entry in vm.filtered_entries} == _GLOBAL | _SOURCE | _PANE

        vm.set_active_service("emr-serverless")
        assert {entry.label for entry in vm.filtered_entries} == _GLOBAL | _SOURCE | _EMR

        vm.set_active_service("settings")
        assert {entry.label for entry in vm.filtered_entries} == _GLOBAL


@pytest.mark.asyncio
async def test_colon_opens_command_palette(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("colon")  # ":" arrives as key "colon"
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        labels = {entry.label for entry in app._app_ctx.command_palette_vm.filtered_entries}
        assert labels == _GLOBAL | _SOURCE | _PANE
        assert app._crash_report is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_ctrl_k_opens_command_palette(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_enter_with_zero_matches_keeps_palette_open(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+k")
        await pilot.press(*"no command can match this value")
        await pilot.pause()
        assert app._app_ctx.command_palette_vm.filtered_entries == ()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, CommandPalette)
        assert app._app_ctx.command_palette_vm.is_open


@pytest.mark.asyncio
async def test_repeated_ctrl_k_does_not_stack_palettes(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+k")
        await pilot.press("ctrl+k")
        await pilot.pause()

        assert sum(isinstance(screen, CommandPalette) for screen in app.screen_stack) == 1
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CommandPalette)
        assert not app._app_ctx.command_palette_vm.is_open


@pytest.mark.asyncio
async def test_palette_entry_action_dispatches(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    # A palette entry's action routes through the ActionRegistry (same path as
    # the key binding), so selecting "Cycle theme" is identical to pressing T.
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._populate_command_palette()
        calls: list[str] = []
        app._actions.register("app.cycle_theme", lambda: calls.append("cycle"))
        app._app_ctx.command_palette_vm._actions["app.cycle_theme"]()
        assert calls == ["cycle"]


@pytest.mark.asyncio
async def test_enter_executes_filtered_palette_entry_with_production_bindings(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        calls: list[str] = []
        app._actions.register("app.cycle_theme", lambda: calls.append("cycle"))
        await pilot.press("colon")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"Cycle theme")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert calls == ["cycle"]
        assert not isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_palette_is_the_discoverability_route_for_the_path_copies(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    """The pane border carries no copy glyph, so the palette must carry it.

    ``p`` / ``P`` were reachable only by already knowing them once the
    clipboard emoji left the border title, and the Commands legend has no
    room for two more chips. This drives the whole production route -- open
    with ``:``, type the label, press ``enter`` -- because the label set
    assertions above would still pass if the entry dispatched nothing.

    ``pane.copy_path`` is ``async def`` and hands the port call to a
    worker, so the write lands after the palette has already closed;
    ``drain_workers`` is what makes the assertion honest rather than a
    race.
    """
    port = InMemoryClipboard()
    ctx = app_context_factory(clipboard=port)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()
        dual = ctx.root_vm.content_host.current
        expected = dual.focused_pane.viewmodel.copy_path

        await pilot.press("colon")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"Copy pane path")
        await pilot.pause()
        vm = ctx.command_palette_vm
        assert [entry.label for entry in vm.filtered_entries] == ["Copy pane path"]

        await pilot.press("enter")
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()

        assert port.writes == [expected]
        assert not isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_glue_handoff_disabled_state_tracks_table_and_snapshot_selection(
    app_context_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    ctx = app_context_factory()
    fake = seeded_glue()
    ref = fake.tables["analytics"][0].ref
    fake.table_details[ref] = replace(
        fake.table_details[ref],
        table_format=TableFormat.ICEBERG,
    )
    vm = GluePageVM(
        client=fake,
        iceberg_inspector=RecordingInspector(),
        connection=Connection(
            name="dev",
            kind="aws",
            region="us-east-1",
            source="test",
            profile="dev",
        ),
        hub=ctx.hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    vm.catalog.iceberg._page_size = 1  # type: ignore[attr-defined]
    await vm.setup()
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
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
            legend = ctx.root_vm.chrome.hint_legend
            legend.set_current_service("glue")
            projections: list[frozenset[str]] = []
            original_set_disabled_actions = legend.set_disabled_actions

            def record_projection(disabled: frozenset[str]) -> None:
                projections.append(disabled)
                original_set_disabled_actions(disabled)

            monkeypatch.setattr(legend, "set_disabled_actions", record_projection)

            def disabled_actions() -> set[str]:
                return {hint.action_id for hint in legend.actions if not hint.enabled}

            await vm.select_view("jobs")
            await pilot.pause()
            assert disabled_actions() == {
                "glue.copy_table_ref",
                "glue.query_in_athena",
                "glue.time_travel_in_athena",
                "glue.load_more",
            }

            await vm.select_view("catalog")
            await pilot.pause()
            assert disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"}

            fake.add_database("empty")
            await vm.catalog.refresh_databases()
            await vm.select_database("empty")
            await pilot.pause()
            assert disabled_actions() == {
                "glue.copy_table_ref",
                "glue.query_in_athena",
                "glue.time_travel_in_athena",
                "glue.load_more",
            }

            await vm.select_database("analytics")
            assert await vm.catalog.iceberg.select_view("snapshots")
            assert vm.catalog.iceberg.select_snapshot(43)
            await pilot.pause()
            assert disabled_actions() == {"glue.load_more"}

            projections.clear()
            assert await vm.catalog.iceberg.load_more()
            await pilot.pause()
            assert frozenset({"glue.load_more"}) in projections
            assert disabled_actions() == {"glue.load_more"}

            assert await vm.catalog.iceberg.select_view("history")
            await pilot.pause()
            assert disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"}

            projections.clear()
            await vm.shutdown()
            await pilot.pause()
            assert projections
            assert disabled_actions() == {
                "glue.copy_table_ref",
                "glue.query_in_athena",
                "glue.time_travel_in_athena",
                "glue.load_more",
            }

            toast_count = len(ctx.root_vm.chrome.toast_stack.toasts)
            await app.action_query_glue_table_in_athena()
            await app.action_time_travel_glue_table_in_athena()
            assert len(ctx.root_vm.chrome.toast_stack.toasts) == toast_count
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_direct_glue_page_disposal_disables_handoffs_without_advisory_toasts(
    app_context_factory,
) -> None:  # type: ignore[no-untyped-def]
    ctx = app_context_factory()
    fake = seeded_glue()
    ref = fake.tables["analytics"][0].ref
    fake.table_details[ref] = replace(
        fake.table_details[ref],
        table_format=TableFormat.ICEBERG,
    )
    vm = GluePageVM(
        client=fake,
        iceberg_inspector=RecordingInspector(),
        connection=Connection(
            name="dev",
            kind="aws",
            region="us-east-1",
            source="test",
            profile="dev",
        ),
        hub=ctx.hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    await vm.setup()
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
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
            legend = ctx.root_vm.chrome.hint_legend
            legend.set_current_service("glue")

            def disabled_actions() -> set[str]:
                return {hint.action_id for hint in legend.actions if not hint.enabled}

            assert await vm.catalog.iceberg.select_view("snapshots")
            assert vm.catalog.iceberg.select_snapshot(43)
            await pilot.pause()
            assert disabled_actions() == {"glue.load_more"}

            vm.dispose()
            await pilot.pause()
            assert disabled_actions() == {
                "glue.copy_table_ref",
                "glue.query_in_athena",
                "glue.time_travel_in_athena",
                "glue.load_more",
            }

            toast_count = len(ctx.root_vm.chrome.toast_stack.toasts)
            app.action_copy_glue_table_reference()
            await app.action_query_glue_table_in_athena()
            await app.action_time_travel_glue_table_in_athena()
            assert len(ctx.root_vm.chrome.toast_stack.toasts) == toast_count
    finally:
        vm.dispose()
