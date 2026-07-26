from __future__ import annotations

import asyncio

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.page_vm import GluePageVM
from aws_tui.vm.messages import OpenAthenaTableRequest
from aws_tui.vm.service_source_vm import SelectionScope, ServiceSelectionStore
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


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
) -> GluePageVM:
    hub: MessageHub[Message] = MessageHub()
    page = GluePageVM(
        client=fake,
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
