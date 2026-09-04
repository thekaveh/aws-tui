"""Exclusive workers must construct their awaitable inside the worker.

``run_worker`` accepts a coroutine object, and passing one is the obvious
spelling. It is also a leak whenever ``exclusive=True``: a second dispatch in
the same group cancels the first worker, and a first worker that has not been
scheduled yet never awaits the coroutine it was handed.
"""

from __future__ import annotations

import ast
import gc
import warnings
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from aws_tui.ui.widgets._worker import DeferredWorkerMixin, run_deferred_worker

REPO_ROOT = Path(__file__).parents[3]
SRC_ROOT = REPO_ROOT / "src"


class _Probe(Static, DeferredWorkerMixin):
    def __init__(self) -> None:
        super().__init__()
        self.entered = 0

    async def _work(self) -> None:
        self.entered += 1

    def fire_eager(self) -> None:
        self.run_worker(self._work(), exclusive=True, group="probe")

    def fire_deferred(self) -> None:
        self._run_lifecycle_worker(self._work, group="probe")

    def fire_function(self) -> None:
        run_deferred_worker(self, self._work, group="probe")


class _ProbeApp(App[None]):
    def compose(self) -> ComposeResult:
        yield _Probe()


def _never_awaited(record: warnings.WarningMessage) -> bool:
    return issubclass(record.category, RuntimeWarning) and "never awaited" in str(record.message)


async def _leaks_from_burst(spelling: str) -> int:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = _ProbeApp()
        async with app.run_test() as pilot:
            probe = app.query_one(_Probe)
            for _ in range(5):
                getattr(probe, spelling)()
            await pilot.pause()
            app.workers.cancel_all()
        gc.collect()
        return len([record for record in caught if _never_awaited(record)])


@pytest.mark.asyncio
async def test_passing_a_coroutine_to_an_exclusive_worker_leaks_the_superseded_ones() -> None:
    """Pins the defect itself, so the guard below is not just an unexplained rule."""
    assert await _leaks_from_burst("fire_eager") > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["fire_deferred", "fire_function"])
async def test_deferred_dispatch_leaks_nothing_when_superseded(spelling: str) -> None:
    assert await _leaks_from_burst(spelling) == 0


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
