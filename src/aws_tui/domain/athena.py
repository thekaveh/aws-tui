"""Paginated Amazon Athena domain client and immutable Athena records."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NoReturn

import botocore.exceptions

from aws_tui.domain.data_catalog import (
    DatabaseRef,
    DatabaseSummary,
    TableRef,
    TableSummary,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.domain.query import (
    AthenaQueryError,
    NamedQuery,
    PreparedStatement,
    PreparedStatementSummary,
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
    ResultPage,
)
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.redaction import redact_text

_PAGE_SIZE = 50
_RESULT_PAGE_SIZE = 1000
_NAMED_QUERY_BATCH_SIZE = 50
_TERMINAL_QUERY_STATES = frozenset(
    {
        QueryState.SUCCEEDED,
        QueryState.FAILED,
        QueryState.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class AthenaWorkgroupSummary:
    name: str
    state: str
    description: str | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class AthenaWorkgroupDetail:
    summary: AthenaWorkgroupSummary
    output_location: str | None = field(repr=False)
    enforce_workgroup_configuration: bool
    publish_cloudwatch_metrics: bool
    bytes_scanned_cutoff: int | None
    engine_version: str | None


@dataclass(frozen=True, slots=True)
class AthenaCatalogSummary:
    name: str
    catalog_type: str
    description: str | None


class ResultConfigurationRequiredError(ValidationError):
    """Athena reported no workgroup or caller-provided result destination."""


_ACCESS_DENIED_CODES = frozenset({"AccessDenied", "AccessDeniedException"})
_AUTH_CODES = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "UnrecognizedClientException",
    }
)
_NOT_FOUND_CODES = frozenset({"ResourceNotFoundException"})
_THROTTLED_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
    }
)
_UNREACHABLE_CODES = frozenset(
    {
        "RequestTimeout",
        "RequestTimeoutException",
    }
)
_VALIDATION_CODES = frozenset(
    {
        "InvalidRequestException",
        "MetadataException",
        "ValidationException",
    }
)
_CREDENTIAL_EXCEPTIONS = (
    botocore.exceptions.NoCredentialsError,
    botocore.exceptions.PartialCredentialsError,
    botocore.exceptions.ProfileNotFound,
    botocore.exceptions.TokenRetrievalError,
    botocore.exceptions.CredentialRetrievalError,
    botocore.exceptions.UnauthorizedSSOTokenError,
    botocore.exceptions.SSOTokenLoadError,
)
_TRANSPORT_EXCEPTIONS = (
    botocore.exceptions.EndpointConnectionError,
    botocore.exceptions.ConnectTimeoutError,
    botocore.exceptions.ReadTimeoutError,
    botocore.exceptions.ConnectionClosedError,
    botocore.exceptions.ConnectionError,
)


def map_athena_error(exc: BaseException) -> ProviderError | None:
    """Map botocore and Athena failures to the provider error taxonomy."""
    return _map_athena_error(exc, sensitive_values=())


def _map_athena_error(
    exc: BaseException,
    *,
    sensitive_values: Sequence[str],
) -> ProviderError | None:
    if isinstance(exc, _CREDENTIAL_EXCEPTIONS):
        if isinstance(exc, botocore.exceptions.CredentialRetrievalError):
            return AuthRequiredError("credential process failed")
        return AuthRequiredError(
            _sanitize_message(str(exc) or "no AWS credentials", sensitive_values)
        )
    if isinstance(exc, _TRANSPORT_EXCEPTIONS):
        return ProviderUnreachableError(
            _sanitize_message(str(exc) or "Athena endpoint unreachable", sensitive_values)
        )
    if isinstance(exc, botocore.exceptions.ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        message = _sanitize_message(
            str(error.get("Message", "Athena request failed")),
            sensitive_values,
        )
        return _provider_error_for_code(code, message)
    if isinstance(exc, botocore.exceptions.ParamValidationError):
        return ValidationError(_sanitize_message(str(exc), sensitive_values))
    if isinstance(exc, KeyError | TypeError | ValueError):
        return ValidationError("malformed Athena response")
    return None


def _raise_mapped_athena_error(
    exc: Exception,
    *,
    sensitive_values: Sequence[str] = (),
    unknown_message: str | None = None,
) -> NoReturn:
    mapped = _map_athena_error(exc, sensitive_values=sensitive_values)
    if mapped is None:
        if unknown_message is None:
            raise exc
        mapped = ProviderError(unknown_message)
    raise mapped from None


def _provider_error_for_code(code: str, message: str) -> ProviderError:
    if code in _ACCESS_DENIED_CODES:
        return PermissionDeniedError(message)
    if code in _AUTH_CODES:
        return AuthRequiredError(message)
    if code in _NOT_FOUND_CODES:
        return NotFoundError(message)
    if code in _THROTTLED_CODES:
        return ThrottledError(message)
    if code in _UNREACHABLE_CODES:
        return ProviderUnreachableError(message)
    if code in _VALIDATION_CODES:
        return ValidationError(message)
    return ProviderError(message)


def _is_missing_result_configuration_error(exc: Exception) -> bool:
    if not isinstance(exc, botocore.exceptions.ClientError):
        return False
    error = exc.response.get("Error", {})
    if str(error.get("Code", "")) != "InvalidRequestException":
        return False
    return "no output location provided" in str(error.get("Message", "")).lower()


def _sanitize_message(message: str, sensitive_values: Sequence[str]) -> str:
    sanitized = redact_text(message)
    fragments = {
        fragment
        for value in sensitive_values
        if value
        for fragment in (
            value,
            value.partition("://")[2] if "://" in value else "",
        )
        if fragment
    }
    for value in sorted(
        fragments,
        key=len,
        reverse=True,
    ):
        sanitized = sanitized.replace(value, "[REDACTED]")
    return sanitized


class AthenaClient:
    """One-page Athena API facade that contains all boto response mappings."""

    def __init__(self, *, aws_session: AwsSession, connection: Connection) -> None:
        self._aws_session = aws_session
        self._connection = connection
        self._sql_policy = ReadOnlySqlPolicy()
        self._app_started_active_queries: set[str] = set()
        self._app_started_query_ids_by_token: dict[str, str] = {}
        self._retired_app_started_queries: set[str] = set()
        self._stop_tasks: dict[str, asyncio.Task[None]] = {}

    async def list_workgroups_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[AthenaWorkgroupSummary], str | None]:
        kwargs = _page_kwargs(start_token)
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.list_work_groups(**kwargs)
            rows = [
                _map_workgroup_summary(item) for item in _response_items(response, "WorkGroups")
            ]
            return rows, _response_token(response)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(start_token or "",),
            )

    async def get_workgroup(self, name: str) -> AthenaWorkgroupDetail:
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.get_work_group(WorkGroup=name)
            workgroup = _required_mapping(_response_mapping(response), "WorkGroup")
            configuration = _optional_mapping(workgroup, "Configuration")
            result_configuration = _optional_mapping(
                configuration,
                "ResultConfiguration",
            )
            engine_version = _optional_mapping(configuration, "EngineVersion")
            return AthenaWorkgroupDetail(
                summary=_map_workgroup_summary(workgroup),
                output_location=_optional_string(
                    result_configuration,
                    "OutputLocation",
                ),
                enforce_workgroup_configuration=_optional_bool(
                    configuration,
                    "EnforceWorkGroupConfiguration",
                    default=False,
                ),
                publish_cloudwatch_metrics=_optional_bool(
                    configuration,
                    "PublishCloudWatchMetricsEnabled",
                    default=False,
                ),
                bytes_scanned_cutoff=_optional_int(
                    configuration,
                    "BytesScannedCutoffPerQuery",
                ),
                engine_version=_engine_version(engine_version),
            )
        except Exception as exc:
            _raise_mapped_athena_error(exc, sensitive_values=(name,))

    async def list_catalogs_page(
        self,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[AthenaCatalogSummary], str | None]:
        kwargs = _page_kwargs(start_token)
        if workgroup is not None:
            kwargs["WorkGroup"] = workgroup
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.list_data_catalogs(**kwargs)
            rows = [
                AthenaCatalogSummary(
                    name=_required_string(item, "CatalogName"),
                    catalog_type=_required_string(item, "Type"),
                    description=None,
                )
                for item in _response_items(response, "DataCatalogsSummary")
            ]
            return rows, _response_token(response)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(workgroup or "", start_token or ""),
            )

    async def list_databases_page(
        self,
        catalog: str,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        kwargs: dict[str, object] = {
            "CatalogName": catalog,
            **_page_kwargs(start_token),
        }
        if workgroup is not None:
            kwargs["WorkGroup"] = workgroup
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.list_databases(**kwargs)
            rows = [
                DatabaseSummary(
                    ref=DatabaseRef(
                        catalog,
                        _required_string(item, "Name"),
                        self._connection.name,
                        self._connection.region,
                    ),
                    description=_optional_string(item, "Description"),
                    location_uri=None,
                    created_at=None,
                )
                for item in _response_items(response, "DatabaseList")
            ]
            return rows, _response_token(response)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(
                    catalog,
                    workgroup or "",
                    start_token or "",
                ),
            )

    async def list_tables_page(
        self,
        catalog: str,
        database: str,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[TableSummary], str | None]:
        kwargs: dict[str, object] = {
            "CatalogName": catalog,
            "DatabaseName": database,
            **_page_kwargs(start_token),
        }
        if workgroup is not None:
            kwargs["WorkGroup"] = workgroup
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.list_table_metadata(**kwargs)
            rows = [
                TableSummary(
                    ref=TableRef(
                        catalog,
                        database,
                        _required_string(item, "Name"),
                        self._connection.name,
                        self._connection.region,
                    ),
                    description=None,
                    owner=None,
                    table_type=_optional_string(item, "TableType"),
                    created_at=_optional_datetime(item, "CreateTime"),
                    updated_at=None,
                )
                for item in _response_items(response, "TableMetadataList")
            ]
            return rows, _response_token(response)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(
                    catalog,
                    database,
                    workgroup or "",
                    start_token or "",
                ),
            )

    async def list_query_executions_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[QueryExecutionRef], str | None]:
        kwargs: dict[str, object] = {
            "WorkGroup": workgroup,
            **_page_kwargs(start_token),
        }
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.list_query_executions(**kwargs)
            rows = [
                QueryExecutionRef(
                    execution_id,
                    self._connection.name,
                    self._connection.region,
                    workgroup,
                )
                for execution_id in _response_strings(
                    response,
                    "QueryExecutionIds",
                )
            ]
            return rows, _response_token(response)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(workgroup, start_token or ""),
            )

    async def get_query_execution(
        self,
        execution_id: str,
    ) -> QueryExecutionDetail:
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.get_query_execution(
                    QueryExecutionId=execution_id,
                )
            execution = _required_mapping(
                _response_mapping(response),
                "QueryExecution",
            )
            detail = self._map_query_execution(execution)
            if detail.summary.ref.execution_id != execution_id:
                raise ValueError("query execution id mismatch")
            if detail.summary.state in _TERMINAL_QUERY_STATES:
                self._retire_app_started_query(execution_id)
            return detail
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(execution_id,),
            )

    async def get_query_runtime_statistics(
        self,
        execution_id: str,
    ) -> QueryStatistics:
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.get_query_runtime_statistics(
                    QueryExecutionId=execution_id,
                )
            runtime = _required_mapping(
                _response_mapping(response),
                "QueryRuntimeStatistics",
            )
            timeline = _optional_mapping(runtime, "Timeline")
            rows = _optional_mapping(runtime, "Rows")
            return QueryStatistics(
                engine_ms=_optional_int(
                    timeline,
                    "EngineExecutionTimeInMillis",
                ),
                queue_ms=_optional_int(
                    timeline,
                    "QueryQueueTimeInMillis",
                ),
                planning_ms=_optional_int(
                    timeline,
                    "QueryPlanningTimeInMillis",
                ),
                service_ms=_optional_int(
                    timeline,
                    "ServiceProcessingTimeInMillis",
                ),
                bytes_scanned=_optional_int(rows, "InputBytes"),
                reused_previous_result=False,
            )
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(execution_id,),
            )

    async def start_query(
        self,
        sql: str,
        context: QueryContext,
        *,
        request_token: str,
        output_location: str | None = None,
    ) -> QueryExecutionRef:
        normalized_sql = self._sql_policy.validate(sql)
        if (
            context.connection_name != self._connection.name
            or context.region != self._connection.region
        ):
            raise ValidationError("query context does not match the active AWS connection")

        kwargs: dict[str, object] = {
            "QueryString": normalized_sql,
            "ClientRequestToken": request_token,
            "QueryExecutionContext": {
                "Catalog": context.catalog,
                "Database": context.database,
            },
            "WorkGroup": context.workgroup,
        }
        if output_location is not None:
            kwargs["ResultConfiguration"] = {
                "OutputLocation": output_location,
            }

        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.start_query_execution(**kwargs)
            execution_id = _required_string(
                _response_mapping(response),
                "QueryExecutionId",
            )
            if execution_id not in self._retired_app_started_queries:
                known_execution_id = self._app_started_query_ids_by_token.get(request_token)
                if known_execution_id is None:
                    self._app_started_query_ids_by_token[request_token] = execution_id
                    self._app_started_active_queries.add(execution_id)
                elif known_execution_id == execution_id:
                    self._app_started_active_queries.add(execution_id)
            return QueryExecutionRef(
                execution_id,
                self._connection.name,
                self._connection.region,
                context.workgroup,
            )
        except Exception as exc:
            if _is_missing_result_configuration_error(exc):
                raise ResultConfigurationRequiredError(
                    "Athena result configuration is required"
                ) from None
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(
                    sql,
                    normalized_sql,
                    request_token,
                    output_location or "",
                ),
                unknown_message="Athena request failed",
            )

    async def stop_query(self, execution_id: str) -> None:
        task = self._stop_tasks.get(execution_id)
        if task is None:
            if execution_id not in self._app_started_active_queries:
                raise ValidationError("query is not an active app-started query")
            self._app_started_active_queries.remove(execution_id)
            task = asyncio.create_task(self._dispatch_stop_query(execution_id))
            self._stop_tasks[execution_id] = task
            task.add_done_callback(
                lambda completed: self._forget_stop_task(
                    execution_id,
                    completed,
                )
            )
        await asyncio.shield(task)

    async def _dispatch_stop_query(self, execution_id: str) -> None:
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                await client.stop_query_execution(
                    QueryExecutionId=execution_id,
                )
        except Exception as exc:
            if execution_id not in self._retired_app_started_queries:
                self._app_started_active_queries.add(execution_id)
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(execution_id,),
            )
        except BaseException:
            if execution_id not in self._retired_app_started_queries:
                self._app_started_active_queries.add(execution_id)
            raise
        self._retire_app_started_query(execution_id)

    def _forget_stop_task(
        self,
        execution_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if not task.cancelled():
            task.exception()
        if self._stop_tasks.get(execution_id) is task:
            del self._stop_tasks[execution_id]

    def _retire_app_started_query(self, execution_id: str) -> None:
        self._app_started_active_queries.discard(execution_id)
        self._retired_app_started_queries.add(execution_id)
        retired_tokens = [
            token
            for token, token_execution_id in self._app_started_query_ids_by_token.items()
            if token_execution_id == execution_id
        ]
        for token in retired_tokens:
            del self._app_started_query_ids_by_token[token]

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        kwargs: dict[str, object] = {
            "QueryExecutionId": execution_id,
            "MaxResults": _RESULT_PAGE_SIZE,
        }
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.get_query_results(**kwargs)
            return _map_result_page(
                _response_mapping(response),
                first_page=start_token is None,
            )
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(execution_id, start_token or ""),
            )

    async def list_named_queries_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[str], str | None]:
        kwargs: dict[str, object] = {
            "WorkGroup": workgroup,
            **_page_kwargs(start_token),
        }
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.list_named_queries(**kwargs)
            return (
                list(_response_strings(response, "NamedQueryIds")),
                _response_token(response),
            )
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(workgroup, start_token or ""),
            )

    async def get_named_queries(
        self,
        ids: Sequence[str],
    ) -> tuple[NamedQuery, ...]:
        if not ids:
            return ()
        try:
            rows: list[NamedQuery] = []
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                for offset in range(0, len(ids), _NAMED_QUERY_BATCH_SIZE):
                    batch_ids = list(ids[offset : offset + _NAMED_QUERY_BATCH_SIZE])
                    response = await client.batch_get_named_query(
                        NamedQueryIds=batch_ids,
                    )
                    unprocessed_error = _unprocessed_named_query_error(
                        response,
                        sensitive_values=batch_ids,
                    )
                    if unprocessed_error is not None:
                        raise unprocessed_error
                    rows.extend(
                        _map_named_query(item) for item in _response_items(response, "NamedQueries")
                    )
            return tuple(rows)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=tuple(ids),
            )

    async def list_prepared_statements_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PreparedStatementSummary], str | None]:
        kwargs: dict[str, object] = {
            "WorkGroup": workgroup,
            **_page_kwargs(start_token),
        }
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.list_prepared_statements(**kwargs)
            rows = [
                _map_prepared_statement_summary(item)
                for item in _response_items(response, "PreparedStatements")
            ]
            return rows, _response_token(response)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(workgroup, start_token or ""),
            )

    async def get_prepared_statement(
        self,
        name: str,
        workgroup: str,
    ) -> PreparedStatement:
        try:
            async with await self._aws_session.client(
                self._connection,
                "athena",
            ) as client:
                response = await client.get_prepared_statement(
                    StatementName=name,
                    WorkGroup=workgroup,
                )
            statement = _required_mapping(
                _response_mapping(response),
                "PreparedStatement",
            )
            return _map_prepared_statement(statement)
        except Exception as exc:
            _raise_mapped_athena_error(
                exc,
                sensitive_values=(name, workgroup),
            )

    def _map_query_execution(
        self,
        execution: Mapping[str, Any],
    ) -> QueryExecutionDetail:
        execution_id = _required_string(execution, "QueryExecutionId")
        workgroup = _required_string(execution, "WorkGroup")
        context = _optional_mapping(execution, "QueryExecutionContext")
        status = _required_mapping(execution, "Status")
        statistics = _optional_mapping(execution, "Statistics")
        result_configuration = _optional_mapping(
            execution,
            "ResultConfiguration",
        )
        engine_version = _optional_mapping(execution, "EngineVersion")
        query = _optional_string(execution, "Query")
        state = QueryState(_required_string(status, "State"))

        return QueryExecutionDetail(
            summary=QueryExecutionSummary(
                ref=QueryExecutionRef(
                    execution_id,
                    self._connection.name,
                    self._connection.region,
                    workgroup,
                ),
                state=state,
                submitted_at=_optional_datetime(
                    status,
                    "SubmissionDateTime",
                ),
                completed_at=_optional_datetime(
                    status,
                    "CompletionDateTime",
                ),
                statement_type=_optional_string(
                    execution,
                    "StatementType",
                ),
            ),
            state_reason=_sanitize_optional_athena_text(
                _optional_string(status, "StateChangeReason"),
                query,
            ),
            context=QueryContext(
                self._connection.name,
                self._connection.region,
                workgroup,
                _optional_string(context, "Catalog") or "",
                _optional_string(context, "Database") or "",
            ),
            statistics=_map_query_statistics(statistics),
            output_location=_optional_string(
                result_configuration,
                "OutputLocation",
            ),
            engine_version=_engine_version(engine_version),
            error=_map_query_error(
                _optional_mapping(status, "AthenaError"),
                query=query,
            ),
        )


def _page_kwargs(start_token: str | None) -> dict[str, object]:
    kwargs: dict[str, object] = {"MaxResults": _PAGE_SIZE}
    if start_token is not None:
        kwargs["NextToken"] = start_token
    return kwargs


def _map_workgroup_summary(
    item: Mapping[str, Any],
) -> AthenaWorkgroupSummary:
    return AthenaWorkgroupSummary(
        name=_required_string(item, "Name"),
        state=_required_string(item, "State"),
        description=_optional_string(item, "Description"),
        created_at=_optional_datetime(item, "CreationTime"),
    )


def _map_query_statistics(
    statistics: Mapping[str, Any],
) -> QueryStatistics:
    reuse = _optional_mapping(statistics, "ResultReuseInformation")
    return QueryStatistics(
        engine_ms=_optional_int(
            statistics,
            "EngineExecutionTimeInMillis",
        ),
        queue_ms=_optional_int(
            statistics,
            "QueryQueueTimeInMillis",
        ),
        planning_ms=_optional_int(
            statistics,
            "QueryPlanningTimeInMillis",
        ),
        service_ms=_optional_int(
            statistics,
            "ServiceProcessingTimeInMillis",
        ),
        bytes_scanned=_optional_int(
            statistics,
            "DataScannedInBytes",
        ),
        reused_previous_result=_optional_bool(
            reuse,
            "ReusedPreviousResult",
            default=False,
        ),
    )


def _map_query_error(
    item: Mapping[str, Any],
    *,
    query: str | None,
) -> AthenaQueryError | None:
    if not item:
        return None
    return AthenaQueryError(
        category=_optional_int(item, "ErrorCategory"),
        error_type=_optional_int(item, "ErrorType"),
        retryable=_optional_bool(item, "Retryable", default=False),
        message=_sanitize_optional_athena_text(
            _optional_string(item, "ErrorMessage"),
            query,
        )
        or "",
    )


def _map_result_page(
    response: Mapping[str, Any],
    *,
    first_page: bool,
) -> ResultPage:
    result_set = _required_mapping(response, "ResultSet")
    metadata = _optional_mapping(result_set, "ResultSetMetadata")
    columns = tuple(
        ResultColumn(
            name=_optional_string(column, "Name") or "",
            type_name=_optional_string(column, "Type") or "unknown",
            nullable=_optional_string(column, "Nullable") or "UNKNOWN",
        )
        for column in _mapping_items(
            _optional_sequence(metadata, "ColumnInfo"),
        )
    )
    rows = tuple(
        tuple(
            _optional_string(cell, "VarCharValue") if "VarCharValue" in cell else None
            for cell in _mapping_items(
                _optional_sequence(row, "Data"),
            )
        )
        for row in _mapping_items(_optional_sequence(result_set, "Rows"))
    )
    if first_page and rows and tuple(column.name for column in columns) == rows[0]:
        rows = rows[1:]
    return ResultPage(columns, rows, _response_token(response))


def _map_named_query(item: Mapping[str, Any]) -> NamedQuery:
    return NamedQuery(
        query_id=_required_string(item, "NamedQueryId"),
        name=_required_string(item, "Name"),
        description=_optional_string(item, "Description"),
        database=_required_string(item, "Database"),
        query_string=_required_string(item, "QueryString"),
        workgroup=_required_string(item, "WorkGroup"),
    )


def _unprocessed_named_query_error(
    response: object,
    *,
    sensitive_values: Sequence[str],
) -> ProviderError | None:
    unprocessed = _response_items(response, "UnprocessedNamedQueryIds")
    if not unprocessed:
        return None
    first = unprocessed[0]
    code = _optional_string(first, "ErrorCode") or ""
    message = _sanitize_message(
        _optional_string(first, "ErrorMessage")
        or "Athena could not process one or more named queries",
        sensitive_values,
    )
    return _provider_error_for_code(code, message)


def _map_prepared_statement(
    item: Mapping[str, Any],
) -> PreparedStatement:
    return PreparedStatement(
        name=_required_string(item, "StatementName"),
        query_statement=_required_string(item, "QueryStatement"),
        workgroup=_required_string(item, "WorkGroupName"),
        description=_optional_string(item, "Description"),
        last_modified_at=_optional_datetime(item, "LastModifiedTime"),
    )


def _map_prepared_statement_summary(
    item: Mapping[str, Any],
) -> PreparedStatementSummary:
    return PreparedStatementSummary(
        name=_required_string(item, "StatementName"),
        last_modified_at=_optional_datetime(item, "LastModifiedTime"),
    )


def _engine_version(item: Mapping[str, Any]) -> str | None:
    return _optional_string(
        item,
        "EffectiveEngineVersion",
    ) or _optional_string(item, "SelectedEngineVersion")


def _sanitize_optional_athena_text(
    value: str | None,
    query: str | None,
) -> str | None:
    if value is None:
        return None
    return _sanitize_message(value, (query,) if query else ())


def _response_mapping(response: object) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        raise TypeError("response must be a mapping")
    return response


def _required_mapping(
    item: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    value = item[key]
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be a mapping")
    return value


def _optional_mapping(
    item: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    value = item.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be a mapping")
    return value


def _optional_sequence(
    item: Mapping[str, Any],
    key: str,
) -> Sequence[object]:
    value = item.get(key)
    if value is None:
        return ()
    if isinstance(value, str | bytes | bytearray) or not isinstance(
        value,
        Sequence,
    ):
        raise TypeError(f"{key} must be a sequence")
    return value


def _mapping_items(
    values: Sequence[object],
) -> tuple[Mapping[str, Any], ...]:
    if not all(isinstance(value, Mapping) for value in values):
        raise TypeError("sequence values must be mappings")
    return tuple(value for value in values if isinstance(value, Mapping))


def _response_items(
    response: object,
    key: str,
) -> tuple[Mapping[str, Any], ...]:
    return _mapping_items(
        _optional_sequence(_response_mapping(response), key),
    )


def _response_strings(
    response: object,
    key: str,
) -> tuple[str, ...]:
    values = _optional_sequence(_response_mapping(response), key)
    if not all(isinstance(value, str) for value in values):
        raise TypeError(f"{key} values must be strings")
    return tuple(value for value in values if isinstance(value, str))


def _required_string(item: Mapping[str, Any], key: str) -> str:
    value = item[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_string(
    item: Mapping[str, Any],
    key: str,
) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_datetime(
    item: Mapping[str, Any],
    key: str,
) -> datetime | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{key} must be a datetime")
    return value


def _optional_int(
    item: Mapping[str, Any],
    key: str,
) -> int | None:
    value = item.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_bool(
    item: Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    value = item.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _response_token(response: object) -> str | None:
    return _optional_string(_response_mapping(response), "NextToken")


__all__ = [
    "AthenaCatalogSummary",
    "AthenaClient",
    "AthenaWorkgroupDetail",
    "AthenaWorkgroupSummary",
    "ResultConfigurationRequiredError",
    "map_athena_error",
]
