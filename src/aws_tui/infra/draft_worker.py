"""A lazy FIFO owner for SQL-bearing synchronous draft operations."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

from aws_tui.infra.athena_draft_store import DraftStoreResult


@dataclass(slots=True)
class _Job:
    operation: Callable[[], DraftStoreResult] = field(repr=False)
    future: Future[DraftStoreResult] = field(repr=False)


class DraftWorker:
    def __init__(self) -> None:
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._lock = threading.Lock()
        self._pending: set[Future[DraftStoreResult]] = set()
        self._thread: threading.Thread | None = None
        self._closed = False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def submit(self, operation: Callable[[], DraftStoreResult]) -> Future[DraftStoreResult]:
        future: Future[DraftStoreResult] = Future()
        with self._lock:
            if self._closed:
                future.set_result(DraftStoreResult(code="cancelled"))
                future.result()
                return future
            self._pending.add(future)
            self._queue.put(_Job(operation, future))
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="athena-draft-worker", daemon=True
                )
                self._thread.start()
        return future

    def close_intake(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread is not None:
                self._queue.put(None)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            future = job.future
            operation: Callable[[], DraftStoreResult] | None = job.operation
            job = None
            result: DraftStoreResult | None = None
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        assert operation is not None
                        result = operation()
                    except Exception:
                        result = DraftStoreResult(code="io")
                    future.set_result(result)
            finally:
                operation = None
                if future.done() and not future.cancelled():
                    future.result()
                result = None
                with self._lock:
                    self._pending.discard(future)
                    del future
