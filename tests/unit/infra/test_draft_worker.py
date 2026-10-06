import asyncio
import gc
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

from aws_tui.infra.athena_draft_store import DraftStoreResult
from aws_tui.infra.draft_worker import DraftWorker


def test_lazy_fifo_owned_thread_and_close(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Default executor forbidden")

    monkeypatch.setattr(asyncio, "to_thread", forbidden)
    monkeypatch.setattr(ThreadPoolExecutor, "submit", forbidden)
    worker = DraftWorker()
    assert worker._thread is None
    assert worker.pending_count == 0
    started, release = threading.Event(), threading.Event()
    order = []

    def first():
        started.set()
        release.wait()
        order.append(1)
        return DraftStoreResult()

    first_future = worker.submit(first)
    assert started.wait(1)
    second_future = worker.submit(lambda: (order.append(2), DraftStoreResult())[1])
    assert worker.pending_count == 2
    worker.close_intake()
    worker.close_intake()
    assert worker.submit(forbidden).result().code == "cancelled"
    assert worker._thread.daemon
    release.set()
    assert first_future.result(timeout=1).code is None
    assert second_future.result(timeout=1).code is None
    worker._thread.join(1)
    assert worker.pending_count == 0
    assert order == [1, 2]


def test_failure_sanitized_and_callable_released(caplog):
    class Operation:
        def __call__(self):
            raise OSError("SQL_PAYLOAD_SECRET")

    worker = DraftWorker()
    operation = Operation()
    ref = weakref.ref(operation)
    future = worker.submit(operation)
    del operation
    result = future.result(timeout=1)
    worker.close_intake()
    worker._thread.join(1)
    gc.collect()
    assert ref() is None
    assert result.code == "io"
    assert future.exception() is None
    assert "SQL_PAYLOAD_SECRET" not in repr(future) + repr(worker) + repr(result) + caplog.text


def test_submit_close_race_accounts_all_accepted_work():
    for _ in range(20):
        worker = DraftWorker()
        gate = threading.Barrier(2)
        results = []

        def submit(gate=gate, results=results, worker=worker):
            gate.wait()
            results.append(worker.submit(lambda: DraftStoreResult()))

        thread = threading.Thread(target=submit)
        thread.start()
        gate.wait()
        worker.close_intake()
        thread.join(1)
        assert results[0].result(timeout=1).code in {None, "cancelled"}
        if worker._thread is not None:
            worker._thread.join(1)
        assert worker.pending_count == 0


def test_cancelled_queued_future_remains_owned_until_fifo_drains():
    worker = DraftWorker()
    entered, release = threading.Event(), threading.Event()

    def held():
        entered.set()
        release.wait()
        return DraftStoreResult()

    first = worker.submit(held)
    assert entered.wait(1)
    called = []
    queued = worker.submit(lambda: (called.append(True), DraftStoreResult())[1])
    assert queued.cancel()
    assert worker.pending_count == 2
    worker.close_intake()
    release.set()
    assert first.result(timeout=1).code is None
    worker._thread.join(1)
    assert worker.pending_count == 0
    assert not called


def test_idle_worker_releases_completed_future_and_result():
    class Result(DraftStoreResult):
        pass

    worker = DraftWorker()
    result = Result()
    ref = weakref.ref(result)
    future = worker.submit(lambda result=result: result)
    assert future.result(timeout=1) is result
    del result, future
    deadline = time.monotonic() + 1
    while worker.pending_count and time.monotonic() < deadline:
        time.sleep(0.001)
    assert worker.pending_count == 0
    gc.collect()
    assert ref() is None
    worker.close_intake()
    worker._thread.join(1)
