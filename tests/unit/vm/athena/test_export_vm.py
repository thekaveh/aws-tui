from __future__ import annotations

import asyncio
import json
import math
import threading
import tracemalloc
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aws_tui.domain.athena import ResultArtifactAccessError
from aws_tui.domain.athena_export import ExportLimits, ResultEncoder
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryExecutionRef,
    QueryExecutionSummary,
    QueryState,
    QueryStatistics,
    ResultColumn,
    ResultPage,
)
from aws_tui.infra.result_export_store import ResultExportStore
from aws_tui.vm.athena.export_vm import AthenaExportVM

CONTEXT = QueryContext("private-source", "us-east-1", "analytics", "catalog", "database")
COLUMNS = (ResultColumn("private-column", "varchar", "NULLABLE"),)
DETAIL = QueryExecutionDetail(
    QueryExecutionSummary(
        QueryExecutionRef("private-execution", *CONTEXT.cache_key[:3]),
        QueryState.SUCCEEDED,
        None,
        None,
        "DML",
    ),
    None,
    CONTEXT,
    QueryStatistics(None, None, None, None, None, False),
    "s3://private-bucket/private-result.csv",
    None,
    None,
)


class Client:
    def __init__(self, pages=None):
        self.detail = DETAIL
        self.pages = pages or {None: ResultPage(COLUMNS, (("full",),), None)}
        self.calls = []
        self.block = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.artifact_closed = False

    async def get_query_execution(self, execution_id):
        self.calls.append(("metadata", execution_id))
        if self.block == "metadata":
            self.entered.set()
            await self.release.wait()
        return self.detail

    async def start_query(self, *_args, **_kwargs):
        self.calls.append(("start_query",))
        raise AssertionError("Exports must not initiate queries")

    async def start_query_execution(self, *_args, **_kwargs):
        self.calls.append(("start_query_execution",))
        raise AssertionError("Exports must not initiate queries")

    async def get_results_page(self, execution_id, *, start_token=None):
        self.calls.append(("page", execution_id, start_token))
        if self.block == "page":
            self.entered.set()
            await self.release.wait()
        return self.pages[start_token]

    async def iter_result_artifact(self, detail, *, max_bytes, chunk_bytes):
        self.calls.append(("artifact", detail, max_bytes, chunk_bytes))
        try:
            yield b'"original"\r\n'
            yield b'"private-cell"\r\n'
        finally:
            self.artifact_closed = True


def make_vm(client=None, **kwargs):
    results = SimpleNamespace(
        execution_id="private-execution",
        projection_generation=1,
        columns=COLUMNS,
        visible_rows=(("visible",), (None,)),
        rows=(("visible",), (None,)),
        filter_text="",
        sort_column=None,
        sort_direction="ascending",
    )
    context = [CONTEXT, 0, True]
    vm = AthenaExportVM(
        client=client or Client(),
        results=results,
        context=lambda: context[0],
        context_revision=lambda: context[1],
        is_current=lambda: context[2],
        **kwargs,
    )
    return vm, results, context


@pytest.mark.parametrize("format", ["csv", "json"])
async def test_loaded_review_is_private_immutable_and_writes_only_visible_rows(tmp_path, format):
    client = Client()
    vm, _, _ = make_vm(client)
    path = tmp_path / "private-export"
    review = await vm.prepare(scope="loaded", format=format, path=path)
    assert review is not None
    assert review.row_count == 2
    assert not list(tmp_path.iterdir())
    assert client.calls == []
    assert "private" not in repr(review)
    with pytest.raises(AttributeError):
        review.row_count = 0
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.rows == 2
    assert receipt.bytes_written == path.stat().st_size
    assert "private" not in repr(receipt)
    if format == "csv":
        assert path.read_bytes() == b"private-column\nvisible\n\\N\n"
    else:
        assert json.loads(path.read_bytes())["rows"] == [["visible"], [None]]
    assert client.calls == []
    assert await vm.run(review) is None


async def test_complete_uses_historical_context_and_fresh_sequential_pages(tmp_path):
    client = Client(
        {
            None: ResultPage(COLUMNS, (("one",),), "token"),
            "token": ResultPage(COLUMNS, (("two",),), None),
        }
    )
    client.detail = replace(DETAIL, context=replace(CONTEXT, database="historical"))
    vm, _, _ = make_vm(client)
    review = await vm.prepare(scope="complete", format="json", path=tmp_path / "result")
    assert review is not None
    assert review.row_count is None
    assert review.context.database == "historical"
    assert len(client.calls) == 1
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.rows == 2
    assert json.loads(receipt.path.read_bytes())["rows"] == [["one"], ["two"]]
    assert [call[0] for call in client.calls] == ["metadata", "metadata", "page", "page"]


async def test_overwrite_is_explicit_exact_version_and_review_is_unforgeable(tmp_path):
    path = tmp_path / "result"
    path.write_bytes(b"prior")
    vm, _, _ = make_vm()
    review = await vm.prepare(scope="loaded", format="csv", path=path)
    assert await vm.run(replace(review)) is None
    assert await vm.run(review) is None
    assert path.read_bytes() == b"prior"
    review = await vm.prepare(scope="loaded", format="csv", path=path)
    path.write_bytes(b"new version")
    assert await vm.run(review, overwrite=True) is None
    assert path.read_bytes() == b"new version"
    review = await vm.prepare(scope="loaded", format="csv", path=path)
    assert await vm.run(review, overwrite=True) is not None


@pytest.mark.parametrize("change", ["execution", "generation", "context", "revision", "inactive"])
async def test_stale_review_cannot_publish(tmp_path, change):
    vm, results, context = make_vm()
    review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
    if change == "execution":
        results.execution_id = "other"
    elif change == "generation":
        results.projection_generation += 1
    elif change == "context":
        context[0] = replace(CONTEXT, region="us-west-2")
    elif change == "revision":
        context[1] += 1
    else:
        context[2] = False
    vm.invalidate()
    assert await vm.run(review) is None
    assert not list(tmp_path.iterdir())


async def test_stale_prepare_and_concurrent_actions_are_refused(tmp_path):
    client = Client()
    client.block = "metadata"
    vm, results, _ = make_vm(client)
    task = asyncio.create_task(vm.prepare(scope="complete", format="csv", path=tmp_path / "one"))
    await client.entered.wait()
    assert vm.busy
    assert await vm.prepare(scope="loaded", format="csv", path=tmp_path / "two") is None
    results.projection_generation += 1
    client.release.set()
    assert await task is None
    assert not vm.busy
    assert vm.review is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "page",
    [
        ResultPage(COLUMNS, (("row",),), "same"),
        ResultPage(COLUMNS, (("row",),), ""),
        ResultPage(COLUMNS, (("row",),), "x" * 1025),
        ResultPage(COLUMNS, (("row",),), 7),
        ResultPage(COLUMNS, (("row", "extra"),), None),
        ResultPage(COLUMNS, ((7,),), None),
        ResultPage(COLUMNS, (("row",),) * 1001, None),
        ResultPage((ResultColumn("changed", "varchar", "NULLABLE"),), (("row",),), None),
        ResultPage(COLUMNS, [["row"]], None),
    ],
)
async def test_invalid_pages_preserve_existing_destination(tmp_path, page):
    client = Client({None: ResultPage(COLUMNS, (), "same"), "same": page})
    vm, _, _ = make_vm(client)
    path = tmp_path / "result"
    path.write_bytes(b"prior")
    review = await vm.prepare(scope="complete", format="csv", path=path)
    assert await vm.run(review, overwrite=True) is None
    assert path.read_bytes() == b"prior"
    assert list(tmp_path.iterdir()) == [path]
    assert "private" not in vm.status


@pytest.mark.parametrize(
    ("limits", "pages", "expected_calls"),
    [
        (ExportLimits(max_requests=2), None, 2),
        (ExportLimits(max_requests=3), {None: ResultPage(COLUMNS, (), "next")}, 3),
        (ExportLimits(max_rows=0), None, 3),
        (ExportLimits(max_bytes=3), None, 3),
    ],
)
async def test_limits_refuse_partial_publication(tmp_path, limits, pages, expected_calls):
    client = Client(pages)
    vm, _, _ = make_vm(client, limits=limits)
    review = await vm.prepare(scope="complete", format="csv", path=tmp_path / "result")
    assert await vm.run(review) is None
    assert len(client.calls) == expected_calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "mutation",
    [
        {"output_location": "s3://different/object.csv"},
        {"context": replace(CONTEXT, database="changed")},
        {"managed_query_results_enabled": True},
        {"summary": replace(DETAIL.summary, state=QueryState.FAILED)},
    ],
)
async def test_metadata_change_between_review_and_dispatch_fails(tmp_path, mutation):
    client = Client()
    vm, _, _ = make_vm(client)
    review = await vm.prepare(scope="complete", format="csv", path=tmp_path / "result")
    client.detail = replace(DETAIL, **mutation)
    assert await vm.run(review) is None
    assert vm.status == "Export data or execution changed; reload results and review again"
    assert [call[0] for call in client.calls] == ["metadata", "metadata"]
    assert not list(tmp_path.iterdir())


class RefuseStageStore(ResultExportStore):
    def __init__(self):
        self.begin_calls = 0

    def begin(self, destination, *, permit):
        self.begin_calls += 1
        raise AssertionError("Unsupported original exports must not create a stage")


async def test_original_loaded_scope_guides_complete_or_normalized_before_any_provider_io(tmp_path):
    client = Client()
    files = RefuseStageStore()
    vm, _, _ = make_vm(client, files=files)
    assert await vm.prepare(scope="loaded", format="original", path=tmp_path / "result") is None
    assert vm.status == (
        "Original AWS CSV requires Complete execution; select Complete execution or normalized CSV/JSON"
    )
    assert client.calls == []
    assert files.begin_calls == 0
    assert vm.review is None
    assert not list(tmp_path.iterdir())


async def test_original_is_explicit_preserves_bytes_and_closes_stream(tmp_path):
    client = Client()
    vm, _, _ = make_vm(client)
    review = await vm.prepare(scope="complete", format="original", path=tmp_path / "result")
    assert [call[0] for call in client.calls] == ["metadata"]
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.rows is None
    assert receipt.path.read_bytes() == b'"original"\r\n"private-cell"\r\n'
    assert [call[0] for call in client.calls] == ["metadata", "artifact"]
    assert client.artifact_closed


@pytest.mark.parametrize(
    "mutation",
    [
        {"managed_query_results_enabled": True},
        {"output_location": None},
        {"output_location": "s3://bucket/key.txt"},
        {"summary": replace(DETAIL.summary, statement_type="DDL")},
        {"summary": replace(DETAIL.summary, state=QueryState.RUNNING)},
        {"context": replace(CONTEXT, connection_name="other")},
    ],
)
async def test_unavailable_original_refused_before_staging(tmp_path, mutation):
    client = Client()
    client.detail = replace(DETAIL, **mutation)
    vm, _, _ = make_vm(client)
    assert await vm.prepare(scope="complete", format="original", path=tmp_path / "result") is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "mutation",
    [
        {"managed_query_results_enabled": True},
        {"output_location": None},
        {"output_location": "s3://private-bucket/private-result.txt"},
        {"output_location": "https://private-url/private-result.csv"},
        {"summary": replace(DETAIL.summary, statement_type="DDL")},
    ],
)
async def test_unavailable_original_guides_normalized_without_staging_or_retrieval(
    tmp_path, mutation
):
    client = Client()
    client.detail = replace(DETAIL, **mutation)
    files = RefuseStageStore()
    vm, _, _ = make_vm(client, files=files)
    assert await vm.prepare(scope="complete", format="original", path=tmp_path / "result") is None
    assert vm.status == (
        "Original AWS CSV is unavailable for this execution; choose normalized CSV/JSON"
    )
    assert "private" not in vm.status
    assert "s3:" not in vm.status
    assert "https:" not in vm.status
    assert [call[0] for call in client.calls] == ["metadata"]
    assert files.begin_calls == 0
    assert vm.review is None
    assert not list(tmp_path.iterdir())


async def test_original_request_budget_reserved_before_dispatch(tmp_path):
    client = Client()
    vm, _, _ = make_vm(client, limits=ExportLimits(max_requests=3))
    review = await vm.prepare(scope="complete", format="original", path=tmp_path / "result")
    assert await vm.run(review) is None
    assert [call[0] for call in client.calls] == ["metadata"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("action", ["cancel", "close", "invalidate", "timeout"])
async def test_cancel_close_source_change_timeout_drain_and_preserve_prior(tmp_path, action):
    client = Client()
    client.block = "page"
    limits = ExportLimits(timeout_seconds=0.1 if action == "timeout" else 10)
    vm, _, context = make_vm(client, limits=limits)
    path = tmp_path / "result"
    path.write_bytes(b"prior")
    review = await vm.prepare(scope="complete", format="csv", path=path)
    task = asyncio.create_task(vm.run(review, overwrite=True))
    await client.entered.wait()
    if action == "cancel":
        vm.cancel()
    elif action == "close":
        await vm.aclose()
    elif action == "invalidate":
        context[2] = False
        vm.invalidate()
    assert await task is None
    assert not vm.busy
    assert path.read_bytes() == b"prior"
    assert list(tmp_path.iterdir()) == [path]


class BlockingStore(ResultExportStore):
    def __init__(self, operation):
        self.operation = operation
        self.entered = threading.Event()
        self.release = threading.Event()
        self.stage = None

    def begin(self, destination, *, permit):
        stage = super().begin(destination, permit=permit)
        self.stage = stage
        original = getattr(stage, self.operation)

        def blocked(*args):
            result = original(*args)
            self.entered.set()
            assert self.release.wait(5)
            return result

        setattr(stage, self.operation, blocked)
        return stage


@pytest.mark.parametrize("operation", ["write", "publish"])
async def test_cancel_drains_io_and_acknowledges_publication_winner(tmp_path, operation):
    files = BlockingStore(operation)
    vm, _, _ = make_vm(files=files)
    path = tmp_path / "result"
    review = await vm.prepare(scope="loaded", format="csv", path=path)
    task = asyncio.create_task(vm.run(review))
    assert await asyncio.to_thread(files.entered.wait, 3)
    vm.cancel()
    closing = asyncio.create_task(vm.aclose())
    await asyncio.sleep(0)
    assert not task.done()
    assert not closing.done()
    files.release.set()
    receipt = await task
    await closing
    if operation == "publish":
        assert receipt is not None
        assert path.exists()
        assert vm.receipt is receipt
    else:
        assert receipt is None
        assert not path.exists()
    assert list(tmp_path.glob(".aws-tui-export-*")) == []


async def test_large_complete_stream_is_bounded_and_responsive(tmp_path):
    class LargeClient(Client):
        async def get_results_page(self, execution_id, *, start_token=None):
            offset = int(start_token or "0")
            rows = tuple((f"{index}:" + "x" * 2048,) for index in range(offset, offset + 1000))
            return ResultPage(COLUMNS, rows, str(offset + 1000) if offset < 11000 else None)

    vm, _, _ = make_vm(LargeClient())
    review = await vm.prepare(scope="complete", format="csv", path=tmp_path / "result")
    beats = 0

    async def heartbeat():
        nonlocal beats
        while vm.busy:
            beats += 1
            await asyncio.sleep(0.001)

    tracemalloc.start()
    task = asyncio.create_task(vm.run(review))
    await asyncio.sleep(0)
    await heartbeat()
    receipt = await task
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert receipt is not None
    assert receipt.rows == 12000
    assert receipt.bytes_written > 24_000_000
    assert peak < 14_000_000
    assert beats > 10


async def test_provider_errors_never_echo_sensitive_details(tmp_path):
    class BrokenClient(Client):
        async def get_query_execution(self, execution_id):
            raise RuntimeError("private SQL cell token s3://bucket/key /tmp/path")

    vm, _, _ = make_vm(BrokenClient())
    assert await vm.prepare(scope="complete", format="csv", path=tmp_path / "result") is None
    assert vm.status
    assert "private" not in vm.status
    assert "s3:" not in vm.status
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("operation", ["inspect", "begin"])
async def test_cancel_waits_for_inspection_and_new_stage_ownership(tmp_path, operation):
    class SlowStore(ResultExportStore):
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()

        def inspect(self, path):
            result = super().inspect(path)
            if operation == "inspect":
                self.entered.set()
                assert self.release.wait(5)
            return result

        def begin(self, destination, *, permit):
            result = super().begin(destination, permit=permit)
            if operation == "begin":
                self.entered.set()
                assert self.release.wait(5)
            return result

    files = SlowStore()
    vm, _, _ = make_vm(files=files)
    if operation == "inspect":
        task = asyncio.create_task(
            vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
        )
    else:
        review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
        task = asyncio.create_task(vm.run(review))
    assert await asyncio.to_thread(files.entered.wait, 3)
    closing = asyncio.create_task(vm.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    assert not task.done()
    files.release.set()
    await closing
    assert await task is None
    assert not list(tmp_path.iterdir())


async def test_failure_after_publication_cannot_hide_success(tmp_path):
    class PostPublicationFailureStore(ResultExportStore):
        def begin(self, destination, *, permit):
            stage = super().begin(destination, permit=permit)
            publish = stage.publish

            def fail_after_publish():
                publish()
                raise RuntimeError("private cleanup problem")

            stage.publish = fail_after_publish
            return stage

    vm, _, _ = make_vm(files=PostPublicationFailureStore())
    review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.rows == 2
    assert receipt.bytes_written == receipt.path.stat().st_size
    assert vm.status == "Export saved"


async def test_loaded_snapshot_survives_later_local_projection_changes(tmp_path):
    vm, results, _ = make_vm()
    review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
    results.visible_rows = (("later rows",),)
    results.rows = results.visible_rows
    results.filter_text = "later"
    results.sort_column = 0
    vm.invalidate()
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.rows == 2
    assert receipt.path.read_bytes() == b"private-column\nvisible\n\\N\n"


async def test_publication_receipt_survives_observer_error(tmp_path):
    vm, _, _ = make_vm()

    def broken_observer():
        if vm.receipt is not None:
            raise RuntimeError("private observer failure")

    vm._notify = broken_observer
    review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.path.is_file()


async def test_idle_invalidation_notifies_once_without_recursive_cancellation(tmp_path):
    vm, _, context = make_vm()
    review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
    notices = 0

    def observer():
        nonlocal notices
        notices += 1
        vm.invalidate()

    vm._notify = observer
    context[1] += 1
    vm.invalidate()
    assert notices == 1
    assert vm.review is None
    assert await vm.run(review) is None


async def test_modal_dismissal_preserves_completed_receipt_and_status(tmp_path):
    vm, _, _ = make_vm()
    review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
    receipt = await vm.run(review)
    assert receipt is not None
    vm.cancel()
    vm.cancel()
    assert vm.receipt is receipt
    assert vm.status == "Export saved"


@pytest.mark.parametrize("scope", ["loaded", "complete"])
@pytest.mark.parametrize("format", ["csv", "json"])
async def test_normalized_writes_remain_bounded_with_larger_configured_chunks(
    tmp_path, scope, format
):
    class MeasuringStore(ResultExportStore):
        def __init__(self):
            self.write_sizes = []

        def begin(self, destination, *, permit):
            stage = super().begin(destination, permit=permit)
            write = stage.write

            def measured_write(chunk):
                self.write_sizes.append(len(chunk))
                write(chunk)

            stage.write = measured_write
            return stage

    cell = "x" * (131_072 + 17)
    files = MeasuringStore()
    client = Client({None: ResultPage(COLUMNS, ((cell,),), None)})
    vm, results, _ = make_vm(client, files=files, limits=ExportLimits(chunk_bytes=131_072))
    results.visible_rows = ((cell,),)
    review = await vm.prepare(scope=scope, format=format, path=tmp_path / "result")
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.rows == 1
    assert receipt.bytes_written == sum(files.write_sizes)
    assert receipt.bytes_written == receipt.path.stat().st_size
    if format == "csv":
        assert receipt.path.read_text() == "private-column\n" + cell + "\n"
    else:
        assert json.loads(receipt.path.read_bytes())["rows"] == [[cell]]
    assert max(files.write_sizes) <= 65_536


@pytest.mark.parametrize("scope", ["loaded", "complete"])
@pytest.mark.parametrize("format", ["csv", "json"])
async def test_small_rows_coalesce_into_bounded_file_writes(tmp_path, scope, format):
    class MeasuringStore(ResultExportStore):
        def __init__(self):
            self.write_sizes = []

        def begin(self, destination, *, permit):
            stage = super().begin(destination, permit=permit)
            write = stage.write

            def measured_write(chunk):
                self.write_sizes.append(len(chunk))
                write(chunk)

            stage.write = measured_write
            return stage

    rows = (("small",),) * 1000
    files = MeasuringStore()
    client = Client({None: ResultPage(COLUMNS, rows, None)})
    vm, results, _ = make_vm(client, files=files)
    results.visible_rows = rows
    review = await vm.prepare(scope=scope, format=format, path=tmp_path / "result")
    receipt = await vm.run(review)
    assert receipt is not None
    assert receipt.rows == 1000
    assert receipt.bytes_written == sum(files.write_sizes)
    assert receipt.bytes_written == receipt.path.stat().st_size
    assert max(files.write_sizes) <= 65_536
    assert len(files.write_sizes) == math.ceil(receipt.bytes_written / 65_536)
    if format == "csv":
        assert receipt.path.read_text() == "private-column\n" + "small\n" * 1000
    else:
        assert json.loads(receipt.path.read_bytes())["rows"] == [["small"]] * 1000


@pytest.mark.parametrize("cancel", [False, True])
async def test_tiny_rows_yield_before_the_first_full_buffer(tmp_path, cancel, monkeypatch):
    encoded = 0
    write_row = ResultEncoder.write_row

    def counted_row(encoder, row):
        nonlocal encoded
        encoded += 1
        return write_row(encoder, row)

    monkeypatch.setattr(ResultEncoder, "write_row", counted_row)
    vm, results, _ = make_vm()
    results.visible_rows = (("",),) * 10_000
    review = await vm.prepare(scope="loaded", format="csv", path=tmp_path / "result")
    beats = 0
    task = asyncio.create_task(vm.run(review))
    await asyncio.sleep(0)
    while vm.busy:
        if 0 < encoded < 10_000:
            beats += 1
            if cancel and beats == 5:
                vm.cancel()
        await asyncio.sleep(0)
    receipt = await task
    assert beats > 5
    if cancel:
        assert receipt is None
        assert not list(tmp_path.iterdir())
    else:
        assert receipt is not None
        assert receipt.rows == 10_000
        assert receipt.bytes_written < 65_536


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("Athena", "Athena result access denied; check Athena result read permissions"),
        ("S3", "S3 result access denied; check S3 object read permissions"),
        ("encryption", "Result encryption access denied; check KMS key permissions"),
        ("private-token", "Export access denied; check source credentials and result permissions"),
        (
            ["private-token"],
            "Export access denied; check source credentials and result permissions",
        ),
        (None, "Export access denied; check source credentials and result permissions"),
    ],
)
async def test_artifact_access_guidance_is_specific_and_value_free(tmp_path, service, expected):
    error = ResultArtifactAccessError("S3")
    error.args = ("private SQL cell token s3://bucket/key /tmp/path",)
    if service is None:
        del error.service
    else:
        error.service = service

    class DeniedClient(Client):
        async def iter_result_artifact(self, detail, *, max_bytes, chunk_bytes):
            yield b"prefix"
            raise error

    vm, _, _ = make_vm(DeniedClient())
    review = await vm.prepare(scope="complete", format="original", path=tmp_path / "result")
    assert review is not None
    assert await vm.run(review) is None
    assert vm.status == expected
    assert "private" not in vm.status
    assert "s3:" not in vm.status
    assert not list(tmp_path.iterdir())
