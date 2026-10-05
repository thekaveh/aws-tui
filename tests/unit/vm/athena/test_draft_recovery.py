from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import fields, replace
from datetime import UTC, datetime

import pytest

from aws_tui.domain.query import QueryContext
from aws_tui.infra.athena_draft_store import DraftPermit, DraftStoreResult
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.infra.log_sink import LogSink
from tests.athena_drafts_helpers import record, runtime_at
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

WARNING = "Draft context is unavailable or changed. Select the exact original context and retry."


async def yes():
    return True


async def no():
    return False


async def seeded(tmp_path, monkeypatch, *, unsaved=False):
    runtime, store = runtime_at(tmp_path)
    client = PageClient()
    page = make_page_vm(client, drafts=runtime, source_is_current=yes)
    await page.setup()
    # Select the actual recorded context explicitly, without recovery fallback.
    await page.select_workgroup("primary")
    await page.select_catalog("AwsDataCatalog")
    await page.select_database("default")
    saved = replace(
        record(context=page.context.cache_key, sql="SELECT 'RECOVERED'"),
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    assert store.save(saved, permit=DraftPermit()).code is None
    if unsaved:
        # Let listing and provider journeys exceed debounce without overwriting target.
        monkeypatch.setattr(store, "save", lambda record, *, permit: DraftStoreResult(code="io"))
        page.query.set_sql("SELECT 'UNSAVED'")
    return page, runtime, store, client, saved


async def close(page, runtime):
    await page.shutdown()
    page.dispose()
    await runtime.shutdown()
    runtime.dispose()


async def assert_stale(page, client):
    assert page.draft_recovery_error == WARNING
    assert page.query.draft_execution_blocked
    assert not page.query.execute_command.can_execute()
    await page.query.execute_command.execute_async()
    assert client.start_calls == []


async def test_restore_prompts_for_vm_seeded_unsaved_text(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch, unsaved=True)
    asked = 0

    async def decline():
        nonlocal asked
        asked += 1
        return False

    assert not await page.restore_draft(saved.id, decline)
    assert asked == 1
    assert page.query.sql == "SELECT 'UNSAVED'"
    assert page.query.draft_state in ("pending", "error")
    assert not page.query.draft_recovery_guard
    assert page.draft_recovery_error is None
    assert client.start_calls == []
    await close(page, runtime)


async def test_restore_installs_only_sql_and_binds_origin(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch, unsaved=True)
    await page.select_view("history")
    assert await page.restore_draft(saved.id, yes)
    assert page.query.sql == saved.sql
    assert page.query.draft_state == "saved"
    assert page.active_view == "query"
    assert page.results.rows == ()
    assert page.query.execution_ref is None
    assert client.start_calls == []
    await page.select_workgroup("analysts")
    assert page.query.draft_execution_blocked
    page.keep_current_editor()
    assert page.query.draft_execution_blocked
    await page.query.execute_command.execute_async()
    assert client.start_calls == []
    await page.select_workgroup("primary")
    assert not page.query.draft_execution_blocked
    await page.select_workgroup("analysts")
    page.query.set_sql("")
    assert not page.query.draft_execution_blocked
    await close(page, runtime)


@pytest.mark.parametrize(
    "change", ["editor", "context", "delete", "record", "disable", "database", "shutdown"]
)
async def test_confirmation_races_refuse_replacement(tmp_path, monkeypatch, change):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch, unsaved=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def confirm():
        entered.set()
        await release.wait()
        return True

    task = asyncio.create_task(page.restore_draft(saved.id, confirm))
    await entered.wait()
    assert page.query.draft_recovery_guard
    if change == "editor":
        page.query.set_sql("SELECT 'NEWER'")
    elif change == "context":
        await page.select_workgroup("analysts")
    elif change == "delete":
        assert await runtime.delete(saved.id)
    elif change == "record":
        path = runtime.directory / f"{saved.id}.json"
        body = json.loads(path.read_text())
        body["sql"] = "SELECT 'CHANGED'"
        path.write_text(json.dumps(body))
    elif change == "disable":
        assert await runtime.set_enabled(False)
    elif change == "database":
        client.databases[("primary", "AwsDataCatalog")] = []
    elif change == "shutdown":
        await page.shutdown()
    release.set()
    assert not await task
    assert page.query.sql == ("SELECT 'NEWER'" if change == "editor" else "SELECT 'UNSAVED'")
    assert client.start_calls == []
    if change not in ("disable", "shutdown"):
        await assert_stale(page, client)
    await close(page, runtime)


@pytest.mark.parametrize("change", ["database", "source", "missing", "busy", "incomplete"])
async def test_stale_cached_context_and_ineligible_recovery(tmp_path, monkeypatch, change):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch)
    page.query.set_sql("SELECT 1")
    if change == "database":
        client.databases[("primary", "AwsDataCatalog")] = []
    elif change == "source":
        page._source_is_current = no
    elif change == "missing":
        saved = replace(saved, id="0" * 64)
    elif change == "busy":
        page.query._busy = True
    else:
        await page.query.set_context(QueryContext("analytics", "us-west-2", "", "", ""))
    assert not await page.restore_draft(saved.id, yes)
    assert client.start_calls == []
    if change != "busy":
        await assert_stale(page, client)
        page.keep_current_editor()
        assert page.draft_recovery_error is None
    page.query._busy = False
    await close(page, runtime)


async def test_shutdown_during_validation_never_installs(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch)
    client.block_workgroup_detail_for = "primary"
    task = asyncio.create_task(page.restore_draft(saved.id, yes))
    await client.workgroup_detail_started.wait()
    await page.shutdown()
    client.release_workgroup_detail.set()
    assert not await task
    assert page.query.sql == ""
    assert client.start_calls == []
    await close(page, runtime)


async def test_cancelled_recovery_restores_prior_guard(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch)
    client.block_workgroup_detail_for = "primary"
    task = asyncio.create_task(page.restore_draft(saved.id, yes))
    await client.workgroup_detail_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not page.query.draft_recovery_guard
    assert page.draft_recovery_error is None
    assert client.start_calls == []
    await close(page, runtime)


@pytest.mark.parametrize(
    "change", ["source", "provider", "editor", "context", "shutdown", "disable"]
)
async def test_direct_execution_fresh_preflight_and_revision_races(tmp_path, monkeypatch, change):
    page, runtime, _, client, _ = await seeded(tmp_path, monkeypatch)
    page.query.set_sql("SELECT 1")
    if change == "source":
        page._source_is_current = no
    elif change == "provider":
        client.databases[("primary", "AwsDataCatalog")] = []
    else:
        entered, release = asyncio.Event(), asyncio.Event()

        async def check():
            entered.set()
            await release.wait()
            return True

        page._source_is_current = check
        task = asyncio.create_task(page.query.execute_command.execute_async())
        await entered.wait()
        if change == "editor":
            page.query.set_sql("SELECT 2")
        elif change == "context":
            # set_context cancels the command; release the provider gate afterwards.
            await page.query.set_context(replace(page.context, database="other"))
        elif change == "shutdown":
            await page.query.shutdown()
        else:
            assert await runtime.set_enabled(False)
        release.set()
        await task
    if change in ("source", "provider"):
        await page.query.execute_command.execute_async()
        assert page.query.validation_error == "Draft context is unavailable or changed."
    assert client.start_calls == []
    await close(page, runtime)


async def test_query_shutdown_flushes_query_context_after_page_clears_its_context(
    tmp_path, monkeypatch
):
    page, runtime, store, _, _ = await seeded(tmp_path, monkeypatch)
    page.query.set_sql("SELECT 'FINAL'")
    context = page.query.context
    await page.shutdown()
    assert not all(page.context.cache_key)
    assert store.list().records[0].context == context.cache_key
    assert store.list().records[0].sql == "SELECT 'FINAL'"
    await close(page, runtime)


async def test_context_validation_bounded_exact_and_private(tmp_path):
    from aws_tui.vm.athena.draft_recovery import validate_draft_context

    context = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
    client = PageClient()
    assert await validate_draft_context(context=context, client=client, source_is_current=yes)
    client.workgroups += client.workgroups
    assert not await validate_draft_context(context=context, client=client, source_is_current=yes)


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "malformed",
        "utf8",
        "surrogate",
        "unsupported",
        "hostile-context",
        "over-limit",
        "oserror",
    ],
)
async def test_draft_diagnostics_never_include_payload(tmp_path, monkeypatch, case):
    page, runtime, store, client, saved = await seeded(tmp_path, monkeypatch)
    sentinel = "DRAFT_SQL_SENTINEL"
    path = runtime.directory / f"{saved.id}.json"
    if case == "valid":
        result = store.save(replace(saved, sql=f"SELECT '{sentinel}'"), permit=DraftPermit())
        assert result.code is None
    elif case == "over-limit":
        result = store.save(replace(saved, sql=sentinel + "x" * 262_144), permit=DraftPermit())
        assert result.code == "limit"
    elif case == "oserror":

        def fail():
            raise OSError("DRAFT_SQL_SENTINEL")

        monkeypatch.setattr(store, "list", fail)
    else:
        body = json.loads(path.read_text())
        body["sql"] = sentinel
        if case == "malformed":
            path.write_bytes(b'{"sql":"DRAFT_SQL_SENTINEL"')
        elif case == "utf8":
            path.write_bytes(b"\xffDRAFT_SQL_SENTINEL")
        else:
            if case == "surrogate":
                body["sql"] += "\ud800"
            elif case == "unsupported":
                body["schema_version"] = 2
            else:
                body["context"]["database"] = sentinel + "\ud800"
            path.write_text(json.dumps(body))
    futures = []
    submit = runtime._worker.submit

    def capture_future(operation):
        future = submit(operation)
        futures.append(future)
        return future

    monkeypatch.setattr(runtime._worker, "submit", capture_future)
    accepted = await page.restore_draft(saved.id, yes)
    assert futures
    assert all(future.done() and future.exception() is None for future in futures)
    assert accepted == (case in ("valid", "over-limit"))
    if case == "valid":
        assert sentinel in page.query.sql  # The editor intentionally contains SQL.
    assert client.start_calls == []
    texts = [
        str(saved),
        repr(saved),
        str(runtime.items),
        repr(runtime.items),
        str(page._draft_session),
        repr(page._draft_session),
        repr(page._draft_session._capture),
        repr(page.query.export_snapshot()),
        repr(page.export_snapshot()),
        str(page.draft_recovery_error),
        str(page.query.draft_error_text),
    ]
    texts += [repr(future.result()) for future in futures]
    if case in ("valid", "over-limit"):
        texts += [str(result), repr(result)]
        assert not any(isinstance(getattr(result, f.name), BaseException) for f in fields(result))
    error, trace, crash, log = diagnostic_artifacts(page, tmp_path)
    texts += [str(error), repr(error), trace, crash, log]
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        texts += [str(current), repr(current), repr(getattr(current, "__notes__", ()))]
        pending += [e for e in (current.__cause__, current.__context__) if e is not None]
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(sentinel not in text for text in texts)
    await close(page, runtime)


def diagnostic_artifacts(page, tmp_path):
    # Fresh error after result-only worker/validation boundaries discarded exceptions.
    try:
        raise RuntimeError("Athena draft recovery was not confirmed")
    except RuntimeError as error:
        sink = LogSink(base_dir=tmp_path / "log")
        sink.error("athena.drafts.recovery", error=page.draft_recovery_error)
        sink.close()
        crash = (
            CrashDump(base_dir=tmp_path / "crash").write(exc=error, log_path=sink.path).read_text()
        )
        trace = "".join(
            traceback.TracebackException.from_exception(error, capture_locals=True).format()
        )
        return error, trace, crash, sink.path.read_text()


@pytest.mark.parametrize("collection", ["workgroups", "catalogs", "databases"])
@pytest.mark.parametrize(
    "failure", ["shape", "row", "token", "loop", "empty", "pages", "rows", "duplicate", "provider"]
)
async def test_fresh_discovery_rejects_malformed_or_unbounded_pages(
    monkeypatch, collection, failure
):
    from aws_tui.vm.athena.draft_recovery import validate_draft_context

    client = PageClient()
    context = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
    methods = {
        "workgroups": "list_workgroups_page",
        "catalogs": "list_catalogs_page",
        "databases": "list_databases_page",
    }
    original = getattr(client, methods[collection])
    kwargs = {} if collection == "workgroups" else {"workgroup": "primary"}
    args = ("AwsDataCatalog",) if collection == "databases" else ()
    valid_rows, _ = await original(*args, **kwargs)
    row = valid_rows[0]
    calls = 0

    async def fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if failure == "shape":
            return tuple(valid_rows), None
        if failure == "row":
            return [object()], None
        if failure == "token":
            return valid_rows, ""
        if failure == "duplicate":
            return [row, row], None
        if failure == "provider":
            raise OSError("DRAFT_SQL_SENTINEL")
        if failure in ("loop", "empty"):
            return [], "same" if failure == "loop" else str(calls)
        count = 1_001 if failure == "rows" else 1
        if collection == "workgroups" or collection == "catalogs":
            rows = [replace(row, name=f"other-{calls}-{i}") for i in range(count)]
        else:
            rows = [
                replace(row, ref=replace(row.ref, database_name=f"other-{calls}-{i}"))
                for i in range(count)
            ]
        return rows, str(calls)

    monkeypatch.setattr(client, methods[collection], fetch)
    assert not await validate_draft_context(context=context, client=client, source_is_current=yes)
    assert calls <= (64 if failure == "pages" else 4)


@pytest.mark.parametrize("change", ["connection_name", "region", "catalog_name", "database_name"])
async def test_database_source_references_must_match_exactly(change):
    from aws_tui.vm.athena.draft_recovery import validate_draft_context

    client = PageClient()
    context = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
    rows, _ = await client.list_databases_page("AwsDataCatalog", workgroup="primary")
    client.database_row_override = replace(rows[0], ref=replace(rows[0].ref, **{change: "other"}))
    assert not await validate_draft_context(context=context, client=client, source_is_current=yes)


async def test_provider_removes_database_during_second_validation(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch)
    original = client.list_databases_page
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def list_databases(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(client, "list_databases_page", list_databases)
    task = asyncio.create_task(page.restore_draft(saved.id, yes))
    await entered.wait()
    client.databases[("primary", "AwsDataCatalog")] = []
    release.set()
    assert not await task
    assert page.query.sql == ""
    await assert_stale(page, client)
    await close(page, runtime)


async def test_source_is_checked_after_provider_discovery(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch)
    client.block_workgroup_detail_for = "primary"
    is_current = True

    async def check():
        return is_current

    page._source_is_current = check
    task = asyncio.create_task(page.restore_draft(saved.id, yes))
    await client.workgroup_detail_started.wait()
    is_current = False
    client.release_workgroup_detail.set()
    assert not await task
    await assert_stale(page, client)
    await close(page, runtime)


@pytest.mark.parametrize("failure", ["disabled", "wrong-name", "invalid-detail"])
async def test_workgroup_detail_requires_exact_enabled_identity(failure):
    from aws_tui.vm.athena.draft_recovery import validate_draft_context

    client = PageClient()
    context = QueryContext("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
    detail = client.workgroup_details["primary"]
    if failure == "disabled":
        detail = replace(detail, summary=replace(detail.summary, state="DISABLED"))
    elif failure == "wrong-name":
        detail = replace(detail, summary=replace(detail.summary, name="other"))
    else:
        detail = object()
    client.workgroup_details["primary"] = detail
    assert not await validate_draft_context(context=context, client=client, source_is_current=yes)


async def test_lifecycle_changes_while_restore_waits_for_guard_are_refused(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    original = client.list_databases_page

    async def databases(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(client, "list_databases_page", databases)
    await page.query._lifecycle_lock.acquire()
    restore = asyncio.create_task(page.restore_draft(saved.id, yes))
    await entered.wait()
    # Queue an actual committed lifecycle transition ahead of restore's guard.
    change = asyncio.create_task(page.query.set_context(replace(page.context, database="other")))
    await asyncio.sleep(0)
    release.set()
    await asyncio.sleep(0)
    page.query._lifecycle_lock.release()
    await change
    assert not await restore
    assert page.query.sql == ""
    await assert_stale(page, client)
    await close(page, runtime)


async def test_cancellation_does_not_clear_a_newer_recovery_guard(tmp_path, monkeypatch):
    page, runtime, _, client, saved = await seeded(tmp_path, monkeypatch)
    client.block_workgroup_detail_for = "primary"
    task = asyncio.create_task(page.restore_draft(saved.id, yes))
    await client.workgroup_detail_started.wait()
    page.query.set_draft_recovery_guard(True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert page.query.draft_recovery_guard
    assert client.start_calls == []
    page.keep_current_editor()
    await close(page, runtime)


async def always_current() -> bool:
    return True


async def accept_replace() -> bool:
    return True


async def test_normal_exit_flush_restores_into_fresh_page(tmp_path):
    runtime, _store = runtime_at(tmp_path)
    client = PageClient()
    page = make_page_vm(client, drafts=runtime, source_is_current=always_current)
    await page.setup()
    page.query.set_sql("SELECT 'NORMAL_EXIT'")
    report = await runtime.shutdown()
    await page.shutdown()
    assert report.unpersisted == 0
    fresh_runtime, fresh_store = runtime_at(tmp_path)
    fresh_page = make_page_vm(client, drafts=fresh_runtime, source_is_current=always_current)
    await fresh_page.setup()
    assert fresh_page.query.sql == ""
    saved = fresh_store.list().records[0]
    before = len(client.start_calls)
    assert await fresh_page.restore_draft(saved.id, accept_replace)
    assert fresh_page.query.sql == "SELECT 'NORMAL_EXIT'"
    assert len(client.start_calls) == before
    await fresh_runtime.shutdown()
    await fresh_page.shutdown()


async def test_abrupt_exit_file_restores_without_source_shutdown(tmp_path):
    from tests.helpers import wait_until

    runtime, _store = runtime_at(tmp_path)
    client = PageClient()
    page = make_page_vm(client, drafts=runtime, source_is_current=always_current)
    await page.setup()
    page.query.set_sql("SELECT 'ABRUPT_EXIT'")
    await wait_until(lambda: page.query.draft_state == "saved", what="debounced draft committed")
    saved_bytes = next((tmp_path / "athena-drafts").glob("*.json")).read_bytes()
    fresh_runtime, fresh_store = runtime_at(tmp_path)
    fresh_page = make_page_vm(client, drafts=fresh_runtime, source_is_current=always_current)
    await fresh_page.setup()
    saved = fresh_store.list().records[0]
    before = len(client.start_calls)
    assert await fresh_page.restore_draft(saved.id, accept_replace)
    assert fresh_page.query.sql == "SELECT 'ABRUPT_EXIT'"
    assert len(client.start_calls) == before
    assert saved_bytes == next((tmp_path / "athena-drafts").glob("*.json")).read_bytes()
    await fresh_runtime.shutdown()
    await fresh_page.shutdown()
    await runtime.shutdown()  # Cleanup only, after recovery assertions.
    await page.shutdown()
