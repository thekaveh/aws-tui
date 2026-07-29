from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import Generic, TypeVar

import anyio
import reactivex as rx
from reactivex import abc
from reactivex.subject import Subject
from vmx import Message, MessageHub

T = TypeVar("T")

_logger = logging.getLogger(__name__)
_vmx_hub_logger = logging.getLogger("vmx.services.message_hub")
_is_value_free_hub_send: ContextVar[bool] = ContextVar(
    "athena_value_free_hub_send",
    default=False,
)


class _ValueFreeHubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if (
            _is_value_free_hub_send.get()
            and record.name == _vmx_hub_logger.name
            and str(record.msg).startswith("MessageHub subscriber raised")
        ):
            record.msg = "Athena MessageHub subscriber raised; subscriber isolated"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


_vmx_hub_logger.addFilter(_ValueFreeHubFilter())


def _is_callback_cancellation(error: BaseException) -> bool:
    if isinstance(error, asyncio.CancelledError):
        return True
    try:
        cancellation_type = anyio.get_cancelled_exc_class()
    except anyio.NoEventLoopError:
        return False
    return isinstance(error, cancellation_type)


class ObserverSafeSubject(rx.Observable[T], Generic[T]):
    """Subject facade that isolates exceptions from each subscriber."""

    def __init__(self) -> None:
        self._subject: Subject[T] = Subject()
        super().__init__(self._subscribe_safely)

    @property
    def observable(self) -> rx.Observable[T]:
        return self

    def _subscribe_safely(
        self,
        observer: abc.ObserverBase[T],
        scheduler: abc.SchedulerBase | None = None,
    ) -> abc.DisposableBase:
        def on_next(value: T) -> None:
            try:
                observer.on_next(value)
            except Exception:
                _logger.error("Athena property observer raised; subscriber isolated")

        def on_error(error: Exception) -> None:
            try:
                observer.on_error(error)
            except Exception:
                _logger.error("Athena property observer raised; subscriber isolated")

        def on_completed() -> None:
            try:
                observer.on_completed()
            except Exception:
                _logger.error("Athena property observer raised; subscriber isolated")
            except BaseException as error:
                if not _is_callback_cancellation(error):
                    raise
                _logger.error("Athena property observer raised; subscriber isolated")

        return self._subject.subscribe(
            on_next=on_next,
            on_error=on_error,
            on_completed=on_completed,
            scheduler=scheduler,
        )

    def on_next(self, value: T) -> None:
        self._subject.on_next(value)

    def on_completed(self) -> None:
        self._subject.on_completed()

    def on_error(self, error: Exception) -> None:
        self._subject.on_error(error)

    def dispose(self) -> None:
        self._subject.dispose()


def send_value_free(hub: MessageHub[Message], message: Message) -> None:
    """Publish while redacting VMx subscriber tracebacks from this synchronous send."""
    token = _is_value_free_hub_send.set(True)
    try:
        hub.send(message)
    finally:
        _is_value_free_hub_send.reset(token)


__all__ = ["ObserverSafeSubject", "send_value_free"]
