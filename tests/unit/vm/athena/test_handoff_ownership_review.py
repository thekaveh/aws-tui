"""Independent counterexamples for stale Athena handoff ownership."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest
from textual.widgets import TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.messages import OpenAthenaTableRequest
from tests.helpers import wait_until
from tests.integration.test_athena_handoff_races import _gated_app
from tests.integration.test_glue_athena_direct_selection import _seed_nondefault_glue_table
from tests.integration.test_glue_athena_navigation import _athena_client, _open_service
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


class GatedDatabaseClient(PageClient):
    def __init__(self) -> None:
        super().__init__()
        self.databases[("primary", "AwsDataCatalog")] = ["default", "newer_choice"]
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def list_databases_page(self, catalog, *, workgroup=None, start_token=None):
        if start_token is None:
            rows, _ = await super().list_databases_page(catalog, workgroup=workgroup)
            return rows, "delayed-page"
        self.entered.set()
        await self.release.wait()
        raise PermissionDeniedError("controlled discovery failure")


def _target() -> TableRef:
    return TableRef("AwsDataCatalog", "missing_target", "events", "analytics", "us-west-2")


@pytest.mark.asyncio
async def test_newer_database_choice_supersedes_pending_discovery_failure() -> None:
    client = GatedDatabaseClient()
    page = make_page_vm(client)
    await page.setup()
    page.query.set_sql("SELECT 1")
    pending = asyncio.create_task(page.open_table(_target()))
    try:
        await asyncio.wait_for(client.entered.wait(), timeout=1)
        await page.select_database("newer_choice")
        page.query.set_sql("SELECT 2 -- newer editor choice")
        client.release.set()
        outcome = (await asyncio.gather(pending, return_exceptions=True))[0]

        assert not isinstance(outcome, BaseException), outcome
        assert page.context.database == "newer_choice"
        assert page.query.sql == "SELECT 2 -- newer editor choice"
    finally:
        client.release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_cancelled_direct_handoff_preserves_newer_context_and_editor() -> None:
    client = GatedDatabaseClient()
    page = make_page_vm(client)
    await page.setup()
    page.query.set_sql("SELECT 1")
    pending = asyncio.create_task(page.open_table(_target()))
    try:
        await asyncio.wait_for(client.entered.wait(), timeout=1)
        await page.select_database("newer_choice")
        page.query.set_sql("SELECT 2 -- newer editor choice")
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending

        assert page.context.database == "newer_choice"
        assert page.query.sql == "SELECT 2 -- newer editor choice"
    finally:
        client.release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_cancelled_old_handoff_does_not_end_new_same_table_prime() -> None:
    client = GatedDatabaseClient()
    page = make_page_vm(client)
    await page.setup()
    pending = asyncio.create_task(page.open_table(_target()))
    try:
        await asyncio.wait_for(client.entered.wait(), timeout=1)
        page.prime_table_query(_target())
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending

        assert page.query.is_context_resolving
    finally:
        client.release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_cancelled_mounted_handoff_preserves_newer_context_and_editor(
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
            entered.set()
            await release.wait()
            return rows[2:], None
        return await original(catalog, workgroup=workgroup, start_token=start_token)

    monkeypatch.setattr(client, "list_databases_page", gated_databases)
    app = AwsTuiApp(ctx)
    try:
        async with _gated_app(app, release) as pilot:
            await _open_service(ctx, app, pilot, "glue")
            ctx.hub.send(OpenAthenaTableRequest(target))
            await wait_until(entered.is_set, what="handoff database discovery blocked")
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, AthenaPageVM)
            await vm.select_database("review_selected_database")
            assert vm.context.database == "review_selected_database"
            vm.query.set_sql("SELECT 2 -- newer mounted editor choice")
            navigation = next(iter(app._table_navigation_tasks))
            navigation.cancel()
            await asyncio.wait_for(asyncio.gather(navigation, return_exceptions=True), timeout=5)
            await pilot.pause()

            assert ctx.root_vm.content_host.current is vm
            assert vm.context.database == "review_selected_database"
            assert vm.query.sql == "SELECT 2 -- newer mounted editor choice"
    finally:
        release.set()
        with suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_handoff", [False, True])
async def test_queued_handoff_rollback_preserves_later_actual_editor_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_handoff: bool
) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    client = _athena_client(ctx, "demo-dev")
    target = _seed_nondefault_glue_table(ctx)
    entered, release, rollback_queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = client.list_databases_page

    async def gated_databases(catalog, *, workgroup=None, start_token=None):
        if catalog == "AwsDataCatalog" and workgroup == "dev-analytics":
            rows, _ = await original(catalog, workgroup=workgroup)
            if start_token is None:
                return rows[:1], "handoff-next"
            entered.set()
            await release.wait()
            raise PermissionDeniedError("controlled discovery failure")
        return await original(catalog, workgroup=workgroup, start_token=start_token)

    monkeypatch.setattr(client, "list_databases_page", gated_databases)
    app = AwsTuiApp(ctx)
    original_restore = app._restore_table_handoff

    async def observe_queued_restore(*args, **kwargs):
        rollback_queued.set()
        return await original_restore(*args, **kwargs)

    monkeypatch.setattr(app, "_restore_table_handoff", observe_queued_restore)
    try:
        async with _gated_app(app, release) as pilot:
            await _open_service(ctx, app, pilot, "glue")
            ctx.hub.send(OpenAthenaTableRequest(target))
            await asyncio.wait_for(entered.wait(), timeout=5)
            vm = ctx.root_vm.content_host.current
            assert isinstance(vm, AthenaPageVM)
            navigation = next(iter(app._table_navigation_tasks))
            async with app._service_navigation_lock:
                if cancel_handoff:
                    navigation.cancel()
                else:
                    release.set()
                await asyncio.wait_for(rollback_queued.wait(), timeout=5)
                assert not navigation.done()
                editor = app.query_one("#athena-editor", TextArea)
                starter = editor.text
                assert await pilot.click(editor)
                await pilot.press("end", "enter", *"queued rollback newer input")
                await wait_until(
                    lambda: vm.query.sql == editor.text and editor.text != starter,
                    what="real editor input acknowledged while rollback waits for navigation lock",
                )
                expected = editor.text

            await asyncio.wait_for(asyncio.gather(navigation, return_exceptions=True), timeout=5)
            await pilot.pause()
            assert ctx.root_vm.content_host.current is vm
            assert vm.query.sql == expected
            assert app.query_one("#athena-editor", TextArea).text == expected
    finally:
        release.set()
        with suppress(Exception):
            await ctx.root_vm.content_host.shutdown()
        ctx.root_vm.dispose()
        ctx.log_sink.close()
