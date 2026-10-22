"""A workspace validates every saved scope before publishing any navigation."""

import asyncio
import threading
from dataclasses import replace

import pytest

from aws_tui.domain.navigation_workspace import NavigationTarget, Workspace, WorkspaceError
from aws_tui.infra.connection_resolver import ConnectionNotFound
from aws_tui.vm.services_protocol import ServiceRegistry
from tests.unit.services.s3.test_workspace_candidate import (
    local,
    remote,
)
from tests.unit.services.s3.test_workspace_candidate import (
    make_service as make_s3,
)
from tests.unit.services.test_workspace_navigation import make_service


def library(tmp_path, *, active="glue"):
    registry = ServiceRegistry()
    services = {}
    fakes = {}
    targets = []
    for service_id in ("athena", "glue", "emr-serverless"):
        service, connection, fake, target = make_service(service_id)
        registry.register(service)
        services[service_id] = service
        fakes[service_id] = fake
        targets.append(target)
    root = tmp_path / "local"
    root.mkdir()
    s3 = make_s3(tmp_path)
    services["s3"] = s3
    registry.register(s3)
    workspace = Workspace(
        "Daily",
        active,
        connection.name,
        connection.region,
        file_panes=(local(root), remote(connection.name, connection.region, ())),
        scopes=tuple(targets),
    )
    return registry, services, fakes, connection, workspace


def dispose_services(services, fakes):
    services["s3"].transfer_runtime.dispose()
    fakes["emr-serverless"].dispose()


@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
@pytest.mark.parametrize("active", [True, False])
@pytest.mark.asyncio
async def test_ordinary_empty_visited_scope_survives_capture(tmp_path, service_id, active):
    from aws_tui.workspace_restore import capture_workspace, retire_workspace_candidate

    registry, services, fakes, connection, _workspace = library(tmp_path, active=service_id)
    fake = fakes[service_id]
    if service_id == "athena":
        fake.workgroups.clear()
    elif service_id == "glue":
        fake.databases.clear()
        fake.tables.clear()
    else:
        fake._apps.clear()
    page = services[service_id].build_vm(connection)
    panes = None
    try:
        page.construct()
        assert services[service_id].navigation_targets() == ()
        await page.setup()
        if active:
            saved = capture_workspace("Empty service", registry, service_id, connection, page)
        else:
            await retire_workspace_candidate(page)
            panes = services["s3"].build_vm(connection)
            panes.construct()
            await panes.setup()
            saved = capture_workspace("Empty service", registry, "s3", connection, panes)
        assert saved.scopes == (NavigationTarget(service_id, connection.name, connection.region),)
    finally:
        await retire_workspace_candidate(page)
        if panes is not None:
            await retire_workspace_candidate(panes)
        dispose_services(services, fakes)


@pytest.mark.parametrize("active", ["s3", "athena", "glue", "emr-serverless"])
@pytest.mark.asyncio
async def test_whole_workspace_preflight_is_isolated_and_commit_installs_all_known_scopes(
    tmp_path, active
):
    from aws_tui.workspace_restore import prepare_workspace, retire_workspace_candidate

    registry, services, fakes, connection, workspace = library(tmp_path, active=active)
    prepared = None
    try:
        prepared = await prepare_workspace(workspace, registry, (connection,))
        assert prepared.service_id == active
        assert prepared.connection == connection
        for service_id in ("athena", "glue", "emr-serverless"):
            assert services[service_id].navigation_targets() == ()
        assert services["s3"].navigation_panes() is None
        other = replace(workspace.scopes[1], connection="unrelated")
        services["glue"].install_navigation_targets((other,))
        prepared.recovery.commit_selection()
        for target in workspace.scopes:
            assert target in services[target.service].navigation_targets()
        assert other in services["glue"].navigation_targets()
        assert services["s3"].navigation_panes() == workspace.file_panes
        # This also protects the mutation-free metadata-read qualification.
        assert not fakes["athena"].start_calls
        assert not fakes["athena"].stop_calls
        assert not fakes["athena"].result_calls
        assert all(
            name not in {"start_job_run", "cancel_job_run"}
            for name, _ in fakes["emr-serverless"].calls
        )
    finally:
        if prepared is not None:
            await retire_workspace_candidate(prepared.recovery.vm)
        dispose_services(services, fakes)


@pytest.mark.parametrize("failed", ["athena", "glue", "emr-serverless", "s3"])
@pytest.mark.asyncio
async def test_one_removed_inactive_destination_cannot_partially_restore_a_workspace(
    tmp_path, failed
):
    from aws_tui.workspace_restore import prepare_workspace

    registry, services, fakes, connection, workspace = library(tmp_path)
    if failed == "s3":
        workspace = replace(
            workspace, file_panes=(local(tmp_path / "local" / "gone"), workspace.file_panes[1])
        )
    else:
        changed = next(target for target in workspace.scopes if target.service == failed)
        selectors = dict(changed.selectors)
        selectors[
            {"athena": "database", "glue": "table", "emr-serverless": "application"}[failed]
        ] = "gone"
        changed = replace(changed, selectors=tuple(selectors.items()))
        workspace = replace(
            workspace,
            scopes=tuple(
                changed if target.service == failed else target for target in workspace.scopes
            ),
        )
    try:
        with pytest.raises(WorkspaceError):
            await prepare_workspace(workspace, registry, (connection,))
        assert services["s3"].navigation_panes() is None
        for service_id in ("athena", "glue", "emr-serverless"):
            assert services[service_id].navigation_targets() == ()
    finally:
        dispose_services(services, fakes)


@pytest.mark.parametrize("invalid", ["missing", "region", "unsupported", "duplicate"])
@pytest.mark.asyncio
async def test_every_source_is_revalidated_before_any_scope_metadata_read(tmp_path, invalid):
    from aws_tui.workspace_restore import prepare_workspace

    registry, services, fakes, connection, workspace = library(tmp_path)
    sources = {
        "missing": (),
        "region": (replace(connection, region="eu-west-1"),),
        "unsupported": (replace(connection, kind="unsupported"),),
        "duplicate": (connection, connection),
    }[invalid]
    try:
        with pytest.raises(WorkspaceError):
            await prepare_workspace(workspace, registry, sources)
        assert not fakes["athena"].workgroup_calls
        assert not fakes["glue"].database_tokens
        assert not fakes["emr-serverless"].calls
    finally:
        dispose_services(services, fakes)


@pytest.mark.asyncio
async def test_local_only_workspace_requires_no_root_connection(tmp_path):
    from aws_tui.workspace_restore import prepare_workspace, retire_workspace_candidate

    registry, services, fakes, _connection, _workspace = library(tmp_path)
    root = tmp_path / "local"
    workspace = Workspace("Local", "s3", file_panes=(local(root), local(root)))
    prepared = None
    try:
        prepared = await prepare_workspace(workspace, registry, ())
        assert prepared.connection is None
        prepared.recovery.commit_selection()
        assert services["s3"].navigation_panes() == workspace.file_panes
    finally:
        if prepared is not None:
            await retire_workspace_candidate(prepared.recovery.vm)
        dispose_services(services, fakes)


@pytest.mark.asyncio
async def test_resolve_only_referenced_current_sources_off_the_event_loop(tmp_path):
    from aws_tui.workspace_restore import resolve_workspace_sources

    _registry, services, fakes, connection, workspace = library(tmp_path)
    calls = []
    main_thread = threading.get_ident()

    class Resolver:
        def resolve_selected(self, name):
            calls.append((name, threading.get_ident()))
            return connection

        def list(self):
            raise AssertionError("Do not dereference unrelated credentials")

    try:
        assert await resolve_workspace_sources(workspace, Resolver()) == (connection,)
        assert [name for name, _thread in calls] == [connection.name]
        assert all(thread != main_thread for _name, thread in calls)
    finally:
        dispose_services(services, fakes)


@pytest.mark.parametrize("change", ["missing", "region"])
@pytest.mark.asyncio
async def test_saved_region_is_revalidated_not_silently_overridden(tmp_path, change):
    from aws_tui.workspace_restore import resolve_workspace_sources

    _registry, services, fakes, connection, workspace = library(tmp_path)

    class Resolver:
        def resolve_selected(self, name):
            if change == "missing":
                raise ConnectionNotFound("synthetic-secret-must-not-leak")
            return replace(connection, region="eu-west-1")

    try:
        with pytest.raises(WorkspaceError) as failure:
            await resolve_workspace_sources(workspace, Resolver())
        assert "synthetic-secret" not in str(failure.value)
    finally:
        dispose_services(services, fakes)


@pytest.mark.asyncio
async def test_cancel_source_resolution_drains_its_thread(tmp_path):
    from aws_tui.workspace_restore import resolve_workspace_sources

    _registry, services, fakes, connection, workspace = library(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Resolver:
        def resolve_selected(self, name):
            entered.set()
            try:
                assert release.wait(5)
                return connection
            finally:
                finished.set()

    task = asyncio.create_task(resolve_workspace_sources(workspace, Resolver()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        dispose_services(services, fakes)


@pytest.mark.asyncio
async def test_capture_exports_both_actual_panes_and_all_known_service_scopes(tmp_path):
    from aws_tui.workspace_restore import capture_workspace, retire_workspace_candidate

    registry, services, fakes, connection, workspace = library(tmp_path, active="s3")
    for target in workspace.scopes:
        services[target.service].install_navigation_targets((target,))
    extra = replace(workspace.scopes[0], connection="other", region="eu-west-1")
    services["athena"].install_navigation_targets((extra,))
    stage = services["s3"].build_workspace_vm(workspace.file_panes, (connection,))
    try:
        stage.vm.construct()
        await stage.vm.setup()
        saved = capture_workspace("Saved", registry, "s3", connection, stage.vm)
        assert saved.file_panes == workspace.file_panes
        assert set(saved.scopes) == {*workspace.scopes, extra}
        assert (saved.active_service, saved.connection, saved.region) == (
            "s3",
            connection.name,
            connection.region,
        )
        assert saved.bookmarks == ()
    finally:
        await retire_workspace_candidate(stage.vm)
        dispose_services(services, fakes)


@pytest.mark.asyncio
async def test_capture_does_not_invent_unvisited_scopes(tmp_path):
    from aws_tui.workspace_restore import capture_workspace, retire_workspace_candidate

    registry, services, fakes, connection, workspace = library(tmp_path, active="s3")
    stage = services["s3"].build_workspace_vm(workspace.file_panes, (connection,))
    try:
        stage.vm.construct()
        await stage.vm.setup()
        saved = capture_workspace("Only Files", registry, "s3", connection, stage.vm)
        assert saved.scopes == ()
    finally:
        await retire_workspace_candidate(stage.vm)
        dispose_services(services, fakes)


@pytest.mark.asyncio
async def test_capture_other_service_keeps_only_the_remembered_file_intent(tmp_path):
    from aws_tui.workspace_restore import (
        capture_workspace,
        prepare_workspace,
        retire_workspace_candidate,
    )

    registry, services, fakes, connection, workspace = library(tmp_path)
    prepared = None
    try:
        prepared = await prepare_workspace(workspace, registry, (connection,))
        prepared.recovery.commit_selection()
        captured = capture_workspace(
            "After Files", registry, "glue", connection, prepared.recovery.vm
        )
        assert captured.file_panes == workspace.file_panes
        assert captured.scopes == workspace.scopes
        assert captured.active_service == "glue"
        with pytest.raises(WorkspaceError):
            capture_workspace(
                "Bad source",
                registry,
                "glue",
                replace(connection, name="stale"),
                prepared.recovery.vm,
            )
    finally:
        if prepared is not None:
            await retire_workspace_candidate(prepared.recovery.vm)
        dispose_services(services, fakes)


@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
@pytest.mark.asyncio
async def test_recovered_page_future_selections_remain_known_after_retirement(tmp_path, service_id):
    from aws_tui.workspace_restore import capture_workspace, retire_workspace_candidate

    registry, services, fakes, connection, workspace = library(tmp_path)
    stage = services[service_id].build_recovery_vm(connection)
    page = stage.vm
    current = None
    try:
        page.construct()
        await page.setup()
        stage.commit_selection()
        if service_id == "athena":
            await page.select_workgroup("analysts")
        elif service_id == "glue":
            await page.select_database("lake")
        else:
            await page.select_application("chosen")
        expected = next(target for target in workspace.scopes if target.service == service_id)
        assert expected in services[service_id].navigation_targets()
        stage.commit_selection()
        assert expected in services[service_id].navigation_targets()
        saved = capture_workspace("Current", registry, service_id, connection, page)
        assert expected in saved.scopes
        await retire_workspace_candidate(page)
        # Now capture a different actual service: the retired page's scope must
        # still reflect its last chosen values, rather than recovery defaults.
        other_id = "glue" if service_id != "glue" else "athena"
        current = services[other_id].build_vm(connection)
        current.construct()
        await current.setup()
        saved = capture_workspace("After", registry, other_id, connection, current)
        assert expected in saved.scopes
    finally:
        if current is not None:
            await retire_workspace_candidate(current)
        await retire_workspace_candidate(page)
        dispose_services(services, fakes)


@pytest.mark.asyncio
async def test_repeated_preflight_cancellation_does_not_interrupt_actual_provider_cleanup(
    tmp_path, monkeypatch
):
    from aws_tui.workspace_restore import prepare_workspace

    _registry, services, fakes, connection, workspace = library(tmp_path, active="athena")
    entered = asyncio.Event()
    cleanup = asyncio.Event()
    release = asyncio.Event()
    cleanup_finished = asyncio.Event()
    exited = asyncio.Event()

    async def held_get_table(ref):
        entered.set()
        try:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup.set()
                await release.wait()
                cleanup_finished.set()
                raise
        finally:
            exited.set()

    monkeypatch.setattr(fakes["glue"], "get_table", held_get_table)
    task = asyncio.create_task(prepare_workspace(workspace, _registry, (connection,)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.wait_for(cleanup.wait(), 5)
        task.cancel()
        for _ in range(20):
            await asyncio.sleep(0)
        assert not exited.is_set()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup_finished.is_set()
        assert exited.is_set()
        assert all(
            services[service].navigation_targets() == ()
            for service in ("athena", "glue", "emr-serverless")
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        dispose_services(services, fakes)
