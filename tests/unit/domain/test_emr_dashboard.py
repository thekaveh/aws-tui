"""Private dashboard links and the captured EMR provider request boundary."""

from __future__ import annotations

import traceback
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, Mock, call

import botocore.exceptions
import botocore.session
import pytest
from botocore.validate import validate_parameters

from aws_tui.domain.emr_dashboard import DashboardLink, validate_dashboard_url
from aws_tui.domain.emr_serverless import EMR_BOTO_CONFIG, EmrServerlessClient
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)


class StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "url",
    [
        "https://dashboard.us-east-1.amazonaws.com/path?token=a%2Fb&x=1#fragment",
        "https://service.cn-north-1.amazonaws.com.cn:443/path?x=%20#view",
        "HTTPS://DASHBOARD.eu-central-2.amazonaws.com/",
        "https://x.us-gov-west-1.amazonaws.com/",
        "https://abc-1.amazonaws.com/",
    ],
)
def test_valid_links_preserve_every_character(url):
    assert validate_dashboard_url(url) == url
    link = DashboardLink(url)
    assert link.url == url
    assert repr(link) == "DashboardLink()"
    assert not hasattr(link, "__dict__")
    with pytest.raises(FrozenInstanceError):
        link.url = "replacement"


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        True,
        b"https://x.amazonaws.com",
        StringSubclass("https://x.amazonaws.com"),
        "",
        "https://x.amazonaws.com/" + "x" * 2048,
        "http://x.amazonaws.com",
        "javascript:alert(1)",
        "//x.amazonaws.com",
        "https:///path",
        "https://amazonaws.com",
        "https://amazonaws.com.cn",
        "https://evilamazonaws.com",
        "https://x.amazonaws.com.evil.test",
        "https://x.amazonaws.com.cn.evil.test",
        "https://user@x.amazonaws.com",
        "https://user:pass@x.amazonaws.com",
        "https://x.amazonaws.com:80",
        "https://x.amazonaws.com:444",
        "https://x.amazonaws.com:abc",
        "https://x.amazonaws.com:65536",
        "https://x.amazonaws.com:",
        "https://x.amazonaws.com:+443",
        "https://x.amazonaws.com./",
        "https://-bad.amazonaws.com",
        "https://bad-.amazonaws.com",
        "https://bad_name.amazonaws.com",
        "https://a..amazonaws.com",
        "https://é.amazonaws.com",
        "https://" + "a" * 64 + ".amazonaws.com",
        "https://[::1]",
        "https://127.0.0.1",
        " https://x.amazonaws.com",
        "https://x.amazonaws.com/ ",
        "https://x.amazonaws.com/\t",
        "https://x.amazonaws.com/\n",
        "https://x.amazonaws.com/\x00",
        "https://x.amazonaws.com/\x7f",
        "https://x.amazonaws.com/\u0085",
        "https://x.amazonaws.com/\u00a0",
        "https://x.amazonaws.com\\evil",
        "https://x.amazonaws.com/path%",
        "https://x.amazonaws.com/path%0G",
        "https://x.amazonaws.com/path?token=%x1",
        "https://x.amazonaws.com/#%",
    ],
)
def test_invalid_links_have_fixed_value_free_errors(value):
    with pytest.raises(ValidationError) as error:
        validate_dashboard_url(value)
    assert str(error.value) == "Invalid EMR Serverless dashboard URL"
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    with pytest.raises(ValidationError):
        DashboardLink(value)


def test_maximum_length_link_is_valid():
    prefix = "https://x.amazonaws.com/"
    assert len(validate_dashboard_url(prefix + "x" * (2048 - len(prefix)))) == 2048


def test_invalid_link_is_private_in_trace_and_crash_dump(tmp_path):
    from aws_tui.infra.crash_dump import CrashDump

    parts = ("private-host", "private-path", "private-query", "private-fragment")
    url = f"https://{parts[0]}.invalid/{parts[1]}?{parts[2]}#{parts[3]}"
    with pytest.raises(ValidationError) as error:
        validate_dashboard_url(url)
    rendered = "".join(traceback.format_exception(error.value))
    dump = CrashDump(base_dir=tmp_path).write(exc=error.value).read_text()
    for secret in (url, *parts):
        assert secret not in repr(error.value)
        assert secret not in rendered
        assert secret not in dump


@pytest.fixture
def provider():
    sdk = Mock()
    sdk.get_dashboard_for_job_run = AsyncMock(return_value={"url": "https://x.amazonaws.com/view"})

    @asynccontextmanager
    async def client(*args, **kwargs):
        yield sdk

    session = Mock()
    session.client = Mock(side_effect=client)
    return EmrServerlessClient(session=session, region_name="eu-west-3"), session, sdk


@pytest.mark.parametrize(("attempt", "logs"), [(None, False), (1, False), (3, True)])
async def test_provider_exact_request_uses_captured_region_and_sdk_model(provider, attempt, logs):
    client, session, sdk = provider
    result = await client.get_dashboard_for_job_run(
        "app123", "run456", attempt=attempt, access_system_profile_logs=logs
    )
    assert isinstance(result, DashboardLink)
    assert result.url == "https://x.amazonaws.com/view"
    expected = {"applicationId": "app123", "jobRunId": "run456", "accessSystemProfileLogs": logs}
    if attempt is not None:
        expected["attempt"] = attempt
    sdk.get_dashboard_for_job_run.assert_awaited_once_with(**expected)
    session.client.assert_called_once_with(
        "emr-serverless", region_name="eu-west-3", config=EMR_BOTO_CONFIG
    )
    model = botocore.session.get_session().get_service_model("emr-serverless")
    shape = model.operation_model("GetDashboardForJobRun").input_shape
    validate_parameters(expected, shape)
    assert shape.members["attempt"].metadata["min"] == 1
    assert shape.members["accessSystemProfileLogs"].type_name == "boolean"
    assert (
        model.operation_model("GetDashboardForJobRun").output_shape.members["url"].metadata["max"]
        == 2048
    )
    assert sdk.method_calls == [call.get_dashboard_for_job_run(**expected)]


async def test_provider_default_explicit_false_and_absent_attempt(provider):
    client, _, sdk = provider
    await client.get_dashboard_for_job_run("a", "r")
    sdk.get_dashboard_for_job_run.assert_awaited_once_with(
        applicationId="a", jobRunId="r", accessSystemProfileLogs=False
    )


@pytest.mark.parametrize(
    ("app", "run", "options"),
    [
        (None, "r", {}),
        (1, "r", {}),
        (StringSubclass("a"), "r", {}),
        ("", "r", {}),
        ("a-b", "r", {}),
        ("A", "r", {}),
        ("a" * 65, "r", {}),
        ("a", "r/", {}),
        ("a", "r\n", {}),
        ("a", True, {}),
        ("a", "r", {"attempt": True}),
        ("a", "r", {"attempt": 0}),
        ("a", "r", {"attempt": -1}),
        ("a", "r", {"attempt": 1.0}),
        ("a", "r", {"attempt": "1"}),
        ("a", "r", {"access_system_profile_logs": 0}),
        ("a", "r", {"access_system_profile_logs": None}),
    ],
)
async def test_provider_rejects_request_before_sdk_io(provider, app, run, options):
    client, session, _ = provider
    with pytest.raises(ValidationError):
        await client.get_dashboard_for_job_run(app, run, **options)
    session.client.assert_not_called()


@pytest.mark.parametrize(
    "response", [None, [], {}, {"url": None}, {"url": 42}, {"url": "http://x.amazonaws.com"}]
)
async def test_provider_rejects_malformed_response(provider, response):
    client, _, sdk = provider
    sdk.get_dashboard_for_job_run.return_value = response
    with pytest.raises(ValidationError):
        await client.get_dashboard_for_job_run("a", "r")


def _aws_error(code, secret):
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": secret}}, "GetDashboardForJobRun"
    )


@pytest.mark.parametrize(
    ("make_error", "expected"),
    [
        (
            lambda s: botocore.exceptions.CredentialRetrievalError(provider="process", error_msg=s),
            AuthRequiredError,
        ),
        (lambda s: _aws_error("ExpiredTokenException", s), AuthRequiredError),
        (lambda s: _aws_error("AccessDeniedException", s), PermissionDeniedError),
        (lambda s: _aws_error("ResourceNotFoundException", s), NotFoundError),
        (lambda s: _aws_error("ValidationException", s), ValidationError),
        (lambda s: botocore.exceptions.ParamValidationError(report=s), ValidationError),
        (lambda s: _aws_error("ThrottlingException", s), ThrottledError),
        (
            lambda s: botocore.exceptions.EndpointConnectionError(endpoint_url=s),
            ProviderUnreachableError,
        ),
        (lambda s: _aws_error("ServiceUnavailableException", s), ProviderUnreachableError),
        (lambda s: _aws_error("UnknownException", s), ProviderError),
        (lambda s: RuntimeError(s), ProviderError),
    ],
)
async def test_provider_failures_are_typed_and_private(provider, make_error, expected, tmp_path):
    from aws_tui.infra.crash_dump import CrashDump

    client, _, sdk = provider
    parts = ("private-sdk-host", "private-sdk-path", "private-sdk-query", "private-sdk-fragment")
    secret = f"https://{parts[0]}.amazonaws.com/{parts[1]}?{parts[2]}#{parts[3]}"
    sdk.get_dashboard_for_job_run.side_effect = make_error(secret)
    with pytest.raises(expected) as caught:
        await client.get_dashboard_for_job_run("a", "r")
    error = caught.value
    assert type(error) is expected
    assert error.__context__ is None
    assert error.__cause__ is None
    rendered = "".join(traceback.format_exception(error))
    dump = CrashDump(base_dir=tmp_path).write(exc=error).read_text()
    for piece in (secret, *parts):
        assert piece not in str(error)
        assert piece not in repr(error)
        assert piece not in rendered
        assert piece not in dump


async def test_context_creation_failure_is_private(provider):
    client, session, _ = provider
    session.client.side_effect = RuntimeError("private-context-token")
    with pytest.raises(ProviderError) as caught:
        await client.get_dashboard_for_job_run("a", "r")
    assert "private-context-token" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("character", ["\u200b", "\u202e", "\ud800"])
def test_url_rejects_invisible_format_controls_and_surrogates(character):
    with pytest.raises(ValidationError):
        validate_dashboard_url("https://x.amazonaws.com/path" + character)


async def test_provider_malformed_url_response_is_private(provider, tmp_path):
    from aws_tui.infra.crash_dump import CrashDump

    client, _, sdk = provider
    parts = (
        "response-private-host",
        "response-private-path",
        "response-private-query",
        "response-private-fragment",
    )
    url = f"http://{parts[0]}.amazonaws.com/{parts[1]}?{parts[2]}#{parts[3]}"
    sdk.get_dashboard_for_job_run.return_value = {"url": url}
    with pytest.raises(ValidationError) as caught:
        await client.get_dashboard_for_job_run("a", "r")
    error = caught.value
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    dump = CrashDump(base_dir=tmp_path).write(exc=error).read_text()
    for part in (url, *parts):
        assert part not in rendered
        assert part not in dump


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        {"Error": None},
        {"Error": []},
        {"Error": "unexpected"},
        {"Error": {"Code": []}},
        {"Error": {"Code": {}}},
        {"Error": {"Code": None}},
        {"Error": {"Code": True}},
        {"Error": {"Code": StringSubclass("AccessDeniedException")}},
    ],
)
async def test_malformed_aws_error_metadata_remains_private(provider, metadata, tmp_path):
    from aws_tui.infra.crash_dump import CrashDump

    client, _, sdk = provider
    parts = (
        "metadata-private-host",
        "metadata-private-path",
        "metadata-private-query",
        "metadata-private-fragment",
    )
    url = f"https://{parts[0]}.amazonaws.com/{parts[1]}?{parts[2]}#{parts[3]}"
    original = _aws_error("UnknownException", url)
    original.response = metadata
    sdk.get_dashboard_for_job_run.side_effect = original
    with pytest.raises(ProviderError) as caught:
        await client.get_dashboard_for_job_run("a", "r")
    error = caught.value
    assert type(error) is ProviderError
    assert str(error) == "EMR Serverless dashboard request failed"
    assert error.__context__ is None
    assert error.__cause__ is None
    rendered = "".join(traceback.format_exception(error))
    dump = CrashDump(base_dir=tmp_path).write(exc=error).read_text()
    for piece in (url, *parts):
        assert piece not in repr(error)
        assert piece not in rendered
        assert piece not in dump


@pytest.mark.parametrize(
    ("url", "valid"),
    [
        ("https://cache-private-host.amazonaws.com/cache-path?cache-query#cache-fragment", True),
        (
            "https://cache-private-host.amazonaws.com.cn:443/cache-path?cache-query#cache-fragment",
            True,
        ),
        ("https://cache-private-host.invalid/cache-path?cache-query#cache-fragment", False),
        (
            "https://cache-private-host.amazonaws.com:444/cache-path?cache-query#cache-fragment",
            False,
        ),
    ],
)
def test_validation_does_not_retain_signed_urls_in_parser_cache(url, valid):
    from urllib.parse import urlsplit

    # Only test setup clears the parser cache. Application validation must
    # neither populate it with a private URL nor clear unrelated entries.
    urlsplit.cache_clear()
    before = urlsplit.cache_info()
    for _ in range(2):
        if valid:
            assert validate_dashboard_url(url) == url
            assert DashboardLink(url).url == url
        else:
            with pytest.raises(ValidationError):
                validate_dashboard_url(url)
    assert urlsplit.cache_info() == before
