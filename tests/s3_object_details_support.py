"""Network-free S3 recorder shared by details provider and real-app tests."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any


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
