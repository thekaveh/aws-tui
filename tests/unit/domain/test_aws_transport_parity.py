"""All AWS-backed providers share one transport-failure taxonomy."""

from __future__ import annotations

import botocore.exceptions
import pytest

from aws_tui.domain import athena, emr_serverless, glue, s3_fs
from aws_tui.domain.aws_transport import AWS_TRANSPORT_EXCEPTIONS
from aws_tui.domain.filesystem import ProviderUnreachableError


def _transport_errors() -> tuple[BaseException, ...]:
    return (
        botocore.exceptions.EndpointResolutionError(msg="cannot resolve endpoint"),
        botocore.exceptions.ResponseStreamingError(error="stream reset"),
        botocore.exceptions.IncompleteReadError(actual_bytes=1, expected_bytes=2),
    )


def test_all_aws_providers_use_the_canonical_transport_tuple() -> None:
    assert athena._TRANSPORT_EXCEPTIONS is AWS_TRANSPORT_EXCEPTIONS
    assert glue._TRANSPORT_EXCEPTIONS is AWS_TRANSPORT_EXCEPTIONS
    assert emr_serverless._TRANSPORT_FAILURE_EXCEPTIONS is AWS_TRANSPORT_EXCEPTIONS
    assert s3_fs._TRANSPORT_FAILURE_EXCEPTIONS is AWS_TRANSPORT_EXCEPTIONS


@pytest.mark.parametrize("error", _transport_errors())
@pytest.mark.parametrize(
    "mapper",
    [athena.map_athena_error, glue.map_glue_error, emr_serverless.map_boto_error],
)
def test_transport_failures_map_to_unreachable(
    mapper: object,
    error: BaseException,
) -> None:
    mapped = mapper(error)  # type: ignore[operator]
    assert isinstance(mapped, ProviderUnreachableError)
