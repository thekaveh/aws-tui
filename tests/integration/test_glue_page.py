from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from textual.widgets import OptionList

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.ui.widgets.glue.page import GluePage


async def wait_for_service_setup(ctx: AppContext, pilot: object) -> None:
    app = pilot.app  # type: ignore[attr-defined]
    await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


async def open_service(ctx: AppContext, pilot: object, service_id: str) -> None:
    app = pilot.app  # type: ignore[attr-defined]
    await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    ctx.root_vm.services_menu.switch_service_command.execute(service_id)
    await wait_for_service_setup(ctx, pilot)


def test_glue_service_is_registered_after_emr(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    try:
        ids = [service.descriptor.id for service in ctx.registry.all()]
        assert ids == ["s3", "emr-serverless", "glue"]
    finally:
        ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_glue_page_mounts_and_explicit_entry_focuses_catalog(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await open_service(ctx, pilot, "glue")

            page = app.query_one("#content-glue-page", GluePage)
            app.focus_active_service_pane()
            await pilot.pause()

            assert ctx.root_vm.content_host.current_id == "glue"
            assert page.has_focus_within
            assert app.focused is page.query_one("#glue-databases-pane").query_one(OptionList)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
