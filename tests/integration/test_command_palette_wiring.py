"""Integration: `:` / `Ctrl+K` open the command palette; entries dispatch."""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.command_palette import CommandPalette

_GLOBAL = {"Theme picker", "Cycle theme", "Settings", "Help", "Quit"}
_SOURCE = {"Switch source"}
_GLUE = {
    "Glue catalog",
    "Glue jobs",
    "Glue crawlers",
    "Choose Glue run state",
    "Choose Glue crawler state",
    "Copy Glue table reference",
    "Open table location in S3",
    "Query table in Athena",
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
        assert {entry.label for entry in vm.filtered_entries} == _GLOBAL | _SOURCE

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
        assert labels == _GLOBAL | _SOURCE
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
