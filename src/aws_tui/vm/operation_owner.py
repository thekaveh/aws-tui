from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class OperationSuperseded(Exception):
    """An owned async operation was invalidated by a lifecycle transition."""


class OperationOwner:
    """Own provider tasks so shutdown can cancel and durably drain them."""

    def __init__(
        self,
        *,
        superseded_error: type[Exception] = OperationSuperseded,
    ) -> None:
        self._accepting = True
        self._superseded_error = superseded_error
        self.tasks: set[asyncio.Task[Any]] = set()
        self._drain_lock = asyncio.Lock()

    @property
    def accepting(self) -> bool:
        return self._accepting

    async def run(self, operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
        if not self._accepting:
            raise self._superseded_error
        caller = asyncio.current_task()
        task: asyncio.Task[T] = asyncio.create_task(operation())
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        try:
            result = await task
        except asyncio.CancelledError:
            if caller is not None and caller.cancelling():
                raise
            if not self._accepting:
                raise self._superseded_error from None
            raise
        except Exception:
            if caller is not None and caller.cancelling():
                raise asyncio.CancelledError from None
            if not self._accepting:
                raise self._superseded_error from None
            raise
        if caller is not None and caller.cancelling():
            raise asyncio.CancelledError
        if not self._accepting:
            raise self._superseded_error
        return result

    def close(self) -> None:
        self._accepting = False
        self.cancel()

    def cancel(self) -> tuple[asyncio.Task[Any], ...]:
        tasks = tuple(self.tasks)
        for task in tasks:
            # Re-cancelling interrupts awaits inside the task's cleanup path.
            if not task.done() and not task.cancelling():
                task.cancel()
        return tasks

    async def cancel_and_drain(self) -> None:
        async with self._drain_lock:
            tasks = self.cancel()
            current = asyncio.current_task()
            cancellation_count = current.cancelling() if current is not None else 0
            cancelled = False
            for task in tasks:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        current_count = current.cancelling() if current is not None else 0
                        if current_count > cancellation_count:
                            cancelled = True
                            cancellation_count = current_count
                    except Exception:
                        # One failed cleanup must not strand the remaining tasks.
                        pass
                if not task.cancelled():
                    with contextlib.suppress(Exception):
                        task.result()
                self.tasks.discard(task)
            if cancelled:
                raise asyncio.CancelledError

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self.tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.exception()


__all__ = ["OperationOwner", "OperationSuperseded"]
