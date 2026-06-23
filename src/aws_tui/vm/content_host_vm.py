"""ContentHostVM — holds the active service's VM tree.

``set_content(new_vm, service_id=...)`` disposes the previous content tree
(synchronously, depth-first via VMx) and constructs the new one. Re-setting
with the same ``service_id`` is a no-op so the menu can publish "selected
service" updates without rebuilding the world.

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
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        self._current: Any | None = None
        self._current_id: str | None = None
        # Background setup task for ``self._current``. Cancelled by the
        # next ``set_content`` (or by ``dispose``) so a stale setup
        # doesn't outlive its VM.
        self._setup_task: asyncio.Task[None] | None = None

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

    async def set_content(self, vm: Any | None, *, service_id: str | None) -> None:
        """Swap the hosted VM. Idempotent on equal ``service_id``.

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
        if self._current_id == service_id and service_id is not None:
            # Same active service — no-op per spec §5.4.
            return
        # Cancel any in-flight setup for the OUTGOING VM before we
        # dispose it (the task holds a reference to the VM; if we
        # dispose first the task may dereference disposed state).
        self._cancel_pending_setup()
        # Dispose the previous content first so its subscriptions / tasks
        # tear down before the new one wires up.
        if self._current is not None:
            self._current.dispose()
            self._current = None
            self._current_id = None
            self._hub.send(PropertyChangedMessage.create(self, self.name, "current"))

        if vm is None:
            return

        # Construct the new one and adopt it before driving setup. Adopting
        # first means a setup failure (e.g. ``S3FS.list`` raising
        # ``NoCredentialsError``) still leaves the View layer with something
        # to mount — every pane renders its own error placeholder per spec
        # §7.7. If we adopted only on setup success, an auth failure would
        # leave the content host entirely blank instead.
        vm.construct()
        self._current = vm
        self._current_id = service_id
        self._hub.send(PropertyChangedMessage.create(self, self.name, "current"))

        setup = getattr(vm, "setup", None)
        if callable(setup):
            result = setup()
            if inspect.isawaitable(result):
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
                    await result
                else:
                    self._setup_task = loop.create_task(
                        self._run_setup(result),
                        name=f"content-host-setup-{service_id}",
                    )

    async def _run_setup(self, awaitable: Any) -> None:
        """Drive ``setup``'s awaitable; swallow cancellation cleanly.

        Errors from the awaitable propagate out — the asyncio task
        result captures them so a test or supervisor can inspect them
        if needed. Pane VMs already surface user-visible failure
        states through their reactive ``state`` property, so the host
        doesn't need to publish anything extra here.
        """
        try:
            await awaitable
        except asyncio.CancelledError:
            return
        finally:
            # If the task is finishing on its own (not cancelled by a
            # later ``set_content``), clear the reference so the next
            # ``_cancel_pending_setup`` is a no-op.
            if self._setup_task is not None and self._setup_task.done():
                self._setup_task = None

    def _cancel_pending_setup(self) -> None:
        task = self._setup_task
        self._setup_task = None
        if task is None or task.done():
            return
        task.cancel()


__all__ = ["ContentHostVM"]
