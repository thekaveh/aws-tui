"""Paginated AWS Glue domain client and immutable Glue records."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn, cast

import botocore.exceptions

from aws_tui.domain.data_catalog import (
    Column,
    ColumnStatistics,
    DatabaseRef,
    DatabaseSummary,
    PartitionSummary,
    StorageDescriptor,
    TableDetail,
    TableFormat,
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
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.redaction import redact_text

_CATALOG_NAME = "AwsDataCatalog"
_COLUMN_STATISTICS_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class GlueJobSummary:
    name: str
    description: str | None
    role: str
    glue_version: str | None
    command_name: str
    script_location: str | None
    worker_type: str | None
    worker_count: int | None
    timeout_minutes: int | None
    max_retries: int | None
    default_arguments: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class GlueJobRunSummary:
    job_name: str
    run_id: str
    state: str
    attempt: int
    trigger_name: str | None
    started_at: datetime | None
    completed_at: datetime | None
    execution_time_seconds: int | None
    execution_class: str | None
    allocated_capacity: int | None
    arguments: tuple[tuple[str, str], ...]
    predecessor_run_ids: tuple[str, ...]
    error_message: str | None
    state_detail: str | None
    log_group_name: str | None


@dataclass(frozen=True, slots=True)
class GlueCrawlerSummary:
    name: str
    state: str
    role: str
    database_name: str | None
    schedule_expression: str | None


@dataclass(frozen=True, slots=True)
class GlueCrawlerMetrics:
    crawler_name: str
    still_estimating: bool
    time_left_seconds: float | None
    median_runtime_seconds: float | None
    tables_created: int
    tables_updated: int
    tables_deleted: int


@dataclass(frozen=True, slots=True)
class GlueCrawlerDetail:
    summary: GlueCrawlerSummary
    targets: tuple[str, ...]
    classifiers: tuple[str, ...]
    recrawl_behavior: str | None
    schema_update_behavior: str | None
    schema_delete_behavior: str | None
    security_configuration: str | None
    lake_formation_account_id: str | None
    use_lake_formation_credentials: bool
    tags: tuple[tuple[str, str], ...]
    last_crawl_status: str | None
    last_crawl_started_at: datetime | None
    last_crawl_duration_seconds: float | None
    last_crawl_error: str | None
    metrics: GlueCrawlerMetrics | None
    supplemental_warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CrawlerMetricsResult:
    metrics: GlueCrawlerMetrics | None
    last_runtime_seconds: float | None


class LakeFormationPermissionError(PermissionDeniedError):
    """Access was denied by Lake Formation rather than Glue IAM."""


_ACCESS_DENIED_CODES = frozenset({"AccessDenied", "AccessDeniedException"})
_AUTH_CODES = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "UnrecognizedClientException",
    }
)
_NOT_FOUND_CODES = frozenset({"EntityNotFoundException", "ResourceNotFoundException"})
_THROTTLED_CODES = frozenset({"Throttling", "ThrottlingException", "TooManyRequestsException"})
_UNREACHABLE_CODES = frozenset(
    {
        "FederationSourceRetryableException",
        "OperationTimeoutException",
        "RequestTimeout",
        "RequestTimeoutException",
    }
)
_VALIDATION_CODES = frozenset(
    {"InvalidInputException", "InvalidParameterValueException", "ValidationException"}
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


def map_glue_error(exc: BaseException) -> ProviderError | None:
    """Map botocore and Glue wire failures to the provider error taxonomy."""
    if isinstance(exc, _CREDENTIAL_EXCEPTIONS):
        if isinstance(exc, botocore.exceptions.CredentialRetrievalError):
            return AuthRequiredError("credential process failed")
        return AuthRequiredError(redact_text(str(exc) or "no AWS credentials"))
    if isinstance(exc, _TRANSPORT_EXCEPTIONS):
        return ProviderUnreachableError(redact_text(str(exc) or "Glue endpoint unreachable"))
    if isinstance(exc, botocore.exceptions.ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        message = redact_text(str(error.get("Message", str(exc))))
        return _provider_error_for_code(
            code,
            message,
            lake_formation=_identifies_lake_formation(exc),
        )
    if isinstance(exc, botocore.exceptions.ParamValidationError):
        return ValidationError(redact_text(str(exc)))
    if isinstance(exc, KeyError | TypeError | ValueError):
        return ValidationError(f"malformed Glue response: {redact_text(str(exc))}")
    return None


def raise_mapped_glue_error(exc: Exception) -> NoReturn:
    """Raise the mapped provider error, or preserve an unrelated exception."""
    mapped = map_glue_error(exc)
    if mapped is None:
        raise exc
    raise mapped from exc


def _identifies_lake_formation(exc: botocore.exceptions.ClientError) -> bool:
    error = exc.response.get("Error", {})
    candidates = (
        error.get("Message", ""),
        error.get("Service", ""),
        error.get("ServiceName", ""),
        exc.operation_name,
    )
    normalized = " ".join(str(candidate) for candidate in candidates).lower()
    return "lake formation" in normalized or "lakeformation" in normalized


def _provider_error_for_code(
    code: str,
    message: str,
    *,
    lake_formation: bool = False,
) -> ProviderError:
    if code in _ACCESS_DENIED_CODES:
        if lake_formation:
            return LakeFormationPermissionError(message)
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


class GlueClient:
    """One-page Glue API facade that maps every wire value to domain records."""

    def __init__(self, *, aws_session: AwsSession, connection: Connection) -> None:
        self._aws_session = aws_session
        self._connection = connection
        self._caller_identity: tuple[str, str] | None = None
        self._caller_identity_lock = asyncio.Lock()

    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        kwargs: dict[str, object] = {"MaxResults": 100}
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_databases(**kwargs)
            rows = [
                self._map_database(item)
                for item in _required_response_items(response, "DatabaseList")
            ]
            return rows, _response_token(response)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def list_tables_page(
        self,
        database: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[TableSummary], str | None]:
        kwargs: dict[str, object] = {
            "DatabaseName": database,
            "MaxResults": 100,
        }
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_tables(**kwargs)
            rows = [
                self._map_table_summary(item, database_name=database)
                for item in _response_items(response, "TableList")
            ]
            return rows, _response_token(response)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def get_table(self, ref: TableRef) -> TableDetail:
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_table(
                    DatabaseName=ref.database_name,
                    Name=ref.table_name,
                )
            table = _required_mapping(_response_mapping(response), "Table")
            return self._map_table_detail(table, ref=ref)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def list_partitions_page(
        self,
        ref: TableRef,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PartitionSummary], str | None]:
        kwargs: dict[str, object] = {
            "DatabaseName": ref.database_name,
            "TableName": ref.table_name,
        }
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_partitions(**kwargs)
            rows = [self._map_partition(item) for item in _response_items(response, "Partitions")]
            return rows, _response_token(response)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def get_column_statistics(
        self,
        ref: TableRef,
        columns: Sequence[str],
    ) -> tuple[ColumnStatistics, ...]:
        if not columns:
            return ()
        try:
            rows: list[ColumnStatistics] = []
            async with await self._aws_session.client(self._connection, "glue") as client:
                for offset in range(0, len(columns), _COLUMN_STATISTICS_BATCH_SIZE):
                    batch = list(columns[offset : offset + _COLUMN_STATISTICS_BATCH_SIZE])
                    response = await client.get_column_statistics_for_table(
                        DatabaseName=ref.database_name,
                        TableName=ref.table_name,
                        ColumnNames=batch,
                    )
                    _raise_column_statistics_errors(response)
                    rows.extend(
                        self._map_column_statistics(item)
                        for item in _response_items(response, "ColumnStatisticsList")
                    )
            return tuple(rows)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def list_jobs_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[GlueJobSummary], str | None]:
        kwargs: dict[str, object] = {"MaxResults": 100}
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_jobs(**kwargs)
            rows = [self._map_job(item) for item in _response_items(response, "Jobs")]
            return rows, _response_token(response)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def list_job_runs_page(
        self,
        job_name: str,
        *,
        start_token: str | None = None,
        states: Sequence[str] = (),
    ) -> tuple[list[GlueJobRunSummary], str | None]:
        kwargs: dict[str, object] = {
            "JobName": job_name,
            "MaxResults": 200,
        }
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                remote_state_filter = bool(states) and _supports_request_parameter(
                    client,
                    operation_name="GetJobRuns",
                    parameter_name="States",
                )
                if remote_state_filter:
                    kwargs["States"] = list(states)
                response = await client.get_job_runs(**kwargs)
            rows = [
                self._map_job_run(item, job_name=job_name)
                for item in _response_items(response, "JobRuns")
            ]
            if states and not remote_state_filter:
                state_filter = frozenset(states)
                rows = [row for row in rows if row.state in state_filter]
            return rows, _response_token(response)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def list_crawlers_page(
        self,
        *,
        start_token: str | None = None,
        state: str | None = None,
    ) -> tuple[list[GlueCrawlerSummary], str | None]:
        kwargs: dict[str, object] = {"MaxResults": 100}
        if start_token is not None:
            kwargs["NextToken"] = start_token
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_crawlers(**kwargs)
            rows = [
                self._map_crawler_summary(item) for item in _response_items(response, "Crawlers")
            ]
            if state is not None:
                rows = [row for row in rows if row.state == state]
            return rows, _response_token(response)
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def get_crawler(self, name: str) -> GlueCrawlerDetail:
        try:
            async with await self._aws_session.client(self._connection, "glue") as client:
                response = await client.get_crawler(Name=name)
            crawler = _required_mapping(_response_mapping(response), "Crawler")
        except Exception as exc:
            raise_mapped_glue_error(exc)

        warnings: list[str] = []
        tags: tuple[tuple[str, str], ...] = ()
        metrics_result = _CrawlerMetricsResult(None, None)

        try:
            tags = await self._get_crawler_tags(name)
        except Exception as exc:
            mapped = map_glue_error(exc)
            if mapped is None:
                raise
            if not isinstance(mapped, PermissionDeniedError):
                raise mapped from exc
            warnings.append(_supplemental_warning("Tags", mapped))

        try:
            metrics_result = await self._get_crawler_metrics_result(name)
        except Exception as exc:
            mapped = map_glue_error(exc)
            if mapped is None:
                raise
            if not isinstance(mapped, PermissionDeniedError):
                raise mapped from exc
            warnings.append(_supplemental_warning("Crawler metrics", mapped))

        try:
            return self._map_crawler_detail(
                crawler,
                tags=tags,
                metrics_result=metrics_result,
                warnings=tuple(warnings),
            )
        except Exception as exc:
            raise_mapped_glue_error(exc)

    async def get_crawler_metrics(self, name: str) -> GlueCrawlerMetrics | None:
        try:
            return (await self._get_crawler_metrics_result(name)).metrics
        except Exception as exc:
            raise_mapped_glue_error(exc)

    def _map_database(self, item: Mapping[str, Any]) -> DatabaseSummary:
        name = _required_string(item, "Name")
        return DatabaseSummary(
            ref=DatabaseRef(
                _CATALOG_NAME,
                name,
                self._connection.name,
                self._connection.region,
            ),
            description=_optional_string(item, "Description"),
            location_uri=_optional_string(item, "LocationUri"),
            created_at=_optional_datetime(item, "CreateTime"),
        )

    def _map_table_summary(
        self,
        item: Mapping[str, Any],
        *,
        database_name: str,
        ref: TableRef | None = None,
    ) -> TableSummary:
        table_name = _required_string(item, "Name")
        table_ref = ref or TableRef(
            _CATALOG_NAME,
            database_name,
            table_name,
            self._connection.name,
            self._connection.region,
        )
        return TableSummary(
            ref=table_ref,
            description=_optional_string(item, "Description"),
            owner=_optional_string(item, "Owner"),
            table_type=_optional_string(item, "TableType"),
            created_at=_optional_datetime(item, "CreateTime"),
            updated_at=_optional_datetime(item, "UpdateTime"),
        )

    def _map_table_detail(
        self,
        item: Mapping[str, Any],
        *,
        ref: TableRef,
    ) -> TableDetail:
        storage = _optional_mapping(item, "StorageDescriptor")
        parameters = _string_pairs(_optional_mapping(item, "Parameters"))
        parameter_map = dict(parameters)
        partition_items = _mapping_items(
            _optional_sequence(item, "PartitionKeys"),
            field="PartitionKeys",
        )
        column_items = _mapping_items(
            _optional_sequence(storage, "Columns"),
            field="StorageDescriptor.Columns",
        )
        serde = _optional_mapping(storage, "SerdeInfo")
        return TableDetail(
            summary=self._map_table_summary(
                item,
                database_name=ref.database_name,
                ref=ref,
            ),
            columns=tuple(self._map_column(column, partition_key=False) for column in column_items),
            partition_keys=tuple(
                self._map_column(column, partition_key=True) for column in partition_items
            ),
            storage=StorageDescriptor(
                location=_optional_string(storage, "Location"),
                input_format=_optional_string(storage, "InputFormat"),
                output_format=_optional_string(storage, "OutputFormat"),
                serde=_optional_string(serde, "SerializationLibrary"),
                compressed=_optional_bool(storage, "Compressed", default=False),
                bucket_count=_optional_int(storage, "NumberOfBuckets") or 0,
            ),
            classification=parameter_map.get("classification"),
            table_format=_detect_table_format(
                parameters=parameter_map,
                table_type=_optional_string(item, "TableType"),
            ),
            parameters=parameters,
        )

    @staticmethod
    def _map_column(item: Mapping[str, Any], *, partition_key: bool) -> Column:
        return Column(
            name=_required_string(item, "Name"),
            type_name=_optional_string(item, "Type") or "",
            comment=_optional_string(item, "Comment"),
            partition_key=partition_key,
        )

    @staticmethod
    def _map_partition(item: Mapping[str, Any]) -> PartitionSummary:
        storage = _optional_mapping(item, "StorageDescriptor")
        return PartitionSummary(
            values=tuple(_string_sequence(item, "Values")),
            created_at=_optional_datetime(item, "CreationTime"),
            last_accessed_at=_optional_datetime(item, "LastAccessTime"),
            storage_location=_optional_string(storage, "Location"),
        )

    @staticmethod
    def _map_column_statistics(item: Mapping[str, Any]) -> ColumnStatistics:
        statistics_data = _required_mapping(item, "StatisticsData")
        statistics_type = _required_string(statistics_data, "Type")
        payload_key = {
            "BINARY": "BinaryColumnStatisticsData",
            "BOOLEAN": "BooleanColumnStatisticsData",
            "DATE": "DateColumnStatisticsData",
            "DECIMAL": "DecimalColumnStatisticsData",
            "DOUBLE": "DoubleColumnStatisticsData",
            "LONG": "LongColumnStatisticsData",
            "STRING": "StringColumnStatisticsData",
        }.get(statistics_type)
        if payload_key is None:
            raise ValueError(f"unknown column statistics type: {statistics_type}")
        payload = _required_mapping(statistics_data, payload_key)
        return ColumnStatistics(
            column_name=_required_string(item, "ColumnName"),
            type_name=_required_string(item, "ColumnType"),
            analyzed_at=_required_datetime(item, "AnalyzedTime"),
            values=tuple((str(key), _statistics_value(value)) for key, value in payload.items()),
        )

    @staticmethod
    def _map_job(item: Mapping[str, Any]) -> GlueJobSummary:
        command = _required_mapping(item, "Command")
        return GlueJobSummary(
            name=_required_string(item, "Name"),
            description=_optional_string(item, "Description"),
            role=_required_string(item, "Role"),
            glue_version=_optional_string(item, "GlueVersion"),
            command_name=_required_string(command, "Name"),
            script_location=_optional_string(command, "ScriptLocation"),
            worker_type=_optional_string(item, "WorkerType"),
            worker_count=_optional_int(item, "NumberOfWorkers"),
            timeout_minutes=_optional_int(item, "Timeout"),
            max_retries=_optional_int(item, "MaxRetries"),
            default_arguments=_string_pairs(_optional_mapping(item, "DefaultArguments")),
        )

    @staticmethod
    def _map_job_run(
        item: Mapping[str, Any],
        *,
        job_name: str,
    ) -> GlueJobRunSummary:
        predecessor_items = _mapping_items(
            _optional_sequence(item, "PredecessorRuns"),
            field="PredecessorRuns",
        )
        error_message = _optional_string(item, "ErrorMessage")
        state_detail = _optional_string(item, "StateDetail")
        return GlueJobRunSummary(
            job_name=_optional_string(item, "JobName") or job_name,
            run_id=_required_string(item, "Id"),
            state=_required_string(item, "JobRunState"),
            attempt=_optional_int(item, "Attempt") or 0,
            trigger_name=_optional_string(item, "TriggerName"),
            started_at=_optional_datetime(item, "StartedOn"),
            completed_at=_optional_datetime(item, "CompletedOn"),
            execution_time_seconds=_optional_int(item, "ExecutionTime"),
            execution_class=_optional_string(item, "ExecutionClass"),
            allocated_capacity=_optional_int(item, "AllocatedCapacity"),
            arguments=_string_pairs(_optional_mapping(item, "Arguments")),
            predecessor_run_ids=tuple(
                _required_string(predecessor, "RunId") for predecessor in predecessor_items
            ),
            error_message=redact_text(error_message) if error_message else None,
            state_detail=redact_text(state_detail) if state_detail else None,
            log_group_name=_optional_string(item, "LogGroupName"),
        )

    @staticmethod
    def _map_crawler_summary(item: Mapping[str, Any]) -> GlueCrawlerSummary:
        schedule = _optional_mapping(item, "Schedule")
        return GlueCrawlerSummary(
            name=_required_string(item, "Name"),
            state=_required_string(item, "State"),
            role=_required_string(item, "Role"),
            database_name=_optional_string(item, "DatabaseName"),
            schedule_expression=_optional_string(schedule, "ScheduleExpression"),
        )

    def _map_crawler_detail(
        self,
        item: Mapping[str, Any],
        *,
        tags: tuple[tuple[str, str], ...],
        metrics_result: _CrawlerMetricsResult,
        warnings: tuple[str, ...],
    ) -> GlueCrawlerDetail:
        recrawl = _optional_mapping(item, "RecrawlPolicy")
        schema_change = _optional_mapping(item, "SchemaChangePolicy")
        lake_formation = _optional_mapping(item, "LakeFormationConfiguration")
        last_crawl = _optional_mapping(item, "LastCrawl")
        last_error = _optional_string(last_crawl, "ErrorMessage")
        return GlueCrawlerDetail(
            summary=self._map_crawler_summary(item),
            targets=_crawler_targets(_optional_mapping(item, "Targets")),
            classifiers=tuple(_string_sequence(item, "Classifiers")),
            recrawl_behavior=_optional_string(recrawl, "RecrawlBehavior"),
            schema_update_behavior=_optional_string(schema_change, "UpdateBehavior"),
            schema_delete_behavior=_optional_string(schema_change, "DeleteBehavior"),
            security_configuration=_optional_string(item, "CrawlerSecurityConfiguration"),
            lake_formation_account_id=_optional_string(lake_formation, "AccountId"),
            use_lake_formation_credentials=_optional_bool(
                lake_formation,
                "UseLakeFormationCredentials",
                default=False,
            ),
            tags=tags,
            last_crawl_status=_optional_string(last_crawl, "Status"),
            last_crawl_started_at=_optional_datetime(last_crawl, "StartTime"),
            last_crawl_duration_seconds=metrics_result.last_runtime_seconds,
            last_crawl_error=redact_text(last_error) if last_error else None,
            metrics=metrics_result.metrics,
            supplemental_warnings=warnings,
        )

    async def _get_crawler_tags(
        self,
        name: str,
    ) -> tuple[tuple[str, str], ...]:
        account_id, partition = await self._resolve_caller_identity()
        resource_arn = f"arn:{partition}:glue:{self._connection.region}:{account_id}:crawler/{name}"
        async with await self._aws_session.client(self._connection, "glue") as client:
            response = await client.get_tags(ResourceArn=resource_arn)
        return _string_pairs(_optional_mapping(_response_mapping(response), "Tags"))

    async def _resolve_caller_identity(self) -> tuple[str, str]:
        if self._caller_identity is not None:
            return self._caller_identity
        async with self._caller_identity_lock:
            if self._caller_identity is not None:
                return self._caller_identity
            async with await self._aws_session.client(self._connection, "sts") as client:
                response = _response_mapping(await client.get_caller_identity())
            account_id = _required_string(response, "Account")
            arn = _required_string(response, "Arn")
            arn_parts = arn.split(":")
            if len(arn_parts) < 2 or arn_parts[0] != "arn" or not arn_parts[1]:
                raise ValueError(f"invalid caller identity ARN: {arn}")
            self._caller_identity = (account_id, arn_parts[1])
            return self._caller_identity

    async def _get_crawler_metrics_result(
        self,
        name: str,
    ) -> _CrawlerMetricsResult:
        async with await self._aws_session.client(self._connection, "glue") as client:
            response = await client.get_crawler_metrics(CrawlerNameList=[name])
        items = _response_items(response, "CrawlerMetricsList")
        if not items:
            return _CrawlerMetricsResult(None, None)
        item = items[0]
        return _CrawlerMetricsResult(
            metrics=GlueCrawlerMetrics(
                crawler_name=_optional_string(item, "CrawlerName") or name,
                still_estimating=_optional_bool(item, "StillEstimating", default=False),
                time_left_seconds=_optional_float(item, "TimeLeftSeconds"),
                median_runtime_seconds=_optional_float(item, "MedianRuntimeSeconds"),
                tables_created=_optional_int(item, "TablesCreated") or 0,
                tables_updated=_optional_int(item, "TablesUpdated") or 0,
                tables_deleted=_optional_int(item, "TablesDeleted") or 0,
            ),
            last_runtime_seconds=_optional_float(item, "LastRuntimeSeconds"),
        )


def _response_mapping(response: object) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        raise TypeError("response is not a mapping")
    return cast(Mapping[str, Any], response)


def _supports_request_parameter(
    client: object,
    *,
    operation_name: str,
    parameter_name: str,
) -> bool:
    """Default to the planned request shape when a test double has no model."""
    try:
        operation = client.meta.service_model.operation_model(operation_name)  # type: ignore[attr-defined]
        input_shape = operation.input_shape
        members = input_shape.members
    except (AttributeError, KeyError, TypeError):
        return True
    if not isinstance(members, Mapping):
        return True
    return parameter_name in members


def _response_items(response: object, field: str) -> list[Mapping[str, Any]]:
    mapping = _response_mapping(response)
    return _mapping_items(_optional_sequence(mapping, field), field=field)


def _required_response_items(
    response: object,
    field: str,
) -> list[Mapping[str, Any]]:
    mapping = _response_mapping(response)
    return _mapping_items(_required_sequence(mapping, field), field=field)


def _raise_column_statistics_errors(response: object) -> None:
    errors = _response_items(response, "Errors")
    if not errors:
        return
    item = errors[0]
    detail = _optional_mapping(item, "Error")
    code = _optional_string(detail, "ErrorCode") or "ColumnStatisticsError"
    message = _optional_string(detail, "ErrorMessage") or code
    column_name = _optional_string(item, "ColumnName")
    visible_message = f"{column_name}: {message}" if column_name else message
    normalized = visible_message.lower()
    raise _provider_error_for_code(
        code,
        redact_text(visible_message),
        lake_formation="lake formation" in normalized or "lakeformation" in normalized,
    )


def _response_token(response: object) -> str | None:
    return _optional_string(_response_mapping(response), "NextToken")


def _required_mapping(
    mapping: Mapping[str, Any],
    field: str,
) -> Mapping[str, Any]:
    value = mapping[field]
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} is not a mapping")
    return cast(Mapping[str, Any], value)


def _optional_mapping(
    mapping: Mapping[str, Any],
    field: str,
) -> Mapping[str, Any]:
    value = mapping.get(field)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} is not a mapping")
    return cast(Mapping[str, Any], value)


def _mapping_items(
    values: Sequence[object],
    *,
    field: str,
) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise TypeError(f"{field} contains a non-mapping item")
        items.append(cast(Mapping[str, Any], value))
    return items


def _optional_sequence(
    mapping: Mapping[str, Any],
    field: str,
) -> Sequence[object]:
    value = mapping.get(field)
    if value is None:
        return ()
    return _validated_sequence(value, field=field)


def _required_sequence(
    mapping: Mapping[str, Any],
    field: str,
) -> Sequence[object]:
    return _validated_sequence(mapping[field], field=field)


def _validated_sequence(value: object, *, field: str) -> Sequence[object]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise TypeError(f"{field} is not a sequence")
    return cast(Sequence[object], value)


def _required_string(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping[field]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is not a non-empty string")
    return value


def _optional_string(mapping: Mapping[str, Any], field: str) -> str | None:
    value = mapping.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} is not a string")
    return value


def _string_sequence(mapping: Mapping[str, Any], field: str) -> tuple[str, ...]:
    values = _optional_sequence(mapping, field)
    if not all(isinstance(value, str) for value in values):
        raise TypeError(f"{field} contains a non-string item")
    return tuple(cast(str, value) for value in values)


def _string_pairs(mapping: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("string map contains a non-string key or value")
        pairs.append((key, value))
    return tuple(sorted(pairs, key=lambda item: item[0]))


def _required_datetime(mapping: Mapping[str, Any], field: str) -> datetime:
    value = mapping[field]
    if not isinstance(value, datetime):
        raise TypeError(f"{field} is not a datetime")
    return value


def _optional_datetime(
    mapping: Mapping[str, Any],
    field: str,
) -> datetime | None:
    value = mapping.get(field)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{field} is not a datetime")
    return value


def _optional_int(mapping: Mapping[str, Any], field: str) -> int | None:
    value = mapping.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} is not an integer")
    return value


def _optional_float(mapping: Mapping[str, Any], field: str) -> float | None:
    value = mapping.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} is not numeric")
    return float(value)


def _optional_bool(
    mapping: Mapping[str, Any],
    field: str,
    *,
    default: bool,
) -> bool:
    value = mapping.get(field)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TypeError(f"{field} is not a boolean")
    return value


def _detect_table_format(
    *,
    parameters: Mapping[str, str],
    table_type: str | None,
) -> TableFormat:
    markers = " ".join(
        (
            parameters.get("classification", ""),
            parameters.get("table_type", ""),
            parameters.get("spark.sql.sources.provider", ""),
            parameters.get("metadata_location", ""),
        )
    ).lower()
    if "iceberg" in markers:
        return TableFormat.ICEBERG
    if "hudi" in markers:
        return TableFormat.HUDI
    if "delta" in markers:
        return TableFormat.DELTA
    if "hive" in markers or table_type in {
        "EXTERNAL_TABLE",
        "MANAGED_TABLE",
        "VIRTUAL_VIEW",
    }:
        return TableFormat.HIVE
    return TableFormat.OTHER


def _statistics_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Mapping | list | tuple):
        return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return str(value)


def _crawler_targets(targets: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for field in ("S3Targets", "JdbcTargets", "MongoDBTargets", "DynamoDBTargets"):
        for target in _mapping_items(_optional_sequence(targets, field), field=field):
            path = _optional_string(target, "Path")
            if path is not None:
                values.append(path)
    for target in _mapping_items(
        _optional_sequence(targets, "CatalogTargets"),
        field="CatalogTargets",
    ):
        database = _required_string(target, "DatabaseName")
        values.extend(f"{database}/{table}" for table in _string_sequence(target, "Tables"))
    for target in _mapping_items(
        _optional_sequence(targets, "DeltaTargets"),
        field="DeltaTargets",
    ):
        values.extend(_string_sequence(target, "DeltaTables"))
    for field in ("IcebergTargets", "HudiTargets"):
        for target in _mapping_items(_optional_sequence(targets, field), field=field):
            values.extend(_string_sequence(target, "Paths"))
    return tuple(values)


def _supplemental_warning(label: str, error: ProviderError) -> str:
    return redact_text(f"{label} unavailable: {error}")


__all__ = [
    "GlueClient",
    "GlueCrawlerDetail",
    "GlueCrawlerMetrics",
    "GlueCrawlerSummary",
    "GlueJobRunSummary",
    "GlueJobSummary",
    "LakeFormationPermissionError",
    "map_glue_error",
    "raise_mapped_glue_error",
]
