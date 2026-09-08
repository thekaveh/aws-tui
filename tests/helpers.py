"""Shared test helpers.

Tier-specific fixtures live in ``tests/<tier>/conftest.py``; this module holds
plain helpers that more than one tier imports directly.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App

DEFAULT_DRAIN_TIMEOUT_SECONDS = 30.0

# A worker that finishes may spawn further workers before it is removed from the
# manager, so a bounded number of rounds is required rather than a single wait.
# The cap only exists to turn a runaway spawn loop into a named failure instead
# of a hang; healthy drains settle in one or two rounds.
_MAX_DRAIN_ROUNDS = 20


async def drain_workers(
    app: App[object],
    *,
    timeout: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Await every Textual worker, including ones spawned while draining.

    ``WorkerManager.wait_for_complete`` and the ``list(app.workers._workers)``
    idiom both snapshot the worker set once. A worker that itself calls
    ``run_worker`` — which is what a service switch or a handoff dispatch does —
    therefore escapes the wait, and the caller's assertions race it. Loop until
    the manager reports no unfinished workers so those descendants are covered.
    """
    workers = app.workers
    # ``timeout`` budgets the WHOLE drain, not each round. Per-round timeouts
    # summed past the 60s pytest-timeout ceiling, so a runaway spawn loop died
    # as an opaque pytest timeout and the diagnostic below was unreachable.
    deadline = asyncio.get_running_loop().time() + timeout
    for _ in range(_MAX_DRAIN_ROUNDS):
        pending = [worker for worker in list(workers._workers) if not worker.is_finished]
        if not pending:
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        await asyncio.wait_for(
            asyncio.gather(*(worker.wait() for worker in pending), return_exceptions=True),
            timeout=remaining,
        )
        # ``Worker._run`` sets its terminal state inside the task, so
        # ``is_finished`` is already true when ``wait()`` returns; this yield is
        # only so the manager has dropped them before the diagnostic below.
        await asyncio.sleep(0)
    raise AssertionError(
        f"workers still pending after {_MAX_DRAIN_ROUNDS} drain rounds: "
        f"{sorted(worker.name for worker in workers._workers)}"
    )
