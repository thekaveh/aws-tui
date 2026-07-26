from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import QueryContext, ResultColumn, ResultPage
from aws_tui.vm.athena.results_vm import AthenaResultsSnapshot, AthenaResultsVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_ID = ResultColumn("id", "varchar", "NULLABLE")
_VALUE = ResultColumn("value", "varchar", "NULLABLE")
_CONTEXT = QueryContext(
    "dev",
    "us-east-1",
    "analytics",
    "AwsDataCatalog",
    "events",
)


class ResultClient:
    def __init__(
        self,
        pages: dict[tuple[str, str | None], ResultPage],
    ) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str | None]] = []
        self.failures: dict[tuple[str, str | None], Exception] = {}
        self.blocked_execution: str | None = None
        self.blocked_request: tuple[str, str | None] | None = None
        self.fetch_started = asyncio.Event()
        self.release_fetch = asyncio.Event()
        self.ignore_cancellation = False

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        self.calls.append((execution_id, start_token))
        if (
            execution_id == self.blocked_execution
            or (execution_id, start_token) == self.blocked_request
        ):
            self.fetch_started.set()
            try:
                await self.release_fetch.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancellation:
                    raise
                await self.release_fetch.wait()
        failure = self.failures.get((execution_id, start_token))
        if failure is not None:
            raise failure
        return self.pages[(execution_id, start_token)]


def make_results_vm(client: ResultClient) -> AthenaResultsVM:
    hub: MessageHub[Message] = MessageHub()
    vm = AthenaResultsVM(
        client=client,
        context=_CONTEXT,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


@pytest.mark.asyncio
async def test_results_use_token_paging_without_eager_materialization() -> None:
    client = ResultClient(
        {
            ("q-1", None): ResultPage((_ID,), (("one",),), "next"),
            ("q-1", "next"): ResultPage((_ID,), (("two",),), None),
        }
    )
    vm = make_results_vm(client)

    await vm.load("q-1")

    assert isinstance(vm._pager, TokenPagedComposition)  # type: ignore[attr-defined]
    assert vm.columns == (_ID,)
    assert vm.rows == (("one",),)
    assert vm.has_more
    assert client.calls == [("q-1", None)]

    await vm.load_more()

    assert vm.rows == (("one",), ("two",))
    assert not vm.has_more
    assert client.calls == [("q-1", None), ("q-1", "next")]


@pytest.mark.asyncio
async def test_results_snapshot_restores_loaded_page_without_provider_fetch() -> None:
    source_client = ResultClient(
        {
            ("RESULT_EXECUTION_SECRET", None): ResultPage(
                (_ID,),
                (("RESULT_ROW_SECRET",),),
                "RESULT_TOKEN_SECRET",
            ),
        }
    )
    source = make_results_vm(source_client)
    await source.load("RESULT_EXECUTION_SECRET")
    snapshot = source.export_snapshot()

    destination_client = ResultClient({})
    destination = make_results_vm(destination_client)
    await destination.restore_snapshot(snapshot)

    assert destination.execution_id == "RESULT_EXECUTION_SECRET"
    assert destination.columns == (_ID,)
    assert destination.rows == (("RESULT_ROW_SECRET",),)
    assert destination.has_more
    assert destination.state is PaneState.IDLE
    assert destination.error_text is None
    assert not destination.is_loading_more
    assert destination_client.calls == []
    rendered = repr(snapshot)
    assert "RESULT_EXECUTION_SECRET" not in rendered
    assert "RESULT_ROW_SECRET" not in rendered
    assert "RESULT_TOKEN_SECRET" not in rendered


@pytest.mark.asyncio
async def test_results_snapshot_preserves_visible_loading_and_error_state() -> None:
    snapshot = AthenaResultsSnapshot(
        execution_id="RESULT_EXECUTION_SECRET",
        columns=(_VALUE,),
        rows=(("RESULT_ROW_SECRET",),),
        next_token="RESULT_TOKEN_SECRET",
        state=PaneState.ERROR,
        error_text="RESULT_ERROR_SECRET",
        is_loading_more=True,
    )
    destination_client = ResultClient({})
    destination = make_results_vm(destination_client)

    await destination.restore_snapshot(snapshot)

    assert destination.state is PaneState.ERROR
    assert destination.error_text == "RESULT_ERROR_SECRET"
    assert destination.is_loading_more
    assert destination_client.calls == []
    rendered = repr(snapshot)
    for marker in (
        "RESULT_EXECUTION_SECRET",
        "RESULT_ROW_SECRET",
        "RESULT_TOKEN_SECRET",
        "RESULT_ERROR_SECRET",
    ):
        assert marker not in rendered


@pytest.mark.asyncio
async def test_results_load_more_exposes_busy_state_for_the_continuation_page() -> None:
    client = ResultClient(
        {
            ("q-1", None): ResultPage((_ID,), (("one",),), "next"),
            ("q-1", "next"): ResultPage((_ID,), (("two",),), None),
        }
    )
    vm = make_results_vm(client)
    await vm.load("q-1")
    client.blocked_request = ("q-1", "next")

    loading = asyncio.create_task(vm.load_more())
    await client.fetch_started.wait()

    assert vm.is_loading_more
    assert client.calls[-1] == ("q-1", "next")

    client.release_fetch.set()
    await loading

    assert not vm.is_loading_more


@pytest.mark.asyncio
async def test_results_load_more_retry_clears_stale_error() -> None:
    client = ResultClient(
        {
            ("q-1", None): ResultPage((_ID,), (("one",),), "next"),
            ("q-1", "next"): ResultPage((_ID,), (("two",),), None),
        }
    )
    vm = make_results_vm(client)
    await vm.load("q-1")
    client.failures[("q-1", "next")] = ProviderError("temporary failure")

    await vm.load_more()

    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena results request failed"

    del client.failures[("q-1", "next")]
    await vm.load_more()

    assert vm.state is PaneState.IDLE
    assert vm.error_text is None


@pytest.mark.asyncio
async def test_results_retry_keeps_error_until_a_continuation_succeeds() -> None:
    client = ResultClient(
        {
            ("q-1", None): ResultPage((_ID,), (("one",),), "next"),
            ("q-1", "next"): ResultPage((_ID,), (("two",),), None),
        }
    )
    vm = make_results_vm(client)
    await vm.load("q-1")
    client.failures[("q-1", "next")] = ProviderError("temporary failure")

    await vm.load_more()

    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena results request failed"

    client.blocked_request = ("q-1", "next")
    client.fetch_started.clear()
    retry = asyncio.create_task(vm.load_more())
    await client.fetch_started.wait()

    assert vm.is_loading_more
    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena results request failed"

    client.release_fetch.set()
    await retry

    assert not vm.is_loading_more
    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena results request failed"

    del client.failures[("q-1", "next")]
    client.blocked_request = ("q-1", "next")
    client.fetch_started.clear()
    client.release_fetch.clear()
    retry = asyncio.create_task(vm.load_more())
    await client.fetch_started.wait()

    assert vm.is_loading_more
    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena results request failed"

    client.release_fetch.set()
    await retry

    assert not vm.is_loading_more
    assert vm.state is PaneState.IDLE
    assert vm.error_text is None


@pytest.mark.asyncio
async def test_results_preserve_null_empty_and_literal_null_values() -> None:
    client = ResultClient(
        {
            ("q-1", None): ResultPage(
                (_ID, _VALUE),
                ((None, ""), ("NULL", "false")),
                None,
            )
        }
    )
    vm = make_results_vm(client)

    await vm.load("q-1")

    assert vm.rows == ((None, ""), ("NULL", "false"))
    null_cell, empty_cell = vm.rendered_rows[0]
    literal_null, boolean_text = vm.rendered_rows[1]
    assert (null_cell.text, null_cell.is_null) == ("NULL", True)
    assert (empty_cell.text, empty_cell.is_null) == ("", False)
    assert (literal_null.text, literal_null.is_null) == ("NULL", False)
    assert (boolean_text.text, boolean_text.is_null) == ("false", False)


@pytest.mark.asyncio
async def test_results_reject_column_changes_between_pages_without_losing_rows() -> None:
    changed = ResultColumn("different", "varchar", "NULLABLE")
    client = ResultClient(
        {
            ("q-1", None): ResultPage((_ID,), (("one",),), "next"),
            ("q-1", "next"): ResultPage((changed,), (("two",),), None),
        }
    )
    vm = make_results_vm(client)
    await vm.load("q-1")

    await vm.load_more()

    assert vm.rows == (("one",),)
    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena returned inconsistent result columns"


@pytest.mark.asyncio
async def test_result_load_replacement_discards_stale_completion() -> None:
    client = ResultClient(
        {
            ("q-old", None): ResultPage((_ID,), (("old-secret",),), None),
            ("q-new", None): ResultPage((_VALUE,), (("new",),), None),
        }
    )
    client.blocked_execution = "q-old"
    client.ignore_cancellation = True
    vm = make_results_vm(client)

    old_load = asyncio.create_task(vm.load("q-old"))
    await client.fetch_started.wait()
    await vm.load("q-new")
    client.release_fetch.set()
    await old_load

    assert vm.execution_id == "q-new"
    assert vm.columns == (_VALUE,)
    assert vm.rows == (("new",),)


@pytest.mark.asyncio
async def test_replacement_load_more_uses_new_generation_while_old_page_is_blocked() -> None:
    client = ResultClient(
        {
            ("q-old", None): ResultPage((_ID,), (("old-first",),), "old-next"),
            ("q-old", "old-next"): ResultPage((_ID,), (("old-late",),), None),
            ("q-new", None): ResultPage((_VALUE,), (("new-first",),), "new-next"),
            ("q-new", "new-next"): ResultPage(
                (_VALUE,),
                (("new-second",),),
                None,
            ),
        }
    )
    vm = make_results_vm(client)
    await vm.load("q-old")
    old_command = vm.load_more_command
    client.blocked_request = ("q-old", "old-next")
    client.ignore_cancellation = True
    old_load_more = asyncio.create_task(vm.load_more())
    await client.fetch_started.wait()

    await vm.load("q-new")
    new_command = vm.load_more_command
    await vm.load_more()
    client.release_fetch.set()
    await old_load_more

    assert new_command is not old_command
    assert vm.execution_id == "q-new"
    assert vm.columns == (_VALUE,)
    assert vm.rows == (("new-first",), ("new-second",))
    assert client.calls == [
        ("q-old", None),
        ("q-old", "old-next"),
        ("q-new", None),
        ("q-new", "new-next"),
    ]


@pytest.mark.asyncio
async def test_shutdown_drains_retired_generation_that_ignores_cancellation() -> None:
    client = ResultClient(
        {
            ("q-old", None): ResultPage((_ID,), (("old-first",),), "old-next"),
            ("q-old", "old-next"): ResultPage((_ID,), (("old-late",),), None),
            ("q-new", None): ResultPage((_VALUE,), (("new-first",),), None),
        }
    )
    vm = make_results_vm(client)
    await vm.load("q-old")
    client.blocked_request = ("q-old", "old-next")
    client.ignore_cancellation = True
    old_load_more = asyncio.create_task(vm.load_more())
    await client.fetch_started.wait()
    await vm.load("q-new")

    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    shutdown_waited_for_old_worker = not shutdown.done()
    client.release_fetch.set()
    await shutdown
    await old_load_more

    assert shutdown_waited_for_old_worker
    assert vm.execution_id is None
    assert vm.rows == ()
    assert not vm.load_more_command.can_execute()


@pytest.mark.asyncio
async def test_result_failure_is_pane_local_and_does_not_expose_values() -> None:
    client = ResultClient(
        {
            ("q-1", None): ResultPage((_ID,), (("ROW_SECRET",),), "next"),
        }
    )
    client.failures[("q-1", "next")] = ProviderError("result ROW_SECRET could not be loaded")
    vm = make_results_vm(client)
    await vm.load("q-1")

    await vm.load_more()

    assert vm.rows == (("ROW_SECRET",),)
    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena results request failed"
    assert "ROW_SECRET" not in repr(vm)
    assert "ROW_SECRET" not in repr(vm.rendered_rows[0][0])


@pytest.mark.asyncio
async def test_dispose_invalidates_blocked_result_load_and_all_commands() -> None:
    client = ResultClient(
        {
            ("q-1", None): ResultPage((_ID,), (("late",),), None),
        }
    )
    client.blocked_execution = "q-1"
    client.ignore_cancellation = True
    vm = make_results_vm(client)
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(notifications.append)

    load = asyncio.create_task(vm.load("q-1"))
    await client.fetch_started.wait()
    notifications.clear()
    pager = vm._pager  # type: ignore[attr-defined]
    vm.dispose()
    client.release_fetch.set()
    await load

    assert notifications == []
    assert pager._disposed  # type: ignore[attr-defined]
    assert pager.load_more_command._disposed  # type: ignore[attr-defined]
    assert pager.refresh_command._disposed  # type: ignore[attr-defined]
    assert vm.load_more_command._disposed  # type: ignore[attr-defined]
    subscription.dispose()


def test_clear_replaces_pager_and_erases_execution_scoped_state() -> None:
    client = ResultClient({})
    vm = make_results_vm(client)
    old_pager = vm._pager  # type: ignore[attr-defined]

    vm.clear()

    assert vm.execution_id is None
    assert vm.columns == ()
    assert vm.rows == ()
    assert vm.state is PaneState.EMPTY
    assert old_pager._disposed  # type: ignore[attr-defined]


def _page(
    columns: Sequence[ResultColumn],
    rows: Sequence[tuple[str | None, ...]],
    next_token: str | None = None,
) -> ResultPage:
    return ResultPage(tuple(columns), tuple(rows), next_token)
