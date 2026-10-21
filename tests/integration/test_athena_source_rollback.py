"""Mounted source transactions retain the outgoing Athena editor on rollback."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from textual.widgets import Button, Static, TextArea

from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.vm.athena.page_vm import AthenaPageVM
from tests.helpers import drain_workers, seed_athena_sql
from tests.integration.test_athena_drafts import mounted_draft_app
from tests.integration.test_credential_recovery import _connected_probe
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_page_vm import PageClient


@asynccontextmanager
async def source_app(tmp_path, monkeypatch):
    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as fixture:
        app, ctx, _runtime, _store, _config, client, pilot = fixture
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        service = ctx.registry.get("athena")
        monkeypatch.setattr(
            service,
            "_client_factory",
            lambda connection: (
                client
                if connection.name == "demo-dev"
                else PageClient(connection_name=connection.name, region=connection.region)
            ),
        )
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        source = ctx.root_vm.content_host.current
        assert isinstance(source, AthenaPageVM)
        sql = "SELECT 'original Ω'\n-- keep trailing newline\n"
        await seed_athena_sql(pilot, source.query, app.query_one("#athena-editor", TextArea), sql)
        yield app, ctx, service, source, sql, pilot


@pytest.mark.parametrize("draft_state", ["off", "pending", "saved", "context_required"])
@pytest.mark.parametrize("outcome", ["failed_mount", "cancelled_mount"])
async def test_source_rollback_restores_exact_editor_context_and_provenance(
    tmp_path, monkeypatch, draft_state, outcome
):
    # Keep pending edits deterministic; explicit shutdown flush remains real.
    monkeypatch.setattr("aws_tui.vm.athena.drafts_vm.DEBOUNCE_SECONDS", 3600)
    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as (
        app,
        ctx,
        runtime,
        store,
        _config,
        client,
        pilot,
    ):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        service = ctx.registry.get("athena")
        clients = {"demo-dev": client}

        def client_for(connection):
            if connection.name not in clients:
                clients[connection.name] = PageClient(
                    connection_name=connection.name, region=connection.region
                )
            return clients[connection.name]

        monkeypatch.setattr(service, "_client_factory", client_for)
        if draft_state != "off":
            assert await runtime.set_enabled(True)
        client.databases["primary", "AwsDataCatalog"].append("other_database")
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        source = ctx.root_vm.content_host.current
        assert isinstance(source, AthenaPageVM)
        prior = ctx.root_vm.active_connection
        assert prior is not None
        sql = "SELECT 123456 AS unsaved_sql\n-- exact [Ω] editor\n"
        await seed_athena_sql(pilot, source.query, app.query_one("#athena-editor", TextArea), sql)
        session = source._draft_session
        assert session is not None
        if draft_state in {"saved", "context_required"}:
            await session.flush(deadline=asyncio.get_running_loop().time() + 2)
            assert source.query.draft_state == "saved"
        if draft_state == "context_required":
            await source.select_database("other_database")
        assert source.query.draft_state == draft_state
        context = source.context
        provenance = source.export_editor_snapshot()
        guard = source.query.draft_execution_blocked
        mount = app._mount_service_view
        target_started = asyncio.Event()
        targets = []

        async def controlled_mount(service_id, *, required_connection=None):
            assert required_connection is not None
            targets.append(required_connection.name)
            if required_connection != prior:
                target_started.set()
                if outcome == "cancelled_mount":
                    await asyncio.Event().wait()
                return False
            return await mount(service_id, required_connection=required_connection)

        monkeypatch.setattr(app, "_mount_service_view", controlled_mount)
        switch = asyncio.create_task(app.action_swap_source())
        try:
            await asyncio.wait_for(target_started.wait(), timeout=3)
            assert source._disposed
            if outcome == "cancelled_mount":
                switch.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(switch, timeout=5)
            else:
                await asyncio.wait_for(switch, timeout=5)
        finally:
            if not switch.done():
                switch.cancel()
                await asyncio.gather(switch, return_exceptions=True)
        await drain_workers(app)
        await pilot.pause()

        restored = ctx.root_vm.content_host.current
        assert isinstance(restored, AthenaPageVM)
        assert restored is not source
        assert ctx.root_vm.active_connection == prior
        assert targets == ["demo-prod", "demo-dev"]
        assert app.query_one(AthenaPage).vm is restored
        assert restored.context == context
        assert restored.query.sql == sql
        assert app.query_one("#athena-editor", TextArea).text == sql
        restored_provenance = restored.export_editor_snapshot()
        assert restored_provenance.editor.draft is not None
        assert provenance.editor.draft is not None
        assert (
            restored_provenance.editor.draft.bound_context == provenance.editor.draft.bound_context
        )
        assert restored_provenance.editor.recovery_guard == provenance.editor.recovery_guard
        assert restored.query.draft_execution_blocked is guard
        if guard:
            assert app.query_one("#athena-execute", Button).disabled
        if restored.query.draft_state == "saved":
            assert any(
                row.sql == sql and row.context == provenance.editor.draft.bound_context.cache_key
                for row in store.list().records
            )
        assert all(candidate.start_calls == [] for candidate in clients.values())


@pytest.mark.parametrize("enabled", [False, True])
async def test_successful_source_cycle_still_clears_outgoing_sql(tmp_path, monkeypatch, enabled):
    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as (
        app,
        ctx,
        runtime,
        _store,
        _config,
        client,
        pilot,
    ):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        service = ctx.registry.get("athena")
        monkeypatch.setattr(
            service,
            "_client_factory",
            lambda connection: (
                client
                if connection.name == "demo-dev"
                else PageClient(connection_name=connection.name, region=connection.region)
            ),
        )
        if enabled:
            assert await runtime.set_enabled(True)
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        source = ctx.root_vm.content_host.current
        assert isinstance(source, AthenaPageVM)
        await seed_athena_sql(
            pilot, source.query, app.query_one("#athena-editor", TextArea), "SELECT 'outgoing'"
        )
        await app.action_swap_source()
        await drain_workers(app)
        await pilot.pause()
        target = ctx.root_vm.content_host.current
        assert isinstance(target, AthenaPageVM)
        assert target.context.connection_name == "demo-prod"
        assert app.query_one(AthenaPage).vm is target
        assert target.query.sql == ""
        assert app.query_one("#athena-editor", TextArea).text == ""


@pytest.mark.parametrize("route", ["header", "handler", "action"])
async def test_retired_source_controls_do_not_advance_navigation(tmp_path, monkeypatch, route):
    from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader

    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as (
        app,
        ctx,
        _runtime,
        _store,
        _config,
        _client,
        pilot,
    ):
        monkeypatch.setattr(ctx.aws_session, "probe_token", _connected_probe)
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        source = ctx.root_vm.content_host.current
        assert isinstance(source, AthenaPageVM)
        page = app.query_one(AthenaPage)
        header = page.query_one(ServiceSourceHeader)
        prior = ctx.root_vm.active_connection
        owner = app._service_navigation_owner
        origin = app._capture_discovery_origin()
        # A queued message can outlive the input claim that retired this page.
        page.disabled = True
        source.close_input_admission()
        if route == "header":
            app.on_service_source_header_source_selected(
                ServiceSourceHeader.SourceSelected(header, "demo-prod", "us-west-2")
            )
        elif route == "handler":
            app._handle_swap_source()
        else:
            await app.action_swap_source(origin=origin)
        await drain_workers(app)
        await pilot.pause()
        assert app._service_navigation_owner == owner
        assert ctx.root_vm.active_connection == prior
        assert ctx.root_vm.content_host.current is source


async def test_mount_waiting_for_transfer_drain_cannot_replace_newer_navigation(
    tmp_path, monkeypatch
):
    from aws_tui.ui.widgets.settings_view import SettingsView

    async with mounted_draft_app(tmp_path, monkeypatch, size=(120, 40)) as (
        app,
        ctx,
        _runtime,
        _store,
        _config,
        _client,
        pilot,
    ):
        await open_service(ctx, pilot, "athena")
        await drain_workers(app)
        prior = ctx.root_vm.active_connection
        drain = app._cancel_transfer_workers_before_content_swap
        entered = asyncio.Event()
        release = asyncio.Event()
        first = True

        async def controlled_drain():
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            await drain()

        monkeypatch.setattr(app, "_cancel_transfer_workers_before_content_swap", controlled_drain)
        stale = asyncio.create_task(app._mount_service_view("athena", required_connection=prior))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            await open_service(ctx, pilot, "settings")
            await drain_workers(app)
            winner = ctx.root_vm.content_host.current
            assert app.query_one(SettingsView).vm is winner
            release.set()
            assert await asyncio.wait_for(stale, timeout=5) is False
            await pilot.pause()
            assert ctx.root_vm.content_host.current is winner
            assert app.query_one(SettingsView).vm is winner
            assert not app.query(AthenaPage)
        finally:
            release.set()
            await asyncio.gather(stale, return_exceptions=True)


@pytest.mark.parametrize(
    "winner", ["edit", "edit_back_to_empty", "queued_edit", "settings", "catalog", "catalog_back"]
)
async def test_newer_input_wins_during_offscreen_source_rollback(tmp_path, monkeypatch, winner):
    from aws_tui.ui.widgets.settings_view import SettingsView

    async with source_app(tmp_path, monkeypatch) as (app, ctx, service, source, _sql, pilot):
        build = service.build_recovery_vm
        mount = app._mount_service_view
        entered = asyncio.Event()
        release = asyncio.Event()
        candidates = []

        def controlled_build(connection):
            recovery = build(connection)
            candidate = recovery.vm
            candidates.append(candidate)
            setup = candidate.setup

            async def gated_setup():
                entered.set()
                await release.wait()
                await setup()

            monkeypatch.setattr(candidate, "setup", gated_setup)
            return recovery

        async def failed_target_mount(service_id, *, required_connection=None):
            mounted = await mount(service_id, required_connection=required_connection)
            return mounted and (
                required_connection is None or required_connection.name == "demo-dev"
            )

        monkeypatch.setattr(service, "build_recovery_vm", controlled_build)
        monkeypatch.setattr(app, "_mount_service_view", failed_target_mount)
        switch = asyncio.create_task(app.action_swap_source())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            target = ctx.root_vm.content_host.current
            assert isinstance(target, AthenaPageVM)
            assert target is not source
            assert app.query_one(AthenaPage).vm is target
            expected = "SELECT 'newer target'"
            expected_context = None
            if winner == "settings":
                # Dispatch new intent now; it needs the source transaction's
                # lock, so draining it before releasing preflight deadlocks.
                ctx.root_vm.services_menu.switch_service_command.execute("settings")
                await pilot.pause()
            elif winner in {"catalog", "catalog_back"}:
                await target.select_catalog("federated")
                if winner == "catalog_back":
                    await target.select_catalog("AwsDataCatalog")
                expected = ""
                expected_context = target.context
            elif winner == "queued_edit":
                # Change the actual widget without settling its Changed event.
                app.query_one("#athena-editor", TextArea).load_text(expected)
            else:
                await seed_athena_sql(
                    pilot, target.query, app.query_one("#athena-editor", TextArea), expected
                )
                if winner == "edit_back_to_empty":
                    expected = ""
                    await seed_athena_sql(
                        pilot, target.query, app.query_one("#athena-editor", TextArea), expected
                    )
            release.set()
            await asyncio.wait_for(switch, timeout=5)
            await drain_workers(app)
            await pilot.pause()
            assert len(candidates) == 1
            assert candidates[0]._disposed
            if winner == "settings":
                winning_vm = ctx.root_vm.content_host.current
                assert ctx.root_vm.content_host.current_id == "settings"
                assert app.query_one(SettingsView).vm is winning_vm
                assert not app.query(AthenaPage)
            else:
                assert ctx.root_vm.content_host.current is target
                assert ctx.root_vm.active_connection.name == "demo-prod"
                assert target.query.sql == expected
                assert app.query_one("#athena-editor", TextArea).text == expected
                assert app.query_one(AthenaPage).vm is target
                if expected_context is not None:
                    assert target.context == expected_context
        finally:
            release.set()
            await asyncio.gather(switch, return_exceptions=True)


async def test_latest_settled_edit_is_captured_after_outgoing_setup_drain(tmp_path, monkeypatch):
    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, source, _sql, pilot):
        host = ctx.root_vm.content_host
        drain = host._cancel_and_drain_setup
        mount = app._mount_service_view
        entered = asyncio.Event()
        release = asyncio.Event()
        first = True

        async def gated_drain():
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            await drain()

        async def failed_mount(service_id, *, required_connection=None):
            if required_connection.name == "demo-prod":
                return False
            return await mount(service_id, required_connection=required_connection)

        monkeypatch.setattr(host, "_cancel_and_drain_setup", gated_drain)
        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        switch = asyncio.create_task(app.action_swap_source())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            latest = "SELECT 'typed while setup drains Ω'\n"
            await seed_athena_sql(
                pilot, source.query, app.query_one("#athena-editor", TextArea), latest
            )
            release.set()
            await asyncio.wait_for(switch, timeout=5)
            await drain_workers(app)
            await pilot.pause()
            restored = host.current
            assert isinstance(restored, AthenaPageVM)
            assert restored is not source
            assert restored.query.sql == latest
            assert app.query_one("#athena-editor", TextArea).text == latest
        finally:
            release.set()
            await asyncio.gather(switch, return_exceptions=True)


async def test_repeated_cancellation_restores_after_outgoing_host_is_empty(tmp_path, monkeypatch):
    async with source_app(tmp_path, monkeypatch) as (app, ctx, service, source, sql, pilot):
        shutdown = source.shutdown
        build = service.build_recovery_vm
        shutting_down = asyncio.Event()
        release_shutdown = asyncio.Event()
        restoring = asyncio.Event()
        release_restore = asyncio.Event()
        candidates = []

        async def gated_shutdown():
            shutting_down.set()
            await release_shutdown.wait()
            await shutdown()

        def controlled_build(connection):
            recovery = build(connection)
            candidate = recovery.vm
            candidates.append(candidate)
            setup = candidate.setup

            async def gated_setup():
                assert ctx.root_vm.content_host.current is None
                restoring.set()
                await release_restore.wait()
                await setup()

            monkeypatch.setattr(candidate, "setup", gated_setup)
            return recovery

        monkeypatch.setattr(source, "shutdown", gated_shutdown)
        monkeypatch.setattr(service, "build_recovery_vm", controlled_build)
        switch = asyncio.create_task(app.action_swap_source())
        try:
            await asyncio.wait_for(shutting_down.wait(), timeout=3)
            switch.cancel()
            release_shutdown.set()
            await asyncio.wait_for(restoring.wait(), timeout=3)
            switch.cancel()
            await asyncio.sleep(0)
            assert not switch.done()
            release_restore.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(switch, timeout=5)
            await drain_workers(app)
            await pilot.pause()
            restored = ctx.root_vm.content_host.current
            assert restored is candidates[0]
            assert not restored._disposed
            assert restored.query.sql == sql
            assert ctx.root_vm.active_connection.name == "demo-dev"
            assert app.query_one(AthenaPage).vm is restored
            assert app.query_one("#athena-editor", TextArea).text == sql
        finally:
            release_shutdown.set()
            release_restore.set()
            await asyncio.gather(switch, return_exceptions=True)


@pytest.mark.parametrize(
    "boundary", ["shutdown_error", "rollback_mount_failure", "incomplete_context"]
)
async def test_rollback_retains_editor_through_recovery_failures(tmp_path, monkeypatch, boundary):
    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, source, sql, pilot):
        mount = app._mount_service_view
        if boundary == "shutdown_error":
            shutdown = source.shutdown
            first = True

            async def failed_shutdown():
                nonlocal first
                await shutdown()
                if first:
                    first = False
                    raise RuntimeError("controlled outgoing shutdown failure")

            monkeypatch.setattr(source, "shutdown", failed_shutdown)
        elif boundary == "incomplete_context":
            # A failed first detail read permits editor-only recovery.
            source._workgroup_detail = None

        async def failed_mount(service_id, *, required_connection=None):
            if required_connection.name == "demo-prod" or boundary == "rollback_mount_failure":
                return False
            return await mount(service_id, required_connection=required_connection)

        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        await app.action_swap_source()
        await drain_workers(app)
        await pilot.pause()
        restored = ctx.root_vm.content_host.current
        assert isinstance(restored, AthenaPageVM)
        assert restored is not source
        assert not restored._disposed
        assert restored.query.sql == sql
        assert ctx.root_vm.active_connection.name == "demo-dev"
        if boundary != "rollback_mount_failure":
            assert app.query_one(AthenaPage).vm is restored
            assert app.query_one("#athena-editor", TextArea).text == sql


async def test_cancellation_after_rollback_publication_keeps_and_mounts_adopted_page(
    tmp_path, monkeypatch
):
    from aws_tui.vm.messages import ConnectionChangedMessage

    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, source, sql, pilot):
        mount = app._mount_service_view
        published = []

        async def failed_mount(service_id, *, required_connection=None):
            if required_connection.name == "demo-prod":
                return False
            return await mount(service_id, required_connection=required_connection)

        def cancel_after_publication(message):
            if (
                isinstance(message, ConnectionChangedMessage)
                and message.connection.name == "demo-dev"
                and ctx.root_vm.content_host.current is not source
                and not published
            ):
                published.append(ctx.root_vm.content_host.current)
                raise asyncio.CancelledError

        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        subscription = ctx.hub.messages.subscribe(cancel_after_publication)
        try:
            with pytest.raises(asyncio.CancelledError):
                await app.action_swap_source()
        finally:
            subscription.dispose()
        await drain_workers(app)
        await pilot.pause()
        restored = ctx.root_vm.content_host.current
        assert len(published) == 1
        assert restored is published[0]
        assert not restored._disposed
        assert not restored._shutdown_started
        assert restored.query.sql == sql
        assert app.query_one(AthenaPage).vm is restored
        assert app.query_one("#athena-editor", TextArea).text == sql


@pytest.mark.parametrize("cancel_at", ["preflight", "cleanup"])
async def test_stale_rollback_cleanup_drains_before_disposal_despite_repeated_cancel(
    tmp_path, monkeypatch, cancel_at
):
    async with source_app(tmp_path, monkeypatch) as (app, ctx, service, _source, _sql, pilot):
        build = service.build_recovery_vm
        mount = app._mount_service_view
        preparing = asyncio.Event()
        release_prepare = asyncio.Event()
        closing = asyncio.Event()
        release_close = asyncio.Event()
        closed = asyncio.Event()
        candidates = []

        def controlled_build(connection):
            recovery = build(connection)
            candidate = recovery.vm
            candidates.append(candidate)
            setup, shutdown = candidate.setup, candidate.shutdown

            async def gated_setup():
                preparing.set()
                await release_prepare.wait()
                await setup()

            async def gated_shutdown():
                closing.set()
                await release_close.wait()
                await shutdown()
                closed.set()

            monkeypatch.setattr(candidate, "setup", gated_setup)
            monkeypatch.setattr(candidate, "shutdown", gated_shutdown)
            return recovery

        async def failed_mount(service_id, *, required_connection=None):
            if required_connection is not None and required_connection.name == "demo-prod":
                return False
            return await mount(service_id, required_connection=required_connection)

        monkeypatch.setattr(service, "build_recovery_vm", controlled_build)
        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        switch = asyncio.create_task(app.action_swap_source())
        try:
            await asyncio.wait_for(preparing.wait(), timeout=3)
            ctx.root_vm.services_menu.switch_service_command.execute("settings")
            await pilot.pause()
            if cancel_at == "preflight":
                switch.cancel()
            else:
                release_prepare.set()
            await asyncio.wait_for(closing.wait(), timeout=3)
            switch.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not candidates[0]._disposed
            assert not closed.is_set()
            release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(switch, timeout=5)
            assert closed.is_set()
            assert candidates[0]._disposed
            await drain_workers(app)
            assert ctx.root_vm.content_host.current_id == "settings"
        finally:
            release_prepare.set()
            release_close.set()
            await asyncio.gather(switch, return_exceptions=True)


async def test_new_navigation_during_target_shutdown_prevents_rollback_publication(
    tmp_path, monkeypatch
):
    from aws_tui.vm.messages import ConnectionChangedMessage

    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, _source, _sql, pilot):
        entered = asyncio.Event()
        release = asyncio.Event()
        mount = app._mount_service_view
        published = []

        async def failed_mount(service_id, *, required_connection=None):
            if required_connection is not None and required_connection.name == "demo-prod":
                target = ctx.root_vm.content_host.current
                shutdown = target.shutdown

                async def gated_shutdown():
                    entered.set()
                    await release.wait()
                    await shutdown()

                monkeypatch.setattr(target, "shutdown", gated_shutdown)
                return False
            return await mount(service_id, required_connection=required_connection)

        subscription = ctx.hub.messages.subscribe(
            lambda message: (
                published.append(message.connection.name)
                if isinstance(message, ConnectionChangedMessage)
                else None
            )
        )
        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        switch = asyncio.create_task(app.action_swap_source())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            ctx.root_vm.services_menu.switch_service_command.execute("settings")
            await pilot.pause()
            release.set()
            await asyncio.wait_for(switch, timeout=5)
            await drain_workers(app)
            assert ctx.root_vm.content_host.current_id == "settings"
            assert published == ["demo-prod"]
            assert not app.query(AthenaPage)
        finally:
            release.set()
            await asyncio.gather(switch, return_exceptions=True)
            subscription.dispose()


@pytest.mark.parametrize("boundary", ["unsafe_export", "queued_editor"])
async def test_source_rejects_teardown_when_editor_cannot_be_captured_safely(
    tmp_path, monkeypatch, boundary
):
    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, source, sql, pilot):
        if boundary == "unsafe_export":

            def reject_snapshot():
                raise ValueError("controlled unavailable editor export")

            monkeypatch.setattr(source, "export_editor_snapshot", reject_snapshot)
        else:
            drain = ctx.root_vm.content_host._cancel_and_drain_setup

            async def queued_editor_change():
                await drain()
                app.query_one("#athena-editor", TextArea).load_text("SELECT 'queued Ω'")

            monkeypatch.setattr(
                ctx.root_vm.content_host, "_cancel_and_drain_setup", queued_editor_change
            )
        await app.action_swap_source()
        await drain_workers(app)
        await pilot.pause()
        expected = sql if boundary == "unsafe_export" else "SELECT 'queued Ω'"
        assert ctx.root_vm.content_host.current is source
        assert ctx.root_vm.active_connection.name == "demo-dev"
        assert not source._disposed
        assert not source._shutdown_started
        assert not app.query_one(AthenaPage).is_disabled
        assert source.query.sql == expected
        assert app.query_one("#athena-editor", TextArea).text == expected


@pytest.mark.parametrize("route", ["keyboard", "header_picker"])
async def test_real_source_controls_restore_editor_after_mount_failure(
    tmp_path, monkeypatch, route
):
    from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
    from tests.helpers import focus_and_settle

    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, source, sql, pilot):
        mount = app._mount_service_view
        attempts = []

        async def failed_mount(service_id, *, required_connection=None):
            attempts.append(required_connection.name)
            if required_connection.name == "demo-prod":
                return False
            return await mount(service_id, required_connection=required_connection)

        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        picker = app.query_one(AthenaPage).query_one(ServiceSourceHeader).picker
        await focus_and_settle(picker)
        if route == "keyboard":
            await pilot.press("S")
        else:
            await pilot.press("enter", "down", "enter")
        await drain_workers(app)
        await pilot.pause()
        restored = ctx.root_vm.content_host.current
        assert attempts == ["demo-prod", "demo-dev"]
        assert isinstance(restored, AthenaPageVM)
        assert restored is not source
        assert ctx.root_vm.active_connection.name == "demo-dev"
        assert restored.query.sql == sql
        assert app.query_one("#athena-editor", TextArea).text == sql


@pytest.mark.parametrize("followup", ["refresh", "authenticate"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_unavailable_original_database_keeps_sql_and_blocks_execution(
    tmp_path, monkeypatch, enabled, followup
):
    async with source_app(tmp_path, monkeypatch) as (app, ctx, service, source, sql, pilot):
        if enabled:
            assert await ctx.athena_drafts_vm.set_enabled(True)
        client = source._client
        client.databases["primary", "AwsDataCatalog"].append("vanishing")
        await source.select_catalog("federated")
        await source.select_catalog("AwsDataCatalog")
        source.query.set_sql("")
        await source.select_database("vanishing")
        assert source.context.database == "vanishing"
        await seed_athena_sql(pilot, source.query, app.query_one("#athena-editor", TextArea), sql)
        provenance = source.export_editor_snapshot()
        assert provenance.editor.draft.bound_context.database == "vanishing"
        build = service.build_recovery_vm
        mount = app._mount_service_view

        def removed_database(connection):
            if "vanishing" in client.databases["primary", "AwsDataCatalog"]:
                client.databases["primary", "AwsDataCatalog"].remove("vanishing")
            return build(connection)

        async def failed_mount(service_id, *, required_connection=None):
            if required_connection.name == "demo-prod":
                return False
            return await mount(service_id, required_connection=required_connection)

        monkeypatch.setattr(service, "build_recovery_vm", removed_database)
        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        await app.action_swap_source()
        await drain_workers(app)
        await pilot.pause()
        restored = ctx.root_vm.content_host.current
        assert isinstance(restored, AthenaPageVM)
        assert restored.context.database != "vanishing"
        assert restored.query.sql == sql
        assert app.query_one("#athena-editor", TextArea).text == sql
        assert (
            restored.export_editor_snapshot().editor.draft.bound_context
            == provenance.editor.draft.bound_context
        )
        assert not restored.query.execute_command.can_execute()
        assert app.query_one("#athena-execute", Button).disabled
        assert restored.context.workgroup == ""
        assert restored.workgroup_detail_error_text is not None
        assert "Previous query context is unavailable" in str(
            app.query_one("#athena-query-detail-text", Static).content
        )
        await restored.query.execute()
        assert not client.start_calls
        if followup == "refresh":
            await app.action_refresh()
        else:
            await app.action_authenticate()
        await drain_workers(app)
        await pilot.pause()
        current = ctx.root_vm.content_host.current
        assert isinstance(current, AthenaPageVM)
        assert current.query.sql == sql
        assert current.context.workgroup == ""
        assert not current.query.execute_command.can_execute()
        assert app.query_one("#athena-execute", Button).disabled
        await current.query.execute()
        assert not client.start_calls


@pytest.mark.parametrize("enabled", [False, True])
async def test_edit_from_rollback_publication_observer_is_not_overwritten(
    tmp_path, monkeypatch, enabled
):
    from aws_tui.vm.messages import ConnectionChangedMessage

    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, source, _sql, pilot):
        if enabled:
            assert await ctx.athena_drafts_vm.set_enabled(True)
        mount = app._mount_service_view
        edited = []
        latest = "SELECT 'new publication observer Ω'"

        async def failed_mount(service_id, *, required_connection=None):
            if required_connection.name == "demo-prod":
                return False
            return await mount(service_id, required_connection=required_connection)

        def observer(message):
            if (
                isinstance(message, ConnectionChangedMessage)
                and message.connection.name == "demo-dev"
            ):
                current = ctx.root_vm.content_host.current
                if current is not source:
                    current.query.set_sql(latest)
                    edited.append(current)

        monkeypatch.setattr(app, "_mount_service_view", failed_mount)
        subscription = ctx.hub.messages.subscribe(observer)
        try:
            await app.action_swap_source()
            await drain_workers(app)
            await pilot.pause()
            restored = ctx.root_vm.content_host.current
            assert edited == [restored]
            assert restored.query.sql == latest
            assert app.query_one("#athena-editor", TextArea).text == latest
            assert app.query_one(AthenaPage).vm is restored
        finally:
            subscription.dispose()


async def test_new_navigation_during_credential_shutdown_prevents_stale_adoption(
    tmp_path, monkeypatch
):
    from aws_tui.vm.messages import ConnectionChangedMessage

    async with source_app(tmp_path, monkeypatch) as (app, ctx, _service, source, _sql, pilot):
        shutdown = source.shutdown
        entered = asyncio.Event()
        release = asyncio.Event()
        published = []

        async def gated_shutdown():
            await shutdown()
            entered.set()
            await release.wait()

        monkeypatch.setattr(source, "shutdown", gated_shutdown)
        subscription = ctx.hub.messages.subscribe(
            lambda message: (
                published.append(message) if isinstance(message, ConnectionChangedMessage) else None
            )
        )
        recovery = asyncio.create_task(app.action_authenticate())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            ctx.root_vm.services_menu.switch_service_command.execute("settings")
            await pilot.pause()
            release.set()
            await asyncio.wait_for(recovery, timeout=5)
            await drain_workers(app)
            assert ctx.root_vm.content_host.current_id == "settings"
            assert not published
        finally:
            release.set()
            await asyncio.gather(recovery, return_exceptions=True)
            subscription.dispose()
