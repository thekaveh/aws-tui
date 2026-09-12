"""Shared ``ProviderError`` -> ``PaneState`` mapping for the VM layer.

Every service VM shares the same four-step ladder (``AuthRequiredError`` ->
``AUTH_REQUIRED``, ``ProviderUnreachableError`` -> ``UNREACHABLE``,
``PermissionDeniedError`` -> ``FORBIDDEN``, everything else -> ``ERROR``).
Holding it in one place keeps the contract uniform: a new ``ProviderError``
subclass, or a state shift, has one site to change.

``vm/glue/_errors.py`` and ``vm/emr_serverless/_errors.py`` previously carried
their own copies. They were behaviourally identical -- Glue's extra
``LakeFormationPermissionError`` arm was redundant, because that class derives
from ``PermissionDeniedError`` and so matched the very next arm anyway -- and
``_visible_error_text`` was byte-identical in both plus ``pane_vm``.

``vm/athena/_errors.py`` deliberately does *not* use this. Athena returns
fixed, redacted copy and never exposes exception text, which is a security
divergence, not an oversight.
"""

from __future__ import annotations

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.infra.redaction import redact_text
from aws_tui.vm.file_manager.pane_vm import PaneState


def visible_error_text(exc: BaseException) -> str | None:
    """Redacted, user-presentable text for ``exc``, or ``None`` when empty."""
    text = str(exc)
    return redact_text(text) if text else None


def map_provider_error(exc: ProviderError) -> tuple[PaneState, str | None]:
    """Translate a ``ProviderError`` into ``(PaneState, error_text)``.

    Unrecognised subclasses fall through to ``PaneState.ERROR`` -- the
    forward-compatible default.
    """
    if isinstance(exc, AuthRequiredError):
        return PaneState.AUTH_REQUIRED, visible_error_text(exc)
    if isinstance(exc, ProviderUnreachableError):
        return PaneState.UNREACHABLE, visible_error_text(exc)
    if isinstance(exc, PermissionDeniedError):
        return PaneState.FORBIDDEN, visible_error_text(exc)
    return PaneState.ERROR, visible_error_text(exc)


def map_unexpected_error(exc: BaseException) -> tuple[PaneState, str]:
    """Translate a non-``ProviderError`` escape into a redacted ERROR state."""
    return PaneState.ERROR, redact_text(f"unexpected error: {exc}")


__all__ = ["map_provider_error", "map_unexpected_error", "visible_error_text"]
