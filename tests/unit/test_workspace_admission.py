"""Independent admission review: in-memory providers only; no repo mutations."""

import asyncio
import contextlib
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from textual.app import App

from aws_tui.app import AwsTuiApp
from aws_tui.domain.data_catalog import TableFormat
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import ProviderError
from aws_tui.domain.query import QueryState
from aws_tui.infra.duckdb import InMemoryDuckDb
from aws_tui.services.s3 import S3Service
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.vm.athena.test_query_vm import make_query_vm, seeded_athena
from tests.unit.vm.emr_serverless.test_page_vm import _make as make_emr
from tests.unit.vm.glue._fake_glue import InMemoryGlue
from tests.unit.vm.glue.test_iceberg_preview_vm import _GatedPort
from tests.unit.vm.glue.test_iceberg_vm import ICEBERG_REF, RecordingInspector
from tests.unit.vm.glue.test_page_vm import make_page_vm


def admission(current=None, *, workers=(), service=None):
    shell = SimpleNamespace(
        _app_ctx=SimpleNamespace(
            root_vm=SimpleNamespace(content_host=SimpleNamespace(current=current)),
            registry=SimpleNamespace(get=lambda _key: service),
        ),
        workers=workers,
    )
    return AwsTuiApp._workspace_has_active_work(shell)


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", ["clear", "silent-drain"])
async def test_metadata_ownership_survives_nonloading_state(retire):
    inspector = RecordingInspector()
    inspector.ignore_cancellation_for = "snapshots"
    page = make_page_vm(InMemoryGlue(), iceberg_inspector=inspector)
    vm = page.catalog.iceberg
    loading = draining = None
    try:
        await vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
        started = inspector.block("snapshots")
        loading = asyncio.create_task(vm.select_view("snapshots"))
        await bounded(started.wait())
        assert admission(page)
        if retire == "clear":
            vm.clear_table()
        else:
            draining = asyncio.create_task(vm.cancel_metadata_loads_and_drain_silently())
        await bounded(inspector.cancellation_seen.wait())
        assert not loading.done(), "accepted provider work is still draining"
        assert all(
            vm.state_for(view) is not PaneState.LOADING
            for view in ("snapshots", "history", "manifests", "files", "partitions", "refs")
        )
        assert admission(page), "Open must refuse while accepted metadata cleanup is owned"
    finally:
        if "snapshots" in inspector.blocked:
            inspector.release("snapshots")
        for task in (loading, draining):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await bounded(task)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
async def test_preview_ownership_survives_resource_rebinding():
    port = _GatedPort(rows=(("held",),))
    page = make_page_vm(InMemoryGlue(), duckdb_port=port)
    vm = page.catalog.iceberg
    task = None
    try:
        await vm.bind_table(
            ICEBERG_REF, table_format=TableFormat.ICEBERG, location="s3://fake/table"
        )
        task = asyncio.create_task(vm.preview.load())
        assert await asyncio.to_thread(port.entered.wait, 5)
        assert admission(page)
        # Real parent-selection reset replaces the resource before thread cleanup ends.
        vm.clear_table()
        assert vm.preview.state is PaneState.EMPTY
        assert not task.done(), "accepted engine query has not completed its thread drain"
        assert admission(page), "Open must refuse while the retired preview still owns a query"
    finally:
        port.release()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await bounded(task)
        await page.shutdown()
        page.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group", ["glue-iceberg-select-view", "glue-iceberg-retry", "glue-iceberg-preview"]
)
async def test_accepted_glue_worker_blocks_before_vm_loading(group):
    page = make_page_vm(
        InMemoryGlue(), iceberg_inspector=RecordingInspector(), duckdb_port=InMemoryDuckDb()
    )
    host = App()
    try:
        async with host.run_test():
            vm = page.catalog.iceberg
            await vm.bind_table(
                ICEBERG_REF, table_format=TableFormat.ICEBERG, location="s3://fake/table"
            )
            operation = {
                "glue-iceberg-select-view": partial(vm.select_view, "snapshots"),
                "glue-iceberg-retry": vm.retry,
                "glue-iceberg-preview": vm.preview.ensure_loaded,
            }[group]
            worker = host.run_worker(operation, group=group)
            assert not worker.is_finished
            assert page.catalog.iceberg.state_for("snapshots") is PaneState.EMPTY
            assert admission(page, workers=host.workers), (
                "queued accepted Glue worker must block Open"
            )
    finally:
        await page.shutdown()
        page.dispose()


@pytest.mark.parametrize(
    "group", ["transfer-copy", "transfer-delete", "emr-clone", "emr-clone-submit", "emr-cancel"]
)
def test_existing_worker_groups_are_admitted(group):
    assert admission(workers=(SimpleNamespace(group=group, is_finished=False),))
    assert not admission(workers=(SimpleNamespace(group=group, is_finished=True),))


@pytest.mark.parametrize(("pending", "ids"), [(True, set()), (False, {"held-transfer"})])
def test_s3_runtime_public_pending_and_ownership_are_admitted(pending, ids):
    service = Mock(spec=S3Service)
    service.transfer_runtime = SimpleNamespace(begins_pending=pending, owned_ids=ids)
    assert admission(service=service)


@pytest.mark.asyncio
async def test_athena_poll_error_keeps_ownership_and_blocks_open():
    class PollFailure(type(seeded_athena([QueryState.RUNNING]))):
        async def get_query_execution(self, execution_id):
            raise ProviderError("fixture status read failure")

    base = seeded_athena([QueryState.RUNNING])
    fake = PollFailure(executions=base.executions)
    query = make_query_vm(fake)
    current = Mock(spec=AthenaPageVM)
    current.query = query
    try:
        query.set_sql("SELECT 1")
        await query.execute()
        assert not query.is_executing
        assert not query.is_submitting
        assert query.pane_state is PaneState.ERROR
        assert query.owns_active_query
        assert admission(current)
    finally:
        await query.shutdown()
        query.dispose()


@pytest.mark.asyncio
async def test_remote_observed_emr_running_is_not_owned_work():
    page, fake = make_emr()
    try:
        fake.add_application(app_id="a1", name="etl")
        fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
        fake.add_job_run_detail(application_id="a1", job_run_id="r1")
        await page.setup()
        assert page.job_runs.runs[0].state is JobRunState.RUNNING
        assert not page.cancel_busy
        assert not admission(page)
    finally:
        await page.shutdown()
        page.dispose()
        await fake.aclose()


@pytest.mark.asyncio
async def test_emr_accepted_cancel_request_is_owned_until_provider_returns():
    from tests.unit.vm.emr_serverless.test_cancel_job_run import RecordingEmr

    fake = RecordingEmr()
    fake.release_request.clear()
    page, _ = make_emr(fake=fake)
    cancellation = None
    try:
        fake.add_application(app_id="a1", name="etl")
        fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
        fake.add_job_run_detail(application_id="a1", job_run_id="r1")
        await page.setup()

        async def confirm(_request):
            return True

        cancellation = asyncio.create_task(page.cancel_selected_run(confirm))
        await bounded(fake.request_started.wait())
        assert page.cancel_busy
        assert admission(page)
        fake.release_request.set()
        await bounded(cancellation)
        assert not page.cancel_busy
        assert not admission(page)
    finally:
        fake.release_request.set()
        if cancellation is not None:
            await bounded(cancellation)
        await page.shutdown()
        page.dispose()
        await fake.aclose()
