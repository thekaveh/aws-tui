"""Integration: `:` / `Ctrl+K` open the command palette; entries dispatch."""

from __future__ import annotations

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.command_palette import CommandPalette

_CURATED = {"Theme picker", "Cycle theme", "Swap pane source", "Settings", "Help", "Quit"}


@pytest.mark.asyncio
async def test_populate_registers_curated_commands(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._populate_command_palette()
        vm = app._app_ctx.command_palette_vm
        labels = {e.label for e in vm.filtered_entries}
        assert labels >= _CURATED
        n = len(vm.filtered_entries)
        app._populate_command_palette()  # idempotent (register_entry dedups by id)
        assert len(vm.filtered_entries) == n


@pytest.mark.asyncio
async def test_colon_opens_command_palette(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("colon")  # ":" arrives as key "colon"
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
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
