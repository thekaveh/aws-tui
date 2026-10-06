"""Mounted comparison routes, explicit choices and read-only provider boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from textual.widgets import Input, Static, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.infra.clipboard import InMemoryClipboard
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.modal_button import ModalButton
from tests.helpers import drain_workers, wait_until
from tests.integration.test_glue_page import open_service

NOW = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
NAME = "[bold]events[/bold]"


class RecordingSession:
    """Every SDK call is recorded; non-comparison reads allowed only before epoch."""

    def __init__(self):
        self.calls = []
        self.epoch = False
        self.table_name = NAME
        self.database_name = "analytics"
        self.forbidden = []
        self.fail = False
        self.gate = None
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.drained = asyncio.Event()

    async def client(self, source, service):
        assert service == "glue"
        return RecordingSDK(self, source)


class RecordingSDK:
    def __init__(self, session, source):
        self.session, self.source = session, source

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __getattr__(self, operation):
        self.session.forbidden.append(operation)
        raise AssertionError(f"Forbidden SDK operation: {operation}")

    def record(self, operation, kwargs):
        self.session.calls.append(
            (self.session.epoch, self.source.name, self.source.region, operation, kwargs)
        )

    async def get_databases(self, **kwargs):
        self.record("get_databases", kwargs)
        return {
            "DatabaseList": [{"Name": self.session.database_name}],
            "NextToken": "d2" if "NextToken" not in kwargs else None,
        }

    async def get_tables(self, **kwargs):
        self.record("get_tables", kwargs)
        return {
            "TableList": [{"Name": self.session.table_name}],
            "NextToken": "t2" if "NextToken" not in kwargs else None,
        }

    async def get_table(self, **kwargs):
        self.record("get_table", kwargs)
        if self.session.epoch and self.session.gate is not None:
            self.session.started.set()
            while not self.session.gate.is_set():
                try:
                    await self.session.gate.wait()
                except asyncio.CancelledError:
                    self.session.cancelled.set()
            self.session.drained.set()
        if self.session.epoch and self.session.fail:
            raise RuntimeError("secret=DO-NOT-DISPLAY")
        return {
            "Table": {
                "Name": self.session.table_name,
                "TableType": "EXTERNAL_TABLE",
                "StorageDescriptor": {
                    "Columns": [
                        {
                            "Name": "[red]id[/red]",
                            "Type": "struct<a: array<decimal(18, 2)>>",
                            "Comment": "literal [blue] comment",
                        }
                    ],
                    "Location": "s3://example/" + self.source.region,
                },
                "Parameters": {"secret": "DO-NOT-DISPLAY"},
            }
        }

    async def get_partitions(self, **kwargs):
        self.record("get_partitions", kwargs)
        assert not self.session.epoch
        return {"Partitions": []}

    async def get_column_statistics_for_table(self, **kwargs):
        self.record("get_column_statistics_for_table", kwargs)
        assert not self.session.epoch
        return {"ColumnStatisticsList": []}


def make_app(tmp_path, monkeypatch, *, clipboard=None):
    clipboard = clipboard or InMemoryClipboard()
    ctx = build_app_context(
        config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True, clipboard=clipboard
    )
    session = RecordingSession()
    service = ctx.registry.get("glue")
    monkeypatch.setattr(service, "_client_factory", None)
    monkeypatch.setattr(service, "_aws_session", session)
    athena_calls = []

    def deny_athena(source):
        if session.epoch:
            athena_calls.append(source)
            raise AssertionError("Comparison created Athena")
        return object()

    monkeypatch.setattr(service, "_athena_client_factory", deny_athena)
    build = service.build_comparison_vm
    samples = []

    def clock():
        value = NOW + timedelta(seconds=len(samples))
        samples.append(value)
        return value

    monkeypatch.setattr(service, "build_comparison_vm", lambda **kw: build(**kw, clock=clock))
    return AwsTuiApp(ctx), ctx, session, clipboard, athena_calls, samples


async def open_comparison(app, ctx, session, pilot, *, pin_ready=False):
    from aws_tui.ui.widgets.glue.comparison_modal import GlueComparisonModal

    await open_service(ctx, pilot, "glue")
    await drain_workers(app)
    await pilot.pause()
    page = app.query_one(GluePage)
    if pin_ready:
        await page.vm.select_database(session.database_name)
        await page.vm.select_table(session.table_name)
        await drain_workers(app)
        await pilot.pause()
    app.focus_active_service_pane()
    await pilot.pause()
    before = app.focused
    session.epoch = True
    await pilot.press("ctrl+g")
    await wait_until(lambda: isinstance(app.screen, GlueComparisonModal), what="comparison modal")
    await drain_workers(app)
    return app.screen, page, before


def button(screen, name):
    return next(b for b in screen.query(ModalButton) if b.button_id == name)


async def press_button(screen, pilot, name):
    button(screen, name).focus()
    await pilot.press("enter")
    await drain_workers(pilot.app)


async def pick(screen, pilot, side, kind, index=0):
    picker = screen.query_one(f"#{side}-{kind}", ContextPicker)
    picker.focus()
    await pilot.press("enter")
    await pilot.press("home")
    for _ in range(index):
        await pilot.press("down")
    await pilot.press("enter")
    await drain_workers(pilot.app)
    assert picker.value is not None, (side, kind, screen.vm.side(side), pilot.app.focused)


async def select_table(screen, pilot, side):
    await pick(screen, pilot, side, "database")
    await pick(screen, pilot, side, "table")


@pytest.mark.asyncio
async def test_global_open_explicit_sides_keyboard_regions_and_literal_headers(
    tmp_path, monkeypatch
):
    app, ctx, session, clipboard, athena, samples = make_app(tmp_path, monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, page, before = await open_comparison(app, ctx, session, pilot)
        assert screen.vm.side("left").selected_table is None
        assert screen.vm.side("right").selected_table is None
        assert samples == []
        assert button(screen, "copy").disabled
        assert clipboard.writes == []
        await pilot.press("ctrl+2", "enter", "escape")
        assert app.screen is screen
        assert not screen.query_one("#right-source", ContextPicker).is_open
        await pick(screen, pilot, "right", "source", 1)
        region = screen.query_one("#right-region", Input)
        region.focus()
        await pilot.press("home", "shift+end", "backspace")
        await pilot.press(*"eu-west-1")
        await press_button(screen, pilot, "right-apply")
        await select_table(screen, pilot, "left")
        await select_table(screen, pilot, "right")
        left, right = screen.vm.side("left"), screen.vm.side("right")
        assert left.selected_table.connection_name != right.selected_table.connection_name
        assert right.selected_table.region == "eu-west-1"
        for side, state in [("left", left), ("right", right)]:
            header = screen.query_one(f"#{side}-header", TextArea).text
            for value in [
                state.selected_table.connection_name,
                state.selected_table.region,
                "AwsDataCatalog",
                "analytics",
                NAME,
            ]:
                assert value in header
            assert state.snapshot.fetched_at.isoformat() in str(
                screen.query_one(f"#{side}-status", Static).render()
            )
        assert samples == [NOW, NOW + timedelta(seconds=1)]
        assert "[red]id[/red]" in screen.query_one("#comparison-body", TextArea).text
        assert "DO-NOT-DISPLAY" not in screen.query_one("#comparison-body", TextArea).text
        initial_calls = list(session.calls)
        await pilot.press("ctrl+d")
        assert (
            "parameters · secret · unavailable"
            in screen.query_one("#comparison-body", TextArea).text
        )
        assert "columns ·" not in screen.query_one("#comparison-body", TextArea).text
        await pilot.press("ctrl+c")
        await wait_until(lambda: len(clipboard.writes) == 1, what="Glue comparison copied")
        filtered_copy = clipboard.writes[0]
        await pilot.press("ctrl+d", "ctrl+c")
        await wait_until(lambda: len(clipboard.writes) == 2, what="full comparison copied again")
        assert clipboard.writes[1] == filtered_copy
        assert "Left" in filtered_copy
        assert "Right" in filtered_copy
        assert "UNCHANGED" in filtered_copy or "unchanged" in filtered_copy
        assert session.calls == initial_calls
        await pilot.press("ctrl+2", "ctrl+r")
        await drain_workers(app)
        assert screen.vm.side("left").snapshot is left.snapshot
        assert screen.vm.side("right").snapshot.fetched_at == NOW + timedelta(seconds=2)
        assert page.vm.source.connection_name == left.selected_table.connection_name
        await pilot.press("escape")
        await wait_until(lambda: app.screen is not screen, what="comparison closed")
        assert app.focused is before
        calls = [call for call in session.calls if call[0]]
        assert {call[3] for call in calls} == {"get_databases", "get_tables", "get_table"}
        assert [call[3] for call in calls] == [
            "get_databases",
            "get_databases",
            "get_databases",
            "get_databases",
            "get_tables",
            "get_table",
            "get_tables",
            "get_table",
            "get_table",
        ]
        assert calls[-1][4] == {"DatabaseName": "analytics", "Name": NAME}
        assert any(call[2] == "eu-west-1" for call in calls)
        assert session.forbidden == []
        assert athena == []


@pytest.mark.asyncio
async def test_left_completion_preserves_right_picker_highlight_and_region_draft(
    tmp_path, monkeypatch
):
    from textual.widgets import OptionList

    app, ctx, session, _, _, _ = make_app(tmp_path, monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, _, _ = await open_comparison(app, ctx, session, pilot)
        await select_table(screen, pilot, "left")
        session.gate = asyncio.Event()
        await pilot.press("ctrl+1", "ctrl+r")
        await session.started.wait()
        draft = screen.query_one("#right-region", Input)
        draft.value = "uncommitted-region"
        await pilot.press("ctrl+2", "enter", "down")
        picker = screen.query_one("#right-source", ContextPicker)
        options = picker.query_one(OptionList)
        highlighted = options.highlighted
        focused = app.focused
        try:
            session.gate.set()
            await session.drained.wait()
            await drain_workers(app)
            await pilot.pause()
            assert picker.is_open
            assert options.highlighted == highlighted
            assert app.focused is focused
            assert draft.value == "uncommitted-region"
            assert screen.vm.side("right").selected_table is None
        finally:
            session.gate.set()


@pytest.mark.asyncio
async def test_pin_each_side_fetches_fresh_and_source_change_clears_only_its_snapshot(
    tmp_path, monkeypatch
):
    app, ctx, session, _, athena, samples = make_app(tmp_path, monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, page, _ = await open_comparison(app, ctx, session, pilot, pin_ready=True)
        opened = page.vm.catalog.table_detail
        assert opened is not None
        assert screen.vm.side("left").selected_table is None
        assert screen.vm.side("right").selected_table is None
        await press_button(screen, pilot, "left-pin")
        left = screen.vm.side("left").snapshot
        assert left.ref == opened.summary.ref
        assert left.detail is not opened
        assert screen.vm.side("right").snapshot is None
        await press_button(screen, pilot, "right-pin")
        right = screen.vm.side("right").snapshot
        assert right.ref == opened.summary.ref
        assert right.detail is not opened
        assert right.detail is not left.detail
        assert left.fetched_at == NOW
        assert right.fetched_at == NOW + timedelta(seconds=1)
        await pick(screen, pilot, "left", "source", 1)
        assert screen.vm.side("left").snapshot is None
        assert screen.vm.side("left").selected_table is None
        assert screen.vm.side("right").snapshot is right
        assert "<not fetched>" in str(screen.query_one("#left-status", Static).render())
        assert NOW.isoformat() not in str(screen.query_one("#left-status", Static).render())
        assert NAME not in screen.query_one("#left-header", TextArea).text
        assert button(screen, "copy").disabled
        assert page.vm.catalog.table_detail is opened
        assert samples == [NOW, NOW + timedelta(seconds=1)]
        assert [c[3] for c in session.calls if c[0]].count("get_table") == 2
        assert athena == []


@pytest.mark.asyncio
async def test_more_buttons_partial_error_and_independent_refresh(tmp_path, monkeypatch):
    app, ctx, session, _, athena, samples = make_app(tmp_path, monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, _, _ = await open_comparison(app, ctx, session, pilot)
        await press_button(screen, pilot, "left-databases")
        assert not screen.vm.side("left").has_more_databases
        await pick(screen, pilot, "left", "database")
        await press_button(screen, pilot, "left-tables")
        assert not screen.vm.side("left").has_more_tables
        await pick(screen, pilot, "left", "table")
        left = screen.vm.side("left").snapshot
        session.fail = True
        await select_table(screen, pilot, "right")
        assert screen.vm.side("right").snapshot is None
        assert screen.vm.side("right").error_text is not None
        assert screen.vm.side("left").snapshot is left
        assert "Left fetched" in screen.query_one("#comparison-body", TextArea).text
        assert "[red]id[/red]" in screen.query_one("#comparison-body", TextArea).text
        assert "DO-NOT-DISPLAY" not in screen.query_one("#comparison-body", TextArea).text
        assert button(screen, "copy").disabled
        session.fail = False
        await press_button(screen, pilot, "right-refresh")
        right = screen.vm.side("right").snapshot
        assert right is not None
        session.fail = True
        await press_button(screen, pilot, "right-refresh")
        assert screen.vm.side("right").snapshot is right
        assert screen.vm.side("left").snapshot is left
        assert "Refresh failed" in str(screen.query_one("#right-status", Static).render())
        assert not button(screen, "copy").disabled
        assert len(samples) == 2
        calls = [c for c in session.calls if c[0]]
        assert any(c[4].get("NextToken") == "d2" for c in calls)
        assert any(c[4].get("NextToken") == "t2" for c in calls)
        assert {c[3] for c in calls} == {"get_databases", "get_tables", "get_table"}
        assert athena == []


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["left", "right"])
async def test_partial_metadata_positions_match_completed_comparison(side, tmp_path, monkeypatch):
    get_table = RecordingSDK.get_table

    async def get_table_with_positions(self, **kwargs):
        response = await get_table(self, **kwargs)
        response["Table"]["StorageDescriptor"]["Columns"].append(
            {"Name": "payload", "Type": "string"}
        )
        response["Table"]["PartitionKeys"] = [
            {"Name": "year", "Type": "int"},
            {"Name": "month", "Type": "int"},
        ]
        return response

    monkeypatch.setattr(RecordingSDK, "get_table", get_table_with_positions)
    app, ctx, session, _, _, _ = make_app(tmp_path, monkeypatch)
    expected = [
        ("columns", 'position=1; name="[red]id[/red]"'),
        ("columns", 'position=2; name="payload"'),
        ("partition keys", 'position=1; name="year"'),
        ("partition keys", 'position=2; name="month"'),
    ]
    async with app.run_test(size=(120, 40)) as pilot:
        screen, _, _ = await open_comparison(app, ctx, session, pilot)
        await select_table(screen, pilot, side)
        assert screen.vm.comparison is None
        partial_body = screen.query_one("#comparison-body", TextArea).text
        for section, value in expected:
            assert f"{side.title()} {section}: {value}" in partial_body
        assert "position=0;" not in partial_body

        counterpart = "right" if side == "left" else "left"
        await select_table(screen, pilot, counterpart)
        assert screen.vm.comparison is not None
        completed_body = screen.query_one("#comparison-body", TextArea).text
        summary = screen.vm.summary_text()
        assert summary is not None
        for _, value in expected:
            assert f"{side.title()}: {value}" in completed_body
            assert value in summary
        assert "position=0;" not in completed_body


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (60, 24)])
async def test_real_viewport_headers_controls_summary_and_focus(size, tmp_path, monkeypatch):
    clipboard = InMemoryClipboard(ok=False, mechanism="none")
    app, ctx, session, _, _, _ = make_app(tmp_path, monkeypatch, clipboard=clipboard)
    async with app.run_test(size=size) as pilot:
        screen, _, before = await open_comparison(app, ctx, session, pilot, pin_ready=True)
        await press_button(screen, pilot, "left-pin")
        await press_button(screen, pilot, "right-pin")
        await pilot.pause()
        for side in ["left", "right"]:
            status = screen.query_one(f"#{side}-status", Static)
            header = screen.query_one(f"#{side}-header", TextArea)
            assert screen.region.contains_region(status.region)
            assert screen.region.contains_region(header.region)
            assert status.region.height == 2
            assert header.content_region.height >= 3
            assert header.region.bottom <= screen.query_one("#comparison-controls").region.y
            assert NAME in header.text
            assert header.virtual_size.height <= header.content_region.height
        controls = screen.query_one("#comparison-controls")
        reached = set()
        await pilot.press("ctrl+1")
        for _ in range(35):
            focused = app.focused
            if isinstance(focused, ModalButton) and not focused.disabled:
                focused.scroll_visible(animate=False)
                await pilot.pause()
                assert controls.content_region.contains_region(focused.region), (
                    size,
                    focused.button_id,
                    controls.content_region,
                    focused.region,
                )
                reached.add(focused.button_id)
            await pilot.press("tab")
        assert {
            "left-apply",
            "right-apply",
            "left-pin",
            "right-pin",
            "left-refresh",
            "right-refresh",
            "copy",
            "differences",
            "summary",
            "close",
        } <= reached
        await pilot.press("ctrl+1", "tab", "shift+tab")
        assert app.focused is screen.query_one("#left-source", ContextPicker)
        await press_button(screen, pilot, "summary")
        summary = screen.query_one("#comparison-body", TextArea)
        assert summary.read_only
        assert summary.text == screen.vm.summary_text()
        await pilot.press("ctrl+c")
        await wait_until(lambda: len(clipboard.writes) == 1, what="no-backend copy attempt")
        assert clipboard.writes[0] == summary.text
        calls = list(session.calls)
        await pilot.press("ctrl+d")
        assert summary.text == screen.vm.summary_text()
        assert session.calls == calls
        await pilot.press("escape")
        await wait_until(lambda: app.screen is not screen, what="comparison close")
        assert app.focused is before


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", [False, True], ids=["escape-close", "app-shutdown"])
async def test_cancellation_resistant_read_drains_without_notifications(
    shutdown, tmp_path, monkeypatch
):
    app, ctx, session, _, _, samples = make_app(tmp_path, monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        screen, _, _ = await open_comparison(app, ctx, session, pilot)
        await pick(screen, pilot, "left", "database")
        session.gate = asyncio.Event()
        picker = screen.query_one("#left-table", ContextPicker)
        picker.focus()
        await pilot.press("enter", "home", "enter")
        await session.started.wait()
        changes = []
        events = []
        subscription = screen.vm.on_property_changed.subscribe(changes.append)
        hub_subscription = ctx.hub.messages.subscribe(
            lambda message: (
                events.append(message)
                if getattr(message, "sender_object", None) is screen.vm
                else None
            )
        )
        closer = asyncio.create_task(app._aws_tui_shutdown() if shutdown else pilot.press("escape"))
        try:
            await wait_until(
                lambda: not screen.vm.actions_available, what="comparison intake closed"
            )
            await session.cancelled.wait()
            assert not session.drained.is_set()
            assert samples == []
            assert changes == []
            assert events == []
            session.gate.set()
            await asyncio.wait_for(closer, 10)
            await session.drained.wait()
            await screen.shutdown()
            assert changes == []
            assert events == []
            assert samples == []
            assert screen.vm.side("left").snapshot is None
        finally:
            session.gate.set()
            await closer
            subscription.dispose()
            hub_subscription.dispose()


@pytest.mark.asyncio
async def test_long_literal_references_scroll_without_hiding_timestamps(tmp_path, monkeypatch):
    app, ctx, session, _, _, _ = make_app(tmp_path, monkeypatch)
    session.table_name = "[bold]table_" + "long_literal_" * 15 + "[/bold]"
    session.database_name = "[green]database_" + "long_database_" * 12 + "[/green]"
    async with app.run_test(size=(60, 24)) as pilot:
        screen, _, _ = await open_comparison(app, ctx, session, pilot, pin_ready=True)
        await press_button(screen, pilot, "left-pin")
        await press_button(screen, pilot, "right-pin")
        for side in ["left", "right"]:
            header = screen.query_one(f"#{side}-header", TextArea)
            status = screen.query_one(f"#{side}-status", Static)
            status_region = status.region
            assert session.table_name in header.text
            assert session.database_name in header.text
            assert header.virtual_size.height > header.content_region.height
            header.focus()
            await pilot.pause()
            assert header.content_region.height == 3
            await pilot.press("pagedown", "pagedown", "pagedown", "pagedown")
            await pilot.pause()
            assert header.scroll_y > 0
            assert status.region == status_region
            assert screen.region.contains_region(status.region)
            assert status.region.bottom <= header.region.y
        await press_button(screen, pilot, "summary")
        text = screen.query_one("#comparison-body", TextArea).text
        assert session.table_name in text
        assert session.database_name in text
        assert "DO-NOT-DISPLAY" not in text


@pytest.mark.asyncio
async def test_space_commits_and_arrow_keys_edit_region_without_page_navigation(
    tmp_path, monkeypatch
):
    app, ctx, session, _, _, _ = make_app(tmp_path, monkeypatch)
    async with app.run_test(size=(60, 24)) as pilot:
        screen, page, _ = await open_comparison(app, ctx, session, pilot)
        source = page.vm.source
        picker = screen.query_one("#left-database", ContextPicker)
        picker.focus()
        await pilot.press("space", "home", "enter")
        await drain_workers(app)
        assert screen.vm.side("left").selected_database.database_name == "analytics"
        field = screen.query_one("#left-region", Input)
        field.focus()
        await pilot.press("home", "right")
        assert app.focused is field
        assert field.cursor_position == 1
        await pilot.press("left")
        assert field.cursor_position == 0
        await pilot.press("home", "shift+end", "backspace")
        await pilot.press(*"us-west-2")
        apply = button(screen, "left-apply")
        apply.focus()
        await pilot.press("space")
        await drain_workers(app)
        assert screen.vm.side("left").region == "us-west-2"
        assert screen.vm.side("left").selected_database is None
        assert page.vm.source is source
