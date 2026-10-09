"""Slow application selection must leave EMR's actual shortcuts responsive."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aws_tui.composition import build_app_context
from tests.helpers import wait_until
from tests.integration.test_glue_athena_navigation import _open_service, _wait_for_service_setup
from tests.integration.test_s3_action_responsiveness import _isolate_runtime_paths, _QuitObservedApp


@pytest.mark.parametrize("next_key", ["q", "A"])
async def test_emr_application_cycle_does_not_block_the_next_shortcut(
    next_key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    app = _QuitObservedApp(ctx)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with app.run_test(size=(120, 40)) as pilot:
        vm = await _open_service(ctx, app, pilot, "emr-serverless")
        await _wait_for_service_setup(ctx, app, pilot)
        assert len(vm.applications.sorted_applications) == 2
        initial = vm.applications.selected_id
        original = vm._client.list_job_runs_page
        block_once = True

        async def gated(*args, **kwargs):
            nonlocal block_once
            if block_once:
                block_once = False
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return await original(*args, **kwargs)

        monkeypatch.setattr(vm._client, "list_job_runs_page", gated)
        first_press = asyncio.create_task(pilot.press("A"))
        second_press: asyncio.Task[None] | None = None
        handled_before_release = False
        try:
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            assert vm.applications.selected_id != initial
            second_press = asyncio.create_task(pilot.press(next_key))
            try:
                if next_key == "q":
                    await asyncio.wait_for(app.quit_requested.wait(), timeout=2.0)
                else:
                    await wait_until(
                        lambda: vm.applications.selected_id == initial,
                        what="second application choice during blocked first choice",
                        timeout=2.0,
                    )
                handled_before_release = True
                await asyncio.wait_for(cancelled.wait(), timeout=2.0)
            except (TimeoutError, AssertionError):
                pass
        finally:
            release.set()
            presses = [first_press, *([second_press] if second_press is not None else [])]
            await asyncio.wait_for(asyncio.gather(*presses, return_exceptions=True), timeout=5.0)
        assert handled_before_release, f"EMR application loading blocked {next_key!r}"
        assert cancelled.is_set()
        if next_key == "A":
            await _wait_for_service_setup(ctx, app, pilot)
            assert vm.applications.selected_id == initial
            assert vm.job_runs.application_id == initial
