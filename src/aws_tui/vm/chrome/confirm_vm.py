"""ConfirmationVM — modal confirm overlay.

This is a thin facade over VMx ``ModalVM``. VMx owns result completion while
this class owns confirm-specific request data, commands, and hub events.

The async ``ask(request)`` opens the modal and returns the user's choice as
a boolean. Only one ``ask`` may be in flight at a time.
"""

from __future__ import annotations

from dataclasses import dataclass

from vmx import ComponentVM, Message, MessageHub, ModalVM, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


@dataclass(frozen=True, slots=True)
class ConfirmPath:
    """A single labeled path block — rendered as bold label + bordered path."""

    label: str
    path: str


@dataclass(frozen=True, slots=True)
class ConfirmRequest:
    """Immutable description of a confirmation prompt.

    The view renders ``paths`` first (each as a bold label + bordered
    Static showing the path), then ``body_lines`` as plain rows beneath.
    The VM applies no formatting of its own.
    """

    title: str
    paths: tuple[ConfirmPath, ...] = ()
    body_lines: tuple[str, ...] = ()
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

        self._request: ConfirmRequest | None = None
        self._is_open: bool = False
        self._modal: ModalVM[bool] | None = None
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
        if self._modal is not None:
            self._modal.dispose()
        self._confirm_command.dispose()
        self._cancel_command.dispose()
        self._inner.dispose()

    # ── Async API ──────────────────────────────────────────────────────────

    async def ask(self, request: ConfirmRequest) -> bool:
        """Open the modal with ``request`` and await the user's choice."""
        if self._is_open or self._modal is not None:
            raise RuntimeError("confirmation is already open")
        if self._disposed:
            raise RuntimeError("confirmation has been disposed")
        self._modal = ModalVM(False)
        self._set_request(request)
        self._set_open(True)
        try:
            return await self._modal.wait_result()
        finally:
            # Always clear UI state on resolution.
            self._modal = None
            self._set_open(False)
            self._set_request(None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _resolve(self, value: bool) -> None:
        if self._modal is None or self._modal.is_dismissed:
            return
        self._modal.dismiss(value)

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


__all__ = ["ConfirmPath", "ConfirmRequest", "ConfirmationVM"]
