from __future__ import annotations

from aws_tui.domain.athena import ResultConfigurationRequiredError
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState


def map_provider_error(
    exc: ProviderError,
    *,
    fallback: str,
) -> tuple[PaneState, str]:
    """Map provider failures without copying sensitive exception text."""
    if isinstance(exc, AuthRequiredError):
        return PaneState.AUTH_REQUIRED, "AWS authentication is required"
    if isinstance(exc, ProviderUnreachableError):
        return PaneState.UNREACHABLE, "Athena is unreachable"
    if isinstance(exc, PermissionDeniedError):
        return PaneState.FORBIDDEN, "Athena access is forbidden"
    if isinstance(exc, NotFoundError):
        return PaneState.ERROR, "Athena resource was not found"
    if isinstance(exc, ThrottledError):
        return PaneState.ERROR, "Athena request was throttled"
    if isinstance(exc, ResultConfigurationRequiredError):
        return PaneState.ERROR, "Athena result configuration is required"
    if isinstance(exc, ValidationError):
        return PaneState.ERROR, "Athena rejected the request"
    return PaneState.ERROR, fallback


def map_unexpected_error(*, fallback: str) -> tuple[PaneState, str]:
    """Return stable copy for failures outside the domain taxonomy."""
    return PaneState.ERROR, fallback


__all__ = ["map_provider_error", "map_unexpected_error"]
