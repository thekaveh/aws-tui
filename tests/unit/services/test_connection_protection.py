from types import SimpleNamespace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.filesystem import PathRef, PermissionDeniedError
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena.service import AthenaService
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.services.glue.service import GlueService
from aws_tui.services.s3.service import S3Service
from tests.unit.vm.athena.test_page_vm import PageClient
from tests.unit.vm.glue._fake_glue import seeded_glue


def connection():
    return Connection(
        name="protected",
        kind="aws",
        profile="synthetic",
        region="us-east-1",
        source="config",
        protected=True,
    )


@pytest.mark.asyncio
async def test_protected_s3_factory_never_receives_mutation(tmp_path):
    calls = []

    async def mkdir(path):
        calls.append(path)

    service = S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        s3_fs_factory=lambda c: SimpleNamespace(mkdir=mkdir),
    )
    provider = service.build_remote_provider(connection())
    with pytest.raises(PermissionDeniedError, match="protected"):
        await provider.mkdir(PathRef(("bucket", "folder")))
    assert calls == []


@pytest.mark.asyncio
async def test_protected_athena_and_emr_factory_initiation_is_blocked():
    calls = []

    async def start(*args, **kwargs):
        calls.append("start")
        return "synthetic"

    athena = AthenaService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        athena_client_factory=lambda c: PageClient(connection_name=c.name, region=c.region),
    )
    page = athena.build_vm(connection())
    with pytest.raises(PermissionDeniedError, match="protected"):
        await page.client.start_query("SELECT 1", None, request_token="synthetic")
    emr = EmrServerlessService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        emr_client_factory=lambda c: SimpleNamespace(start_job_run=start),
        emr_logs_client_factory=lambda c: object(),
    )
    page = emr.build_vm(connection())
    with pytest.raises(PermissionDeniedError, match="protected"):
        await page.client.start_job_run(
            "application",
            execution_role_arn="synthetic",
            entry_point="synthetic",
            entry_point_arguments=None,
            spark_submit_parameters=None,
            client_token="synthetic",
        )
    assert calls == []


@pytest.mark.asyncio
async def test_glue_athena_inspection_uses_protected_client():
    calls = []

    async def start(*args, **kwargs):
        calls.append("start")

    service = GlueService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=lambda c: seeded_glue(),
        athena_client_factory=lambda c: SimpleNamespace(start_query=start),
    )
    page = service.build_vm(connection())
    inspector = page.catalog.iceberg._inspector
    with pytest.raises(PermissionDeniedError, match="protected"):
        await inspector._runner.client.start_query("SELECT 1", None, request_token="synthetic")
    assert calls == []


@pytest.mark.asyncio
async def test_protected_glue_metadata_remains_readable_but_inspection_never_starts():
    from dataclasses import replace

    from aws_tui.domain.mutation_guard import PROTECTED_MESSAGE
    from aws_tui.vm.file_manager.pane_vm import PaneState
    from tests.unit.services.glue.test_service import _iceberg_glue, _IcebergAthena

    athena = _IcebergAthena(("primary",))
    service = GlueService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        glue_client_factory=lambda c: _iceberg_glue(),
        athena_client_factory=lambda c: athena,
    )
    page = service.build_vm(replace(connection(), name="dev"))
    page.construct()
    try:
        await page.setup()
        assert page.catalog.table_detail is not None
        await page.catalog.iceberg.select_view("snapshots")
        assert athena.workgroup_calls == 1
        assert athena.start_calls == 0
        assert page.catalog.iceberg.state == PaneState.FORBIDDEN
        assert page.catalog.iceberg.error_text == PROTECTED_MESSAGE
    finally:
        page.dispose()


@pytest.mark.asyncio
async def test_protected_clients_keep_read_and_explicit_cancellation_calls():
    calls = []

    async def cancel(*args, **kwargs):
        calls.append("cancel")

    async def read(*args, **kwargs):
        calls.append("read")
        return "metadata"

    async def start(*args, **kwargs):
        raise AssertionError("mutation reached provider")

    emr = EmrServerlessService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        emr_client_factory=lambda c: SimpleNamespace(
            start_job_run=start, cancel_job_run=cancel, get_job_run=read
        ),
        emr_logs_client_factory=lambda c: object(),
    )
    page = emr.build_vm(connection())
    assert await page.client.get_job_run("application", "run") == "metadata"
    await page.client.cancel_job_run("application", "run")
    athena = AthenaService(
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        athena_client_factory=lambda c: PageClient(connection_name=c.name, region=c.region),
    )
    page = athena.build_vm(connection())
    await page.client.get_results_page("existing-query")
    await page.client.stop_query("existing-query")
    assert calls == ["read", "cancel"]
