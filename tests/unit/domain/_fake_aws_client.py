"""Shared aioboto-shaped test doubles for domain client tests."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import AsyncMock

from aws_tui.infra.connection_resolver import Connection


class FakeAwsClient:
    """Async AWS client with explicit mocks for domain-client surfaces."""

    def __init__(self) -> None:
        self.batch_get_query_execution = AsyncMock()
        self.batch_get_named_query = AsyncMock()
        self.get_prepared_statement = AsyncMock()
        self.get_query_execution = AsyncMock()
        self.get_query_results = AsyncMock()
        self.get_query_runtime_statistics = AsyncMock()
        self.get_work_group = AsyncMock()
        self.get_caller_identity = AsyncMock()
        self.get_column_statistics_for_table = AsyncMock()
        self.get_crawler = AsyncMock()
        self.get_crawler_metrics = AsyncMock()
        self.get_crawlers = AsyncMock()
        self.get_databases = AsyncMock()
        self.get_job_runs = AsyncMock()
        self.get_jobs = AsyncMock()
        self.get_partitions = AsyncMock()
        self.get_table = AsyncMock()
        self.get_tables = AsyncMock()
        self.get_tags = AsyncMock()
        self.list_data_catalogs = AsyncMock()
        self.list_databases = AsyncMock()
        self.list_named_queries = AsyncMock()
        self.list_prepared_statements = AsyncMock()
        self.list_query_executions = AsyncMock()
        self.list_table_metadata = AsyncMock()
        self.list_work_groups = AsyncMock()
        self.start_query_execution = AsyncMock()
        self.stop_query_execution = AsyncMock()


class FakeAwsClientContext:
    def __init__(self, client: FakeAwsClient) -> None:
        self._client = client

    async def __aenter__(self) -> FakeAwsClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeAwsSession:
    """Routes requested service names to stable fake clients."""

    def __init__(self, clients: Mapping[str, FakeAwsClient]) -> None:
        self._clients = dict(clients)
        self.requests: list[tuple[Connection, str]] = []

    async def client(
        self,
        connection: Connection,
        service: str,
    ) -> FakeAwsClientContext:
        self.requests.append((connection, service))
        return FakeAwsClientContext(self._clients[service])


__all__ = ["FakeAwsClient", "FakeAwsClientContext", "FakeAwsSession"]
