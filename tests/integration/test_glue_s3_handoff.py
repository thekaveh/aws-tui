from __future__ import annotations

import contextlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
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
