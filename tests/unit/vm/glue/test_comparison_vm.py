"""Event-gated ownership and bounded discovery for Glue comparisons."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.data_catalog import (
    DatabaseSummary,
    StorageDescriptor,
    TableDetail,
    TableFormat,
    TableRef,
    TableSummary,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderUnreachableError,
)
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.glue.service import GlueClientFactory, GlueService
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.comparison_ports import ResolvedSource
from aws_tui.vm.glue.comparison_vm import GlueComparisonVM
from aws_tui.vm.service_source_vm import ServiceSourceContext

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def ref(name: str = "a", source: str = "prod") -> TableRef:
    return TableRef("AwsDataCatalog", "analytics", name, source, "us-east-1")


def detail(table: TableRef) -> TableDetail:
    return TableDetail(
        TableSummary(table, None, None, None, None, None),
        (),
        (),
        StorageDescriptor(None, None, None, None, False, 0),
        None,
        TableFormat.OTHER,
        (("key", "secret"),),
    )


class Gate:
    def __init__(self, value=None, error: Exception | None = None) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.value = value
        self.error = error

    async def run(self):
        self.started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
        if self.error is not None:
            raise self.error
        return self.value


class Client:
    def __init__(self) -> None:
        self.details: list[Gate | Exception | TableDetail] = []
        self.database_pages = []
        self.table_pages = []
        self.calls = []

    async def get_table(self, table: TableRef) -> TableDetail:
        self.calls.append(("detail", table))
        response = self.details.pop(0) if self.details else detail(table)
        if isinstance(response, Gate):
            return await response.run()
        if isinstance(response, Exception):
            raise response
        return response

    async def list_databases_page(self, *, start_token=None):
        self.calls.append(("databases", start_token))
        response = self.database_pages.pop(0) if self.database_pages else ([], None)
        if isinstance(response, Gate):
            return await response.run()
        return response

    async def list_tables_page(self, database: str, *, start_token=None):
        self.calls.append(("tables", database, start_token))
        response = self.table_pages.pop(0) if self.table_pages else ([], None)
        if isinstance(response, Gate):
            return await response.run()
        return response


class Router:
    def __init__(self) -> None:
        self.connections = {
            name: Connection(name, "aws", "us-east-1", "config") for name in ("prod", "dev")
        }
        self.clients = {name: Client() for name in self.connections}
        self.resolves = []

    def sources(self):
        return tuple(ServiceSourceContext.from_connection(c) for c in self.connections.values())

    def resolve(self, name: str, region: str) -> ResolvedSource:
        self.resolves.append((name, region))
        return ResolvedSource(replace(self.connections[name], region=region), self.clients[name])

    def is_current(self, source: ResolvedSource) -> bool:
        current = self.connections.get(source.connection_name)
        return current is not None and replace(current, region=source.region) == source.connection


class Harness:
    def __init__(self) -> None:
        self.router = Router()
        self.clock_calls = []
        self.hub = MessageHub()
        self.vm = GlueComparisonVM(
            router=self.router, hub=self.hub, dispatcher=NULL_DISPATCHER, clock=self.clock
        )
        self.vm.construct()

    def clock(self) -> datetime:
        self.clock_calls.append(NOW)
        return NOW

    async def pin(self, side="left", table=None):
        await self.vm.load_revision(side, self.vm.pin(side, table or ref()))


@pytest.fixture
def h():
    return Harness()


async def test_both_refs_start_unset_and_pin_fetches_only_selected_side(h) -> None:
    assert h.vm.side("left").selected_table is None
    assert h.vm.side("right").selected_table is None
    assert h.vm.comparison is None
    assert h.vm.summary_text() is None
    assert h.router.resolves == []
    await h.pin()
    assert h.vm.side("left").snapshot.ref == ref()
    assert h.vm.side("left").snapshot.fetched_at == NOW
    assert h.vm.side("right").snapshot is None
    assert h.clock_calls == [NOW]
    assert h.router.clients["prod"].calls == [("detail", ref())]
    await h.vm.shutdown()


async def test_refresh_left_retains_previous_snapshot_and_never_touches_right(h) -> None:
    await h.pin()
    await h.pin("right", ref("r", "dev"))
    right = h.vm.side("right")
    left_snapshot = h.vm.side("left").snapshot
    gate = Gate(detail(ref()))
    h.router.clients["prod"].details.append(gate)
    revision = h.vm.refresh("left")
    assert h.vm.side("left").snapshot is left_snapshot
    assert "previous fetch" in h.vm.side("left").status_text.lower()
    request = asyncio.create_task(h.vm.load_revision("left", revision))
    await gate.started.wait()
    assert h.vm.side("right") == right
    gate.release.set()
    await request
    assert h.vm.side("right") == right
    assert h.router.clients["dev"].calls == [("detail", ref("r", "dev"))]
    assert h.clock_calls == [NOW] * 3
    await h.vm.shutdown()


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (PermissionDeniedError("denied"), PaneState.FORBIDDEN),
        (ProviderUnreachableError("offline"), PaneState.UNREACHABLE),
        (AuthRequiredError("login"), PaneState.AUTH_REQUIRED),
    ],
)
async def test_failure_is_local_and_retains_previous_success(h, error, state) -> None:
    await h.pin()
    await h.pin("right", ref("r", "dev"))
    right = h.vm.side("right")
    before = h.vm.side("left").snapshot
    h.router.clients["prod"].details.append(error)
    await h.vm.load_revision("left", h.vm.refresh("left"))
    assert h.vm.side("left").state is state
    assert h.vm.side("left").snapshot is before
    assert h.vm.side("right") == right
    assert h.clock_calls == [NOW, NOW]
    assert "Left freshness: Refresh failed; showing previous fetch" in h.vm.summary_text()
    await h.vm.shutdown()


@pytest.mark.parametrize(
    "error", [None, PermissionDeniedError("stale"), RuntimeError("stale unexpected")]
)
@pytest.mark.parametrize("transition", ["swap", "aba", "refresh"])
async def test_cancel_resistant_stale_success_and_failure_never_publish_or_sample_clock(
    h, error, transition
) -> None:
    gate = Gate(detail(ref()), error)
    h.router.clients["prod"].details.append(gate)
    request = asyncio.create_task(h.vm.load_revision("left", h.vm.pin("left", ref())))
    await gate.started.wait()
    if transition == "refresh":
        newest = h.vm.refresh("left")
    else:
        newest = h.vm.choose_table("left", ref("b"))
        if transition == "aba":
            newest = h.vm.choose_table("left", ref())
    await h.vm.load_revision("left", newest)
    latest = h.vm.side("left")
    notifications = []
    sub = h.vm.on_property_changed.subscribe(notifications.append)
    gate.release.set()
    await request
    assert h.vm.side("left") == latest
    assert notifications == []
    assert h.clock_calls == [NOW]
    sub.dispose()
    await h.vm.shutdown()


async def test_queued_superseded_revision_starts_zero_provider_calls(h) -> None:
    stale = h.vm.pin("left", ref())
    h.vm.choose_table("left", ref("b"))
    await h.vm.load_revision("left", stale)
    assert h.router.clients["prod"].calls == []
    assert h.clock_calls == []
    await h.vm.shutdown()


@pytest.mark.parametrize("remove", [False, True])
@pytest.mark.parametrize("error", [None, RuntimeError("old-route-error")])
async def test_route_replacement_or_removal_invalidates_success_and_error(h, remove, error) -> None:
    gate = Gate(detail(ref()), error)
    h.router.clients["prod"].details.append(gate)
    request = asyncio.create_task(h.vm.load_revision("left", h.vm.pin("left", ref())))
    await gate.started.wait()
    if remove:
        del h.router.connections["prod"]
    else:
        h.router.connections["prod"] = replace(h.router.connections["prod"], profile="replacement")
    gate.release.set()
    await request
    assert h.vm.side("left").snapshot is None
    assert h.vm.side("left").state is PaneState.ERROR
    assert "source changed" in h.vm.side("left").error_text.lower()
    assert h.clock_calls == []
    await h.vm.shutdown()


async def test_response_identity_is_checked_before_clock(h) -> None:
    h.router.clients["prod"].details.append(detail(ref("wrong")))
    await h.pin()
    assert h.vm.side("left").snapshot is None
    assert h.vm.side("left").state is PaneState.ERROR
    assert h.clock_calls == []
    await h.vm.shutdown()


async def test_source_and_database_choices_clear_downstream_data_without_auto_selection(h) -> None:
    await h.pin()
    db = ref().database
    revision = h.vm.choose_database("left", db)
    assert h.vm.side("left").selected_table is None
    assert h.vm.side("left").snapshot is None
    h.router.clients["prod"].table_pages.append(([detail(ref()).summary], None))
    await h.vm.load_revision("left", revision)
    assert h.vm.side("left").tables == (detail(ref()).summary,)
    assert h.vm.side("left").selected_table is None
    revision = h.vm.choose_source("left", "dev", "us-east-1")
    assert h.vm.side("left").selected_database is None
    assert h.vm.side("left").tables == ()
    await h.vm.load_revision("left", revision)
    assert h.vm.side("left").selected_database is None
    await h.vm.shutdown()


@pytest.mark.parametrize("kind", ["databases", "tables"])
async def test_stale_list_cannot_repopulate_after_source_or_database_change(h, kind) -> None:
    client = h.router.clients["prod"]
    if kind == "databases":
        gate = Gate(([DatabaseSummary(ref().database, None, None, None)], None))
        client.database_pages.append(gate)
        old = h.vm.choose_source("left", "prod", "us-east-1")
    else:
        h.vm.pin("left", ref())
        gate = Gate(([detail(ref()).summary], None))
        client.table_pages.append(gate)
        old = h.vm.choose_database("left", ref().database)
    request = asyncio.create_task(h.vm.load_revision("left", old))
    await gate.started.wait()
    h.vm.choose_source("left", "dev", "us-east-1")
    before = h.vm.side("left")
    gate.release.set()
    await request
    assert h.vm.side("left") == before
    assert h.clock_calls == []
    await h.vm.shutdown()


async def test_manual_paging_forwards_tokens_deduplicates_and_prevents_overlapping_more(h) -> None:
    client = h.router.clients["prod"]
    db = DatabaseSummary(ref().database, None, None, None)
    other = replace(db, ref=replace(db.ref, database_name="other"))
    client.database_pages.append(([db], "next"))
    gate = Gate(([db, other], None))
    client.database_pages.append(gate)
    revision = h.vm.choose_source("left", "prod", "us-east-1")
    await h.vm.load_revision("left", revision)
    assert h.vm.side("left").has_more_databases
    assert h.vm.side("left").selected_database is None
    more = asyncio.create_task(h.vm.load_more_databases("left", revision))
    await gate.started.wait()
    await h.vm.load_more_databases("left", revision)
    assert client.calls == [("databases", None), ("databases", "next")]
    gate.release.set()
    await more
    assert h.vm.side("left").databases == (db, other)
    assert not h.vm.side("left").has_more_databases
    revision = h.vm.choose_database("left", db.ref)
    first = detail(ref()).summary
    second = detail(ref("b")).summary
    client.table_pages.extend([([first], "table-next"), ([first, second], None)])
    await h.vm.load_revision("left", revision)
    await h.vm.load_more_tables("left", revision)
    assert h.vm.side("left").tables == (first, second)
    assert client.calls[-1] == ("tables", "analytics", "table-next")
    assert h.vm.side("left").selected_table is None
    await h.vm.shutdown()


@pytest.mark.parametrize("kind", ["databases", "tables"])
@pytest.mark.parametrize("limit", ["items", "pages", "empty", "cycle"])
async def test_discovery_stops_at_exact_safety_limits(h, kind, limit) -> None:
    client = h.router.clients["prod"]

    def row(i):
        if kind == "databases":
            return DatabaseSummary(
                replace(ref().database, database_name=f"db{i}"), None, None, None
            )
        return detail(ref(f"t{i}")).summary

    if limit == "items":
        pages = [([row(i) for i in range(1001)], "next")]
        expected_calls, expected_rows = 1, 1000
    elif limit == "pages":
        pages = [([row(i)], str(i + 1)) for i in range(64)]
        expected_calls, expected_rows = 64, 64
    elif limit == "empty":
        pages = [([], str(i + 1)) for i in range(3)]
        expected_calls, expected_rows = 3, 0
    else:
        pages = [([row(0)], "x"), ([row(1)], "x")]
        expected_calls, expected_rows = 2, 2
    if kind == "databases":
        client.database_pages.extend(pages)
        revision = h.vm.choose_source("left", "prod", "us-east-1")
        more = h.vm.load_more_databases
    else:
        client.table_pages.extend(pages)
        h.vm.pin("left", ref())
        revision = h.vm.choose_database("left", ref().database)
        more = h.vm.load_more_tables
    await h.vm.load_revision("left", revision)
    for _ in range(70):
        await more("left", revision)
    state = h.vm.side("left")
    assert len(getattr(state, kind)) == expected_rows
    assert getattr(
        state, "database_limit_reached" if kind == "databases" else "table_limit_reached"
    )
    assert not getattr(state, "has_more_" + kind)
    assert len(client.calls) == expected_calls
    await h.vm.shutdown()


async def test_foreign_discovery_refs_are_rejected_before_publication(h) -> None:
    client = h.router.clients["prod"]
    client.database_pages.append(
        ([DatabaseSummary(ref(source="dev").database, None, None, None)], None)
    )
    await h.vm.load_revision("left", h.vm.choose_source("left", "prod", "us-east-1"))
    assert h.vm.side("left").state is PaneState.ERROR
    assert h.vm.side("left").databases == ()
    await h.vm.shutdown()


async def test_filter_never_changes_full_summary_and_property_messages_have_no_values(h) -> None:
    await h.pin()
    assert h.vm.summary_text() is None
    await h.pin("right", ref("b", "dev"))
    before = h.vm.summary_text()
    notifications = []
    hub_messages = []
    sub = h.vm.on_property_changed.subscribe(notifications.append)
    hub_sub = h.hub.messages.subscribe(hub_messages.append)
    h.vm.toggle_differences_only()
    assert h.vm.differences_only
    assert h.vm.summary_text() == before
    assert notifications == ["differences_only"]
    assert len(hub_messages) == 1
    assert "secret" not in repr(hub_messages)
    assert "Left freshness:" in before
    assert "Right freshness:" in before
    sub.dispose()
    hub_sub.dispose()
    await h.vm.shutdown()


async def test_close_drains_both_cancellation_resistant_requests_without_notifications(h) -> None:
    left = Gate(detail(ref()))
    right = Gate(detail(ref("b", "dev")), RuntimeError("stale failure"))
    h.router.clients["prod"].details.append(left)
    h.router.clients["dev"].details.append(right)
    requests = [
        asyncio.create_task(h.vm.load_revision("left", h.vm.pin("left", ref()))),
        asyncio.create_task(h.vm.load_revision("right", h.vm.pin("right", ref("b", "dev")))),
    ]
    await left.started.wait()
    await right.started.wait()
    events = []
    sub = h.vm.on_property_changed.subscribe(events.append)
    hub_sub = h.hub.messages.subscribe(events.append)
    h.vm.close()
    terminal = (h.vm.side("left"), h.vm.side("right"))
    shutdown = asyncio.create_task(h.vm.shutdown())
    await left.cancelled.wait()
    await right.cancelled.wait()
    assert not shutdown.done()
    left.release.set()
    right.release.set()
    await asyncio.gather(*requests, shutdown)
    await h.vm.shutdown()
    await h.vm.load_revision("left", h.vm.pin("left", ref("new")))
    assert (h.vm.side("left"), h.vm.side("right")) == terminal
    assert events == []
    assert h.clock_calls == []
    assert h.vm.summary_text() is None
    sub.dispose()
    hub_sub.dispose()


async def test_glue_service_builds_vm_without_changing_page_or_creating_athena() -> None:
    router = Router()
    service = GlueService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=cast(GlueClientFactory, lambda c: router.clients[c.name]),
    )
    vm = service.build_comparison_vm(
        connections=lambda: tuple(router.connections.values()), clock=lambda: NOW
    )
    vm.construct()
    await vm.load_revision("left", vm.pin("left", ref()))
    assert vm.side("left").snapshot.fetched_at == NOW
    await vm.shutdown()


async def test_queued_provider_task_is_superseded_before_it_starts(h) -> None:
    revision = h.vm.pin("left", ref())
    request = asyncio.create_task(h.vm.load_revision("left", revision))
    # Run the caller, which enqueues the owned provider task, then supersede it.
    await asyncio.sleep(0)
    h.vm.choose_table("left", ref("b"))
    await request
    assert h.router.clients["prod"].calls == []
    assert h.clock_calls == []
    await h.vm.shutdown()


async def test_new_database_invalidates_pending_table_list(h) -> None:
    client = h.router.clients["prod"]
    h.vm.pin("left", ref())
    gate = Gate(([detail(ref()).summary], "next"))
    client.table_pages.append(gate)
    request = asyncio.create_task(
        h.vm.load_revision("left", h.vm.choose_database("left", ref().database))
    )
    await gate.started.wait()
    other = replace(ref().database, database_name="archive")
    revision = h.vm.choose_database("left", other)
    gate.release.set()
    await request
    assert h.vm.side("left").selected_database == other
    assert h.vm.side("left").tables == ()
    assert not h.vm.side("left").has_more_tables
    await h.vm.load_revision("left", revision)
    assert client.calls[-1] == ("tables", "archive", None)
    await h.vm.shutdown()


async def test_refresh_resolves_a_replaced_route_and_preserves_right_error(h) -> None:
    await h.pin()
    h.router.clients["dev"].details.append(PermissionDeniedError("denied"))
    await h.pin("right", ref("r", "dev"))
    right = h.vm.side("right")
    h.router.connections["prod"] = replace(h.router.connections["prod"], profile="new-profile")
    await h.vm.load_revision("left", h.vm.refresh("left"))
    assert h.vm.side("left").state is PaneState.IDLE
    assert h.vm.side("right") == right
    assert h.clock_calls == [NOW, NOW]
    assert h.router.resolves == [("prod", "us-east-1"), ("dev", "us-east-1"), ("prod", "us-east-1")]
    await h.vm.shutdown()


async def test_cancelled_shutdown_still_drains_both_sides(h) -> None:
    gates = [Gate(detail(ref())), Gate(detail(ref("b", "dev")))]
    h.router.clients["prod"].details.append(gates[0])
    h.router.clients["dev"].details.append(gates[1])
    tasks = [
        asyncio.create_task(h.vm.load_revision("left", h.vm.pin("left", ref()))),
        asyncio.create_task(h.vm.load_revision("right", h.vm.pin("right", ref("b", "dev")))),
    ]
    for gate in gates:
        await gate.started.wait()
    shutdown = asyncio.create_task(h.vm.shutdown())
    for gate in gates:
        await gate.cancelled.wait()
    shutdown.cancel()
    # Deliver cancellation inside the owner's shielded drain, before release.
    await asyncio.sleep(0)
    for gate in gates:
        gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    await asyncio.gather(*tasks)
    await h.vm.shutdown()
    assert all(task.done() for task in tasks)
    assert h.clock_calls == []


async def test_three_empty_page_limit_is_consecutive_not_cumulative(h) -> None:
    client = h.router.clients["prod"]
    row = DatabaseSummary(ref().database, None, None, None)
    client.database_pages.extend([([], "1"), ([row], "2"), ([], "3"), ([], None)])
    revision = h.vm.choose_source("left", "prod", "us-east-1")
    await h.vm.load_revision("left", revision)
    for _ in range(3):
        await h.vm.load_more_databases("left", revision)
    assert h.vm.side("left").databases == (row,)
    assert not h.vm.side("left").database_limit_reached
    assert not h.vm.side("left").has_more_databases
    await h.vm.shutdown()


async def test_foreign_selection_rejected_without_provider_or_clock(h) -> None:
    h.vm.choose_source("left", "prod", "us-east-1")
    revision = h.vm.choose_database("left", ref(source="dev").database)
    await h.vm.load_revision("left", revision)
    assert h.vm.side("left").state is PaneState.ERROR
    assert h.vm.side("left").selected_database is None
    assert h.router.clients["prod"].calls == []
    assert h.clock_calls == []
    await h.vm.shutdown()
