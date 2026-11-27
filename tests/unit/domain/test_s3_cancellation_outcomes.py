"""Cancellation outcomes of S3 publication and multipart cleanup.

A pending cancel was reported in place of a definite rejection, and a failed multipart
abort replaced a pending cancel with a provider error.
"""

from __future__ import annotations

import asyncio

import pytest
from botocore.exceptions import ClientError

from aws_tui.domain import s3_fs
from aws_tui.domain.filesystem import ProviderError


def _access_denied() -> ClientError:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject")


@pytest.mark.asyncio
async def test_rejection_during_pending_cancellation_stays_a_rejection() -> None:
    """The rejection became a CancelledError, so the upload was reported as unknown."""

    async def rejected() -> None:
        await asyncio.sleep(0)
        raise _access_denied()

    async def publish() -> None:
        task = asyncio.create_task(rejected())
        asyncio.current_task().cancel()  # type: ignore[union-attr]
        await s3_fs._drain_publication_task(task)

    with pytest.raises(ClientError):
        await asyncio.create_task(publish())


@pytest.mark.asyncio
async def test_failed_abort_reports_the_cancel_that_was_pending() -> None:
    """The abort's failure dropped the cancellation flag, so the copy raised a provider error."""

    class _AbortFails:
        async def abort_multipart_upload(self, **_kwargs: object) -> None:
            raise _access_denied()

    async def abort() -> tuple[bool, BaseException | None]:
        asyncio.current_task().cancel()  # type: ignore[union-attr]
        return await s3_fs._abort_multipart_upload(
            _AbortFails(), bucket="b", key="k", upload_id="u"
        )

    cancelled, error = await asyncio.create_task(abort())

    assert cancelled is True
    assert isinstance(error, ClientError)


@pytest.mark.asyncio
async def test_cleanup_failure_is_a_provider_error_without_a_cancel() -> None:
    exc = ProviderError("copy failed")
    error = s3_fs._multipart_cleanup_failure(exc, cleanup_cancelled=False, message="manual abort")
    assert error is not exc
    assert isinstance(error, ProviderError)
    assert str(error) == "manual abort"


@pytest.mark.asyncio
async def test_cleanup_failure_during_a_cancel_stays_a_cancellation() -> None:
    exc = ProviderError("copy failed")
    error = s3_fs._multipart_cleanup_failure(exc, cleanup_cancelled=True, message="manual abort")
    assert isinstance(error, asyncio.CancelledError)
    assert "manual abort" in error.__notes__
