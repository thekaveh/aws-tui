from __future__ import annotations

import asyncio

import pytest

from aws_tui.app import _await_with_notice


@pytest.mark.asyncio
async def test_a_slow_shutdown_step_announces_itself_once_and_still_completes() -> None:
    """A step still running after the delay said nothing, so a slow save looked like a hang."""
    announced: list[str] = []
    finished: list[str] = []

    async def slow_step() -> None:
        await asyncio.sleep(0.1)
        finished.append("done")

    await _await_with_notice("athena_drafts.shutdown", slow_step, announced.append, delay=0.01)

    assert announced == ["athena_drafts.shutdown"]
    assert finished == ["done"]


@pytest.mark.asyncio
async def test_a_fast_shutdown_step_announces_nothing() -> None:
    announced: list[str] = []

    async def quick_step() -> None:
        return None

    await _await_with_notice("content_host.shutdown", quick_step, announced.append, delay=0.5)
    await asyncio.sleep(0.6)

    assert announced == []
