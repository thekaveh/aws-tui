"""Direct unit tests for the shared ``map_provider_error`` helper.

Pass-2 H-1 (test-review): the helper is a small pure function used by
all three EMR VMs (``applications_vm``, ``job_runs_vm``,
``job_run_detail_vm``). Its four branches and the ``str(exc) or None``
empty-message handling were only exercised indirectly via the VM
refresh tests. A dedicated table-driven test against the helper itself
catches a refactor that drops a branch or flips the ``or None``
semantics.
"""

from __future__ import annotations

import pytest

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.vm.emr_serverless._errors import map_provider_error
from aws_tui.vm.file_manager.pane_vm import PaneState


@pytest.mark.parametrize(
    ("exc_cls", "expected_state"),
    [
        (AuthRequiredError, PaneState.AUTH_REQUIRED),
        (ProviderUnreachableError, PaneState.UNREACHABLE),
        (PermissionDeniedError, PaneState.FORBIDDEN),
    ],
)
def test_known_provider_subclasses_map_to_expected_state(
    exc_cls: type[ProviderError], expected_state: PaneState
) -> None:
    """Each of the three recognised subclasses lands on the right
    ``PaneState``. The non-empty message is propagated as
    ``error_text``."""
    state, error_text = map_provider_error(exc_cls("boom"))
    assert state is expected_state
    assert error_text == "boom"


def test_catch_all_provider_error_maps_to_error_state() -> None:
    """A bare ``ProviderError`` (or any unrecognised subclass) falls
    through to ``PaneState.ERROR``. This is the forward-compat path
    — the helper must NEVER raise an unhandled type error if a new
    subclass is added to the ``filesystem`` module before the helper
    is updated."""

    class _NovelProviderError(ProviderError):
        pass

    state, error_text = map_provider_error(_NovelProviderError("unknown"))
    assert state is PaneState.ERROR
    assert error_text == "unknown"


def test_catch_all_bare_provider_error_maps_to_error_state() -> None:
    """A bare ``ProviderError`` instance (not a subclass) also lands
    on ``PaneState.ERROR`` via the fall-through path."""
    state, error_text = map_provider_error(ProviderError("kaboom"))
    assert state is PaneState.ERROR
    assert error_text == "kaboom"


@pytest.mark.parametrize(
    ("exc_cls", "expected_state"),
    [
        (AuthRequiredError, PaneState.AUTH_REQUIRED),
        (ProviderUnreachableError, PaneState.UNREACHABLE),
        (PermissionDeniedError, PaneState.FORBIDDEN),
        (ProviderError, PaneState.ERROR),
    ],
)
def test_empty_message_returns_none_error_text(
    exc_cls: type[ProviderError], expected_state: PaneState
) -> None:
    """An exception with ``str(exc) == ""`` returns ``None`` as
    ``error_text`` so the placeholder renders the default copy
    (e.g. "endpoint unreachable — press r to retry") rather than
    a blank string. This is the ``str(exc) or None`` contract."""
    state, error_text = map_provider_error(exc_cls(""))
    assert state is expected_state
    assert error_text is None


@pytest.mark.parametrize(
    "exc_cls",
    [AuthRequiredError, ProviderUnreachableError, PermissionDeniedError, ProviderError],
)
def test_non_empty_message_is_returned_verbatim(exc_cls: type[ProviderError]) -> None:
    """Non-empty messages pass through as ``error_text`` unchanged —
    the helper does NOT prefix or wrap them; downstream placeholder
    rendering picks the copy. Verified across all four state lanes
    so a refactor that introduces accidental wrapping is caught."""
    msg = "endpoint dns lookup failed: nodename nor servname provided"
    state, error_text = map_provider_error(exc_cls(msg))
    assert error_text == msg
    assert state is not None


def test_provider_error_message_redacts_endpoint_secrets() -> None:
    state, error_text = map_provider_error(
        ProviderUnreachableError(
            "failed to reach https://user:pass@example.com/bucket?X-Amz-Signature=sig token=abc123"
        )
    )

    assert state is PaneState.UNREACHABLE
    assert error_text is not None
    assert "user" not in error_text
    assert "pass" not in error_text
    assert "X-Amz-Signature" not in error_text
    assert "sig" not in error_text
    assert "abc123" not in error_text
    assert "token=[REDACTED]" in error_text
