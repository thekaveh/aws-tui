"""Direct unit tests for Athena's ``map_provider_error``.

Modelled on ``tests/unit/vm/emr_serverless/test_errors.py``. The EMR helper has
a dedicated table-driven test and scored 0/12 mutant survivors; Athena's had
none, and a mutation sweep found every branch here survivable — including
``AuthRequiredError``, the only one returning a non-``ERROR`` state
(``AUTH_REQUIRED``), which is what drives the re-auth affordance. Deleting a
branch merely degraded its message to the caller's generic fallback, which no
VM test asserted.

Each row pins both halves of the return: the state AND the exact copy. The copy
matters because the module's contract is to describe the failure without echoing
sensitive exception text — every message here is a constant, never ``str(exc)``.
"""

from __future__ import annotations

import pytest

from aws_tui.domain.athena import (
    QueryContextRejectedError,
    ResultConfigurationRequiredError,
    ResultLocationUnavailableError,
    WorkgroupRejectedError,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.vm.athena._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.file_manager.pane_vm import PaneState

pytestmark = pytest.mark.unit

_FALLBACK = "Athena results request failed"

_SECRET = "s3://private-bucket/secret-prefix/token=ABCDEF"


@pytest.mark.parametrize(
    ("error", "expected_state", "expected_text"),
    [
        (AuthRequiredError(_SECRET), PaneState.AUTH_REQUIRED, "AWS authentication is required"),
        (ProviderUnreachableError(_SECRET), PaneState.UNREACHABLE, "Athena is unreachable"),
        (PermissionDeniedError(_SECRET), PaneState.FORBIDDEN, "Athena access is forbidden"),
        (NotFoundError(_SECRET), PaneState.ERROR, "Athena resource was not found"),
        (ThrottledError(_SECRET), PaneState.ERROR, "Athena request was throttled"),
        (
            ResultConfigurationRequiredError(_SECRET),
            PaneState.ERROR,
            "Athena result configuration is required",
        ),
        (
            ResultLocationUnavailableError(_SECRET),
            PaneState.ERROR,
            "Athena cannot access the workgroup result location",
        ),
        (
            QueryContextRejectedError(_SECRET),
            PaneState.ERROR,
            "Athena rejected the selected query context",
        ),
        (
            WorkgroupRejectedError(_SECRET),
            PaneState.ERROR,
            "Athena rejected the selected workgroup",
        ),
        (ValidationError(_SECRET), PaneState.ERROR, "Athena rejected the request"),
        (ProviderError(_SECRET), PaneState.ERROR, _FALLBACK),
    ],
)
def test_map_provider_error_pins_every_branch(
    error: ProviderError, expected_state: PaneState, expected_text: str
) -> None:
    state, text = map_provider_error(error, fallback=_FALLBACK)

    assert state is expected_state
    assert text == expected_text


@pytest.mark.parametrize(
    "error",
    [
        AuthRequiredError(_SECRET),
        ProviderUnreachableError(_SECRET),
        PermissionDeniedError(_SECRET),
        NotFoundError(_SECRET),
        ThrottledError(_SECRET),
        ValidationError(_SECRET),
        ProviderError(_SECRET),
    ],
)
def test_map_provider_error_never_echoes_the_exception_text(error: ProviderError) -> None:
    """The module docstring promises no sensitive exception text is copied."""
    _state, text = map_provider_error(error, fallback=_FALLBACK)

    assert _SECRET not in text
    assert "private-bucket" not in text
    assert "ABCDEF" not in text


def test_auth_required_is_the_only_non_error_state_that_unblocks_reauth() -> None:
    """``AUTH_REQUIRED`` drives the re-auth affordance; the rest must not."""
    auth_state, _text = map_provider_error(AuthRequiredError("x"), fallback=_FALLBACK)
    assert auth_state is PaneState.AUTH_REQUIRED

    for other in (NotFoundError("x"), ThrottledError("x"), ValidationError("x")):
        state, _ = map_provider_error(other, fallback=_FALLBACK)
        assert state is PaneState.ERROR, other


def test_map_unexpected_error_returns_the_caller_fallback() -> None:
    assert map_unexpected_error(fallback=_FALLBACK) == (PaneState.ERROR, _FALLBACK)
