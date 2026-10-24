"""Query Refresh discovers fresh metadata without replacing editor ownership."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace

import pytest
from textual.widgets import OptionList, Static, TextArea

from aws_tui.domain.athena import AthenaCatalogSummary
from aws_tui.domain.filesystem import PermissionDeniedError, ProviderError
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import drain_workers, wait_until
from tests.unit.ui.athena.test_page import _AthenaApp
from tests.unit.vm.athena.test_page_vm import PageClient, _accept_sql_replacement, make_page_vm


def _option_ids(app, selector):
    options = app.query_one(selector, ContextPicker).query_one(OptionList)
    return [options.get_option_at_index(index).id for index in range(options.option_count)]


@pytest.mark.parametrize("guarded", [False, True])
async def test_mounted_refresh_reloads_healthy_catalogs_databases_and_keeps_query(guarded):
    client = PageClient()
    client.databases[("primary", "AwsDataCatalog")] = ["default", "obsolete"]
    vm = make_page_vm(client)
    await vm.setup()
    vm.query.set_sql("SELECT 7")
    await vm.query.execute()
    vm.query.set_draft_recovery_guard(guarded)
    before_query = vm.query.export_snapshot()
    before_context = vm.context
    before_calls = (
        len(client.workgroup_detail_calls),
        len(client.catalog_calls),
        len(client.database_calls),
    )
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert _option_ids(pilot.app, "#athena-catalog") == ["AwsDataCatalog", "federated"]
            assert _option_ids(pilot.app, "#athena-database") == ["default", "obsolete"]
            client.catalogs["primary"] = [
                client.catalogs["primary"][0],
                AthenaCatalogSummary("new_catalog", "LAMBDA", None),
            ]
            client.databases[("primary", "AwsDataCatalog")] = ["default", "new_database"]

            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()

            assert _option_ids(pilot.app, "#athena-catalog") == ["AwsDataCatalog", "new_catalog"]
            assert _option_ids(pilot.app, "#athena-database") == ["default", "new_database"]
            assert (
                len(client.workgroup_detail_calls),
                len(client.catalog_calls),
                len(client.database_calls),
            ) == tuple(count + 1 for count in before_calls)
            assert vm.context == before_context
            assert vm.query.export_snapshot() == before_query
            assert pilot.app.query_one("#athena-editor", TextArea).text == "SELECT 7"
            assert client.stop_calls == []
            assert len(client.start_calls) == 1
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("enforced", [False, True])
async def test_mounted_refresh_adopts_changed_workgroup_configuration(enforced):
    client = PageClient()
    vm = make_page_vm(client)
    await vm.setup()
    updated = replace(
        client.workgroup_details["primary"],
        output_location="s3://updated-results/",
        enforce_workgroup_configuration=enforced,
    )
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            client.workgroup_details["primary"] = updated
            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()

            assert vm.workgroup_detail == updated
            detail = str(pilot.app.query_one("#athena-query-detail-text", Static).render())
            assert "s3://updated-results/" in detail
            assert ("enforced" if enforced else "caller configurable") in detail
            assert client.start_calls == []
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("failed", ["workgroup", "workgroup_detail", "catalog", "database"])
async def test_partial_failure_is_local_to_its_control_and_preserves_query(failed):
    client = PageClient()
    vm = make_page_vm(client)
    await vm.setup()
    vm.query.set_sql("SELECT 7")
    await vm.query.execute()
    before = vm.query.export_snapshot()
    client.catalogs["primary"].append(AthenaCatalogSummary("new_catalog", "LAMBDA", None))
    client.databases[("primary", "AwsDataCatalog")].append("new_database")
    setattr(client, f"{failed}_error", PermissionDeniedError("private denied context"))
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()
            state = getattr(vm, f"{failed if failed == 'workgroup_detail' else failed + 's'}_state")
            assert state is PaneState.FORBIDDEN
            if failed != "catalog":
                assert "new_catalog" in _option_ids(pilot.app, "#athena-catalog")
            if failed != "database":
                assert "new_database" in _option_ids(pilot.app, "#athena-database")
            if failed != "workgroup_detail":
                assert vm.workgroup_detail_state is PaneState.IDLE
            else:
                assert not vm.query.execute_command.can_execute()
            assert vm.query.export_snapshot() == before
            assert client.stop_calls == []
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("disabled_in", ["summary", "detail"])
async def test_fresh_disabled_workgroup_blocks_run_without_retiring_prior_execution(disabled_in):
    client = PageClient()
    vm = make_page_vm(client)
    await vm.setup()
    vm.query.set_sql("SELECT 7")
    await vm.query.execute()
    before = vm.query.export_snapshot()
    if disabled_in == "summary":
        client.workgroups[0] = replace(client.workgroups[0], state="DISABLED")
    else:
        detail = client.workgroup_details["primary"]
        client.workgroup_details["primary"] = replace(
            detail, summary=replace(detail.summary, state="DISABLED")
        )
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert not vm.query.execute_command.can_execute()
            assert vm.query.export_snapshot() == before
            assert client.stop_calls == []
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("removed", ["catalog", "database"])
async def test_successful_discovery_does_not_leave_removed_context_executable(removed):
    client = PageClient()
    vm = make_page_vm(client)
    await vm.setup()
    vm.query.set_sql("SELECT 7")
    if removed == "catalog":
        client.catalogs["primary"] = [client.catalogs["primary"][1]]
    else:
        client.databases[("primary", "AwsDataCatalog")] = ["new_database"]
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert getattr(vm.context, removed) != (
                "AwsDataCatalog" if removed == "catalog" else "default"
            )
            assert vm.query.sql == "SELECT 7"
            assert client.start_calls == []
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_refresh_reloads_later_pages_even_when_first_page_is_unchanged():
    client = PageClient()
    refreshed = False

    async def catalogs(*, workgroup=None, start_token=None):
        client.catalog_calls.append((workgroup, start_token))
        if start_token is None:
            return client.catalogs["primary"][:1], "later"
        return [
            client.catalogs["primary"][1],
            AthenaCatalogSummary("new_catalog" if refreshed else "retired_catalog", "LAMBDA", None),
        ], None

    async def databases(catalog, *, workgroup=None, start_token=None):
        from aws_tui.domain.data_catalog import DatabaseRef, DatabaseSummary

        client.database_calls.append((workgroup, catalog, start_token))
        names = (
            ["first"]
            if start_token is None
            else ["remote", "new_database" if refreshed else "retired_database"]
        )
        return [
            DatabaseSummary(
                DatabaseRef(catalog, name, client.connection_name, client.region), None, None, None
            )
            for name in names
        ], "later" if start_token is None else None

    client.list_catalogs_page = catalogs
    client.list_databases_page = databases
    vm = make_page_vm(client)
    await vm.setup()
    await vm.load_more_catalogs()
    await vm.select_catalog("federated")
    await vm.load_more_databases()
    await vm.select_database("remote")
    vm.query.set_sql("SELECT 7")
    before = vm.query.export_snapshot()
    refreshed = True
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert _option_ids(pilot.app, "#athena-catalog") == [
                "AwsDataCatalog",
                "federated",
                "new_catalog",
            ]
            assert _option_ids(pilot.app, "#athena-database") == ["first", "remote", "new_database"]
            assert vm.query.export_snapshot() == before
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("failed", ["workgroup", "workgroup_detail", "catalog", "database"])
async def test_unexpected_refresh_failure_has_stable_control_state(failed):
    client = PageClient()
    vm = make_page_vm(client)
    await vm.setup()
    setattr(client, f"{failed}_error", RuntimeError("private://unavailable"))
    try:
        await vm.refresh_query_context()
        state = getattr(vm, f"{failed if failed == 'workgroup_detail' else failed + 's'}_state")
        assert state is PaneState.ERROR
        text = getattr(vm, f"{failed if failed == 'workgroup_detail' else failed + 's'}_error_text")
        assert "private" not in text
        assert vm.context.database == "default"
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("blocked", ["workgroups", "detail", "catalogs", "databases"])
@pytest.mark.parametrize("superseding", ["workgroup", "catalog", "database", "shutdown"])
async def test_late_refresh_cannot_publish_after_newer_selection_or_shutdown(blocked, superseding):
    client = PageClient()
    client.databases[("primary", "AwsDataCatalog")].append("other")
    vm = make_page_vm(client)
    await vm.setup()
    started, release = asyncio.Event(), asyncio.Event()
    method = {
        "workgroups": "list_workgroups_page",
        "detail": "get_workgroup",
        "catalogs": "list_catalogs_page",
        "databases": "list_databases_page",
    }[blocked]
    original = getattr(client, method)
    first = True

    async def delayed(*args, **kwargs):
        nonlocal first
        response = await original(*args, **kwargs)
        if first:
            first = False
            started.set()
            while not release.is_set():
                # A provider can deliver an old reply despite cancellation.
                with suppress(asyncio.CancelledError):
                    await release.wait()
        return response

    setattr(client, method, delayed)
    refreshing = asyncio.create_task(vm.refresh_query_context())
    shutdown = None
    try:
        await wait_until(started.is_set, what="controlled metadata stage started")
        if superseding == "workgroup":
            await vm.select_workgroup("analysts")
        elif superseding == "catalog":
            await vm.select_catalog("federated")
        elif superseding == "database":
            await vm.select_database("other")
        else:
            shutdown = asyncio.create_task(vm.shutdown())
            await wait_until(lambda: vm._shutdown_started, what="shutdown reserves admission")
        expected = vm.context, vm.workgroup_detail, vm.workgroups, vm.catalogs, vm.databases
        table_calls = []
        refresh_tables = vm.tables.refresh

        async def tracked_tables():
            table_calls.append(vm.context)
            await refresh_tables()

        vm.tables.refresh = tracked_tables
        release.set()
        results = await asyncio.gather(refreshing, return_exceptions=True)
        if shutdown is not None:
            await shutdown
        else:
            assert results == [None]
            assert all(
                state is not PaneState.LOADING
                for state in (
                    vm.workgroups_state,
                    vm.workgroup_detail_state,
                    vm.catalogs_state,
                    vm.databases_state,
                )
            )
        assert (
            vm.context,
            vm.workgroup_detail,
            vm.workgroups,
            vm.catalogs,
            vm.databases,
        ) == expected
        assert table_calls == []
    finally:
        release.set()
        await asyncio.gather(refreshing, return_exceptions=True)
        if shutdown is not None:
            await shutdown
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("kind", ["workgroups", "catalogs", "databases"])
async def test_incomplete_discovery_cannot_enable_an_unverified_selection(kind):
    from aws_tui.domain.athena import AthenaWorkgroupSummary
    from aws_tui.domain.data_catalog import DatabaseRef, DatabaseSummary

    client = PageClient()
    if kind == "workgroups":
        client.workgroups[0] = replace(client.workgroups[0], state="DISABLED")
    vm = make_page_vm(client)
    await vm.setup()
    vm.query.set_sql("SELECT 7")
    before = vm.context
    if kind == "workgroups":

        async def capped(*, start_token=None):
            return [
                AthenaWorkgroupSummary(f"other-{i}", "ENABLED", None, None) for i in range(1000)
            ], "beyond-budget"

        client.list_workgroups_page = capped
    elif kind == "catalogs":

        async def capped(*, workgroup=None, start_token=None):
            return [
                AthenaCatalogSummary(f"other-{i}", "LAMBDA", None) for i in range(1000)
            ], "beyond-budget"

        client.list_catalogs_page = capped
    else:

        async def capped(catalog, *, workgroup=None, start_token=None):
            return [
                DatabaseSummary(
                    DatabaseRef(catalog, f"other-{i}", client.connection_name, client.region),
                    None,
                    None,
                    None,
                )
                for i in range(1000)
            ], "beyond-budget"

        client.list_databases_page = capped
    try:
        await vm.refresh_query_context()
        assert vm.context == before
        assert not vm.query.execute_command.can_execute()
        assert client.start_calls == []
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("recovery", [False, True])
async def test_shutdown_drains_actual_provider_after_refresh_awaiter_is_cancelled(recovery):
    client = PageClient()
    if recovery:
        client.workgroup_detail_error = ProviderError("initially unavailable")
    vm = make_page_vm(client)
    await vm.setup()
    client.workgroup_detail_error = None
    started, release = asyncio.Event(), asyncio.Event()
    provider_task = None
    original = client.list_catalogs_page

    async def delayed(*args, **kwargs):
        nonlocal provider_task
        provider_task = asyncio.current_task()
        started.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        return await original(*args, **kwargs)

    client.list_catalogs_page = delayed
    refreshing = asyncio.create_task(vm.refresh_query_context())
    shutdown = None
    try:
        await wait_until(started.is_set, what="actual staged provider task started")
        refreshing.cancel()
        await asyncio.gather(refreshing, return_exceptions=True)
        assert provider_task is not None
        assert not provider_task.done()
        assert provider_task in vm._page_tasks
        shutdown = asyncio.create_task(vm.shutdown())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        await shutdown
        assert provider_task.done()
        assert not [task for task in vm._page_tasks if not task.done()]
    finally:
        release.set()
        await asyncio.gather(refreshing, return_exceptions=True)
        if provider_task is not None:
            await asyncio.gather(provider_task, return_exceptions=True)
        if shutdown is not None:
            await shutdown
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("origin_mismatch", [False, True])
async def test_mounted_refresh_preserves_real_durable_draft_provenance(tmp_path, origin_mismatch):
    from tests.athena_drafts_helpers import runtime_at
    from tests.unit.vm.athena.test_draft_recovery import yes

    runtime, store = runtime_at(tmp_path)
    client = PageClient()
    client.databases[("primary", "AwsDataCatalog")].append("other")
    vm = make_page_vm(client, drafts=runtime, source_is_current=yes)
    await vm.setup()
    vm.query.set_sql("SELECT 7")
    origin = vm.context
    await vm._draft_session.flush(deadline=asyncio.get_running_loop().time() + 2)
    if origin_mismatch:
        await vm.select_database("other", confirm=_accept_sql_replacement)
        assert vm.query.draft_state == "context_required"
        assert not vm.query.execute_command.can_execute()
    before = vm.query.export_editor_snapshot()
    client.databases[("primary", "AwsDataCatalog")].append("new_database")
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert "new_database" in _option_ids(pilot.app, "#athena-database")
            assert vm.query.export_editor_snapshot() == before
            assert vm.query.execute_command.can_execute() is not origin_mismatch
            records = store.list().records
            assert len(records) == 1
            assert records[0].context == origin.cache_key
            assert records[0].sql == "SELECT 7"
    finally:
        await vm.shutdown()
        vm.dispose()
        await runtime.shutdown()
        runtime.dispose()


@pytest.mark.parametrize("superseding", ["refresh", "round_trip"])
async def test_older_detail_cannot_override_newer_refresh_or_same_identity_round_trip(superseding):
    client = PageClient()
    vm = make_page_vm(client)
    await vm.setup()
    vm.query.set_sql("SELECT 7")
    started, release = asyncio.Event(), asyncio.Event()
    original = client.get_workgroup
    first = True

    async def delayed(name):
        nonlocal first
        response = await original(name)
        if first:
            first = False
            started.set()
            await release.wait()
        return response

    client.get_workgroup = delayed
    refreshing = asyncio.create_task(vm.refresh_query_context())
    try:
        await wait_until(started.is_set, what="old workgroup detail captured")
        client.workgroup_details["primary"] = replace(
            client.workgroup_details["primary"], output_location="s3://newer/"
        )
        if superseding == "refresh":
            await vm.refresh_query_context()
        else:
            await vm.select_workgroup("analysts", confirm=_accept_sql_replacement)
            await vm.select_workgroup("primary", confirm=_accept_sql_replacement)
        before = vm.export_snapshot()
        release.set()
        await refreshing
        assert vm.export_snapshot() == before
        assert vm.workgroup_detail.output_location == "s3://newer/"
    finally:
        release.set()
        await refreshing
        await vm.shutdown()
        vm.dispose()


async def test_mounted_refresh_preserves_selected_table_and_execution():
    from aws_tui.domain.data_catalog import TableRef, TableSummary

    client = PageClient()
    ref = TableRef("AwsDataCatalog", "default", "events", client.connection_name, client.region)

    async def tables(catalog, database, *, workgroup=None, start_token=None):
        return [TableSummary(ref, None, None, None, None, None)], None

    client.list_tables_page = tables
    vm = make_page_vm(client)
    await vm.setup()
    await vm.tables.refresh()
    await vm.select_table("events")
    await vm.query.execute()
    before = vm.query.export_snapshot()
    try:
        async with _AthenaApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.app.query_one(AthenaPage).action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert vm.selected_table_ref == ref
            assert vm.query.export_snapshot() == before
            assert pilot.app.query_one("#athena-table", ContextPicker).value == "events"
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_refresh_rechecks_owner_after_retiring_old_load_more_notification():
    client = PageClient()
    started, release = asyncio.Event(), asyncio.Event()
    refreshed = False

    async def catalogs(*, workgroup=None, start_token=None):
        if start_token is not None:
            started.set()
            await release.wait()
            return [AthenaCatalogSummary("old_later", "LAMBDA", None)], None
        return [
            AthenaCatalogSummary("AwsDataCatalog", "LAMBDA", None),
            AthenaCatalogSummary("new" if refreshed else "old", "LAMBDA", None),
        ], "later"

    client.list_catalogs_page = catalogs
    vm = make_page_vm(client)
    await vm.setup()
    before = vm.catalogs
    loading = asyncio.create_task(vm.load_more_catalogs())
    await wait_until(started.is_set, what="old load more started")
    refreshed = True

    def reserve(name):
        if name == "is_loading_more_catalogs" and not vm.is_loading_more_catalogs:
            vm.close_input_admission()

    subscription = vm.on_property_changed.subscribe(reserve)
    try:
        await vm.refresh_query_context()
        release.set()
        await loading
        assert vm.catalogs == before
    finally:
        release.set()
        await loading
        subscription.dispose()
        await vm.shutdown()
        vm.dispose()


async def test_newer_refresh_also_supersedes_incomplete_context_recovery():
    client = PageClient()
    client.workgroup_detail_error = ProviderError("initially unavailable")
    vm = make_page_vm(client)
    await vm.setup()
    client.workgroup_detail_error = None
    detail_started, release_detail = asyncio.Event(), asyncio.Event()
    list_started, release_list = asyncio.Event(), asyncio.Event()
    get_detail, get_groups = client.get_workgroup, client.list_workgroups_page
    first_detail = True
    gate_list = False

    async def delayed_detail(name):
        nonlocal first_detail
        response = await get_detail(name)
        if first_detail:
            first_detail = False
            detail_started.set()
            await release_detail.wait()
        return response

    async def delayed_groups(*, start_token=None):
        response = await get_groups(start_token=start_token)
        if gate_list:
            list_started.set()
            await release_list.wait()
        return response

    client.get_workgroup, client.list_workgroups_page = delayed_detail, delayed_groups
    first = asyncio.create_task(vm.refresh_query_context())
    second = None
    try:
        await wait_until(detail_started.is_set, what="first recovery detail captured")
        client.workgroup_details["primary"] = replace(
            client.workgroup_details["primary"], output_location="s3://newest/"
        )
        gate_list = True
        second = asyncio.create_task(vm.refresh_query_context())
        await wait_until(list_started.is_set, what="newer recovery owns workgroup discovery")
        release_detail.set()
        await first
        release_list.set()
        await second
        assert vm.workgroup_detail.output_location == "s3://newest/"
        assert vm.context.database == "default"
        assert vm.workgroup_detail_state is PaneState.IDLE
    finally:
        release_detail.set()
        release_list.set()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )
        await vm.shutdown()
        vm.dispose()


async def test_cancelled_incomplete_recovery_cannot_publish_late_detail():
    client = PageClient()
    client.workgroup_detail_error = ProviderError("initially unavailable")
    vm = make_page_vm(client)
    await vm.setup()
    before = vm.context
    client.workgroup_detail_error = None
    started, release = asyncio.Event(), asyncio.Event()
    original = client.get_workgroup

    async def delayed(name):
        response = await original(name)
        started.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        return response

    client.get_workgroup = delayed
    refreshing = asyncio.create_task(vm.refresh_query_context())
    try:
        await wait_until(started.is_set, what="recovery detail is pending")
        refreshing.cancel()
        release.set()
        result = await asyncio.gather(refreshing, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert vm.context == before
        assert vm.workgroup_detail is None
        assert vm.workgroup_detail_state is PaneState.ERROR
        assert client.catalog_calls == []
    finally:
        release.set()
        await asyncio.gather(refreshing, return_exceptions=True)
        await vm.shutdown()
        vm.dispose()
