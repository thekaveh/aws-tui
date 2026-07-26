from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
)
from aws_tui.vm.athena.history_vm import AthenaHistoryVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_STATS = QueryStatistics(10, 2, 3, 1, 128, False)


def _detail(
    execution_id: str,
    workgroup: str,
    *,
    state_reason: str | None = None,
) -> QueryExecutionDetail:
    ref = QueryExecutionRef(execution_id, "analytics", "us-west-2", workgroup)
    return QueryExecutionDetail(
        summary=QueryExecutionSummary(
            ref,
            QueryState.SUCCEEDED,
            datetime(2026, 7, 25, tzinfo=UTC),
            datetime(2026, 7, 25, 0, 0, 1, tzinfo=UTC),
            "DML",
        ),
        state_reason=state_reason,
        context=QueryContext(
            "analytics",
            "us-west-2",
            workgroup,
            "AwsDataCatalog",
            "events",
        ),
        statistics=_STATS,
        output_location="s3://private-results/result.csv",
        engine_version="Athena engine version 3",
        error=None,
    )


class HistoryClient:
    def __init__(self) -> None:
        self.pages: dict[
            tuple[str, str | None],
            tuple[list[QueryExecutionRef], str | None],
        ] = {}
        self.details: dict[str, QueryExecutionDetail] = {}
        self.list_calls: list[tuple[str, str | None]] = []
        self.detail_calls: list[str] = []
        self.stop_calls: list[str] = []
        self.block_request: tuple[str, str | None] | None = None
        self.fetch_started = asyncio.Event()
        self.release_fetch = asyncio.Event()

    async def list_query_executions_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[QueryExecutionRef], str | None]:
        request = (workgroup, start_token)
        self.list_calls.append(request)
        if request == self.block_request:
            self.fetch_started.set()
            await self.release_fetch.wait()
        return self.pages[request]

    async def get_query_execution(self, execution_id: str) -> QueryExecutionDetail:
        self.detail_calls.append(execution_id)
        return self.details[execution_id]

    async def stop_query(self, execution_id: str) -> None:
        self.stop_calls.append(execution_id)


def _seeded_client() -> HistoryClient:
    client = HistoryClient()
    first = _detail("q-2", "analysts")
    second = _detail("q-1", "analysts")
    client.details = {
        "q-2": first,
        "q-1": second,
    }
    client.pages = {
        (
            "analysts",
            None,
        ): ([first.summary.ref], "next"),
        (
            "analysts",
            "next",
        ): ([second.summary.ref], None),
    }
    return client


def make_history_vm(client: HistoryClient, workgroup: str = "analysts") -> AthenaHistoryVM:
    hub: MessageHub[Message] = MessageHub()
    vm = AthenaHistoryVM(
        client=client,
        workgroup=workgroup,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


@pytest.mark.asyncio
async def test_history_hydrates_only_the_current_token_page() -> None:
    client = _seeded_client()
    vm = make_history_vm(client)

    await vm.setup()

    assert isinstance(vm._pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert tuple(row.ref.execution_id for row in vm.items) == ("q-2",)
    assert vm.has_more
    assert client.list_calls == [("analysts", None)]
    assert client.detail_calls == ["q-2"]

    await vm.load_more()

    assert tuple(row.ref.execution_id for row in vm.items) == ("q-2", "q-1")
    assert not vm.has_more
    assert client.list_calls == [("analysts", None), ("analysts", "next")]
    assert client.detail_calls == ["q-2", "q-1"]


@pytest.mark.asyncio
async def test_history_selection_reads_detail_without_granting_stop_authority() -> None:
    client = _seeded_client()
    vm = make_history_vm(client)
    await vm.setup()

    await vm.select_execution("q-2")

    assert vm.selected_execution_id == "q-2"
    assert vm.detail == client.details["q-2"]
    assert client.stop_calls == []


@pytest.mark.asyncio
async def test_replacing_workgroup_discards_a_late_old_page() -> None:
    client = _seeded_client()
    old = _detail("old-secret-id", "analysts")
    new = _detail("new-id", "engineering")
    client.details.update({"old-secret-id": old, "new-id": new})
    client.pages[("analysts", None)] = ([old.summary.ref], None)
    client.pages[("engineering", None)] = ([new.summary.ref], None)
    client.block_request = ("analysts", None)
    vm = make_history_vm(client)

    old_setup = asyncio.create_task(vm.setup())
    await client.fetch_started.wait()
    vm.replace_workgroup("engineering")
    await vm.setup()
    client.release_fetch.set()
    await old_setup

    assert vm.workgroup == "engineering"
    assert vm.items == (new.summary,)
    assert vm.selected_execution_id is None
    assert vm.detail is None


@pytest.mark.asyncio
async def test_history_shutdown_drains_blocked_page_and_publishes_nothing_late() -> None:
    client = _seeded_client()
    client.block_request = ("analysts", None)
    vm = make_history_vm(client)
    setup = asyncio.create_task(vm.setup())
    await client.fetch_started.wait()

    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    waited_for_page = not shutdown.done()
    client.release_fetch.set()
    await shutdown
    await setup

    assert waited_for_page
    assert vm.items == ()
    assert vm.state is PaneState.EMPTY
    assert not vm.load_more_command.can_execute()


@pytest.mark.asyncio
async def test_history_error_and_repr_do_not_expose_sensitive_detail_text() -> None:
    client = _seeded_client()
    secret = "HISTORY_SQL_SECRET"
    client.details["q-2"] = _detail("q-2", "analysts", state_reason=secret)
    vm = make_history_vm(client)

    await vm.setup()
    await vm.select_execution("q-2")

    assert secret not in repr(vm)
    assert vm.error_text is None
