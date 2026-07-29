from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
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
    def __init__(
        self,
        *,
        connection_name: str = "dev",
        region: str = "us-east-1",
    ) -> None:
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
        self.database_tokens: list[str | None] = []
        self.table_requests: list[tuple[str, str | None]] = []
        self.partition_requests: list[tuple[TableRef, str | None]] = []
        self.job_tokens: list[str | None] = []
        self.run_requests: list[tuple[str, str | None, tuple[str, ...]]] = []
        self.crawler_requests: list[tuple[str | None, str | None]] = []
        self.table_detail_requests: list[TableRef] = []
        self.crawler_detail_requests: list[str] = []
        self.crawlers_error: Exception | None = None
        self._database_block: tuple[asyncio.Event, asyncio.Event] | None = None
        self._table_blocks: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}
        self._detail_blocks: dict[TableRef, tuple[asyncio.Event, asyncio.Event]] = {}
        self._run_blocks: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}
        self._crawler_detail_blocks: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}

    def add_database(self, name: str) -> DatabaseSummary:
        summary = DatabaseSummary(
            DatabaseRef("AwsDataCatalog", name, self.connection_name, self.region),
            None,
            None,
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
            f"{table_name} table",
            "data-team",
            "EXTERNAL_TABLE",
            None,
            None,
        )
        columns = (
            Column("id", "string", None, False),
            Column("day", "date", None, True),
        )
        detail = TableDetail(
            summary,
            columns,
            (columns[1],),
            StorageDescriptor(
                f"s3://warehouse/{database_name}/{table_name}/",
                "parquet-input",
                "parquet-output",
                "parquet-serde",
                False,
                0,
            ),
            "parquet",
            TableFormat.HIVE,
            (("classification", "parquet"),),
        )
        self.tables[database_name].append(summary)
        self.table_details[summary.ref] = detail
        self.partitions[summary.ref] = []
        self.statistics[summary.ref] = (
            ColumnStatistics("id", "string", None, (("NumberOfNulls", "0"),)),
        )
        return summary

    def add_partition(self, ref: TableRef, value: str) -> None:
        self.partitions.setdefault(ref, []).append(
            PartitionSummary((value,), None, None, f"s3://warehouse/{value}/")
        )

    def add_job(self, name: str) -> GlueJobSummary:
        job = GlueJobSummary(
            name,
            f"{name} job",
            "GlueRole",
            "5.0",
            "glueetl",
            f"s3://scripts/{name}.py",
            "G.1X",
            2,
            60,
            0,
            (),
        )
        self.jobs.append(job)
        self.runs.setdefault(name, [])
        return job

    def add_run(self, job_name: str, run_id: str, state: str = "SUCCEEDED") -> GlueJobRunSummary:
        if not any(job.name == job_name for job in self.jobs):
            self.add_job(job_name)
        run = GlueJobRunSummary(
            job_name,
            run_id,
            state,
            0,
            None,
            None,
            None,
            10,
            "STANDARD",
            2,
            (),
            (),
            None,
            None,
            f"/aws-glue/jobs/{job_name}",
        )
        self.runs[job_name].append(run)
        return run

    def add_crawler(self, name: str, state: str = "READY") -> GlueCrawlerSummary:
        summary = GlueCrawlerSummary(name, state, "CrawlerRole", "analytics", None)
        metrics = GlueCrawlerMetrics(name, False, None, 30.0, 1, 2, 0)
        detail = GlueCrawlerDetail(
            summary,
            (f"s3://raw/{name}/",),
            (),
            "CRAWL_EVERYTHING",
            "UPDATE_IN_DATABASE",
            "LOG",
            None,
            None,
            False,
            (),
            "SUCCEEDED",
            None,
            30.0,
            None,
            metrics,
            (),
        )
        self.crawlers.append(summary)
        self.crawler_details[name] = detail
        return summary

    def block_tables(self, database_name: str) -> asyncio.Event:
        started = asyncio.Event()
        release = asyncio.Event()
        self._table_blocks[database_name] = (started, release)
        return started

    def block_databases(self) -> asyncio.Event:
        started = asyncio.Event()
        release = asyncio.Event()
        self._database_block = (started, release)
        return started

    def release_databases(self) -> None:
        if self._database_block is not None:
            self._database_block[1].set()

    def release_tables(self, database_name: str) -> None:
        self._table_blocks[database_name][1].set()

    def block_table_detail(self, ref: TableRef) -> asyncio.Event:
        started = asyncio.Event()
        release = asyncio.Event()
        self._detail_blocks[ref] = (started, release)
        return started

    def release_table_detail(self, ref: TableRef) -> None:
        self._detail_blocks[ref][1].set()

    def block_runs(self, job_name: str) -> asyncio.Event:
        started = asyncio.Event()
        release = asyncio.Event()
        self._run_blocks[job_name] = (started, release)
        return started

    def release_runs(self, job_name: str) -> None:
        self._run_blocks[job_name][1].set()

    def block_crawler_detail(self, name: str) -> asyncio.Event:
        started = asyncio.Event()
        release = asyncio.Event()
        self._crawler_detail_blocks[name] = (started, release)
        return started

    def release_crawler_detail(self, name: str) -> None:
        self._crawler_detail_blocks[name][1].set()

    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        self.database_tokens.append(start_token)
        await _wait_for_block(self._database_block)
        return _page(self.databases, start_token, self.database_page_size)

    async def list_tables_page(
        self,
        database: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[TableSummary], str | None]:
        self.table_requests.append((database, start_token))
        await _wait_for_block(self._table_blocks.get(database))
        return _page(self.tables.get(database, []), start_token, self.table_page_size)

    async def get_table(self, ref: TableRef) -> TableDetail:
        self.table_detail_requests.append(ref)
        await _wait_for_block(self._detail_blocks.get(ref))
        return self.table_details[ref]

    async def list_partitions_page(
        self,
        ref: TableRef,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PartitionSummary], str | None]:
        self.partition_requests.append((ref, start_token))
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
        self.job_tokens.append(start_token)
        return _page(self.jobs, start_token, self.job_page_size)

    async def list_job_runs_page(
        self,
        job_name: str,
        *,
        start_token: str | None = None,
        states: Sequence[str] = (),
    ) -> tuple[list[GlueJobRunSummary], str | None]:
        normalized_states = tuple(states)
        self.run_requests.append((job_name, start_token, normalized_states))
        await _wait_for_block(self._run_blocks.get(job_name))
        rows = self.runs.get(job_name, [])
        if normalized_states:
            allowed = frozenset(normalized_states)
            rows = [row for row in rows if row.state in allowed]
        return _page(rows, start_token, self.run_page_size)

    async def list_crawlers_page(
        self,
        *,
        start_token: str | None = None,
        state: str | None = None,
    ) -> tuple[list[GlueCrawlerSummary], str | None]:
        self.crawler_requests.append((start_token, state))
        if self.crawlers_error is not None:
            raise self.crawlers_error
        rows = self.crawlers
        if state is not None:
            rows = [row for row in rows if row.state == state]
        return _page(rows, start_token, self.crawler_page_size)

    async def get_crawler(self, name: str) -> GlueCrawlerDetail:
        self.crawler_detail_requests.append(name)
        await _wait_for_block(self._crawler_detail_blocks.get(name))
        return replace(self.crawler_details[name])


class ProviderCallBlock:
    def __init__(self, method: str) -> None:
        self.method = method
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancellation_seen = asyncio.Event()

    async def wait(self) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancellation_seen.set()
            await self.release.wait()


class CancellationResistantGlue(InMemoryGlue):
    """Test provider that can hold one call through task cancellation."""

    def __init__(
        self,
        *,
        connection_name: str = "dev",
        region: str = "us-east-1",
    ) -> None:
        super().__init__(connection_name=connection_name, region=region)
        self.provider_calls: list[str] = []
        self._provider_block: ProviderCallBlock | None = None

    def block_provider(self, method: str) -> ProviderCallBlock:
        block = ProviderCallBlock(method)
        self._provider_block = block
        return block

    async def _before_provider_call(self, method: str) -> None:
        self.provider_calls.append(method)
        block = self._provider_block
        if block is not None and block.method == method:
            await block.wait()

    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[DatabaseSummary], str | None]:
        await self._before_provider_call("list_databases_page")
        return await super().list_databases_page(start_token=start_token)

    async def list_tables_page(
        self,
        database: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[TableSummary], str | None]:
        await self._before_provider_call("list_tables_page")
        return await super().list_tables_page(database, start_token=start_token)

    async def get_table(self, ref: TableRef) -> TableDetail:
        await self._before_provider_call("get_table")
        return await super().get_table(ref)

    async def list_partitions_page(
        self,
        ref: TableRef,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PartitionSummary], str | None]:
        await self._before_provider_call("list_partitions_page")
        return await super().list_partitions_page(ref, start_token=start_token)

    async def get_column_statistics(
        self,
        ref: TableRef,
        columns: Sequence[str],
    ) -> tuple[ColumnStatistics, ...]:
        await self._before_provider_call("get_column_statistics")
        return await super().get_column_statistics(ref, columns)

    async def list_jobs_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[GlueJobSummary], str | None]:
        await self._before_provider_call("list_jobs_page")
        return await super().list_jobs_page(start_token=start_token)

    async def list_job_runs_page(
        self,
        job_name: str,
        *,
        start_token: str | None = None,
        states: Sequence[str] = (),
    ) -> tuple[list[GlueJobRunSummary], str | None]:
        await self._before_provider_call("list_job_runs_page")
        return await super().list_job_runs_page(
            job_name,
            start_token=start_token,
            states=states,
        )

    async def list_crawlers_page(
        self,
        *,
        start_token: str | None = None,
        state: str | None = None,
    ) -> tuple[list[GlueCrawlerSummary], str | None]:
        await self._before_provider_call("list_crawlers_page")
        return await super().list_crawlers_page(start_token=start_token, state=state)

    async def get_crawler(self, name: str) -> GlueCrawlerDetail:
        await self._before_provider_call("get_crawler")
        return await super().get_crawler(name)


async def _wait_for_block(block: tuple[asyncio.Event, asyncio.Event] | None) -> None:
    if block is None:
        return
    started, release = block
    started.set()
    await release.wait()


def _page(
    rows: Sequence[T],
    start_token: str | None,
    page_size: int,
) -> tuple[list[T], str | None]:
    offset = int(start_token or "0")
    page = list(rows[offset : offset + page_size])
    next_offset = offset + page_size
    return page, str(next_offset) if next_offset < len(rows) else None


def seeded_glue() -> InMemoryGlue:
    return _seed_glue(InMemoryGlue())


def seeded_cancellation_resistant_glue() -> CancellationResistantGlue:
    return _seed_glue(CancellationResistantGlue())


def _seed_glue(fake: T) -> T:
    first = fake.add_table("analytics", "events")
    fake.add_table("analytics", "sessions")
    fake.add_partition(first.ref, "2026-07-24")
    fake.add_partition(first.ref, "2026-07-25")
    fake.add_run("nightly", "jr-1", "RUNNING")
    fake.add_run("nightly", "jr-2", "SUCCEEDED")
    fake.add_run("hourly", "jr-3", "FAILED")
    fake.add_crawler("ready-crawler", "READY")
    fake.add_crawler("running-crawler", "RUNNING")
    return fake
