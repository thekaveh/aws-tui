"""Speculative destinations use fresh navigation intent and normal selectors."""

import asyncio
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.domain.athena import AthenaCatalogSummary
from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.domain.navigation_workspace import NavigationTarget, WorkspaceError
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena import AthenaService
from aws_tui.services.emr_serverless import EmrServerlessService
from aws_tui.services.glue import GlueService
from tests.unit.vm.athena.test_page_vm import PageClient
from tests.unit.vm.glue._fake_glue import InMemoryGlue


def make_service(service_id):
    connection = Connection("dev", "aws", "us-east-1", "test", profile="dev")
    ports = {"hub": MessageHub(), "dispatcher": NULL_DISPATCHER}
    if service_id == "athena":
        fake = PageClient(connection_name=connection.name, region=connection.region)
        service = AthenaService(
            **ports, aws_session=AwsSession(), athena_client_factory=lambda _connection: fake
        )
        selected = (
            ("workgroup", "analysts"),
            ("catalog", "AwsDataCatalog"),
            ("database", "events"),
        )
    elif service_id == "glue":
        fake = InMemoryGlue()
        fake.add_table("a-first", "a-first")
        fake.add_table("lake", "events")
        service = GlueService(
            **ports, aws_session=AwsSession(), glue_client_factory=lambda _connection: fake
        )
        selected = (("database", "lake"), ("table", "events"))
    else:
        fake = InMemoryEmr()
        fake.add_application(app_id="a-first", name="First")
        fake.add_application(app_id="chosen", name="Chosen")
        service = EmrServerlessService(**ports, emr_client_factory=lambda _connection: fake)
        selected = (("application", "chosen"),)
    return service, connection, fake, NavigationTarget(service_id, "dev", "us-east-1", selected)


@pytest.mark.asyncio
@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
async def test_candidate_is_isolated_and_commits_only_navigation(service_id):
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, fake, target = make_service(service_id)
    live = service.build_vm(connection)
    live.construct()
    await live.setup()
    if service_id == "athena":
        await live.select_view("history")
        live.query.set_sql("SELECT 'DO_NOT_RESTORE'")
        before_history = len(fake.history_calls)
    elif service_id == "glue":
        await live.select_view("jobs")
    before = service.navigation_targets()
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        await resolve_service_target(page, target)
        assert service.navigation_targets() == before
        if service_id == "athena":
            assert page.active_view == "query"
            assert "DO_NOT_RESTORE" not in page.query.sql
            assert page.context.workgroup == "analysts"
            assert page.context.database == "events"
            assert len(fake.history_calls) == before_history
            assert not fake.start_calls
            assert not fake.stop_calls
            assert not fake.result_calls
        elif service_id == "glue":
            assert page.active_view == "catalog"
            assert page.catalog.selected_database_name == "lake"
            assert page.catalog.selected_table_name == "events"
        else:
            assert page.applications.selected_id == "chosen"
            assert all(name not in {"start_job_run", "cancel_job_run"} for name, _ in fake.calls)
        # A later unrelated scope update must survive this staged commit.
        other = replace(target, connection="other")
        service.install_navigation_targets((other,))
        candidate.commit_selection()
        assert set(service.navigation_targets()) == {target, other}
    finally:
        await page.shutdown()
        page.dispose()
        await live.shutdown()
        live.dispose()
        if service_id == "emr-serverless":
            fake.dispose()


@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
@pytest.mark.parametrize(
    "change", [{"name": "missing"}, {"region": "eu-west-1"}, {"kind": "s3-compatible"}]
)
def test_candidate_rejects_wrong_current_source_before_build(service_id, change):
    service, connection, fake, target = make_service(service_id)
    with pytest.raises(WorkspaceError):
        service.build_navigation_vm(replace(connection, **change), target)
    assert service.navigation_targets() == ()
    if service_id == "athena":
        assert not fake.workgroup_calls
    elif service_id == "glue":
        assert not fake.database_tokens
    else:
        assert not fake.calls


@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
def test_service_refuses_foreign_target_batch_before_install(service_id):
    service, _connection, _fake, target = make_service(service_id)
    foreign_service = "glue" if service_id != "glue" else "athena"
    foreign = NavigationTarget(foreign_service, "other", "us-east-1")
    with pytest.raises(WorkspaceError):
        service.install_navigation_targets((target, foreign))
    assert service.navigation_targets() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
async def test_removed_destination_never_succeeds_with_first_default(service_id):
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, fake, target = make_service(service_id)
    selectors = dict(target.selectors)
    selectors[next(iter(selectors))] = "removed"
    target = replace(target, selectors=tuple(selectors.items()))
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        with pytest.raises(WorkspaceError) as error:
            await resolve_service_target(page, target)
        assert error.value.code == "destination_missing"
        assert not service.navigation_targets()
    finally:
        await page.shutdown()
        page.dispose()
        if service_id == "emr-serverless":
            fake.dispose()


@pytest.mark.asyncio
async def test_later_page_glue_database_and_table_use_normal_selection():
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, fake, target = make_service("glue")
    fake.database_page_size = fake.table_page_size = 1
    fake.add_table("lake", "z-last")
    target = replace(target, selectors=(("database", "lake"), ("table", "z-last")))
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        await resolve_service_target(page, target)
        assert fake.database_tokens == [None, "1"]
        assert ("lake", "1") in fake.table_requests
        assert page.catalog.selected_database_name == "lake"
        assert page.catalog.selected_table_name == "z-last"
        assert page.catalog.table_detail.summary.ref.table_name == "z-last"
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_athena_denied_metadata_is_unavailable_not_removed():
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, fake, target = make_service("athena")
    fake.database_error = PermissionDeniedError("do not expose provider text")
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        with pytest.raises(WorkspaceError) as error:
            await resolve_service_target(page, target)
        assert error.value.code == "destination_unavailable"
        assert "provider text" not in str(error.value)
        assert not service.navigation_targets()
        assert not fake.start_calls
        assert not fake.stop_calls
    finally:
        await page.shutdown()
        page.dispose()


class PagedAthena(PageClient):
    @staticmethod
    def _page(rows, token):
        offset = int(token or 0)
        return rows[offset : offset + 1], str(offset + 1) if offset + 1 < len(rows) else None

    async def list_workgroups_page(self, *, start_token=None):
        self.workgroup_calls.append(start_token)
        return self._page(self.workgroups, start_token)

    async def list_catalogs_page(self, *, workgroup=None, start_token=None):
        rows, _ = await super().list_catalogs_page(workgroup=workgroup)
        return self._page(rows, start_token)

    async def list_databases_page(self, catalog, *, workgroup=None, start_token=None):
        rows, _ = await super().list_databases_page(catalog, workgroup=workgroup)
        return self._page(rows, start_token)


@pytest.mark.asyncio
async def test_later_page_athena_workgroup_catalog_database_resolve_exactly():
    from aws_tui.vm.workspace_navigation import resolve_service_target

    _service, connection, _fake, target = make_service("athena")
    fake = PagedAthena(connection_name=connection.name, region=connection.region)
    fake.catalogs["analysts"].insert(0, AthenaCatalogSummary("first", "LAMBDA", None))
    fake.databases[("analysts", "first")] = ["unused"]
    fake.databases[("analysts", "AwsDataCatalog")] = ["first", "events"]
    service = AthenaService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        athena_client_factory=lambda _connection: fake,
    )
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        await resolve_service_target(page, target)
        assert fake.workgroup_calls == [None, "1"]
        assert page.context.workgroup == "analysts"
        assert page.context.catalog == "AwsDataCatalog"
        assert page.context.database == "events"
        assert not fake.start_calls
        assert not fake.stop_calls
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_unfinished_page_budget_is_unavailable_not_missing(monkeypatch):
    import aws_tui.vm.workspace_navigation as module

    service, connection, fake, target = make_service("glue")
    for index in range(5):
        fake.add_database(f"other-{index}")
    fake.database_page_size = 1
    target = replace(target, selectors=(("database", "unseen"),))
    monkeypatch.setattr(module, "MAX_DESTINATION_PAGES", 2)
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        with pytest.raises(WorkspaceError) as error:
            await module.resolve_service_target(page, target)
        assert error.value.code == "destination_unavailable"
        assert fake.database_tokens == [None, "1", "2"]
        assert not service.navigation_targets()
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_disabled_saved_workgroup_is_not_acknowledged_as_available():
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, fake, target = make_service("athena")
    fake.workgroups[1] = replace(fake.workgroups[1], state="DISABLED")
    fake.workgroup_details["analysts"] = replace(
        fake.workgroup_details["analysts"], summary=fake.workgroups[1]
    )
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        with pytest.raises(WorkspaceError) as error:
            await resolve_service_target(page, target)
        assert error.value.code == "destination_unavailable"
        assert not service.navigation_targets()
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_swallowed_setup_cancellation_does_not_become_success(monkeypatch):
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, _fake, target = make_service("athena")
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    entered, release, canceled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_setup = page.setup

    async def resistant_setup():
        await real_setup()
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            canceled.set()
            await release.wait()

    monkeypatch.setattr(page, "setup", resistant_setup)
    task = asyncio.create_task(resolve_service_target(page, target))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.wait_for(canceled.wait(), 2)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service.navigation_targets()
    finally:
        release.set()
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
async def test_adopted_page_keeps_service_navigation_current(service_id):
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, fake, target = make_service(service_id)
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    try:
        await resolve_service_target(page, target)
        candidate.commit_selection()
        if service_id == "athena":
            await page.select_workgroup("primary")
            expected = NavigationTarget(
                service_id,
                "dev",
                "us-east-1",
                (("workgroup", "primary"), ("catalog", "AwsDataCatalog"), ("database", "default")),
            )
        elif service_id == "glue":
            await page.select_database("a-first")
            expected = NavigationTarget(
                service_id, "dev", "us-east-1", (("database", "a-first"), ("table", "a-first"))
            )
        else:
            await page.select_application("a-first")
            expected = NavigationTarget(
                service_id, "dev", "us-east-1", (("application", "a-first"),)
            )
        assert service.navigation_targets() == (expected,)
        # Repeated acknowledgement must not restore the stale original choice.
        candidate.commit_selection()
        assert service.navigation_targets() == (expected,)
        reentered = service.build_vm(connection)
        reentered.construct()
        try:
            await reentered.setup()
            if service_id == "athena":
                assert reentered.context.workgroup == "primary"
            elif service_id == "glue":
                assert reentered.catalog.selected_database_name == "a-first"
            else:
                assert reentered.applications.selected_id == "a-first"
        finally:
            await reentered.shutdown()
            reentered.dispose()
    finally:
        await page.shutdown()
        page.dispose()
        if service_id == "emr-serverless":
            fake.dispose()


@pytest.mark.asyncio
async def test_repeated_athena_commit_does_not_reactivate_old_draft_owner(monkeypatch):
    from aws_tui.vm.workspace_navigation import resolve_service_target

    service, connection, _fake, target = make_service("athena")
    candidate = service.build_navigation_vm(connection, target)
    page = candidate.vm
    page.construct()
    activated = []
    monkeypatch.setattr(page, "activate_drafts", lambda: activated.append(page))
    try:
        await resolve_service_target(page, target)
        candidate.commit_selection()
        candidate.commit_selection()
        assert activated == [page]
    finally:
        await page.shutdown()
        page.dispose()
