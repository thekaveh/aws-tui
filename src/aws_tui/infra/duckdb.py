"""Local DuckDB engine behind a port, for previewing Iceberg tables on S3.

The Protocol and its result type live here rather than in ``domain`` because
``scripts/check-layers.sh`` forbids ``infra`` from importing ``aws_tui.domain``
-- the same reason ``ClipboardPort`` is defined in ``infra/clipboard.py``. The
port therefore speaks plain strings: a statement, a profile name and a region.

``duckdb`` is an optional extra, so it is imported inside ``_default_connect``
and never at module scope. A missing engine is a reported outcome, not an
exception: reporting success when nothing happened is the failure this project
already fixed once, in the clipboard path.

``query`` is synchronous and blocks. Callers on an event loop must offload it
with ``anyio.to_thread.run_sync``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DuckDbErrorTypes",
    "DuckDbOutcome",
    "DuckDbPort",
    "DuckDbResult",
    "InMemoryDuckDb",
    "NativeDuckDb",
]

_EXTENSIONS = ("httpfs", "aws", "iceberg")
_SECRET_NAME = "aws_tui_preview"


class DuckDbOutcome(StrEnum):
    """What happened, in terms the view model can map without reading text."""

    OK = "ok"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    AUTH_REQUIRED = "auth_required"
    NOT_ICEBERG = "not_iceberg"
    CANCELLED = "cancelled"
    ENGINE_MISSING = "engine_missing"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DuckDbResult:
    """One query outcome. ``error_type`` is a class name, never engine text.

    DuckDB echoes the failing statement into its message, which carries the S3
    path. Keeping only the class name here means no caller can accidentally log
    that path.
    """

    outcome: DuckDbOutcome
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str | None, ...], ...] = ()
    error_type: str | None = None


@runtime_checkable
class DuckDbPort(Protocol):
    """Run one generated statement against a local engine."""

    def query(self, sql: str, *, profile: str, region: str) -> DuckDbResult: ...

    def interrupt(self) -> None: ...


def _default_connect() -> Any:
    import duckdb

    return duckdb.connect()


@dataclass(frozen=True, slots=True)
class DuckDbErrorTypes:
    """The engine exception classes this port needs, named rather than ordered.

    ``_classify`` used to unpack a bare tuple positionally, so a tuple of any
    other length raised ``ValueError`` out of ``query`` from inside an ``except``
    handler -- turning a classifiable engine failure into a crash. Naming the
    roles removes the arity assumption entirely.
    """

    base: type[BaseException]
    http: type[BaseException]
    interrupt: type[BaseException]

    @property
    def catchable(self) -> tuple[type[BaseException], ...]:
        """For the ``except`` clause. Injected types need not share a base."""
        return (self.base, self.http, self.interrupt)


def _default_error_types() -> DuckDbErrorTypes:
    import duckdb

    return DuckDbErrorTypes(
        base=duckdb.Error, http=duckdb.HTTPException, interrupt=duckdb.InterruptException
    )


class NativeDuckDb:
    """The real engine, with its connection factory injected for tests."""

    __slots__ = ("_connect", "_connection", "_error_types")

    def __init__(
        self,
        *,
        connect: Callable[[], Any] | None = None,
        error_types: DuckDbErrorTypes | None = None,
    ) -> None:
        self._connect = connect or _default_connect
        self._error_types = error_types
        self._connection: Any | None = None

    def interrupt(self) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            connection.interrupt()
        except Exception:
            return

    def query(self, sql: str, *, profile: str, region: str) -> DuckDbResult:
        try:
            connection = self._connect()
        except ImportError as exc:
            return DuckDbResult(outcome=DuckDbOutcome.ENGINE_MISSING, error_type=type(exc).__name__)
        except Exception as exc:
            return DuckDbResult(outcome=DuckDbOutcome.FAILED, error_type=type(exc).__name__)
        # Everything past a successful connect runs under this guard: resolving
        # the error types imports the engine and can raise, and an early return
        # there used to leak the open connection and leave ``self._connection``
        # dangling for a later ``interrupt``.
        try:
            try:
                error_types = self._error_types or _default_error_types()
            except ImportError as exc:
                return DuckDbResult(
                    outcome=DuckDbOutcome.ENGINE_MISSING, error_type=type(exc).__name__
                )
            except Exception as exc:
                return DuckDbResult(outcome=DuckDbOutcome.FAILED, error_type=type(exc).__name__)
            self._connection = connection
            try:
                self._prepare(connection, profile=profile, region=region)
                cursor = connection.execute(sql)
                columns = tuple(str(column[0]) for column in cursor.description)
                rows = tuple(
                    tuple(None if value is None else str(value) for value in row)
                    for row in cursor.fetchall()
                )
            except error_types.catchable as exc:
                return DuckDbResult(
                    outcome=_classify(exc, error_types), error_type=type(exc).__name__
                )
            except Exception as exc:
                return DuckDbResult(outcome=DuckDbOutcome.FAILED, error_type=type(exc).__name__)
        finally:
            self._connection = None
            _close_quietly(connection)
        return DuckDbResult(outcome=DuckDbOutcome.OK, columns=columns, rows=rows)

    def _prepare(self, connection: Any, *, profile: str, region: str) -> None:
        for extension in _EXTENSIONS:
            connection.execute(f"INSTALL {extension}")
            connection.execute(f"LOAD {extension}")
        connection.execute("SET unsafe_enable_version_guessing = true")
        connection.execute(
            f"CREATE OR REPLACE SECRET {_SECRET_NAME} ("
            "TYPE s3, PROVIDER credential_chain, "
            f"PROFILE '{_escape(profile)}', REGION '{_escape(region)}')"
        )


def _escape(value: str) -> str:
    return value.replace("'", "''")


def _close_quietly(connection: Any) -> None:
    try:
        connection.close()
    except Exception:
        return


def _classify(exc: BaseException, error_types: DuckDbErrorTypes) -> DuckDbOutcome:
    """Map an engine failure, preferring structured fields over message text.

    The Athena path classifies by casefolded message and its own comments call
    that fragile. DuckDB exposes ``HTTPException.status_code``, so use it.
    """
    if isinstance(exc, error_types.interrupt):
        return DuckDbOutcome.CANCELLED
    status = getattr(exc, "status_code", None) if isinstance(exc, error_types.http) else None
    if status == 403:
        return DuckDbOutcome.FORBIDDEN
    if status == 404:
        return DuckDbOutcome.NOT_FOUND
    message = str(exc).casefold()
    if "secret validation failure" in message or "no profile" in message:
        return DuckDbOutcome.AUTH_REQUIRED
    if "could not guess iceberg table version" in message or "no version was provided" in message:
        return DuckDbOutcome.NOT_ICEBERG
    return DuckDbOutcome.FAILED


@dataclass(slots=True)
class InMemoryDuckDb:
    """Test double. Records every query and replays a canned result."""

    outcome: DuckDbOutcome = DuckDbOutcome.OK
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str | None, ...], ...] = ()
    error_type: str | None = None
    queries: list[tuple[str, str, str]] = field(default_factory=list)
    interrupts: int = 0

    def query(self, sql: str, *, profile: str, region: str) -> DuckDbResult:
        self.queries.append((sql, profile, region))
        return DuckDbResult(
            outcome=self.outcome,
            columns=self.columns,
            rows=self.rows,
            error_type=self.error_type,
        )

    def interrupt(self) -> None:
        self.interrupts += 1
