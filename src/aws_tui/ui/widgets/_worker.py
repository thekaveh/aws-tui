"""Deferred dispatch for exclusive Textual workers.

``DOMNode.run_worker`` accepts either a coroutine object or a
zero-argument callable returning an awaitable. Passing the coroutine object
constructs it at call time, before the worker that will consume it exists.

With ``exclusive=True`` that is a leak. A second call in the same group cancels
the first worker, and when the first has not been scheduled yet its coroutine is
discarded without ever being awaited -- a ``coroutine ... was never awaited``
RuntimeWarning, plus whatever the coroutine had already allocated. Measured on a
minimal Textual app, five rapid ``exclusive=True`` dispatches leak four
coroutines; the same five through :func:`run_deferred_worker` leak none.
Dispatching and then removing the widget leaks one, and none respectively.

Deferring also means the work callable is invoked *inside* the worker, so
``get_current_worker()`` resolves for anything the callable itself runs.

:class:`DeferredWorkerMixin` carries the widget-facing spelling; ``AwsTuiApp``
uses it too, so the whole UI layer has one idiom for this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from textual.dom import DOMNode
    from textual.worker import Worker

_ResultT = TypeVar("_ResultT")


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


__all__ = ["DeferredWorkerMixin", "run_deferred_worker"]
