"""Shared publication boundary for recovered service exceptions."""

from __future__ import annotations

from vmx import Message, MessageHub

from aws_tui.vm.messages import ServiceOperationFailedMessage


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
    hub.send(
        ServiceOperationFailedMessage.from_error(
            service=service,
            operation=operation,
            error=error,
            source=source,
            region=region,
        )
    )


__all__ = ["report_unexpected_service_error"]
