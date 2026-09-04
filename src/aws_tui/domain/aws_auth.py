"""Shared botocore authentication failure taxonomy."""

from __future__ import annotations

import botocore.exceptions

AWS_AUTH_ERROR_CODES = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidAccessKeyId",
        "InvalidClientTokenId",
        "SignatureDoesNotMatch",
        "TokenRefreshRequired",
        "UnrecognizedClientException",
    }
)

AWS_CREDENTIAL_EXCEPTIONS = (
    botocore.exceptions.NoCredentialsError,
    botocore.exceptions.PartialCredentialsError,
    botocore.exceptions.ProfileNotFound,
    botocore.exceptions.TokenRetrievalError,
    botocore.exceptions.CredentialRetrievalError,
    botocore.exceptions.SSOTokenLoadError,
    botocore.exceptions.UnauthorizedSSOTokenError,
    botocore.exceptions.NoAuthTokenError,
)

__all__ = ["AWS_AUTH_ERROR_CODES", "AWS_CREDENTIAL_EXCEPTIONS"]
