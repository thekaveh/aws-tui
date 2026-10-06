"""Shared admission limits for one read-only preview operation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

RAW_PREVIEW_BYTES = 64 * 1024
PREVIEW_SNIFF_BYTES = 4 * 1024
PREVIEW_MAX_BYTES = 8 * 1024 * 1024
PREVIEW_MAX_REQUESTS = 32
PREVIEW_MAX_RANGE_BYTES = 1024 * 1024
PARQUET_MAX_FOOTER_BYTES = 512 * 1024
PARQUET_MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
PREVIEW_MAX_ROWS = 50
PREVIEW_MAX_COLUMNS = 24
PREVIEW_MAX_CELL_CHARS = 256
PREVIEW_MAX_NESTING = 16
PREVIEW_MAX_NODES = 4096
PREVIEW_MAX_RENDER_CHARS = 256 * 1024
PREVIEW_TIMEOUT_SECONDS = 5.0
PREVIEW_CLEANUP_SECONDS = 1.0


class PreviewLimitExceeded(Exception):
    """The shared preview admission budget or deadline was exhausted."""


class PreviewRequestKind(StrEnum):
    OPEN = "open"
    RANGE = "range"
    VALIDATE = "validate"


@dataclass(slots=True)
class PreviewBudget:
    deadline: float
    clock: Callable[[], float]
    requests: int = 0
    bytes_requested: int = 0

    @classmethod
    def start(cls, *, clock: Callable[[], float] = monotonic) -> PreviewBudget:
        return cls(clock() + PREVIEW_TIMEOUT_SECONDS, clock)

    def remaining_seconds(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise PreviewLimitExceeded("Preview timed out")
        return remaining

    def check(self) -> None:
        self.remaining_seconds()

    def charge_request(self, *, kind: PreviewRequestKind, length: int = 0) -> None:
        self.check()
        if length < 0 or length > PREVIEW_MAX_RANGE_BYTES:
            raise PreviewLimitExceeded("Preview range exceeds budget")
        if kind is not PreviewRequestKind.RANGE and length:
            raise ValueError("only range requests may charge bytes")
        reserve = 0 if kind is PreviewRequestKind.VALIDATE else 1
        if self.requests + 1 + reserve > PREVIEW_MAX_REQUESTS:
            raise PreviewLimitExceeded("Preview request budget exceeded")
        if self.bytes_requested + length > PREVIEW_MAX_BYTES:
            raise PreviewLimitExceeded("Preview byte budget exceeded")
        self.requests += 1
        self.bytes_requested += length
