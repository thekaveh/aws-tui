from __future__ import annotations

from collections.abc import Callable

import botocore.exceptions
import pytest

from aws_tui.domain.athena import map_athena_error
from aws_tui.domain.emr_logs import map_boto_error as map_emr_log_error
from aws_tui.domain.emr_serverless import map_boto_error as map_emr_error
from aws_tui.domain.filesystem import AuthRequiredError, ProviderError
from aws_tui.domain.glue import map_glue_error
from aws_tui.domain.s3_fs import _map_client_error


def _client_error(code: str) -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": "authentication failed"}},
        "TestOperation",
    )


AUTH_MAPPERS: tuple[tuple[str, Callable[[BaseException], ProviderError | None]], ...] = (
    ("athena", map_athena_error),
    ("glue", map_glue_error),
    ("emr", map_emr_error),
    ("emr-logs", map_emr_log_error),
    ("s3", lambda exc: _map_client_error(exc, "bucket")),  # type: ignore[arg-type]
)


@pytest.mark.parametrize(
    "code",
    [
        "ExpiredToken",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "TokenRefreshRequired",
    ],
)
@pytest.mark.parametrize(("service", "mapper"), AUTH_MAPPERS)
def test_service_client_error_auth_taxonomy_is_in_parity(
    service: str,
    mapper: Callable[[BaseException], ProviderError | None],
    code: str,
) -> None:
    assert isinstance(mapper(_client_error(code)), AuthRequiredError), service


@pytest.mark.parametrize(("service", "mapper"), AUTH_MAPPERS[:-1])
def test_service_credential_exception_auth_taxonomy_is_in_parity(
    service: str,
    mapper: Callable[[BaseException], ProviderError | None],
) -> None:
    assert isinstance(mapper(botocore.exceptions.NoAuthTokenError()), AuthRequiredError), service
