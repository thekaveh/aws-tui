"""A queued source-picker commit must not occupy the App message pump."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aws_tui.composition import build_app_context
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from tests.helpers import focus_and_settle, wait_until
from tests.integration.test_glue_athena_navigation import _open_service, _wait_for_service_setup
from tests.integration.test_s3_action_responsiveness import _isolate_runtime_paths, _QuitObservedApp


@pytest.mark.parametrize("service", ["athena", "glue", "emr-serverless"])
async def test_source_picker_waiting_for_navigation_does_not_block_quit(
    service: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = _QuitObservedApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_service(ctx, app, pilot, service)
        await _wait_for_service_setup(ctx, app, pilot)
        source = ctx.root_vm.active_connection
        header = app.query_one(ServiceSourceHeader)
        picker = header.picker
        assert picker.value == "0"
        await focus_and_settle(picker)
        await pilot.press("enter")
        await wait_until(lambda: picker.is_open, what="real source options open")
        await pilot.press("down")
        generation = app._table_navigation_generation
        await app._service_navigation_lock.acquire()
        commit = asyncio.create_task(pilot.press("enter"))
        quit_press: asyncio.Task[None] | None = None
        handled_before_release = False
        try:
            await wait_until(
                lambda: app._table_navigation_generation > generation,
                what="source commit queued behind existing navigation",
            )
            quit_press = asyncio.create_task(pilot.press("q"))
            try:
                await asyncio.wait_for(app.quit_requested.wait(), timeout=2.0)
                handled_before_release = True
            except TimeoutError:
                pass
        finally:
            app._service_navigation_lock.release()
            presses = [commit, *([quit_press] if quit_press is not None else [])]
            await asyncio.wait_for(asyncio.gather(*presses, return_exceptions=True), timeout=10.0)
        assert handled_before_release, f"{service} source picker blocked quit behind navigation"
        assert ctx.root_vm.active_connection == source
