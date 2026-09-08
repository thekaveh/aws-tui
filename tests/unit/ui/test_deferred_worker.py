"""Exclusive workers must construct their awaitable inside the worker.

``run_worker`` accepts a coroutine object, and passing one is the obvious
spelling. It is also a leak whenever ``exclusive=True``: a second dispatch in
the same group cancels the first worker, and a first worker that has not been
scheduled yet never awaits the coroutine it was handed.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from aws_tui.ui.widgets._worker import DeferredWorkerMixin, run_deferred_worker

REPO_ROOT = Path(__file__).parents[3]
SRC_ROOT = REPO_ROOT / "src"


class _Probe(Static, DeferredWorkerMixin):
    def __init__(self) -> None:
        super().__init__()
        self.constructed: list[Coroutine[Any, Any, None]] = []

    async def _work(self) -> None: ...

    def _make(self) -> Coroutine[Any, Any, None]:
        """Build the awaitable, recording it so its final state can be read."""
        coroutine = self._work()
        self.constructed.append(coroutine)
        return coroutine

    def fire_eager(self) -> None:
        self.run_worker(self._make(), exclusive=True, group="probe")

    def fire_deferred(self) -> None:
        self._run_lifecycle_worker(self._make, group="probe")

    def fire_function(self) -> None:
        run_deferred_worker(self, self._make, group="probe")


class _ProbeApp(App[None]):
    def compose(self) -> ComposeResult:
        yield _Probe()


async def _burst(spelling: str) -> tuple[int, int]:
    """Dispatch five times in one group; return (coroutines built, never started).

    A coroutine still in ``CORO_CREATED`` was constructed and never entered,
    which is precisely the leak. Reading the coroutine's own state is
    deterministic. The previous spelling inferred the leak from a
    "coroutine was never awaited" RuntimeWarning, which fires only when the
    interpreter happens to finalize the object -- under CI load it could land
    outside the capture and read as "no leak", failing this file intermittently
    on three separate pull requests.
    """
    app = _ProbeApp()
    async with app.run_test() as pilot:
        probe = app.query_one(_Probe)
        for _ in range(5):
            getattr(probe, spelling)()
        await pilot.pause()
        app.workers.cancel_all()
    never_started = sum(
        1
        for coroutine in probe.constructed
        if inspect.getcoroutinestate(coroutine) is inspect.CORO_CREATED
    )
    total = len(probe.constructed)
    for coroutine in probe.constructed:
        coroutine.close()
    return total, never_started


@pytest.mark.asyncio
async def test_passing_a_coroutine_to_an_exclusive_worker_leaks_the_superseded_ones() -> None:
    """Pins the defect itself, so the guard below is not just an unexplained rule."""
    built, never_started = await _burst("fire_eager")

    assert built == 5, "the eager spelling builds one awaitable per dispatch"
    assert never_started == 4, "every superseded worker abandons the coroutine it was handed"


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["fire_deferred", "fire_function"])
async def test_deferred_dispatch_leaks_nothing_when_superseded(spelling: str) -> None:
    built, never_started = await _burst(spelling)

    assert never_started == 0
    assert built == 1, "only the surviving worker should ever build an awaitable"


def test_no_source_file_hands_a_freshly_built_awaitable_to_an_exclusive_worker() -> None:
    """Structural guard: 35 call sites shared this defect, so pin the shape.

    A behavioural test per site would be 35 near-identical tests; the shape is
    what matters and it is cheap to check directly.
    """
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_worker"
                and node.args
            ):
                continue
            keywords = {kw.arg: ast.unparse(kw.value) for kw in node.keywords}
            if keywords.get("exclusive") != "True":
                continue
            if isinstance(node.args[0], ast.Call):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                    f"run_worker({ast.unparse(node.args[0])[:60]}, exclusive=True)"
                )

    assert offenders == [], (
        "exclusive run_worker calls handed an already-constructed awaitable; "
        "a superseded worker never awaits it. Use _run_lifecycle_worker or "
        "run_deferred_worker instead:\n  " + "\n  ".join(offenders)
    )
