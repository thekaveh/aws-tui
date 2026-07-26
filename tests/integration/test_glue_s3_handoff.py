from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest
from textual.containers import Container
from textual.worker import WorkerCancelled

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo import seeds
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import OpenS3LocationRequest


async def _wait_for_service_setup(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> None:
    await app.workers.wait_for_complete(list(app.workers._workers))
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


async def _wait_for_service_setup_after_supersession(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> None:
    outcomes = await asyncio.gather(
        *(worker.wait() for worker in list(app.workers._workers)),
        return_exceptions=True,
    )
    assert any(isinstance(outcome, WorkerCancelled) for outcome in outcomes)
    unexpected = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, BaseException) and not isinstance(outcome, WorkerCancelled)
    ]
    assert unexpected == []
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


async def _open_demo_glue(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> GluePageVM:
    await _wait_for_service_setup(ctx, app, pilot)
    ctx.root_vm.services_menu.switch_service_command.execute("glue")
    await _wait_for_service_setup(ctx, app, pilot)
    vm = ctx.root_vm.content_host.current
    assert isinstance(vm, GluePageVM)
    assert vm.catalog.table_detail is not None
    return vm


async def _invoke_open_s3(app: AwsTuiApp) -> None:
    result = app.action_dispatch("glue.open_s3_location")
    if inspect.isawaitable(result):
        await result


def _handoff_request(*, preferred_pane: str = "left") -> OpenS3LocationRequest:
    return OpenS3LocationRequest(
        connection_name="demo-dev",
        region="us-east-1",
        uri="s3://demo-dev/dev_analytics/dev_events/",
        preferred_pane=preferred_pane,  # type: ignore[arg-type]
    )


def _assert_redacted_failure(ctx: AppContext, *, toast_id: str) -> None:
    toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
    assert toast.id == toast_id
    ctx.log_sink.flush()
    output = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
    assert "HANDOFF_SECRET" not in output
    assert "s3://demo-dev" not in output


def _assert_coherent_service(ctx: AppContext, service_id: str) -> None:
    assert ctx.root_vm.content_host.current_id == service_id
    assert ctx.root_vm.services_menu.selected_id == service_id
    assert ctx.root_vm.active_connection is not None
    assert ctx.root_vm.active_connection.name == "demo-dev"


def _assert_visible_glue(ctx: AppContext, app: AwsTuiApp) -> None:
    _assert_coherent_service(ctx, "glue")
    current = ctx.root_vm.content_host.current
    assert isinstance(current, GluePageVM)
    host = app.query_one("#content-host", Container)
    assert len(host.children) > 0
    pages = list(host.query(GluePage))
    assert len(pages) == 1
    assert pages[0].vm is current
    assert len(host.query(DualPane)) == 0


def _assert_visible_s3(ctx: AppContext, app: AwsTuiApp) -> DualPaneVM:
    _assert_coherent_service(ctx, "s3")
    current = ctx.root_vm.content_host.current
    assert isinstance(current, DualPaneVM)
    host = app.query_one("#content-host", Container)
    assert len(host.children) > 0
    panes = list(host.query(DualPane))
    assert len(panes) == 1
    assert panes[0].vm is current
    assert current.left.current_connection_key == ("aws", "demo-dev")
    return current


def _assert_visible_emr(ctx: AppContext, app: AwsTuiApp) -> None:
    _assert_coherent_service(ctx, "emr-serverless")
    current = ctx.root_vm.content_host.current
    assert isinstance(current, EmrServerlessPageVM)
    assert current.source.connection_name == "demo-dev"
    assert current.source.profile == "demo-dev"
    assert current.source.region == "us-east-1"
    host = app.query_one("#content-host", Container)
    assert len(host.children) > 0
    child_ids = [child.id for child in host.children if child.id is not None]
    assert len(child_ids) == len(set(child_ids))
    pages = list(host.query(EmrServerlessPage))
    assert len(pages) == 1
    assert pages[0]._vm is current
    assert len(host.query(GluePage)) == 0
    assert len(host.query(DualPane)) == 0


@pytest.mark.asyncio
async def test_glue_table_location_opens_same_profile_in_s3(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            vm = await _open_demo_glue(ctx, app, pilot)
            detail = vm.catalog.table_detail
            assert detail is not None
            source_ref = detail.summary.ref

            await _invoke_open_s3(app)
            await _wait_for_service_setup(ctx, app, pilot)

            assert ctx.root_vm.content_host.current_id == "s3"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == source_ref.connection_name
            pane = ctx.root_vm.content_host.current.left
            assert pane.current_connection_key == ("aws", source_ref.connection_name)
            assert pane.path.as_posix() == "/demo-dev/dev_analytics/dev_events"
            assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_s3_handoff_ignores_stale_fallback_retry_and_keeps_exact_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)
            stale_retry = ctx.connection_resolver.resolve("demo-prod")
            app._chain_resolved_to_local = True
            app._chain_initial_conn = stale_retry
            retries: list[str] = []

            async def switch_to_stale_retry(connection: Connection) -> str:
                retries.append(connection.name)
                await ctx.root_vm.switch_connection_and_service(
                    connection,
                    TokenState.CONNECTED,
                    "s3",
                )
                await app._mount_initial_service_view()
                return "ok"

            monkeypatch.setattr(app, "_try_connection", switch_to_stale_retry)
            await app._open_s3_location_request(_handoff_request())
            await _wait_for_service_setup(ctx, app, pilot)

            assert retries == []
            dual = _assert_visible_s3(ctx, app)
            mounted = app.query_one("#content-dual-pane", DualPane)
            assert mounted.vm is dual
            assert dual.left.current_connection_key == ("aws", "demo-dev")
            assert dual.left.path.as_posix() == ("/demo-dev/dev_analytics/dev_events")
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_s3_handoff_mount_failure_is_reported_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)
            host = app.query_one("#content-host", Container)
            mount_results: list[tuple[str, bool]] = []
            original_mount = host.mount
            original_mount_service_view = app._mount_service_view

            def fail_mount(*widgets: object, **kwargs: object) -> object:
                if any(isinstance(widget, DualPane) for widget in widgets):
                    raise RuntimeError(
                        "mount failed for s3://private-bucket/events/?token=HANDOFF_SECRET"
                    )
                return original_mount(*widgets, **kwargs)  # type: ignore[arg-type]

            async def record_mount_result(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                result = await original_mount_service_view(
                    service_id,
                    required_connection=required_connection,
                )
                mount_results.append((service_id, result))
                return result

            monkeypatch.setattr(host, "mount", fail_mount)
            monkeypatch.setattr(app, "_mount_service_view", record_mount_result)
            await app._open_s3_location_request(_handoff_request())
            monkeypatch.setattr(host, "mount", original_mount)
            await _wait_for_service_setup(ctx, app, pilot)

            assert mount_results == [("s3", False), ("glue", True)]
            _assert_visible_glue(ctx, app)
            _assert_redacted_failure(ctx, toast_id="s3-handoff-mount-failed")
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_s3_handoff_bind_failure_is_contained_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)

            async def fail_bind(pane: PaneVM, connection: object) -> None:
                del pane, connection
                raise RuntimeError(
                    "bind failed for s3://private-bucket/events/?token=HANDOFF_SECRET"
                )

            monkeypatch.setattr(app, "_rebind_pane_to_connection", fail_bind)
            await app._open_s3_location_request(_handoff_request(preferred_pane="right"))

            _assert_visible_glue(ctx, app)
            _assert_redacted_failure(ctx, toast_id="s3-handoff-bind-failed")
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_s3_handoff_navigation_failure_is_contained_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)

            async def fail_navigation(self: PaneVM, path: object) -> None:
                del self, path
                raise RuntimeError(
                    "navigate failed for s3://private-bucket/events/?token=HANDOFF_SECRET"
                )

            monkeypatch.setattr(PaneVM, "navigate_to", fail_navigation)
            await app._open_s3_location_request(_handoff_request())

            _assert_visible_glue(ctx, app)
            _assert_redacted_failure(ctx, toast_id="s3-handoff-navigation-failed")
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_overlapping_handoff_and_nav_mounts_are_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)
            first_mount_started = asyncio.Event()
            second_mount_started = asyncio.Event()
            release_first_mount = asyncio.Event()
            original_mount_service_view = app._mount_service_view
            calls: list[str] = []
            active_mounts = 0
            max_active_mounts = 0

            async def observed_mount(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                nonlocal active_mounts, max_active_mounts
                active_mounts += 1
                max_active_mounts = max(max_active_mounts, active_mounts)
                calls.append(service_id)
                try:
                    if len(calls) == 1:
                        first_mount_started.set()
                        await release_first_mount.wait()
                    else:
                        second_mount_started.set()
                    return await original_mount_service_view(
                        service_id,
                        required_connection=required_connection,
                    )
                finally:
                    active_mounts -= 1

            monkeypatch.setattr(app, "_mount_service_view", observed_mount)
            ctx.hub.send(_handoff_request())
            await asyncio.wait_for(first_mount_started.wait(), timeout=2)

            ctx.root_vm.services_menu.switch_service_command.execute("glue")
            await asyncio.wait_for(second_mount_started.wait(), timeout=2)
            release_first_mount.set()
            await _wait_for_service_setup(ctx, app, pilot)

            assert calls == ["s3", "glue"]
            assert max_active_mounts == 1
            _assert_coherent_service(ctx, "glue")
            host = app.query_one("#content-host", Container)
            child_ids = [child.id for child in host.children if child.id is not None]
            assert len(child_ids) == len(set(child_ids))
            assert len(app.query(GluePage)) == 1
            assert len(app.query(DualPane)) == 0
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_user_emr_navigation_supersedes_handoff_paused_during_root_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)
            switch_started = asyncio.Event()
            release_switch = asyncio.Event()
            original_switch = ctx.root_vm.switch_connection_and_service

            async def pause_handoff_switch(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
            ) -> None:
                if service_id == "s3":
                    switch_started.set()
                    await release_switch.wait()
                await original_switch(connection, auth_state, service_id)

            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                pause_handoff_switch,
            )
            ctx.hub.send(_handoff_request())
            await asyncio.wait_for(switch_started.wait(), timeout=2)

            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            release_switch.set()
            await _wait_for_service_setup_after_supersession(ctx, app, pilot)

            _assert_visible_emr(ctx, app)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_user_emr_navigation_supersedes_handoff_paused_during_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)
            rollback_started = asyncio.Event()
            release_rollback = asyncio.Event()
            original_switch = ctx.root_vm.switch_connection_and_service
            original_mount = app._mount_service_view

            async def pause_rollback_switch(
                connection: Connection,
                auth_state: TokenState,
                service_id: str,
            ) -> None:
                if service_id == "glue":
                    rollback_started.set()
                    await release_rollback.wait()
                await original_switch(connection, auth_state, service_id)

            async def fail_handoff_mount(
                service_id: str,
                *,
                required_connection: Connection | None = None,
            ) -> bool:
                if service_id == "s3" and required_connection is not None:
                    return False
                return await original_mount(
                    service_id,
                    required_connection=required_connection,
                )

            monkeypatch.setattr(
                ctx.root_vm,
                "switch_connection_and_service",
                pause_rollback_switch,
            )
            monkeypatch.setattr(app, "_mount_service_view", fail_handoff_mount)
            ctx.hub.send(_handoff_request())
            await asyncio.wait_for(rollback_started.wait(), timeout=2)

            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            release_rollback.set()
            await _wait_for_service_setup_after_supersession(ctx, app, pilot)

            _assert_visible_emr(ctx, app)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_s3_handoff_rejects_region_mismatch_without_substitution(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_demo_glue(ctx, app, pilot)

            ctx.hub.send(
                OpenS3LocationRequest(
                    connection_name="demo-dev",
                    region="us-west-2",
                    uri="s3://private-bucket/events/?token=HANDOFF_SECRET",
                    preferred_pane="right",
                )
            )
            await _wait_for_service_setup(ctx, app, pilot)

            assert ctx.root_vm.content_host.current_id == "glue"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "s3-handoff-region-mismatch"
            assert "HANDOFF_SECRET" not in toast.text
            assert "s3://private-bucket" not in toast.text
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param(
            "https://user:pass@example.test/data?token=GLUE_URI_SECRET",
            id="unsupported-scheme",
        ),
        pytest.param("s3://[GLUE_URI_SECRET", id="malformed-authority"),
        pytest.param("s3://valid-bucket:443/GLUE_URI_SECRET", id="port"),
        pytest.param("s3://valid-bucket/prefix?GLUE_URI_SECRET=1", id="query"),
        pytest.param("s3://valid-bucket/raw\x00GLUE_URI_SECRET", id="raw-control"),
        pytest.param("s3://valid-bucket/%0AGLUE_URI_SECRET", id="encoded-control"),
    ],
)
async def test_missing_or_malformed_glue_location_is_advisory_and_redacted(
    tmp_path: Path,
    location: str | None,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            vm = await _open_demo_glue(ctx, app, pilot)
            detail = vm.catalog.table_detail
            assert detail is not None
            vm.catalog._table_detail = replace(  # type: ignore[attr-defined]
                detail,
                storage=replace(detail.storage, location=location),
            )

            await _invoke_open_s3(app)
            await _wait_for_service_setup(ctx, app, pilot)

            _assert_visible_glue(ctx, app)
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "glue-s3-location-invalid"
            assert "selected table has no valid S3 location" in toast.text
            ctx.log_sink.flush()
            diagnostic = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
            assert "GLUE_URI_SECRET" not in diagnostic
            assert "user:pass" not in diagnostic
            assert "example.test" not in diagnostic
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_valid_dotted_hyphenated_glue_prefix_opens_as_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = "s3://warehouse.prod-2026/events-data/prefix/"
    monkeypatch.setitem(
        seeds._PROFILE_OBJECTS,  # type: ignore[attr-defined]
        "demo-dev",
        (
            *seeds._PROFILE_OBJECTS["demo-dev"],  # type: ignore[attr-defined]
            ("warehouse.prod-2026/events-data/prefix/part-0000.parquet", 128),
        ),
    )
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            vm = await _open_demo_glue(ctx, app, pilot)
            detail = vm.catalog.table_detail
            assert detail is not None
            vm.catalog._table_detail = replace(  # type: ignore[attr-defined]
                detail,
                storage=replace(detail.storage, location=location),
            )

            await _invoke_open_s3(app)
            await _wait_for_service_setup(ctx, app, pilot)

            dual = _assert_visible_s3(ctx, app)
            assert dual.left.path.as_posix() == "/warehouse.prod-2026/events-data/prefix"
            assert dual.left.selected_entry is not None
            assert dual.left.selected_entry.entry.name == ".."
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
