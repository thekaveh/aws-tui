"""Shared test helpers.

Tier-specific fixtures live in ``tests/<tier>/conftest.py``; this module holds
plain helpers that more than one tier imports directly.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from textual.app import App
    from textual.widget import Widget
    from textual.widgets import TextArea

DEFAULT_DRAIN_TIMEOUT_SECONDS = 30.0

# A worker that finishes may spawn further workers before it is removed from the
# manager, so a bounded number of rounds is required rather than a single wait.
# The cap only exists to turn a runaway spawn loop into a named failure instead
# of a hang; healthy drains settle in one or two rounds.
_MAX_DRAIN_ROUNDS = 20


async def drain_workers(
    # ``App`` is invariant in its return type, so ``App[object]`` rejects the
    # concrete ``AwsTuiApp`` (an ``App[None]``). Only ``app.workers`` is touched
    # here, so the parameter is deliberately unconstrained.
    app: App[Any],
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


async def focus_and_settle(
    widget: Widget,
    *,
    timeout: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Focus ``widget`` and wait for the focus to actually land.

    ``Widget.focus()`` defers through ``call_later`` and ``App.set_focus`` is a
    no-op while the widget is not yet focusable, so ``widget.focus()`` followed
    immediately by ``pilot.press(...)`` sends the key wherever focus happens to
    be at that moment. That is order- and load-dependent: it passes alone and
    fails in a full run, which is exactly how
    ``test_enter_and_space_press_all_enabled_iceberg_buttons`` failed with a
    30-second timeout while passing standalone, in its own file, and across its
    whole subtree.

    Waiting on the precondition rather than assuming it keeps the test honest:
    a widget that genuinely cannot take focus now fails saying so, instead of
    silently asserting against a key that went somewhere else.
    """
    widget.focus()
    deadline = asyncio.get_running_loop().time() + timeout
    while not widget.has_focus:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"{type(widget).__name__}(id={widget.id!r}) never took focus within {timeout}s"
            )
        await asyncio.sleep(0.01)


async def seed_athena_sql(pilot: object, query_vm: object, editor: TextArea, sql: str) -> None:
    """Put ``sql`` in the editor without racing the MVVM binding.

    Assigning ``editor.text`` reaches the view model through a queued Textual
    ``Changed`` message, while ``AthenaQueryView._refresh`` -- scheduled by any
    VM notification through ``call_after_refresh`` -- rewrites the editor from
    ``vm.sql``. Whichever callback lands first wins. A widget-seeded editor can
    therefore be blanked again before the assertion runs, and assertions on
    VM-derived state (command admission, hint enablement) can read the previous
    value. Reproduced deterministically by running ``_refresh`` before the
    ``Changed`` message: the editor comes back empty, which is the
    ``assert '' == 'SELECT 1'`` seen on windows-latest.

    Seeding the view model -- the source of truth the editor is a projection of
    -- makes both sides agree from the outset. Tests that deliberately simulate
    a user typing still assign ``editor.text`` directly; this is only for
    establishing a precondition.
    """
    # Seeding the view model removes the clobber race, but two more things can
    # still undo it, so re-assert until the state holds across a settle.
    #
    # The projection back onto the editor is a deferred refresh, so one pause is
    # not enough on a slow runner. And `AthenaQueryView._refresh` guards its own
    # write with a synchronous `_syncing_editor` flag while Textual delivers the
    # resulting `Changed` message *later* -- by which time the flag is back to
    # False. A stale echo carrying the previous text therefore reaches
    # `set_sql` and reverts the view model, which is how this helper failed on
    # windows-latest with `assert 'SELECT 1' == 'DELETE FROM events'` when two
    # values were seeded in quick succession.
    for _ in range(50):
        if editor.text == sql and query_vm.sql == sql:  # type: ignore[attr-defined]
            await pilot.pause()  # type: ignore[attr-defined]
            if editor.text == sql and query_vm.sql == sql:  # type: ignore[attr-defined]
                return
        query_vm.set_sql(sql)  # type: ignore[attr-defined]
        await pilot.pause()  # type: ignore[attr-defined]
    assert editor.text == sql
