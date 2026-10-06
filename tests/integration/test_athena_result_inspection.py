"""Loaded-result acceptance through the real demo app and configured keyboard routes."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Static, TextArea

from aws_tui.domain.query import ResultColumn, ResultPage
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.result_cell_modal import AthenaResultCellModal
from aws_tui.ui.widgets.athena.result_filter_modal import AthenaResultFilterModal
from aws_tui.ui.widgets.help_modal import HelpModal
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.snapshot.apps.demo_mode import DemoModeApp
from tests.snapshot.test_demo_mode import _dismiss_demo_startup_advisory

LITERAL = "full literal [bold]é[/bold]\nsecond line retained"
ROWS = (
    (None, ""),
    ("10", LITERAL),
    ("2", "NULL"),
    ("9007199254740993", "match"),
    ("9007199254740992", "match"),
)
COLUMNS = (ResultColumn("duplicate", "varchar", "NULLABLE"),) * 2


class LoadedPages:
    """Fails any provider operation not expressly admitted by the journey."""

    def __init__(self):
        self.calls = []
        self.allow_more = False
        self.block_more = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def start_query(self, *args, **kwargs):
        pytest.fail("result controls must never start a query")

    async def get_results_page(self, execution_id, *, start_token=None):
        self.calls.append((execution_id, start_token))
        if start_token is None:
            assert len(self.calls) == 1 or execution_id == "replacement"
            return ResultPage(COLUMNS, ROWS, "second")
        assert self.allow_more, "unrequested result page"
        assert start_token == "second"
        if self.block_more:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()  # Exercise a cancellation-resistant retired page.
        return ResultPage(COLUMNS, (("1", "match"),), None)


async def show_loaded_results(pilot, *, pages=None):
    """Seed one current fake execution, then use the mounted production view."""
    app = pilot.app
    await drain_workers(app)
    app.app_ctx.root_vm.services_menu.switch_service_command.execute("athena")
    await wait_until(lambda: bool(app.query(AthenaPage)), what="actual Athena page mounted")
    await drain_workers(app)
    page = app.query_one(AthenaPage)
    await wait_until(lambda: page.vm.context.database == "dev_events", what="demo Athena context")
    await _dismiss_demo_startup_advisory(pilot)
    pages = LoadedPages() if pages is None else pages
    page.vm.results._client = pages
    page.vm.query._client = pages
    await page.vm.results.load("acceptance")
    await pilot.press("3")
    table = app.query_one("#athena-results-table", DataTable)
    await wait_until(
        lambda: table.row_count == len(ROWS) and table.region.height > 0,
        what="loaded table rendered",
    )
    await focus_and_settle(table)
    return page, table, pages


async def select_cell(table, vm, row, column):
    table.move_cursor(row=row, column=column)
    await wait_until(
        lambda: vm.selection == (vm.visible_row_indices[row], column),
        what="original cell selection",
    )


async def apply_filter(pilot, text):
    await pilot.press("alt+f")
    await wait_until(
        lambda: isinstance(pilot.app.screen, AthenaResultFilterModal), what="filter dialog open"
    )
    field = pilot.app.screen.query_one(Input)
    await focus_and_settle(field)
    field.value = text
    await pilot.press("enter")
    await wait_until(
        lambda: not isinstance(pilot.app.screen, AthenaResultFilterModal),
        what="filter dialog applied",
    )


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_app_result_keyboard_journey(size, monkeypatch):
    app = DemoModeApp(theme="carbon")
    copies = []
    monkeypatch.setattr(app, "copy_value", lambda value, label: copies.append(value))
    try:
        async with app.run_test(size=size) as pilot:
            page, table, pages = await show_loaded_results(pilot)
            vm = page.vm.results
            original = vm.export_snapshot()
            await pilot.press("right", "down", "alt+enter")
            await wait_until(
                lambda: isinstance(app.screen, AthenaResultCellModal),
                what="second-row second-column inspector",
            )
            assert vm.selection == (1, 1)
            assert table.cursor_coordinate == Coordinate(1, 1)
            body = app.screen.query_one(TextArea)
            assert body.text == LITERAL
            assert body.read_only
            await pilot.press("enter", "ctrl+enter", "alt+s", "alt+f", "alt+c")
            assert body.text == LITERAL
            assert not copies
            await pilot.press("escape")
            await wait_until(lambda: table.has_focus, what="inspector returns table focus")
            assert table.cursor_coordinate == Coordinate(1, 1)
            assert vm.selection == (1, 1)
            await pilot.press("alt+c", "alt+shift+c", "left", "alt+c")
            assert copies == [
                '"full literal [bold]é[/bold]\\nsecond line retained"',
                '["10","full literal [bold]é[/bold]\\nsecond line retained"]',
                '"10"',
            ]
            await select_cell(table, vm, 0, 0)
            await pilot.press("alt+c", "right", "alt+c")
            await select_cell(table, vm, 2, 1)
            await pilot.press("alt+c")
            assert copies[-3:] == ["null", '""', '"NULL"']
            await select_cell(table, vm, 1, 0)
            for order in ((1, 2, 4, 3, 0), (3, 4, 2, 1, 0), (0, 1, 2, 3, 4)):
                await pilot.press("alt+s")
                await wait_until(
                    lambda order=order: vm.visible_row_indices == order,
                    what="keyboard sort projection",
                )
            assert vm.rows == original.rows
            assert vm.export_snapshot().next_token == original.next_token
            await apply_filter(pilot, "not present")
            footer = app.query_one("#athena-results-footer", Static)
            await wait_until(
                lambda: "0 visible" in str(footer.render()), what="zero-match loaded-only footer"
            )
            assert "5 loaded" in str(footer.render())
            assert "more available" in str(footer.render())
            assert "· local" in str(footer.render())
            assert vm.selection is None
            await pilot.press("alt+enter", "alt+c", "alt+shift+c", "alt+s")
            assert len(copies) == 6
            assert len(app.screen_stack) == 1
            await pilot.press("alt+r")
            await wait_until(lambda: table.row_count == 5, what="reset loaded table")
            await select_cell(table, vm, 3, 0)
            await pilot.press("alt+s")
            await apply_filter(pilot, "MATCH")
            assert vm.visible_row_indices == (4, 3)
            assert vm.selection == (3, 0)
            pages.allow_more = True
            assert app.app_ctx.root_vm.content_host.current_id == "athena"
            assert table.has_focus
            await pilot.press("l")
            assert pages.calls == [("acceptance", None), ("acceptance", "second")]
            await wait_until(
                lambda: len(vm.rows) == 6 and table.row_count == 3,
                what="explicit second page projected",
            )
            assert vm.visible_row_indices == (5, 4, 3)
            assert vm.selection == (3, 0)
            assert table.cursor_coordinate == Coordinate(2, 0)
            assert pages.calls == [("acceptance", None), ("acceptance", "second")]
            await pilot.press("alt+r", "1")
            editor = app.query_one("#athena-editor", TextArea)
            await focus_and_settle(editor)
            await pilot.press(
                "alt+enter", "alt+c", "alt+shift+c", "alt+f", "alt+s", "alt+r", "W", "C", "D", "i"
            )
            assert editor.text.endswith("WCDi")
            assert len(copies) == 6
            assert len(app.screen_stack) == 1
            await pilot.press("ctrl+k")
            await wait_until(
                lambda: len(app.screen_stack) == 2, what="global palette still opens from editor"
            )
            await pilot.press("escape")
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("retirement", ["context", "execution"])
async def test_actual_app_retirement_drops_late_page_and_inspector(size, retirement):
    app = DemoModeApp(theme="carbon")
    try:
        async with app.run_test(size=size) as pilot:
            page, table, pages = await show_loaded_results(pilot)
            vm = page.vm.results
            await select_cell(table, vm, 1, 1)
            await pilot.press("alt+s")
            await apply_filter(pilot, "literal")
            pages.allow_more = pages.block_more = True
            await focus_and_settle(table)
            assert app.app_ctx.root_vm.content_host.current_id == "athena"
            assert table.has_focus
            await pilot.press("l")
            await wait_until(pages.started.is_set, what="explicit delayed second page started")
            await pilot.press("alt+enter")
            await wait_until(
                lambda: isinstance(app.screen, AthenaResultCellModal),
                what="inspector before retirement",
            )
            old_generation = vm.projection_generation
            if retirement == "context":
                vm.set_context(replace(page.vm.context, database="retired_context"))
            else:
                replacement = asyncio.create_task(vm.load("replacement"))
                await wait_until(
                    lambda: vm.projection_generation != old_generation,
                    what="replacement execution retires page",
                )
            pages.release.set()
            if retirement == "execution":
                await replacement
            await drain_workers(app)
            await wait_until(
                lambda: not isinstance(app.screen, AthenaResultCellModal),
                what="retired overlay closed",
            )
            assert vm.projection_generation != old_generation
            assert vm.selection is None
            assert vm.filter_text == ""
            assert vm.sort_column is None
            assert ("1", "match") not in vm.rows
            assert len(vm.rows) == (0 if retirement == "context" else 5)
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_app_remapped_results_help_and_palette(size, monkeypatch):
    overlay = {
        "athena.inspect_cell": "ctrl+shift+i",
        "athena.copy_cell": "ctrl+g",
        "athena.copy_row": "alt+shift+x",
        "athena.filter_results": "alt+d",
        "athena.sort_results": "alt+n",
        "athena.reset_results": "alt+z",
    }
    app = DemoModeApp(theme="carbon", keymap=KeymapStore(overlay=overlay))
    copies = []
    monkeypatch.setattr(app, "copy_value", lambda value, label: copies.append(value))
    try:
        async with app.run_test(size=size) as pilot:
            page, table, pages = await show_loaded_results(pilot)
            await select_cell(table, page.vm.results, 1, 1)
            await pilot.press("alt+c", "ctrl+g", "alt+shift+x", "ctrl+shift+i")
            await wait_until(
                lambda: isinstance(app.screen, AthenaResultCellModal), what="remapped inspector"
            )
            assert copies == [
                '"full literal [bold]é[/bold]\\nsecond line retained"',
                '["10","full literal [bold]é[/bold]\\nsecond line retained"]',
            ]
            await pilot.press("escape", "alt+n")
            assert page.vm.results.sort_column == 1
            await pilot.press("alt+d")
            await wait_until(
                lambda: isinstance(app.screen, AthenaResultFilterModal), what="remapped filter"
            )
            await pilot.press("escape", "alt+z")
            assert page.vm.results.sort_column is None
            await pilot.press("?")
            await wait_until(lambda: isinstance(app.screen, HelpModal), what="actual app help")
            rows = " ".join(str(row.content) for row in app.screen.query(".help-row"))
            for key in ("Ctrl+Shift+i", "Ctrl+g", "Alt+Shift+x", "Alt+d", "Alt+n", "Alt+z"):
                assert key in rows
            await pilot.press("escape", "ctrl+k")
            await wait_until(lambda: len(app.screen_stack) == 2, what="actual Athena palette")
            palette = app.app_ctx.command_palette_vm
            labels = {entry.id: entry.label for entry in palette.filtered_entries}
            for action, key in overlay.items():
                assert f"({key})" in labels[action]
            await pilot.press(*"Inspect Athena cell", "enter")
            await wait_until(
                lambda: isinstance(app.screen, AthenaResultCellModal),
                what="palette result inspector dispatch",
            )
            assert app.screen.query_one(TextArea).text == LITERAL
            assert pages.calls == [("acceptance", None)]
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.parametrize("service", ["athena", "glue"])
@pytest.mark.parametrize(
    ("bindings", "pressed", "expected"),
    [
        ({}, "l", "current"),
        ({"glue.load_more": "alt+l", "athena.load_more": "alt+l"}, "alt+l", "current"),
        ({"glue.load_more": "alt+g", "athena.load_more": "alt+a"}, "alt+g", "glue"),
        ({"glue.load_more": "alt+g", "athena.load_more": "alt+a"}, "alt+a", "athena"),
    ],
)
async def test_actual_app_load_more_alias_and_explicit_routes(service, bindings, pressed, expected):
    app = DemoModeApp(theme="carbon", keymap=KeymapStore(overlay=bindings))
    calls = []

    async def athena():
        calls.append("athena")

    async def glue():
        calls.append("glue")

    app._actions.register("athena.load_more", athena)
    app._actions.register("glue.load_more", glue)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            app.app_ctx.root_vm.services_menu.switch_service_command.execute(service)
            await drain_workers(app)
            assert app.app_ctx.root_vm.content_host.current_id == service
            await pilot.press(pressed)
            assert calls == [service if expected == "current" else expected]
            await app.action_dispatch("glue.load_more")
            await app.action_dispatch("athena.load_more")
            assert calls[-2:] == ["glue", "athena"]
            await pilot.press("?")
            await wait_until(
                lambda: isinstance(app.screen, HelpModal), what="load-more guard help modal"
            )
            await pilot.press(pressed)
            assert len(calls) == 3
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_app_slow_keyboard_load_more_keeps_inspector_responsive(size):
    app = DemoModeApp(theme="carbon")
    pages = LoadedPages()
    try:
        async with app.run_test(size=size) as pilot:
            page, table, _ = await show_loaded_results(pilot, pages=pages)
            await select_cell(table, page.vm.results, 1, 1)
            assert page.vm.results.selection == (1, 1)
            pages.allow_more = pages.block_more = True
            assert app.app_ctx.root_vm.content_host.current_id == "athena"
            assert table.has_focus
            loading = asyncio.create_task(pilot.press("l"))
            await wait_until(pages.started.is_set, what="keyboard delayed page request started")
            inspecting = asyncio.create_task(pilot.press("alt+enter"))
            try:
                await wait_until(
                    lambda: isinstance(app.screen, AthenaResultCellModal),
                    what="keyboard inspector responds while explicit page is blocked",
                )
            finally:
                pages.release.set()
                await loading
                await inspecting
            await drain_workers(app)
            assert pages.calls == [("acceptance", None), ("acceptance", "second")]
            assert len(page.vm.results.rows) == 6
            assert not page.vm.results.has_more
    finally:
        app.app_ctx.root_vm.dispose()


@pytest.mark.parametrize("first", ["key", "button"])
async def test_actual_app_repeated_load_more_declines_inflight_page_without_restarting(first):
    class GatedPages(LoadedPages):
        def __init__(self):
            super().__init__()
            self.cancelled = []

        async def get_results_page(self, execution_id, *, start_token=None):
            if start_token is None:
                return await super().get_results_page(execution_id, start_token=start_token)
            self.calls.append((execution_id, start_token))
            assert self.allow_more
            assert start_token == "second"
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.append(start_token)
                raise
            return ResultPage(COLUMNS, (("1", "match"),), None)

    app = DemoModeApp(theme="carbon")
    pages = GatedPages()
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            page, table, _ = await show_loaded_results(pilot, pages=pages)
            vm = page.vm.results
            original = vm.export_snapshot()
            pages.allow_more = True
            if first == "button":
                assert await pilot.click("#athena-more-results")
            else:
                await pilot.press("l")
            await wait_until(pages.started.is_set, what="authorized continuation is in flight")
            assert vm.is_loading_more
            assert vm._pager.current_token == original.next_token == "second"
            await focus_and_settle(table)
            try:
                await pilot.press("l", "l")
                await pilot.pause()
                assert vm.is_loading_more
                assert pages.calls == [("acceptance", None), ("acceptance", "second")]
                assert pages.cancelled == []
                assert vm.rows == ROWS
                assert vm._pager.current_token == "second"
            finally:
                pages.release.set()
                await drain_workers(app)
            assert vm.rows == (*ROWS, ("1", "match"))
            assert pages.calls == [("acceptance", None), ("acceptance", "second")]
            assert pages.cancelled == []
            assert not vm.is_loading_more
            assert not vm.has_more
            assert vm.export_snapshot().next_token is None
    finally:
        pages.release.set()
        app.app_ctx.root_vm.dispose()
