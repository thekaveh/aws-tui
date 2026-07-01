"""Tests for the ``_auth_error`` helper extracted in the Pass-1
refactor of ``s3_fs.py``.

The helper centralises the auth-error message that used to be
duplicated 8x verbatim across ``S3FS`` operations. Pinning it
here ensures the recovery hint stays stable across versions —
the message is what the user actually sees on auth failure, so
silent drift is a UX regression."""

from __future__ import annotations

import pytest
from botocore.exceptions import (
    CredentialRetrievalError,
    NoCredentialsError,
    ProfileNotFound,
    TokenRetrievalError,
)

from aws_tui.domain.filesystem import AuthRequiredError, PathRef
from aws_tui.domain.s3_fs import _AUTH_HINT, S3FS, _auth_error


def test_auth_error_wraps_no_credentials() -> None:
    exc = NoCredentialsError()
    mapped = _auth_error(exc)
    assert isinstance(mapped, AuthRequiredError)
    msg = str(mapped)
    assert "AWS auth:" in msg
    assert _AUTH_HINT in msg


def test_auth_error_wraps_profile_not_found() -> None:
    exc = ProfileNotFound(profile="kaveh-dev")
    mapped = _auth_error(exc)
    assert isinstance(mapped, AuthRequiredError)
    assert "kaveh-dev" in str(mapped)
    assert _AUTH_HINT in str(mapped)


def test_auth_error_wraps_token_retrieval_error() -> None:
    """SSO token refresh failures must surface as the same
    ``AuthRequiredError`` + recovery hint the other auth-error
    types produce. Without this, an expired SSO session bypasses the
    auth-error mapping in ``S3FS`` and the user sees a raw
    ``TokenRetrievalError`` traceback through the pane's UNREACHABLE
    placeholder — losing the ``aws sso login`` recovery hint."""
    exc = TokenRetrievalError(provider="sso", error_msg="token expired")
    mapped = _auth_error(exc)
    assert isinstance(mapped, AuthRequiredError)
    assert _AUTH_HINT in str(mapped)


def test_auth_error_masks_credential_process_stderr() -> None:
    exc = CredentialRetrievalError(
        provider="credential-process",
        error_msg="SECRET_TOKEN=leaked",
    )
    mapped = _auth_error(exc)
    assert isinstance(mapped, AuthRequiredError)
    msg = str(mapped)
    assert "credential process failed" in msg
    assert "SECRET_TOKEN" not in msg
    assert "leaked" not in msg
    assert _AUTH_HINT in msg


class _CredentialProcessFailureClient:
    async def __aenter__(self) -> _CredentialProcessFailureClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def list_buckets(self) -> object:
        raise CredentialRetrievalError(
            provider="credential-process",
            error_msg="SECRET_TOKEN=leaked",
        )


class _CredentialProcessFailureSession:
    def client(self, service_name: str, **kwargs: object) -> _CredentialProcessFailureClient:
        assert service_name == "s3"
        assert "config" in kwargs
        return _CredentialProcessFailureClient()


@pytest.mark.asyncio
async def test_s3_list_masks_credential_process_stderr() -> None:
    fs = S3FS(
        session=_CredentialProcessFailureSession(),  # type: ignore[arg-type]
        bucket=None,
    )

    with pytest.raises(AuthRequiredError) as exc_info:
        await fs.list(PathRef(()))

    msg = str(exc_info.value)
    assert "credential process failed" in msg
    assert "SECRET_TOKEN" not in msg
    assert "leaked" not in msg
    assert _AUTH_HINT in msg


def test_auth_hint_lists_the_three_recovery_paths() -> None:
    """The hint must mention the three documented recovery paths so
    the user actually has a way out: ``aws sso login`` refresh,
    ``credential_process`` / ``source_profile`` inspection, and the
    explicit env-var fallback."""
    assert "aws sso login" in _AUTH_HINT
    assert "credential_process" in _AUTH_HINT
    assert "AWS_ACCESS_KEY_ID" in _AUTH_HINT
    assert "AWS_SESSION_TOKEN" in _AUTH_HINT
