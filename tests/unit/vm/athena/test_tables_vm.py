from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from vmx import MessageHub

from aws_tui.domain.data_catalog import TableRef, TableSummary
from aws_tui.domain.filesystem import AuthRequiredError, PermissionDeniedError, ProviderError
from aws_tui.domain.query import QueryContext
from aws_tui.vm.athena.tables_vm import AthenaTablesVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_diagnostics import capture_service_diagnostics

CONTEXT = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "events")


def table(name="events", context=CONTEXT):
    return TableSummary(
        TableRef(context.catalog, context.database, name, context.connection_name, context.region),
        None,
        None,
        "EXTERNAL_TABLE",
        None,
        None,
    )


class TablesClient:
    def __init__(self):
        self.pages = {}
        self.calls = []
        self.error = None
        self.block = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.ignore_cancel = False

    async def list_tables_page(self, catalog, database, *, workgroup, start_token=None):
        self.calls.append((catalog, database, workgroup, start_token))
        if (database, start_token) == self.block:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.ignore_cancel:
                    raise
                await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.pages.get((database, start_token), ([], None))


async def test_refresh_and_load_more_preserve_literal_names_and_request_context():
    client = TablesClient()
    first, second = table('[red]a"b[/red]'), table("other")
    client.pages = {("events", None): ([first], "next"), ("events", "next"): ([second], None)}
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    names = []
    vm.on_property_changed.subscribe(names.append)
    assert vm.context == CONTEXT
    assert vm.items == ()
    assert vm.state is PaneState.EMPTY
    await vm.refresh()
    assert vm.items == (first,)
    assert vm.state is PaneState.IDLE
    assert vm.has_more
    await vm.load_more()
    assert vm.items == (first, second)
    assert not vm.has_more
    assert not vm.is_loading_more
    assert vm.error_text is None
    assert client.calls == [
        (CONTEXT.catalog, CONTEXT.database, CONTEXT.workgroup, None),
        (CONTEXT.catalog, CONTEXT.database, CONTEXT.workgroup, "next"),
    ]
    assert set(names) <= {
        "context",
        "items",
        "state",
        "error_text",
        "has_more",
        "limit_reached",
        "is_loading_more",
    }
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize("field", list(CONTEXT.__dataclass_fields__))
async def test_incomplete_context_makes_no_requests(field):
    client = TablesClient()
    vm = AthenaTablesVM(client=client, context=replace(CONTEXT, **{field: ""}), hub=MessageHub())
    await vm.refresh()
    await vm.load_more()
    assert client.calls == []
    assert vm.items == ()
    assert vm.state is PaneState.EMPTY
    assert not vm.has_more
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize("field", ["connection_name", "region", "catalog_name", "database_name"])
async def test_mismatched_response_is_rejected_without_publishing_rows(field):
    client = TablesClient()
    malformed = replace(table(), ref=replace(table().ref, **{field: "wrong"}))
    client.pages[("events", None)] = ([table("valid"), malformed], None)
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    with capture_service_diagnostics() as diagnostics:
        await vm.refresh()
    assert vm.items == ()
    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena tables request failed"
    assert [(entry.service, entry.operation) for entry in diagnostics] == [
        ("athena", "list_tables")
    ]
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize(
    "ref",
    [None, "not-a-ref", replace(table().ref, table_name=""), replace(table().ref, table_name=42)],
)
async def test_malformed_table_reference_is_rejected(ref):
    client = TablesClient()
    client.pages[("events", None)] = ([replace(table(), ref=ref)], None)
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    with capture_service_diagnostics():
        await vm.refresh()
    assert vm.items == ()
    assert vm.state is PaneState.ERROR
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (AuthRequiredError("SECRET"), PaneState.AUTH_REQUIRED),
        (PermissionDeniedError("SECRET"), PaneState.FORBIDDEN),
        (ProviderError("SECRET"), PaneState.ERROR),
        (RuntimeError("SECRET"), PaneState.ERROR),
    ],
)
async def test_failures_map_to_honest_redacted_states(error, state):
    client = TablesClient()
    client.error = error
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    with capture_service_diagnostics() as diagnostics:
        await vm.refresh()
    assert vm.state is state
    assert "SECRET" not in vm.error_text
    assert bool(diagnostics) is isinstance(error, RuntimeError)
    await vm.shutdown()
    vm.dispose()


async def test_missing_legacy_method_is_an_error_with_diagnostic():
    vm = AthenaTablesVM(client=object(), context=CONTEXT, hub=MessageHub())
    with capture_service_diagnostics() as diagnostics:
        await vm.refresh()
    assert vm.state is PaneState.ERROR
    assert vm.error_text == "Athena tables request failed"
    assert diagnostics[0].error_type == "AttributeError"
    await vm.shutdown()
    vm.dispose()


async def test_context_replacement_cancels_old_request_and_fences_late_reply():
    client = TablesClient()
    client.block = ("events", None)
    client.ignore_cancel = True
    next_context = replace(CONTEXT, database="new")
    client.pages = {
        ("events", None): ([table("old")], "old-token"),
        ("new", None): ([table("new", next_context)], None),
    }
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    old = asyncio.create_task(vm.refresh())
    await client.started.wait()
    assert vm.state is PaneState.LOADING
    vm.replace_context(next_context)
    await client.cancelled.wait()
    assert vm.context == next_context
    assert vm.items == ()
    assert vm.state is PaneState.EMPTY
    await vm.refresh()
    client.release.set()
    await old
    assert vm.items == (table("new", next_context),)
    assert vm.state is PaneState.IDLE
    assert not vm.has_more
    await vm.shutdown()
    vm.dispose()


@pytest.mark.parametrize("notify", [True, False])
async def test_context_replacement_can_restore_silently(notify):
    client = TablesClient()
    client.pages[("events", None)] = ([table()], "next")
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    names = []
    vm.on_property_changed.subscribe(names.append)
    await vm.refresh()
    names.clear()
    revision = vm.context_revision
    next_context = replace(CONTEXT, database="new")
    if notify:
        vm.replace_context(next_context)
    else:
        vm.replace_context(next_context, notify=False)
    assert vm.context == next_context
    assert vm.context_revision == revision + 1
    assert vm.items == ()
    assert vm.state is PaneState.EMPTY
    assert not vm.has_more
    assert len(client.calls) == 1
    assert names == (
        ["context", "items", "state", "error_text", "has_more", "limit_reached", "is_loading_more"]
        if notify
        else []
    )
    await vm.shutdown()
    vm.dispose()


async def test_load_more_is_single_request_and_replacement_resets_loading():
    client = TablesClient()
    client.pages[("events", None)] = ([table()], "next")
    client.block = ("events", "next")
    client.ignore_cancel = True
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    await vm.refresh()
    pending = asyncio.create_task(vm.load_more())
    await client.started.wait()
    assert vm.is_loading_more
    await vm.load_more()
    assert len(client.calls) == 2
    vm.replace_context(replace(CONTEXT, database="new"))
    assert not vm.is_loading_more
    client.release.set()
    await pending
    assert vm.state is PaneState.EMPTY
    assert vm.items == ()
    await vm.shutdown()
    vm.dispose()


async def test_shutdown_waits_for_owned_request_and_dispose_stops_notifications():
    client = TablesClient()
    client.block = ("events", None)
    client.ignore_cancel = True
    client.pages[("events", None)] = ([table()], None)
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    names = []
    vm.on_property_changed.subscribe(names.append)
    pending = asyncio.create_task(vm.refresh())
    await client.started.wait()
    shutdown = asyncio.create_task(vm.shutdown())
    await client.cancelled.wait()
    try:
        # Let the command's cancellation callbacks settle while the provider
        # remains held; observing only its first cancellation signal is too early.
        for _ in range(6):
            await asyncio.sleep(0)
        assert not shutdown.done()
        vm.dispose()
        vm.dispose()
        count = len(names)
    finally:
        client.release.set()
        await asyncio.gather(pending, shutdown)
    assert len(names) == count
    await vm.refresh()
    await vm.load_more()
    await vm.shutdown()
    assert len(client.calls) == 1


async def test_collection_cap_retains_accepted_page_and_repeated_token_errors():
    client = TablesClient()
    first = [table(f"table-{index}") for index in range(1000)]
    client.pages[("events", None)] = (first, "next")
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    await vm.refresh()
    assert vm.items == tuple(first)
    assert vm.limit_reached
    assert not vm.has_more
    await vm.load_more()
    assert len(client.calls) == 1
    client.pages = {
        ("events", None): ([table("first")], "again"),
        ("events", "again"): ([table("second")], "again"),
    }
    await vm.refresh()
    await vm.load_more()
    assert vm.items == (table("first"),)
    assert vm.state is PaneState.ERROR
    assert not vm.has_more
    await vm.shutdown()
    vm.dispose()


async def test_append_over_cap_preserves_accepted_rows_and_sets_limit():
    client = TablesClient()
    first = [table(f"table-{index}") for index in range(999)]
    client.pages = {
        ("events", None): (first, "next"),
        ("events", "next"): ([table("extra-1"), table("extra-2")], None),
    }
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    await vm.refresh()
    await vm.load_more()
    assert vm.items == tuple(first)
    assert vm.limit_reached
    assert vm.state is PaneState.IDLE
    assert vm.error_text is None
    assert not vm.has_more
    await vm.shutdown()
    vm.dispose()


async def test_empty_page_with_token_can_load_following_tables():
    client = TablesClient()
    client.pages = {("events", None): ([], "next"), ("events", "next"): ([table()], None)}
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    await vm.refresh()
    assert vm.state is PaneState.EMPTY
    assert vm.has_more
    await vm.load_more()
    assert vm.items == (table(),)
    assert vm.state is PaneState.IDLE
    await vm.shutdown()
    vm.dispose()


async def test_initial_over_cap_response_has_truthful_limit_flag():
    client = TablesClient()
    client.pages[("events", None)] = ([table(str(index)) for index in range(1001)], "next")
    vm = AthenaTablesVM(client=client, context=CONTEXT, hub=MessageHub())
    await vm.refresh()
    assert vm.items == ()
    assert vm.limit_reached
    assert not vm.has_more
    assert vm.state is PaneState.EMPTY
    await vm.shutdown()
    vm.dispose()
