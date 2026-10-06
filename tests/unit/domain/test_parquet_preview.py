"""Real Arrow fixtures and supervised sparse-decoder admission contracts."""

from __future__ import annotations

import asyncio
import io
import sys
from time import monotonic

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aws_tui.domain.filesystem import PathRef, PreviewSourceChangedError, ReadSnapshot
from aws_tui.domain.preview import PreviewCellKind, PreviewFormat, load_preview
from aws_tui.domain.preview_limits import PreviewBudget, PreviewLimitExceeded, PreviewRequestKind


def fixture_bytes(table=None, *, row_group_size=20):
    sink = io.BytesIO()
    if table is None:
        table = pa.table({"id": list(range(120)), "note": ["sample"] * 120})
    pq.write_table(table, sink, row_group_size=row_group_size, compression="snappy")
    return sink.getvalue()


class RecordingSession:
    def __init__(self, data, budget, *, drift=False):
        self.data = data
        self.budget = budget
        self.snapshot = ReadSnapshot(len(data), "test-revision")
        self.reads = []
        self.closed = False
        self.validated = False
        self.drift = drift

    async def read_range(self, offset, length):
        assert offset >= 0
        assert offset + length <= len(self.data)
        assert length <= 1024 * 1024
        self.budget.charge_request(kind=PreviewRequestKind.RANGE, length=length)
        self.reads.append((offset, length))
        return self.data[offset : offset + length]

    async def validate(self):
        self.budget.charge_request(kind=PreviewRequestKind.VALIDATE)
        self.validated = True
        if self.drift:
            raise PreviewSourceChangedError("source changed")

    async def aclose(self):
        self.closed = True


class RecordingProvider:
    def __init__(self, data, *, drift=False):
        self.data = data
        self.session = None
        self.drift = drift

    async def open_preview(self, path, *, budget):
        budget.charge_request(kind=PreviewRequestKind.OPEN)
        self.session = RecordingSession(self.data, budget, drift=self.drift)
        return self.session


@pytest.mark.asyncio
async def test_multi_group_actual_process_schema_ordered_sample_and_physical_reads():
    data = fixture_bytes()
    metadata = pq.ParquetFile(io.BytesIO(data)).metadata
    assert metadata.num_row_groups == 6
    fs = RecordingProvider(data)
    result = await load_preview(fs, PathRef(("sample",)), name="no-suffix", mime="")
    assert result.format is PreviewFormat.PARQUET
    assert [(c.name, c.type_name) for c in result.columns] == [("id", "int64"), ("note", "string")]
    assert [int(row[0].text) for row in result.rows] == list(range(50))
    assert fs.session.closed
    assert fs.session.validated
    assert fs.session.reads[0] == (0, min(len(data), 65536))
    assert fs.session.reads[1] == (len(data) - 8, 8)
    expected = []
    for group in range(3):
        for index in range(2):
            col = metadata.row_group(group).column(index)
            start = min(col.dictionary_page_offset, col.data_page_offset)
            expected.append((start, col.total_compressed_size))
    assert fs.session.reads[3:] == expected
    assert fs.session.budget.requests == len(fs.session.reads) + 2
    assert fs.session.budget.bytes_requested == sum(n for _, n in fs.session.reads)


@pytest.mark.asyncio
async def test_nested_null_empty_and_empty_file_schema():
    fs = RecordingProvider(
        fixture_bytes(pa.table({"a": [None, ""], "nested": [{"x": 1}, {"x": 2}]}))
    )
    result = await load_preview(fs, PathRef(("a",)), name="a.parquet", mime="")
    assert result.format is PreviewFormat.PARQUET
    assert result.rows[0][0].kind is PreviewCellKind.NULL
    assert result.rows[1][0].kind is PreviewCellKind.EMPTY
    assert result.rows[0][1].kind is PreviewCellKind.NESTED
    fs = RecordingProvider(fixture_bytes(pa.table({"a": pa.array([], type=pa.int64())})))
    result = await load_preview(fs, PathRef(("a",)), name="a.parquet", mime="")
    assert result.format is PreviewFormat.PARQUET
    assert result.columns[0].name == "a"
    assert result.rows == ()


@pytest.mark.asyncio
async def test_physical_leaf_projection_caps_nested_top_level():
    fs = RecordingProvider(fixture_bytes(pa.table({f"c{i}": [i] for i in range(25)})))
    result = await load_preview(fs, PathRef(("a",)), name="a", mime="")
    assert len(result.columns) == 24
    assert len(result.rows[0]) == 24
    # A 25-leaf nested projection is skipped as a unit; the later scalar fits.
    fs = RecordingProvider(
        fixture_bytes(pa.table({"too_wide": [{f"c{i}": i for i in range(25)}], "ok": [1]}))
    )
    result = await load_preview(fs, PathRef(("a",)), name="a", mime="")
    assert tuple(c.name for c in result.columns) == ("ok",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"PARE" + b"x" * 10, "Encrypted Parquet preview is not supported"),
        (b"PAR1" + b"x" * 10, "Malformed Parquet footer"),
        (b"PAR1" + (512 * 1024 + 1).to_bytes(4, "little") + b"PAR1", "Malformed Parquet footer"),
    ],
)
async def test_named_footer_errors_validate_and_close(data, message):
    fs = RecordingProvider(data)
    result = await load_preview(fs, PathRef(("a",)), name="a", mime="")
    assert result.format is PreviewFormat.RAW
    assert result.notes == (message,)
    assert fs.session.validated
    assert fs.session.closed


@pytest.mark.asyncio
async def test_drift_never_returns_decoder_result():
    fs = RecordingProvider(fixture_bytes(), drift=True)
    with pytest.raises(PreviewSourceChangedError):
        await load_preview(fs, PathRef(("a",)), name="a", mime="")
    assert fs.session.closed


@pytest.mark.asyncio
async def test_hanging_child_deadline_terminates_reaps_and_closes_pipes(monkeypatch):
    from aws_tui.domain import _parquet_preview_worker as worker

    real_spawn = asyncio.create_subprocess_exec
    children = []

    async def hanging_spawn(*args, **kwargs):
        proc = await real_spawn(sys.executable, "-c", "import time; time.sleep(600)", **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", hanging_spawn)
    budget = PreviewBudget(monotonic() + 0.3, monotonic)
    data = fixture_bytes()
    session = RecordingSession(data, budget)
    with pytest.raises(PreviewLimitExceeded, match="Preview timed out"):
        await worker.preview_parquet(session, data[:65536], budget=budget)
    assert len(children) == 1
    assert children[0].returncode is not None
    assert children[0].stdin.is_closing()
    assert children[0].stdout.at_eof()


@pytest.mark.asyncio
async def test_cancelled_engine_reaps_child_and_closes_session(monkeypatch):
    from aws_tui.domain import _parquet_preview_worker as worker

    real_spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    children = []

    async def hanging_spawn(*args, **kwargs):
        proc = await real_spawn(sys.executable, "-c", "import time; time.sleep(600)", **kwargs)
        children.append(proc)
        started.set()
        return proc

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", hanging_spawn)
    fs = RecordingProvider(fixture_bytes())
    task = asyncio.create_task(load_preview(fs, PathRef(("a",)), name="a", mime=""))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fs.session.closed
    assert children[0].returncode is not None
    assert children[0].stdin.is_closing()


def test_sparse_missing_data_refused_without_zero_fill():
    from aws_tui.domain._parquet_preview_worker import _SparseFile

    source = _SparseFile(100, [(0, b"PAR1"), (90, b"0123456789")])
    assert source.read(4) == b"PAR1"
    with pytest.raises(OSError, match="Missing admitted Parquet segment"):
        source.read(1)


@pytest.mark.asyncio
async def test_oversized_frame_rejected_before_payload_read():
    from aws_tui.domain._parquet_preview_worker import _MAX_FRAME_BYTES, _receive_frame

    reader = asyncio.StreamReader()
    reader.feed_data((_MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
    with pytest.raises(ValueError, match="frame"):
        await _receive_frame(reader)


def test_worker_environment_drops_synthetic_credentials(monkeypatch):
    from aws_tui.domain._parquet_preview_worker import _worker_environment

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "SYNTHETIC-NOT-A-CREDENTIAL")
    monkeypatch.setenv("AWS_PROFILE", "synthetic-profile")
    env = _worker_environment()
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_PROFILE" not in env
    assert "HOME" not in env
    assert "PYTHONPATH" not in env


def _fake_metadata(
    *,
    codec="SNAPPY",
    data_offset=10,
    dictionary_offset=4,
    compressed=20,
    uncompressed=30,
    external="",
    file_offset=0,
):
    from types import SimpleNamespace

    column = SimpleNamespace(
        compression=codec,
        data_page_offset=data_offset,
        dictionary_page_offset=dictionary_offset,
        total_compressed_size=compressed,
        total_uncompressed_size=uncompressed,
        file_path=external,
        file_offset=file_offset,
    )
    group = SimpleNamespace(num_rows=20, column=lambda _: column)
    metadata = SimpleNamespace(num_columns=1, num_row_groups=1, row_group=lambda _: group)
    return metadata, pa.schema([("a", pa.int64())])


@pytest.mark.parametrize("codec", ["UNKNOWN-CODEC", "SNAPPY"])
def test_codec_unavailable_has_named_app_error(monkeypatch, codec):
    from aws_tui.domain._parquet_preview_worker import _ParquetFailure, _plan

    # Metadata boundary injection exercises declared codec admission without native bombs.
    class UnavailableCodec:
        @staticmethod
        def is_available(name):
            if name == "unknown-codec":
                raise ValueError("invalid compression: unknown-codec")
            return False

    monkeypatch.setattr(pa, "Codec", UnavailableCodec)
    metadata, schema = _fake_metadata(codec=codec)
    with pytest.raises(_ParquetFailure, match=f"^Unsupported Parquet codec: {codec.lower()}$"):
        _plan(metadata, schema, 100, 80)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"data_offset": -1},
        {"dictionary_offset": 11},
        {"compressed": 100},
        {"external": "elsewhere.parquet"},
        {"file_offset": 100},
    ],
)
def test_invalid_declared_chunk_boundaries_are_footer_errors(kwargs):
    from aws_tui.domain._parquet_preview_worker import _ParquetFailure, _plan

    metadata, schema = _fake_metadata(**kwargs)
    with pytest.raises(_ParquetFailure, match=r"^Malformed Parquet footer$"):
        _plan(metadata, schema, 100, 80)


def test_uncompressed_declared_chunk_cap_retains_schema_without_decode():
    from aws_tui.domain._parquet_preview_worker import _plan

    metadata, schema = _fake_metadata(uncompressed=32 * 1024 * 1024)
    plan = _plan(metadata, schema, 100, 80)
    assert plan["chunks"] == [[4, 20]]
    metadata, schema = _fake_metadata(uncompressed=32 * 1024 * 1024 + 1)
    plan = _plan(metadata, schema, 100, 80)
    assert plan["chunks"] == []
    assert plan["columns"] == [["a", "int64"]]
    assert plan["notes"] == ["Parquet sample exceeds preview budget"]


def _admission_plan(length):
    return {
        "phase": "plan",
        "columns": [["a", "int64"]],
        "names": ["a"],
        "groups": [0],
        "physical": [0],
        "chunks": [[4, length]],
        "uncompressed": length,
        "notes": [],
    }


def test_parent_range_byte_request_exact_limits_and_plus_one():
    from aws_tui.domain._parquet_preview_worker import _admit_plan, _ParquetFailure

    budget = PreviewBudget.start()
    budget.bytes_requested = 8 * 1024 * 1024 - 1024 * 1024
    budget.requests = 30
    columns, ranges, notes = _admit_plan(
        _admission_plan(1024 * 1024),
        size=10 * 1024 * 1024,
        footer_offset=9 * 1024 * 1024,
        budget=budget,
    )
    assert columns[0].name == "a"
    assert ranges == [(4, 1024 * 1024)]
    assert notes == ()
    with pytest.raises(_ParquetFailure, match="sample exceeds"):
        _admit_plan(
            _admission_plan(1024 * 1024 + 1),
            size=10 * 1024 * 1024,
            footer_offset=9 * 1024 * 1024,
            budget=budget,
        )
    budget.requests = 31
    with pytest.raises(_ParquetFailure, match="sample exceeds"):
        _admit_plan(_admission_plan(1), size=100, footer_offset=80, budget=budget)


@pytest.mark.asyncio
async def test_open_is_inside_the_single_absolute_deadline(monkeypatch):
    from aws_tui.domain import preview

    opened = asyncio.Event()
    cancelled = False

    class SlowProvider:
        async def open_preview(self, path, *, budget):
            nonlocal cancelled
            opened.set()
            try:
                await asyncio.sleep(600)
            finally:
                cancelled = True

    monkeypatch.setattr(
        preview.PreviewBudget, "start", lambda: PreviewBudget(monotonic() + 0.05, monotonic)
    )
    with pytest.raises(PreviewLimitExceeded, match="Preview timed out"):
        await load_preview(SlowProvider(), PathRef(("a",)), name="a", mime="")
    assert opened.is_set()
    assert cancelled


@pytest.mark.asyncio
async def test_source_validation_deadline_does_not_publish_raw_fallback(monkeypatch):
    from aws_tui.domain import preview

    class SlowValidateSession(RecordingSession):
        async def validate(self):
            await asyncio.sleep(600)

    fs = RecordingProvider(b"not json")

    async def open_preview(path, *, budget):
        fs.session = SlowValidateSession(fs.data, budget)
        return fs.session

    fs.open_preview = open_preview
    monkeypatch.setattr(
        preview.PreviewBudget, "start", lambda: PreviewBudget(monotonic() + 0.05, monotonic)
    )
    with pytest.raises(PreviewLimitExceeded, match="Preview timed out"):
        await load_preview(fs, PathRef(("a",)), name="a.json", mime="")
    assert fs.session.closed


@pytest.mark.asyncio
async def test_cancellation_during_child_startup_retains_process_ownership(monkeypatch):
    from aws_tui.domain import _parquet_preview_worker as worker

    real_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    children = []

    async def slow_spawn(*args, **kwargs):
        proc = await real_spawn(sys.executable, "-c", "import time; time.sleep(600)", **kwargs)
        children.append(proc)
        spawned.set()
        await asyncio.sleep(0.05)
        return proc

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", slow_spawn)
    fs = RecordingProvider(fixture_bytes())
    task = asyncio.create_task(load_preview(fs, PathRef(("a",)), name="a", mime=""))
    await spawned.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        assert fs.session.closed
    finally:
        # A RED test must also clean up the intentionally exposed orphan.
        if children[0].returncode is None:
            children[0].kill()
            await children[0].wait()


@pytest.mark.asyncio
async def test_ambiguous_dotted_projection_cannot_decode_unadmitted_leaves():
    # Arrow prefix selection for literal a.x also selects the nested a.x leaf.
    fs = RecordingProvider(
        fixture_bytes(
            pa.table(
                {
                    "a": [{"x": 1}],
                    "a.x": [2],
                    **{f"c{i}": [i] for i in range(23)},
                }
            )
        )
    )
    result = await load_preview(fs, PathRef(("a",)), name="a", mime="")
    assert result.format is PreviewFormat.RAW
    assert result.notes == ("Parquet preview unavailable",)
    assert fs.session.closed


@pytest.mark.asyncio
async def test_hanging_child_ignoring_terminate_is_killed_within_cleanup_allowance(monkeypatch):
    from aws_tui.domain import _parquet_preview_worker as worker

    real_spawn = asyncio.create_subprocess_exec
    children = []

    async def stubborn_spawn(*args, **kwargs):
        proc = await real_spawn(
            sys.executable,
            "-c",
            "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); sys.stdout.buffer.write(b'READ'); sys.stdout.buffer.flush(); time.sleep(600)",
            **kwargs,
        )
        # Observe registration before handing this intentionally hung native boundary over.
        assert await proc.stdout.readexactly(4) == b"READ"
        children.append(proc)
        return proc

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", stubborn_spawn)
    budget = PreviewBudget(monotonic() + 0.2, monotonic)
    data = fixture_bytes()
    session = RecordingSession(data, budget)
    start = monotonic()
    with pytest.raises(PreviewLimitExceeded, match="Preview timed out"):
        await worker.preview_parquet(session, data[:65536], budget=budget)
    assert monotonic() - start < 1.2
    assert children[0].returncode is not None
    assert children[0].stdout.at_eof()


def test_worker_render_cap_is_enforced_before_ipc_serialization():
    from aws_tui.domain._parquet_preview_worker import _ParquetFailure, append_bounded_cells

    batch = pa.record_batch({f"c{i}": ["\x1b" * 64] * 50 for i in range(24)})
    with pytest.raises(_ParquetFailure, match="Parquet sample exceeds preview budget"):
        append_bounded_cells(batch, 50)


def test_sync_oversized_frame_refused_before_allocation():
    from aws_tui.domain._parquet_preview_worker import _MAX_FRAME_BYTES, _read_frame

    class HeaderOnly:
        def read(self, length):
            assert length == 4
            return (_MAX_FRAME_BYTES + 1).to_bytes(4, "big")

    with pytest.raises(ValueError, match="frame"):
        _read_frame(HeaderOnly())


def test_empty_projection_closes_sparse_reader():
    from aws_tui.domain._parquet_preview_worker import _decode, _SparseFile

    source = _SparseFile(100, [(0, b"PAR1")])
    assert _decode(None, {"names": []}, source) == []
    assert source.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["startup", "cleanup"])
@pytest.mark.parametrize("deadline_overlap", [False, True])
async def test_repeated_cancellation_retains_child_and_session_until_reaped(
    monkeypatch, phase, deadline_overlap
):
    from aws_tui.domain import _parquet_preview_worker as worker
    from aws_tui.domain import preview

    real_spawn = asyncio.create_subprocess_exec
    real_reap = worker._reap
    real_timeout = asyncio.timeout
    blocked = asyncio.Event()
    release = asyncio.Event()
    deadline_fired = asyncio.Event()
    children = []
    background = []
    engine = None
    fs = RecordingProvider(fixture_bytes())
    budget = PreviewBudget.start()
    monkeypatch.setattr(preview.PreviewBudget, "start", lambda: budget)
    engine_deadlines = []

    def controlled_timeout(delay):
        deadline = real_timeout(delay)
        engine_deadlines.append(deadline)
        return deadline

    async def controlled_spawn(*args, **kwargs):
        background.append(asyncio.current_task())
        proc = await real_spawn(sys.executable, "-c", "import time; time.sleep(600)", **kwargs)
        children.append(proc)
        if phase == "startup":
            blocked.set()
            await release.wait()
        return proc

    async def controlled_reap(proc):
        background.append(asyncio.current_task())
        if phase == "cleanup":
            blocked.set()
            await release.wait()
        await real_reap(proc)

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", controlled_spawn)
    monkeypatch.setattr(worker, "_reap", controlled_reap)
    monkeypatch.setattr(worker.asyncio, "timeout", controlled_timeout)
    try:
        engine = asyncio.create_task(load_preview(fs, PathRef(("a",)), name="a", mime=""))
        if phase == "cleanup":
            while not children:
                await asyncio.sleep(0)
            engine.cancel()
        await blocked.wait()
        engine.cancel()
        await asyncio.sleep(0)
        if deadline_overlap:
            # Expire load_preview's actual work deadline while the owned wait is blocked.
            engine_deadlines[0].reschedule(asyncio.get_running_loop().time())
            asyncio.get_running_loop().call_soon(deadline_fired.set)
            await deadline_fired.wait()
        for _ in range(4):
            engine.cancel()
            await asyncio.sleep(0)
        assert not engine.done(), "engine released its child before handle/reap drain"
        assert not fs.session.closed, "session closed while decoder remained owned"
        assert children[0].returncode is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await engine
        assert children[0].returncode is not None
        assert children[0].stdin.is_closing()
        assert children[0].stdout.at_eof()
        assert fs.session.closed
    finally:
        # RED also drains delayed startup/cleanup tasks and any leaked actual child.
        release.set()
        if engine is not None and not engine.done():
            engine.cancel()
        if engine is not None:
            await asyncio.gather(engine, return_exceptions=True)
        await asyncio.gather(*background, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
            await real_reap(child)


@pytest.mark.asyncio
async def test_parquet_node_allowance_is_shared_across_row_group_layouts():
    table = pa.table({"nested": [[0] * 100 for _ in range(50)]})
    results = []
    for group_size in (50, 20, 10):
        fs = RecordingProvider(fixture_bytes(table, row_group_size=group_size))
        result = await load_preview(fs, PathRef(("a",)), name="a", mime="")
        assert result.format is PreviewFormat.PARQUET
        assert len(result.rows) == 50
        assert not result.rows[39][0].truncated
        assert result.rows[40][0].truncated
        assert result.rows[-1][0].truncated
        assert fs.session.validated
        assert fs.session.closed
        results.append(result.rows)
    assert results[0] == results[1] == results[2]


@pytest.mark.asyncio
async def test_cancelled_startup_failure_cannot_publish_raw_fallback(monkeypatch):
    from aws_tui.domain import _parquet_preview_worker as worker

    started = asyncio.Event()
    release = asyncio.Event()
    startup_tasks = []

    async def failing_spawn(*args, **kwargs):
        startup_tasks.append(asyncio.current_task())
        started.set()
        await release.wait()
        raise OSError("controlled spawn failure")

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", failing_spawn)
    fs = RecordingProvider(fixture_bytes())
    engine = asyncio.create_task(load_preview(fs, PathRef(("a",)), name="a", mime=""))
    try:
        await started.wait()
        for _ in range(3):
            engine.cancel()
            await asyncio.sleep(0)
        assert not engine.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await engine
        assert not fs.session.validated
        assert fs.session.closed
    finally:
        release.set()
        await asyncio.gather(engine, *startup_tasks, return_exceptions=True)
