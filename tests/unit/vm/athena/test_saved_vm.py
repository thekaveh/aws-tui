from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.query import NamedQuery, PreparedStatement, PreparedStatementSummary
from aws_tui.vm.athena.saved_vm import AthenaSavedVM, SavedQueryKind
from aws_tui.vm.file_manager.pane_vm import PaneState


class SavedClient:
    def __init__(self) -> None:
        self.named_pages: dict[tuple[str, str | None], tuple[list[str], str | None]] = {}
        self.named: dict[str, NamedQuery] = {}
        self.prepared_pages: dict[
            tuple[str, str | None],
            tuple[list[PreparedStatementSummary], str | None],
        ] = {}
        self.prepared: dict[tuple[str, str], PreparedStatement] = {}
        self.named_list_calls: list[tuple[str, str | None]] = []
        self.named_detail_calls: list[tuple[str, ...]] = []
        self.prepared_list_calls: list[tuple[str, str | None]] = []
        self.prepared_detail_calls: list[tuple[str, str]] = []
        self.block_named_request: tuple[str, str | None] | None = None
        self.named_fetch_started = asyncio.Event()
        self.release_named_fetch = asyncio.Event()

    async def list_named_queries_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[str], str | None]:
        request = (workgroup, start_token)
        self.named_list_calls.append(request)
        if request == self.block_named_request:
            self.named_fetch_started.set()
            await self.release_named_fetch.wait()
        return self.named_pages[request]

    async def get_named_queries(self, ids: list[str]) -> tuple[NamedQuery, ...]:
        self.named_detail_calls.append(tuple(ids))
        return tuple(self.named[query_id] for query_id in ids)

    async def list_prepared_statements_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PreparedStatementSummary], str | None]:
        request = (workgroup, start_token)
        self.prepared_list_calls.append(request)
        return self.prepared_pages[request]

    async def get_prepared_statement(
        self,
        name: str,
        workgroup: str,
    ) -> PreparedStatement:
        self.prepared_detail_calls.append((name, workgroup))
        return self.prepared[(name, workgroup)]


def _seeded_client() -> SavedClient:
    client = SavedClient()
    client.named = {
        "named-1": NamedQuery(
            "named-1",
            "Event count",
            "Counts events",
            "events",
            "SELECT count(*) FROM events",
            "analysts",
        ),
        "named-2": NamedQuery(
            "named-2",
            "Recent events",
            None,
            "events",
            "SELECT * FROM events LIMIT 10",
            "analysts",
        ),
    }
    first_summary = PreparedStatementSummary(
        "prepared-1",
        datetime(2026, 7, 25, tzinfo=UTC),
    )
    client.prepared[("prepared-1", "analysts")] = PreparedStatement(
        "prepared-1",
        "SELECT * FROM events WHERE id = ?",
        "analysts",
        "One event",
        first_summary.last_modified_at,
    )
    client.named_pages = {
        ("analysts", None): (["named-1"], "named-next"),
        ("analysts", "named-next"): (["named-2"], None),
    }
    client.prepared_pages = {
        ("analysts", None): ([first_summary], None),
    }
    return client


def make_saved_vm(client: SavedClient, workgroup: str = "analysts") -> AthenaSavedVM:
    hub: MessageHub[Message] = MessageHub()
    vm = AthenaSavedVM(
        client=client,
        workgroup=workgroup,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


@pytest.mark.asyncio
async def test_saved_lists_use_independent_token_pagers_without_fetch_all() -> None:
    client = _seeded_client()
    vm = make_saved_vm(client)

    await vm.setup()

    assert isinstance(vm._named_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert isinstance(vm._prepared_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert tuple(query.query_id for query in vm.named_queries) == ("named-1",)
    assert tuple(row.name for row in vm.prepared_statements) == ("prepared-1",)
    assert client.named_list_calls == [("analysts", None)]
    assert client.named_detail_calls == [("named-1",)]
    assert client.prepared_list_calls == [("analysts", None)]

    await vm.load_more_named_queries()

    assert tuple(query.query_id for query in vm.named_queries) == ("named-1", "named-2")
    assert client.named_list_calls[-1] == ("analysts", "named-next")
    assert client.named_detail_calls[-1] == ("named-2",)


@pytest.mark.asyncio
async def test_prepared_selection_uses_real_summary_then_detail_api() -> None:
    client = _seeded_client()
    vm = make_saved_vm(client)
    await vm.setup()

    await vm.select_prepared_statement("prepared-1")

    assert vm.selected_kind is SavedQueryKind.PREPARED
    assert vm.selected_query_id == "prepared-1"
    assert vm.selected_prepared_statement == client.prepared[("prepared-1", "analysts")]
    assert client.prepared_detail_calls == [("prepared-1", "analysts")]
    assert vm.selected_sql() == "SELECT * FROM events WHERE id = ?"


@pytest.mark.asyncio
async def test_named_selection_exposes_sql_only_through_explicit_access() -> None:
    client = _seeded_client()
    vm = make_saved_vm(client)
    await vm.setup()

    await vm.select_named_query("named-1")

    assert vm.selected_kind is SavedQueryKind.NAMED
    assert vm.selected_query_id == "named-1"
    assert vm.selected_sql() == "SELECT count(*) FROM events"
    assert "SELECT count(*) FROM events" not in repr(vm)
    assert "SELECT count(*) FROM events" not in repr(vm.selected_named_query)


@pytest.mark.asyncio
async def test_replacing_saved_workgroup_discards_late_named_queries() -> None:
    client = _seeded_client()
    old_secret = NamedQuery(
        "old-id",
        "Old",
        None,
        "private",
        "SELECT 'SAVED_SQL_SECRET'",
        "analysts",
    )
    new_query = NamedQuery(
        "new-id",
        "New",
        None,
        "public",
        "SELECT 1",
        "engineering",
    )
    client.named.update({"old-id": old_secret, "new-id": new_query})
    client.named_pages[("analysts", None)] = (["old-id"], None)
    client.named_pages[("engineering", None)] = (["new-id"], None)
    client.prepared_pages[("engineering", None)] = ([], None)
    client.block_named_request = ("analysts", None)
    vm = make_saved_vm(client)

    old_setup = asyncio.create_task(vm.setup())
    await client.named_fetch_started.wait()
    vm.replace_workgroup("engineering")
    await vm.setup()
    client.release_named_fetch.set()
    await old_setup

    assert vm.workgroup == "engineering"
    assert tuple(query.query_id for query in vm.named_queries) == ("new-id",)
    assert vm.selected_query_id is None
    assert "SAVED_SQL_SECRET" not in repr(vm)


@pytest.mark.asyncio
async def test_saved_shutdown_drains_blocked_load_and_disables_both_pagers() -> None:
    client = _seeded_client()
    client.block_named_request = ("analysts", None)
    vm = make_saved_vm(client)
    setup = asyncio.create_task(vm.setup())
    await client.named_fetch_started.wait()

    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    waited_for_page = not shutdown.done()
    client.release_named_fetch.set()
    await shutdown
    await setup

    assert waited_for_page
    assert vm.named_queries == ()
    assert vm.prepared_statements == ()
    assert vm.named_state is PaneState.EMPTY
    assert vm.prepared_state is PaneState.EMPTY
    assert not vm.load_more_named_command.can_execute()
    assert not vm.load_more_prepared_command.can_execute()
