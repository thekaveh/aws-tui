"""Unit tests for the Iceberg preview view model."""

from __future__ import annotations

import threading

import anyio
import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages import ConstructionStatusChangedMessage
from vmx.messages.protocols import Message

from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbResult, InMemoryDuckDb
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.iceberg_preview_vm import IcebergPreviewVM
from tests.helpers import WAIT_UNTIL_TIMEOUT_SECONDS, wait_until


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


class _GatedPort:
    """A port whose query blocks in the worker thread until released.

    `InMemoryDuckDb` is slotted, so its `query` cannot be patched, and a test
    for an in-flight scan needs the scan to actually still be in flight.
    """

    def __init__(self, *, rows: tuple[tuple[str | None, ...], ...]) -> None:
        self._rows = rows
        self.entered = threading.Event()
        self._gate = threading.Event()

    def release(self) -> None:
        self._gate.set()

    def query(self, sql: str, *, profile: str, region: str) -> DuckDbResult:
        self.entered.set()
        assert self._gate.wait(WAIT_UNTIL_TIMEOUT_SECONDS), "the gate was never released"
        return DuckDbResult(outcome=DuckDbOutcome.OK, columns=("a",), rows=self._rows)

    def interrupt(self) -> None:
        self._gate.set()


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
async def test_a_cancelled_first_query_leaves_the_pane_empty_without_an_error() -> None:
    """Cancelling is not a failure, and an empty pane must not claim to be loaded.

    This previously asserted IDLE, which is how the pane came to report "0 rows"
    for a table it had never finished reading.
    """
    vm = _build(InMemoryDuckDb(outcome=DuckDbOutcome.CANCELLED))
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.load()

    assert vm.state is PaneState.EMPTY
    assert vm.error_text is None
    assert vm.rows == ()


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
    of binding to `on_property_changed`. The inner `AsyncResourceVM` would
    dual-publish, so it is given a private hub -- this test is what holds that
    wiring in place.
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

    # Nothing at all, not even the resource's lifecycle: the shared hub has no
    # audience for this VM's internals.
    leaked = [msg for msg in seen if not isinstance(msg, ConstructionStatusChangedMessage)]
    assert leaked == [], f"VM state leaked to the shared hub: {leaked}"


@pytest.mark.asyncio
async def test_an_in_flight_scan_cannot_write_its_rows_into_a_rebound_pane() -> None:
    """The stale-write defect: table A's rows appearing under table B's name.

    `AsyncResourceVM` stamps each operation and discards a superseded one's
    value, so releasing A's scan after the re-bind must change nothing.
    """
    port = _GatedPort(rows=(("from-table-a",),))
    vm = _build(port)
    vm.bind("s3://bkt/table-a", profile="p", region="r")

    async with anyio.create_task_group() as tg:
        tg.start_soon(vm.load)
        # The scan is genuinely inside the engine call before we re-bind.
        await wait_until(port.entered.is_set, what="the scan for table A to start")
        vm.bind("s3://bkt/table-b", profile="p", region="r")
        port.release()

    assert vm.rows == (), f"table A's rows leaked into table B's pane: {vm.rows}"
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_cancelling_a_reload_keeps_the_rows_already_on_screen() -> None:
    """The wipe-on-cancel defect.

    `cancel_metadata_loads_and_drain_silently` runs on an ordinary database or
    table switch, so wiping here showed the user "0 rows" for a table they had
    just successfully read.
    """
    port = InMemoryDuckDb(columns=("a",), rows=(("1",), ("2",)))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="p", region="r")
    await vm.load()
    assert vm.rows == (("1",), ("2",))

    # A genuinely cancelled scan comes back with no columns and no rows -- that
    # empty result is what used to be written straight over the loaded page.
    port.outcome = DuckDbOutcome.CANCELLED
    port.columns = ()
    port.rows = ()
    await vm.load()

    assert vm.rows == (("1",), ("2",)), "a cancelled reload wiped the loaded rows"
    assert vm.columns == ("a",)
    assert vm.error_text is None
    assert vm.state is PaneState.IDLE


@pytest.mark.asyncio
async def test_a_failed_reload_keeps_the_rows_already_on_screen() -> None:
    """RETAIN_PREVIOUS: an error annotates the pane rather than emptying it."""
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="p", region="r")
    await vm.load()
    assert vm.rows == (("1",),)

    port.outcome = DuckDbOutcome.FORBIDDEN
    port.columns = ()
    port.rows = ()
    await vm.load()

    assert vm.state is PaneState.FORBIDDEN
    assert vm.error_text == "S3 access is forbidden for this table"
    assert vm.rows == (("1",),), "the error discarded rows the user could still read"


@pytest.mark.asyncio
async def test_ensure_loaded_runs_once_per_binding_then_no_ops() -> None:
    """The pane calls this on every refresh, so it must not re-query.

    It is also what stops the pane looping: a scan that legitimately rests on
    EMPTY must not be retried forever by the next refresh.
    """
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.ensure_loaded()
    await vm.ensure_loaded()
    await vm.ensure_loaded()

    assert len(port.queries) == 1
    assert vm.needs_load is False

    # A new binding is a new question, so it may load again.
    vm.bind("s3://bkt/other", profile="p", region="r")
    assert vm.needs_load is True
    await vm.ensure_loaded()
    assert len(port.queries) == 2


@pytest.mark.asyncio
async def test_a_cancelled_scan_that_rests_on_empty_is_not_retried_forever() -> None:
    """The loop `ensure_loaded` exists to prevent, stated as a test."""
    vm = _build(InMemoryDuckDb(outcome=DuckDbOutcome.CANCELLED))
    vm.bind("s3://bkt/t", profile="p", region="r")

    await vm.ensure_loaded()
    assert vm.state is PaneState.EMPTY

    # The pane would call this again on the refresh that EMPTY itself triggered.
    assert vm.needs_load is False


def test_a_subscriber_cannot_dispose_the_property_stream_for_the_others() -> None:
    """VMX-013: the observable is sealed, so one subscriber cannot break another.

    `on_property_changed` used to return the `ObserverSafeSubject` itself, which
    exposes `on_next` and `dispose` to anyone holding it.
    """
    vm = _build(InMemoryDuckDb())
    seen: list[str] = []
    vm.on_property_changed.subscribe(on_next=seen.append)
    stream = vm.on_property_changed

    # Neither lever the raw subject exposes survives the seal.
    assert not hasattr(stream, "on_next"), "a subscriber can inject property names"
    assert not hasattr(stream, "dispose"), "a subscriber can dispose the shared stream"
    vm.bind("s3://bkt/t", profile="p", region="r")

    assert seen, "the subscriber received no notifications at all"
