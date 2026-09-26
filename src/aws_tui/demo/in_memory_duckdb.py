"""Demo-mode backing for the local Iceberg row preview.

Demo mode must not reach the outside world. A real ``NativeDuckDb`` would, twice
over: ``INSTALL httpfs``/``aws``/``iceberg`` fetches extensions from
extensions.duckdb.org on first use, and the generated statement resolves the
demo profile's credentials and reads S3. Either breaks the no-real-AWS contract
``tests/integration/test_demo_mode.py`` asserts.

This is the demo peer of ``in_memory_glue`` and ``in_memory_athena``, not a test
double: ``infra.duckdb.InMemoryDuckDb`` remains test-only. The composition root
used to seed that test double for demo instead, which put a class documented as
"Test double" on a user-facing path and left the demo rows sitting in
``composition.py``.
"""

from __future__ import annotations

from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbResult

_COLUMNS: tuple[str, ...] = ("event_id", "event_date", "user_id", "payload")

# Shaped like the design spec's worked example, and deliberately including a
# NULL so the pane's dimmed-NULL rendering is visible in demo mode.
_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("8821", "2026-07-24", "u-4410", '{"a": 1}'),
    ("8822", "2026-07-24", None, '{"a": 2}'),
    ("8823", "2026-07-24", "u-4411", '{"a": 3}'),
)


class InMemoryDuckDb:
    """Replays a fixed preview page. Never starts an engine, never reads S3."""

    __slots__ = ("queries",)

    def __init__(self) -> None:
        self.queries: list[tuple[str, str, str]] = []

    def query(self, sql: str, *, profile: str, region: str) -> DuckDbResult:
        self.queries.append((sql, profile, region))
        return DuckDbResult(outcome=DuckDbOutcome.OK, columns=_COLUMNS, rows=_ROWS)

    def interrupt(self) -> None:
        """Nothing to interrupt: the query returns without blocking."""
        return


__all__ = ["InMemoryDuckDb"]
