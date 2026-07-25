from __future__ import annotations

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.domain.glue import LakeFormationPermissionError
from aws_tui.infra.redaction import redact_text
from aws_tui.vm.file_manager.pane_vm import PaneState


def _visible_error_text(exc: BaseException) -> str | None:
    text = str(exc)
    return redact_text(text) if text else None


def map_provider_error(exc: ProviderError) -> tuple[PaneState, str | None]:
    if isinstance(exc, AuthRequiredError):
        return PaneState.AUTH_REQUIRED, _visible_error_text(exc)
    if isinstance(exc, ProviderUnreachableError):
        return PaneState.UNREACHABLE, _visible_error_text(exc)
    if isinstance(exc, LakeFormationPermissionError | PermissionDeniedError):
        return PaneState.FORBIDDEN, _visible_error_text(exc)
    return PaneState.ERROR, _visible_error_text(exc)


__all__ = ["map_provider_error"]
