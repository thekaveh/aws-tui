from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
)
from aws_tui.vm.athena._pager_compat import SnapshotTokenPager
from aws_tui.vm.athena.history_vm import AthenaHistoryVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.messages import OpenS3LocationRequest

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
        self.block_detail_ids: set[str] = set()
        self.fail_detail_ids: set[str] = set()
        self.fail_detail_after: str | None = None
        self.ignore_detail_cancellation = False
        self.detail_started: dict[str, asyncio.Event] = {}
        self.detail_cancelled: set[str] = set()
        self.active_detail_ids: set[str] = set()
        self.release_details = asyncio.Event()

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
        self.active_detail_ids.add(execution_id)
        self.detail_started.setdefault(execution_id, asyncio.Event()).set()
        try:
            if execution_id in self.fail_detail_ids:
                if self.fail_detail_after is not None:
                    await self.detail_started.setdefault(
                        self.fail_detail_after,
                        asyncio.Event(),
                    ).wait()
                raise RuntimeError("detail hydration failed")
            if execution_id in self.block_detail_ids:
                try:
                    await self.release_details.wait()
                except asyncio.CancelledError:
                    self.detail_cancelled.add(execution_id)
                    if not self.ignore_detail_cancellation:
                        raise
                    await self.release_details.wait()
            return self.details[execution_id]
        finally:
            self.active_detail_ids.discard(execution_id)

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
        context=QueryContext(
            "analytics",
            "us-west-2",
            workgroup,
            "AwsDataCatalog",
            "events",
        ),
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

    assert isinstance(vm._pager, SnapshotTokenPager)  # type: ignore[attr-defined]
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
async def test_history_load_more_exposes_busy_state_for_the_continuation_page() -> None:
    client = _seeded_client()
    vm = make_history_vm(client)
    await vm.setup()
    client.block_request = ("analysts", "next")

    loading = asyncio.create_task(vm.load_more())
    await client.fetch_started.wait()

    assert vm.is_loading_more
    assert client.list_calls[-1] == ("analysts", "next")

    client.release_fetch.set()
    await loading

    assert not vm.is_loading_more


def test_retired_history_worker_cannot_clear_current_busy_state() -> None:
    vm = make_history_vm(_seeded_client())
    old_worker = vm._worker  # type: ignore[attr-defined]
    vm._begin_loading_more(old_worker)  # type: ignore[attr-defined]

    vm.replace_context(
        QueryContext(
            "analytics",
            "us-west-2",
            "engineering",
            "AwsDataCatalog",
            "events",
        )
    )
    current_worker = vm._worker  # type: ignore[attr-defined]
    vm._begin_loading_more(current_worker)  # type: ignore[attr-defined]
    vm._finish_loading_more(old_worker)  # type: ignore[attr-defined]

    assert vm.is_loading_more
    vm._finish_loading_more(current_worker)  # type: ignore[attr-defined]
    assert not vm.is_loading_more
    vm.dispose()


@pytest.mark.asyncio
async def test_history_load_more_retry_clears_stale_error() -> None:
    client = _seeded_client()
    vm = make_history_vm(client)
    await vm.setup()
    original = client.list_query_executions_page
    failed = True

    async def fail_once(
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[QueryExecutionRef], str | None]:
        nonlocal failed
        if start_token == "next" and failed:
            failed = False
            raise ProviderError("temporary failure")
        return await original(workgroup, start_token=start_token)

    client.list_query_executions_page = fail_once  # type: ignore[method-assign]

    await vm.load_more()

    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena history request failed"

    await vm.load_more()

    assert vm.state is PaneState.IDLE
    assert vm.error_text is None


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
    vm.replace_context(
        QueryContext(
            "analytics",
            "us-west-2",
            "engineering",
            "AwsDataCatalog",
            "events",
        )
    )
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
async def test_history_detail_failure_cancels_and_drains_every_sibling() -> None:
    client = _seeded_client()
    failed = _detail("q-failed", "analysts")
    first_sibling = _detail("q-sibling-1", "analysts")
    second_sibling = _detail("q-sibling-2", "analysts")
    client.details.update(
        {
            "q-failed": failed,
            "q-sibling-1": first_sibling,
            "q-sibling-2": second_sibling,
        }
    )
    client.pages[("analysts", None)] = (
        [
            failed.summary.ref,
            first_sibling.summary.ref,
            second_sibling.summary.ref,
        ],
        None,
    )
    client.fail_detail_ids = {"q-failed"}
    client.fail_detail_after = "q-sibling-2"
    client.block_detail_ids = {"q-sibling-1", "q-sibling-2"}
    client.ignore_detail_cancellation = True
    vm = make_history_vm(client)

    setup = asyncio.create_task(vm.setup())
    try:
        await client.detail_started.setdefault(
            "q-sibling-2",
            asyncio.Event(),
        ).wait()
        for _ in range(5):
            await asyncio.sleep(0)

        assert client.detail_cancelled == {"q-sibling-1", "q-sibling-2"}
        assert not setup.done()
        assert client.active_detail_ids == {"q-sibling-1", "q-sibling-2"}
    finally:
        client.release_details.set()
        await setup

    assert client.active_detail_ids == set()
    assert vm.items == ()
    assert vm.state is PaneState.ERROR


@pytest.mark.asyncio
async def test_history_shutdown_cancels_and_drains_all_detail_siblings() -> None:
    client = _seeded_client()
    first = _detail("q-sibling-1", "analysts")
    second = _detail("q-sibling-2", "analysts")
    client.details = {
        "q-sibling-1": first,
        "q-sibling-2": second,
    }
    client.pages[("analysts", None)] = (
        [first.summary.ref, second.summary.ref],
        None,
    )
    client.block_detail_ids = {"q-sibling-1", "q-sibling-2"}
    client.ignore_detail_cancellation = True
    vm = make_history_vm(client)
    setup = asyncio.create_task(vm.setup())
    await asyncio.gather(
        *(
            client.detail_started.setdefault(execution_id, asyncio.Event()).wait()
            for execution_id in client.block_detail_ids
        )
    )

    shutdown = asyncio.create_task(vm.shutdown())
    try:
        for _ in range(5):
            await asyncio.sleep(0)

        assert client.detail_cancelled == client.block_detail_ids
        assert not shutdown.done()
        assert client.active_detail_ids == client.block_detail_ids
    finally:
        client.release_details.set()
        await asyncio.gather(shutdown, setup)

    assert client.active_detail_ids == set()
    assert vm.items == ()
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_repeated_setup_cancellation_keeps_detail_tasks_tracked_for_shutdown() -> None:
    client = _seeded_client()
    client.block_detail_ids = set(client.details)
    client.pages[("analysts", None)] = (
        [detail.summary.ref for detail in client.details.values()],
        None,
    )
    client.ignore_detail_cancellation = True
    vm = make_history_vm(client)
    setup = asyncio.create_task(vm.setup())
    await asyncio.gather(
        *(
            client.detail_started.setdefault(execution_id, asyncio.Event()).wait()
            for execution_id in client.block_detail_ids
        )
    )

    setup.cancel()
    for _ in range(10):
        await asyncio.sleep(0)
        if client.detail_cancelled == client.block_detail_ids:
            break
    setup.cancel()
    shutdown = asyncio.create_task(vm.shutdown())
    try:
        await asyncio.sleep(0)

        assert not shutdown.done()
        assert client.active_detail_ids == client.block_detail_ids
        assert any(
            not task.done()
            for worker in vm._workers  # type: ignore[attr-defined]
            for task in worker.tasks
        )
    finally:
        client.release_details.set()
        await asyncio.gather(setup, return_exceptions=True)
        await shutdown

    assert client.active_detail_ids == set()
    assert vm.items == ()


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


@pytest.mark.asyncio
async def test_history_rejects_coherent_detail_owned_by_another_profile() -> None:
    client = _seeded_client()
    detail = client.details["q-2"]
    foreign_context = QueryContext(
        "foreign-profile",
        "eu-west-1",
        "analysts",
        "ForeignCatalog",
        "foreign_database",
    )
    foreign_ref = replace(
        detail.summary.ref,
        connection_name=foreign_context.connection_name,
        region=foreign_context.region,
    )
    client.details["q-2"] = replace(
        detail,
        summary=replace(detail.summary, ref=foreign_ref),
        context=foreign_context,
        output_location="s3://foreign-results/PROFILE_SECRET.csv",
    )
    client.pages[("analysts", None)] = ([foreign_ref], None)
    vm = make_history_vm(client)
    published: list[object] = []
    subscription = vm._hub.messages.subscribe(published.append)  # type: ignore[attr-defined]
    try:
        await vm.setup()
        await vm.select_execution("q-2")

        assert vm.open_s3_location() is False
        assert not any(isinstance(message, OpenS3LocationRequest) for message in published)
        assert "PROFILE_SECRET" not in repr(vm)
    finally:
        subscription.dispose()


@pytest.mark.asyncio
async def test_history_snapshot_relational_invariants_reject_atomically_without_calls() -> None:
    client = _seeded_client()
    vm = make_history_vm(client)
    await vm.setup()
    await vm.select_execution("q-2")
    snapshot = vm.export_snapshot()
    before_calls = (tuple(client.list_calls), tuple(client.detail_calls))
    duplicate_summary = snapshot.items[0]
    duplicate_detail = snapshot.details[0]
    empty = replace(
        snapshot,
        items=(),
        details=(),
        next_token=None,
        selected_execution_id=None,
        state=PaneState.EMPTY,
        error_text=None,
    )
    invalid = (
        replace(snapshot, next_token=""),
        replace(
            snapshot,
            items=(duplicate_summary, duplicate_summary),
            details=(duplicate_detail, duplicate_detail),
        ),
        replace(snapshot, details=()),
        replace(snapshot, state=PaneState.EMPTY),
        replace(empty, next_token="next"),
        replace(
            empty,
            next_token="next",
            state=PaneState.ERROR,
            error_text="request failed",
        ),
        replace(empty, state=PaneState.IDLE),
        replace(snapshot, state=PaneState.ERROR, error_text=None),
        replace(snapshot, error_text="unexpected"),
    )

    for candidate in invalid:
        with pytest.raises(ValueError, match=r"^Athena history snapshot is invalid$"):
            vm.restore_snapshot(candidate)
        assert vm.export_snapshot() == snapshot

    assert (tuple(client.list_calls), tuple(client.detail_calls)) == before_calls

    retryable = replace(
        snapshot,
        state=PaneState.ERROR,
        error_text="request failed",
    )
    vm.restore_snapshot(retryable)
    assert vm.export_snapshot() == retryable
    assert vm.has_more
