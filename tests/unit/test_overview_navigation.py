from dataclasses import replace

import pytest

from aws_tui.domain.scoped_overview import OverviewEntry, WatchScope
from aws_tui.overview_navigation import prepare_overview_entry
from aws_tui.vm.service_source_vm import SelectionScope
from aws_tui.vm.services_protocol import ServiceRegistry
from tests.unit.services.test_workspace_navigation import make_service


def route(kind):
    service_id = "glue" if kind.startswith("glue-") else kind
    service, connection, fake, _ = make_service(service_id)
    resource, identifier = "chosen", "chosen-run"
    if kind == "athena":
        resource, identifier = "analysts", "history-analysts"
    elif kind == "glue-job":
        fake.add_run("chosen", "chosen-run")
        fake.job_page_size = fake.run_page_size = 1
        selection = SelectionScope("glue", connection.name, connection.region)
        service._selections.set(selection, "active_view", "catalog")
        service._selections.set(selection, "job_run_states", "FAILED")
    elif kind == "glue-crawler":
        fake.add_crawler("chosen")
        identifier = resource
    else:
        fake.add_job_run(application_id="chosen", job_run_id="chosen-run")
        fake.add_job_run_detail(application_id="chosen", job_run_id="chosen-run")
    registry = ServiceRegistry()
    registry.register(service)
    return (
        registry,
        service,
        connection,
        fake,
        OverviewEntry(
            WatchScope(kind, connection.name, connection.region, resource), identifier, "FAILED"
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["athena", "glue-job", "glue-crawler", "emr-serverless"])
async def test_exact_overview_entry_prepares_existing_page_without_execution(kind):
    registry, service, connection, fake, entry = route(kind)
    before = service.navigation_targets()
    prepared = await prepare_overview_entry(entry, connection, registry)
    page = prepared.recovery.vm
    try:
        assert service.navigation_targets() == before
        assert page.source.connection_key == (connection.name, connection.region)
        if kind == "athena":
            assert page.active_view == "history"
            assert page.context.workgroup == "analysts"
            assert page.history.selected_execution_id == entry.identifier
            assert fake.start_calls == []
            assert fake.stop_calls == []
            assert fake.result_calls == []
        elif kind == "glue-job":
            assert page.active_view == "jobs"
            assert page.jobs.selected_job_name == entry.scope.resource
            assert page.jobs.selected_run_id == entry.identifier
            assert page.jobs.run_state_filter == frozenset()
            assert fake.database_tokens == []
        elif kind == "glue-crawler":
            assert page.active_view == "crawlers"
            assert page.crawlers.selected_crawler_name == entry.identifier
            assert fake.database_tokens == []
        else:
            assert page.applications.selected_id == entry.scope.resource
            assert page.job_runs.selected_id == entry.identifier
            assert page.job_run_detail.detail.job_run_id == entry.identifier
            assert all(
                name not in {"start_job_run", "cancel_job_run", "list_files", "stream"}
                for name, _ in fake.calls
            )
    finally:
        await page.shutdown()
        page.dispose()
        if kind == "emr-serverless":
            fake.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["athena", "glue-job", "glue-crawler", "emr-serverless"])
async def test_removed_entry_does_not_succeed_with_a_default(kind):
    registry, service, connection, fake, entry = route(kind)
    before = service.navigation_targets()
    entry = replace(entry, identifier="removed")
    with pytest.raises(ValueError, match="unavailable"):
        await prepare_overview_entry(entry, connection, registry)
    assert service.navigation_targets() == before
    if kind == "emr-serverless":
        fake.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["athena", "glue-job", "glue-crawler", "emr-serverless"])
async def test_wrong_current_source_is_refused_before_client_setup(kind):
    registry, _service, connection, fake, entry = route(kind)
    with pytest.raises(ValueError, match="source"):
        await prepare_overview_entry(entry, replace(connection, region="eu-west-1"), registry)
    if kind == "athena":
        assert fake.workgroup_calls == []
    elif kind.startswith("glue-"):
        assert fake.database_tokens == []
    else:
        assert not fake.calls
        fake.dispose()
