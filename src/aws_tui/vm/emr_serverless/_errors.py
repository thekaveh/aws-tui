"""Shared error→PaneState mapping for the three EMR VMs.

Each ``refresh()`` shares the identical four-step ladder
(``AuthRequiredError → AUTH_REQUIRED``, etc.). Holding the map in
one place keeps the contract uniform — if a new ``ProviderError``
subclass is added (or a state shifts), one site is the source of
truth."""

from __future__ import annotations

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.infra.redaction import redact_text
from aws_tui.vm.file_manager.pane_vm import PaneState


def _visible_error_text(exc: BaseException) -> str | None:
    text = str(exc)
    return redact_text(text) if text else None


def map_provider_error(exc: ProviderError) -> tuple[PaneState, str | None]:
    """Translate a ``ProviderError`` to the (PaneState, error_text)
    tuple every EMR VM uses on its catch-all paths.

    Returns ``(PaneState.ERROR, str(exc))`` for any unrecognised
    ``ProviderError`` subclass — the fall-through default, kept for
    forward compatibility."""
    if isinstance(exc, AuthRequiredError):
        return PaneState.AUTH_REQUIRED, _visible_error_text(exc)
    if isinstance(exc, ProviderUnreachableError):
        return PaneState.UNREACHABLE, _visible_error_text(exc)
    if isinstance(exc, PermissionDeniedError):
        return PaneState.FORBIDDEN, _visible_error_text(exc)
    return PaneState.ERROR, _visible_error_text(exc)


__all__ = ["map_provider_error"]
