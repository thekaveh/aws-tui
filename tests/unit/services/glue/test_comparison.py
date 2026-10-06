"""Explicit, read-only routing for independently selected Glue environments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.data_catalog import (
    DatabaseRef,
    DatabaseSummary,
    StorageDescriptor,
    TableDetail,
    TableFormat,
    TableRef,
    TableSummary,
)
from aws_tui.domain.filesystem import PermissionDeniedError, ValidationError
from aws_tui.domain.table_comparison import ChangeKind, compare_tables
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.glue.comparison import GlueComparisonRouter
from aws_tui.services.glue.service import GlueClientFactory, GlueService
from aws_tui.vm.glue.comparison_ports import ComparisonCatalogClient
from aws_tui.vm.service_source_vm import ServiceSourceContext


def connection(name: str = "prod") -> Connection:
    return Connection(name, "aws", "us-east-1", "config", profile=f"{name}-profile")


class CatalogStub:
    def __init__(self, source: Connection) -> None:
        self.ref = TableRef("AwsDataCatalog", "analytics", "events", source.name, source.region)
        self.databases = [DatabaseSummary(self.ref.database, None, None, None)]
        self.tables = [TableSummary(self.ref, None, None, None, None, None)]
        self.detail = TableDetail(
            self.tables[0],
            (),
            (),
            StorageDescriptor(None, None, None, None, False, 0),
            None,
            TableFormat.OTHER,
            (),
        )
        self.calls: list[tuple[object, ...]] = []
        self.error: Exception | None = None

    async def list_databases_page(self, *, start_token: str | None = None):
        self.calls.append(("databases", start_token))
        return self.databases, "database-next"

    async def list_tables_page(self, database: str, *, start_token: str | None = None):
        self.calls.append(("tables", database, start_token))
        return self.tables, "table-next"

    async def get_table(self, ref: TableRef) -> TableDetail:
        self.calls.append(("detail", ref))
        if self.error is not None:
            raise self.error
        return self.detail


class RecordingFactory:
    def __init__(self) -> None:
        self.calls: list[Connection] = []
        self.clients: list[CatalogStub] = []

    def __call__(self, source: Connection) -> ComparisonCatalogClient:
        self.calls.append(source)
        client = CatalogStub(source)
        self.clients.append(client)
        return client


def test_explicit_cross_connection_and_region_preserves_private_source_settings() -> None:
    prod = replace(
        connection(),
        endpoint_url="https://private.example.invalid",
        verify_tls=False,
        access_key_id="PRIVATE-KEY",
        secret_access_key="PRIVATE-SECRET",
        session_token="PRIVATE-TOKEN",
    )
    dev = connection("dev")
    factory = RecordingFactory()
    router = GlueComparisonRouter(connections=lambda: [prod, dev], client_factory=factory)
    left = router.resolve("prod", " us-west-2 ")
    right = router.resolve("dev", "eu-west-1")
    assert factory.calls == [replace(prod, region="us-west-2"), replace(dev, region="eu-west-1")]
    assert left.connection_name == "prod"
    assert left.region == "us-west-2"
    assert right.connection_name == "dev"
    assert right.region == "eu-west-1"
    assert left.connection == factory.calls[0]
    assert left.client is not right.client
    assert repr(left) == "ResolvedSource()"
    assert "PRIVATE" not in repr(left)
    assert router.is_current(left)
    assert len(factory.calls) == 2


def test_sources_are_live_aws_only_without_creating_clients() -> None:
    sources = [connection("prod"), replace(connection("minio"), kind="s3-compatible")]
    factory = RecordingFactory()
    router = GlueComparisonRouter(connections=lambda: sources, client_factory=factory)
    assert router.sources() == (ServiceSourceContext.from_connection(sources[0]),)
    sources[:] = [connection("dev")]
    assert router.sources() == (ServiceSourceContext.from_connection(sources[0]),)
    assert factory.calls == []


@pytest.mark.parametrize(
    "region",
    ["", " \t ", "us west-2", "us\nwest-2", "us\x00west-2", "us\x7fwest-2", "us\u200bwest-2"],
)
def test_invalid_region_never_creates_client(region: str) -> None:
    factory = RecordingFactory()
    router = GlueComparisonRouter(connections=lambda: [connection()], client_factory=factory)
    with pytest.raises(ValidationError, match="region"):
        router.resolve("prod", region)
    assert factory.calls == []


@pytest.mark.parametrize("name", ["missing", "minio", " prod"])
def test_absent_non_aws_or_inexact_connection_is_rejected(name: str) -> None:
    factory = RecordingFactory()
    router = GlueComparisonRouter(
        connections=lambda: [connection(), replace(connection("minio"), kind="s3-compatible")],
        client_factory=factory,
    )
    with pytest.raises(ValidationError, match="source"):
        router.resolve(name, "us-east-1")
    assert factory.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint_url": "https://changed.example.invalid"},
        {"profile": "changed"},
        {"access_key_id": "changed"},
        {"secret_access_key": "changed"},
        {"session_token": "changed"},
        {"verify_tls": False},
        {"force_path_style": True},
        {"kind": "s3-compatible"},
    ],
)
def test_full_effective_route_replacement_invalidates_without_building_client(changes) -> None:
    original = connection()
    sources = [original]
    factory = RecordingFactory()
    router = GlueComparisonRouter(connections=lambda: sources, client_factory=factory)
    route = router.resolve("prod", "us-west-2")
    sources[:] = [replace(original, **changes)]
    assert not router.is_current(route)
    assert len(factory.calls) == 1


def test_route_removal_and_discovery_failure_are_stale_without_secret_errors() -> None:
    sources = [connection()]
    failure = False

    def discover() -> Sequence[Connection]:
        if failure:
            raise RuntimeError("PRIVATE-CONFIG-SECRET")
        return sources

    router = GlueComparisonRouter(connections=discover, client_factory=RecordingFactory())
    route = router.resolve("prod", "us-west-2")
    sources.clear()
    assert not router.is_current(route)
    failure = True
    assert not router.is_current(route)
    with pytest.raises(ValidationError) as raised:
        router.sources()
    assert "PRIVATE" not in str(raised.value)


def test_duplicate_connection_names_do_not_silently_select_a_route() -> None:
    router = GlueComparisonRouter(
        connections=lambda: [connection(), replace(connection(), profile="other")],
        client_factory=RecordingFactory(),
    )
    with pytest.raises(ValidationError, match="source"):
        router.resolve("prod", "us-east-1")


async def test_reads_forward_tokens_and_return_exact_owned_refs() -> None:
    factory = RecordingFactory()
    route = GlueComparisonRouter(
        connections=lambda: [connection()], client_factory=factory
    ).resolve("prod", "us-west-2")
    stub = factory.clients[0]
    assert await route.client.list_databases_page(start_token="d") == (
        stub.databases,
        "database-next",
    )
    assert await route.client.list_tables_page("analytics", start_token="t") == (
        stub.tables,
        "table-next",
    )
    assert await route.client.get_table(stub.ref) == stub.detail
    assert stub.calls == [("databases", "d"), ("tables", "analytics", "t"), ("detail", stub.ref)]


@pytest.mark.parametrize(
    "changes", [{"connection_name": "other"}, {"region": "eu-west-1"}, {"catalog_name": "foreign"}]
)
async def test_foreign_request_ref_is_rejected_before_provider_call(changes) -> None:
    factory = RecordingFactory()
    route = GlueComparisonRouter(
        connections=lambda: [connection()], client_factory=factory
    ).resolve("prod", "us-east-1")
    stub = factory.clients[0]
    with pytest.raises(ValidationError, match="identity"):
        await route.client.get_table(replace(stub.ref, **changes))
    assert stub.calls == []


@pytest.mark.parametrize(
    "field", ["catalog_name", "connection_name", "region", "database_name", "table_name"]
)
async def test_mismatched_returned_detail_is_rejected(field: str) -> None:
    factory = RecordingFactory()
    route = GlueComparisonRouter(
        connections=lambda: [connection()], client_factory=factory
    ).resolve("prod", "us-east-1")
    stub = factory.clients[0]
    stub.detail = replace(
        stub.detail, summary=replace(stub.detail.summary, ref=replace(stub.ref, **{field: "other"}))
    )
    with pytest.raises(ValidationError, match="identity"):
        await route.client.get_table(stub.ref)


@pytest.mark.parametrize("field", ["catalog_name", "connection_name", "region"])
async def test_foreign_database_discovery_is_rejected(field: str) -> None:
    factory = RecordingFactory()
    route = GlueComparisonRouter(
        connections=lambda: [connection()], client_factory=factory
    ).resolve("prod", "us-east-1")
    stub = factory.clients[0]
    stub.databases = [
        replace(stub.databases[0], ref=replace(stub.ref.database, **{field: "other"}))
    ]
    with pytest.raises(ValidationError, match="identity"):
        await route.client.list_databases_page()


@pytest.mark.parametrize("field", ["catalog_name", "connection_name", "region", "database_name"])
async def test_foreign_table_discovery_is_rejected(field: str) -> None:
    factory = RecordingFactory()
    route = GlueComparisonRouter(
        connections=lambda: [connection()], client_factory=factory
    ).resolve("prod", "us-east-1")
    stub = factory.clients[0]
    stub.tables = [replace(stub.tables[0], ref=replace(stub.ref, **{field: "other"}))]
    with pytest.raises(ValidationError, match="identity"):
        await route.client.list_tables_page("analytics")


async def test_provider_permission_error_category_is_preserved() -> None:
    factory = RecordingFactory()
    route = GlueComparisonRouter(
        connections=lambda: [connection()], client_factory=factory
    ).resolve("prod", "us-east-1")
    stub = factory.clients[0]
    error = PermissionDeniedError("permission denied")
    stub.error = error
    with pytest.raises(PermissionDeniedError) as raised:
        await route.client.get_table(stub.ref)
    assert raised.value is error


def test_service_builder_uses_injected_glue_factory_without_athena_creation() -> None:
    factory = RecordingFactory()
    athena_calls: list[Connection] = []

    def athena_factory(source: Connection):
        athena_calls.append(source)
        raise AssertionError("comparison must not create Athena clients")

    service = GlueService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=cast(GlueClientFactory, factory),
        athena_client_factory=athena_factory,
    )
    router = service.build_comparison_router(connections=lambda: [connection()])
    assert factory.calls == []
    route = router.resolve("prod", "us-west-2")
    assert route.connection.region == "us-west-2"
    assert factory.calls == [replace(connection(), region="us-west-2")]
    assert athena_calls == []


class ReadOnlySDK:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.unexpected: list[str] = []
        self.compressed: bool | None = None

    def __getattr__(self, name: str):
        self.unexpected.append(name)
        raise AssertionError(f"unexpected SDK operation: {name}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def get_databases(self, **kwargs: object):
        self.calls.append(("get_databases", kwargs))
        return {"DatabaseList": [{"Name": "analytics"}], "NextToken": "d2"}

    async def get_tables(self, **kwargs: object):
        self.calls.append(("get_tables", kwargs))
        return {"TableList": [{"Name": "events"}], "NextToken": "t2"}

    async def get_table(self, **kwargs: object):
        self.calls.append(("get_table", kwargs))
        storage = {} if self.compressed is None else {"Compressed": self.compressed}
        return {"Table": {"Name": "events", "StorageDescriptor": storage}}


class ReadOnlySession:
    def __init__(self, sdk: ReadOnlySDK) -> None:
        self.sdk = sdk
        self.calls: list[tuple[Connection, str]] = []

    async def client(self, source: Connection, service: str) -> ReadOnlySDK:
        self.calls.append((source, service))
        assert service == "glue"
        return self.sdk


async def test_default_builder_only_uses_read_allowlist_and_preserves_default_false() -> None:
    sdk = ReadOnlySDK()
    session = ReadOnlySession(sdk)
    service = GlueService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=cast(AwsSession, session),
    )
    router = service.build_comparison_router(connections=lambda: [connection()])
    route = router.resolve("prod", "us-west-2")
    databases, database_token = await route.client.list_databases_page(start_token="d1")
    tables, table_token = await route.client.list_tables_page("analytics", start_token="t1")
    assert databases[0].ref == DatabaseRef("AwsDataCatalog", "analytics", "prod", "us-west-2")
    assert database_token == "d2"
    assert table_token == "t2"
    absent = await route.client.get_table(tables[0].ref)
    sdk.compressed = False
    explicit = await route.client.get_table(tables[0].ref)
    row = next(row for row in compare_tables(absent, explicit).rows if row.key == "compressed")
    assert row.left is False
    assert row.right is False
    assert row.changes == (ChangeKind.UNCHANGED,)
    assert sdk.calls == [
        ("get_databases", {"MaxResults": 100, "NextToken": "d1"}),
        ("get_tables", {"DatabaseName": "analytics", "MaxResults": 100, "NextToken": "t1"}),
        ("get_table", {"DatabaseName": "analytics", "Name": "events"}),
        ("get_table", {"DatabaseName": "analytics", "Name": "events"}),
    ]
    assert sdk.unexpected == []
    assert session.calls == [(replace(connection(), region="us-west-2"), "glue")] * 4
