"""Supported service jumps and exact contextual source discovery, using local clients."""

from pathlib import Path

import pytest
from textual.widgets import Input

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from tests.helpers import wait_until


async def _choose(pilot, app, ctx, label, entry_id=None):
    await pilot.press("ctrl+k")
    app.screen.query_one("#palette-input", Input).value = label
    await pilot.pause()
    rows = ctx.command_palette_vm.filtered_entries
    assert any(row.id == entry_id if entry_id else row.label == label for row in rows)
    chosen = next(row for row in rows if row.id == entry_id or row.label == label)
    await pilot.press("enter")
    return chosen.id


@pytest.mark.parametrize(
    ("service_id", "label", "page_type"),
    [
        ("athena", "Go to Athena", AthenaPage),
        ("glue", "Go to Glue", GluePage),
        ("emr-serverless", "Go to EMR Serverless", EmrServerlessPage),
        ("s3", "Go to S3", DualPane),
        ("settings", "Settings", SettingsView),
    ],
)
async def test_service_palette_adopts_nav_and_dom(tmp_path: Path, service_id, label, page_type):
    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _choose(
                pilot,
                app,
                ctx,
                label,
                "app.open_settings" if service_id == "settings" else f"service.open.{service_id}",
            )
            await wait_until(
                lambda: (
                    ctx.root_vm.content_host.current_id == service_id and bool(app.query(page_type))
                ),
                what=f"palette {service_id} adoption",
            )
            assert ctx.root_vm.services_menu.selected_id == service_id
            assert app.query_one(page_type).vm is ctx.root_vm.content_host.current
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("aws", {"s3", "athena", "glue", "emr-serverless"}), ("s3-compatible", {"s3"}), (None, set())],
)
async def test_service_capability_uses_active_connection_without_factories(
    tmp_path,
    monkeypatch,
    kind,
    expected,
):
    from aws_tui.infra.connection_resolver import Connection

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            connection = (
                Connection(name="configured", kind=kind, region="us-east-1", source="config")
                if kind
                else None
            )
            monkeypatch.setattr(ctx.root_vm, "_connection", connection)
            for service in ctx.registry.all():

                def forbidden(*args, **kwargs):
                    pytest.fail("discovery must not construct a service/client/provider")

                monkeypatch.setattr(service, "build_vm", forbidden)
                for factory in ("_client_factory", "_s3_fs_factory", "build_remote_provider"):
                    if hasattr(service, factory):
                        monkeypatch.setattr(service, factory, forbidden)
            monkeypatch.setattr(app, "_make_s3_provider_for_connection", forbidden)
            app._populate_command_palette()
            rows = ctx.command_palette_vm.filtered_entries
            assert {
                r.id.removeprefix("service.open.") for r in rows if r.id.startswith("service.open.")
            } == expected
            assert sum(r.id == "app.open_settings" for r in rows) == 1
            if "glue" not in expected:
                before = ctx.root_vm.services_menu.selected_id
                app._actions.invoke("service.open.glue")
                assert ctx.root_vm.services_menu.selected_id == before
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def _host(app, ctx, pilot, service_id):
    ctx.root_vm.services_menu.switch_service_command.execute(service_id)
    await app.workers.wait_for_complete(list(app.workers._workers))
    setup = ctx.root_vm.content_host._setup_task
    if setup is not None:
        await setup
    await pilot.pause()
    assert ctx.root_vm.content_host.current_id == service_id


def _multi_profile_glue_context(tmp_path):
    from tests.unit.vm.glue._fake_glue import seeded_glue

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    sources = [
        _connection("prod-west", "us-west-2"),
        _connection(),
        _connection("minio", "us-east-1", "s3-compatible"),
    ]
    ctx.connection_resolver.list = lambda: sources
    calls = []

    def build_client(connection):
        calls.append(connection.name)
        return seeded_glue()

    ctx.registry.get("glue")._client_factory = build_client
    return ctx, calls


async def test_source_palette_glue_exact_choice(tmp_path, monkeypatch):

    ctx, calls = _multi_profile_glue_context(tmp_path)
    app = AwsTuiApp(ctx)
    selected = []
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host(app, ctx, pilot, "glue")
            switch = app._switch_single_context_source_to

            async def trace(service_id, name, region, **kwargs):
                selected.append((service_id, name, region, kwargs))
                return await switch(service_id, name, region, **kwargs)

            monkeypatch.setattr(app, "_switch_single_context_source_to", trace)
            entry_id = await _choose(pilot, app, ctx, "Use dev · us-east-1 for Glue")
            await wait_until(
                lambda: (
                    ctx.root_vm.content_host.current.source.connection_key == ("dev", "us-east-1")
                    and bool(app.query(GluePage))
                    and app.query_one(GluePage).vm is ctx.root_vm.content_host.current
                ),
                what="exact Glue palette source adoption",
            )
            assert entry_id.startswith("source.choice.")
            assert selected == [("glue", "dev", "us-east-1", {"connection_kind": "aws"})]
            assert ctx.root_vm.active_connection.name == "dev"
            assert ctx.root_vm.services_menu.selected_id == "glue"
            assert calls == ["prod-west", "dev"]
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


def _connection(name="dev", region="us-east-1", kind="aws", **kwargs):
    from aws_tui.infra.connection_resolver import Connection

    return Connection(name=name, kind=kind, region=region, source="config", **kwargs)


def test_source_reconstruction_exact_tuple_ids_and_single_snapshot(tmp_path, monkeypatch):
    from aws_tui.app import _DiscoveryOrigin

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    sources = [_connection(), _connection(region="us-west-2"), _connection(kind="s3-compatible")]
    reads = []
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: reads.append(1) or sources)
    origin = _DiscoveryOrigin("s3", object(), None, FocusSlot.S3_LEFT, frozenset(), None)
    try:
        first = app._reconcile_discovery_sources(origin)
        assert len(reads) == 1
        assert len(first) == 3
        assert len({row.id for row in first}) == 3
        assert len({row.label for row in first}) == 3
        assert all(app._actions.has(row.id) and row.key_source == "unbound" for row in first)
        assert "(aws)" in first[0].label
        assert "(s3-compatible)" in first[2].label
        before = app._discovery_source_ids.copy()
        sources.reverse()
        sources.append(sources[0])
        assert len(app._reconcile_discovery_sources(origin)) == 3
        assert app._discovery_source_ids == before
        removed = sources.pop(1)
        removed_id = next(
            i
            for t, i in before.items()
            if t.region == removed.region and t.connection_kind == removed.kind
        )
        app._reconcile_discovery_sources(origin)
        assert not app._actions.has(removed_id)
        sources.append(removed)
        app._reconcile_discovery_sources(origin)
        new_id = next(
            i
            for t, i in app._discovery_source_ids.items()
            if t.region == removed.region and t.connection_kind == removed.kind
        )
        assert new_id != removed_id
        assert int(new_id.rsplit(".", 1)[1]) > 3
        assert removed_id not in app._discovery_source_targets
        # Only S3 filters its reachability observations.
        ctx.unreachable_connections.add(("aws", "dev"))
        s3_rows = app._reconcile_discovery_sources(origin)
        assert len(s3_rows) == 1
        glue_origin = _DiscoveryOrigin("glue", object(), None, FocusSlot.S3_LEFT, frozenset(), None)
        assert len(app._reconcile_discovery_sources(glue_origin)) == 2
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_open_close_reorder_retains_registry_and_vm_children(tmp_path, monkeypatch):
    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    sources = [_connection(), _connection("prod-west", "us-west-2")]
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: sources)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            app.set_focus(None)
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.pause()
            retained = app._discovery_source_ids.copy()
            assert all(entry_id in ctx.command_palette_vm._items for entry_id in retained.values())
            actions = len(app._actions.known_actions())
            children = tuple(ctx.command_palette_vm._inner_registry)
            for _ in range(3):
                await pilot.press("escape")
                sources.reverse()
                await pilot.press("ctrl+k")
                await pilot.pause()
                assert app._discovery_source_ids == retained
                assert len(app._actions.known_actions()) == actions
                assert tuple(ctx.command_palette_vm._inner_registry) == children
                assert (
                    sum(
                        r.id == "app.open_settings" for r in ctx.command_palette_vm.filtered_entries
                    )
                    == 1
                )
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("mutation", ["remove", "kind", "region"])
async def test_source_stale_row_refuses_mutated_target_and_reopen_removes_child(
    tmp_path,
    monkeypatch,
    mutation,
):
    from dataclasses import replace

    from aws_tui.demo.in_memory_fs import InMemoryFS

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    sources = [_connection("exact", "us-west-2")]
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: sources)
    app = AwsTuiApp(ctx)
    calls = []
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            app.set_focus(None)
            await pilot.pause()
            monkeypatch.setattr(
                app, "_make_s3_provider_for_connection", lambda c: calls.append(c) or InMemoryFS()
            )
            await pilot.press("ctrl+k")
            app.screen.query_one(Input).value = "Use exact · us-west-2 for S3"
            await pilot.pause()
            assert ctx.command_palette_vm.filtered_entries
            assert (
                ctx.command_palette_vm.filtered_entries[0].label == "Use exact · us-west-2 for S3"
            )
            old = ctx.command_palette_vm.filtered_entries[0].id
            child = ctx.command_palette_vm._items[old].inner
            if mutation == "remove":
                sources.clear()
            else:
                sources[:] = [
                    replace(
                        sources[0],
                        **{mutation: "s3-compatible" if mutation == "kind" else "eu-west-1"},
                    )
                ]
            await pilot.press("enter")
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks, what="stale source action drain"
            )
            assert calls == []
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert not app._actions.has(old)
            assert old not in ctx.command_palette_vm._items
            assert child not in ctx.command_palette_vm._inner_registry
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("kind", ["aws", "s3-compatible"])
async def test_source_s3_same_name_region_selects_exact_kind_only_focused_pane(
    tmp_path,
    monkeypatch,
    kind,
):
    from aws_tui.demo.in_memory_fs import InMemoryFS
    from aws_tui.ui.widgets.pane import Pane

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    sources = [
        _connection("same", "us-west-2", "aws"),
        _connection("same", "us-west-2", "s3-compatible"),
    ]
    app = AwsTuiApp(ctx)
    calls = []
    provider = InMemoryFS()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            monkeypatch.setattr(ctx.connection_resolver, "list", lambda: sources)
            dual = ctx.root_vm.content_host.current
            right = dual.right
            old = (
                right.provider,
                right.identity_label,
                right.path,
                right.current_connection_key,
                right.transfer_connection,
            )
            root_connection = ctx.root_vm.active_connection
            # The left pane deliberately exercises non-default focused-pane adoption.
            app.query_one("#pane-left", Pane).focus()
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_LEFT)
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "_make_s3_provider_for_connection",
                lambda c: calls.append((c.kind, c.name, c.region)) or provider,
            )
            await _choose(pilot, app, ctx, f"Use same ({kind}) · us-west-2 for S3")
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks, what="S3 source action drain"
            )
            assert calls == [(kind, "same", "us-west-2")]
            assert dual.left.provider is provider
            assert dual.left.current_connection_key == (kind, "same")
            assert dual.left.transfer_connection.kind == kind
            assert dual.left.transfer_connection.name == "same"
            assert (
                right.provider,
                right.identity_label,
                right.path,
                right.current_connection_key,
                right.transfer_connection,
            ) == old
            assert ctx.root_vm.active_connection is root_connection
            assert app.query_one(DualPane).vm is dual
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_s3_provider_refusal_retains_both_panes_and_root(tmp_path, monkeypatch):
    from aws_tui.domain.filesystem import AuthRequiredError

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    sources = [_connection("minio", "us-west-2", "s3-compatible")]
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: sources)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            dual = ctx.root_vm.content_host.current
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            app.set_focus(None)
            await pilot.pause()
            old = [
                (
                    p.provider,
                    p.identity_label,
                    p.path,
                    p.current_connection_key,
                    p.transfer_connection,
                )
                for p in (dual.left, dual.right)
            ]
            root_connection = ctx.root_vm.active_connection

            def refuse(connection):
                raise AuthRequiredError("fake credentials refused")

            monkeypatch.setattr(app, "_make_s3_provider_for_connection", refuse)
            await _choose(pilot, app, ctx, "Use minio · us-west-2 for S3")
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks, what="provider refusal drain"
            )
            assert [
                (
                    p.provider,
                    p.identity_label,
                    p.path,
                    p.current_connection_key,
                    p.transfer_connection,
                )
                for p in (dual.left, dual.right)
            ] == old
            assert ctx.root_vm.active_connection is root_connection
            assert any(
                t.model.id == "swap-source-auth-required"
                for t in ctx.root_vm.chrome.toast_stack.toasts
            )
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_resolver_config_error_keeps_global_commands_and_toast(tmp_path, monkeypatch):
    from aws_tui.infra.config_store import ConfigError

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()

            def fail():
                raise ConfigError("local invalid config")

            monkeypatch.setattr(ctx.connection_resolver, "list", fail)
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert not any(
                r.id.startswith("source.choice.") for r in ctx.command_palette_vm.filtered_entries
            )
            assert any(r.id == "app.open_settings" for r in ctx.command_palette_vm.filtered_entries)
            assert any(
                t.model.id == "connection-discovery-failed"
                for t in ctx.root_vm.chrome.toast_stack.toasts
            )
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_failed_aws_adoption_restores_current_header_picker_and_dom(
    tmp_path, monkeypatch
):
    from textual.widgets import Static

    from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader

    ctx, _ = _multi_profile_glue_context(tmp_path)
    app = AwsTuiApp(ctx)
    restores = []
    restore = ServiceSourceHeader.restore_source

    def spy_restore(header):
        restores.append(header)
        restore(header)

    monkeypatch.setattr(ServiceSourceHeader, "restore_source", spy_restore)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host(app, ctx, pilot, "glue")
            prior = ctx.root_vm.active_connection
            mount = app._mount_service_view

            async def refuse(service_id, *, required_connection=None):
                if required_connection.name == "dev":
                    return False
                return await mount(service_id, required_connection=required_connection)

            monkeypatch.setattr(app, "_mount_service_view", refuse)
            await _choose(pilot, app, ctx, "Use dev · us-east-1 for Glue")
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks,
                what="failed AWS palette adoption rollback",
            )
            page = app.query_one(GluePage)
            header = page.query_one(ServiceSourceHeader)
            assert header in restores
            assert header.is_attached
            assert header.picker.value == header._active_value()
            rendered = str(header.picker.query_one(".context-picker-value", Static).render())
            assert prior.name in rendered
            assert prior.region in rendered
            assert ctx.root_vm.active_connection == prior
            assert page.vm is ctx.root_vm.content_host.current
            assert page.vm.source.connection_key == (prior.name, prior.region)
            assert ctx.root_vm.services_menu.selected_id == "glue"
            assert len(app.query(GluePage)) == 1
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.parametrize("newer", ["settings", "athena"])
async def test_source_suspended_adoption_superseded_by_navigation(tmp_path, monkeypatch, newer):
    import asyncio

    ctx, _ = _multi_profile_glue_context(tmp_path)
    app = AwsTuiApp(ctx)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _host(app, ctx, pilot, "glue")
            mount = app._mount_service_view

            async def suspend(service_id, *, required_connection=None):
                if required_connection is not None and required_connection.name == "dev":
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                return await mount(service_id, required_connection=required_connection)

            monkeypatch.setattr(app, "_mount_service_view", suspend)
            await _choose(pilot, app, ctx, "Use dev · us-east-1 for Glue")
            await entered.wait()
            ctx.root_vm.services_menu.switch_service_command.execute(newer)
            try:
                await wait_until(
                    lambda: cancelled.is_set(), what="newer navigation cancels source adoption"
                )
            finally:
                release.set()
            await wait_until(
                lambda: (
                    ctx.root_vm.content_host.current_id == newer
                    and not ctx.command_palette_vm._pending_tasks
                ),
                what="superseded source action drain",
            )
            await app.workers.wait_for_complete(list(app.workers._workers))
            page_type = SettingsView if newer == "settings" else AthenaPage
            assert app.query_one(page_type).vm is ctx.root_vm.content_host.current
            assert ctx.root_vm.services_menu.selected_id == newer
            assert not app.query(GluePage)
    finally:
        release.set()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_shutdown_cancels_owned_action_before_lock_drain(tmp_path, monkeypatch):
    import asyncio

    ctx, _ = _multi_profile_glue_context(tmp_path)
    app = AwsTuiApp(ctx)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    shutdown = None
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await _host(app, ctx, pilot, "glue")
            mount = app._mount_service_view

            async def suspend(service_id, *, required_connection=None):
                if required_connection is not None and required_connection.name == "dev":
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                return await mount(service_id, required_connection=required_connection)

            monkeypatch.setattr(app, "_mount_service_view", suspend)
            await _choose(pilot, app, ctx, "Use dev · us-east-1 for Glue")
            await entered.wait()
            shutdown = asyncio.create_task(app._aws_tui_shutdown())
            try:
                await wait_until(
                    lambda: cancelled.is_set(), what="shutdown cancels source lock owner"
                )
            finally:
                release.set()
            await shutdown
            assert not ctx.command_palette_vm._pending_tasks
            assert not app._service_navigation_lock.locked()
            assert app._shutdown_complete
            old_current = ctx.root_vm.content_host.current
            release.set()
            await pilot.pause()
            assert ctx.root_vm.content_host.current is old_current
    finally:
        release.set()
        if shutdown is not None:
            await shutdown
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_literal_display_and_exact_raw_selection(tmp_path, monkeypatch):
    from aws_tui.demo.in_memory_fs import InMemoryFS
    from aws_tui.ui.widgets.command_palette import CommandPaletteItem
    from aws_tui.vm.chrome.action_catalog import literal_display

    name = '[bold] Unicode 雪 "quote" / · [/]\x1b\n'
    region = 'region/·"\t'
    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    sources = [_connection(name, region, "s3-compatible")]
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: sources)
    app = AwsTuiApp(ctx)
    seen = []
    try:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            app.set_focus(None)
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "_make_s3_provider_for_connection",
                lambda c: seen.append((c.name, c.region)) or InMemoryFS(),
            )
            await pilot.press("ctrl+k")
            app.screen.query_one(Input).value = "Unicode 雪"
            await pilot.pause()
            row = next(
                r
                for r in app.screen.query(CommandPaletteItem)
                if r.action_id.startswith("source.choice.")
            )
            label = f"Use {name} · {region} for S3"
            assert row.presentation.label == label
            assert literal_display(label) in str(row.render())
            assert "[bold]" in str(row.render())
            assert "[/]" in str(row.render())
            assert "\\x1b" in str(row.render())
            assert "\\n" in str(row.render())
            assert "\\t" in str(row.render())
            assert "\x1b" not in str(row.render())
            assert "\n" not in str(row.render())
            await pilot.press("enter")
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks, what="literal source selection"
            )
            assert seen == [(name, region)]
            from aws_tui.ui.widgets.help_modal import HelpActionRow

            await pilot.press("question_mark")
            await pilot.pause()
            help_row = next(
                r for r in app.screen.query(HelpActionRow) if r.action_id == row.action_id
            )
            assert help_row.presentation.label == label
            assert literal_display(label) in str(help_row.render())
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_failure_payload_and_new_logs_are_value_free(tmp_path, monkeypatch):
    import asyncio
    import dataclasses
    import json

    from aws_tui.vm.messages import PaletteActionFailedMessage

    values = [
        "NAME_SENTINEL_245",
        "REGION_SENTINEL_245",
        "ACCESS_SENTINEL_245",
        "SECRET_SENTINEL_245",
        "TOKEN_SENTINEL_245",
        "https://ENDPOINT_SENTINEL_245.invalid",
    ]
    source = _connection(
        values[0],
        values[1],
        "s3-compatible",
        access_key_id=values[2],
        secret_access_key=values[3],
        session_token=values[4],
        endpoint_url=values[5],
    )
    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: [source])
    app = AwsTuiApp(ctx)
    messages, logs = [], []
    subscription = ctx.hub.messages.subscribe(
        lambda m: messages.append(m) if isinstance(m, PaletteActionFailedMessage) else None
    )
    warning = ctx.log_sink.warning

    def capture(event, **fields):
        if event.startswith("discovery."):
            logs.append((event, fields))
        warning(event, **fields)

    monkeypatch.setattr(ctx.log_sink, "warning", capture)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            app.set_focus(None)
            await pilot.pause()

            def fail(connection):
                raise RuntimeError(" ".join(values))

            monkeypatch.setattr(app, "_make_s3_provider_for_connection", fail)
            entry_id = await _choose(pilot, app, ctx, f"Use {values[0]} · {values[1]} for S3")
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks,
                what="private provider failure drain",
            )
            assert logs == [
                ("discovery.source.failed", {"service_id": "s3", "error_type": "RuntimeError"})
            ]

            # Unexpected callback failures still use the palette's existing safe payload.
            async def unexpected(*args, **kwargs):
                raise ValueError(" ".join(values))

            monkeypatch.setattr(app, "_switch_single_context_source_to", unexpected)
            assert (
                await _choose(pilot, app, ctx, f"Use {values[0]} · {values[1]} for S3") == entry_id
            )
            await wait_until(lambda: bool(messages), what="opaque palette failure payload")
            serialized = json.dumps([dataclasses.asdict(m) for m in messages])
            assert entry_id in serialized
            assert "ValueError" in serialized
            identifiers = (
                json.dumps(app._actions.known_actions())
                + json.dumps([child.name for child in ctx.command_palette_vm._inner_registry])
                + json.dumps([task.get_name() for task in asyncio.all_tasks()])
            )
            assert all(value not in serialized + json.dumps(logs) + identifiers for value in values)
            assert any(
                r.label == f"Use {values[0]} · {values[1]} for S3"
                for r in ctx.command_palette_vm.filtered_entries
            )
    finally:
        subscription.dispose()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_same_exact_s3_choice_is_idempotent_and_preserves_path(tmp_path, monkeypatch):
    from aws_tui.demo.in_memory_fs import InMemoryFS
    from aws_tui.domain.filesystem import PathRef

    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    source = _connection("chosen", "us-west-2")
    monkeypatch.setattr(ctx.connection_resolver, "list", lambda: [source])
    app = AwsTuiApp(ctx)
    provider = InMemoryFS()
    await provider.mkdir(PathRef(("folder",)))
    calls = []
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            app.set_focus(None)
            await pilot.pause()
            monkeypatch.setattr(
                app, "_make_s3_provider_for_connection", lambda c: calls.append(c) or provider
            )
            entry_id = await _choose(pilot, app, ctx, "Use chosen · us-west-2 for S3")
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks, what="first S3 source choice"
            )
            pane = ctx.root_vm.content_host.current.right
            await pane.navigate_to(PathRef(("folder",)))
            assert await _choose(pilot, app, ctx, "Use chosen · us-west-2 for S3") == entry_id
            await wait_until(
                lambda: not ctx.command_palette_vm._pending_tasks,
                what="idempotent S3 source choice",
            )
            assert calls == [source]
            assert pane.path == PathRef(("folder",))
            assert pane.provider is provider
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_projection_replaced_origin_omits_choices_and_retained_handler_is_inert(
    tmp_path, monkeypatch
):
    ctx, _ = _multi_profile_glue_context(tmp_path)
    app = AwsTuiApp(ctx)
    calls = []
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await _host(app, ctx, pilot, "glue")
            origin = app._capture_discovery_origin()
            before = app._project_discovery_actions(origin)
            row = next(r for r in before if r.id.startswith("source.choice."))
            handler = app._actions._handlers[row.id]
            await _host(app, ctx, pilot, "settings")
            projected = app._project_discovery_actions(origin)
            assert all(not r.available for r in projected if r.id.startswith("source.choice."))

            async def forbidden(*args, **kwargs):
                calls.append((args, kwargs))
                return True

            monkeypatch.setattr(app, "_switch_single_context_source_to", forbidden)
            await handler()
            assert calls == []
            assert app.query_one(SettingsView).vm is ctx.root_vm.content_host.current
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()


async def test_source_s3_missing_captured_pane_omits_inert_choices(tmp_path):
    ctx = build_app_context(config_dir=tmp_path, demo=True, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            ctx.focus_coordinator.set_focused_slot(FocusSlot.NAV_MENU)
            app.set_focus(None)
            await pilot.pause()
            rows = app._project_discovery_actions(app._capture_discovery_origin())
            sources = [r for r in rows if r.id.startswith("source.choice.")]
            assert sources
            assert all(r.availability_reason == "focus_required" for r in sources)
            ctx.focus_coordinator.set_focused_slot(FocusSlot.S3_RIGHT)
            rows = app._project_discovery_actions(app._capture_discovery_origin())
            assert all(r.available for r in rows if r.id.startswith("source.choice."))
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()
