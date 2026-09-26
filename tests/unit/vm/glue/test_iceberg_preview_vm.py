"""Unit tests for the Iceberg preview view model."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages import ConstructionStatusChangedMessage
from vmx.messages.protocols import Message

from aws_tui.infra.duckdb import DuckDbOutcome, InMemoryDuckDb
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.iceberg_preview_vm import IcebergPreviewVM


def _build(port: InMemoryDuckDb) -> IcebergPreviewVM:
    hub: MessageHub[Message] = MessageHub()
    vm = IcebergPreviewVM(port=port, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def test_is_unavailable_without_a_location() -> None:
    vm = _build(InMemoryDuckDb())
    vm.bind(None, profile="analytics", region="us-east-1")

    assert vm.available is False


def test_is_unavailable_without_an_aws_profile() -> None:
    # s3-compatible connections carry no profile and are a non-goal.
    vm = _build(InMemoryDuckDb())
    vm.bind("s3://bkt/t", profile=None, region="us-east-1")

    assert vm.available is False


@pytest.mark.asyncio
async def test_loads_rows_and_reaches_idle() -> None:
    port = InMemoryDuckDb(columns=("a", "b"), rows=(("1", None),))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="analytics", region="us-east-1")

    await vm.load()

    assert vm.state is PaneState.IDLE
    assert vm.columns == ("a", "b")
    assert vm.rows == (("1", None),)
    assert port.queries[0][1:] == ("analytics", "us-east-1")


@pytest.mark.asyncio
async def test_pins_the_selected_snapshot_in_the_statement() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.load(snapshot_id=4201)

    assert "snapshot_from_id := 4201" in port.queries[0][0]
    assert vm.snapshot_id == 4201


@pytest.mark.asyncio
async def test_load_more_reruns_at_the_next_limit() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=tuple((str(n),) for n in range(100)))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="p", region="r")
    await vm.load()
    assert vm.limit == 100
    assert vm.has_more is True

    await vm.load_more()

    # A real second query, not a widened local window.
    assert len(port.queries) == 2
    assert "LIMIT 1000" in port.queries[1][0]
    assert vm.limit == 1000


@pytest.mark.asyncio
async def test_has_more_is_false_when_fewer_rows_than_the_limit_return() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.load()

    assert vm.has_more is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_state", "expected_text"),
    [
        (DuckDbOutcome.FORBIDDEN, PaneState.FORBIDDEN, "S3 access is forbidden for this table"),
        (DuckDbOutcome.NOT_FOUND, PaneState.ERROR, "table location not found"),
        (DuckDbOutcome.AUTH_REQUIRED, PaneState.AUTH_REQUIRED, "AWS authentication is required"),
        (DuckDbOutcome.NOT_ICEBERG, PaneState.ERROR, "not a readable Iceberg table"),
        (DuckDbOutcome.ENGINE_MISSING, PaneState.ERROR, "pip install aws-tui[duckdb]"),
        (DuckDbOutcome.FAILED, PaneState.ERROR, "local preview failed"),
    ],
)
async def test_maps_each_outcome_to_a_pane_state(
    outcome: DuckDbOutcome, expected_state: PaneState, expected_text: str
) -> None:
    vm = _build(InMemoryDuckDb(outcome=outcome))
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.load()

    assert vm.state is expected_state
    assert vm.error_text is not None
    assert expected_text in vm.error_text


@pytest.mark.asyncio
async def test_a_cancelled_query_returns_to_idle_without_an_error() -> None:
    vm = _build(InMemoryDuckDb(outcome=DuckDbOutcome.CANCELLED))
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.load()

    assert vm.state is PaneState.IDLE
    assert vm.error_text is None


@pytest.mark.asyncio
async def test_cancel_interrupts_the_engine() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.cancel()

    assert port.interrupts == 1


@pytest.mark.asyncio
async def test_loading_while_unbound_is_a_no_op_not_a_raise() -> None:
    """This runs in a Textual worker; an escaping exception is invisible."""
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind(None, profile="analytics", region="us-east-1")

    await vm.load()

    assert vm.state is PaneState.EMPTY
    assert port.queries == []


@pytest.mark.asyncio
async def test_publishes_property_changes_on_its_own_subject() -> None:
    # MVVM: the view binds to this, never to the shared hub.
    vm = _build(InMemoryDuckDb(columns=("a",), rows=(("1",),)))
    vm.bind("s3://bkt/t", profile="p", region="r")
    seen: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=seen.append)

    await vm.load()

    subscription.dispose()
    assert "state" in seen
    assert "rows" in seen


@pytest.mark.asyncio
async def test_no_state_change_is_published_through_the_shared_hub() -> None:
    """MVVM: this VM publishes only through its own subject.

    Sibling VMs in this package deliberately dual-publish to the shared hub;
    this one must not, so a view can never be tempted to filter the hub instead
    of binding to `on_property_changed`. Without this test the guarantee rests
    on the implementation happening to be written correctly, and a future edit
    reintroducing a hub publish would pass the whole suite.
    """
    hub: MessageHub[Message] = MessageHub()
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = IcebergPreviewVM(port=port, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    seen: list[object] = []
    hub.messages.subscribe(on_next=seen.append)

    vm.bind("s3://bkt/t", profile="analytics", region="us-east-1")
    await vm.load()
    await vm.load_more()
    await vm.cancel()
    vm.dispose()

    # Filter out infrastructure lifecycle messages from the inner ComponentVM.
    # Data state changes (state, rows, columns, etc.) must never be published.
    data_state_messages = [
        msg for msg in seen if not isinstance(msg, ConstructionStatusChangedMessage)
    ]
    assert data_state_messages == [], f"VM state leaked to the shared hub: {data_state_messages}"
