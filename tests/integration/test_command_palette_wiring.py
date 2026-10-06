"""Integration: `:` / `Ctrl+K` open the command palette; entries dispatch."""

from __future__ import annotations

from dataclasses import replace

import pytest
from textual.containers import Container
from vmx import NULL_DISPATCHER

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.data_catalog import TableFormat
from aws_tui.infra.clipboard import InMemoryClipboard
from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import drain_workers, wait_until
from tests.unit.vm.glue._fake_glue import seeded_glue
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector


async def _host_demo_service(app, ctx, service_id):
    ctx.root_vm.services_menu.switch_service_command.execute(service_id)
    from aws_tui.ui.widgets.dual_pane import DualPane
    from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
    from aws_tui.ui.widgets.settings_view import SettingsView

    page_types = {
        "athena": AthenaPage,
        "glue": GluePage,
        "s3": DualPane,
        "emr-serverless": EmrServerlessPage,
        "settings": SettingsView,
    }
    await wait_until(
        lambda: (
            ctx.root_vm.content_host.current_id == service_id
            and (
                service_id not in page_types
                or (
                    bool(app.query(page_types[service_id]))
                    and app.query_one(page_types[service_id]).vm is ctx.root_vm.content_host.current
                )
            )
        ),
        what=f"hosted {service_id}",
    )
    await drain_workers(app)


@pytest.mark.asyncio
async def test_palette_projects_only_global_and_active_service_commands(tmp_path) -> None:
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for service in ("glue", "athena", "s3", "emr-serverless", "settings"):
                await _host_demo_service(app, ctx, service)
                app._populate_command_palette()
                origin = app._capture_discovery_origin()
                expected = tuple(
                    row for row in app._project_discovery_actions(origin) if row.available
                )
                assert {row.id: row for row in ctx.command_palette_vm.filtered_entries} == {
                    row.id: row for row in expected
                }
                assert all(app._actions.has(row.id) for row in expected)
                if service == "athena":
                    ids = {row.id for row in expected}
                    assert "glue.jobs" not in ids
                    assert "athena.cancel" not in ids
                    assert "athena.load_more" not in ids
                if service == "settings":
                    assert all(not row.service_ids for row in expected)
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def _prepare_marked_parent_cursor(app, ctx, pilot):
    from aws_tui.demo.in_memory_fs import InMemoryFS
    from aws_tui.domain.filesystem import PathRef
    from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot

    await pilot.pause()
    await drain_workers(app)
    pane = ctx.root_vm.content_host.current.right
    fs = InMemoryFS()
    await fs.mkdir(PathRef(("folder",)))

    async def data():
        yield b"marked contents"

    await fs.write_stream(PathRef(("folder", "marked.txt")), data())
    await pane.swap_provider(fs, identity_label="fixture", path_protocol="")
    await pane.navigate_to(PathRef(("folder",)))
    pane.mark_at(
        next(i for i, entry in enumerate(pane.filtered_entries) if entry.name == "marked.txt")
    )
    pane.move_cursor_to(
        next(i for i, entry in enumerate(pane.filtered_entries) if entry.is_parent_link)
    )
    ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
    app.set_focus(None)
    await pilot.pause()
    assert pane.selected_entry.is_parent_link
    assert [entry.name for entry in pane.marked_entries] == ["marked.txt"]
    return pane, fs


@pytest.mark.parametrize(("action", "label"), [("pane.copy", "Copy"), ("pane.delete", "Delete")])
async def test_marked_parent_cursor_help_and_palette_open_real_confirmation(
    app_context_factory,
    action,
    label,
):
    from aws_tui.domain.filesystem import PathRef
    from aws_tui.ui.widgets.confirm_modal import ConfirmModal
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, fs = await _prepare_marked_parent_cursor(app, ctx, pilot)
        # Compact footer intentionally retains its cursor-only parent denial.
        assert {"pane.copy", "pane.delete"} <= app._readiness_disabled()
        await pilot.press("question_mark")
        await wait_until(lambda: isinstance(app.screen, HelpModal), what="marked-target Help")
        assert {"pane.copy", "pane.delete"} <= {
            row.action_id for row in app.screen.query(HelpActionRow)
        }
        await pilot.press("escape", "ctrl+k")
        await pilot.pause()
        assert {"pane.copy", "pane.delete"} <= {
            row.action_id for row in app.screen.query(".palette-item")
        }
        await pilot.press(*f"{label} selected entries")
        await pilot.pause()
        assert [row.id for row in ctx.command_palette_vm.filtered_entries] == [action]
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="marked-target confirmation"
        )
        request = ctx.confirm_vm.request
        assert request.title == f"{label} 1 item?"
        assert request.confirm_label == label
        assert request.danger is (action == "pane.delete")
        assert request.paths[0].path.endswith("/marked.txt")
        await pilot.press("escape")
        await drain_workers(app)
        assert not ctx.confirm_vm.is_open
        assert not app._confirmation_pending
        assert pane.selected_entry.is_parent_link
        assert [entry.name for entry in pane.marked_entries] == ["marked.txt"]
        assert [entry.name for entry in await fs.list(PathRef(("folder",)))] == ["marked.txt"]
        assert ctx.transfer_journal.load_history() == ()


async def test_unmarked_parent_cursor_omits_copy_delete_from_help_and_palette(app_context_factory):
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, _fs = await _prepare_marked_parent_cursor(app, ctx, pilot)
        pane.set_marked_entries(pane.marked_entries, marked=False)
        assert pane.marked_entries == ()
        await pilot.press("question_mark")
        await wait_until(lambda: isinstance(app.screen, HelpModal), what="unmarked-parent Help")
        assert {"pane.copy", "pane.delete"}.isdisjoint(
            row.action_id for row in app.screen.query(HelpActionRow)
        )
        await pilot.press("escape", "ctrl+k")
        await pilot.pause()
        assert {"pane.copy", "pane.delete"}.isdisjoint(
            row.action_id for row in app.screen.query(".palette-item")
        )
        await pilot.press("escape")


@pytest.mark.parametrize(("action", "label"), [("pane.copy", "Copy"), ("pane.delete", "Delete")])
async def test_marked_parent_cursor_palette_rechecks_marks_after_dismissal(
    app_context_factory,
    monkeypatch,
    action,
    label,
):
    from aws_tui.ui.widgets.confirm_modal import ConfirmModal

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, _fs = await _prepare_marked_parent_cursor(app, ctx, pilot)
        await pilot.press("ctrl+k")
        await pilot.press(*f"{label} selected entries")
        await pilot.pause()
        assert [row.id for row in ctx.command_palette_vm.filtered_entries] == [action]
        calls = []
        invoke = app._actions.invoke
        after_refresh = app.call_after_refresh
        cleared = []

        def spy(action_id):
            calls.append(action_id)
            return invoke(action_id)

        def schedule(callback, *args, **kwargs):
            if getattr(callback, "__name__", None) == "after_dismissal":

                def clear_then_release():
                    assert not isinstance(app.screen, CommandPalette)
                    pane.set_marked_entries(pane.marked_entries, marked=False)
                    cleared.append(True)
                    callback()

                return after_refresh(clear_then_release)
            return after_refresh(callback, *args, **kwargs)

        monkeypatch.setattr(app._actions, "invoke", spy)
        monkeypatch.setattr(app, "call_after_refresh", schedule)
        await pilot.press("enter")
        await pilot.pause()
        assert cleared == [True]
        assert pane.marked_entries == ()
        assert pane.selected_entry.is_parent_link
        assert calls == ["pane.descend"], "only the existing modal Enter router may run"
        assert action not in calls
        assert not isinstance(app.screen, (CommandPalette, ConfirmModal))
        assert not ctx.confirm_vm.is_open
        assert not app._confirmation_pending
        assert ctx.command_palette_vm._pending_tasks == {}


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
        assert "Cycle theme" in labels
        assert "Glue jobs" not in labels
        assert "Athena query" not in labels
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
        await app._app_ctx.command_palette_vm._actions["app.cycle_theme"]()
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
            await _pilot.pause()
            await ctx.root_vm.content_host.set_content(vm, service_id="glue", already_prepared=True)
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
                return {
                    hint.action_id
                    for hint in legend.actions
                    if not hint.enabled and hint.action_id != "glue.compare_tables"
                }

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
            assert next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

            await vm.select_view("catalog")
            await wait_until(
                lambda: disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"},
                what="Glue catalog to restore available table handoffs",
            )
            assert disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"}
            assert next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

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
            assert next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

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
            assert next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

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
            assert next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

            assert await vm.catalog.iceberg.select_view("history")
            await wait_until(
                lambda: disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"},
                what="Iceberg history to disable snapshot time travel",
            )
            assert disabled_actions() == {"glue.time_travel_in_athena", "glue.load_more"}
            assert next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

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
            assert not next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

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
            await _pilot.pause()
            await ctx.root_vm.content_host.set_content(vm, service_id="glue", already_prepared=True)
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
                return {
                    hint.action_id
                    for hint in legend.actions
                    if not hint.enabled and hint.action_id != "glue.compare_tables"
                }

            assert await vm.catalog.iceberg.select_view("snapshots")
            assert vm.catalog.iceberg.select_snapshot(43)
            await wait_until(
                lambda: disabled_actions() == {"glue.load_more"},
                what="selected snapshot to enable handoffs before disposal",
            )
            assert disabled_actions() == {"glue.load_more"}
            assert next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

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
            assert not next(
                hint for hint in legend.actions if hint.action_id == "glue.compare_tables"
            ).enabled

            toast_count = len(ctx.root_vm.chrome.toast_stack.toasts)
            app.action_copy_glue_table_reference()
            await app.action_query_glue_table_in_athena()
            await app.action_time_travel_glue_table_in_athena()
            assert len(ctx.root_vm.chrome.toast_stack.toasts) == toast_count
    finally:
        vm.dispose()


async def test_athena_loaded_result_controls_have_registered_scoped_palette_actions(tmp_path):
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    controls = {
        "inspect_cell",
        "copy_cell",
        "copy_row",
        "filter_results",
        "sort_results",
        "reset_results",
    }
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "athena")
            for action in controls:
                assert app._actions.has("athena." + action)
            vm = ctx.root_vm.content_host.current
            await vm.select_view("results")
            await pilot.pause()
            app._populate_command_palette()
            entries = {entry.id for entry in ctx.command_palette_vm.filtered_entries}
            assert {"athena.filter_results", "athena.reset_results"} <= entries
            assert "athena.copy_cell" not in entries
            await _host_demo_service(app, ctx, "s3")
            app._populate_command_palette()
            assert not {"athena." + action for action in controls} & {
                entry.id for entry in ctx.command_palette_vm.filtered_entries
            }
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_athena_result_shortcuts_arrows_modal_containment_and_palette_dispatch(
    tmp_path,
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

    ctx = build_app_context(
        config_dir=tmp_path, cache_dir=tmp_path, demo=True, clipboard=InMemoryClipboard()
    )
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
            await pilot.pause()
            await ctx.root_vm.content_host.set_content(
                vm, service_id="athena", already_prepared=True
            )
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
    ctx.keymap_store = KeymapStore(
        overlay={"glue.compare_tables": [], "athena.copy_cell": "ctrl+g"}
    )
    app = AwsTuiApp(ctx)
    calls = []
    app._actions.register("athena.copy_cell", lambda: calls.append("copy"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("alt+c")
        assert calls == []
        await pilot.press("ctrl+g")
        assert calls == ["copy"]


@pytest.mark.parametrize("remapped", [False, True])
async def test_athena_result_palette_labels_show_actual_configured_keys(
    tmp_path, remapped, monkeypatch
):
    from aws_tui.domain.query import ResultColumn, ResultPage
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.vm.chrome.action_catalog import format_effective_keys

    labels = {
        "athena.inspect_cell": "Inspect Athena cell",
        "athena.copy_cell": "Copy Athena cell as JSON",
        "athena.copy_row": "Copy Athena row as JSON",
        "athena.filter_results": "Filter loaded Athena results",
        "athena.sort_results": "Sort loaded Athena results",
        "athena.reset_results": "Reset loaded Athena results",
    }
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    if remapped:
        ctx.keymap_store = KeymapStore(
            overlay={
                "glue.compare_tables": [],
                "athena.inspect_cell": "ctrl+shift+i",
                "athena.copy_cell": ["ctrl+g", "alt+g"],
                "athena.copy_row": "alt+shift+x",
                "athena.filter_results": "alt+d",
                "athena.sort_results": "alt+n",
                "athena.reset_results": "alt+z",
            }
        )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "athena")
            vm = ctx.root_vm.content_host.current

            async def result_page(_execution_id, *, start_token=None):
                return ResultPage(
                    (ResultColumn("col", "varchar", "NULLABLE"),), (("value",),), None
                )

            monkeypatch.setattr(vm.results._client, "get_results_page", result_page)
            await vm.results.load("configured-key-result")
            assert vm.results.select_cell(0, 0)
            await vm.select_view("results")
            await pilot.pause()
            await pilot.press("colon")
            await pilot.pause()
            actual = {row.action_id: row for row in app.screen.query(".palette-item")}
            for action, label in labels.items():
                assert actual[action].presentation.label == label
                assert actual[action].presentation.effective_keys == ctx.keymap_store.resolve(
                    action
                )
                assert label in str(actual[action].content)
                assert format_effective_keys(ctx.keymap_store.resolve(action)) in str(
                    actual[action].content
                )
            assert actual["athena.query"].presentation.label == "Athena query"
            await pilot.press("escape")
            await _host_demo_service(app, ctx, "s3")
            app._populate_command_palette()
            assert not set(labels) & {entry.id for entry in ctx.command_palette_vm.filtered_entries}
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_help_projects_hosted_athena(tmp_path):
    from aws_tui.composition import build_app_context
    from aws_tui.ui.widgets.athena.page import AthenaPage
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("athena")
            await wait_until(
                lambda: (
                    ctx.root_vm.content_host.current_id == "athena"
                    and bool(app.query(AthenaPage))
                    and app.query_one(AthenaPage).vm is ctx.root_vm.content_host.current
                ),
                what="hosted Athena page",
            )
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="Help opened")
            sections = [str(row.content) for row in app.screen.query(".help-section")]
            assert any("Athena" in text and "Loaded Athena" not in text for text in sections)
            assert any("Global" in text for text in sections)
            ids = {row.action_id for row in app.screen.query(HelpActionRow)}
            assert "athena.query" in ids
            assert not {"glue.jobs", "emr.cancel", "athena.cancel", "athena.load_more"} & ids
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_palette_renders_effective_key(app_context_factory):
    from aws_tui.infra.keymap_store import KeymapStore

    ctx = app_context_factory()
    ctx.keymap_store = KeymapStore(overlay={"glue.compare_tables": [], "app.cycle_theme": "ctrl+g"})
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("colon")
        await wait_until(lambda: isinstance(app.screen, CommandPalette), what="palette opened")
        row = next(
            row for row in app.screen.query(".palette-item") if "Cycle theme" in str(row.content)
        )
        assert "Ctrl+g" in str(row.content)


async def test_hosted_athena_unavailable_commands_are_omitted_from_both_surfaces(tmp_path):
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "athena")
            page = app.query_one(AthenaPage)
            page.vm.query.set_sql("")
            assert not page.vm.query.execute_command.can_execute()
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="Athena Help opened")
            help_ids = {row.action_id for row in app.screen.query(HelpActionRow)}
            unavailable = {
                "athena.execute",
                "athena.cancel",
                "athena.load_more",
                "glue.jobs",
                "emr.cancel",
            }
            assert not unavailable & help_ids
            assert all(app._actions.has(action_id) for action_id in help_ids)
            await pilot.press("escape")
            await pilot.press("ctrl+k")
            await wait_until(
                lambda: isinstance(app.screen, CommandPalette), what="Athena palette opened"
            )
            palette_ids = {row.action_id for row in app.screen.query(".palette-item")}
            assert palette_ids == help_ids
            assert not unavailable & palette_ids
            assert all(app._actions.has(action_id) for action_id in palette_ids)
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_palette_arrows_with_focused_input_select_and_execute_second_match(
    app_context_factory,
):
    from textual.widgets import Input

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    calls = []
    app._actions.register("app.cycle_theme", lambda: calls.append("cycle"))
    app._actions.register("app.themes", lambda: calls.append("picker"))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app._focused_file_pane()
        before = (pane.path, pane.cursor_index, pane.listing_revision)
        await pilot.press("ctrl+k")
        await pilot.press(*"theme")
        await pilot.pause()
        entries = ctx.command_palette_vm.filtered_entries
        assert len(entries) == 2
        assert isinstance(app.focused, Input)
        await pilot.press("down")
        await pilot.pause()
        assert ctx.command_palette_vm.selected_index == 1
        await pilot.press("up")
        await pilot.pause()
        assert ctx.command_palette_vm.selected_index == 0
        await pilot.press("down", "enter")
        await wait_until(lambda: bool(calls), what="second palette match invoked")
        assert calls == ["cycle" if entries[1].id == "app.cycle_theme" else "picker"]
        assert not isinstance(app.screen, CommandPalette)
        assert (pane.path, pane.cursor_index, pane.listing_revision) == before


@pytest.mark.parametrize("keys", [["ctrl+g", "alt+g"], []])
async def test_two_openings_share_row_label_keys_and_literal_text(app_context_factory, keys):
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal
    from aws_tui.vm.chrome.action_catalog import format_effective_keys

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        for overlay in (
            {"app.cycle_theme": "ctrl+y"},
            {"glue.compare_tables": [], "app.cycle_theme": keys},
        ):
            ctx.keymap_store = KeymapStore(overlay=overlay)
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="Help opening")
            help_row = next(
                row for row in app.screen.query(HelpActionRow) if row.action_id == "app.cycle_theme"
            )
            help_presentation = help_row.presentation
            assert help_presentation.label == "Cycle theme"
            assert help_presentation.effective_keys == ctx.keymap_store.resolve("app.cycle_theme")
            assert format_effective_keys(help_presentation.effective_keys) in str(help_row.content)
            assert "Cycle theme" in str(help_row.content)
            await pilot.press("escape")
            await pilot.press("colon")
            await wait_until(lambda: isinstance(app.screen, CommandPalette), what="palette opening")
            palette_row = next(
                row
                for row in app.screen.query(".palette-item")
                if row.action_id == "app.cycle_theme"
            )
            assert palette_row.presentation.label == help_presentation.label
            assert palette_row.presentation.effective_keys == help_presentation.effective_keys
            assert format_effective_keys(help_presentation.effective_keys) in str(
                palette_row.content
            )
            assert "Cycle theme" in str(palette_row.content)
            await pilot.press("escape")


async def test_missing_handler_omitted_and_unregister_after_open_never_dispatches(
    app_context_factory,
    monkeypatch,
):
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    calls = []
    invoke = app._actions.invoke

    def spy(action_id):
        calls.append(action_id)
        return invoke(action_id)

    monkeypatch.setattr(app._actions, "invoke", spy)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._actions.unregister("app.cycle_theme")
        await pilot.press("question_mark")
        await wait_until(lambda: isinstance(app.screen, HelpModal), what="Help opened")
        assert "app.cycle_theme" not in {row.action_id for row in app.screen.query(HelpActionRow)}
        await pilot.press("escape")
        await pilot.press("colon")
        await pilot.pause()
        assert "app.cycle_theme" not in {row.action_id for row in app.screen.query(".palette-item")}
        await pilot.press("escape")
        app._actions.register("app.cycle_theme", lambda: None)
        await pilot.press("colon")
        await pilot.press(*"Cycle theme")
        await pilot.pause()
        assert [row.id for row in ctx.command_palette_vm.filtered_entries] == ["app.cycle_theme"]
        app._actions.unregister("app.cycle_theme")
        calls.clear()
        pane = app._focused_file_pane()
        before = (pane.path, pane.listing_revision, pane.cursor_index)
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, CommandPalette)
        # Production Enter routes through pane.descend to the active modal.
        assert calls == ["pane.descend"]
        assert calls.count("app.cycle_theme") == 0
        assert (pane.path, pane.listing_revision, pane.cursor_index) == before
        assert ctx.command_palette_vm._pending_tasks == {}


async def test_refresh_preserves_search_selection_and_standalone_entries(app_context_factory):
    from aws_tui.vm.chrome.command_palette_vm import PaletteEntry

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        vm = ctx.command_palette_vm
        vm.register_entry(PaletteEntry("standalone", "Standalone", "Test"), lambda: None)
        await pilot.press("colon")
        await pilot.press(*"theme")
        await pilot.pause()
        vm.move_selection_command.execute(1)
        selected = vm.filtered_entries[vm.selected_index].id
        app._refresh_discovery_surfaces()
        await pilot.pause()
        assert vm.filter_text == "theme"
        assert vm.filtered_entries[vm.selected_index].id == selected
        await pilot.press("escape")
        await pilot.press("colon")
        await pilot.pause()
        assert "standalone" in {row.id for row in vm.filtered_entries}


async def test_detached_hosted_s3_blocks_retained_callback(app_context_factory):
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    calls = []
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._actions.register("app.cycle_theme", lambda: calls.append("cycle"))
        origin = app._capture_discovery_origin()
        await app.query_one("#content-host", Container).remove_children()
        await app._invoke_discovery_action("app.cycle_theme", origin)
        assert calls == []


async def test_captured_load_more_focus_survives_palette_input(tmp_path, monkeypatch):
    from textual.widgets import Input

    from aws_tui.ui.widgets.context_picker import ContextPicker

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "athena")
            page = app.query_one(AthenaPage)
            # The public pager predicate reads its VM-owned worker.
            monkeypatch.setattr(type(page.vm), "has_more_workgroups", property(lambda _self: True))
            monkeypatch.setattr(
                type(page.vm), "is_loading_more_workgroups", property(lambda _self: False)
            )
            picker = page.query_one("#athena-workgroup", ContextPicker)
            picker.focus()
            await pilot.pause()
            origin = app._capture_discovery_origin()
            assert "athena-workgroup" in origin.focused_ids
            assert page.can_load_more(focused_ids=origin.focused_ids)
            await pilot.press("colon")
            await pilot.pause()
            assert isinstance(app.focused, Input)
            assert page.can_load_more(focused_ids=origin.focused_ids)
            assert "athena.load_more" in {
                row.action_id for row in app.screen.query(".palette-item")
            }
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_navigation_discovery_requires_an_actionable_focused_target(tmp_path):
    from textual.widgets import TextArea

    from aws_tui.ui.widgets.nav_menu import NavMenu

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "athena")
            app.query_one("#athena-editor", TextArea).focus()
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            assert "pane.descend" not in {
                row.action_id for row in app.screen.query(".palette-item")
            }
            await pilot.press("escape")
            await _host_demo_service(app, ctx, "s3")
            app.query_one(NavMenu).focus()
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            assert "pane.descend" in {row.action_id for row in app.screen.query(".palette-item")}
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_help_projects_s3_and_settings_hosted_contexts(tmp_path):
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for service in ("s3", "settings"):
                await _host_demo_service(app, ctx, service)
                origin = app._capture_discovery_origin()
                expected = {
                    row.id: row for row in app._project_discovery_actions(origin) if row.available
                }
                await pilot.press("question_mark")
                await wait_until(lambda: isinstance(app.screen, HelpModal), what="contextual Help")
                actual = {
                    row.action_id: row.presentation for row in app.screen.query(HelpActionRow)
                }
                assert actual == expected
                headings = [str(row.content) for row in app.screen.query(".help-section")]
                assert any("Global —" in heading for heading in headings)
                assert any("S3 —" in heading for heading in headings) is (service == "s3")
                assert "athena.query" not in actual
                assert "glue.jobs" not in actual
                assert "emr.next_application" not in actual
                assert all(app._actions.has(action) for action in actual)
                await pilot.press("escape")
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_switch_source_omitted_when_fresh_s3_candidates_have_only_local(
    app_context_factory,
    monkeypatch,
):
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        monkeypatch.setattr(ctx.connection_resolver, "list", lambda: ())
        await pilot.press("colon")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        assert "app.swap_source" not in {row.action_id for row in app.screen.query(".palette-item")}
        await pilot.press("escape")
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpModal)
        assert "app.swap_source" not in {row.action_id for row in app.screen.query(HelpActionRow)}


async def test_hosted_emr_picker_help_palette_and_dispatch_share_activation(tmp_path):
    from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "emr-serverless")
            setup = ctx.root_vm.content_host._setup_task
            if setup is not None:
                await setup
            await drain_workers(app)
            picker = app.query_one(ApplicationPicker)
            picker.focus()
            await pilot.pause()
            vm = ctx.root_vm.content_host.current
            slot = ctx.focus_coordinator.focused_slot
            assert app.focused is picker
            assert not picker.is_open
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="EMR Help opened")
            help_rows = {row.action_id: row for row in app.screen.query(HelpActionRow)}
            assert "pane.descend" in help_rows, "actionable EMR picker must appear in Help"
            presentation = help_rows["pane.descend"].presentation
            assert presentation.label == "Open focused item"
            assert presentation.available
            assert presentation.effective_keys == ctx.keymap_store.resolve("pane.descend")
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is picker
            assert ctx.root_vm.content_host.current is vm
            assert ctx.focus_coordinator.focused_slot is slot
            assert not picker.is_open
            await pilot.press("ctrl+k")
            await wait_until(lambda: isinstance(app.screen, CommandPalette), what="EMR palette")
            await pilot.press(*"Open focused item")
            await pilot.pause()
            rows = list(app.screen.query(".palette-item"))
            assert len(rows) == 1
            assert rows[0].action_id == "pane.descend"
            assert rows[0].presentation == presentation
            await pilot.press("enter")
            await wait_until(lambda: picker.is_open, what="palette dispatch opened EMR picker")
            assert not isinstance(app.screen, CommandPalette)
            assert ctx.root_vm.content_host.current is vm
            await pilot.press("escape")
            await pilot.pause()
            assert not picker.is_open
            assert app.focused is picker
            assert ctx.focus_coordinator.focused_slot is slot
            assert ctx.command_palette_vm._pending_tasks == {}
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("target", ["runs", "logs"])
async def test_hosted_emr_custom_activation_targets_dispatch_from_palette(
    tmp_path, monkeypatch, target
):
    from aws_tui.domain.emr_logs import FilterMode, LogFilter
    from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
    from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogsState

    (tmp_path / "config.toml").write_text(
        '[connections.dev]\nkind = "aws"\nprofile = "dev"\nregion = "us-east-1"\n'
        '[defaults]\nconnection = "dev"\n'
    )
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    calls = []
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "emr-serverless")
            setup = ctx.root_vm.content_host._setup_task
            if setup is not None:
                await setup
            await drain_workers(app)
            vm = ctx.root_vm.content_host.current
            await wait_until(
                lambda: vm.applications.sorted_applications, what="demo EMR applications loaded"
            )
            application = next(
                application
                for application in vm.applications.sorted_applications
                if "etl-pipeline" in application.name
            )
            await vm.select_application(application.id)
            run = next(run for run in vm.job_runs.runs if "etl-success" in run.job_run_id)
            await vm.select_job_run(run.job_run_id)
            await drain_workers(app)
            if target == "runs":
                control = app.query_one(JobRunsPane)
                original = vm.select_job_run

                async def select(run_id):
                    calls.append("run")
                    await original(run_id)

                monkeypatch.setattr(vm, "select_job_run", select)
                assert vm.job_runs.runs
            else:
                control = app.query_one(JobRunLogsPane)
                original_load = vm.job_run_logs.load
                vm.job_run_logs.set_filter(LogFilter((), mode=FilterMode.PASSTHROUGH))
                assert vm.job_run_logs.state is LogsState.IDLE

                async def load():
                    calls.append("logs")
                    await original_load()

                monkeypatch.setattr(vm.job_run_logs, "load", load)
            control.focus()
            await pilot.pause()
            assert app.focused is control
            slot = ctx.focus_coordinator.focused_slot
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="custom target Help")
            help_rows = {row.action_id: row for row in app.screen.query(HelpActionRow)}
            assert "pane.descend" in help_rows, "actionable EMR pane must appear in Help"
            presentation = help_rows["pane.descend"].presentation
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is control
            assert ctx.focus_coordinator.focused_slot is slot
            assert calls == []
            await pilot.press("ctrl+k")
            await pilot.press(*"Open focused item")
            await pilot.pause()
            row = app.screen.query_one(".palette-item")
            assert row.action_id == "pane.descend"
            assert row.presentation == presentation
            await pilot.press("enter")
            await wait_until(
                lambda: calls == ["run" if target == "runs" else "logs"],
                what="custom target dispatched",
            )
            await drain_workers(app)
            assert app.focused is control
            assert ctx.root_vm.content_host.current is vm
            assert ctx.focus_coordinator.focused_slot is slot
            assert not isinstance(app.screen, CommandPalette)
            assert ctx.command_palette_vm._pending_tasks == {}
            if target == "logs":
                assert vm.job_run_logs.state is LogsState.READY
                assert vm.job_run_logs.current_file is not None
                assert vm.job_run_logs.lines
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_hosted_glue_custom_tab_is_actionable_but_metadata_rows_are_inert(
    app_context_factory,
):
    from textual.widgets import DataTable

    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = app_context_factory()
    fake = seeded_glue()
    ref = fake.tables["analytics"][0].ref
    fake.table_details[ref] = replace(fake.table_details[ref], table_format=TableFormat.ICEBERG)
    vm = GluePageVM(
        client=fake,
        iceberg_inspector=RecordingInspector(),
        connection=Connection(
            name="dev", kind="aws", region="us-east-1", source="test", profile="dev"
        ),
        hub=ctx.hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    await vm.setup()
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await ctx.root_vm.content_host.set_content(vm, service_id="glue", already_prepared=True)
            host = app.query_one("#content-host", Container)
            await host.remove_children()
            await host.mount(
                GluePage(
                    vm, hub=ctx.hub, focus_coordinator=ctx.focus_coordinator, id="content-glue-page"
                )
            )
            await vm.select_view("catalog")
            await vm.select_table(ref.table_name)
            assert await vm.catalog.iceberg.select_view("snapshots")
            await pilot.pause()
            tab = app.query_one("#glue-iceberg-tab-history")
            tab.focus()
            await pilot.pause()
            assert app.focused is tab
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="Glue tab Help")
            assert "pane.descend" in {row.action_id for row in app.screen.query(HelpActionRow)}
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is tab
            await pilot.press("ctrl+k")
            await pilot.press(*"Open focused item")
            await pilot.pause()
            assert app.screen.query_one(".palette-item").action_id == "pane.descend"
            await pilot.press("enter")
            await wait_until(
                lambda: vm.catalog.iceberg.active_view == "history",
                what="palette selected Iceberg history",
            )
            await drain_workers(app)
            table = app.query_one("#glue-iceberg-table", DataTable)
            assert table.row_count > 0
            table.focus()
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert "pane.descend" not in {
                row.action_id for row in app.screen.query(".palette-item")
            }
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is table
    finally:
        vm.dispose()


@pytest.mark.parametrize("target", ["detail", "empty-runs", "empty-logs"])
async def test_hosted_emr_inert_targets_are_omitted_and_restore_focus(tmp_path, target):
    from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
    from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
    from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
    from aws_tui.ui.widgets.help_modal import HelpActionRow, HelpModal

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "emr-serverless")
            setup = ctx.root_vm.content_host._setup_task
            if setup is not None:
                await setup
            await drain_workers(app)
            vm = ctx.root_vm.content_host.current
            if target == "detail":
                control = app.query_one(JobRunDetailPane)
            elif target == "empty-runs":
                vm.job_runs.set_application(None)
                control = app.query_one(JobRunsPane)
                assert vm.job_runs.runs == ()
            else:
                vm.job_run_logs.set_target(None, None, None)
                control = app.query_one(JobRunLogsPane)
            control.focus()
            await pilot.pause()
            slot = ctx.focus_coordinator.focused_slot
            assert app.focused is control
            await pilot.press("question_mark")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="inert EMR Help")
            assert "pane.descend" not in {row.action_id for row in app.screen.query(HelpActionRow)}
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is control
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert "pane.descend" not in {
                row.action_id for row in app.screen.query(".palette-item")
            }
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is control
            assert ctx.focus_coordinator.focused_slot is slot
            assert ctx.root_vm.content_host.current is vm
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_hosted_emr_retained_activation_rechecks_loadability(tmp_path, monkeypatch):
    from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
    from aws_tui.vm.emr_serverless.job_run_logs_vm import LogsState

    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path, demo=True)
    app = AwsTuiApp(ctx)
    calls = []
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host_demo_service(app, ctx, "emr-serverless")
            setup = ctx.root_vm.content_host._setup_task
            if setup is not None:
                await setup
            await drain_workers(app)
            logs = app.query_one(JobRunLogsPane)
            vm = ctx.root_vm.content_host.current
            vm.job_run_logs.set_target("test-app", "test-run", "s3://test-logs/logs")
            logs.focus()
            await pilot.pause()
            origin = app._capture_discovery_origin()
            assert next(
                row for row in app._project_discovery_actions(origin) if row.id == "pane.descend"
            ).available
            await pilot.press("ctrl+k")
            await pilot.press(*"Open focused item")
            await pilot.pause()
            assert app.screen.query_one(".palette-item").action_id == "pane.descend"
            invoke = app._actions.invoke

            def spy(action_id):
                calls.append(action_id)
                return invoke(action_id)

            monkeypatch.setattr(app._actions, "invoke", spy)
            # Keep the displayed row/callback, then change the underlying state
            # without a projection event: dispatch must revalidate regardless.
            monkeypatch.setattr(vm.job_run_logs, "_state", LogsState.LOADING)
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, CommandPalette)
            assert calls == ["pane.descend"], "only the existing modal Enter router may run"
            assert ctx.command_palette_vm._pending_tasks == {}
            assert app.focused is logs
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_s3_directory_and_parent_activation_still_dispatch_after_modal_dismissal(
    app_context_factory,
):
    from aws_tui.demo.in_memory_fs import InMemoryFS
    from aws_tui.domain.filesystem import PathRef
    from aws_tui.ui.widgets.pane import Pane
    from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot

    fs = InMemoryFS()
    await fs.mkdir(PathRef(("folder",)))
    ctx = app_context_factory(fs=fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await drain_workers(app)
        pane = app.query_one("#pane-left", Pane).vm
        await pane.navigate_to(PathRef(()))
        for name, destination in (("folder", PathRef(("folder",))), ("..", PathRef(()))):
            pane.move_cursor_to(
                next(i for i, entry in enumerate(pane.filtered_entries) if entry.name == name)
            )
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_LEFT)
            app.set_focus(None)
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.press(*"Open focused item")
            await pilot.pause()
            assert app.screen.query_one(".palette-item").action_id == "pane.descend"
            await pilot.press("enter")
            await wait_until(
                lambda destination=destination: pane.path == destination,
                what="S3 palette navigation",
            )
            assert not isinstance(app.screen, CommandPalette)
        assert pane.path.is_root
        assert all(entry.name != ".." for entry in pane.filtered_entries)
