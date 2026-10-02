"""Pure, secret-safe guidance for active-source credential recovery."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import StrEnum

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderUnreachableError,
)
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection


class RecoveryFailureKind(StrEnum):
    """Stable user-facing credential recovery failure categories."""

    SSO_EXPIRED = "sso_expired"
    CREDENTIALS_MISSING = "credentials_missing"
    ACCESS_DENIED = "access_denied"
    NETWORK = "network"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class RecoveryGuidance:
    """Fixed guidance safe to display without exposing provider details."""

    kind: RecoveryFailureKind
    message: str
    retryable: bool


def classify_recovery_failure(
    connection: Connection,
    *,
    token_state: TokenState | None = None,
    error: BaseException | None = None,
) -> RecoveryGuidance:
    """Classify typed recovery signals without interpolating error details."""
    if isinstance(error, PermissionDeniedError):
        return RecoveryGuidance(
            kind=RecoveryFailureKind.ACCESS_DENIED,
            message=(
                "Credentials are valid, but this identity does not have permission "
                "to read the active source. Update IAM or resource permissions, "
                "then press a to retry."
            ),
            retryable=True,
        )
    if isinstance(error, ProviderUnreachableError):
        return RecoveryGuidance(
            kind=RecoveryFailureKind.NETWORK,
            message=(
                "The active source could not be reached. Check network, VPN, DNS, "
                "TLS, and the configured endpoint, then press a to retry."
            ),
            retryable=True,
        )
    if token_state is TokenState.EXPIRED:
        profile = shlex.quote(connection.profile or connection.name)
        return RecoveryGuidance(
            kind=RecoveryFailureKind.SSO_EXPIRED,
            message=(
                "AWS SSO session expired. Run `aws sso login --profile "
                f"{profile}` externally, then press a to retry."
            ),
            retryable=True,
        )
    if token_state is TokenState.MISSING or isinstance(error, AuthRequiredError):
        profile = shlex.quote(connection.profile or connection.name)
        return RecoveryGuidance(
            kind=RecoveryFailureKind.CREDENTIALS_MISSING,
            message=(
                "AWS credentials are unavailable. Configure shared credentials, "
                "credential_process, environment or role credentials; verify with "
                f"`aws sts get-caller-identity --profile {profile}`; then press a "
                "to retry."
            ),
            retryable=True,
        )
    return RecoveryGuidance(
        kind=RecoveryFailureKind.OTHER,
        message=(
            "Credential recovery could not verify the active source. Check the "
            "source configuration and logs, then press a to retry."
        ),
        retryable=True,
    )


__all__ = [
    "RecoveryFailureKind",
    "RecoveryGuidance",
    "classify_recovery_failure",
]
