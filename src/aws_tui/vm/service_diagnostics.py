"""Shared publication boundary for recovered service exceptions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from vmx import Message, MessageHub

from aws_tui.vm.messages import ServiceOperationFailedMessage


@dataclass(frozen=True, slots=True)
class CapturedServiceDiagnostic:
    """Secret-free identity of an unexpected staged service failure."""

    service: str
    operation: str
    error_type: str


_DIAGNOSTIC_CAPTURE: ContextVar[list[CapturedServiceDiagnostic] | None] = ContextVar(
    "aws_tui_service_diagnostic_capture",
    default=None,
)


@contextmanager
def capture_service_diagnostics() -> Iterator[list[CapturedServiceDiagnostic]]:
    """Capture only type metadata while an off-screen candidate is staged."""
    captured: list[CapturedServiceDiagnostic] = []
    token = _DIAGNOSTIC_CAPTURE.set(captured)
    try:
        yield captured
    finally:
        _DIAGNOSTIC_CAPTURE.reset(token)


def report_unexpected_service_error(
    hub: MessageHub[Message],
    *,
    service: str,
    operation: str,
    error: BaseException,
    source: str | None = None,
    region: str | None = None,
) -> None:
    """Publish one redacted diagnostic for an exception recovered into VM state."""
    captured = _DIAGNOSTIC_CAPTURE.get()
    if captured is not None:
        captured.append(
            CapturedServiceDiagnostic(
                service=service,
                operation=operation,
                error_type=type(error).__name__,
            )
        )
        return
    hub.send(
        ServiceOperationFailedMessage.from_error(
            service=service,
            operation=operation,
            error=error,
            source=source,
            region=region,
        )
    )


__all__ = [
    "CapturedServiceDiagnostic",
    "capture_service_diagnostics",
    "report_unexpected_service_error",
]
