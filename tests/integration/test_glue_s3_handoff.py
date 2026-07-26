from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest
from textual.containers import Container

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
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
        uri="s3://private-bucket/events/?token=HANDOFF_SECRET",
        preferred_pane=preferred_pane,  # type: ignore[arg-type]
    )


def _assert_redacted_failure(ctx: AppContext, *, toast_id: str) -> None:
    toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
    assert toast.id == toast_id
    ctx.log_sink.flush()
    output = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
    assert "HANDOFF_SECRET" not in output
    assert "s3://private-bucket" not in output


def _assert_coherent_service(ctx: AppContext, service_id: str) -> None:
    assert ctx.root_vm.content_host.current_id == service_id
    assert ctx.root_vm.services_menu.selected_id == service_id
    assert ctx.root_vm.active_connection is not None
    assert ctx.root_vm.active_connection.name == "demo-dev"


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
            mount_results: list[bool] = []
            original_mount = host.mount
            original_mount_service_view = app._mount_service_view

            def fail_mount(*widgets: object, **kwargs: object) -> object:
                del widgets, kwargs
                raise RuntimeError(
                    "mount failed for s3://private-bucket/events/?token=HANDOFF_SECRET"
                )

            async def record_mount_result(service_id: str) -> bool:
                result = await original_mount_service_view(service_id)
                mount_results.append(result)
                return result

            monkeypatch.setattr(host, "mount", fail_mount)
            monkeypatch.setattr(app, "_mount_service_view", record_mount_result)
            await app._open_s3_location_request(_handoff_request())
            monkeypatch.setattr(host, "mount", original_mount)

            assert mount_results == [False]
            _assert_coherent_service(ctx, "s3")
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

            _assert_coherent_service(ctx, "s3")
            assert len(app.query(DualPane)) == 1
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

            _assert_coherent_service(ctx, "s3")
            assert len(app.query(DualPane)) == 1
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

            async def observed_mount(service_id: str) -> bool:
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
                    return await original_mount_service_view(service_id)
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
@pytest.mark.parametrize("location", [None, "", "https://user:pass@example.test/data?token=SECRET"])
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

            assert ctx.root_vm.content_host.current_id == "glue"
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "glue-s3-location-invalid"
            assert "SECRET" not in toast.text
            assert "user:pass" not in toast.text
            assert "example.test" not in toast.text
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
