from __future__ import annotations

from aws_tui.vm.operation_owner import OperationOwner, OperationSuperseded


class GlueOperationSuperseded(OperationSuperseded):
    """An owned provider operation was invalidated by a lifecycle transition."""


class GlueOperationOwner(OperationOwner):
    """Generic operation ownership with Glue's public stale-work exception."""

    def __init__(self) -> None:
        super().__init__(superseded_error=GlueOperationSuperseded)


__all__ = [
    "GlueOperationOwner",
    "GlueOperationSuperseded",
]
