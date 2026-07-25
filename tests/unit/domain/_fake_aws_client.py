"""Shared aioboto-shaped test doubles for domain client tests."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import AsyncMock

from aws_tui.infra.connection_resolver import Connection


class FakeAwsClient:
    """Async AWS client with explicit mocks for the Glue Task 2 surface."""

    def __init__(self) -> None:
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
