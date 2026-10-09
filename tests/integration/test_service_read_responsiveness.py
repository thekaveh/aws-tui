"""First-view reads and pagination must not occupy the App message pump."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aws_tui.composition import build_app_context
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from tests.helpers import focus_and_settle, wait_until
from tests.integration.test_glue_athena_navigation import (
    _activate_handoff,
    _athena_client,
    _open_service,
    _wait_for_service_setup,
)
from tests.integration.test_s3_action_responsiveness import _isolate_runtime_paths, _QuitObservedApp


@pytest.mark.parametrize(
    ("service", "key", "method"),
    [
        ("glue", "2", "list_jobs_page"),
        ("glue", "3", "list_crawlers_page"),
        ("glue", "F", "list_jobs_page"),
        ("glue", "G", "list_crawlers_page"),
        ("athena", "2", "list_query_executions_page"),
        ("athena", "4", "list_named_queries_page"),
        ("athena", "l", "list_workgroups_page"),
    ],
)
async def test_pending_service_read_does_not_block_quit(
    service: str,
    key: str,
    method: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    if key == "l":
        _athena_client(ctx, "demo-dev").page_size = 1
    app = _QuitObservedApp(ctx)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with app.run_test(size=(120, 40)) as pilot:
        vm = await _open_service(ctx, app, pilot, service)
        await _wait_for_service_setup(ctx, app, pilot)
        if key == "l":
            assert vm.has_more_workgroups
            target = app.query_one("#athena-workgroup", ContextPicker)
        else:
            target = app.query_one(ServiceSourceHeader).picker
        await focus_and_settle(target)
        original = getattr(vm._client, method)

        async def gated(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return await original(*args, **kwargs)

        monkeypatch.setattr(vm._client, method, gated)
        read_press = asyncio.create_task(pilot.press(key))
        quit_press: asyncio.Task[None] | None = None
        handled_before_release = False
        try:
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            quit_press = asyncio.create_task(pilot.press("q"))
            try:
                await asyncio.wait_for(app.quit_requested.wait(), timeout=2.0)
                handled_before_release = True
                await asyncio.wait_for(cancelled.wait(), timeout=2.0)
            except TimeoutError:
                pass
        finally:
            release.set()
            presses = [read_press, *([quit_press] if quit_press is not None else [])]
            await asyncio.wait_for(asyncio.gather(*presses, return_exceptions=True), timeout=10.0)
        assert handled_before_release, f"{service} {key!r} read blocked quit"
        assert cancelled.is_set()


@pytest.mark.parametrize("service", ["glue", "athena"])
@pytest.mark.parametrize("interaction", ["keyboard", "pointer"])
async def test_new_view_choice_cancels_a_pending_first_view_read(
    service: str,
    interaction: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = _QuitObservedApp(ctx)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first, latest, method = (
        ("jobs", "crawlers", "list_jobs_page")
        if service == "glue"
        else ("history", "results", "list_query_executions_page")
    )
    async with app.run_test(size=(120, 40)) as pilot:
        vm = await _open_service(ctx, app, pilot, service)
        await _wait_for_service_setup(ctx, app, pilot)
        await focus_and_settle(app.query_one(ServiceSourceHeader).picker)
        original = getattr(vm._client, method)

        async def gated(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return await original(*args, **kwargs)

        monkeypatch.setattr(vm._client, method, gated)
        try:
            if interaction == "keyboard":
                await pilot.press("2")
            else:
                assert await pilot.click(f"#{service}-tab-{first}")
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            if interaction == "keyboard":
                await pilot.press("3")
            else:
                assert await pilot.click(f"#{service}-tab-{latest}")
            await wait_until(lambda: vm.active_view == latest, what="latest view owns the page")
            await asyncio.wait_for(cancelled.wait(), timeout=2.0)
        finally:
            release.set()
        await _wait_for_service_setup(ctx, app, pilot)
        assert vm.active_view == latest


async def test_default_result_location_palette_action_keeps_quit_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = _QuitObservedApp(ctx)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with app.run_test(size=(120, 40)) as pilot:
        vm = await _open_service(ctx, app, pilot, "athena")
        await vm.select_view("history")
        await vm.select_history_execution("q-dev-succeeded")
        await vm.open_history_results()
        await _wait_for_service_setup(ctx, app, pilot)
        assert vm.active_view == "results"
        original = vm._client.get_query_execution

        async def gated(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return await original(*args, **kwargs)

        monkeypatch.setattr(vm._client, "get_query_execution", gated)
        try:
            await _activate_handoff(pilot, key=None, label="Open Athena result in S3")
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            await pilot.press("q")
            await asyncio.wait_for(app.quit_requested.wait(), timeout=2.0)
            await asyncio.wait_for(cancelled.wait(), timeout=2.0)
        finally:
            release.set()
