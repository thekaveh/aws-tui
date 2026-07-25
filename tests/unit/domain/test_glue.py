"""Contract tests for the paginated AWS Glue domain client."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import call

import botocore.exceptions
import pytest

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
from aws_tui.domain.glue import (
    GlueClient,
    GlueCrawlerDetail,
    GlueCrawlerMetrics,
    GlueCrawlerSummary,
    GlueJobRunSummary,
    GlueJobSummary,
    LakeFormationPermissionError,
    map_glue_error,
)
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from tests.unit.domain._fake_aws_client import FakeAwsClient, FakeAwsSession

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


def _connection(*, region: str = "us-east-1") -> Connection:
    return Connection(
        name="dev",
        kind="aws",
        region=region,
        source="profile",
        profile="dev",
    )


def _client(
    *,
    glue: FakeAwsClient | None = None,
    sts: FakeAwsClient | None = None,
    region: str = "us-east-1",
) -> tuple[GlueClient, FakeAwsClient, FakeAwsClient, FakeAwsSession]:
    glue = glue or FakeAwsClient()
    sts = sts or FakeAwsClient()
    session = FakeAwsSession({"glue": glue, "sts": sts})
    client = GlueClient(
        aws_session=cast(AwsSession, session),
        connection=_connection(region=region),
    )
    return client, glue, sts, session


def _client_error(
    code: str,
    message: str = "request failed",
    *,
    operation: str = "GetJobs",
    service: str | None = None,
) -> botocore.exceptions.ClientError:
    error: dict[str, str] = {"Code": code, "Message": message}
    if service is not None:
        error["Service"] = service
    return botocore.exceptions.ClientError(
        {
            "Error": error,
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _crawler(
    name: str = "events-crawler",
    *,
    state: str = "READY",
) -> dict[str, object]:
    return {
        "Name": name,
        "State": state,
        "Role": "arn:aws:iam::123456789012:role/GlueCrawler",
        "DatabaseName": "analytics",
        "Schedule": {"ScheduleExpression": "cron(0 2 * * ? *)"},
    }


def test_glue_records_are_frozen_slot_dataclasses_with_exact_fields() -> None:
    record_types = (
        GlueJobSummary,
        GlueJobRunSummary,
        GlueCrawlerSummary,
        GlueCrawlerMetrics,
        GlueCrawlerDetail,
    )
    assert all(is_dataclass(record_type) for record_type in record_types)
    assert all(hasattr(record_type, "__slots__") for record_type in record_types)
    assert [field.name for field in fields(GlueJobSummary)] == [
        "name",
        "description",
        "role",
        "glue_version",
        "command_name",
        "script_location",
        "worker_type",
        "worker_count",
        "timeout_minutes",
        "max_retries",
        "default_arguments",
    ]

    summary = GlueCrawlerSummary("crawler", "READY", "role", None, None)
    with pytest.raises(FrozenInstanceError):
        summary.state = "RUNNING"  # type: ignore[misc]


async def test_list_databases_page_maps_optional_fields_and_opaque_token() -> None:
    client, glue, _, _ = _client()
    glue.get_databases.return_value = {
        "DatabaseList": [
            {
                "Name": "analytics",
                "Description": "curated data",
                "LocationUri": "s3://lake/analytics/",
                "CreateTime": NOW,
            },
            {"Name": "scratch"},
        ],
        "NextToken": "page-2",
    }

    rows, token = await client.list_databases_page(start_token="opaque")

    assert rows == [
        DatabaseSummary(
            DatabaseRef("AwsDataCatalog", "analytics", "dev", "us-east-1"),
            "curated data",
            "s3://lake/analytics/",
            NOW,
        ),
        DatabaseSummary(
            DatabaseRef("AwsDataCatalog", "scratch", "dev", "us-east-1"),
            None,
            None,
            None,
        ),
    ]
    assert token == "page-2"
    glue.get_databases.assert_awaited_once_with(MaxResults=100, NextToken="opaque")


async def test_list_databases_page_omits_absent_token() -> None:
    client, glue, _, _ = _client()
    glue.get_databases.return_value = {"DatabaseList": []}

    await client.list_databases_page()

    glue.get_databases.assert_awaited_once_with(MaxResults=100)


async def test_list_databases_page_rejects_missing_required_database_list() -> None:
    client, glue, _, _ = _client()
    glue.get_databases.return_value = {}

    with pytest.raises(ValidationError, match="DatabaseList"):
        await client.list_databases_page()


async def test_list_tables_page_maps_context_and_next_token() -> None:
    client, glue, _, _ = _client()
    glue.get_tables.return_value = {
        "TableList": [
            {
                "Name": "events",
                "DatabaseName": "analytics",
                "Description": "curated events",
                "Owner": "data-platform",
                "TableType": "EXTERNAL_TABLE",
                "CreateTime": NOW,
                "UpdateTime": NOW,
            }
        ],
        "NextToken": "page-2",
    }

    rows, token = await client.list_tables_page("analytics")

    assert rows == [
        TableSummary(
            TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1"),
            "curated events",
            "data-platform",
            "EXTERNAL_TABLE",
            NOW,
            NOW,
        )
    ]
    assert token == "page-2"
    glue.get_tables.assert_awaited_once_with(DatabaseName="analytics", MaxResults=100)


async def test_list_tables_page_passes_token_without_none_keys() -> None:
    client, glue, _, _ = _client()
    glue.get_tables.return_value = {"TableList": []}

    await client.list_tables_page("analytics", start_token="opaque")

    glue.get_tables.assert_awaited_once_with(
        DatabaseName="analytics",
        MaxResults=100,
        NextToken="opaque",
    )


async def test_get_table_maps_columns_storage_parameters_and_iceberg_format() -> None:
    client, glue, _, _ = _client()
    ref = TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1")
    glue.get_table.return_value = {
        "Table": {
            "Name": "events",
            "DatabaseName": "analytics",
            "Description": "event stream",
            "Owner": "platform",
            "TableType": "EXTERNAL_TABLE",
            "CreateTime": NOW,
            "UpdateTime": NOW,
            "Parameters": {
                "write.format.default": "parquet",
                "table_type": "ICEBERG",
                "classification": "iceberg",
            },
            "StorageDescriptor": {
                "Columns": [
                    {"Name": "event_id", "Type": "string", "Comment": "identifier"},
                    {"Name": "payload"},
                ],
                "Location": "s3://lake/events/",
                "InputFormat": "input",
                "OutputFormat": "output",
                "Compressed": True,
                "NumberOfBuckets": 8,
                "SerdeInfo": {"SerializationLibrary": "serde"},
            },
            "PartitionKeys": [{"Name": "event_date", "Type": "date"}],
        }
    }

    detail = await client.get_table(ref)

    assert detail == TableDetail(
        summary=TableSummary(
            ref=ref,
            description="event stream",
            owner="platform",
            table_type="EXTERNAL_TABLE",
            created_at=NOW,
            updated_at=NOW,
        ),
        columns=(
            Column("event_id", "string", "identifier", False),
            Column("payload", "", None, False),
        ),
        partition_keys=(Column("event_date", "date", None, True),),
        storage=StorageDescriptor(
            "s3://lake/events/",
            "input",
            "output",
            "serde",
            True,
            8,
        ),
        classification="iceberg",
        table_format=TableFormat.ICEBERG,
        parameters=(
            ("classification", "iceberg"),
            ("table_type", "ICEBERG"),
            ("write.format.default", "parquet"),
        ),
    )
    glue.get_table.assert_awaited_once_with(DatabaseName="analytics", Name="events")


@pytest.mark.parametrize(
    ("parameters", "table_type", "expected"),
    [
        ({"classification": "hudi"}, "EXTERNAL_TABLE", TableFormat.HUDI),
        ({"spark.sql.sources.provider": "delta"}, "EXTERNAL_TABLE", TableFormat.DELTA),
        ({"classification": "parquet"}, "EXTERNAL_TABLE", TableFormat.HIVE),
        ({}, "UNKNOWN", TableFormat.OTHER),
    ],
)
async def test_get_table_detects_supported_formats(
    parameters: dict[str, str],
    table_type: str,
    expected: TableFormat,
) -> None:
    client, glue, _, _ = _client()
    glue.get_table.return_value = {
        "Table": {
            "Name": "events",
            "Parameters": parameters,
            "TableType": table_type,
        }
    }

    detail = await client.get_table(
        TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1")
    )

    assert detail.table_format is expected


async def test_list_partitions_page_maps_one_page_and_omits_optional_token() -> None:
    client, glue, _, _ = _client()
    ref = TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1")
    glue.get_partitions.return_value = {
        "Partitions": [
            {
                "Values": ["2026-07-25", "12"],
                "CreationTime": NOW,
                "LastAccessTime": NOW,
                "StorageDescriptor": {"Location": "s3://lake/events/date=2026-07-25/"},
            },
            {},
        ],
        "NextToken": "next-partitions",
    }

    rows, token = await client.list_partitions_page(ref)

    assert rows == [
        PartitionSummary(
            ("2026-07-25", "12"),
            NOW,
            NOW,
            "s3://lake/events/date=2026-07-25/",
        ),
        PartitionSummary((), None, None, None),
    ]
    assert token == "next-partitions"
    glue.get_partitions.assert_awaited_once_with(
        DatabaseName="analytics",
        TableName="events",
    )


async def test_list_partitions_page_passes_only_opaque_token_when_present() -> None:
    client, glue, _, _ = _client()
    glue.get_partitions.return_value = {"Partitions": []}
    ref = TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1")

    await client.list_partitions_page(ref, start_token="opaque")

    glue.get_partitions.assert_awaited_once_with(
        DatabaseName="analytics",
        TableName="events",
        NextToken="opaque",
    )


async def test_get_column_statistics_batches_at_glue_limit_and_maps_values() -> None:
    client, glue, _, _ = _client()
    ref = TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1")
    columns = tuple(f"column_{index}" for index in range(101))
    glue.get_column_statistics_for_table.side_effect = [
        {
            "ColumnStatisticsList": [
                {
                    "ColumnName": "column_0",
                    "ColumnType": "bigint",
                    "AnalyzedTime": NOW,
                    "StatisticsData": {
                        "Type": "LONG",
                        "LongColumnStatisticsData": {
                            "NumberOfNulls": 2,
                            "MaximumValue": 99,
                            "MinimumValue": 1,
                        },
                    },
                }
            ]
        },
        {
            "ColumnStatisticsList": [
                {
                    "ColumnName": "column_100",
                    "ColumnType": "boolean",
                    "AnalyzedTime": NOW,
                    "StatisticsData": {
                        "Type": "BOOLEAN",
                        "BooleanColumnStatisticsData": {
                            "NumberOfTrues": 8,
                            "NumberOfFalses": 1,
                            "NumberOfNulls": 0,
                        },
                    },
                }
            ]
        },
    ]

    result = await client.get_column_statistics(ref, columns)

    assert result == (
        ColumnStatistics(
            "column_0",
            "bigint",
            NOW,
            (
                ("MaximumValue", "99"),
                ("MinimumValue", "1"),
                ("NumberOfNulls", "2"),
            ),
        ),
        ColumnStatistics(
            "column_100",
            "boolean",
            NOW,
            (
                ("NumberOfFalses", "1"),
                ("NumberOfNulls", "0"),
                ("NumberOfTrues", "8"),
            ),
        ),
    )
    assert glue.get_column_statistics_for_table.await_args_list == [
        call(
            DatabaseName="analytics",
            TableName="events",
            ColumnNames=list(columns[:100]),
        ),
        call(
            DatabaseName="analytics",
            TableName="events",
            ColumnNames=["column_100"],
        ),
    ]


async def test_get_column_statistics_with_no_columns_makes_no_request() -> None:
    client, glue, _, _ = _client()
    ref = TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1")

    assert await client.get_column_statistics(ref, ()) == ()
    glue.get_column_statistics_for_table.assert_not_awaited()


async def test_get_column_statistics_surfaces_redacted_per_column_errors() -> None:
    client, glue, _, _ = _client()
    ref = TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-east-1")
    glue.get_column_statistics_for_table.return_value = {
        "ColumnStatisticsList": [],
        "Errors": [
            {
                "ColumnName": "secret_column",
                "Error": {
                    "ErrorCode": "AccessDeniedException",
                    "ErrorMessage": "denied token=column-secret",
                },
            }
        ],
    }

    with pytest.raises(PermissionDeniedError) as exc_info:
        await client.get_column_statistics(ref, ("secret_column",))

    assert "column-secret" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


async def test_list_jobs_page_maps_job_definitions_and_request() -> None:
    client, glue, _, _ = _client()
    glue.get_jobs.return_value = {
        "Jobs": [
            {
                "Name": "daily-etl",
                "Description": "daily load",
                "Role": "GlueJobRole",
                "GlueVersion": "5.0",
                "Command": {
                    "Name": "glueetl",
                    "ScriptLocation": "s3://jobs/daily.py",
                },
                "WorkerType": "G.2X",
                "NumberOfWorkers": 10,
                "Timeout": 60,
                "MaxRetries": 2,
                "DefaultArguments": {"--zeta": "last", "--alpha": "first"},
            }
        ],
        "NextToken": "jobs-2",
    }

    rows, token = await client.list_jobs_page()

    assert rows == [
        GlueJobSummary(
            "daily-etl",
            "daily load",
            "GlueJobRole",
            "5.0",
            "glueetl",
            "s3://jobs/daily.py",
            "G.2X",
            10,
            60,
            2,
            (("--alpha", "first"), ("--zeta", "last")),
        )
    ]
    assert token == "jobs-2"
    glue.get_jobs.assert_awaited_once_with(MaxResults=100)


async def test_list_job_runs_page_maps_states_and_omits_empty_filter() -> None:
    client, glue, _, _ = _client()
    glue.get_job_runs.return_value = {
        "JobRuns": [
            {
                "JobName": "daily-etl",
                "Id": "jr_1",
                "JobRunState": "SUCCEEDED",
                "Attempt": 1,
                "TriggerName": "daily",
                "StartedOn": NOW,
                "CompletedOn": NOW,
                "ExecutionTime": 42,
                "ExecutionClass": "STANDARD",
                "AllocatedCapacity": 10,
                "Arguments": {"--date": "2026-07-25"},
                "PredecessorRuns": [{"RunId": "jr_0"}],
                "ErrorMessage": None,
                "StateDetail": "complete",
                "LogGroupName": "/aws-glue/jobs",
            }
        ],
        "NextToken": "runs-2",
    }

    rows, token = await client.list_job_runs_page("daily-etl")

    assert rows == [
        GlueJobRunSummary(
            "daily-etl",
            "jr_1",
            "SUCCEEDED",
            1,
            "daily",
            NOW,
            NOW,
            42,
            "STANDARD",
            10,
            (("--date", "2026-07-25"),),
            ("jr_0",),
            None,
            "complete",
            "/aws-glue/jobs",
        )
    ]
    assert token == "runs-2"
    glue.get_job_runs.assert_awaited_once_with(JobName="daily-etl", MaxResults=200)


async def test_list_job_runs_page_passes_token_and_non_empty_states() -> None:
    client, glue, _, _ = _client()
    glue.get_job_runs.return_value = {"JobRuns": []}

    await client.list_job_runs_page(
        "daily-etl",
        start_token="opaque",
        states=("RUNNING", "FAILED"),
    )

    glue.get_job_runs.assert_awaited_once_with(
        JobName="daily-etl",
        MaxResults=200,
        NextToken="opaque",
        States=["RUNNING", "FAILED"],
    )


async def test_list_job_runs_page_filters_locally_when_model_lacks_states() -> None:
    client, glue, _, _ = _client()
    glue.meta = SimpleNamespace(
        service_model=SimpleNamespace(
            operation_model=lambda _name: SimpleNamespace(
                input_shape=SimpleNamespace(
                    members={"JobName": object(), "NextToken": object(), "MaxResults": object()}
                )
            )
        )
    )
    glue.get_job_runs.return_value = {
        "JobRuns": [
            {
                "Id": "running",
                "JobRunState": "RUNNING",
            },
            {
                "Id": "failed",
                "JobRunState": "FAILED",
            },
        ]
    }

    rows, _ = await client.list_job_runs_page("daily-etl", states=("RUNNING",))

    assert [row.run_id for row in rows] == ["running"]
    glue.get_job_runs.assert_awaited_once_with(JobName="daily-etl", MaxResults=200)


async def test_list_crawlers_page_filters_state_locally() -> None:
    client, glue, _, _ = _client()
    glue.get_crawlers.return_value = {
        "Crawlers": [
            _crawler("ready", state="READY"),
            _crawler("running", state="RUNNING"),
        ],
        "NextToken": "crawlers-2",
    }

    rows, token = await client.list_crawlers_page(state="RUNNING")

    assert rows == [
        GlueCrawlerSummary(
            "running",
            "RUNNING",
            "arn:aws:iam::123456789012:role/GlueCrawler",
            "analytics",
            "cron(0 2 * * ? *)",
        )
    ]
    assert token == "crawlers-2"
    glue.get_crawlers.assert_awaited_once_with(MaxResults=100)


async def test_list_crawlers_page_passes_token_but_never_state() -> None:
    client, glue, _, _ = _client()
    glue.get_crawlers.return_value = {"Crawlers": []}

    await client.list_crawlers_page(start_token="opaque", state="READY")

    glue.get_crawlers.assert_awaited_once_with(MaxResults=100, NextToken="opaque")


async def test_get_crawler_metrics_maps_metrics_and_empty_response() -> None:
    client, glue, _, _ = _client()
    glue.get_crawler_metrics.side_effect = [
        {
            "CrawlerMetricsList": [
                {
                    "CrawlerName": "events-crawler",
                    "StillEstimating": True,
                    "TimeLeftSeconds": 12.5,
                    "MedianRuntimeSeconds": 44.25,
                    "TablesCreated": 2,
                    "TablesUpdated": 3,
                    "TablesDeleted": 1,
                }
            ]
        },
        {"CrawlerMetricsList": []},
    ]

    metrics = await client.get_crawler_metrics("events-crawler")
    missing = await client.get_crawler_metrics("missing")

    assert metrics == GlueCrawlerMetrics(
        "events-crawler",
        True,
        12.5,
        44.25,
        2,
        3,
        1,
    )
    assert missing is None
    assert glue.get_crawler_metrics.await_args_list == [
        call(CrawlerNameList=["events-crawler"]),
        call(CrawlerNameList=["missing"]),
    ]


@pytest.mark.parametrize(
    ("region", "identity_arn", "partition"),
    [
        ("us-east-1", "arn:aws:iam::123456789012:user/dev", "aws"),
        ("us-gov-west-1", "arn:aws-us-gov:iam::123456789012:user/dev", "aws-us-gov"),
        ("cn-north-1", "arn:aws-cn:iam::123456789012:user/dev", "aws-cn"),
    ],
)
async def test_get_crawler_combines_core_tags_metrics_and_partition_arn(
    region: str,
    identity_arn: str,
    partition: str,
) -> None:
    client, glue, sts, _ = _client(region=region)
    crawler = _crawler()
    crawler.update(
        {
            "Targets": {
                "S3Targets": [{"Path": "s3://lake/events/"}],
                "JdbcTargets": [{"Path": "analytics/events"}],
                "CatalogTargets": [{"DatabaseName": "source", "Tables": ["one", "two"]}],
                "IcebergTargets": [{"Paths": ["s3://iceberg/events/"]}],
            },
            "Classifiers": ["json"],
            "RecrawlPolicy": {"RecrawlBehavior": "CRAWL_NEW_FOLDERS_ONLY"},
            "SchemaChangePolicy": {
                "UpdateBehavior": "UPDATE_IN_DATABASE",
                "DeleteBehavior": "LOG",
            },
            "CrawlerSecurityConfiguration": "crawler-security",
            "LakeFormationConfiguration": {
                "AccountId": "999999999999",
                "UseLakeFormationCredentials": True,
            },
            "LastCrawl": {
                "Status": "SUCCEEDED",
                "StartTime": NOW,
                "ErrorMessage": None,
            },
        }
    )
    glue.get_crawler.return_value = {"Crawler": crawler}
    sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": identity_arn,
    }
    glue.get_tags.return_value = {"Tags": {"team": "platform", "env": "dev"}}
    glue.get_crawler_metrics.return_value = {
        "CrawlerMetricsList": [
            {
                "CrawlerName": "events-crawler",
                "StillEstimating": False,
                "LastRuntimeSeconds": 31.5,
                "MedianRuntimeSeconds": 30.0,
                "TablesCreated": 1,
                "TablesUpdated": 2,
                "TablesDeleted": 0,
            }
        ]
    }

    detail = await client.get_crawler("events-crawler")

    assert detail.summary.name == "events-crawler"
    assert detail.targets == (
        "s3://lake/events/",
        "analytics/events",
        "source/one",
        "source/two",
        "s3://iceberg/events/",
    )
    assert detail.classifiers == ("json",)
    assert detail.recrawl_behavior == "CRAWL_NEW_FOLDERS_ONLY"
    assert detail.schema_update_behavior == "UPDATE_IN_DATABASE"
    assert detail.schema_delete_behavior == "LOG"
    assert detail.security_configuration == "crawler-security"
    assert detail.lake_formation_account_id == "999999999999"
    assert detail.use_lake_formation_credentials is True
    assert detail.tags == (("env", "dev"), ("team", "platform"))
    assert detail.last_crawl_status == "SUCCEEDED"
    assert detail.last_crawl_started_at == NOW
    assert detail.last_crawl_duration_seconds == 31.5
    assert detail.metrics == GlueCrawlerMetrics(
        "events-crawler",
        False,
        None,
        30.0,
        1,
        2,
        0,
    )
    assert detail.supplemental_warnings == ()
    glue.get_crawler.assert_awaited_once_with(Name="events-crawler")
    sts.get_caller_identity.assert_awaited_once_with()
    glue.get_tags.assert_awaited_once_with(
        ResourceArn=(f"arn:{partition}:glue:{region}:123456789012:crawler/events-crawler")
    )


async def test_get_crawler_caches_sts_identity_across_detail_requests() -> None:
    client, glue, sts, _ = _client()
    glue.get_crawler.side_effect = [
        {"Crawler": _crawler("first")},
        {"Crawler": _crawler("second")},
    ]
    sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:sts::123456789012:assumed-role/dev/session",
    }
    glue.get_tags.return_value = {"Tags": {}}
    glue.get_crawler_metrics.return_value = {"CrawlerMetricsList": []}

    await client.get_crawler("first")
    await client.get_crawler("second")

    sts.get_caller_identity.assert_awaited_once_with()
    assert glue.get_tags.await_args_list == [
        call(ResourceArn="arn:aws:glue:us-east-1:123456789012:crawler/first"),
        call(ResourceArn="arn:aws:glue:us-east-1:123456789012:crawler/second"),
    ]


async def test_get_crawler_keeps_core_detail_when_supplements_are_denied() -> None:
    client, glue, sts, _ = _client()
    glue.get_crawler.return_value = {"Crawler": _crawler()}
    sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/dev",
    }
    glue.get_tags.side_effect = _client_error(
        "AccessDeniedException",
        "tags denied token=tags-secret",
        operation="GetTags",
    )
    glue.get_crawler_metrics.side_effect = _client_error(
        "AccessDeniedException",
        "metrics denied password=metrics-secret",
        operation="GetCrawlerMetrics",
    )

    detail = await client.get_crawler("events-crawler")

    assert detail.summary.name == "events-crawler"
    assert detail.tags == ()
    assert detail.metrics is None
    assert len(detail.supplemental_warnings) == 2
    assert "tags unavailable" in detail.supplemental_warnings[0].lower()
    assert "crawler metrics unavailable" in detail.supplemental_warnings[1].lower()
    assert "tags-secret" not in repr(detail.supplemental_warnings)
    assert "metrics-secret" not in repr(detail.supplemental_warnings)
    assert "[REDACTED]" in repr(detail.supplemental_warnings)


@pytest.mark.parametrize(
    ("method_name", "error"),
    [
        ("get_tags", botocore.exceptions.NoCredentialsError()),
        (
            "get_crawler_metrics",
            botocore.exceptions.EndpointConnectionError(endpoint_url="https://glue.example.test"),
        ),
    ],
)
async def test_get_crawler_propagates_non_permission_supplement_failures(
    method_name: str,
    error: BaseException,
) -> None:
    client, glue, sts, _ = _client()
    glue.get_crawler.return_value = {"Crawler": _crawler()}
    sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/dev",
    }
    glue.get_tags.return_value = {"Tags": {}}
    glue.get_crawler_metrics.return_value = {"CrawlerMetricsList": []}
    getattr(glue, method_name).side_effect = error

    expected = AuthRequiredError if method_name == "get_tags" else ProviderUnreachableError
    with pytest.raises(expected):
        await client.get_crawler("events-crawler")


async def test_get_crawler_resolves_identity_once_for_concurrent_first_requests() -> None:
    client, glue, sts, _ = _client()
    identity_started = asyncio.Event()
    release_identity = asyncio.Event()

    async def _identity() -> dict[str, str]:
        identity_started.set()
        await release_identity.wait()
        return {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/dev",
        }

    glue.get_crawler.return_value = {"Crawler": _crawler()}
    glue.get_tags.return_value = {"Tags": {}}
    glue.get_crawler_metrics.return_value = {"CrawlerMetricsList": []}
    sts.get_caller_identity.side_effect = _identity

    first = asyncio.create_task(client.get_crawler("first"))
    await identity_started.wait()
    second = asyncio.create_task(client.get_crawler("second"))
    await asyncio.sleep(0)
    release_identity.set()
    await asyncio.gather(first, second)

    sts.get_caller_identity.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (botocore.exceptions.NoCredentialsError(), AuthRequiredError),
        (
            botocore.exceptions.PartialCredentialsError(
                provider="env",
                cred_var="AWS_SECRET_ACCESS_KEY",
            ),
            AuthRequiredError,
        ),
        (botocore.exceptions.UnauthorizedSSOTokenError(), AuthRequiredError),
        (
            botocore.exceptions.SSOTokenLoadError(error_msg="bad token cache"),
            AuthRequiredError,
        ),
        (
            botocore.exceptions.EndpointConnectionError(endpoint_url="https://glue.example.test"),
            ProviderUnreachableError,
        ),
        (_client_error("AccessDeniedException", "denied"), PermissionDeniedError),
        (_client_error("ThrottlingException", "slow down"), ThrottledError),
        (_client_error("EntityNotFoundException", "missing"), NotFoundError),
        (_client_error("InvalidInputException", "bad input"), ValidationError),
        (_client_error("InternalServiceException", "service failed"), ProviderError),
        (
            botocore.exceptions.ParamValidationError(report="invalid parameter"),
            ValidationError,
        ),
        (KeyError("Name"), ValidationError),
    ],
)
def test_map_glue_error_uses_canonical_provider_errors(
    error: BaseException,
    expected_type: type[ProviderError],
) -> None:
    mapped = map_glue_error(error)

    assert isinstance(mapped, expected_type)


def test_map_glue_error_redacts_visible_messages() -> None:
    mapped = map_glue_error(
        _client_error(
            "AccessDeniedException",
            "denied token=super-secret",
        )
    )

    assert isinstance(mapped, PermissionDeniedError)
    assert str(mapped) == "denied token=[REDACTED]"


@pytest.mark.parametrize(
    "error",
    [
        _client_error(
            "AccessDeniedException",
            "Insufficient Lake Formation permission on analytics",
        ),
        _client_error(
            "AccessDeniedException",
            "access denied",
            service="lakeformation",
        ),
    ],
)
def test_lake_formation_access_denied_maps_to_specialized_error(
    error: botocore.exceptions.ClientError,
) -> None:
    mapped = map_glue_error(error)

    assert isinstance(mapped, LakeFormationPermissionError)
    assert isinstance(mapped, PermissionDeniedError)


async def test_list_jobs_page_maps_access_denied_from_aws_call() -> None:
    client, glue, _, _ = _client()
    glue.get_jobs.side_effect = _client_error("AccessDeniedException", "denied")

    with pytest.raises(PermissionDeniedError, match="denied"):
        await client.list_jobs_page()


async def test_missing_required_wire_field_maps_to_validation_error() -> None:
    client, glue, _, _ = _client()
    glue.get_jobs.return_value = {
        "Jobs": [
            {
                "Name": "missing-role",
                "Command": {"Name": "glueetl"},
            }
        ]
    }

    with pytest.raises(ValidationError, match="malformed Glue response"):
        await client.list_jobs_page()


async def test_unrelated_programming_error_is_not_rewritten() -> None:
    client, glue, _, _ = _client()
    glue.get_jobs.side_effect = RuntimeError("test sentinel")

    with pytest.raises(RuntimeError, match="test sentinel"):
        await client.list_jobs_page()
