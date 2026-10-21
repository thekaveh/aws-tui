"""Explicit schema completion is bounded and owned by one current AWS page."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
from dataclasses import replace

import pytest

from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.domain.data_catalog import Column
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.domain.query import QueryContext
from aws_tui.domain.sql_authoring import CompletionTable
from tests.helpers import wait_until
from tests.unit.vm.athena.test_query_vm import make_query_vm

CTX = QueryContext("fixture", "eu-west-1", "analytics", "catalog", "sales")


def api():
    assert importlib.util.find_spec("aws_tui.vm.athena.completion_vm") is not None, (
        "Bounded completion controller is missing"
    )
    return importlib.import_module("aws_tui.vm.athena.completion_vm")


def client():
    fake = InMemoryAthena(connection_name=CTX.connection_name, region=CTX.region)
    fake.add_workgroup(CTX.workgroup, output_location="s3://fixture/results/")
    fake.add_catalog(CTX.workgroup, CTX.catalog)
    fake.add_database(CTX.workgroup, CTX.catalog, CTX.database)
    fake.add_table(
        CTX.workgroup,
        CTX.catalog,
        CTX.database,
        "events",
        columns=(Column("amount", "bigint", None, False), Column("day", "date", None, True)),
    )
    return fake


def make(tmp_path, *, fake=None, **kwargs):
    m = api()
    fake = fake or client()
    query = make_query_vm(fake, context=CTX)
    vm = m.AthenaCompletionVM(client=fake, query=query, **kwargs)
    return vm, query, fake


async def close(vm, query):
    await vm.shutdown()
    await query.shutdown()
    query.dispose()


async def test_explicit_command_offers_current_schema_without_execution(tmp_path):
    vm, query, fake = make(tmp_path)
    try:
        query.set_sql("SELECT  FROM events")
        assert not fake.calls
        batch = await vm.complete(7)
        assert batch is not None
        assert vm.is_current(batch)
        assert batch.state == "ready"
        assert any(s.kind == "keyword" and s.text == "SELECT" for s in batch.choices)
        assert any(s.kind == "column" and s.text == '"amount"' for s in batch.choices)
        assert any(s.text == '"day"' for s in batch.choices)
        assert all(c.method in {"list_tables_page", "get_table_columns"} for c in fake.calls)
        assert next(c.arguments for c in fake.calls if c.method == "get_table_columns") == (
            "catalog",
            "sales",
            "events",
            "analytics",
        )
    finally:
        await close(vm, query)


async def test_paging_and_display_are_bounded_and_partial_is_visible(tmp_path):
    fake = client()
    fake.page_size = 2
    for index in range(20):
        fake.add_table("analytics", "catalog", "sales", f"table_{index}")
    vm, query, _ = make(tmp_path, fake=fake)
    try:
        query.set_sql("SELECT * FROM ")
        batch = await vm.complete(len(query.sql))
        assert batch is not None
        assert batch.state == "partial"
        assert batch.message
        assert len(batch.choices) <= 200
        assert len([c for c in fake.calls if c.method == "list_tables_page"]) == 4
        assert not any(c.method == "get_table_columns" for c in fake.calls)
    finally:
        await close(vm, query)


async def test_select_comma_after_scalar_subquery_fetches_outer_schema(tmp_path):
    fake = client()
    fake.add_table(
        CTX.workgroup,
        CTX.catalog,
        CTX.database,
        "other",
        columns=(Column("inner_only", "bigint", None, False),),
    )
    vm, query, _ = make(tmp_path, fake=fake)
    try:
        marked = "SELECT (SELECT count(*) FROM other), | FROM events"
        query.set_sql(marked.replace("|", ""))
        batch = await vm.complete(marked.index("|"))
        assert batch is not None
        assert batch.state == "ready"
        assert any(s.text == '"amount"' and s.kind == "column" for s in batch.choices)
        assert not any(s.text == '"inner_only"' or s.kind == "table" for s in batch.choices)
        assert any(
            c.method == "get_table_columns" and c.arguments[2] == "events" for c in fake.calls
        )
        assert all(c.method in {"list_tables_page", "get_table_columns"} for c in fake.calls)
    finally:
        await close(vm, query)


async def test_details_fanout_is_capped_even_for_many_referenced_tables(tmp_path):
    fake = client()
    for index in range(9):
        fake.add_table(
            "analytics",
            "catalog",
            "sales",
            f"t{index}",
            columns=(Column(f"c{index}", "int", None, False),),
        )
    vm, query, _ = make(tmp_path, fake=fake)
    try:
        query.set_sql("SELECT  FROM t0" + "".join(f" JOIN t{i} ON true" for i in range(1, 9)))
        batch = await vm.complete(7)
        assert batch is not None
        assert batch.state == "partial"
        assert len([c for c in fake.calls if c.method == "get_table_columns"]) == 4
    finally:
        await close(vm, query)


async def test_schema_cache_is_reused_then_dropped_for_context_change_and_refresh(tmp_path):
    fake = client()
    fake.add_database("analytics", "catalog", "other")
    fake.add_table(
        "analytics",
        "catalog",
        "other",
        "events",
        columns=(Column("new_column", "varchar", None, False),),
    )
    revision = [0]
    vm, query, _ = make(tmp_path, fake=fake, context_revision=lambda: revision[0])
    try:
        query.set_sql("SELECT  FROM events")
        first = await vm.complete(7)
        count = len(fake.calls)
        assert await vm.complete(7) is not None
        assert len(fake.calls) == count
        await query.set_context(replace(CTX, database="other"))
        changed = await vm.complete(7)
        assert changed is not None
        assert not vm.is_current(first)
        assert any(s.text == '"new_column"' for s in changed.choices)
        assert not any(s.text == '"amount"' for s in changed.choices)
        count = len(fake.calls)
        revision[0] += 1
        vm.invalidate()
        assert not vm.is_current(changed)
        assert await vm.complete(7) is not None
        assert len(fake.calls) > count
    finally:
        await close(vm, query)


async def test_new_provider_lifetime_cannot_accept_old_batch(tmp_path):
    first, query, fake = make(tmp_path)
    second = None
    try:
        query.set_sql("SELECT  FROM events")
        batch = await first.complete(7)
        second = api().AthenaCompletionVM(client=fake, query=query)
        assert not second.is_current(batch)
    finally:
        if second is not None:
            await second.shutdown()
        await close(first, query)


async def test_denial_keeps_keywords_with_visible_unavailable_state(tmp_path):
    fake = client()
    fake.access_error = PermissionDeniedError("private-provider-value")
    vm, query, _ = make(tmp_path, fake=fake)
    try:
        query.set_sql("SEL")
        batch = await vm.complete(3)
        assert batch is not None
        assert batch.state == "unavailable"
        assert any(s.text == "SELECT" for s in batch.choices)
        assert "private-provider-value" not in batch.message
        assert "private-provider-value" not in vm.message
    finally:
        await close(vm, query)


async def test_missing_context_still_offers_keywords_without_metadata_requests(tmp_path):
    vm, query, fake = make(tmp_path)
    try:
        await query.set_context(replace(CTX, database=""))
        query.set_sql("SEL")
        batch = await vm.complete(3)
        assert batch is not None
        assert batch.state == "unavailable"
        assert any(s.text == "SELECT" for s in batch.choices)
        assert not fake.calls
    finally:
        await close(vm, query)


class GatedMetadata(InMemoryAthena):
    def __init__(self, source):
        self.__dict__.update(source.__dict__)
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def list_tables_page(self, *args, **kwargs):
        self.entered.set()
        await self.release.wait()
        return await super().list_tables_page(*args, **kwargs)


async def test_edit_during_metadata_request_retires_old_result(tmp_path):
    fake = GatedMetadata(client())
    vm, query, _ = make(tmp_path, fake=fake)
    pending = None
    try:
        query.set_sql("SELECT  FROM events")
        pending = asyncio.create_task(vm.complete(7))
        await wait_until(fake.entered.is_set, what="completion metadata started")
        query.set_sql("SELECT 1")
        fake.release.set()
        assert await pending is None
        assert not vm.busy
    finally:
        fake.release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(vm, query)


async def test_deadline_is_aggregate_and_does_not_submit(tmp_path):
    fake = GatedMetadata(client())
    vm, query, _ = make(tmp_path, fake=fake, metadata_timeout=0.02)
    try:
        query.set_sql("SEL")
        batch = await asyncio.wait_for(vm.complete(3), timeout=1)
        assert batch is not None
        assert batch.state == "unavailable"
        assert batch.message
        assert any(s.text == "SELECT" for s in batch.choices)
        assert not any(c.method == "start_query" for c in fake.calls)
    finally:
        fake.release.set()
        await close(vm, query)


async def test_shutdown_cancels_and_drains_metadata_owner(tmp_path):
    fake = GatedMetadata(client())
    vm, query, _ = make(tmp_path, fake=fake)
    pending = None
    try:
        query.set_sql("SELECT  FROM events")
        pending = asyncio.create_task(vm.complete(7))
        await wait_until(fake.entered.is_set, what="completion metadata started")
        await vm.shutdown()
        assert pending.done()
        assert await pending is None
        assert await vm.complete(7) is None
    finally:
        fake.release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(vm, query)


class SwallowedCancellation(GatedMetadata):
    async def list_tables_page(self, *args, **kwargs):
        self.entered.set()
        with contextlib.suppress(asyncio.CancelledError):
            await self.release.wait()
        return await InMemoryAthena.list_tables_page(self, *args, **kwargs)


async def test_provider_cannot_swallow_user_cancel_and_publish_completion(tmp_path):
    fake = SwallowedCancellation(client())
    vm, query, _ = make(tmp_path, fake=fake)
    pending = None
    try:
        query.set_sql("SELECT  FROM events")
        pending = asyncio.create_task(vm.complete(7))
        await wait_until(fake.entered.is_set, what="metadata cancellation boundary")
        pending.cancel()
        assert await pending is None
        assert not any(c.method == "get_table_columns" for c in fake.calls)
    finally:
        fake.release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(vm, query)


async def test_expired_budget_cannot_start_more_reads_after_provider_swallows_cancel(tmp_path):
    fake = SwallowedCancellation(client())
    vm, query, _ = make(tmp_path, fake=fake, metadata_timeout=0.02)
    try:
        query.set_sql("SELECT  FROM events")
        batch = await asyncio.wait_for(vm.complete(7), timeout=1)
        assert batch is not None
        assert batch.state in {"partial", "unavailable"}
        assert not any(c.method == "get_table_columns" for c in fake.calls)
    finally:
        fake.release.set()
        await close(vm, query)


async def test_literal_cursor_has_unavailable_state_and_no_metadata_reads(tmp_path):
    vm, query, fake = make(tmp_path)
    try:
        query.set_sql("SELECT 'value' FROM events")
        batch = await vm.complete(10)
        assert batch is not None
        assert batch.state == "unavailable"
        assert not batch.choices
        assert not fake.calls
    finally:
        await close(vm, query)


async def test_database_wide_fallback_discloses_unread_table_schemas(tmp_path):
    fake = client()
    for index in range(5):
        fake.add_table(
            "analytics",
            "catalog",
            "sales",
            f"t{index}",
            columns=(Column(f"field_{index}", "int", None, False),),
        )
    vm, query, _ = make(tmp_path, fake=fake)
    try:
        query.set_sql("SELECT ")
        batch = await vm.complete(len(query.sql))
        assert batch is not None
        assert batch.state == "partial"
        assert len([c for c in fake.calls if c.method == "get_table_columns"]) == 4
    finally:
        await close(vm, query)


@pytest.mark.parametrize("name", [None, ""])
async def test_malformed_table_names_do_not_crash_or_offer_empty_identifiers(tmp_path, name):
    fake = client()
    row = fake.tables[("analytics", "catalog", "sales")][0]
    fake.tables[("analytics", "catalog", "sales")] = [
        replace(row, ref=replace(row.ref, table_name=name))
    ]
    vm, query, _ = make(tmp_path, fake=fake)
    try:
        query.set_sql("SELECT * FROM ")
        batch = await vm.complete(len(query.sql))
        assert batch is not None
        assert batch.state in {"partial", "unavailable"}
        assert not batch.choices
    finally:
        await close(vm, query)


@pytest.mark.parametrize(
    "column", [Column("", "int", None, False), Column("amount", "", None, False)]
)
async def test_empty_column_identity_or_type_is_unavailable_metadata(tmp_path, column):
    fake = client()

    async def malformed(*args, **kwargs):
        ref = fake.tables[("analytics", "catalog", "sales")][0].ref
        return CompletionTable(ref, (column,))

    fake.get_table_columns = malformed
    vm, query, _ = make(tmp_path, fake=fake)
    try:
        query.set_sql("SELECT  FROM events")
        batch = await vm.complete(7)
        assert batch is not None
        assert batch.state in {"partial", "unavailable"}
        assert not any(s.kind == "column" for s in batch.choices)
    finally:
        await close(vm, query)


async def test_detail_lookup_uses_unique_canonical_listed_name(tmp_path):
    vm, query, fake = make(tmp_path)
    try:
        query.set_sql("SELECT  FROM EVENTS")
        batch = await vm.complete(7)
        assert batch is not None
        assert batch.state == "ready"
        assert any(s.text == '"amount"' for s in batch.choices)
        assert (
            next(c.arguments[2] for c in fake.calls if c.method == "get_table_columns") == "events"
        )
    finally:
        await close(vm, query)


class WaitingAfterCancellation(GatedMetadata):
    def __init__(self, source):
        super().__init__(source)
        self.cancel_seen = asyncio.Event()

    async def list_tables_page(self, *args, **kwargs):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.release.wait()
        return await InMemoryAthena.list_tables_page(self, *args, **kwargs)


async def test_deadline_returns_without_abandoning_noncooperative_provider(tmp_path):
    fake = WaitingAfterCancellation(client())
    vm, query, _ = make(tmp_path, fake=fake, metadata_timeout=0.02)
    pending = shutdown = None
    try:
        query.set_sql("SEL")
        pending = asyncio.create_task(vm.complete(3))
        await wait_until(fake.cancel_seen.is_set, what="metadata deadline reached")
        done, _ = await asyncio.wait({pending}, timeout=0.2)
        assert pending in done, "Completion response must respect the aggregate deadline"
        batch = await pending
        assert batch is not None
        assert batch.state == "unavailable"
        assert any(s.text == "SELECT" for s in batch.choices)
        assert vm.busy, "Actual provider work must stay owned until it drains"
        assert await vm.complete(3) is None
        shutdown = asyncio.create_task(vm.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        fake.release.set()
        await shutdown
        assert not vm.busy
        assert not any(c.method == "get_table_columns" for c in fake.calls)
    finally:
        fake.release.set()
        await asyncio.gather(
            *(t for t in (pending, shutdown) if t is not None), return_exceptions=True
        )
        await close(vm, query)


async def test_user_cancellation_settles_loading_state(tmp_path):
    fake = GatedMetadata(client())
    vm, query, _ = make(tmp_path, fake=fake)
    pending = None
    try:
        query.set_sql("SEL")
        pending = asyncio.create_task(vm.complete(3))
        await wait_until(fake.entered.is_set, what="metadata read started")
        pending.cancel()
        assert await pending is None
        assert vm.state != "loading"
        assert "Loading" not in vm.message
    finally:
        fake.release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(vm, query)


async def test_outer_timeout_cannot_revive_a_retired_metadata_request(tmp_path):
    fake = WaitingAfterCancellation(client())
    vm, query, _ = make(tmp_path, fake=fake, metadata_timeout=1)
    try:
        query.set_sql("SELECT  FROM events")

        async def timed_command():
            async with asyncio.timeout(0.02):
                return await vm.complete(7)

        assert await timed_command() is None
        await wait_until(fake.cancel_seen.is_set, what="canceled metadata still owned")
        fake.release.set()
        await wait_until(lambda: not vm.busy, what="retired provider drained")
        assert not any(c.method == "get_table_columns" for c in fake.calls)
        assert vm._tables is None
        assert not vm._schemas
    finally:
        fake.release.set()
        await close(vm, query)
