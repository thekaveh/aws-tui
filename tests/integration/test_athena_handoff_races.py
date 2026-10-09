"""User input during remote table discovery must retain ownership."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from textual.widgets import OptionList, Static, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.toast import Toast
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.helpers import wait_until
from tests.integration.test_glue_athena_direct_selection import _seed_nondefault_glue_table
from tests.integration.test_glue_athena_navigation import (
    _athena_client,
    _open_service,
    _wait_for_service_setup,
)


@asynccontextmanager
async def _gated_app(app, release):
    async with app.run_test(size=(120, 40)) as pilot:
        try:
            yield pilot
        finally:
            # Release synthetic requests before run_test drains app shutdown,
            # including when an interaction precondition fails.
            release.set()


async def _click_bottom_athena(app, pilot) -> None:
    await wait_until(
        lambda: (
            not any("Demo mode active" in toast.toast_vm.model.text for toast in app.query(Toast))
        ),
        what="demo warning expiry before real control interaction",
        timeout=10,
    )
    await pilot.pause()

    def label() -> Static | None:
        for chip in app.query_one(HintLegend).query(".hint-chip"):
            if chip.action.action_id != "glue.query_in_athena" or not chip.action.enabled:
                continue
            candidate = chip.query_one(".hint-label", Static)
            if (
                candidate.region.width
                and app.get_widget_at(*candidate.region.offset)[0] is candidate
            ):
                return candidate
        return None

    await wait_until(lambda: label() is not None, what="clickable bottom Athena command")
    target = label()
    assert target is not None
    assert await pilot.click(target)


async def _choose(pilot, picker: ContextPicker, value: str) -> None:
    assert not picker.disabled
    assert await pilot.click(picker)
    await wait_until(
        lambda: picker.is_open and picker.query_one(OptionList).has_focus,
        what="real context picker options focused",
    )
    options = picker.query_one(OptionList)
    index = options.get_option_index(value)
    await pilot.press("home", *("down" for _ in range(index)), "enter")
    await wait_until(
        lambda: picker.value == value and not picker.is_open,
        what="new context choice committed",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("discovery_fails", [False, True])
async def test_pending_glue_handoff_preserves_user_editor_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, discovery_fails: bool
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    client = _athena_client(ctx, "demo-dev")
    entered, release = asyncio.Event(), asyncio.Event()
    original = client.list_workgroups_page

    async def gated_workgroups(*, start_token=None):
        entered.set()
        await release.wait()
        if discovery_fails:
            raise PermissionDeniedError("controlled discovery failure")
        return await original(start_token=start_token)

    monkeypatch.setattr(client, "list_workgroups_page", gated_workgroups)
    app = AwsTuiApp(ctx)
    try:
        async with _gated_app(app, release) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            await _click_bottom_athena(app, pilot)
            await wait_until(entered.is_set, what="gated Athena setup request")
            await wait_until(
                lambda: (
                    isinstance(ctx.root_vm.content_host.current, AthenaPageVM)
                    and bool(app.query("#athena-editor"))
                ),
                what="eager Athena editor mounted",
            )
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, AthenaPageVM)
            editor = app.query_one("#athena-editor", TextArea)
            await pilot.pause()
            starter = editor.text
            assert starter.endswith("LIMIT 5")
            assert vm.query.is_context_resolving
            assert not vm.query.execute_command.can_execute()
            assert await pilot.click(editor)
            await pilot.press("end", "enter", *"reviewed")
            await pilot.pause()
            expected = editor.text
            assert expected != starter
            assert "reviewed" in expected
            await wait_until(
                lambda: editor.text == expected and vm.query.sql == expected,
                what="actual editor input acknowledged before discovery completes",
            )
            release.set()
            await _wait_for_service_setup(ctx, app, pilot)
            assert ctx.root_vm.content_host.current is vm
            assert not vm.query.is_context_resolving
            assert not any(call.method == "start_query" for call in client.calls)
            assert vm.query.sql == expected
            assert editor.text == expected
            if discovery_fails:
                assert vm.workgroups_state is PaneState.FORBIDDEN
                assert not vm.query.execute_command.can_execute()
    finally:
        release.set()
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_pending_glue_handoff_preserves_newer_context_picker_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    client = _athena_client(ctx, "demo-dev")
    client.add_database("dev-analytics", "AwsDataCatalog", "review_selected_database")
    target = _seed_nondefault_glue_table(ctx)
    entered, release = asyncio.Event(), asyncio.Event()
    original = client.list_databases_page

    async def gated_databases(catalog, *, workgroup=None, start_token=None):
        if catalog == "AwsDataCatalog" and workgroup == "dev-analytics":
            rows, _ = await original(catalog, workgroup=workgroup)
            if start_token is None:
                return rows[:2], "handoff-next"
            assert start_token == "handoff-next"
            entered.set()
            await release.wait()
            return rows[2:], None
        return await original(catalog, workgroup=workgroup, start_token=start_token)

    monkeypatch.setattr(client, "list_databases_page", gated_databases)
    app = AwsTuiApp(ctx)
    try:
        async with _gated_app(app, release) as pilot:
            glue = await _open_service(ctx, app, pilot, "glue")
            assert isinstance(glue, GluePageVM)
            await glue.open_table(target)
            await _click_bottom_athena(app, pilot)
            await wait_until(entered.is_set, what="handoff database discovery next page blocked")
            await wait_until(
                lambda: bool(app.query("#athena-workgroup")), what="Athena selectors mounted"
            )
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, AthenaPageVM)
            await pilot.pause()
            assert vm.query.is_context_resolving
            assert vm.context.catalog == "AwsDataCatalog"
            assert vm.context.database == "dev_analytics"
            await _choose(
                pilot,
                app.query_one("#athena-database", ContextPicker),
                "review_selected_database",
            )
            await wait_until(
                lambda: (
                    vm.context.database == "review_selected_database"
                    and vm.query.context == vm.context
                ),
                what="explicit newer database committed before old discovery returns",
            )
            release.set()
            await _wait_for_service_setup(ctx, app, pilot)
            assert ctx.root_vm.content_host.current is vm
            assert not any(call.method == "start_query" for call in client.calls)
            assert vm.context.workgroup == "dev-analytics"
            assert vm.context.database == "review_selected_database"
            assert app.query_one("#athena-database", ContextPicker).value == vm.context.database
    finally:
        release.set()
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
