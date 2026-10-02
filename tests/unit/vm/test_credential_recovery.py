"""Credential recovery guidance is fixed, actionable, and secret-safe."""

from __future__ import annotations

from dataclasses import replace

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
    connection_binding_identity,
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


def test_connection_binding_identity_rejects_routing_changes_but_allows_rotation() -> None:
    original = Connection(
        name="private-store",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        profile="storage",
        endpoint_url="https://one.example.test",
        access_key_id="old-id",
        secret_access_key="old-secret",
        session_token="old-token",
        force_path_style=True,
        verify_tls=False,
    )
    rotated = Connection(
        name=original.name,
        kind=original.kind,
        region=original.region,
        source=original.source,
        profile=original.profile,
        endpoint_url=original.endpoint_url,
        access_key_id="new-id",
        secret_access_key="new-secret",
        session_token="new-token",
        force_path_style=original.force_path_style,
        verify_tls=original.verify_tls,
    )

    assert connection_binding_identity(rotated) == connection_binding_identity(original)
    for changed in (
        replace(original, profile="other"),
        replace(original, endpoint_url="https://two.example.test"),
        replace(original, source="aws-config"),
        replace(original, force_path_style=False),
        replace(original, verify_tls=True),
    ):
        assert connection_binding_identity(changed) != connection_binding_identity(original)


def test_s3_compatible_missing_credentials_uses_provider_appropriate_guidance() -> None:
    connection = Connection(
        name="private-store",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="https://objects.example.test",
    )

    guidance = classify_recovery_failure(connection, token_state=TokenState.MISSING)

    assert guidance.kind is RecoveryFailureKind.CREDENTIALS_MISSING
    assert guidance.message == (
        "S3-compatible credentials are unavailable. Repair the configured keychain, "
        "environment, AWS profile, or static credential source externally, then press "
        "a to retry."
    )
    assert "sts" not in guidance.message


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
