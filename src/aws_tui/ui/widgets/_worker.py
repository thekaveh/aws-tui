"""Lifecycle ownership for Textual workers.

Creating a coroutine before worker admission can abandon it if cancellation
occurs before the first worker turn. This occurs during exclusive replacement
and shutdown.

``run_deferred_worker`` constructs work inside the worker. Use it when invocation
can wait until the worker starts.

``run_owned_awaitable`` retains existing work when synchronous dispatch must
capture context immediately. It closes native coroutines or cancels futures if
cancellation precedes entry.

Both functions keep Textual's worker ownership and error handling. The owned
bridge keeps independent invocations in the same group.

``DeferredWorkerMixin`` provides the widget-facing deferred spelling.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar, cast

from textual.worker import Worker

if TYPE_CHECKING:
    from textual.dom import DOMNode

_ResultT = TypeVar("_ResultT")


class _OwnedAwaitableWorker(Worker[_ResultT]):
    """Release supplied work if cancellation precedes the worker's first turn."""

    def __init__(self, node: DOMNode, work: Awaitable[_ResultT], *, group: str) -> None:
        super().__init__(node, work, group=group)
        self._owned_awaitable = work
        self._entered = False

    async def run(self) -> _ResultT:
        self._entered = True
        return await self._owned_awaitable

    def cancel(self) -> None:
        super().cancel()
        if not self._entered:
            if inspect.iscoroutine(self._owned_awaitable):
                self._owned_awaitable.close()
            elif asyncio.isfuture(self._owned_awaitable):
                self._owned_awaitable.cancel()


def run_owned_awaitable(
    node: DOMNode, work: Awaitable[_ResultT], *, group: str
) -> Worker[_ResultT]:
    """Own existing work while preserving synchronous dispatch and App lifetime."""
    worker = _OwnedAwaitableWorker(node, work, group=group)
    node.workers.add_worker(worker, exclusive=False)
    return worker


def run_deferred_worker(
    node: DOMNode,
    work: Callable[[], Awaitable[_ResultT]],
    *,
    group: str,
    exclusive: bool = True,
    exit_on_error: bool = True,
    name: str = "",
) -> Worker[_ResultT]:
    """Run ``work`` in a worker, constructing its awaitable inside the worker."""

    async def deferred() -> _ResultT:
        return await work()

    return node.run_worker(
        deferred,
        exclusive=exclusive,
        group=group,
        exit_on_error=exit_on_error,
        name=name,
    )


class DeferredWorkerMixin:
    """Provides :meth:`_run_lifecycle_worker` to widgets, screens and the app.

    Consumers are always ``DOMNode`` subclasses. The mixin does not declare
    ``run_worker`` itself: doing so shadows ``DOMNode``'s real signature for
    every consumer, which silently degrades their own ``run_worker`` calls to
    ``Any``. It narrows ``self`` at the one place it needs the node instead.
    """

    def _run_lifecycle_worker(
        self,
        work: Callable[[], Awaitable[_ResultT]],
        *,
        group: str,
        exclusive: bool = True,
        exit_on_error: bool = True,
        name: str = "",
    ) -> Worker[_ResultT]:
        return run_deferred_worker(
            cast("DOMNode", self),
            work,
            group=group,
            exclusive=exclusive,
            exit_on_error=exit_on_error,
            name=name,
        )


__all__ = ["DeferredWorkerMixin", "run_deferred_worker", "run_owned_awaitable"]
