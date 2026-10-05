"""Writable test-only runtime in the actual app; all AWS services remain fake."""

from __future__ import annotations

import contextlib
import os
from contextlib import asynccontextmanager
from html import unescape
from pathlib import Path
from threading import Event

import pytest
from rich.text import Text
from textual.widgets import Button, Static, TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.infra.athena_draft_store import AthenaDraftStore, DraftPermit, DraftStoreResult
from aws_tui.infra.config_store import ConfigStore
from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.athena_drafts_helpers import record
from tests.helpers import drain_workers, focus_and_settle, seed_athena_sql, wait_until
from tests.integration.test_glue_page import open_service
from tests.unit.ui.test_first_run import _visible_wrapped_text


@asynccontextmanager
async def mounted_draft_app(tmp_path, monkeypatch, *, size=(80, 24)):
    ctx = build_app_context(
        config_dir=tmp_path / "demo-config", cache_dir=tmp_path / "cache", demo=True
    )
    # This replacement is test-only: production demo's runtime remains read-only/off.
    config = ConfigStore(path=tmp_path / "writable" / "config.toml")
    store = AthenaDraftStore(config=config, directory=config.path.parent / "athena-drafts")
    ctx.athena_drafts_vm.dispose()
    runtime = AthenaDraftsVM(
        store=store,
        enabled=False,
        read_only=False,
        directory=config.path.parent / "athena-drafts",
        hub=ctx.hub,
        dispatcher=ctx.dispatcher,
    )
    ctx.athena_drafts_vm = runtime
    service = ctx.registry.get("athena")
    service._drafts = runtime
    from tests.unit.vm.athena.test_page_vm import PageClient

    client = PageClient(connection_name="demo-dev", region="us-east-1")
    service._client_factory = lambda _connection: client

    async def source_current():
        return True

    service._source_check_factory = lambda _connection: source_current

    def no_aws(*args, **kwargs):
        raise AssertionError("real AWS client must not be constructed")

    monkeypatch.setattr("aws_tui.services.athena.service.AthenaClient", no_aws)
    app = AwsTuiApp(ctx)
    app.draft_results = []
    push = app.push_screen

    def capture_result(screen, callback=None, **kwargs):
        if isinstance(screen, AthenaDraftsModal):

            def capture(result):
                app.draft_results.append(result)
                if callback is not None:
                    callback(result)

            return push(screen, capture, **kwargs)
        return push(screen, callback, **kwargs)

    monkeypatch.setattr(app, "push_screen", capture_result)
    try:
        async with app.run_test(size=size) as pilot:
            await drain_workers(app)
            stack = ctx.root_vm.chrome.toast_stack
            for toast in tuple(stack.toasts):
                stack.dismiss(toast.model.id)
            await pilot.pause()
            yield app, ctx, runtime, store, config, client, pilot
    finally:
        with contextlib.suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        await runtime.shutdown()
        ctx.root_vm.dispose()
        runtime.dispose()
        ctx.log_sink.close()


async def tab_to(pilot, identity):
    for _ in range(35):
        if pilot.app.focused is not None and pilot.app.focused.id == identity:
            return
        await pilot.press("tab")
        await pilot.pause()
    raise AssertionError(f"keyboard never reached {identity}")


def capture_ui(app, label):
    destination = os.environ.get("AWS_TUI_SNAPSHOT_ARTIFACT_DIR")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"full-app-{label}-{app.size.width}x{app.size.height}.svg").write_text(
            app.export_screenshot(), encoding="utf-8"
        )


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_app_keyboard_enable_save_restore_delete_disable(tmp_path, monkeypatch, size):
    async with mounted_draft_app(tmp_path, monkeypatch, size=size) as (
        app,
        ctx,
        runtime,
        store,
        config,
        client,
        pilot,
    ):
        app.action_open_settings()
        await wait_until(lambda: bool(app.query(SettingsView)), what="Settings mounted")
        await drain_workers(app)
        app.focus_active_service_pane()
        await pilot.pause()
        await pilot.press("enter")  # Collapse Connections to expose the complete draft panel.
        await pilot.pause()
        await tab_to(pilot, "athena-drafts-toggle")
        await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        assert runtime.enabled
        assert config.load().athena_sql_drafts
        path_widget = app.query_one("#athena-drafts-path", Static)
        expected = Text(str(runtime.directory)).wrap(app.console, path_widget.content_size.width)
        assert "".join(row.plain.rstrip(" ") for row in expected) in _visible_wrapped_text(
            path_widget
        )
        retention = app.query_one("#athena-drafts-retention", Static)
        expected_retention = Text(str(retention.content)).wrap(
            app.console, retention.content_size.width
        )
        assert "".join(
            row.plain.rstrip(" ") for row in expected_retention
        ) in _visible_wrapped_text(retention)
        assert "SQL is stored as plaintext" in str(retention.content)
        capture_ui(app, "settings-enabled")
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        page = app.query_one(AthenaPage)
        vm = page.vm
        await vm.select_workgroup("primary")
        await vm.select_catalog("AwsDataCatalog")
        await vm.select_database("default")
        editor = page.query_one("#athena-editor", TextArea)
        gate, started = Event(), Event()
        save = store.save

        def blocked_save(record, *, permit):
            started.set()
            if not gate.wait(10):
                return DraftStoreResult(code="io")
            return save(record, permit=permit)

        monkeypatch.setattr(store, "save", blocked_save)
        try:
            await seed_athena_sql(pilot, vm.query, editor, "SELECT 42 AS draft_render_marker")
            await wait_until(started.is_set, what="draft commit running")
            assert vm.query.draft_state == "pending"
            await focus_and_settle(editor)
            await tab_to(pilot, "athena-drafts")
            assert ctx.focus_coordinator.focused_slot is FocusSlot.ATHENA_DRAFTS
            assert "Draft pending" in unescape(app.export_screenshot()).replace("\xa0", " ")
            capture_ui(app, "pending")
        finally:
            gate.set()
        await wait_until(lambda: vm.query.draft_state == "saved", what="acknowledged draft saved")
        await pilot.pause()
        assert "Draft saved" in unescape(app.export_screenshot()).replace("\xa0", " ")
        capture_ui(app, "saved")
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, AthenaDraftsModal), what="manager mounted")
        await drain_workers(app)
        await pilot.pause()
        modal = app.screen
        extra = record(
            context=("demo-dev", "us-east-1", "analysts", "AwsDataCatalog", "events"),
            sql="SELECT 2",
        )
        assert save(extra, permit=DraftPermit()).code is None
        await runtime.refresh()
        await pilot.pause()
        capture_ui(app, "manager")
        original = vm.query.sql
        await pilot.press("down", "enter", "up", "enter")
        assert vm.query.sql == original
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused.id == "athena-drafts"
        assert ctx.focus_coordinator.focused_slot is FocusSlot.ATHENA_DRAFTS
        assert app.draft_results == ["closed"]
        monkeypatch.setattr(store, "save", lambda record, *, permit: DraftStoreResult(code="io"))
        await seed_athena_sql(pilot, vm.query, editor, "SELECT 99 AS unsaved_marker")
        await wait_until(lambda: vm.query.draft_state == "error", what="failed save feedback")
        await pilot.pause()
        assert "Draft not saved" in unescape(app.export_screenshot()).replace("\xa0", " ")
        capture_ui(app, "failed-save")
        await focus_and_settle(page.query_one("#athena-drafts", Button))
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, AthenaDraftsModal), what="manager reopened")
        await drain_workers(app)
        await pilot.pause()
        modal = app.screen
        # Select the original record through list arrows, not an action callback.
        from textual.widgets import OptionList

        listing = modal.query_one(OptionList)
        while runtime.items[listing.highlighted].context != vm.context.cache_key:
            await pilot.press("down")
        await tab_to(pilot, "athena-drafts-restore")
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="replace confirmation")
        await pilot.press("escape")
        await drain_workers(app)
        await pilot.pause()
        assert app.screen is modal
        assert vm.query.sql == "SELECT 99 AS unsaved_marker"
        assert client.start_calls == []
        assert app.focused.id == "athena-drafts-restore"
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="replace confirmation again"
        )
        await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        assert not isinstance(app.screen, AthenaDraftsModal)
        assert vm.query.sql == original
        assert app.draft_results == ["closed", "restored"]
        await wait_until(
            lambda: (
                app.focused is editor
                and ctx.focus_coordinator.focused_slot is FocusSlot.ATHENA_PRIMARY
            ),
            what="restored editor focus and native focus projection",
        )
        assert app.focused is editor
        assert ctx.focus_coordinator.focused_slot is FocusSlot.ATHENA_PRIMARY
        assert client.start_calls == []
        capture_ui(app, "restored")
        await tab_to(pilot, "athena-drafts")
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, AthenaDraftsModal), what="manager for deletion"
        )
        await drain_workers(app)
        await pilot.pause()
        await tab_to(pilot, "athena-drafts-delete")
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="delete confirmation")
        await pilot.press("tab", "enter")
        await drain_workers(app)
        await pilot.pause()
        assert len(runtime.items) == 1
        await tab_to(pilot, "athena-drafts-clear")
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="clear confirmation")
        await pilot.press("tab", "enter")
        await drain_workers(app)
        await pilot.pause()
        assert runtime.items == ()
        assert not list(runtime.directory.glob("*.json"))
        await tab_to(pilot, "athena-drafts-close")
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused.id == "athena-drafts"
        assert ctx.focus_coordinator.focused_slot is FocusSlot.ATHENA_DRAFTS
        app.action_open_settings()
        await wait_until(lambda: bool(app.query(SettingsView)), what="Settings reopened")
        await drain_workers(app)
        app.focus_active_service_pane()
        await pilot.pause()
        await tab_to(pilot, "athena-drafts-toggle")
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="disable confirmation")
        await pilot.press("tab", "enter")
        await drain_workers(app)
        assert not runtime.enabled
        assert not config.load().athena_sql_drafts
        assert not list(runtime.directory.glob("*.json"))


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_actual_app_priority_arrows_scroll_long_metadata_and_keep_editor(
    tmp_path, monkeypatch, size
):
    from textual.containers import VerticalScroll
    from textual.widgets import OptionList

    async with mounted_draft_app(tmp_path, monkeypatch, size=size) as (
        app,
        ctx,
        runtime,
        store,
        _config,
        client,
        pilot,
    ):
        await runtime.set_enabled(True)
        long_context = (
            "analytics" + "[literal] " * 80,
            "us-west-2",
            "primary",
            "AwsDataCatalog",
            "default",
        )
        assert store.save(record(context=long_context), permit=DraftPermit()).code is None
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        page = app.query_one(AthenaPage)
        await focus_and_settle(page.query_one("#athena-drafts", Button))
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, AthenaDraftsModal), what="long metadata manager"
        )
        await drain_workers(app)
        await pilot.pause()
        modal = app.screen
        assert modal.query_one(OptionList).highlighted == 0
        await tab_to(pilot, "athena-drafts-restore")
        await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        assert page.vm.draft_recovery_error is not None
        assert page.vm.query.sql == ""
        assert client.start_calls == []
        await pilot.press("shift+tab")
        await pilot.pause()
        detail = modal.query_one("#athena-drafts-detail-scroll", VerticalScroll)
        assert app.focused is detail
        assert ctx.focus_coordinator.is_modal
        assert "Connection:" in unescape(app.export_screenshot())
        assert "[literal]" in unescape(app.export_screenshot())
        capture_ui(app, "long-metadata-top")
        await pilot.press(*(["down"] * 30))
        await wait_until(
            lambda: detail.scroll_y == detail.max_scroll_y,
            what="long metadata bottom through app priority arrows",
        )
        await pilot.pause()
        rows = app.screen._compositor.render_strips()
        visible = "".join(
            row.crop(detail.content_region.x, detail.content_region.right).text.rstrip()
            for row in rows[detail.content_region.y : detail.content_region.bottom]
        )
        for text in (
            "Region: us-west-2",
            "Workgroup: primary",
            "Catalog: AwsDataCatalog",
            "Database: default",
            "Saved: 2026-10-05T16:30:00+00:00",
        ):
            assert text in visible
        expected = Text(page.vm.draft_recovery_error).wrap(app.console, detail.content_size.width)
        assert "".join(row.plain.rstrip(" ") for row in expected) in visible
        capture_ui(app, "long-metadata-bottom")
        await tab_to(pilot, "athena-drafts-keep")
        await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        assert page.vm.draft_recovery_error is None
        assert page.vm.query.sql == ""
        assert client.start_calls == []
        await pilot.press("escape")


@pytest.mark.parametrize("action", ["delete", "clear"])
async def test_actual_app_declines_danger_confirmation_and_restores_manager_focus(
    tmp_path, monkeypatch, action
):
    async with mounted_draft_app(tmp_path, monkeypatch) as (
        app,
        ctx,
        runtime,
        store,
        _config,
        _client,
        pilot,
    ):
        await runtime.set_enabled(True)
        saved = record(sql="SELECT 7")
        assert store.save(saved, permit=DraftPermit()).code is None
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        await focus_and_settle(app.query_one("#athena-drafts", Button))
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, AthenaDraftsModal), what="manager for cancellation"
        )
        await drain_workers(app)
        await pilot.pause()
        modal = app.screen
        identity = f"athena-drafts-{action}"
        await tab_to(pilot, identity)
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="danger confirmation")
        assert app.screen.request.danger
        await pilot.press("enter")  # Danger requests initially select Cancel.
        await drain_workers(app)
        await pilot.pause()
        assert app.screen is modal
        assert app.focused.id == identity
        assert len(runtime.items) == 1
        assert (runtime.directory / f"{saved.id}.json").is_file()
        await pilot.press("escape")


async def test_closing_manager_does_not_cancel_owned_draft_deletion(tmp_path, monkeypatch):
    async with mounted_draft_app(tmp_path, monkeypatch) as (
        app,
        ctx,
        runtime,
        store,
        _config,
        _client,
        pilot,
    ):
        await runtime.set_enabled(True)
        saved = record(sql="SELECT 8")
        assert store.save(saved, permit=DraftPermit()).code is None
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        await focus_and_settle(app.query_one("#athena-drafts", Button))
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, AthenaDraftsModal), what="manager for owned deletion"
        )
        await drain_workers(app)
        await pilot.pause()
        gate, started = Event(), Event()
        original = store.delete

        def held(identity):
            started.set()
            assert gate.wait(10)
            return original(identity)

        monkeypatch.setattr(store, "delete", held)
        try:
            await tab_to(pilot, "athena-drafts-delete")
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, ConfirmModal), what="delete accepted before close"
            )
            await pilot.press("tab", "enter")
            await wait_until(started.is_set, what="owned deletion worker in flight")
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, AthenaDraftsModal)
            assert app.focused.id == "athena-drafts"
        finally:
            gate.set()
        await wait_until(
            lambda: not runtime.busy, what="owned deletion completes after manager close"
        )
        assert runtime.items == ()
        assert not (runtime.directory / f"{saved.id}.json").exists()


@pytest.mark.parametrize("mixed", [False, True])
async def test_actual_manager_skipped_records_warning_and_confirmed_clear(
    tmp_path, monkeypatch, mixed
):
    from aws_tui.infra.athena_draft_store import draft_id

    async with mounted_draft_app(tmp_path, monkeypatch) as (
        app,
        ctx,
        runtime,
        store,
        _config,
        client,
        pilot,
    ):
        assert await runtime.set_enabled(True)
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        page = app.query_one(AthenaPage)
        await page.vm.select_workgroup("primary")
        await page.vm.select_catalog("AwsDataCatalog")
        await page.vm.select_database("default")
        valid = record(page.vm.context.cache_key)
        if mixed:
            assert store.save(valid, permit=DraftPermit()).code is None
        corrupt = runtime.directory / (draft_id((*valid.context[:4], "broken")) + ".json")
        runtime.directory.mkdir(exist_ok=True)
        corrupt.write_bytes(b'{"PRIVATE_CORRUPT_PAYLOAD":')
        unrelated = runtime.directory / "notes.txt"
        unrelated.write_text("leave me")
        modal = AthenaDraftsModal(page.vm, hub=ctx.hub)
        app.push_screen(modal)
        await drain_workers(app)
        await pilot.pause()
        detail = modal.query_one("#athena-drafts-warning", Static)
        assert "1 local draft record(s) could not be read" in str(detail.content)
        assert "1 local draft record(s) could not be read" in _visible_wrapped_text(detail)
        assert runtime.skipped == 1
        assert runtime.error_text is None
        assert "PRIVATE_CORRUPT_PAYLOAD" not in app.export_screenshot()
        assert not modal.query_one("#athena-drafts-clear", Button).disabled
        assert modal.query_one("#athena-drafts-restore", Button).disabled == (not mixed)
        capture_ui(app, "skipped-mixed" if mixed else "skipped-only")
        if mixed:
            await tab_to(pilot, "athena-drafts-restore")
            await pilot.press("enter")
            await drain_workers(app)
            assert page.vm.query.sql == valid.sql
            assert client.start_calls == []
            modal = AthenaDraftsModal(page.vm, hub=ctx.hub)
            app.push_screen(modal)
            await drain_workers(app)
        await tab_to(pilot, "athena-drafts-clear")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        assert corrupt.exists()
        await pilot.press("escape")
        await drain_workers(app)
        assert corrupt.exists()
        await tab_to(pilot, "athena-drafts-clear")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("tab", "enter")
        await drain_workers(app)
        await pilot.pause()
        assert not corrupt.exists()
        assert store.list().records == ()
        assert runtime.items == ()
        assert runtime.skipped == 0
        assert "could not be read" not in str(
            modal.query_one("#athena-drafts-warning", Static).content
        )
        assert unrelated.read_text() == "leave me"


@pytest.mark.parametrize("unknown", [False, True])
async def test_actual_settings_failed_enable_has_keyboard_retry(tmp_path, monkeypatch, unknown):
    from aws_tui.infra import athena_draft_store as module

    async with mounted_draft_app(tmp_path, monkeypatch) as (
        app,
        _ctx,
        runtime,
        _store,
        config,
        _client,
        pilot,
    ):
        setting, directory = config.set_athena_sql_drafts, module._private_directory
        load = config.load
        rollback_attempted = False

        def fail_load():
            if unknown and rollback_attempted:
                raise OSError("PRIVATE_SETTINGS_READBACK")
            return load()

        def fail_rollback(enabled):
            nonlocal rollback_attempted
            if not enabled:
                rollback_attempted = True
                raise OSError("PRIVATE_SETTINGS_FAILURE")
            setting(enabled)

        def fail_directory(*args, **kwargs):
            raise OSError("PRIVATE_SETTINGS_FAILURE")

        monkeypatch.setattr(config, "load", fail_load)
        monkeypatch.setattr(config, "set_athena_sql_drafts", fail_rollback)
        monkeypatch.setattr(module, "_private_directory", fail_directory)
        app.action_open_settings()
        await wait_until(lambda: bool(app.query(SettingsView)), what="Settings mounted")
        await drain_workers(app)
        app.focus_active_service_pane()
        await pilot.pause()
        await pilot.press("enter")
        await tab_to(pilot, "athena-drafts-toggle")
        await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        status = app.query_one("#athena-drafts-setting-status", Static)
        assert "saving is suspended" in str(status.content)
        assert runtime.enabled == (not unknown)
        assert load().athena_sql_drafts
        assert "Disable and delete" in str(app.query_one("#athena-drafts-toggle", Button).label)
        assert "PRIVATE_SETTINGS_FAILURE" not in app.export_screenshot()
        capture_ui(app, "settings-enable-unknown" if unknown else "settings-enable-failed")
        monkeypatch.setattr(config, "load", load)
        monkeypatch.setattr(config, "set_athena_sql_drafts", setting)
        monkeypatch.setattr(module, "_private_directory", directory)
        await tab_to(pilot, "athena-drafts-retry-enable")
        await pilot.press("enter")
        await drain_workers(app)
        assert runtime.enabled
        assert not runtime._saving_suspended
        assert not runtime.enable_required
        assert runtime.error_text is None
