"""ConfirmationVM — modal confirm overlay.

This is a thin shim around an :class:`asyncio.Future` rather than VMx's
``vmx.notifications.ConfirmationVM`` — the notifications subpackage's
notification-hub indirection is overkill for our single-modal use case and
would force callers to wire a separate ``INotificationHub``.

The async ``ask(request)`` opens the modal and returns the user's choice as
a boolean. Only one ``ask`` may be in flight at a time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


@dataclass(frozen=True, slots=True)
class ConfirmRequest:
    """Immutable description of a confirmation prompt.

    The view layer renders ``body_lines`` exactly as supplied; the VM adds
    no formatting.
    """

    title: str
    body_lines: tuple[str, ...]
    confirm_label: str = "OK"
    cancel_label: str = "Cancel"
    danger: bool = False


class ConfirmationVM:
    """Single-modal confirm overlay.

    Properties: :attr:`is_open`, :attr:`request`.
    Commands: :attr:`confirm_command`, :attr:`cancel_command`.
    Async: :meth:`ask`.
    """

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        self._request: ConfirmRequest | None = None
        self._is_open: bool = False
        self._future: asyncio.Future[bool] | None = None
        self._disposed: bool = False

        self._inner: ComponentVM = (
            ComponentVM.builder().name("confirmation").services(hub, dispatcher).build()
        )
        self._confirm_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._is_open)
            .task(lambda: self._resolve(True))
            .build()
        )
        self._cancel_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._is_open)
            .task(lambda: self._resolve(False))
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def request(self) -> ConfirmRequest | None:
        return self._request

    @property
    def confirm_command(self) -> RelayCommand:
        return self._confirm_command

    @property
    def cancel_command(self) -> RelayCommand:
        return self._cancel_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        # If we tear down with a pending ask, resolve it as False so the
        # awaiter unblocks rather than leaks.
        if self._future is not None and not self._future.done():
            self._future.set_result(False)
        self._confirm_command.dispose()
        self._cancel_command.dispose()
        self._inner.dispose()

    # ── Async API ──────────────────────────────────────────────────────────

    async def ask(self, request: ConfirmRequest) -> bool:
        """Open the modal with ``request`` and await the user's choice."""
        if self._is_open or self._future is not None:
            raise RuntimeError("confirmation is already open")
        if self._disposed:
            raise RuntimeError("confirmation has been disposed")
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self._set_request(request)
        self._set_open(True)
        try:
            return await self._future
        finally:
            # Always clear UI state on resolution.
            self._future = None
            self._set_open(False)
            self._set_request(None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _resolve(self, value: bool) -> None:
        if self._future is None or self._future.done():
            return
        self._future.set_result(value)

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_open"))

    def _set_request(self, value: ConfirmRequest | None) -> None:
        if self._request is value:
            return
        self._request = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "request"))


__all__ = ["ConfirmRequest", "ConfirmationVM"]
