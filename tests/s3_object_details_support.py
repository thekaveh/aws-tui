"""Network-free S3 recorder shared by details provider and real-app tests."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.s3_object_details import S3ObjectDetails


@dataclass
class ReadBarrier:
    """Signal an attempted read and hold it until released (or cancelled)."""

    outcome: dict[str, Any] | BaseException = field(default_factory=dict)
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def read(self) -> dict[str, Any]:
        self.entered.set()
        await self.release.wait()
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


Outcome = dict[str, Any] | BaseException | ReadBarrier


class RecordingClient:
    """Permit queued read outcomes only; record every attempted operation."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.outcomes: dict[str, deque[Outcome]] = defaultdict(deque)
        self.enter_count = 0
        self.exit_count = 0
        self.exit_types: list[type[BaseException] | None] = []

    def queue(self, operation: str, *outcomes: Outcome) -> None:
        self.outcomes[operation].extend(outcomes)

    async def __aenter__(self) -> RecordingClient:
        self.enter_count += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_count += 1
        self.exit_types.append(exc_type)

    def __getattr__(self, operation: str) -> Any:
        async def read(**kwargs: Any) -> dict[str, Any]:
            self.calls.append((operation, dict(kwargs)))
            if operation not in {
                "head_object",
                "get_object_tagging",
                "list_buckets",
                "list_objects_v2",
            }:
                raise AssertionError(f"Unexpected or mutating S3 operation: {operation}")
            if not self.outcomes[operation]:
                raise AssertionError(f"Unqueued S3 operation: {operation}")
            outcome = self.outcomes[operation].popleft()
            if isinstance(outcome, ReadBarrier):
                return await outcome.read()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return read


class RecordingSession:
    """Return one async client context, recording its configuration."""

    def __init__(self, client: RecordingClient | None = None) -> None:
        self.s3 = client if client is not None else RecordingClient()
        self.client_calls: list[tuple[str, dict[str, Any]]] = []
        self.enter_count = 0
        self.exit_count = 0
        self.exit_types: list[type[BaseException] | None] = []

    def client(self, service_name: str, **kwargs: Any) -> RecordingClient:
        self.client_calls.append((service_name, dict(kwargs)))
        assert service_name == "s3"
        return self.s3

    async def __aenter__(self) -> RecordingSession:
        self.enter_count += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_count += 1
        self.exit_types.append(exc_type)


@dataclass
class DetailsReadBarrier:
    """Hold a details outcome, optionally finishing despite cancellation."""

    outcome: S3ObjectDetails | Exception
    cancellation_resistant: bool = False
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    async def read(self) -> S3ObjectDetails:
        self.entered.set()
        try:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.cancellation_resistant:
                    raise
                await self.release.wait()
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome
        finally:
            self.finished.set()


DetailsOutcome = S3ObjectDetails | Exception | DetailsReadBarrier | asyncio.Future[S3ObjectDetails]


class DetailsInMemoryFS(InMemoryFS):
    """Real listing/selection fixture with an optional details capability."""

    def __init__(self) -> None:
        super().__init__()
        self.details_paths: list[PathRef] = []
        self.details_outcomes: deque[DetailsOutcome] = deque()

    def queue_details(self, *outcomes: DetailsOutcome) -> None:
        self.details_outcomes.extend(outcomes)

    async def read_object_details(self, path: PathRef) -> S3ObjectDetails:
        self.details_paths.append(path)
        if not self.details_outcomes:
            raise AssertionError(f"Unqueued details read: {path}")
        outcome = self.details_outcomes.popleft()
        if isinstance(outcome, DetailsReadBarrier):
            return await outcome.read()
        if isinstance(outcome, asyncio.Future):
            return await outcome
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class HeldDetailsClipboard:
    """Recording native-port replacement with explicit helper-thread teardown."""

    def __init__(self) -> None:
        from threading import Event

        self.entered = Event()
        self.release = Event()
        self.finished = Event()
        self.writes: list[str] = []

    def write(self, text: str):
        from aws_tui.infra.clipboard import ClipboardResult

        self.writes.append(text)
        self.entered.set()
        try:
            if not self.release.wait(5):
                raise AssertionError("details clipboard barrier was not released")
            return ClipboardResult(ok=True, mechanism="held-fixture")
        finally:
            self.finished.set()
