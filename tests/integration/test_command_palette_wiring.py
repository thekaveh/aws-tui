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
from tests.helpers import drain_workers, wait_until
from tests.unit.vm.glue._fake_glue import seeded_glue
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector

_GLOBAL = {
    "Theme picker",
    "Cycle theme",
    "Settings",
    "Help",
    "Quit",
    "Transfer history and recovery",
}
_SOURCE = {"Switch source", "Retry active source credentials"}
# Scoped to the file manager: ``pane.copy_entry_path`` / ``pane.copy_path``
# resolve through ``_focused_file_pane()``, and only the S3 service hosts a
# ``DualPaneVM``, so they are inert on every other page.
_PANE = {
    "S3 object details",
    "Filter loaded entries",
    "Find loaded entry",
    "Sort loaded entries",
    "Clear pane filter",
    "Copy cursor entry path",
    "Copy pane path",
    "Enter multi-select mode",
    "Toggle cursor selection",
    "Select all visible entries",
    "Clear selection",
    "Exit multi-select mode",
}
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
    "Inspect Athena cell",
    "Copy Athena cell as JSON",
    "Copy Athena row as JSON",
    "Filter loaded Athena results",
    "Sort loaded Athena results",
    "Reset loaded Athena results",
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
        await wait_until(
            lambda: isinstance(app.screen, CommandPalette),
            what="colon to open the command palette",
        )
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
        await wait_until(
            lambda: isinstance(app.screen, CommandPalette),
            what="Ctrl+K to open the command palette",
        )
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_enter_with_zero_matches_keeps_palette_open(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+k")
        await pilot.press(*"no command can match this value")
        await wait_until(
            lambda: app._app_ctx.command_palette_vm.filtered_entries == (),
            what="palette query to filter out all entries",
        )
        assert app._app_ctx.command_palette_vm.filtered_entries == ()

        await pilot.press("enter")
        # Deliver Enter before checking that an empty palette stays open.
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
        # Deliver the repeated shortcut before checking that no second palette was pushed.
        await pilot.pause()

        assert sum(isinstance(screen, CommandPalette) for screen in app.screen_stack) == 1
        await pilot.press("escape")
        await wait_until(
            lambda: (
                (not isinstance(app.screen, CommandPalette))
                and (not app._app_ctx.command_palette_vm.is_open)
            ),
            what="Escape to close the command palette and its model",
        )
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
        await wait_until(
            lambda: isinstance(app.screen, CommandPalette),
            what="command palette to open before filtering",
        )
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"Cycle theme")
        await pilot.pause()
        await pilot.press("enter")
        await wait_until(
            lambda: (calls == ["cycle"]) and (not isinstance(app.screen, CommandPalette)),
            what="Cycle theme palette entry to dispatch and close",
        )

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
        await wait_until(
            lambda: isinstance(app.screen, CommandPalette),
            what="command palette to open for pane-path copy",
        )
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"Copy pane path")
        await pilot.pause()
        vm = ctx.command_palette_vm
        assert [entry.label for entry in vm.filtered_entries] == ["Copy pane path"]

        await pilot.press("enter")
        await pilot.pause()
        await drain_workers(app)
        await wait_until(
            lambda: (port.writes == [expected]) and (not isinstance(app.screen, CommandPalette)),
            what="pane-path copy to write the expected path and close the palette",
        )

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
        async with app.run_test(size=(120, 40)) as _pilot:
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
            await wait_until(
                lambda: (
                    disabled_actions()
                    == {
                        "glue.copy_table_ref",
                        "glue.query_in_athena",
                        "glue.time_travel_in_athena",
                        "glue.load_more",
                    }
                ),
                what="Glue jobs view to disable catalog handoffs",
            )
            assert disabled_actions() == {
                "glue.copy_table_ref",
                "glue.query_in_athena",
                "glue.time_travel_in_athena",
                "glue.load_more",
            }

            await vm.select_view("catalog")
            await wait_until(
                lambda: disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"},
                what="Glue catalog to restore available table handoffs",
            )
            assert disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"}

            fake.add_database("empty")
            await vm.catalog.refresh_databases()
            await vm.select_database("empty")
            await wait_until(
                lambda: (
                    disabled_actions()
                    == {
                        "glue.copy_table_ref",
                        "glue.query_in_athena",
                        "glue.time_travel_in_athena",
                        "glue.load_more",
                    }
                ),
                what="empty Glue database to disable table handoffs",
            )
            assert disabled_actions() == {
                "glue.copy_table_ref",
                "glue.query_in_athena",
                "glue.time_travel_in_athena",
                "glue.load_more",
            }

            # `select_database` clears the table selection and calls
            # `iceberg.clear_table()`, so there are no snapshots to select until a
            # table is bound again. The tables pane re-highlighting row 0 does
            # that, which makes the next two assertions a race against a repaint
            # -- they failed on both Windows legs across two runs for exactly
            # that reason. Selecting the table is what "tracks table and snapshot
            # selection" means, so do it rather than race it.
            await vm.select_database("analytics")
            await vm.select_table("events")
            assert await vm.catalog.iceberg.select_view("snapshots")
            await wait_until(
                lambda: vm.catalog.iceberg.snapshots,
                what="the snapshots pane to load for the re-selected table",
            )
            assert vm.catalog.iceberg.select_snapshot(43)
            await wait_until(
                lambda: disabled_actions() == {"glue.load_more"},
                what="selected Iceberg snapshot to enable time travel",
            )
            assert disabled_actions() == {"glue.load_more"}

            projections.clear()
            assert await vm.catalog.iceberg.load_more()
            await wait_until(
                lambda: (
                    (frozenset({"glue.load_more"}) in projections)
                    and (disabled_actions() == {"glue.load_more"})
                ),
                what="load-more to project refreshed handoff availability",
            )
            assert frozenset({"glue.load_more"}) in projections
            assert disabled_actions() == {"glue.load_more"}

            assert await vm.catalog.iceberg.select_view("history")
            await wait_until(
                lambda: disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"},
                what="Iceberg history to disable snapshot time travel",
            )
            assert disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"}

            projections.clear()
            await vm.shutdown()
            await wait_until(
                lambda: (
                    (projections)
                    and (
                        disabled_actions()
                        == {
                            "glue.copy_table_ref",
                            "glue.query_in_athena",
                            "glue.time_travel_in_athena",
                            "glue.load_more",
                        }
                    )
                ),
                what="Glue shutdown to project all handoffs disabled",
            )
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
        async with app.run_test(size=(120, 40)) as _pilot:
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
            await wait_until(
                lambda: disabled_actions() == {"glue.load_more"},
                what="selected snapshot to enable handoffs before disposal",
            )
            assert disabled_actions() == {"glue.load_more"}

            vm.dispose()
            await wait_until(
                lambda: (
                    disabled_actions()
                    == {
                        "glue.copy_table_ref",
                        "glue.query_in_athena",
                        "glue.time_travel_in_athena",
                        "glue.load_more",
                    }
                ),
                what="Glue disposal to disable all handoffs",
            )
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


async def test_athena_loaded_result_controls_have_registered_scoped_palette_actions(
    app_context_factory,
):
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.ui.widgets.help_modal import HelpModal

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    controls = {
        "inspect_cell",
        "copy_cell",
        "copy_row",
        "filter_results",
        "sort_results",
        "reset_results",
    }
    async with app.run_test() as pilot:
        await pilot.pause()
        for action in controls:
            assert app._actions.has("athena." + action)
        app._populate_command_palette()
        palette = ctx.command_palette_vm
        palette.set_active_service("athena")
        entries = {entry.id for entry in palette.filtered_entries}
        assert {"athena." + action for action in controls} <= entries
        palette.set_active_service("s3")
        assert not {"athena." + action for action in controls} & {
            entry.id for entry in palette.filtered_entries
        }
        overlay = HelpModal(keymap=KeymapStore(overlay={"athena.copy_cell": "ctrl+g"}))
        app.push_screen(overlay)
        await pilot.pause()
        rows = " ".join(str(row.content) for row in overlay.query(".help-row"))
        assert "Ctrl+g" in rows
        assert "loaded Athena" in rows


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_athena_result_shortcuts_arrows_modal_containment_and_palette_dispatch(
    app_context_factory,
    monkeypatch,
    size,
):
    from textual.widgets import DataTable, Input, TextArea

    from aws_tui.domain.query import ResultColumn, ResultPage
    from aws_tui.ui.widgets.athena.page import AthenaPage
    from aws_tui.ui.widgets.athena.result_cell_modal import AthenaResultCellModal
    from aws_tui.ui.widgets.athena.result_filter_modal import AthenaResultFilterModal
    from tests.helpers import focus_and_settle
    from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

    ctx = app_context_factory()
    client = PageClient()
    result_calls = []

    async def results(execution_id, *, start_token=None):
        result_calls.append((execution_id, start_token))
        assert start_token is None
        return ResultPage(
            (
                ResultColumn("dup", "varchar", "NULLABLE"),
                ResultColumn("dup", "varchar", "NULLABLE"),
            ),
            ((None, ""), ("2", "literal\n[bold]é[/bold]")),
            None,
        )

    client.get_results_page = results
    vm = make_page_vm(client, hub=ctx.hub)
    await vm.setup()
    await vm.results.load("shortcuts")
    await vm.select_view("results")
    copies = []
    app = AwsTuiApp(ctx)
    monkeypatch.setattr(app, "copy_value", lambda value, label: copies.append((value, label)))
    try:
        async with app.run_test(size=size) as pilot:
            host = app.query_one("#content-host", Container)
            await host.remove_children()
            await host.mount(
                AthenaPage(
                    vm,
                    hub=ctx.hub,
                    focus_coordinator=ctx.focus_coordinator,
                    id="content-athena-page",
                )
            )
            await pilot.pause()
            table = app.query_one(DataTable)
            await focus_and_settle(table)
            await pilot.press("right", "down")
            await pilot.pause()
            assert vm.results.selection == (1, 1)
            await pilot.press("alt+c", "alt+shift+c")
            assert [value for value, _ in copies] == [
                '"literal\\n[bold]é[/bold]"',
                '["2","literal\\n[bold]é[/bold]"]',
            ]
            await pilot.press("left")
            await pilot.pause()
            assert vm.results.selection == (1, 0)
            await pilot.press("alt+enter")
            await pilot.pause()
            assert isinstance(app.screen, AthenaResultCellModal)
            body = app.screen.query_one(TextArea)
            await pilot.press("enter", "alt+c", "alt+s", "ctrl+enter", "alt+f")
            assert isinstance(app.screen, AthenaResultCellModal)
            assert body.text == "2"
            assert len(copies) == 2
            assert vm.results.sort_column is None
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("alt+f")
            await pilot.pause()
            assert isinstance(app.screen, AthenaResultFilterModal)
            field = app.screen.query_one(Input)
            field.value = "missing"
            await pilot.press("alt+c", "alt+s", "ctrl+enter", "enter")
            await pilot.pause()
            assert vm.results.filter_text == "missing"
            assert vm.results.selection is None
            await pilot.press("alt+r")
            await pilot.pause()
            assert vm.results.filter_text == ""
            assert vm.results.selection is None
            vm.results.select_cell(1, 1)
            await pilot.pause()
            app._populate_command_palette()
            ctx.command_palette_vm.set_active_service("athena")
            await pilot.press("ctrl+k")
            ctx.command_palette_vm.set_active_service("athena")
            await pilot.press(*"Inspect Athena cell")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AthenaResultCellModal)
            await pilot.press("escape")
            await pilot.pause()
            await vm.select_view("query")
            await pilot.pause()
            editor = app.query_one("#athena-editor", TextArea)
            await focus_and_settle(editor)
            await pilot.press("alt+c", "alt+shift+c", "alt+f", "alt+s", "alt+r", "alt+enter")
            assert len(copies) == 2
            assert len(app.screen_stack) == 1
            assert client.start_calls == []
            assert result_calls == [("shortcuts", None)]
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_athena_result_control_configured_key_dispatch_replaces_default(app_context_factory):
    from aws_tui.infra.keymap_store import KeymapStore

    ctx = app_context_factory()
    ctx.keymap_store = KeymapStore(overlay={"athena.copy_cell": "ctrl+g"})
    app = AwsTuiApp(ctx)
    calls = []
    app._actions.register("athena.copy_cell", lambda: calls.append("copy"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("alt+c")
        assert calls == []
        await pilot.press("ctrl+g")
        assert calls == ["copy"]
