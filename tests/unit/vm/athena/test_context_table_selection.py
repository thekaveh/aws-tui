"""Explicit parent choices coordinate table metadata and the authored document."""

from __future__ import annotations

import asyncio

import pytest

from aws_tui.domain.data_catalog import TableRef, TableSummary
from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.sql_policy import select_starter_sql
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


class ContextTablesClient(PageClient):
    def __init__(self):
        super().__init__()
        self.databases["primary", "AwsDataCatalog"] = ["default", 'other"db', "empty"]
        self.names = {
            "default": ['shared"table'],
            'other"db': ['first"table', 'shared"table'],
            "empty": [],
            "remote": ['remote"table'],
            "events": ['event"table'],
        }
        self.pages = {}
        self.error = None
        self.block_database = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def list_tables_page(self, catalog, database, *, workgroup=None, start_token=None):
        self.calls.append((workgroup, catalog, database, start_token))
        if database == self.block_database:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()  # Model a provider which delivers after cancellation.
        if self.error:
            raise self.error
        names, token = self.pages.get((database, start_token), (self.names[database], None))
        return [
            TableSummary(
                TableRef(catalog, database, name, self.connection_name, self.region),
                None,
                None,
                None,
                None,
                None,
            )
            for name in names
        ], token


async def yes(_message):
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("retain", [True, False])
async def test_database_choice_retains_table_or_selects_first_and_explicit_run_uses_context(retain):
    client = ContextTablesClient()
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        if not retain:
            client.names['other"db'] = ['first"table']
        await page.select_database('other"db')
        name = 'shared"table' if retain else 'first"table'
        ref = TableRef("AwsDataCatalog", 'other"db', name, "analytics", "us-west-2")
        assert page.selected_table_ref == ref
        assert page.query.sql == select_starter_sql(ref)
        assert page.query.context == page.context == page.tables.context
        assert client.start_calls == []
        await page.query.execute()
        assert client.start_calls[0][:2] == (select_starter_sql(ref), page.context)
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "value", "name"),
    [
        ("catalog", "federated", 'remote"table'),
        ("workgroup", "analysts", 'event"table'),
        ("database", "empty", None),
    ],
)
async def test_parent_choice_replaces_generated_sql_or_clears_empty_database(kind, value, name):
    client = ContextTablesClient()
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        await getattr(page, "select_" + kind)(value)
        if name is None:
            assert page.selected_table_ref is None
            assert page.query.sql == ""
            assert page.tables.state is PaneState.EMPTY
        else:
            ref = TableRef(
                page.context.catalog, page.context.database, name, "analytics", "us-west-2"
            )
            assert page.selected_table_ref == ref
            assert page.query.sql == select_starter_sql(ref)
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("accept", [False, True])
async def test_modified_sql_requires_confirmation_before_parent_context_changes(accept):
    client = ContextTablesClient()
    page = make_page_vm(client)
    prompts = []

    async def confirm(message):
        prompts.append(message)
        return accept

    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        original_context, original_ref = page.context, page.selected_table_ref
        page.query.set_sql("SELECT 42 -- my edit")
        await page.select_database('other"db', confirm=confirm)
        assert len(prompts) == 1
        if accept:
            assert page.context.database == 'other"db'
            assert page.query.sql == select_starter_sql(page.selected_table_ref)
        else:
            assert page.context == original_context
            assert page.selected_table_ref == original_ref
            assert page.query.sql == "SELECT 42 -- my edit"
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_table_metadata_failure_is_visible_and_retires_stale_generated_sql():
    client = ContextTablesClient()
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        client.error = ProviderError("controlled failure")
        await page.select_database('other"db')
        assert page.tables.state is PaneState.ERROR
        assert page.tables.error_text
        assert page.selected_table_ref is None
        assert page.query.sql == ""
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("newer", ["edit", "choice"])
async def test_delayed_table_listing_cannot_overwrite_newer_input(newer):
    client = ContextTablesClient()
    page = make_page_vm(client)
    pending = None
    try:
        await page.setup()
        client.block_database = 'other"db'
        pending = asyncio.create_task(page.select_database('other"db'))
        await asyncio.wait_for(client.entered.wait(), 1)
        if newer == "edit":
            page.query.set_sql("SELECT 99 -- newer")
        else:
            await page.select_database("default")
        client.release.set()
        await pending
        if newer == "edit":
            assert page.query.sql == "SELECT 99 -- newer"
            assert page.selected_table_ref is None
        else:
            assert page.context.database == "default"
            assert page.selected_table_ref.table_name == 'shared"table'
            assert page.query.sql == select_starter_sql(page.selected_table_ref)
        assert client.start_calls == []
    finally:
        client.release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("retain", [False, True])
async def test_table_choice_discovers_empty_pages_and_retained_names_beyond_first_page(retain):
    client = ContextTablesClient()
    client.pages = {
        ('other"db', None): ([], "p2"),
        ('other"db', "p2"): (['first"table'], "p3"),
        ('other"db', "p3"): (['shared"table'] if retain else [], None),
    }
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        await page.select_database('other"db')
        assert page.selected_table_ref.table_name == ('shared"table' if retain else 'first"table')
        assert page.query.sql == select_starter_sql(page.selected_table_ref)
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_incomplete_retention_discovery_is_visible_without_stale_generated_sql():
    client = ContextTablesClient()
    client.pages = {
        ('other"db', None): (['first"table'], "repeat"),
        ('other"db', "repeat"): ([], "repeat"),
    }
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        await page.select_database('other"db')
        assert page.tables.state is PaneState.ERROR
        assert page.tables.error_text
        assert page.selected_table_ref is None
        assert page.query.sql == ""
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("newer", ["edit", "choice"])
async def test_parent_confirmation_cannot_authorize_a_later_edit_or_choice(newer):
    client = ContextTablesClient()
    page = make_page_vm(client)
    entered, release = asyncio.Event(), asyncio.Event()

    async def confirm(_message):
        entered.set()
        await release.wait()
        return True

    pending = None
    try:
        await page.setup()
        page.query.set_sql("SELECT 42")
        pending = asyncio.create_task(page.select_database('other"db', confirm=confirm))
        await asyncio.wait_for(entered.wait(), 1)
        if newer == "edit":
            page.query.set_sql("SELECT 43")
        else:
            await page.select_catalog("federated", confirm=yes)
        release.set()
        await pending
        if newer == "edit":
            assert page.context.database == "default"
            assert page.query.sql == "SELECT 43"
        else:
            assert page.context.catalog == "federated"
            assert page.selected_table_ref.table_name == 'remote"table'
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_bounded_empty_page_discovery_exposes_failure_instead_of_ready_stale_sql():
    client = ContextTablesClient()
    client.pages = {('other"db', None): ([], "p1")}
    client.pages.update(
        {('other"db', f"p{number}"): ([], f"p{number + 1}") for number in range(1, 8)}
    )
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        await page.select_database('other"db')
        assert page.tables.state is PaneState.ERROR
        assert page.tables.error_text
        assert page.query.sql == ""
        assert page.selected_table_ref is None
        assert len(client.calls) <= 6
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_empty_context_retains_confirmed_authored_sql_and_document():
    client = ContextTablesClient()
    page = make_page_vm(client)
    try:
        await page.setup()
        page.query.set_sql("SELECT 42 -- authored")
        document = page.authoring.export_snapshot()
        await page.select_database("empty", confirm=yes)
        assert page.context.database == "empty"
        assert page.query.sql == "SELECT 42 -- authored"
        assert page.authoring.export_snapshot() == document
        assert page.selected_table_ref is None
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_reentrant_edit_during_generated_document_clear_is_not_implicitly_confirmed():
    client = ContextTablesClient()
    page = make_page_vm(client)
    subscription = None
    try:
        await page.setup()
        await page.tables.refresh()
        await page.select_table('shared"table')
        original = page.context

        def edit(property_name):
            if property_name == "authoring" and page.query.sql == "":
                page.query.set_sql("SELECT 99 -- reentrant edit")

        subscription = page.on_property_changed.subscribe(edit)
        await page.select_database('other"db')
        assert page.query.sql == "SELECT 99 -- reentrant edit"
        assert page.context == original
        assert client.start_calls == []
    finally:
        if subscription is not None:
            subscription.dispose()
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_queued_view_refresh_cannot_replace_explicit_context_listing(monkeypatch):
    from aws_tui.ui.widgets.athena.page import AthenaPage

    class QueuedClient(ContextTablesClient):
        async def list_tables_page(self, catalog, database, **kwargs):
            if database == 'other"db' and self.entered.is_set():
                self.release.set()
            return await super().list_tables_page(catalog, database, **kwargs)

    client = QueuedClient()
    page = make_page_vm(client)
    queued = []
    pending = None
    try:
        await page.setup()
        view = AthenaPage(page, hub=page._hub)
        monkeypatch.setattr(view, "_picker_coordination_available", lambda: True)
        monkeypatch.setattr(
            view, "_run_lifecycle_worker", lambda callback, **kwargs: queued.append(callback)
        )
        view._request_table_refresh()
        assert len(queued) == 1
        client.block_database = 'other"db'
        pending = asyncio.create_task(page.select_database('other"db'))
        await asyncio.wait_for(client.entered.wait(), 1)
        # This request was queued by the old view before the parent choice.
        await queued[0]()
        client.release.set()
        await pending
        assert page.selected_table_ref is not None
        assert page.selected_table_ref.table_name == 'first"table'
        assert page.query.sql == select_starter_sql(page.selected_table_ref)
        assert sum(call[2] == 'other"db' for call in client.calls) == 1
    finally:
        client.release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["history", "saved"])
async def test_parent_context_auto_selection_preserves_active_view(view):
    client = ContextTablesClient()
    page = make_page_vm(client)
    try:
        await page.setup()
        await page.select_view(view)
        assert page.active_view == view
        await page.select_database('other"db')
        assert page.active_view == view
        assert page.context.database == 'other"db'
        assert page.selected_table_ref.table_name == 'first"table'
        assert page.query.sql == select_starter_sql(page.selected_table_ref)
        assert page.query.context == page.context
        assert client.start_calls == []
        await page.select_table('first"table')
        assert page.active_view == "query"
        assert client.start_calls == []
    finally:
        await page.shutdown()
        page.dispose()
