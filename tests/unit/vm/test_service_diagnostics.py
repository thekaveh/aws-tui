"""Service diagnostics publication boundaries."""

from __future__ import annotations

from typing import cast

from vmx import Message, MessageHub

from aws_tui.vm.messages import ServiceOperationFailedMessage
from aws_tui.vm.service_diagnostics import (
    capture_service_diagnostics,
    report_unexpected_service_error,
)


def test_capture_retains_type_only_and_restores_normal_publication() -> None:
    hub = cast("MessageHub[Message]", MessageHub())
    received: list[ServiceOperationFailedMessage] = []
    subscription = hub.messages.subscribe(
        on_next=lambda message: (
            received.append(message) if isinstance(message, ServiceOperationFailedMessage) else None
        )
    )

    with capture_service_diagnostics() as captured:
        report_unexpected_service_error(
            hub,
            service="athena",
            operation="staged_refresh",
            error=RuntimeError("candidate-secret"),
        )
    report_unexpected_service_error(
        hub,
        service="athena",
        operation="live_refresh",
        error=RuntimeError("visible failure"),
    )

    assert [(item.service, item.operation, item.error_type) for item in captured] == [
        ("athena", "staged_refresh", "RuntimeError")
    ]
    assert "candidate-secret" not in repr(captured)
    assert [message.operation for message in received] == ["live_refresh"]
    subscription.dispose()
    hub.dispose()
