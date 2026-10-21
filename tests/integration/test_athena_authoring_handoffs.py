"""Real confirmation and adoption preserve the exact outgoing SQL document."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import TextArea

from aws_tui.domain.data_catalog import TableRef
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.messages import OpenAthenaTableRequest
from tests.helpers import drain_workers, seed_athena_sql, wait_until
from tests.integration.test_athena_page import _mounted_athena_app
from tests.integration.test_credential_recovery import _connected_probe
from tests.unit.vm.athena.test_authoring_vm import yes


def target(vm):
    context = vm.context
    return TableRef(
        context.catalog, context.database, "events", context.connection_name, context.region
    )


async def prepare(app, vm, pilot, tmp_path):
    path = tmp_path / "source.sql"
    path.write_text("SELECT 'file baseline'")
    assert await vm.authoring.open_sql(path, confirm=yes)
    sql = "SELECT 'modified file'"
    await seed_athena_sql(pilot, vm.query, app.query_one("#athena-editor", TextArea), sql)
    return path, sql


@pytest.mark.parametrize("answer", [False, True])
async def test_handoff_asks_before_discarding_modified_file(tmp_path, monkeypatch, answer):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        path, sql = await prepare(app, source, pilot, tmp_path)
        document = source.authoring.export_snapshot()
        ctx.hub.send(OpenAthenaTableRequest(target(source)))
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal),
            what="handoff replacement confirmation",
            timeout=1,
        )
        assert ctx.root_vm.content_host.current is source
        assert source.query.sql == sql
        assert source.authoring.export_snapshot() == document
        await pilot.press(*(("right", "enter") if answer else ("escape",)))
        await drain_workers(app)
        current = ctx.root_vm.content_host.current
        if answer:
            assert isinstance(current, AthenaPageVM)
            assert current is not source
            assert current.query.sql.endswith('"events" LIMIT 5')
            assert current.authoring.path is None
            assert current.authoring.export_snapshot().origin == "Table query"
            assert not current.authoring.dirty
        else:
            assert current is source
            assert source.query.sql == sql
            assert source.authoring.path == path
            assert source.authoring.dirty
        assert client.start_calls == []


@pytest.mark.parametrize("change", ["edit-roundtrip", "equal-document", "context"])
async def test_handoff_confirmation_cannot_authorize_later_document_state(
    tmp_path, monkeypatch, change
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        _path, sql = await prepare(app, source, pilot, tmp_path)
        ctx.hub.send(OpenAthenaTableRequest(target(source)))
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="handoff confirmation", timeout=1
        )
        if change == "edit-roundtrip":
            source.query.set_sql("SELECT 'intermediate'")
            source.query.set_sql(sql)
        elif change == "equal-document":
            source.authoring.install_snapshot(source.authoring.export_snapshot())
        else:
            await source.select_workgroup("analysts")
        await pilot.press("right", "enter")
        await drain_workers(app)
        assert ctx.root_vm.content_host.current is source
        assert source.query.sql == sql
        assert source.authoring.path is not None
        assert client.start_calls == []


async def test_handoff_rechecks_actual_typing_after_outgoing_setup_drains(tmp_path, monkeypatch):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        _path, sql = await prepare(app, source, pilot, tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        host = ctx.root_vm.content_host
        original = host._cancel_and_drain_setup

        async def gated():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(host, "_cancel_and_drain_setup", gated)
        try:
            ctx.hub.send(OpenAthenaTableRequest(target(source)))
            await wait_until(
                lambda: isinstance(app.screen, ConfirmModal), what="handoff confirmation", timeout=1
            )
            await pilot.press("right", "enter")
            await wait_until(entered.is_set, what="candidate waits for outgoing setup drain")
            editor = app.query_one("#athena-editor", TextArea)
            editor.move_cursor((0, len(sql)))
            editor.insert(" -- later input")
            release.set()
            await drain_workers(app)
            await pilot.pause()
            assert ctx.root_vm.content_host.current is source
            assert source.query.sql == editor.text == sql + " -- later input"
            assert source.authoring.dirty
            assert not editor.read_only
            assert client.start_calls == []
        finally:
            release.set()


async def test_failed_accepted_handoff_restores_file_baseline_and_modified_sql(
    tmp_path, monkeypatch
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        path, sql = await prepare(app, source, pilot, tmp_path)
        document = source.authoring.export_snapshot()
        original = app._mount_service_view
        failed = False

        async def fail_once(service_id, **kwargs):
            nonlocal failed
            if service_id == "athena" and not failed:
                failed = True
                return False
            return await original(service_id, **kwargs)

        monkeypatch.setattr(app, "_mount_service_view", fail_once)
        ctx.hub.send(OpenAthenaTableRequest(target(source)))
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="handoff confirmation", timeout=1
        )
        await pilot.press("right", "enter")
        await drain_workers(app)
        restored = ctx.root_vm.content_host.current
        assert isinstance(restored, AthenaPageVM)
        assert restored.query.sql == sql
        assert restored.authoring.path == path
        assert restored.authoring.export_snapshot() == document
        assert restored.authoring.dirty
        assert client.start_calls == []


async def begin(app, ctx, source, pilot):
    ctx.hub.send(OpenAthenaTableRequest(target(source)))
    await wait_until(
        lambda: isinstance(app.screen, ConfirmModal), what="actual handoff confirmation"
    )
    task = next(iter(app.service_navigation.navigation_tasks))
    return task


async def test_cancel_during_confirmation_preserves_live_document_and_cleans_modal(
    tmp_path, monkeypatch
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        path, sql = await prepare(app, source, pilot, tmp_path)
        document = source.authoring.export_snapshot()
        task = await begin(app, ctx, source, pilot)
        task.cancel()
        await drain_workers(app)
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert ctx.root_vm.content_host.current is source
        assert source.query.sql == sql
        assert source.authoring.export_snapshot() == document
        assert source.authoring.path == path
        assert source.authoring.dirty
        assert not app.query_one("#athena-editor", TextArea).read_only
        assert client.start_calls == []


@pytest.mark.parametrize("outcome", ["cancel", "error"])
async def test_preclaim_failure_keeps_newer_native_edit(tmp_path, monkeypatch, outcome):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        path, sql = await prepare(app, source, pilot, tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        host = ctx.root_vm.content_host
        original = host._cancel_and_drain_setup
        first = True

        async def gated():
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
                if outcome == "error":
                    raise RuntimeError("controlled preclaim failure")
            await original()

        monkeypatch.setattr(host, "_cancel_and_drain_setup", gated)
        try:
            task = await begin(app, ctx, source, pilot)
            await pilot.press("right", "enter")
            await wait_until(entered.is_set, what="preclaim setup drain")
            editor = app.query_one("#athena-editor", TextArea)
            assert not editor.read_only
            editor.move_cursor((0, len(sql)))
            editor.insert(" -- newer input")
            await wait_until(
                lambda: source.query.sql == sql + " -- newer input",
                what="native newer edit acknowledged",
            )
            if outcome == "cancel":
                task.cancel()
            release.set()
            await drain_workers(app)
            await pilot.pause()
            current = ctx.root_vm.content_host.current
            assert isinstance(current, AthenaPageVM)
            assert current.query.sql == sql + " -- newer input", (
                "preclaim rollback discarded newer input"
            )
            assert current is source, (
                "preclaim failure must not replace still-live outgoing document"
            )
            assert current.authoring.path == path
            assert current.authoring.dirty
            assert not app.query_one("#athena-editor", TextArea).read_only
            assert client.start_calls == []
        finally:
            release.set()


@pytest.mark.parametrize("outcome", ["cancel", "settings"])
async def test_postclaim_retirement_preserves_owned_rollback_or_newer_navigation(
    tmp_path, monkeypatch, outcome
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        path, sql = await prepare(app, source, pilot, tmp_path)
        document = source.authoring.export_snapshot()
        entered, release = asyncio.Event(), asyncio.Event()
        original = source.shutdown

        async def gated():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(source, "shutdown", gated)
        try:
            task = await begin(app, ctx, source, pilot)
            await pilot.press("right", "enter")
            await wait_until(entered.is_set, what="claimed outgoing shutdown")
            assert app.query_one("#athena-editor", TextArea).read_only
            if outcome == "cancel":
                task.cancel()
            else:
                app.action_open_settings()
            release.set()
            await drain_workers(app)
            await pilot.pause()
            current = ctx.root_vm.content_host.current
            if outcome == "cancel":
                assert isinstance(current, AthenaPageVM)
                assert current.query.sql == sql
                assert current.authoring.path == path
                assert current.authoring.export_snapshot() == document
                assert current.authoring.dirty
            else:
                assert ctx.root_vm.content_host.current_id == "settings"
                assert current is not None
            assert len(app.screen_stack) == 1
            assert client.start_calls == []
        finally:
            release.set()


async def test_failed_rollback_is_recovered_before_a_successor_can_replace_sql(
    tmp_path, monkeypatch
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, source, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        path, sql = await prepare(app, source, pilot, tmp_path)
        document = source.authoring.export_snapshot()
        service = ctx.registry.get("athena")
        original_build = service.build_vm
        original_close = source.close_input_admission
        built = 0
        fired = False
        ref = target(source)

        def build(connection):
            nonlocal built
            built += 1
            if built == 2:
                raise RuntimeError("controlled first rollback construction failure")
            return original_build(connection)

        def close_and_send():
            nonlocal fired
            original_close()
            if not fired:
                fired = True
                ctx.hub.send(OpenAthenaTableRequest(ref))

        monkeypatch.setattr(service, "build_vm", build)
        monkeypatch.setattr(source, "close_input_admission", close_and_send)
        await begin(app, ctx, source, pilot)
        await pilot.press("right", "enter")
        await wait_until(
            lambda: built >= 3 and isinstance(app.screen, ConfirmModal),
            what="successor asks after recovering the failed rollback",
            timeout=3,
        )
        recovered = ctx.root_vm.content_host.current
        assert isinstance(recovered, AthenaPageVM)
        assert recovered.query.sql == sql
        assert recovered.authoring.path == path
        assert recovered.authoring.export_snapshot() == document
        await pilot.press("escape")
        await drain_workers(app)
        assert ctx.root_vm.content_host.current is recovered
        assert recovered.authoring.dirty
        assert client.start_calls == []


@pytest.mark.parametrize("acknowledged", [False, True])
async def test_pending_retry_preserves_newer_actual_typing_during_setup_drain(
    tmp_path, monkeypatch, acknowledged
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, old, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        _path, _old_sql = await prepare(app, old, pilot, tmp_path)
        pending = app._capture_table_handoff_snapshot()
        connection = ctx.root_vm.active_connection
        with app.service_navigation.suppress_selection("athena"):
            assert await ctx.root_vm.switch_connection_and_service(
                connection, ctx.root_vm.active_auth_state, "athena"
            )
        assert await app._mount_service_view("athena", required_connection=connection)
        await app._wait_for_current_service_setup()
        await pilot.pause()
        current = ctx.root_vm.content_host.current
        assert isinstance(current, AthenaPageVM)
        assert current is not old
        assert current.query.sql == ""
        assert not current.authoring.dirty
        editor = app.query_one("#athena-editor", TextArea)
        assert editor.text == ""
        assert not editor.read_only
        app._pending_table_handoff = pending
        entered, release = asyncio.Event(), asyncio.Event()
        original = ctx.root_vm.content_host._cancel_and_drain_setup
        first = True

        async def gated():
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            await original()

        monkeypatch.setattr(ctx.root_vm.content_host, "_cancel_and_drain_setup", gated)
        try:
            ctx.hub.send(OpenAthenaTableRequest(target(current)))
            await wait_until(entered.is_set, what="pending recovery enters actual setup drain")
            newer = "SELECT 'newer retry input'"
            editor.insert(newer)
            if acknowledged:
                await wait_until(
                    lambda: current.query.sql == newer, what="new retry input acknowledged"
                )
            else:
                assert editor.text == newer
                assert current.query.sql == ""
            release.set()
            await wait_until(
                lambda: (
                    isinstance(app.screen, ConfirmModal)
                    or not app.service_navigation.navigation_tasks
                ),
                what="retry settles or asks successor",
            )
            if isinstance(app.screen, ConfirmModal):
                await pilot.press("escape")
            await drain_workers(app)
            await pilot.pause()
            latest = ctx.root_vm.content_host.current
            assert latest is current, "retry retired the newer editor before owning input"
            assert latest.query.sql == newer
            assert app.query_one("#athena-editor", TextArea).text == newer
            assert app._pending_table_handoff is pending
            assert not client.start_calls
        finally:
            release.set()


async def test_pending_recovery_has_no_editable_empty_document_before_restore(
    tmp_path, monkeypatch
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, old, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        _path, old_sql = await prepare(app, old, pilot, tmp_path)
        pending = app._capture_table_handoff_snapshot()
        connection = ctx.root_vm.active_connection
        with app.service_navigation.suppress_selection("athena"):
            assert await ctx.root_vm.switch_connection_and_service(
                connection, ctx.root_vm.active_auth_state, "athena"
            )
        assert await app._mount_service_view("athena", required_connection=connection)
        await app._wait_for_current_service_setup()
        await pilot.pause()
        source = ctx.root_vm.content_host.current
        app._pending_table_handoff = pending
        entered, release = asyncio.Event(), asyncio.Event()
        original = app._wait_for_current_service_setup

        async def gated():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(app, "_wait_for_current_service_setup", gated)
        try:
            ctx.hub.send(OpenAthenaTableRequest(target(source)))
            await wait_until(entered.is_set, what="pending recovery setup")
            page = app._athena_page()
            current = ctx.root_vm.content_host.current
            assert (
                page is None
                or page.vm is not current
                or (
                    page.query_one("#athena-editor", TextArea).read_only
                    or page.query_one("#athena-editor", TextArea).text == old_sql
                )
            ), "recovery exposes an editable empty document before restoring the retained SQL"
        finally:
            release.set()
            await wait_until(
                lambda: isinstance(app.screen, ConfirmModal), what="recovered document confirmation"
            )
            await pilot.press("escape")
            await drain_workers(app)
        assert client.start_calls == []


async def test_pending_recovery_can_be_declined_without_discarding_either_document(
    tmp_path, monkeypatch
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, current, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        _path, sql = await prepare(app, current, pilot, tmp_path)
        pending = app._capture_table_handoff_snapshot()
        app._pending_table_handoff = pending
        ctx.hub.send(OpenAthenaTableRequest(target(current)))
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal),
            what="pending recovery replacement decision",
            timeout=2,
        )
        assert "Restore previous SQL" in app.screen.request.title
        await pilot.press("escape")
        await drain_workers(app)
        assert ctx.root_vm.content_host.current is current
        assert current.query.sql == sql
        assert app._pending_table_handoff is pending
        assert client.start_calls == []


async def test_superseded_restore_failure_retains_the_claimed_original_document(
    tmp_path, monkeypatch
):
    async with _mounted_athena_app(tmp_path) as (app, ctx, old, client, pilot):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        await prepare(app, old, pilot, tmp_path)
        snapshot = app._capture_table_handoff_snapshot()
        ref = target(old)
        connection = ctx.root_vm.active_connection
        with app.service_navigation.suppress_selection("athena"):
            assert await ctx.root_vm.switch_connection_and_service(
                connection,
                ctx.root_vm.active_auth_state,
                "athena",
                prepare_vm=lambda candidate: candidate.prime_table_query(ref),
            )
        generation = app.service_navigation.advance("table")
        app.service_navigation.advance("table")

        def unavailable(_connection):
            raise RuntimeError("controlled recovery construction failure")

        monkeypatch.setattr(ctx.registry.get("athena"), "build_vm", unavailable)
        assert await app._restore_superseded_table_handoff(generation, snapshot)
        assert app._pending_table_handoff is snapshot
        assert app._pending_table_handoff.athena.query.sql == old.query.sql
        assert client.start_calls == []


async def test_rollback_rechecks_newer_native_input_after_its_setup_drain(tmp_path, monkeypatch):
    async with _mounted_athena_app(tmp_path) as (app, ctx, old, client, pilot):
        await prepare(app, old, pilot, tmp_path)
        snapshot = app._capture_table_handoff_snapshot()
        connection = ctx.root_vm.active_connection
        with app.service_navigation.suppress_selection("athena"):
            assert await ctx.root_vm.switch_connection_and_service(
                connection, ctx.root_vm.active_auth_state, "athena"
            )
        assert await app._mount_service_view("athena", required_connection=connection)
        await app._wait_for_current_service_setup()
        await pilot.pause()
        current = ctx.root_vm.content_host.current
        editor = app.query_one("#athena-editor", TextArea)
        revision = current.query.editor_revision
        generation = app.service_navigation.advance("table")
        entered, release = asyncio.Event(), asyncio.Event()
        original = ctx.root_vm.content_host._cancel_and_drain_setup

        async def gated():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(ctx.root_vm.content_host, "_cancel_and_drain_setup", gated)

        def owned():
            return (
                ctx.root_vm.content_host.current is current
                and current.query.editor_revision == revision
                and editor.text == ""
            )

        task = asyncio.create_task(
            app._restore_table_handoff_durably(snapshot, generation, input_is_owned=owned)
        )
        try:
            await wait_until(entered.is_set, what="rollback setup drain")
            editor.insert("SELECT 'newer rollback input'")
            release.set()
            restored, _ = await task
            await pilot.pause()
            assert not restored
            assert ctx.root_vm.content_host.current is current
            assert current.query.sql == editor.text == "SELECT 'newer rollback input'"
            assert not editor.read_only
            assert client.start_calls == []
        finally:
            release.set()
            await task
