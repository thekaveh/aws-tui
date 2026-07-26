"""Deterministic, profile-local AWS Glue client for demo mode."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

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
from aws_tui.domain.glue import (
    GlueCrawlerDetail,
    GlueCrawlerMetrics,
    GlueCrawlerSummary,
    GlueJobRunSummary,
    GlueJobSummary,
)

T = TypeVar("T")


class InMemoryGlue:
    """In-memory implementation of the read-only Glue client surface."""

    def __init__(self, *, connection_name: str, region: str) -> None:
        self.connection_name = connection_name
        self.region = region
        self.database_page_size = 100
        self.table_page_size = 100
        self.partition_page_size = 100
        self.job_page_size = 100
        self.run_page_size = 100
        self.crawler_page_size = 100
        self.databases: list[DatabaseSummary] = []
        self.tables: dict[str, list[TableSummary]] = {}
        self.table_details: dict[TableRef, TableDetail] = {}
        self.partitions: dict[TableRef, list[PartitionSummary]] = {}
        self.statistics: dict[TableRef, tuple[ColumnStatistics, ...]] = {}
        self.jobs: list[GlueJobSummary] = []
        self.runs: dict[str, list[GlueJobRunSummary]] = {}
        self.crawlers: list[GlueCrawlerSummary] = []
        self.crawler_details: dict[str, GlueCrawlerDetail] = {}
        self.database_error: Exception | None = None
        self.jobs_error: Exception | None = None
        self.crawlers_error: Exception | None = None

    def add_database(self, name: str) -> DatabaseSummary:
        summary = DatabaseSummary(
            DatabaseRef("AwsDataCatalog", name, self.connection_name, self.region),
            f"{name} demo database",
            f"s3://{self.connection_name}/{name}/",
            None,
        )
        self.databases.append(summary)
        self.tables.setdefault(name, [])
        return summary

    def add_table(self, database_name: str, table_name: str) -> TableSummary:
        if not any(row.ref.database_name == database_name for row in self.databases):
            self.add_database(database_name)
        summary = TableSummary(
            TableRef(
                "AwsDataCatalog",
                database_name,
                table_name,
                self.connection_name,
                self.region,
            ),
            f"{table_name} Parquet table",
            "data-platform",
            "EXTERNAL_TABLE",
            None,
            None,
        )
        columns = (
            Column("id", "string", "Record identifier", False),
            Column("event_date", "date", "Partition date", True),
        )
        self.tables[database_name].append(summary)
        self.table_details[summary.ref] = TableDetail(
            summary,
            columns,
            (columns[1],),
            StorageDescriptor(
                f"s3://{self.connection_name}/{database_name}/{table_name}/",
                "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                True,
                0,
            ),
            "parquet",
            TableFormat.HIVE,
            (("classification", "parquet"),),
        )
        self.partitions[summary.ref] = []
        self.statistics[summary.ref] = (
            ColumnStatistics("id", "string", None, (("NumberOfNulls", "0"),)),
        )
        return summary

    def add_partition(self, ref: TableRef, value: str) -> None:
        self.partitions.setdefault(ref, []).append(
            PartitionSummary(
                (value,),
                None,
                None,
                f"s3://{self.connection_name}/{ref.database_name}/{ref.table_name}/{value}/",
            )
        )

    def add_job(self, name: str) -> GlueJobSummary:
        job = GlueJobSummary(
            name,
            f"{name} demo job",
            "AWSGlueServiceRole-demo",
            "5.0",
            "glueetl",
            f"s3://{self.connection_name}/scripts/{name}.py",
            "G.1X",
            2,
            60,
            0,
            (("--enable-metrics", "true"),),
        )
        self.jobs.append(job)
        self.runs.setdefault(name, [])
        return job

    def add_run(
        self,
        job_name: str,
        run_id: str,
        state: str,
    ) -> GlueJobRunSummary:
        if not any(job.name == job_name for job in self.jobs):
            self.add_job(job_name)
        failed = state == "FAILED"
        run = GlueJobRunSummary(
            job_name,
            run_id,
            state,
            0,
            "demo-schedule",
            None,
            None,
            48,
            "STANDARD",
            2,
            (),
            (),
            "Demo transform failed" if failed else None,
            "Demo failure state" if failed else None,
            f"/aws-glue/jobs/{job_name}",
        )
        self.runs[job_name].append(run)
        return run

    def add_crawler(
        self,
        name: str,
        state: str,
        *,
        database_name: str,
    ) -> GlueCrawlerSummary:
        failed = state == "FAILED"
        summary = GlueCrawlerSummary(
            name,
            state,
            "AWSGlueServiceRole-demo",
            database_name,
            None,
        )
        metrics = GlueCrawlerMetrics(name, state == "RUNNING", None, 32.0, 1, 2, 0)
        self.crawlers.append(summary)
        self.crawler_details[name] = GlueCrawlerDetail(
            summary,
            (f"s3://{self.connection_name}/raw/{name}/",),
            (),
            "CRAWL_EVERYTHING",
            "UPDATE_IN_DATABASE",
            "LOG",
            None,
            None,
            False,
            (("environment", "demo"),),
            "FAILED" if failed else "SUCCEEDED",
            None,
            32.0,
            "Demo crawler failed" if failed else None,
            metrics,
            (),
        )
        return summary

    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        if self.database_error is not None:
            raise self.database_error
        return _page(self.databases, start_token, self.database_page_size)

    async def list_tables_page(
        self,
        database: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[TableSummary], str | None]:
        return _page(self.tables.get(database, []), start_token, self.table_page_size)

    async def get_table(self, ref: TableRef) -> TableDetail:
        return self.table_details[ref]

    async def list_partitions_page(
        self,
        ref: TableRef,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PartitionSummary], str | None]:
        return _page(self.partitions.get(ref, []), start_token, self.partition_page_size)

    async def get_column_statistics(
        self,
        ref: TableRef,
        columns: Sequence[str],
    ) -> tuple[ColumnStatistics, ...]:
        names = frozenset(columns)
        return tuple(row for row in self.statistics.get(ref, ()) if row.column_name in names)

    async def list_jobs_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[GlueJobSummary], str | None]:
        if self.jobs_error is not None:
            raise self.jobs_error
        return _page(self.jobs, start_token, self.job_page_size)

    async def list_job_runs_page(
        self,
        job_name: str,
        *,
        start_token: str | None = None,
        states: Sequence[str] = (),
    ) -> tuple[list[GlueJobRunSummary], str | None]:
        rows = self.runs.get(job_name, [])
        if states:
            allowed = frozenset(states)
            rows = [row for row in rows if row.state in allowed]
        return _page(rows, start_token, self.run_page_size)

    async def list_crawlers_page(
        self,
        *,
        start_token: str | None = None,
        state: str | None = None,
    ) -> tuple[list[GlueCrawlerSummary], str | None]:
        if self.crawlers_error is not None:
            raise self.crawlers_error
        rows = self.crawlers
        if state is not None:
            rows = [row for row in rows if row.state == state]
        return _page(rows, start_token, self.crawler_page_size)

    async def get_crawler(self, name: str) -> GlueCrawlerDetail:
        return self.crawler_details[name]

    async def get_crawler_metrics(self, name: str) -> GlueCrawlerMetrics | None:
        return self.crawler_details[name].metrics


def _page(
    rows: Sequence[T],
    start_token: str | None,
    page_size: int,
) -> tuple[list[T], str | None]:
    offset = int(start_token or "0")
    page = list(rows[offset : offset + page_size])
    next_offset = offset + page_size
    return page, str(next_offset) if next_offset < len(rows) else None


__all__ = ["InMemoryGlue"]
