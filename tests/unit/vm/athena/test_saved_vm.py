from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import ProviderError
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
        self.block_prepared_detail = False
        self.ignore_prepared_detail_cancellation = False
        self.prepared_detail_started = asyncio.Event()
        self.prepared_detail_cancelled = asyncio.Event()
        self.release_prepared_detail = asyncio.Event()
        self.active_prepared_details: set[tuple[str, str]] = set()

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
        request = (name, workgroup)
        self.prepared_detail_calls.append(request)
        self.active_prepared_details.add(request)
        self.prepared_detail_started.set()
        try:
            if self.block_prepared_detail:
                try:
                    await self.release_prepared_detail.wait()
                except asyncio.CancelledError:
                    self.prepared_detail_cancelled.set()
                    if not self.ignore_prepared_detail_cancellation:
                        raise
                    await self.release_prepared_detail.wait()
            return self.prepared[request]
        finally:
            self.active_prepared_details.discard(request)


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
async def test_saved_load_more_exposes_independent_busy_states() -> None:
    client = _seeded_client()
    second_prepared = PreparedStatementSummary(
        "prepared-2",
        datetime(2026, 7, 26, tzinfo=UTC),
    )
    client.prepared_pages[("analysts", None)] = (
        client.prepared_pages[("analysts", None)][0],
        "prepared-next",
    )
    client.prepared_pages[("analysts", "prepared-next")] = ([second_prepared], None)
    vm = make_saved_vm(client)
    await vm.setup()

    named_loading = asyncio.create_task(vm.load_more_named_queries())
    await named_loading
    assert not vm.is_loading_more_named_queries
    assert client.named_list_calls[-1] == ("analysts", "named-next")

    release = asyncio.Event()
    started = asyncio.Event()
    original = client.list_prepared_statements_page

    async def blocked_prepared(
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[PreparedStatementSummary], str | None]:
        if start_token == "prepared-next":
            started.set()
            await release.wait()
        return await original(workgroup, start_token=start_token)

    client.list_prepared_statements_page = blocked_prepared  # type: ignore[method-assign]
    prepared_loading = asyncio.create_task(vm.load_more_prepared_statements())
    await started.wait()

    assert vm.is_loading_more_prepared_statements
    assert not vm.is_loading_more_named_queries
    assert client.prepared_list_calls[-1] == ("analysts", None)

    release.set()
    await prepared_loading

    assert not vm.is_loading_more_prepared_statements
    assert client.prepared_list_calls[-1] == ("analysts", "prepared-next")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "loader_name", "state_attribute", "text_attribute", "request_attribute"),
    [
        (
            "named",
            "load_more_named_queries",
            "named_state",
            "named_error_text",
            "list_named_queries_page",
        ),
        (
            "prepared",
            "load_more_prepared_statements",
            "prepared_state",
            "prepared_error_text",
            "list_prepared_statements_page",
        ),
    ],
)
async def test_saved_load_more_retry_clears_stale_error(
    kind: str,
    loader_name: str,
    state_attribute: str,
    text_attribute: str,
    request_attribute: str,
) -> None:
    client = _seeded_client()
    if kind == "prepared":
        client.prepared_pages[("analysts", None)] = (
            client.prepared_pages[("analysts", None)][0],
            "prepared-next",
        )
        client.prepared_pages[("analysts", "prepared-next")] = ([], None)
    vm = make_saved_vm(client)
    await vm.setup()
    original = getattr(client, request_attribute)
    failed = True

    async def fail_once(
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> object:
        nonlocal failed
        if start_token is not None and failed:
            failed = False
            raise ProviderError("temporary failure")
        return await original(workgroup, start_token=start_token)

    setattr(client, request_attribute, fail_once)

    await getattr(vm, loader_name)()

    assert getattr(vm, state_attribute) is PaneState.ERROR
    assert (
        getattr(vm, text_attribute) == "Athena saved query request failed"
        if kind == "named"
        else "Athena prepared statement request failed"
    )

    await getattr(vm, loader_name)()

    assert getattr(vm, state_attribute) is PaneState.IDLE
    assert getattr(vm, text_attribute) is None


@pytest.mark.asyncio
async def test_named_query_list_exposes_sql_free_summaries_before_selection() -> None:
    from aws_tui.domain.query import NamedQuerySummary

    client = _seeded_client()
    vm = make_saved_vm(client)

    await vm.setup()

    summary = vm.named_queries[0]
    assert isinstance(summary, NamedQuerySummary)
    assert not hasattr(summary, "query_string")
    assert vm.selected_named_query is None
    assert vm.selected_sql() is None
    assert client.named_detail_calls == [("named-1",)]


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


@pytest.mark.asyncio
async def test_workgroup_replacement_cancels_and_retains_prepared_detail_until_drained() -> None:
    client = _seeded_client()
    client.block_prepared_detail = True
    client.ignore_prepared_detail_cancellation = True
    vm = make_saved_vm(client)
    await vm.setup()
    selection = asyncio.create_task(vm.select_prepared_statement("prepared-1"))
    await client.prepared_detail_started.wait()

    vm.replace_workgroup("engineering")
    try:
        await asyncio.wait_for(client.prepared_detail_cancelled.wait(), timeout=1)

        assert client.prepared_detail_cancelled.is_set()
        assert not selection.done()
        assert client.active_prepared_details == {("prepared-1", "analysts")}
        assert vm.selected_prepared_statement is None
    finally:
        client.release_prepared_detail.set()
        await selection

    assert client.active_prepared_details == set()
    assert vm._detail_tasks == set()  # type: ignore[attr-defined]
    assert vm.selected_query_id is None
    assert vm.selected_prepared_statement is None


@pytest.mark.asyncio
async def test_saved_shutdown_cancels_and_drains_prepared_detail_request() -> None:
    client = _seeded_client()
    client.block_prepared_detail = True
    client.ignore_prepared_detail_cancellation = True
    vm = make_saved_vm(client)
    await vm.setup()
    selection = asyncio.create_task(vm.select_prepared_statement("prepared-1"))
    await client.prepared_detail_started.wait()

    shutdown = asyncio.create_task(vm.shutdown())
    try:
        await asyncio.wait_for(client.prepared_detail_cancelled.wait(), timeout=1)

        assert client.prepared_detail_cancelled.is_set()
        assert not shutdown.done()
        assert client.active_prepared_details == {("prepared-1", "analysts")}
    finally:
        client.release_prepared_detail.set()
        await asyncio.gather(shutdown, selection)

    assert client.active_prepared_details == set()
    assert vm._detail_tasks == set()  # type: ignore[attr-defined]
    assert vm.selected_prepared_statement is None
    assert vm.detail_state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_saved_snapshot_relational_invariants_reject_atomically_without_calls() -> None:
    client = _seeded_client()
    vm = make_saved_vm(client)
    await vm.setup()
    snapshot = vm.export_snapshot()
    before_calls = (
        tuple(client.named_list_calls),
        tuple(client.named_detail_calls),
        tuple(client.prepared_list_calls),
        tuple(client.prepared_detail_calls),
    )
    named_summary = snapshot.named_queries[0]
    named_detail = snapshot.named_query_details[0]
    prepared_summary = snapshot.prepared_statements[0]
    empty = replace(
        snapshot,
        named_queries=(),
        named_query_details=(),
        prepared_statements=(),
        named_next_token=None,
        prepared_next_token=None,
        selected_kind=None,
        selected_query_id=None,
        selected_named_query=None,
        selected_prepared_statement=None,
        named_state=PaneState.EMPTY,
        prepared_state=PaneState.EMPTY,
        detail_state=PaneState.EMPTY,
        named_error_text=None,
        prepared_error_text=None,
        detail_error_text=None,
    )
    invalid = (
        replace(snapshot, named_next_token=""),
        replace(snapshot, prepared_next_token=""),
        replace(
            snapshot,
            named_queries=(named_summary, named_summary),
            named_query_details=(named_detail, named_detail),
        ),
        replace(snapshot, named_query_details=()),
        replace(
            snapshot,
            prepared_statements=(prepared_summary, prepared_summary),
        ),
        replace(snapshot, named_state=PaneState.EMPTY),
        replace(snapshot, prepared_state=PaneState.EMPTY),
        replace(
            snapshot,
            selected_kind=SavedQueryKind.NAMED,
            selected_query_id="missing",
            selected_named_query=named_detail,
            detail_state=PaneState.IDLE,
        ),
        replace(snapshot, named_state=PaneState.ERROR, named_error_text=None),
        replace(snapshot, named_error_text="unexpected"),
        replace(
            empty,
            named_next_token="next",
            named_state=PaneState.ERROR,
            named_error_text="request failed",
        ),
        replace(
            empty,
            prepared_next_token="next",
            prepared_state=PaneState.FORBIDDEN,
            prepared_error_text="request failed",
        ),
    )

    for candidate in invalid:
        with pytest.raises(ValueError, match=r"^Athena saved query snapshot is invalid$"):
            vm.restore_snapshot(candidate)
        assert vm.export_snapshot() == snapshot

    assert (
        tuple(client.named_list_calls),
        tuple(client.named_detail_calls),
        tuple(client.prepared_list_calls),
        tuple(client.prepared_detail_calls),
    ) == before_calls

    retryable = replace(
        snapshot,
        named_state=PaneState.ERROR,
        named_error_text="request failed",
    )
    vm.restore_snapshot(retryable)
    assert vm.export_snapshot() == retryable
    assert vm.has_more_named_queries
