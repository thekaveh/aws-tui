"""The completion metadata read uses the selected Athena catalog/workgroup."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.domain.athena import AthenaClient
from aws_tui.domain.data_catalog import Column, TableRef
from aws_tui.domain.filesystem import PermissionDeniedError, ProviderError
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from tests.unit.domain._fake_aws_client import FakeAwsClient, FakeAwsSession
from tests.unit.domain.test_athena import _client_error

CONNECTION = Connection(
    name="fixture", kind="aws", region="eu-west-1", source="config", profile="fixture"
)


def fixture_client(response):
    boto = FakeAwsClient()
    boto.get_table_metadata = AsyncMock(return_value=response)
    session = FakeAwsSession({"athena": boto})
    return AthenaClient(aws_session=cast(AwsSession, session), connection=CONNECTION), boto, session


async def test_column_metadata_preserves_active_source_and_partition_columns():
    client, boto, session = fixture_client(
        {
            "TableMetadata": {
                "Name": "events",
                "Columns": [
                    {"Name": "payload", "Type": 'row("nested value" varchar)', "Comment": "nested"}
                ],
                "PartitionKeys": [{"Name": "day", "Type": "date"}],
            }
        }
    )
    assert hasattr(client, "get_table_columns"), "No Athena column-completion metadata port"
    metadata = await client.get_table_columns("federated", "sales", "events", workgroup="analytics")
    assert metadata.ref == TableRef("federated", "sales", "events", "fixture", "eu-west-1")
    assert metadata.columns == (
        Column("payload", 'row("nested value" varchar)', "nested", False),
        Column("day", "date", None, True),
    )
    boto.get_table_metadata.assert_awaited_once_with(
        CatalogName="federated", DatabaseName="sales", TableName="events", WorkGroup="analytics"
    )
    assert session.requests == [(CONNECTION, "athena")]
    boto.start_query_execution.assert_not_awaited()


async def test_optional_workgroup_is_omitted_not_null():
    client, boto, _ = fixture_client({"TableMetadata": {"Name": "events"}})
    assert hasattr(client, "get_table_columns"), "No Athena column-completion metadata port"
    metadata = await client.get_table_columns("catalog", "sales", "events")
    assert metadata.columns == ()
    boto.get_table_metadata.assert_awaited_once_with(
        CatalogName="catalog", DatabaseName="sales", TableName="events"
    )


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {"Name": "other"},
        {"Name": "events", "Columns": "invalid"},
        {"Name": "events", "Columns": [{"Name": "x"}]},
        {"Name": "events", "Columns": [{"Name": "", "Type": "int"}]},
        {"Name": "events", "Columns": [{"Name": "x", "Type": "int"}] * 2001},
    ],
)
async def test_malformed_wrong_table_or_oversized_metadata_fails_closed(metadata):
    client, _, _ = fixture_client({"TableMetadata": metadata})
    assert hasattr(client, "get_table_columns"), "No Athena column-completion metadata port"
    with pytest.raises(ProviderError):
        await client.get_table_columns("catalog", "sales", "events", workgroup="analytics")


async def test_metadata_denial_uses_existing_redacted_provider_errors():
    client, boto, _ = fixture_client({})
    assert hasattr(client, "get_table_columns"), "No Athena column-completion metadata port"
    boto.get_table_metadata.side_effect = _client_error(
        "AccessDeniedException", "cannot read private-table", operation="GetTableMetadata"
    )
    with pytest.raises(PermissionDeniedError) as error:
        await client.get_table_columns("catalog", "sales", "private-table")
    assert "private-table" not in str(error.value)


async def test_demo_metadata_is_profile_local_and_records_no_execution():
    client = InMemoryAthena(connection_name="fixture", region="eu-west-1")
    assert hasattr(client, "get_table_columns"), "Demo lacks column completion metadata"
    client.add_workgroup("analytics", output_location="s3://fixture/results/")
    client.add_catalog("analytics", "catalog")
    client.add_database("analytics", "catalog", "sales")
    client.add_table(
        "analytics",
        "catalog",
        "sales",
        "events",
        columns=(Column("event id", "bigint", None, False),),
    )
    result = await client.get_table_columns("catalog", "sales", "events", workgroup="analytics")
    assert result.columns == (Column("event id", "bigint", None, False),)
    assert result.ref.connection_name == "fixture"
    assert [c.method for c in client.calls] == ["get_table_columns"]
    client.access_error = PermissionDeniedError("denied")
    with pytest.raises(PermissionDeniedError):
        await client.get_table_columns("catalog", "sales", "events", workgroup="analytics")
