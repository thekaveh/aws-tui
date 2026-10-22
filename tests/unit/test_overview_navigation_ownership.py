import asyncio
from dataclasses import replace

import pytest

import aws_tui.overview_navigation as navigation
from aws_tui.overview_navigation import prepare_overview_entry
from aws_tui.workspace_restore import retire_workspace_candidate
from tests.unit.test_overview_navigation import route


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["glue-job", "glue-crawler"])
async def test_preflight_refuses_foreign_selected_detail_identity(kind):
    registry, _service, connection, fake, entry = route(kind)
    prepared = None
    if kind == "glue-job":
        fake.runs["chosen"][0] = replace(fake.runs["chosen"][0], job_name="foreign-job")
    else:
        detail = fake.crawler_details["chosen"]
        fake.crawler_details["chosen"] = replace(
            detail, summary=replace(detail.summary, name="foreign-crawler")
        )
    try:
        try:
            prepared = await prepare_overview_entry(entry, connection, registry)
        except ValueError:
            return
        pytest.fail("preflight accepted foreign service detail under the requested selection label")
    finally:
        if prepared is not None:
            await retire_workspace_candidate(prepared.recovery.vm)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["glue-job", "glue-crawler"])
async def test_later_page_exact_resource_and_run_uses_no_catalog(kind):
    registry, service, connection, fake, entry = route(kind)
    if kind == "glue-job":
        fake.add_run("a-first", "other-run")
        fake.jobs.sort(key=lambda x: x.name)
        fake.add_run("chosen", "first-run")
        fake.runs["chosen"] = list(reversed(fake.runs["chosen"]))
        fake.job_page_size = fake.run_page_size = 1
    else:
        fake.add_crawler("a-first")
        fake.crawlers.sort(key=lambda x: x.name)
        fake.crawler_page_size = 1
    before = service.navigation_targets()
    prepared = await prepare_overview_entry(entry, connection, registry)
    try:
        page = prepared.recovery.vm
        assert service.navigation_targets() == before
        assert fake.database_tokens == []
        assert fake.table_requests == []
        if kind == "glue-job":
            assert fake.job_tokens == [None, "1"]
            assert ("chosen", "1", ()) in fake.run_requests
            assert page.jobs.selected_job_name == "chosen"
            assert page.jobs.selected_run_id == "chosen-run"
        else:
            assert fake.crawler_requests == [(None, None), ("1", None)]
            assert page.crawlers.selected_crawler_name == "chosen"
            assert page.crawlers.crawler_detail.summary.name == "chosen"
    finally:
        await retire_workspace_candidate(prepared.recovery.vm)


@pytest.mark.asyncio
async def test_repeated_outer_cancel_drains_actual_provider_cleanup():
    registry, service, connection, fake, entry = route("glue-crawler")
    original = fake.get_crawler
    started, canceled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleanup_completed = False

    async def read(name):
        nonlocal cleanup_completed
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            canceled.set()
            await release.wait()
            cleanup_completed = True
        return await original(name)

    fake.get_crawler = read
    owner = asyncio.create_task(prepare_overview_entry(entry, connection, registry))
    try:
        await asyncio.wait_for(started.wait(), 1)
        owner.cancel()
        await asyncio.wait_for(canceled.wait(), 1)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert cleanup_completed
        assert service.navigation_targets() == ()
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_winning_success_delivery_retires_unreceived_candidate(monkeypatch):
    registry, service, connection, _fake, entry = route("glue-crawler")
    original = navigation._prepare
    prepared = []
    owner = None

    async def controlled(*args):
        result = await original(*args)
        prepared.append(result)
        asyncio.get_running_loop().call_soon(owner.cancel)
        return result

    monkeypatch.setattr(navigation, "_prepare", controlled)
    owner = asyncio.create_task(prepare_overview_entry(entry, connection, registry))
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert len(prepared) == 1
    assert prepared[0].recovery.vm._shutdown_complete
    assert prepared[0].recovery.vm._disposed
    assert service.navigation_targets() == ()


@pytest.mark.asyncio
async def test_public_lookup_preserves_existing_finite_64_load_more_bound():
    from aws_tui.vm.file_manager.pane_vm import PaneState
    from aws_tui.vm.workspace_navigation import WorkspaceNavigationError, find_navigation_value

    calls = 0

    async def load():
        nonlocal calls
        calls += 1

    with pytest.raises(WorkspaceNavigationError):
        await find_navigation_value(
            "missing",
            names=lambda: (),
            state=lambda: PaneState.EMPTY,
            more=lambda: True,
            limited=lambda: False,
            load_more=load,
        )
    assert calls == 64


@pytest.mark.asyncio
async def test_athena_preflight_refuses_foreign_history_source_identity():
    registry, _service, connection, fake, entry = route("athena")
    original_list = fake.list_query_executions_page
    original_get = fake.get_query_execution

    async def list_history(*args, **kwargs):
        rows, token = await original_list(*args, **kwargs)
        return [replace(row, connection_name="foreign", region="eu-west-1") for row in rows], token

    async def get_history(identity):
        detail = await original_get(identity)
        ref = replace(detail.summary.ref, connection_name="foreign", region="eu-west-1")
        return replace(
            detail,
            summary=replace(detail.summary, ref=ref),
            context=replace(detail.context, connection_name="foreign", region="eu-west-1"),
        )

    fake.list_query_executions_page = list_history
    fake.get_query_execution = get_history
    prepared = None
    try:
        try:
            prepared = await prepare_overview_entry(entry, connection, registry)
        except ValueError:
            return
        pytest.fail("Athena exact-ID preflight accepted a foreign-source history detail")
    finally:
        if prepared is not None:
            await retire_workspace_candidate(prepared.recovery.vm)
