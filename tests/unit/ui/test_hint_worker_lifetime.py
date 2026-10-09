"""Clickable commands must retain owned, nonexclusive action lifetimes."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Coroutine
from typing import Any

import pytest
from textual.app import App, ComposeResult
from textual.events import Click

from aws_tui.ui.widgets.hint_legend import _HintChip
from aws_tui.vm.chrome.hint_legend_vm import HintAction


class _HintWorkerApp(App[None]):
    def __init__(self, *, mode: str = "coroutine") -> None:
        super().__init__()
        self.mode = mode
        self.actions: list[str] = []
        self.constructed: list[Coroutine[Any, Any, None]] = []
        self.futures: list[asyncio.Future[None]] = []
        self.finished: list[str] = []
        self.release = asyncio.Event()

    def compose(self) -> ComposeResult:
        yield _HintChip(HintAction("athena.query", "1", "Query", "Open query"))

    def action_dispatch(self, action_id: str) -> Awaitable[None] | None:
        self.actions.append(action_id)
        if self.mode == "sync":
            self.finished.append(action_id)
            return None
        if self.mode == "failed-future":
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            future.set_exception(RuntimeError("fixture action failed"))
            self.futures.append(future)
            return future

        async def work() -> None:
            await self.release.wait()
            self.finished.append(action_id)

        coroutine = work()
        self.constructed.append(coroutine)
        if self.mode == "task":
            task = asyncio.create_task(coroutine)
            self.futures.append(task)
            return task
        return coroutine


def _click(chip: _HintChip) -> None:
    chip.on_click(
        Click(
            widget=chip,
            x=0,
            y=0,
            delta_x=0,
            delta_y=0,
            button=1,
            shift=False,
            meta=False,
            ctrl=False,
        )
    )


@pytest.mark.parametrize("mode", ["coroutine", "task", "failed-future"])
async def test_hint_cancelled_before_worker_starts_does_not_abandon_coroutine(mode: str) -> None:
    app = _HintWorkerApp(mode=mode)
    try:
        async with app.run_test() as pilot:
            _click(app.query_one(_HintChip))
            app.workers.cancel_all()
            await pilot.pause()
        assert all(
            inspect.getcoroutinestate(coroutine) != inspect.CORO_CREATED
            for coroutine in app.constructed
        )
        assert app.finished == []
        assert all(future.done() for future in app.futures)
        # Observe CPython's fault-retrieval flag without retrieving the fault ourselves.
        assert all(not future._log_traceback for future in app.futures)
    finally:
        for coroutine in app.constructed:
            coroutine.close()


@pytest.mark.parametrize("mode", ["sync", "coroutine", "task"])
async def test_repeated_hint_clicks_keep_each_action_owned(mode: str) -> None:
    app = _HintWorkerApp(mode=mode)
    async with app.run_test() as pilot:
        chip = app.query_one(_HintChip)
        _click(chip)
        _click(chip)
        assert app.actions == ["athena.query", "athena.query"]
        await pilot.pause()
        assert app.actions == ["athena.query", "athena.query"]
        app.release.set()
        await app.workers.wait_for_complete()
        assert app.finished == app.actions
