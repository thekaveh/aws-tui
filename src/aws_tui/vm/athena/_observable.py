from __future__ import annotations

import logging
from typing import Generic, TypeVar

import reactivex as rx
from reactivex import abc
from reactivex.subject import Subject

T = TypeVar("T")

_logger = logging.getLogger(__name__)


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

        return self._subject.subscribe(
            on_next=on_next,
            on_error=observer.on_error,
            on_completed=observer.on_completed,
            scheduler=scheduler,
        )

    def on_next(self, value: T) -> None:
        self._subject.on_next(value)

    def on_completed(self) -> None:
        self._subject.on_completed()

    def dispose(self) -> None:
        self._subject.dispose()


__all__ = ["ObserverSafeSubject"]
