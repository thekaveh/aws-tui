"""Bounded read-only service calls for explicitly selected overview scopes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

from aws_tui.domain.athena import map_athena_error
from aws_tui.domain.emr_serverless import map_boto_error
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.domain.glue import map_glue_error
from aws_tui.domain.scoped_overview import (
    OVERVIEW_PAGE_SIZE,
    OverviewEntry,
    OverviewPage,
    WatchScope,
)
from aws_tui.infra.connection_resolver import Connection


class OverviewSession(Protocol):
    async def client(
        self, connection: Connection, service: str
    ) -> AbstractAsyncContextManager[Any]: ...


def _invalid() -> ValidationError:
    return ValidationError("Overview response has an invalid shape or scope")


def _safe_error(error: ProviderError) -> ProviderError:
    # Some shared mappers preserve SDK Message text. The overview needs only
    # the taxonomy; raw exception payloads must not become section summaries.
    for error_type, message in (
        (AuthRequiredError, "AWS credentials unavailable"),
        (PermissionDeniedError, "Overview access denied"),
        (ThrottledError, "Overview request throttled"),
        (NotFoundError, "Watched resource unavailable"),
        (ProviderUnreachableError, "Overview service unavailable"),
        (ValidationError, "Overview response has an invalid shape or scope"),
    ):
        if isinstance(error, error_type):
            return error_type(message)
    return ProviderError("Overview request failed")


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid()
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise _invalid()
    return value


def _optional_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _text(value)


def _time(value: object) -> datetime | None:
    if value is not None and not isinstance(value, datetime):
        raise _invalid()
    return value


def _rows(response: Mapping[str, Any], key: str) -> list[Any]:
    value = response.get(key, [])
    if type(value) is not list:
        raise _invalid()
    return value


def _cap(rows: list[Any], token: object) -> str | None:
    if token is not None and type(token) is not str:
        raise _invalid()
    if len(rows) > OVERVIEW_PAGE_SIZE or token:
        return "First page only: at most 25 entries; more available"
    return None


def _athena_error_summary(status: Mapping[str, Any]) -> str | None:
    error = _object(status.get("AthenaError", {}))
    fields: list[str] = []
    for key, label in (("ErrorCategory", "category"), ("ErrorType", "type")):
        value = error.get(key)
        if value is not None:
            if type(value) is not int or not 0 <= value <= 2**63 - 1:
                raise _invalid()
            fields.append(f"{label}={value}")
    retryable = error.get("Retryable")
    if retryable is not None:
        if type(retryable) is not bool:
            raise _invalid()
        fields.append(f"retryable={str(retryable).lower()}")
    # Free-form reasons may echo normalized SQL, identifiers or excerpts, even
    # when the Query field is absent. Literal replacement cannot prove that
    # this projection excludes SQL. Preserve native structured error fields
    # and disclose the omitted free-form explanation instead.
    has_text = bool(_optional_text(error.get("ErrorMessage"))) or bool(
        _optional_text(status.get("StateChangeReason"))
    )
    if fields:
        return "Athena error: " + ", ".join(fields) + "; free-form text omitted (may contain SQL)"
    return "Provider reason omitted (may contain SQL)" if has_text else None


class OverviewClient:
    """One page per scope, at most two service calls, no discovery or mutations.

    The shared session retains its finite SDK retry/transport policy. There is
    no application retry loop, pagination, query/result access or log access.
    Only the immutable allowlisted projection escapes the request lifetime.
    """

    def __init__(self, session: OverviewSession) -> None:
        self._session = session

    async def read(self, scope: WatchScope, connection: Connection) -> OverviewPage:
        if (
            connection.kind != "aws"
            or (
                scope.connection_name,
                scope.region,
            )
            != (connection.name, connection.region)
            or scope.kind == "transfers"
        ):
            raise ValidationError("Overview source no longer matches the watched scope")
        try:
            async with asyncio.timeout(30):
                async with await self._session.client(connection, scope.service) as client:
                    return await self._read(client, scope)
        except TimeoutError:
            raise ProviderUnreachableError("Overview refresh timed out") from None
        except ProviderError as error:
            raise _safe_error(error) from None
        except (ValueError, TypeError):
            raise _invalid() from None
        except Exception as exc:
            mapper = {
                "athena": map_athena_error,
                "glue": map_glue_error,
                "emr-serverless": map_boto_error,
            }[scope.service]
            mapped = mapper(exc)
            raise (
                _safe_error(mapped)
                if mapped is not None
                else ProviderError("Overview refresh failed")
            ) from None

    async def _read(self, client: Any, scope: WatchScope) -> OverviewPage:
        if scope.kind == "athena":
            return await self._athena(client, scope)
        if scope.kind == "glue-crawler":
            response = _object(await client.get_crawler(Name=scope.resource))
            crawler = _object(response.get("Crawler"))
            if crawler.get("Name") != scope.resource:
                raise _invalid()
            last = _object(crawler.get("LastCrawl", {}))
            return OverviewPage(
                (
                    OverviewEntry(
                        scope,
                        scope.resource,
                        _text(crawler.get("State")),
                        times=(
                            ("last crawl started", _time(last.get("StartTime"))),
                            ("updated", _time(crawler.get("LastUpdated"))),
                        ),
                        error_summary=_optional_text(last.get("ErrorMessage")),
                        last_status=_optional_text(last.get("Status")),
                    ),
                )
            )
        if scope.kind == "glue-job":
            response = _object(
                await client.get_job_runs(JobName=scope.resource, MaxResults=OVERVIEW_PAGE_SIZE)
            )
            rows = _rows(response, "JobRuns")
            cap = _cap(rows, response.get("NextToken"))
            entries = []
            for raw in rows[:OVERVIEW_PAGE_SIZE]:
                row = _object(raw)
                if row.get("JobName") != scope.resource:
                    raise _invalid()
                entries.append(
                    OverviewEntry(
                        scope,
                        _text(row.get("Id")),
                        _text(row.get("JobRunState")),
                        times=(
                            ("started", _time(row.get("StartedOn"))),
                            ("completed", _time(row.get("CompletedOn"))),
                        ),
                        error_summary=_optional_text(row.get("ErrorMessage")),
                    )
                )
        else:
            response = _object(
                await client.list_job_runs(
                    applicationId=scope.resource, maxResults=OVERVIEW_PAGE_SIZE
                )
            )
            if "jobRuns" not in response:
                raise _invalid()
            rows = _rows(response, "jobRuns")
            cap = _cap(rows, response.get("nextToken"))
            entries = []
            for raw in rows[:OVERVIEW_PAGE_SIZE]:
                row = _object(raw)
                if row.get("applicationId") != scope.resource:
                    raise _invalid()
                entries.append(
                    OverviewEntry(
                        scope,
                        _text(row.get("id")),
                        _text(row.get("state")),
                        times=(
                            ("created", _time(row.get("createdAt"))),
                            ("updated", _time(row.get("updatedAt"))),
                        ),
                        error_summary=_optional_text(row.get("stateDetails")),
                    )
                )
        if len({entry.identifier for entry in entries}) != len(entries):
            raise _invalid()
        return OverviewPage(tuple(entries), cap)

    async def _athena(self, client: Any, scope: WatchScope) -> OverviewPage:
        response = _object(
            await client.list_query_executions(
                WorkGroup=scope.resource, MaxResults=OVERVIEW_PAGE_SIZE
            )
        )
        raw_ids = _rows(response, "QueryExecutionIds")
        cap = _cap(raw_ids, response.get("NextToken"))
        identifiers = [_text(value) for value in raw_ids[:OVERVIEW_PAGE_SIZE]]
        if len(set(identifiers)) != len(identifiers):
            raise _invalid()
        if not identifiers:
            return OverviewPage((), cap)
        response = _object(await client.batch_get_query_execution(QueryExecutionIds=identifiers))
        if _rows(response, "UnprocessedQueryExecutionIds"):
            raise _invalid()
        entries: dict[str, OverviewEntry] = {}
        details = _rows(response, "QueryExecutions")
        if len(details) != len(identifiers):
            raise _invalid()
        for raw in details:
            row = _object(raw)
            identifier = _text(row.get("QueryExecutionId"))
            if row.get("WorkGroup") != scope.resource or identifier in entries:
                raise _invalid()
            status = _object(row.get("Status"))
            entries[identifier] = OverviewEntry(
                scope,
                identifier,
                _text(status.get("State")),
                times=(
                    ("submitted", _time(status.get("SubmissionDateTime"))),
                    ("completed", _time(status.get("CompletionDateTime"))),
                ),
                error_summary=_athena_error_summary(status),
            )
        if entries.keys() != set(identifiers):
            raise _invalid()
        return OverviewPage(tuple(entries[identifier] for identifier in identifiers), cap)
