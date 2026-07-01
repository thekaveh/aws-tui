"""ToastVM — single toast facade over a VMx ``ComponentVMOf[ToastModel]``.

A toast carries an immutable :class:`ToastModel` (text + level + sticky flag +
optional action). The :class:`ToastStackVM` owns the collection and
schedules auto-dismiss timers; this module only models the individual
notification.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


class ToastLevel(StrEnum):
    """Severity tier used by the view layer to pick the toast tint."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToastModel:
    """Immutable description of a toast.

    Parameters
    ----------
    id:
        Stable identifier used by callers to dismiss specific toasts and to
        dedupe re-raises (the stack tolerates duplicates — caller dedupes).
    text:
        Human-readable message.
    level:
        Toast severity tier.
    sticky:
        When True the toast stays until explicitly dismissed. When False the
        stack auto-dismisses it after ``timeout_seconds`` elapses.
    timeout_seconds:
        Auto-dismiss delay for non-sticky toasts. ``None`` is treated as
        "stay forever" (effectively sticky) but is a programmer error for a
        non-sticky toast and the stack does nothing in that case.
    action_label:
        Optional inline action label (e.g. "authenticate").
    action_action:
        Action id resolved by :class:`KeymapStore` for hot-key invocation.
    """

    id: str
    text: str
    level: ToastLevel
    sticky: bool
    timeout_seconds: float | None
    action_label: str | None
    action_action: str | None


class ToastVM:
    """Facade for a single toast notification.

    Wraps a VMx ``ComponentVMOf[ToastModel]`` so the view layer can bind to
    ``model``-property changes and to the ``dismiss_command``.
    """

    def __init__(
        self,
        model: ToastModel,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        on_dismiss: Callable[[ToastVM], None] | None = None,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._is_dismissed: bool = False
        self._on_dismiss: Callable[[ToastVM], None] | None = on_dismiss

        self._inner: ComponentVMOf[ToastModel] = (
            ComponentVMOf[ToastModel]
            .builder()
            .name(f"toast.{model.id}")
            .model(model)
            .services(hub, dispatcher)
            .build()
        )
        self._dismiss_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: not self._is_dismissed)
            .task(self._dismiss)
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def model(self) -> ToastModel:
        return self._inner.model

    @property
    def is_dismissed(self) -> bool:
        return self._is_dismissed

    @property
    def dismiss_command(self) -> RelayCommand:
        return self._dismiss_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def inner(self) -> ComponentVMOf[ToastModel]:
        """Underlying VMx component. ``ToastStackVM`` uses this to
        compose a parent ``CompositeVM`` over the live toasts; tests and
        other VM facades access it the same way (matches the public
        ``inner`` accessor on ``EntryVM`` / ``TransferVM`` / ``PaneVM``).
        """
        return self._inner

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._dismiss_command.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _dismiss(self) -> None:
        if self._is_dismissed:
            return
        self._is_dismissed = True
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_dismissed"))
        if self._on_dismiss is not None:
            self._on_dismiss(self)


__all__ = ["ToastLevel", "ToastModel", "ToastVM"]
