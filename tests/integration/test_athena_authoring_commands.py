"""Real app dispatch exposes SQL authoring independently of editor key handling."""

from __future__ import annotations

import pytest
from textual import events
from textual._xterm_parser import XTermParser
from textual.widgets import TextArea

from aws_tui.domain.sql_authoring import format_sql
from aws_tui.infra.keymap_store import KeymapStore, textual_key_name
from tests.helpers import drain_workers, focus_and_settle, seed_athena_sql, wait_until
from tests.integration.test_athena_page import _mounted_athena_app


async def test_sql_menu_label_is_fully_rendered_in_the_actual_app(tmp_path):
    from textual.widgets import Button

    async with _mounted_athena_app(tmp_path) as (app, _ctx, _vm, _client, pilot):
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        button = app.query_one("#athena-authoring", Button)
        rendered = [button.render_line(row).text for row in range(button.size.height)]
        assert any("SQL" in line for line in rendered), rendered


async def test_seeded_demo_completion_inserts_only_selected_column_and_switches_identity(
    tmp_path, monkeypatch
):
    from textual.widgets import OptionList

    from aws_tui.app import AwsTuiApp
    from aws_tui.composition import build_app_context
    from aws_tui.ui.widgets.athena.authoring_dialogs import SqlChoiceModal
    from tests.integration.test_credential_recovery import _connected_probe
    from tests.integration.test_glue_athena_navigation import _open_service

    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            old = await _open_service(ctx, app, pilot, "athena")
            editor = app.query_one("#athena-editor", TextArea)
            original = "SELECT event_ FROM events"
            await seed_athena_sql(pilot, old.query, editor, original)
            editor.move_cursor((0, len("SELECT event_")))
            await focus_and_settle(editor)
            await pilot.press("ctrl+space")
            await wait_until(
                lambda: isinstance(app.screen, SqlChoiceModal), what="seeded demo column choices"
            )
            options = app.screen.query_one(OptionList)
            labels = [
                str(options.get_option_at_index(i).prompt) for i in range(options.option_count)
            ]
            assert any("event_id" in label for label in labels)
            assert not any("order_id" in label for label in labels)
            options.highlighted = next(i for i, label in enumerate(labels) if "event_id" in label)
            await pilot.press("enter")
            await drain_workers(app)
            assert editor.text == old.query.sql == 'SELECT "event_id" FROM events'
            editor.undo()
            await wait_until(lambda: old.query.sql == original, what="completion native undo")
            app._handle_swap_source()
            await drain_workers(app)
            new = ctx.root_vm.content_host.current
            assert new is not old
            assert new.connection.name == "demo-prod"
            editor = app.query_one("#athena-editor", TextArea)
            await seed_athena_sql(pilot, new.query, editor, "SELECT order_ FROM daily_sales")
            editor.move_cursor((0, len("SELECT order_")))
            await focus_and_settle(editor)
            await pilot.press("ctrl+space")
            await wait_until(
                lambda: isinstance(app.screen, SqlChoiceModal), what="new profile column choices"
            )
            options = app.screen.query_one(OptionList)
            labels = [
                str(options.get_option_at_index(i).prompt) for i in range(options.option_count)
            ]
            assert any("order_id" in label for label in labels)
            assert not any("event_id" in label for label in labels)
            await pilot.press("escape")
            await drain_workers(app)
            assert all(
                call.method != "start_query" for vm in (old, new) for call in vm.client.calls
            )
    finally:
        await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("remapped", [False, True])
async def test_format_binding_works_in_the_actual_app_editor(tmp_path, remapped):
    assert "athena.format_sql" in KeymapStore.DEFAULT_BINDINGS
    key = "ctrl+alt+h" if remapped else "ctrl+alt+f"
    keymap = KeymapStore(overlay={"athena.format_sql": key})
    async with _mounted_athena_app(tmp_path, keymap=keymap) as (app, _ctx, vm, client, pilot):
        editor = app.query_one("#athena-editor", TextArea)
        sql = "SELECT value FROM events WHERE value > 1"
        await seed_athena_sql(pilot, vm.query, editor, sql)
        await focus_and_settle(editor)
        await pilot.press(textual_key_name(key))
        await drain_workers(app)
        await wait_until(
            lambda: vm.query.sql == format_sql(sql).sql, what="actual authoring key dispatch"
        )
        assert editor.text == vm.query.sql
        assert not client.start_calls


@pytest.mark.parametrize(
    ("sequence", "overlay"),
    [("\x1b[102;7u", None), ("\x1b[104;7u", {"athena.format_sql": "ctrl+alt+h"})],
)
async def test_terminal_driver_dispatches_default_and_remapped_format(tmp_path, sequence, overlay):
    async with _mounted_athena_app(tmp_path, keymap=KeymapStore(overlay=overlay)) as (
        app,
        _ctx,
        vm,
        client,
        pilot,
    ):
        editor = app.query_one("#athena-editor", TextArea)
        sql = "SELECT value FROM events WHERE value > 1"
        await seed_athena_sql(pilot, vm.query, editor, sql)
        await focus_and_settle(editor)
        key = next(token for token in XTermParser().feed(sequence) if isinstance(token, events.Key))
        app._driver.process_message(key)
        await pilot.pause()
        await drain_workers(app)
        assert vm.query.sql == format_sql(sql).sql
        assert editor.text == vm.query.sql
        assert not client.start_calls
