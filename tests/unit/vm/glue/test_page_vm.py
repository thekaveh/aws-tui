from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableFormat, TableRef
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.catalog_vm import GlueCatalogVM
from aws_tui.vm.glue.crawlers_vm import GlueCrawlersVM
from aws_tui.vm.glue.jobs_vm import GlueJobsVM
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import (
    CopyTableReferenceRequest,
    OpenAthenaTableRequest,
    OpenS3LocationRequest,
)
from aws_tui.vm.service_source_vm import SelectionScope, ServiceSelectionStore
from tests.unit.vm.glue._fake_glue import (
    CancellationResistantGlue,
    InMemoryGlue,
    ProviderCallBlock,
    seeded_cancellation_resistant_glue,
    seeded_glue,
)
from tests.unit.vm.glue.test_iceberg_vm import RecordingInspector


class OwnedSelectionStore(ServiceSelectionStore):
    def __init__(self) -> None:
        super().__init__()
        self.dispose_calls = 0

    def dispose(self) -> None:
        self.dispose_calls += 1


def make_page_vm(
    fake: InMemoryGlue,
    *,
    connection_name: str = "dev",
    region: str = "us-east-1",
    selection_store: ServiceSelectionStore | None = None,
    iceberg_inspector: RecordingInspector | None = None,
) -> GluePageVM:
    hub: MessageHub[Message] = MessageHub()
    page = GluePageVM(
        client=fake,
        iceberg_inspector=iceberg_inspector,
        connection=Connection(
            name=connection_name,
            kind="aws",
            region=region,
            source="config",
            profile=connection_name,
        ),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        selection_store=selection_store,
    )
    page.construct()
    return page


def _child_state(page: GluePageVM) -> tuple[object, ...]:
    return (
        page.catalog.selected_database_name,
        page.catalog.selected_table_name,
        page.catalog.table_detail,
        page.catalog.partitions,
        page.catalog.column_statistics,
        page.catalog.databases_state,
        page.catalog.tables_state,
        page.catalog.detail_state,
        page.catalog.partitions_state,
        page.catalog.statistics_state,
        page.jobs.selected_job_name,
        page.jobs.selected_run_id,
        page.jobs.jobs,
        page.jobs.runs,
        page.jobs.jobs_state,
        page.jobs.runs_state,
        page.crawlers.selected_crawler_name,
        page.crawlers.crawler_detail,
        page.crawlers.crawlers,
        page.crawlers.state,
        page.crawlers.detail_state,
    )


async def _blocked_operation(
    page: GluePageVM,
    fake: CancellationResistantGlue,
    method: str,
) -> tuple[ProviderCallBlock, asyncio.Task[None]]:
    if method == "get_crawler":
        await page.select_view("crawlers")
    block = fake.block_provider(method)
    if method in {
        "get_table",
        "list_partitions_page",
        "get_column_statistics",
    }:
        operation = asyncio.create_task(page.select_table("sessions"))
    elif method in {"list_jobs_page", "list_job_runs_page"}:
        operation = asyncio.create_task(page.select_view("jobs"))
    elif method == "list_crawlers_page":
        operation = asyncio.create_task(page.select_view("crawlers"))
    else:
        operation = asyncio.create_task(page.select_crawler("running-crawler"))
    await block.started.wait()
    return block, operation


@pytest.mark.asyncio
async def test_setup_loads_only_catalog_and_first_selections() -> None:
    fake = seeded_glue()
    page = make_page_vm(fake)

    await page.setup()

    assert page.active_view == "catalog"
    assert page.catalog.databases
    assert page.catalog.selected_database_name == "analytics"
    assert page.catalog.selected_table_name == "events"
    assert fake.job_tokens == []
    assert fake.crawler_requests == []


@pytest.mark.asyncio
async def test_catalog_open_table_selects_exact_table_identity() -> None:
    fake = seeded_glue()
    fake.table_page_size = 1
    page = make_page_vm(fake)
    await page.setup()
    ref = TableRef("AwsDataCatalog", "analytics", "sessions", "dev", "us-east-1")

    await page.catalog.open_table(ref)

    assert page.catalog.selected_database_name == "analytics"
    assert page.catalog.selected_table_name == "sessions"


@pytest.mark.asyncio
async def test_catalog_open_table_loads_later_database_page() -> None:
    fake = seeded_glue()
    table = fake.add_table("warehouse", "orders")
    fake.database_page_size = 1
    page = make_page_vm(fake)
    await page.setup()

    await page.catalog.open_table(table.ref)

    assert page.catalog.selected_database_name == "warehouse"
    assert page.catalog.selected_table_name == "orders"


@pytest.mark.asyncio
async def test_catalog_open_table_rejects_mismatched_or_missing_identity() -> None:
    page = make_page_vm(seeded_glue())
    await page.setup()
    before = (
        page.catalog.selected_database_name,
        page.catalog.selected_table_name,
    )

    for ref in (
        TableRef("AwsDataCatalog", "analytics", "events", "other", "us-east-1"),
        TableRef("AwsDataCatalog", "analytics", "events", "dev", "us-west-2"),
        TableRef("OtherCatalog", "analytics", "events", "dev", "us-east-1"),
        TableRef("AwsDataCatalog", "missing", "events", "dev", "us-east-1"),
        TableRef("AwsDataCatalog", "analytics", "missing", "dev", "us-east-1"),
    ):
        with pytest.raises(ValueError, match="table"):
            await page.catalog.open_table(ref)
        assert (
            page.catalog.selected_database_name,
            page.catalog.selected_table_name,
        ) == before


@pytest.mark.asyncio
async def test_catalog_query_in_athena_sends_selected_table_identity() -> None:
    page = make_page_vm(seeded_glue())
    await page.setup()
    messages: list[OpenAthenaTableRequest] = []
    subscription = page._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda message: (
            messages.append(message) if isinstance(message, OpenAthenaTableRequest) else None
        )
    )
    try:
        assert page.catalog.query_in_athena()
        assert messages == [
            OpenAthenaTableRequest(
                TableRef(
                    "AwsDataCatalog",
                    "analytics",
                    "events",
                    "dev",
                    "us-east-1",
                )
            )
        ]
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_catalog_copy_sends_exact_selected_table_identity_or_no_ops() -> None:
    page = make_page_vm(seeded_glue())
    await page.setup()
    messages: list[CopyTableReferenceRequest] = []
    subscription = page.hub.messages.subscribe(
        on_next=lambda message: (
            messages.append(message) if isinstance(message, CopyTableReferenceRequest) else None
        )
    )
    selected = next(
        row.ref
        for row in page.catalog.tables
        if row.ref.table_name == page.catalog.selected_table_name
    )
    try:
        assert page.catalog.copy_table_reference() is True
        assert messages == [CopyTableReferenceRequest(selected)]

        page.catalog._selected_table_name = None  # type: ignore[attr-defined]
        assert page.catalog.copy_table_reference() is False
        assert messages == [CopyTableReferenceRequest(selected)]
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_page_copy_delegates_to_catalog_without_selection_state() -> None:
    page = make_page_vm(seeded_glue())
    await page.setup()
    calls = 0

    def copy() -> bool:
        nonlocal calls
        calls += 1
        return True

    page.catalog.copy_table_reference = copy  # type: ignore[method-assign]

    assert page.copy_table_reference() is True
    assert calls == 1


@pytest.mark.asyncio
async def test_page_handoffs_preserve_selected_table_identity() -> None:
    page = make_page_vm(seeded_glue())
    await page.setup()
    messages: list[Message] = []
    subscription = page.hub.messages.subscribe(on_next=messages.append)

    try:
        assert page.open_s3_location(preferred_pane="right")
        assert page.query_in_athena()
    finally:
        subscription.dispose()

    ref = TableRef(
        "AwsDataCatalog",
        "analytics",
        "events",
        "dev",
        "us-east-1",
    )
    assert messages == [
        OpenS3LocationRequest(
            connection_name="dev",
            region="us-east-1",
            uri="s3://warehouse/analytics/events/",
            preferred_pane="right",
        ),
        OpenAthenaTableRequest(ref),
    ]


@pytest.mark.asyncio
async def test_page_athena_handoff_capabilities_are_catalog_scoped_and_guarded() -> None:
    fake = seeded_glue()
    ref = fake.tables["analytics"][0].ref
    fake.table_details[ref] = replace(
        fake.table_details[ref],
        table_format=TableFormat.ICEBERG,
    )
    page = make_page_vm(fake, iceberg_inspector=RecordingInspector())
    await page.setup()
    messages: list[OpenAthenaTableRequest] = []
    subscription = page.hub.messages.subscribe(
        on_next=lambda message: (
            messages.append(message) if isinstance(message, OpenAthenaTableRequest) else None
        )
    )

    try:
        assert page.can_query_in_athena is (
            page.active_view == "catalog" and page.catalog.can_copy_table_reference
        )
        assert page.can_time_travel_in_athena is (
            page.active_view == "catalog" and page.catalog.iceberg.can_time_travel_in_athena
        )
        assert page.query_in_athena()

        assert await page.catalog.iceberg.select_view("snapshots")
        assert page.catalog.iceberg.select_snapshot(43)
        assert page.can_time_travel_in_athena
        assert page.time_travel_in_athena()

        await page.select_view("jobs")
        assert not page.can_query_in_athena
        assert not page.can_time_travel_in_athena
        assert not page.query_in_athena()
        assert not page.time_travel_in_athena()
    finally:
        subscription.dispose()

    assert messages == [OpenAthenaTableRequest(ref), OpenAthenaTableRequest(ref, snapshot_id=43)]
    page.dispose()
    assert not page.can_query_in_athena
    assert not page.can_time_travel_in_athena


@pytest.mark.asyncio
async def test_select_view_lazily_loads_each_view_once() -> None:
    fake = seeded_glue()
    page = make_page_vm(fake)
    await page.setup()

    await page.select_view("jobs")
    await page.select_view("catalog")
    await page.select_view("jobs")
    await page.select_view("crawlers")
    await page.select_view("crawlers")

    assert fake.job_tokens == [None]
    assert fake.crawler_requests == [(None, None)]


@pytest.mark.asyncio
async def test_restored_active_view_is_the_only_view_loaded() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    store.set(scope, "active_view", "jobs")
    page = make_page_vm(fake, selection_store=store)

    await page.setup()

    assert page.active_view == "jobs"
    assert page.jobs.jobs
    assert page.catalog.databases == ()
    assert fake.crawler_requests == []


@pytest.mark.asyncio
async def test_source_and_selection_scope_include_connection_region() -> None:
    fake = InMemoryGlue(connection_name="analytics", region="us-west-2")
    fake.add_table("warehouse", "events")
    store = ServiceSelectionStore()
    page = make_page_vm(
        fake,
        connection_name="analytics",
        region="us-west-2",
        selection_store=store,
    )

    await page.setup()
    await page.select_table("events")

    assert page.source.connection_key == ("analytics", "us-west-2")
    assert page.source.label == "analytics · us-west-2"
    scope = SelectionScope("glue", "analytics", "us-west-2")
    assert store.get(scope, "database_name") == "warehouse"
    assert store.get(scope, "table_name") == "events"
    assert store.get(SelectionScope("glue", "analytics", "us-east-1"), "table_name") is None


@pytest.mark.asyncio
async def test_restores_valid_catalog_selections_after_latest_pages_load() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    store.set(scope, "database_name", "analytics")
    store.set(scope, "table_name", "sessions")
    page = make_page_vm(fake, selection_store=store)

    await page.setup()

    assert page.catalog.selected_database_name == "analytics"
    assert page.catalog.selected_table_name == "sessions"


@pytest.mark.asyncio
async def test_invalid_restored_catalog_selection_is_discarded_and_falls_back() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    store.set(scope, "database_name", "deleted")
    store.set(scope, "table_name", "deleted")
    page = make_page_vm(fake, selection_store=store)

    await page.setup()

    assert page.catalog.selected_database_name == "analytics"
    assert page.catalog.selected_table_name == "events"
    assert store.get(scope, "database_name") == "analytics"
    assert store.get(scope, "table_name") == "events"


@pytest.mark.asyncio
async def test_jobs_restore_selection_and_sorted_filter_serialization() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    store.set(scope, "active_view", "jobs")
    store.set(scope, "job_name", "nightly")
    store.set(scope, "job_run_states", "SUCCEEDED,RUNNING")
    page = make_page_vm(fake, selection_store=store)

    await page.setup()
    await page.set_job_run_states(frozenset({"FAILED", "RUNNING"}))

    assert page.jobs.selected_job_name == "nightly"
    assert page.jobs.run_state_filter == frozenset({"FAILED", "RUNNING"})
    assert store.get(scope, "job_run_states") == "FAILED,RUNNING"


@pytest.mark.asyncio
async def test_crawler_restore_and_filter_are_validated() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    store.set(scope, "active_view", "crawlers")
    store.set(scope, "crawler_state", "RUNNING")
    store.set(scope, "crawler_name", "missing")
    page = make_page_vm(fake, selection_store=store)

    await page.setup()

    assert page.crawlers.state_filter == "RUNNING"
    assert page.crawlers.selected_crawler_name == "running-crawler"
    assert store.get(scope, "crawler_name") == "running-crawler"


@pytest.mark.asyncio
async def test_refresh_active_reloads_crawlers_with_unchanged_stored_filter() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    store.set(scope, "active_view", "crawlers")
    store.set(scope, "crawler_state", "RUNNING")
    page = make_page_vm(fake, selection_store=store)
    await page.setup()

    await page.refresh_active()

    assert fake.crawler_requests == [(None, "RUNNING"), (None, "RUNNING")]


@pytest.mark.asyncio
async def test_empty_catalog_refresh_clears_selection_data_notifications_and_store() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    page = make_page_vm(fake, selection_store=store)
    await page.setup()
    notifications: list[str] = []
    subscription = page.catalog.on_property_changed.subscribe(on_next=notifications.append)
    fake.databases.clear()

    await page.refresh_active()

    assert page.catalog.selected_database_name is None
    assert page.catalog.selected_table_name is None
    assert page.catalog.tables == ()
    assert page.catalog.table_detail is None
    assert page.catalog.partitions == ()
    assert page.catalog.column_statistics == ()
    assert page.catalog.tables_state is PaneState.EMPTY
    assert page.catalog.detail_state is PaneState.EMPTY
    assert page.catalog.partitions_state is PaneState.EMPTY
    assert page.catalog.statistics_state is PaneState.EMPTY
    assert store.get(scope, "database_name") is None
    assert store.get(scope, "table_name") is None
    assert {
        "selected_database_name",
        "selected_table_name",
        "tables",
        "table_detail",
        "partitions",
        "column_statistics",
    }.issubset(notifications)
    subscription.dispose()


@pytest.mark.asyncio
async def test_empty_jobs_refresh_clears_selection_runs_notifications_and_store() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    page = make_page_vm(fake, selection_store=store)
    await page.setup()
    await page.select_view("jobs")
    notifications: list[str] = []
    subscription = page.jobs.on_property_changed.subscribe(on_next=notifications.append)
    fake.jobs.clear()

    await page.refresh_active()

    assert page.jobs.selected_job_name is None
    assert page.jobs.selected_run_id is None
    assert page.jobs.runs == ()
    assert page.jobs.runs_state is PaneState.EMPTY
    assert store.get(scope, "job_name") is None
    assert {"selected_job_name", "selected_run_id", "runs"}.issubset(notifications)
    subscription.dispose()


@pytest.mark.asyncio
async def test_crawler_access_denied_is_scoped_to_crawlers_view() -> None:
    fake = seeded_glue()
    fake.crawlers_error = PermissionDeniedError("glue:GetCrawlers denied")
    page = make_page_vm(fake)
    await page.setup()
    await page.select_view("crawlers")

    assert page.crawlers.state is PaneState.FORBIDDEN
    assert page.catalog.state is PaneState.IDLE


@pytest.mark.asyncio
async def test_page_dispose_prevents_blocked_setup_from_mutating_page_or_store() -> None:
    fake = seeded_glue()
    databases_started = fake.block_databases()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    page = make_page_vm(fake, selection_store=store)
    setup = asyncio.create_task(page.setup())
    await databases_started.wait()

    page.dispose()
    fake.release_databases()
    await setup

    assert page._loaded_views == set()  # type: ignore[attr-defined]
    assert store.get(scope, "database_name") is None
    assert store.get(scope, "table_name") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "get_table",
        "list_partitions_page",
        "get_column_statistics",
        "list_jobs_page",
        "list_job_runs_page",
        "list_crawlers_page",
        "get_crawler",
    ],
)
async def test_shutdown_cancels_drains_and_suppresses_every_child_provider_path(
    method: str,
) -> None:
    fake = seeded_cancellation_resistant_glue()
    page = make_page_vm(fake)
    await page.setup()
    block, operation = await _blocked_operation(page, fake, method)
    state_at_shutdown = _child_state(page)
    messages: list[Message] = []
    subscription = page.hub.messages.subscribe(on_next=messages.append)

    shutdown = asyncio.create_task(page.shutdown())
    cancellation = asyncio.create_task(block.cancellation_seen.wait())
    await asyncio.wait(
        {shutdown, cancellation},
        timeout=1,
        return_when=asyncio.FIRST_COMPLETED,
    )
    cancelled_provider = block.cancellation_seen.is_set()
    returned_before_release = shutdown.done()
    messages.clear()
    block.release.set()
    results = await asyncio.gather(shutdown, operation, return_exceptions=True)
    cancellation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancellation

    assert results == [None, None]
    assert cancelled_provider
    assert not returned_before_release
    assert _child_state(page) == state_at_shutdown
    assert not any(isinstance(message, PropertyChangedMessage) for message in messages)
    assert getattr(page, "_provider_tasks", None) == set()
    assert page._shutdown_complete  # type: ignore[attr-defined]
    subscription.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["shutdown", "dispose"])
@pytest.mark.parametrize("method", ["list_jobs_page", "list_crawlers_page"])
async def test_catalog_child_termination_preserves_shared_sibling_operations(
    termination: str,
    method: str,
) -> None:
    fake = seeded_cancellation_resistant_glue()
    page = make_page_vm(fake)
    await page.setup()
    block, operation = await _blocked_operation(page, fake, method)

    if termination == "shutdown":
        await page.catalog.shutdown()
    else:
        page.catalog.dispose()

    assert page._is_alive()  # type: ignore[attr-defined]
    assert page._operations.accepting  # type: ignore[attr-defined]
    assert not block.cancellation_seen.is_set()
    assert not operation.done()

    shutdown = asyncio.create_task(page.shutdown())
    await block.cancellation_seen.wait()
    returned_before_release = shutdown.done()
    block.release.set()
    results = await asyncio.gather(shutdown, operation, return_exceptions=True)

    assert results == [None, None]
    assert not returned_before_release
    assert page._provider_tasks == set()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("child_name", "termination", "method"),
    [
        ("jobs", "shutdown", "list_crawlers_page"),
        ("crawlers", "dispose", "list_jobs_page"),
    ],
)
async def test_jobs_and_crawlers_child_termination_preserves_shared_owner(
    child_name: str,
    termination: str,
    method: str,
) -> None:
    fake = seeded_cancellation_resistant_glue()
    page = make_page_vm(fake)
    await page.setup()
    block, operation = await _blocked_operation(page, fake, method)
    child = getattr(page, child_name)

    if termination == "shutdown":
        await child.shutdown()
    else:
        child.dispose()

    assert page._is_alive()  # type: ignore[attr-defined]
    assert page._operations.accepting  # type: ignore[attr-defined]
    assert not block.cancellation_seen.is_set()
    assert not operation.done()

    shutdown = asyncio.create_task(page.shutdown())
    await block.cancellation_seen.wait()
    returned_before_release = shutdown.done()
    block.release.set()
    results = await asyncio.gather(shutdown, operation, return_exceptions=True)

    assert results == [None, None]
    assert not returned_before_release
    assert page._provider_tasks == set()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("child_name", "method"),
    [
        ("catalog", "list_databases_page"),
        ("jobs", "list_jobs_page"),
        ("crawlers", "list_crawlers_page"),
    ],
)
async def test_standalone_child_dispose_closes_and_durably_drains_private_owner(
    child_name: str,
    method: str,
) -> None:
    fake = seeded_cancellation_resistant_glue()
    hub: MessageHub[Message] = MessageHub()
    if child_name == "catalog":
        child = GlueCatalogVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    elif child_name == "jobs":
        child = GlueJobsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    else:
        child = GlueCrawlersVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    child.construct()
    block = fake.block_provider(method)
    operation = asyncio.create_task(child.setup())
    await block.started.wait()

    child.dispose()
    shutdown = asyncio.create_task(child.shutdown())
    await block.cancellation_seen.wait()
    returned_before_release = shutdown.done()
    block.release.set()
    results = await asyncio.gather(shutdown, operation, return_exceptions=True)

    assert results == [None, None]
    assert not returned_before_release
    assert not child._operations.accepting  # type: ignore[attr-defined]
    assert child._operations.tasks == set()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("termination_order", ["dispose-first", "concurrent"])
async def test_dispose_shutdown_race_durably_drains_shared_provider_owner(
    termination_order: str,
) -> None:
    fake = seeded_cancellation_resistant_glue()
    page = make_page_vm(fake)
    await page.setup()
    block = fake.block_provider("get_table")
    selection = asyncio.create_task(page.select_table("sessions"))
    await block.started.wait()
    messages: list[Message] = []
    subscription = page.hub.messages.subscribe(on_next=messages.append)

    if termination_order == "dispose-first":
        page.dispose()
        shutdown = asyncio.create_task(page.shutdown())
    else:
        shutdown = asyncio.create_task(page.shutdown())
        await block.cancellation_seen.wait()
        page.dispose()
    await block.cancellation_seen.wait()
    returned_before_release = shutdown.done()
    messages.clear()
    block.release.set()
    results = await asyncio.gather(shutdown, selection, return_exceptions=True)

    await page.shutdown()
    page.dispose()

    assert results == [None, None]
    assert not returned_before_release
    assert not any(isinstance(message, PropertyChangedMessage) for message in messages)
    assert page._provider_tasks == set()  # type: ignore[attr-defined]
    assert page._shutdown_complete  # type: ignore[attr-defined]
    subscription.dispose()


@pytest.mark.asyncio
async def test_cancelled_caller_remains_cancelled_after_resistant_provider_releases() -> None:
    fake = seeded_cancellation_resistant_glue()
    page = make_page_vm(fake)
    await page.setup()
    block = fake.block_provider("get_table")
    selection = asyncio.create_task(page.select_table("sessions"))
    await block.started.wait()

    selection.cancel()
    await block.cancellation_seen.wait()
    block.release.set()

    with pytest.raises(asyncio.CancelledError):
        await selection
    assert getattr(page, "_provider_tasks", None) == set()


@pytest.mark.asyncio
async def test_interrupted_shutdown_is_retryable_after_durable_provider_drain() -> None:
    fake = seeded_cancellation_resistant_glue()
    page = make_page_vm(fake)
    await page.setup()
    block = fake.block_provider("get_table")
    selection = asyncio.create_task(page.select_table("sessions"))
    await block.started.wait()
    shutdown = asyncio.create_task(page.shutdown())
    cancellation = asyncio.create_task(block.cancellation_seen.wait())
    await asyncio.wait(
        {shutdown, cancellation},
        timeout=1,
        return_when=asyncio.FIRST_COMPLETED,
    )
    cancelled_provider = block.cancellation_seen.is_set()

    shutdown.cancel()
    block.release.set()
    first_result = await asyncio.gather(shutdown, return_exceptions=True)
    await asyncio.gather(selection, return_exceptions=True)
    completed_after_interruption = page._shutdown_complete  # type: ignore[attr-defined]
    await page.shutdown()
    cancellation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancellation

    assert cancelled_provider
    assert len(first_result) == 1
    assert isinstance(first_result[0], asyncio.CancelledError)
    assert not completed_after_interruption
    assert page._shutdown_complete  # type: ignore[attr-defined]
    assert getattr(page, "_provider_tasks", None) == set()


@pytest.mark.asyncio
async def test_actions_and_direct_children_fail_closed_during_terminal_drain() -> None:
    fake = seeded_cancellation_resistant_glue()
    page = make_page_vm(fake)
    await page.setup()
    await page.select_view("jobs")
    await page.select_view("crawlers")
    block = fake.block_provider("get_crawler")
    selection = asyncio.create_task(page.select_crawler("running-crawler"))
    await block.started.wait()
    selected_run = page.jobs.selected_run_id
    selected_table = page.catalog.selected_table_name
    selected_crawler = page.crawlers.selected_crawler_name
    shutdown = asyncio.create_task(page.shutdown())
    cancellation = asyncio.create_task(block.cancellation_seen.wait())
    await asyncio.wait(
        {shutdown, cancellation},
        timeout=1,
        return_when=asyncio.FIRST_COMPLETED,
    )
    calls_before = tuple(fake.provider_calls)
    handoffs: list[Message] = []
    subscription = page.hub.messages.subscribe(on_next=handoffs.append)

    page_open_s3 = getattr(page, "open_s3_location", page.catalog.open_s3_location)
    page_query = getattr(page, "query_in_athena", page.catalog.query_in_athena)
    page_select_run = getattr(page, "select_job_run", page.jobs.select_run)
    open_result = page_open_s3()
    query_result = page_query()
    direct_open_result = page.catalog.open_s3_location()
    direct_query_result = page.catalog.query_in_athena()
    page_select_run("jr-2")
    page.jobs.select_run("jr-2")
    await page.catalog.select_table("events")
    await page.jobs.select_job("hourly")
    calls_during_drain = tuple(fake.provider_calls)

    block.release.set()
    await asyncio.gather(shutdown, selection, return_exceptions=True)
    await page.crawlers.select_crawler("ready-crawler")
    post_shutdown_open = page_open_s3()
    post_shutdown_query = page_query()
    page_select_run("jr-2")
    cancellation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancellation

    assert not open_result
    assert not query_result
    assert not direct_open_result
    assert not direct_query_result
    assert not post_shutdown_open
    assert not post_shutdown_query
    assert handoffs == []
    assert calls_during_drain == calls_before
    assert page.catalog.selected_table_name == selected_table
    assert page.jobs.selected_run_id == selected_run
    assert page.crawlers.selected_crawler_name == selected_crawler
    subscription.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("termination_order", ["dispose-first", "concurrent"])
async def test_page_shutdown_still_drains_catalog_after_dispose(
    monkeypatch: pytest.MonkeyPatch,
    termination_order: str,
) -> None:
    page = make_page_vm(seeded_glue())
    drain_started = asyncio.Event()
    drain_release = asyncio.Event()
    drain_calls = 0

    async def drain_catalog() -> None:
        nonlocal drain_calls
        drain_calls += 1
        drain_started.set()
        await drain_release.wait()

    monkeypatch.setattr(page.catalog, "shutdown", drain_catalog)

    if termination_order == "dispose-first":
        page.dispose()
        shutdown = asyncio.create_task(page.shutdown())
    else:
        shutdown = asyncio.create_task(page.shutdown())
        page.dispose()
    await asyncio.sleep(0)
    started_before_release = drain_started.is_set()
    drain_release.set()
    await shutdown

    await page.shutdown()
    page.dispose()

    assert started_before_release
    assert drain_calls == 1


@pytest.mark.asyncio
async def test_setup_during_and_after_shutdown_cannot_resurrect_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    page = make_page_vm(fake, selection_store=store)
    shutdown_started = asyncio.Event()
    shutdown_release = asyncio.Event()
    original_shutdown = page.catalog.shutdown

    async def blocked_shutdown() -> None:
        shutdown_started.set()
        await shutdown_release.wait()
        await original_shutdown()

    monkeypatch.setattr(page.catalog, "shutdown", blocked_shutdown)
    shutdown = asyncio.create_task(page.shutdown())
    await shutdown_started.wait()

    await page.setup()
    calls_during_shutdown = (
        tuple(fake.database_tokens),
        tuple(fake.table_requests),
        tuple(fake.table_detail_requests),
    )
    shutdown_release.set()
    await shutdown
    await page.setup()

    assert calls_during_shutdown == ((), (), ())
    assert fake.database_tokens == []
    assert fake.table_requests == []
    assert fake.table_detail_requests == []
    assert page.catalog.databases == ()
    assert page._loaded_views == set()  # type: ignore[attr-defined]
    assert store.get(scope, "database_name") is None
    assert store.get(scope, "table_name") is None


@pytest.mark.asyncio
async def test_public_page_actions_are_terminal_after_shutdown() -> None:
    fake = seeded_glue()
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    page = make_page_vm(fake, selection_store=store)
    await page.setup()
    await page.shutdown()
    ref = fake.tables["analytics"][1].ref
    active_view = page.active_view
    loaded_views = set(page._loaded_views)  # type: ignore[attr-defined]
    selected_database = page.catalog.selected_database_name
    selected_table = page.catalog.selected_table_name
    stored = {
        key: store.get(scope, key)
        for key in (
            "active_view",
            "database_name",
            "table_name",
            "job_name",
            "job_run_states",
            "crawler_name",
            "crawler_state",
        )
    }
    call_counts = (
        len(fake.database_tokens),
        len(fake.table_requests),
        len(fake.table_detail_requests),
        len(fake.job_tokens),
        len(fake.run_requests),
        len(fake.crawler_requests),
        len(fake.crawler_detail_requests),
    )

    await page.setup()
    await page.select_view("jobs")
    await page.refresh_active()
    await page.select_database("analytics")
    await page.select_table("sessions")
    await page.select_job("nightly")
    await page.set_job_run_states(frozenset({"FAILED"}))
    await page.select_crawler("ready-crawler")
    await page.set_crawler_state("READY")
    with pytest.raises(ValueError, match="table"):
        await page.open_table(ref)
    await page.shutdown()

    assert page.active_view == active_view
    assert page._loaded_views == loaded_views  # type: ignore[attr-defined]
    assert page.catalog.selected_database_name == selected_database
    assert page.catalog.selected_table_name == selected_table
    assert {
        key: store.get(scope, key)
        for key in (
            "active_view",
            "database_name",
            "table_name",
            "job_name",
            "job_run_states",
            "crawler_name",
            "crawler_state",
        )
    } == stored
    assert (
        len(fake.database_tokens),
        len(fake.table_requests),
        len(fake.table_detail_requests),
        len(fake.job_tokens),
        len(fake.run_requests),
        len(fake.crawler_requests),
        len(fake.crawler_detail_requests),
    ) == call_counts


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["shutdown", "dispose"])
async def test_actions_available_tracks_page_terminal_lifecycle(termination: str) -> None:
    page = make_page_vm(seeded_glue())

    assert page.actions_available

    if termination == "shutdown":
        await page.shutdown()
    else:
        page.dispose()

    assert not page.actions_available


def test_dispose_cascades_once_without_disposing_service_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = OwnedSelectionStore()
    page = make_page_vm(seeded_glue(), selection_store=store)
    children = [page.catalog, page.jobs, page.crawlers]
    calls = {id(child): 0 for child in children}
    for child in children:
        original = child.dispose

        def counted_dispose(*, target: object = child, dispose: object = original) -> None:
            calls[id(target)] += 1
            dispose()  # type: ignore[operator]

        monkeypatch.setattr(child, "dispose", counted_dispose)

    page.dispose()
    page.dispose()

    assert set(calls.values()) == {1}
    assert store.dispose_calls == 0
    assert page._inner.status is ConstructionStatus.DISPOSED  # type: ignore[attr-defined]
