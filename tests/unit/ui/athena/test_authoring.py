"""Mounted SQL authoring commands use native edits and never submit SQL."""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from contextlib import asynccontextmanager

import pytest
from textual.widgets import Button, Input, OptionList, Static, TextArea

from aws_tui.domain.sql_authoring import format_sql
from aws_tui.infra.sql_snippet_store import SqlSnippetStore
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from tests.helpers import drain_workers, focus_and_settle, wait_until
from tests.unit.ui.athena.test_page import _AthenaApp
from tests.unit.vm.athena.test_completion_vm import client
from tests.unit.vm.athena.test_page_vm import _accept_sql_replacement, make_page_vm


@asynccontextmanager
async def mounted(tmp_path):
    assert importlib.util.find_spec("aws_tui.ui.widgets.athena.authoring_dialogs") is not None, (
        "SQL authoring controls are missing"
    )
    fake = client()
    vm = make_page_vm(fake, sql_snippets=SqlSnippetStore(path=tmp_path / "snippets.json"))
    await vm.setup()
    app = _AthenaApp(vm)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            yield app, vm, fake, pilot
    finally:
        await vm.shutdown()
        vm.dispose()


async def typed(app, vm, pilot, sql):
    editor = app.query_one("#athena-editor", TextArea)
    await focus_and_settle(editor)
    editor.insert(sql)
    await wait_until(lambda: vm.query.sql == sql, what="typed SQL acknowledged")
    await pilot.pause()
    return editor


async def test_sql_menu_button_keeps_its_complete_label_at_minimum_size(tmp_path):
    async with mounted(tmp_path) as (app, _vm, _fake, _pilot):
        button = app.query_one("#athena-authoring", Button)
        assert str(button.label) == "SQL"
        assert button.content_region.width >= 3


async def choose(app, pilot, identity):
    listing = app.screen.query_one("#athena-sql-choices", OptionList)
    listing.highlighted = listing.get_option_index(identity)
    await pilot.press("enter")
    await pilot.pause()


async def command(app, pilot, identity):
    button = app.query_one("#athena-authoring", Button)
    assert button.visible
    assert not button.disabled
    await pilot.click(button)
    await wait_until(lambda: bool(app.screen.query("#athena-sql-choices")), what="SQL menu opened")
    await choose(app, pilot, identity)


async def enter_value(app, pilot, value):
    await wait_until(
        lambda: bool(app.screen.query("#athena-sql-value")), what="local SQL value prompt"
    )
    field = app.screen.query_one("#athena-sql-value", Input)
    field.value = str(value)
    await pilot.press("enter")
    await pilot.pause()


def no_starts(fake):
    assert not any(
        call.method.startswith(("start_query", "create_named", "delete_named", "update_named"))
        for call in fake.calls
    )


async def test_format_menu_preserves_native_undo_and_incomplete_text(tmp_path):
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        sql = "SELECT amount FROM events WHERE amount > 1"
        editor = await typed(app, vm, pilot, sql)
        expected = format_sql(sql).sql
        assert expected != sql
        await command(app, pilot, "format")
        await drain_workers(app)
        assert editor.text == vm.query.sql == expected
        await focus_and_settle(editor)
        await pilot.press("ctrl+z")
        await wait_until(
            lambda: editor.text == vm.query.sql == sql, what="format undo restores input"
        )
        editor.redo()
        await wait_until(
            lambda: editor.text == vm.query.sql == expected, what="format redo restores layout"
        )
        editor.undo()
        await wait_until(
            lambda: editor.text == vm.query.sql == sql, what="second undo restores input"
        )
        editor.select_all()
        editor.replace("SELECT FROM", editor.selection.start, editor.selection.end)
        await wait_until(lambda: vm.query.sql == "SELECT FROM", what="incomplete SQL acknowledged")
        await command(app, pilot, "format")
        await drain_workers(app)
        assert editor.text == "SELECT FROM"
        assert vm.authoring.status
        no_starts(fake)


async def test_file_open_uses_real_confirmation_and_save_keeps_undo(tmp_path, monkeypatch):
    calls = []
    original = ConfirmationVM.ask

    async def ask(self, request, **kwargs):
        calls.append(request)
        return await original(self, request, **kwargs)

    monkeypatch.setattr(ConfirmationVM, "ask", ask)
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        editor = await typed(app, vm, pilot, "SELECT 1")
        path = tmp_path / "source.sql"
        path.write_text("SELECT 2")
        await command(app, pilot, "open")
        await enter_value(app, pilot, path)
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="modified replacement confirmation"
        )
        await pilot.press("escape")
        await drain_workers(app)
        assert editor.text == "SELECT 1"
        assert vm.authoring.path is None
        assert calls
        await command(app, pilot, "open")
        await enter_value(app, pilot, path)
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="accepted replacement confirmation"
        )
        await pilot.press("enter")
        await drain_workers(app)
        assert editor.text == "SELECT 2"
        assert vm.authoring.path == path
        assert not vm.authoring.dirty
        await focus_and_settle(editor)
        editor.move_cursor((0, len(editor.text)))
        editor.insert(" -- edited")
        await wait_until(lambda: vm.authoring.dirty, what="file edit is dirty")
        destination = tmp_path / "saved.sql"
        await command(app, pilot, "save_as")
        await enter_value(app, pilot, destination)
        await drain_workers(app)
        assert destination.read_text() == "SELECT 2 -- edited"
        assert not vm.authoring.dirty
        assert "saved.sql" in str(app.query_one("#athena-query-status", Static).render())
        await focus_and_settle(editor)
        await pilot.press("ctrl+z")
        await wait_until(lambda: vm.query.sql == "SELECT 2", what="Save As preserves undo history")
        assert vm.authoring.dirty
        no_starts(fake)


async def test_completion_inserts_only_chosen_column_and_undo_restores_input(tmp_path):
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        sql = "SELECT  FROM events"
        editor = await typed(app, vm, pilot, sql)
        editor.move_cursor((0, 7))
        await command(app, pilot, "complete")
        await wait_until(
            lambda: bool(app.screen.query("#athena-sql-choices")), what="schema choices displayed"
        )
        listing = app.screen.query_one("#athena-sql-choices", OptionList)
        choice = next(
            listing.get_option_at_index(i).id
            for i in range(listing.option_count)
            if "amount" in str(listing.get_option_at_index(i).prompt)
        )
        await choose(app, pilot, choice)
        await drain_workers(app)
        assert editor.text == vm.query.sql == 'SELECT "amount" FROM events'
        await focus_and_settle(editor)
        await pilot.press("ctrl+z")
        await wait_until(lambda: vm.query.sql == sql, what="completion undo restores input")
        no_starts(fake)


async def test_local_snippets_show_provenance_and_insert_without_aws_mutation(tmp_path):
    from aws_tui.domain.query import NamedQuery, PreparedStatement

    async with mounted(tmp_path) as (app, vm, fake, pilot):
        named = NamedQuery(
            "aws-one",
            "AWS named example",
            None,
            vm.context.database,
            "SELECT 7",
            vm.context.workgroup,
        )
        prepared = PreparedStatement("aws-prepared", "SELECT 8", vm.context.workgroup, None, None)
        fake.add_named_query(named)
        fake.add_prepared_statement(prepared)
        editor = await typed(app, vm, pilot, "SELECT 1")
        await command(app, pilot, "save_snippet")
        await enter_value(app, pilot, "Local example")
        await drain_workers(app)
        assert len(vm.authoring.snippets) == 1
        assert "fixture" in vm.authoring.snippets[0].provenance
        await vm.select_view("saved")
        await pilot.pause()
        await drain_workers(app)
        named_list = app.query_one("#athena-named-pane-options", OptionList)
        prepared_list = app.query_one("#athena-prepared-pane-options", OptionList)
        assert [named_list.get_option_at_index(i).id for i in range(named_list.option_count)] == [
            "aws-one"
        ]
        assert [
            prepared_list.get_option_at_index(i).id for i in range(prepared_list.option_count)
        ] == ["aws-prepared"]
        assert all(
            "Local example" not in str(listing.get_option_at_index(i).prompt)
            for listing in (named_list, prepared_list)
            for i in range(listing.option_count)
        )
        await vm.select_view("query")
        await pilot.pause()
        editor = app.query_one("#athena-editor", TextArea)
        editor.move_cursor((0, len(editor.text)))
        await command(app, pilot, "insert_snippet")
        await wait_until(
            lambda: bool(app.screen.query("#athena-sql-choices")), what="local snippets displayed"
        )
        assert "Local SQL snippets" in str(
            app.screen.query_one("#athena-sql-title", Static).render()
        )
        local_list = app.screen.query_one("#athena-sql-choices", OptionList)
        assert local_list.option_count == 1
        assert "Local example" in str(local_list.get_option_at_index(0).prompt)
        assert "AWS named example" not in str(local_list.get_option_at_index(0).prompt)
        await choose(app, pilot, vm.authoring.snippets[0].id)
        await drain_workers(app)
        assert editor.text == vm.query.sql == "SELECT 1SELECT 1"
        await focus_and_settle(editor)
        await pilot.press("ctrl+z")
        await wait_until(lambda: vm.query.sql == "SELECT 1", what="snippet undo restores input")
        assert fake.named_queries["aws-one"] == named
        assert fake.prepared_statements[(vm.context.workgroup, "aws-prepared")] == prepared
        assert not any(
            call.method.startswith(("create_", "update_", "delete_")) for call in fake.calls
        )
        no_starts(fake)


async def test_format_does_not_steal_newer_focus_intent(tmp_path, monkeypatch):
    from aws_tui.vm.athena import authoring_vm

    entered, release = threading.Event(), threading.Event()
    original = authoring_vm.format_sql

    def gated(sql):
        entered.set()
        assert release.wait(5)
        return original(sql)

    monkeypatch.setattr(authoring_vm, "format_sql", gated)
    try:
        async with mounted(tmp_path) as (app, vm, _fake, pilot):
            await typed(app, vm, pilot, "SELECT amount FROM events")
            await command(app, pilot, "format")
            await wait_until(entered.is_set, what="format computation started")
            status = app.query_one("#athena-query-status", Static)
            await focus_and_settle(status)
            release.set()
            await drain_workers(app)
            await pilot.pause()
            assert app.focused is status
    finally:
        release.set()


@pytest.mark.parametrize("prompt", ["menu", "open"])
async def test_retired_view_dismisses_its_owned_prompt(tmp_path, prompt):
    async with mounted(tmp_path) as (app, _vm, _fake, pilot):
        view = app.query_one(AthenaQueryView)
        if prompt == "menu":
            await pilot.click("#athena-authoring")
            await wait_until(lambda: len(app.screen_stack) == 2, what="authoring menu shown")
        else:
            await command(app, pilot, "open")
            await wait_until(
                lambda: bool(app.screen.query("#athena-sql-value")), what="local path prompt shown"
            )
        await view.remove()
        await wait_until(
            lambda: len(app.screen_stack) == 1, what="retired SQL prompt dismissed", timeout=1
        )


async def test_invalid_home_path_is_recoverable(tmp_path):
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        editor = await typed(app, vm, pilot, "SELECT 1")
        await command(app, pilot, "open")
        await enter_value(app, pilot, "~aws_tui_nonexistent_user_255/file.sql")
        await drain_workers(app)
        assert editor.text == "SELECT 1"
        assert vm.authoring.path is None
        assert "path" in vm.authoring.status.lower()
        assert "aws_tui_nonexistent" not in vm.authoring.status
        assert len(app.screen_stack) == 1
        no_starts(fake)


async def test_sql_menu_is_in_page_keyboard_focus_ring(tmp_path):
    async with mounted(tmp_path) as (app, _vm, fake, pilot):
        page = app.query_one(AthenaPage)
        assert "athena-authoring" in {widget.id for _, widget in page._focus_targets()}
        assert len([c for c in fake.calls if c.method == "list_tables_page"]) <= 2
        await pilot.pause()
        await pilot.pause()
        assert len([c for c in fake.calls if c.method == "list_tables_page"]) <= 2


async def test_context_change_while_completion_picker_open_cannot_apply_old_choice(tmp_path):
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        fake.add_database("analytics", "catalog", "other")
        await vm.refresh_query_context()
        sql = "SELECT  FROM events"
        editor = await typed(app, vm, pilot, sql)
        editor.move_cursor((0, 7))
        await command(app, pilot, "complete")
        await wait_until(
            lambda: bool(app.screen.query("#athena-sql-choices")), what="completion picker open"
        )
        listing = app.screen.query_one("#athena-sql-choices", OptionList)
        choice = next(
            listing.get_option_at_index(i).id
            for i in range(listing.option_count)
            if "amount" in str(listing.get_option_at_index(i).prompt)
        )
        # Accept the empty-table context change while preserving the authored
        # SQL, so the stale completion decision is still exercised.
        await vm.select_database("other", confirm=_accept_sql_replacement)
        await choose(app, pilot, choice)
        await drain_workers(app)
        assert vm.query.context.database == "other"
        assert editor.text == vm.query.sql == sql
        no_starts(fake)


async def test_authoring_feedback_keeps_live_submission_status_visible(tmp_path):
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        await typed(app, vm, pilot, "SELECT amount FROM events WHERE amount > 1")
        await command(app, pilot, "format")
        await drain_workers(app)
        entered, release = asyncio.Event(), asyncio.Event()
        original = fake.start_query

        async def gated(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        fake.start_query = gated
        try:
            app.query_one(AthenaQueryView).dispatch_execute()
            await wait_until(entered.is_set, what="query submission admitted")
            await pilot.pause()
            assert vm.query.is_submitting
            assert "SUBMITTING" in str(app.query_one("#athena-query-status", Static).render())
            assert not app.query_one("#athena-cancel", Button).disabled
        finally:
            release.set()
            await drain_workers(app)


async def test_authoring_feedback_cannot_hide_read_only_rejection(tmp_path):
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        editor = await typed(app, vm, pilot, "SELECT 1")
        await command(app, pilot, "format")
        await drain_workers(app)
        editor.select_all()
        editor.replace("DELETE FROM events", editor.selection.start, editor.selection.end)
        await wait_until(
            lambda: vm.query.sql == "DELETE FROM events", what="invalid SQL acknowledged"
        )
        app.query_one(AthenaQueryView).dispatch_execute()
        await drain_workers(app)
        await pilot.pause()
        assert vm.query.validation_error
        assert "INVALID QUERY" in str(app.query_one("#athena-query-status", Static).render())
        no_starts(fake)


async def test_declining_table_picker_restores_the_committed_display_value(tmp_path):
    async with mounted(tmp_path) as (app, vm, fake, pilot):
        await typed(app, vm, pilot, "SELECT 'keep'")
        picker = app.query_one("#athena-table", ContextPicker)
        assert picker.value is None
        await focus_and_settle(picker)
        await pilot.press("enter")
        await wait_until(lambda: picker.is_open, what="table options opened")
        listing = app.query_one("#athena-table-options", OptionList)
        listing.highlighted = listing.get_option_index("events")
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="table replacement asks first"
        )
        await pilot.press("escape")
        await drain_workers(app)
        await pilot.pause()
        assert vm.query.sql == "SELECT 'keep'"
        assert vm.selected_table_ref is None
        assert picker.value is None
        no_starts(fake)
