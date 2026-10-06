from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena import AthenaService
from aws_tui.vm.services_protocol import ServiceDescriptor
from tests.unit.vm.athena.test_page_vm import PageClient


def _connection(
    name: str,
    *,
    kind: str = "aws",
    region: str = "us-east-1",
) -> Connection:
    return Connection(
        name=name,
        kind=kind,
        region=region,
        source="test",
        profile=name if kind == "aws" else None,
        endpoint_url="http://localhost:9000" if kind != "aws" else None,
        access_key_id="key" if kind != "aws" else None,
        secret_access_key="secret" if kind != "aws" else None,
    )


def _service(**kwargs: object) -> AthenaService:
    hub: MessageHub[Message] = MessageHub()
    return AthenaService(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        aws_session=AwsSession(),
        **kwargs,  # type: ignore[arg-type]
    )


def test_athena_service_is_aws_only() -> None:
    service = _service()

    assert service.supports(_connection("dev"))
    assert not service.supports(_connection("minio", kind="s3-compatible"))


def test_athena_service_descriptor_matches_navigation_contract() -> None:
    descriptor = AthenaService.descriptor

    assert descriptor == ServiceDescriptor(
        id="athena",
        label="Athena",
        icon=descriptor.icon,
    )
    assert descriptor.icon


@pytest.mark.asyncio
async def test_service_owns_selections_but_builds_disposable_page_dependencies() -> None:
    clients: list[PageClient] = []
    policies: list[ReadOnlySqlPolicy] = []

    def client_factory(connection: Connection) -> PageClient:
        client = PageClient(
            connection_name=connection.name,
            region=connection.region,
        )
        clients.append(client)
        return client

    def policy_factory() -> ReadOnlySqlPolicy:
        policy = ReadOnlySqlPolicy()
        policies.append(policy)
        return policy

    service = _service(
        athena_client_factory=client_factory,
        sql_policy_factory=policy_factory,
    )
    connection = _connection("analytics", region="us-west-2")

    first = service.build_vm(connection)
    first.construct()
    await first.setup()
    await first.select_view("saved")
    await first.shutdown()
    first.dispose()

    replacement = service.build_vm(connection)
    replacement.construct()
    await replacement.setup()

    assert replacement.active_view == "saved"
    assert replacement.client is clients[1]
    assert first.query.runner.client is clients[0]
    assert replacement.query.runner.client is clients[1]
    assert first.query.runner is not replacement.query.runner
    assert len(clients) == 2
    assert len(policies) == 2
    assert policies[0] is not policies[1]

    await replacement.shutdown()
    replacement.dispose()


@pytest.mark.asyncio
async def test_staged_draft_editor_writes_only_after_commit_and_next_edit(tmp_path) -> None:
    from tests.athena_drafts_helpers import runtime_at
    from tests.helpers import wait_until

    runtime, store = runtime_at(tmp_path)
    checks = []

    def factory(connection):
        async def check():
            checks.append(connection.name)
            return True

        return check

    service = _service(
        drafts=runtime,
        source_check_factory=factory,
        athena_client_factory=lambda c: PageClient(connection_name=c.name, region=c.region),
    )
    candidate = service.build_recovery_vm(_connection("analytics", region="us-west-2"))
    page = candidate.vm
    await page.setup()
    page.query.set_sql("SELECT 'STAGED'")
    await page.shutdown()
    assert store.list().records == ()
    page.dispose()
    candidate = service.build_recovery_vm(_connection("analytics", region="us-west-2"))
    page = candidate.vm
    await page.setup()
    from tests.unit.vm.athena.test_page_vm import make_page_vm

    source = make_page_vm(PageClient())
    await source.setup()
    source.query.set_sql("SELECT 'SNAPSHOT'")
    snapshot = source.export_snapshot()
    await page.restore_snapshot(snapshot)
    assert page.query.sql == "SELECT 'SNAPSHOT'"
    assert store.list().records == ()
    await source.shutdown()
    candidate.commit_selection()
    assert runtime._worker._thread is None
    page.query.set_sql("SELECT 'NEXT_EDIT'")
    await wait_until(lambda: page.query.draft_state == "saved", what="active edited draft")
    assert store.list().records[0].sql == "SELECT 'NEXT_EDIT'"
    assert checks == ["analytics", "analytics"]
    await runtime.shutdown()
    await page.shutdown()


@pytest.mark.asyncio
async def test_old_page_flush_cannot_overwrite_new_active_page_edit(tmp_path) -> None:
    from tests.athena_drafts_helpers import runtime_at

    runtime, store = runtime_at(tmp_path)

    async def check():
        return True

    service = _service(
        drafts=runtime,
        source_check_factory=lambda c: check,
        athena_client_factory=lambda c: PageClient(connection_name=c.name, region=c.region),
    )
    connection = _connection("analytics", region="us-west-2")
    old = service.build_vm(connection)
    await old.setup()
    old.query.set_sql("SELECT 'OLD'")
    replacement = service.build_vm(connection)
    await replacement.setup()
    replacement.query.set_sql("SELECT 'NEW'")
    await old.shutdown()
    await runtime.shutdown()
    await replacement.shutdown()
    assert store.list().records[0].sql == "SELECT 'NEW'"


@pytest.mark.asyncio
async def test_shutdown_with_only_staged_editor_has_no_draft_writes(tmp_path):
    from tests.athena_drafts_helpers import runtime_at

    runtime, store = runtime_at(tmp_path)

    async def check():
        return True

    service = _service(
        drafts=runtime,
        source_check_factory=lambda c: check,
        athena_client_factory=lambda c: PageClient(connection_name=c.name, region=c.region),
    )
    candidate = service.build_recovery_vm(_connection("analytics", region="us-west-2"))
    await candidate.vm.setup()
    candidate.vm.query.set_sql("SELECT 'SPECULATIVE'")
    assert (await runtime.shutdown()).unpersisted == 0
    await candidate.vm.shutdown()
    assert store.list().records == ()
    assert runtime._worker._thread is None


@pytest.mark.asyncio
async def test_switching_source_keeps_each_editor_under_its_original_id(tmp_path):
    from tests.athena_drafts_helpers import runtime_at

    runtime, store = runtime_at(tmp_path)

    async def check():
        return True

    service = _service(
        drafts=runtime,
        source_check_factory=lambda c: check,
        athena_client_factory=lambda c: PageClient(connection_name=c.name, region=c.region),
    )
    old = service.build_vm(_connection("analytics", region="us-west-2"))
    await old.setup()
    old.query.set_sql("SELECT 'ORIGINAL'")
    newer = service.build_vm(_connection("other", region="us-west-2"))
    await newer.setup()
    newer.query.set_sql("SELECT 'OTHER'")
    await runtime.shutdown()
    await old.shutdown()
    await newer.shutdown()
    records = {r.context[0]: r for r in store.list().records}
    assert records["analytics"].sql == "SELECT 'ORIGINAL'"
    assert records["other"].sql == "SELECT 'OTHER'"
    assert records["analytics"].id != records["other"].id
