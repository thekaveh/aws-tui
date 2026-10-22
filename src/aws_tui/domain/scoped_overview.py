"""Allowlisted, session-only status projections for explicitly watched scopes."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from aws_tui.infra.redaction import redact_text

MAX_WATCHES = 8
OVERVIEW_PAGE_SIZE = 25
MAX_CONCURRENT_REFRESHES = 2

_SERVICES = {
    "athena": "athena",
    "glue-job": "glue",
    "glue-crawler": "glue",
    "emr-serverless": "emr-serverless",
    "transfers": "s3",
}
_ACTIVE = {
    "athena": frozenset({"QUEUED", "RUNNING"}),
    "glue-job": frozenset({"WAITING", "STARTING", "RUNNING", "STOPPING"}),
    "glue-crawler": frozenset({"RUNNING", "STOPPING"}),
    "emr-serverless": frozenset(
        {"SUBMITTED", "PENDING", "SCHEDULED", "RUNNING", "CANCELLING", "QUEUED"}
    ),
    "transfers": frozenset({"pending", "running"}),
}
_FAILED = {
    "athena": frozenset({"FAILED"}),
    "glue-job": frozenset({"FAILED", "ERROR", "TIMEOUT", "EXPIRED"}),
    "glue-crawler": frozenset({"FAILED"}),
    "emr-serverless": frozenset({"FAILED"}),
    "transfers": frozenset({"failed"}),
}


def _valid_text(value: object, maximum: int, *, empty: bool = False) -> bool:
    return (
        type(value) is str
        and (empty or bool(value.strip()))
        and len(value) <= maximum
        and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
    )


def overview_error(text: str | None) -> str | None:
    """Bound/redact provider summaries; UI must still render them as literal text."""
    if text is None:
        return None
    redacted = redact_text(text)
    return "".join(
        " " if unicodedata.category(char) in {"Cc", "Cs"} else char for char in redacted
    )[:500]


@dataclass(frozen=True, slots=True)
class WatchScope:
    kind: str
    connection_name: str
    region: str
    resource: str

    def __post_init__(self) -> None:
        if (
            self.kind not in _SERVICES
            or not _valid_text(self.connection_name, 256)
            or not _valid_text(self.region, 128)
            or not _valid_text(self.resource, 1024, empty=self.kind == "transfers")
            or (self.kind == "transfers" and self.resource != "")
        ):
            raise ValueError("Invalid watched scope")

    @property
    def service(self) -> str:
        return _SERVICES[self.kind]


@dataclass(frozen=True, slots=True)
class OverviewEntry:
    scope: WatchScope
    identifier: str
    status: str
    times: tuple[tuple[str, datetime | None], ...] = ()
    error_summary: str | None = field(default=None, repr=False)
    last_status: str | None = None

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.identifier, 1024)
            or not _valid_text(self.status, 64)
            or (self.last_status is not None and not _valid_text(self.last_status, 64))
        ):
            raise ValueError("Invalid overview entry identity or status")
        object.__setattr__(self, "error_summary", overview_error(self.error_summary))

    @property
    def group(self) -> Literal["active", "failed", "other"]:
        if self.status in _ACTIVE[self.scope.kind]:
            return "active"
        status = self.status
        if self.scope.kind == "glue-crawler" and status == "READY":
            status = self.last_status or status
        return "failed" if status in _FAILED[self.scope.kind] else "other"


@dataclass(frozen=True, slots=True)
class OverviewPage:
    entries: tuple[OverviewEntry, ...]
    cap: str | None = None
