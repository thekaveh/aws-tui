"""Unit tests for the DuckDB port.

Nothing here ever starts a real engine or touches S3. ``NativeDuckDb`` takes
its connection factory as an injected keyword, the same seam
``NativeClipboard`` uses for ``which`` and ``run``
(``tests/unit/infra/test_clipboard.py:3``).
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from aws_tui.infra.duckdb import (
    DuckDbErrorTypes,
    DuckDbOutcome,
    InMemoryDuckDb,
    NativeDuckDb,
)


class _FakeError(Exception):
    pass


class _FakeHttpError(_FakeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FakeInterrupt(_FakeError):
    pass


# Every ``NativeDuckDb`` here injects this. Leaving it off makes ``query`` fall
# through to ``_default_error_types``, which imports the engine -- and the CI
# unit tier installs the project without the ``duckdb`` extra, so such a test
# passes locally and fails on all nine legs. ``test_needs_no_engine_...`` below
# is the guard that keeps it that way.
_FAKE_ERROR_TYPES = DuckDbErrorTypes(base=_FakeError, http=_FakeHttpError, interrupt=_FakeInterrupt)


class _FakeConnection:
    """Records statements and replays a scripted outcome."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.statements: list[str] = []
        self.interrupts = 0
        self.closed = False
        self._raises = raises

    def execute(self, sql: str) -> _FakeConnection:
        self.statements.append(sql)
        if self._raises is not None and sql.lstrip().upper().startswith("SELECT"):
            raise self._raises
        return self

    def description(self) -> Any:  # pragma: no cover - replaced below
        raise NotImplementedError

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [(1, None), (2, "x")]

    def interrupt(self) -> None:
        self.interrupts += 1

    def close(self) -> None:
        self.closed = True


def _connection_with(rows: list[tuple[Any, ...]], columns: list[str]) -> _FakeConnection:
    connection = _FakeConnection()
    connection.fetchall = lambda: rows  # type: ignore[method-assign]
    connection.description = [(name,) for name in columns]  # type: ignore[assignment]
    return connection


def test_loads_extensions_then_creates_the_secret_then_scans() -> None:
    connection = _connection_with([(1,)], ["a"])
    port = NativeDuckDb(connect=lambda: connection, error_types=_FAKE_ERROR_TYPES)

    port.query("SELECT 1", profile="analytics", region="us-east-1")

    joined = " | ".join(connection.statements)
    assert "INSTALL httpfs" in joined
    assert "LOAD iceberg" in joined
    assert "unsafe_enable_version_guessing" in joined
    # The secret must name the caller's profile and region, never the ambient chain.
    assert "PROVIDER credential_chain" in joined
    assert "PROFILE 'analytics'" in joined
    assert "REGION 'us-east-1'" in joined
    # Ordering: the scan runs last.
    assert connection.statements[-1] == "SELECT 1"
    # Ordering: extensions must load before the secret is created.
    joined_upper = " | ".join(connection.statements).upper()
    assert joined_upper.index("LOAD AWS") < joined_upper.index("CREATE OR REPLACE SECRET")
    assert joined_upper.index("LOAD HTTPFS") < joined_upper.index("CREATE OR REPLACE SECRET")


def test_returns_columns_and_stringified_rows_preserving_null() -> None:
    connection = _connection_with([(1, None), (2, "x")], ["n", "label"])
    port = NativeDuckDb(connect=lambda: connection, error_types=_FAKE_ERROR_TYPES)

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.OK
    assert result.columns == ("n", "label")
    # NULL must survive as None, never as the string "NULL".
    assert result.rows == (("1", None), ("2", "x"))


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(403, DuckDbOutcome.FORBIDDEN), (404, DuckDbOutcome.NOT_FOUND)],
)
def test_maps_http_status_codes_to_outcomes(status_code: int, expected: DuckDbOutcome) -> None:
    connection = _FakeConnection(raises=_FakeHttpError(status_code))
    port = NativeDuckDb(
        connect=lambda: connection,
        error_types=_FAKE_ERROR_TYPES,
    )

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is expected
    assert result.error_type == "_FakeHttpError"


def test_maps_a_version_guess_failure_to_not_iceberg() -> None:
    failure = _FakeError("Could not guess Iceberg table version using 'none' compression")
    port = NativeDuckDb(
        connect=lambda: _FakeConnection(raises=failure),
        error_types=_FAKE_ERROR_TYPES,
    )

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.NOT_ICEBERG


def test_maps_a_missing_profile_to_auth_required() -> None:
    failure = _FakeError("Secret Validation Failure: no profile 'x' found in config file")
    port = NativeDuckDb(
        connect=lambda: _FakeConnection(raises=failure),
        error_types=_FAKE_ERROR_TYPES,
    )

    result = port.query("SELECT 1", profile="x", region="r")

    assert result.outcome is DuckDbOutcome.AUTH_REQUIRED


def test_maps_an_interrupt_to_cancelled() -> None:
    port = NativeDuckDb(
        connect=lambda: _FakeConnection(raises=_FakeInterrupt("interrupted")),
        error_types=_FAKE_ERROR_TYPES,
    )

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.CANCELLED


def test_reports_engine_missing_when_the_import_fails() -> None:
    def _no_engine() -> Any:
        raise ImportError("No module named 'duckdb'")

    port = NativeDuckDb(connect=_no_engine)

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.ENGINE_MISSING
    assert result.rows == ()


@pytest.mark.parametrize("failure", [RuntimeError("boom"), OSError("disk full")])
def test_a_connect_failure_that_is_not_an_import_error_is_reported_not_raised(
    failure: Exception,
) -> None:
    """`query()` must return a result for any input — see the module docstring."""

    def _boom() -> Any:
        raise failure

    result = NativeDuckDb(connect=_boom).query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.FAILED
    assert result.error_type == type(failure).__name__


def test_a_quote_in_the_profile_cannot_break_out_of_the_secret_literal() -> None:
    connection = _connection_with([(1,)], ["a"])
    port = NativeDuckDb(connect=lambda: connection, error_types=_FAKE_ERROR_TYPES)

    port.query("SELECT 1", profile="ev'il", region="us-east-1")

    secret = next(s for s in connection.statements if "CREATE OR REPLACE SECRET" in s)
    assert "PROFILE 'ev''il'" in secret


def test_interrupt_during_a_query_reaches_the_live_connection() -> None:
    """`interrupt()` fires from another thread while the scan is running.

    That is the only moment it can do anything: `query()` closes the connection
    on its way out, so interrupting afterwards is meaningless by construction.
    """
    connection = _connection_with([(1,)], ["a"])
    port = NativeDuckDb(connect=lambda: connection, error_types=_FAKE_ERROR_TYPES)
    original_execute = connection.execute

    def _execute_and_interrupt(sql: str) -> object:
        # Stands in for the event-loop thread interrupting the worker mid-scan.
        if sql.lstrip().upper().startswith("SELECT"):
            port.interrupt()
        return original_execute(sql)

    connection.execute = _execute_and_interrupt  # type: ignore[method-assign]

    port.query("SELECT 1", profile="p", region="r")

    assert connection.interrupts == 1


def test_interrupt_after_the_query_returns_is_a_no_op() -> None:
    """The connection is closed and released once `query()` returns."""
    connection = _connection_with([(1,)], ["a"])
    port = NativeDuckDb(connect=lambda: connection, error_types=_FAKE_ERROR_TYPES)
    port.query("SELECT 1", profile="p", region="r")

    port.interrupt()

    assert connection.interrupts == 0


def test_in_memory_fake_records_queries_and_replays_a_result() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))

    result = port.query("SELECT 1", profile="p", region="r")

    assert port.queries == [("SELECT 1", "p", "r")]
    assert result.outcome is DuckDbOutcome.OK
    assert result.rows == (("1",),)


def test_needs_no_engine_installed_to_run_a_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """The port must never import ``duckdb`` when both seams are injected.

    The CI unit tier installs the project WITHOUT the ``duckdb`` extra, so a
    test that reaches ``_default_connect`` or ``_default_error_types`` passes
    locally and fails on every leg. Hiding the module here makes that failure
    local and immediate instead.
    """

    class _Blocker:
        def find_module(self, name: str, path: object = None) -> None:
            if name == "duckdb" or name.startswith("duckdb."):
                raise AssertionError(f"the port imported {name!r}")
            return None

        def find_spec(self, name: str, path: object = None, target: object = None) -> None:
            return self.find_module(name, path)

    monkeypatch.delitem(sys.modules, "duckdb", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])

    connection = _connection_with([(1, None)], ["n", "label"])
    port = NativeDuckDb(connect=lambda: connection, error_types=_FAKE_ERROR_TYPES)

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.OK
    assert result.rows == (("1", None),)


def test_a_failure_resolving_the_error_types_still_closes_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connection opened before that failure must not leak.

    ``self._connection`` used to be published outside the guarded block, so this
    path left an open connection behind and a stale handle for ``interrupt``.
    """
    connection = _connection_with([(1,)], ["a"])

    def _no_types() -> DuckDbErrorTypes:
        raise RuntimeError("engine half-installed")

    monkeypatch.setattr("aws_tui.infra.duckdb._default_error_types", _no_types)

    port = NativeDuckDb(connect=lambda: connection)
    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.FAILED
    assert connection.closed is True
    # The handle was released too, so a late interrupt cannot reach it.
    port.interrupt()
    assert connection.interrupts == 0


def test_an_unrecognised_engine_failure_is_classified_not_raised() -> None:
    """``_classify`` must not assume how many error types it was given.

    It unpacked the tuple positionally inside an ``except`` handler, so any
    other shape raised ``ValueError`` out of ``query``.
    """
    failure = _FakeError("something the taxonomy has never seen")
    port = NativeDuckDb(
        connect=lambda: _FakeConnection(raises=failure),
        error_types=_FAKE_ERROR_TYPES,
    )

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.FAILED
    assert result.error_type == "_FakeError"
