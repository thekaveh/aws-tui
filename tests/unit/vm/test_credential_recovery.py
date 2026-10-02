"""Credential recovery guidance is fixed, actionable, and secret-safe."""

from __future__ import annotations

import pytest

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderUnreachableError,
)
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.credential_recovery import (
    RecoveryFailureKind,
    classify_recovery_failure,
)


def _aws_connection() -> Connection:
    return Connection(
        name="engineering",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="engineering admin",
    )


@pytest.mark.parametrize(
    ("token_state", "error", "expected_kind", "expected_message"),
    [
        (
            TokenState.EXPIRED,
            None,
            RecoveryFailureKind.SSO_EXPIRED,
            "AWS SSO session expired. Run `aws sso login --profile "
            "'engineering admin'` externally, then press a to retry.",
        ),
        (
            TokenState.MISSING,
            None,
            RecoveryFailureKind.CREDENTIALS_MISSING,
            "AWS credentials are unavailable. Configure shared credentials, "
            "credential_process, environment or role credentials; verify with "
            "`aws sts get-caller-identity --profile 'engineering admin'`; then "
            "press a to retry.",
        ),
        (
            None,
            AuthRequiredError("expired token SECRET"),
            RecoveryFailureKind.CREDENTIALS_MISSING,
            "AWS credentials are unavailable. Configure shared credentials, "
            "credential_process, environment or role credentials; verify with "
            "`aws sts get-caller-identity --profile 'engineering admin'`; then "
            "press a to retry.",
        ),
        (
            TokenState.CONNECTED,
            PermissionDeniedError("denied for AKIA-SECRET"),
            RecoveryFailureKind.ACCESS_DENIED,
            "Credentials are valid, but this identity does not have permission "
            "to read the active source. Update IAM or resource permissions, "
            "then press a to retry.",
        ),
        (
            TokenState.CONNECTED,
            ProviderUnreachableError("https://user:pw@example.test?token=SECRET"),
            RecoveryFailureKind.NETWORK,
            "The active source could not be reached. Check network, VPN, DNS, "
            "TLS, and the configured endpoint, then press a to retry.",
        ),
        (
            TokenState.CONNECTED,
            RuntimeError("secret=SECRET endpoint=https://user:pw@example.test?q=1"),
            RecoveryFailureKind.OTHER,
            "Credential recovery could not verify the active source. Check the "
            "source configuration and logs, then press a to retry.",
        ),
    ],
)
def test_classifies_failures_with_fixed_guidance(
    token_state: TokenState | None,
    error: BaseException | None,
    expected_kind: RecoveryFailureKind,
    expected_message: str,
) -> None:
    guidance = classify_recovery_failure(_aws_connection(), token_state=token_state, error=error)

    assert guidance.kind is expected_kind
    assert guidance.retryable is True
    assert guidance.message == expected_message


def test_typed_provider_error_takes_precedence_over_connected_probe() -> None:
    guidance = classify_recovery_failure(
        _aws_connection(),
        token_state=TokenState.CONNECTED,
        error=PermissionDeniedError("not authorized"),
    )

    assert guidance.kind is RecoveryFailureKind.ACCESS_DENIED


def test_guidance_never_includes_exception_or_endpoint_secrets() -> None:
    secret_values = (
        "AKIA-SECRET",
        "SECRET-ACCESS-KEY",
        "SESSION-TOKEN",
        "user:pw",
        "query-secret",
        "exception-secret",
    )
    connection = Connection(
        name="private-store",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="https://user:pw@example.test?token=query-secret",
        access_key_id="AKIA-SECRET",
        secret_access_key="SECRET-ACCESS-KEY",
        session_token="SESSION-TOKEN",
    )

    guidance = classify_recovery_failure(
        connection,
        error=RuntimeError("exception-secret https://user:pw@example.test"),
    )

    assert guidance.kind is RecoveryFailureKind.OTHER
    assert all(value not in guidance.message for value in secret_values)
