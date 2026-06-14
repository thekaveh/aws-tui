"""ToastStackVM — owns the toast collection and auto-dismiss timers.

The stack is the only place that schedules :mod:`asyncio` timers for
non-sticky toasts; the individual :class:`ToastVM` is purely declarative.
Disposing the stack cancels all in-flight timers before tearing down each
child toast.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import reactivex as rx
from vmx import ComponentVMOf, CompositeVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.vm.chrome.toast_vm import ToastModel, ToastVM


class ToastStackVM:
    """Facade for the toast collection.

    Backed by a VMx :class:`CompositeVM` so the view layer can observe
    additions and removals via ``on_collection_changed``.
    """

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._toasts: list[ToastVM] = []
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._disposed: bool = False

        # The composite tracks the underlying VMx ``ComponentVMOf`` instances
        # (CompositeVM requires real VMx VMs as children — facade objects
        # cannot be parented). The facade-to-inner mapping lives in
        # ``self._toasts``; we keep them in lockstep.
        self._inner: CompositeVM[ComponentVMOf[ToastModel]] = (
            CompositeVM[ComponentVMOf[ToastModel]]
            .builder()
            .name("toast_stack")
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return self._inner.count

    @property
    def toasts(self) -> tuple[ToastVM, ...]:
        return tuple(self._toasts)

    @property
    def on_collection_changed(self) -> rx.Observable[object]:
        # Forwarded so subscribers don't reach into the composite directly.
        # We widen to ``rx.Observable[object]`` because the CollectionChangedEvent
        # type lives under vmx.collections and we don't need that detail here.
        return self._inner.on_collection_changed

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._cancel_all_timers()
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._cancel_all_timers()
        self._inner.dispose()

    # ── Public API ──────────────────────────────────────────────────────────

    def raise_toast(self, model: ToastModel) -> ToastVM:
        """Add a new toast to the stack and start its auto-dismiss timer (if any).

        Returns the constructed :class:`ToastVM` so callers may keep a reference.
        """
        toast = ToastVM(
            model,
            hub=self._hub,
            dispatcher=self._dispatcher,
            on_dismiss=self._on_toast_dismissed,
        )
        self._toasts.append(toast)
        # CompositeVM owns the lifecycle of the *inner* VM; we construct the
        # facade in lockstep so the dismiss_command is wired before the toast
        # is observable.
        if self._inner.is_constructed:
            toast.construct()
        self._inner.append(toast._inner)
        # auto_construct_on_add=True takes care of construct() on the inner
        # once the composite itself is constructed. If we're called before
        # construct() the toast gets constructed by the composite's own
        # _on_construct cascade.

        if not model.sticky and model.timeout_seconds is not None:
            self._schedule_auto_dismiss(toast)

        return toast

    def dismiss(self, toast_id: str) -> None:
        """Remove the toast with ``toast_id``. Unknown ids are silently ignored."""
        target = self._find(toast_id)
        if target is None:
            return
        target.dismiss_command.execute()

    # ── Internal ────────────────────────────────────────────────────────────

    def _initial_children(self) -> Iterable[ComponentVMOf[ToastModel]]:
        # Toasts are only ever added at runtime via raise_toast; the initial
        # collection is whatever has already been queued before construct().
        return tuple(t._inner for t in self._toasts)

    def _on_toast_dismissed(self, toast: ToastVM) -> None:
        # Cancel timer first so the on_dismiss callback doesn't race with
        # the auto-dismiss task we may have scheduled.
        timer = self._timers.pop(toast.model.id, None)
        if timer is not None and not timer.done():
            timer.cancel()
        # Remove from the facade list, drop the inner child from the
        # composite (does NOT dispose), then dispose the facade which in
        # turn disposes the inner VMx VM.
        if toast in self._toasts:
            self._toasts.remove(toast)
        if toast._inner in self._inner:
            self._inner.remove(toast._inner)
        toast.dispose()

    def _schedule_auto_dismiss(self, toast: ToastVM) -> None:
        timeout = toast.model.timeout_seconds
        if timeout is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller is expected to drive the stack from an
            # asyncio context. Quietly skip the timer so non-async callers
            # don't crash.
            return

        async def _run() -> None:
            try:
                await asyncio.sleep(timeout)
            except asyncio.CancelledError:
                return
            if self._disposed:
                return
            if toast.is_dismissed:
                return
            toast.dismiss_command.execute()

        task = loop.create_task(_run(), name=f"toast-auto-dismiss-{toast.model.id}")
        self._timers[toast.model.id] = task

    def _cancel_all_timers(self) -> None:
        for task in list(self._timers.values()):
            if not task.done():
                task.cancel()
        self._timers.clear()

    def _find(self, toast_id: str) -> ToastVM | None:
        for t in self._toasts:
            if t.model.id == toast_id:
                return t
        return None


__all__ = ["ToastStackVM"]
