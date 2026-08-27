"""ContentHostVM — holds the active service's VM tree.

``set_content(new_vm, service_id=...)`` constructs the candidate before
shutting down and disposing the previous content tree. Re-setting
with the identical non-null VM instance is a no-op. A different VM with
the same ``service_id`` is still a real replacement and runs the complete
shutdown, disposal, construction, and setup lifecycle.

``setup`` (if the hosted VM exposes one) is dispatched as a BACKGROUND
asyncio task by ``set_content`` rather than awaited inline. This is what
lets the chrome paint the new content immediately when the user clicks
a service even if its async ``setup()`` includes a slow listing call
(e.g. ``S3FS.list`` blocking on a 60-second botocore retry budget for an
unreachable endpoint). The pane VM still transitions LOADING → IDLE /
UNREACHABLE / FORBIDDEN through the existing reactive ``state`` property
once ``setup`` finishes — the host just doesn't gate the swap itself on
that completion any more. If a new ``set_content`` swaps the content out
before the prior setup finishes, the prior task is cancelled.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


class ContentHostVM:
    """Owns the currently active service VM and orchestrates the swap."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        on_setup_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._hub: MessageHub[Message] = hub

        self._current: Any | None = None
        self._current_id: str | None = None
        # Background setup task for ``self._current``. Cancelled by the
        # next ``set_content`` (or by ``dispose``) so a stale setup
        # doesn't outlive its VM.
        self._setup_task: asyncio.Task[None] | None = None
        self._on_setup_error = on_setup_error
        self._swap_lock = asyncio.Lock()

        self._inner: ComponentVM = (
            ComponentVM.builder().name("content_host").services(hub, dispatcher).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def current(self) -> Any | None:
        return self._current

    @property
    def current_id(self) -> str | None:
        return self._current_id

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
        self._cancel_pending_setup()
        if self._current is not None:
            self._current.destruct()
        self._inner.destruct()

    def dispose(self) -> None:
        self._cancel_pending_setup()
        if self._current is not None:
            self._current.dispose()
            self._current = None
            self._current_id = None
        self._inner.dispose()

    # ── Public API ─────────────────────────────────────────────────────────

    async def set_content(
        self,
        vm: Any | None,
        *,
        service_id: str | None,
        before_publish: Callable[[], None] | None = None,
    ) -> None:
        try:
            async with self._swap_lock:
                await self._set_content_locked(
                    vm,
                    service_id=service_id,
                    before_publish=before_publish,
                )
        except BaseException:
            # Ownership transfers before lock acquisition. If adoption never
            # happened, the host is the candidate's sole disposer.
            if vm is not None and self._current is not vm:
                vm.dispose()
            raise

    async def _set_content_locked(
        self,
        vm: Any | None,
        *,
        service_id: str | None,
        before_publish: Callable[[], None] | None,
    ) -> None:
        """Swap the hosted VM. Idempotent only for the identical VM instance.

        Adoption + the ``"current"`` :class:`PropertyChangedMessage`
        fire synchronously inside the await — the View layer can mount
        the new widget tree as soon as this returns. If the hosted VM
        exposes a ``setup`` callable it is dispatched as a background
        ``asyncio.Task`` (not awaited inline); the pane VMs reflect
        its outcome through their reactive ``state`` so the View
        re-renders LOADING → IDLE / UNREACHABLE / FORBIDDEN without
        the host having to gate the swap on setup completion. A
        subsequent ``set_content`` cancels the prior setup task.
        """
        if self._current is vm and vm is not None:
            # Re-adopting the identical VM instance is a true no-op.
            return
        if vm is not None:
            # Construction is the only synchronous adoption step that can
            # fail. Complete it while the outgoing VM is still intact so a
            # bad replacement cannot empty the content host.
            vm.construct()
        # Cancel any in-flight setup for the OUTGOING VM before we
        # dispose it (the task holds a reference to the VM; if we
        # dispose first the task may dereference disposed state).
        shutdown_cancelled = False
        try:
            await self._cancel_and_drain_setup()
            shutdown_cancelled = await self._shutdown_current_for_swap()
        except BaseException:
            raise
        # Dispose the previous content first so its subscriptions / tasks
        # tear down before the new one wires up.
        if self._current is not None:
            try:
                self._current.dispose()
            except BaseException:
                raise
            self._current = None
            self._current_id = None

        if shutdown_cancelled:
            self._hub.send(PropertyChangedMessage.create(self, self.name, "current"))
            raise asyncio.CancelledError

        if vm is None:
            self._hub.send(PropertyChangedMessage.create(self, self.name, "current"))
            return

        # Adopt the already-constructed candidate before driving setup. Adopting
        # first means a setup failure (e.g. ``S3FS.list`` raising
        # ``NoCredentialsError``) still leaves the View layer with something
        # to mount — every pane renders its own error placeholder per spec
        # §7.7. If we adopted only on setup success, an auth failure would
        # leave the content host entirely blank instead.
        self._current = vm
        self._current_id = service_id
        if before_publish is not None:
            before_publish()
        self._hub.send(PropertyChangedMessage.create(self, self.name, "current"))

        setup = getattr(vm, "setup", None)
        if callable(setup):
            # Dispatch as a background task so a slow ``setup``
            # (e.g. ``S3FS.list`` blocking on a 60-second botocore
            # retry budget) doesn't block the View from mounting
            # the freshly-adopted VM. The pane VMs surface progress
            # through their reactive ``state`` property.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop — caller is driving us from a
                # sync context (some tests). Fall back to awaiting
                # inline so behaviour is at least deterministic.
                result = setup()
                if inspect.isawaitable(result):
                    await result
            else:
                self._setup_task = loop.create_task(
                    self._run_setup(setup),
                    name=f"content-host-setup-{service_id}",
                )
                # Drain exceptions so a non-cancel raise from the
                # awaitable (NoCredentialsError, ProviderError,
                # botocore client error, programmer bug) doesn't
                # silently log "Task exception was never
                # retrieved" to stderr — invisible in a TUI.
                # Same R36 / R37 shield pattern as
                # CommandPaletteVM._spawn_awaitable and
                # ToastStackVM auto-dismiss. Without this the
                # task finishes with a non-cancel exception,
                # _cancel_pending_setup short-circuits at
                # task.done() and drops the reference, and
                # asyncio's GC emits the warning.
                self._setup_task.add_done_callback(self._on_setup_done)

    async def shutdown(self) -> None:
        """Await the current VM's optional graceful-shutdown hook."""
        async with self._swap_lock:
            await self._cancel_and_drain_setup()
            await self._shutdown_current()

    async def _shutdown_current(self) -> None:
        if self._current is None:
            return
        shutdown = getattr(self._current, "shutdown", None)
        if not callable(shutdown):
            return
        result = shutdown()
        if inspect.isawaitable(result):
            await result

    async def _shutdown_current_for_swap(self) -> bool:
        """Drain outgoing shutdown before honoring caller cancellation."""
        if self._current is None:
            return False
        shutdown = getattr(self._current, "shutdown", None)
        if not callable(shutdown):
            return False
        result = shutdown()
        if not inspect.isawaitable(result):
            return False

        shutdown_task = asyncio.ensure_future(result)
        caller = asyncio.current_task()
        caller_cancellation_count = 0 if caller is None else caller.cancelling()
        cancellation_requested = False
        while not shutdown_task.done():
            try:
                await asyncio.shield(shutdown_task)
            except asyncio.CancelledError:
                current_count = 0 if caller is None else caller.cancelling()
                if current_count > caller_cancellation_count:
                    cancellation_requested = True
                    caller_cancellation_count = current_count
                elif shutdown_task.done():
                    break
            except Exception:
                break
        shutdown_task.result()
        return cancellation_requested

    async def _run_setup(self, setup: Any) -> None:
        """Drive ``setup``'s awaitable; swallow cancellation cleanly.

        Errors from the awaitable propagate out — the asyncio task
        result captures them so a test or supervisor can inspect them
        if needed. Pane VMs already surface user-visible failure
        states through their reactive ``state`` property, so the host
        doesn't need to publish anything extra here.
        """
        try:
            result = setup()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            return

    def _cancel_pending_setup(self) -> None:
        task = self._setup_task
        self._setup_task = None
        if task is None or task.done():
            return
        task.cancel()

    async def _cancel_and_drain_setup(self) -> None:
        task = self._setup_task
        self._setup_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        caller = asyncio.current_task()
        caller_cancellation_count = 0 if caller is None else caller.cancelling()
        cancellation_requested = caller_cancellation_count > 0
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                current_count = 0 if caller is None else caller.cancelling()
                if current_count > caller_cancellation_count:
                    # Keep draining setup cleanup even when this owner is
                    # cancelled repeatedly. Re-raise only after the stale task
                    # cannot touch the outgoing VM anymore.
                    cancellation_requested = True
                    caller_cancellation_count = current_count
                elif task.done():
                    # The shield surfaced cancellation of the setup task
                    # itself; the caller remains live and may complete its swap.
                    break
            except Exception:
                break
        if cancellation_requested:
            raise asyncio.CancelledError

    def _on_setup_done(self, task: asyncio.Task[None]) -> None:
        """Done-callback drain — see add_done_callback site for
        rationale. Pops the task reference if it's still the current
        one (a later set_content may have already replaced it)."""
        if self._setup_task is task:
            self._setup_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and self._on_setup_error is not None:
            self._on_setup_error(error)


__all__ = ["ContentHostVM"]
