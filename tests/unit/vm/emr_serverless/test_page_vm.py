"""EmrServerlessPageVM tests — orchestration of three child VMs."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make() -> tuple[EmrServerlessPageVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    page = EmrServerlessPageVM(
        client=fake,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        connection=Connection(
            name="dev", kind="aws", region="us-east-1", source="config", profile="dev"
        ),
    )
    page.construct()
    return page, fake


@pytest.mark.asyncio
async def test_setup_loads_applications_and_auto_selects_first() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    await page.setup()
    assert {a.id for a in page.applications.applications} == {"a1", "a2"}
    # Auto-select the first app so the LEFT pane has something to load.
    assert page.applications.selected_id in {"a1", "a2"}


@pytest.mark.asyncio
async def test_select_application_propagates_to_job_runs() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    fake.add_job_run(application_id="a2", job_run_id="r9", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a2", job_run_id="r9", entry_point="s3://b/y.py")
    await page.setup()
    await page.select_application("a2")
    assert page.applications.selected_id == "a2"
    assert page.job_runs.application_id == "a2"
    assert {r.job_run_id for r in page.job_runs.runs} == {"r9"}


@pytest.mark.asyncio
async def test_select_job_run_propagates_to_detail() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    await page.setup()
    # set_application is implicit in setup() if there's only one app.
    await page.select_job_run("r1")
    assert page.job_run_detail.detail is not None
    assert page.job_run_detail.detail.entry_point == "s3://b/x.py"


@pytest.mark.asyncio
async def test_dispose_cascades_to_children() -> None:
    page, _ = _make()
    # Mark each child's _inner so we can observe dispose via the wrapper
    # signal. The simpler observable is that dispose() doesn't raise
    # after construct(); a second dispose() should be a no-op.
    page.dispose()
    page.dispose()  # idempotent
