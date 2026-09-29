# DuckDB Iceberg Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a seventh "Peek" pane to the Glue Iceberg view that previews rows of the selected Iceberg table by querying S3 locally with DuckDB, pinned to the selected snapshot when one is chosen.

**Architecture:** Four pieces, one per layer. A subprocess-free port in `infra/` owns the DuckDB connection and takes plain strings. A pure function in `domain/` builds the single statement that runs. A view model in `vm/glue/` offloads the blocking call and classifies the outcome. The pane in `ui/` only binds. The port is injected at composition, exactly as `ClipboardPort` is.

**Tech Stack:** Python 3.11-3.13, DuckDB 1.5.5 as an optional extra, Textual 8.2.8, VMx 3.23, anyio, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-duckdb-iceberg-preview-design.md`

## 1. Global Constraints

- **MVVM is not negotiable.** UI logic lives in view models built from VMx primitives; the View only binds. Views bind to a per-VM `ObserverSafeSubject` (`vm.on_property_changed`). Never filter the shared `MessageHub`. Never push state from a parent widget into children.
- **`scripts/check-layers.sh` forbids `infra/` importing `aws_tui.domain`.** `DuckDbPort` and `DuckDbResult` are defined inside `infra/duckdb.py` and take plain strings. No `TableRef`, no `QueryContext`.
- **Both offloads are mandatory.** `anyio.to_thread.run_sync` in the view model, never `asyncio.to_thread`. `_run_lifecycle_worker` at the caller.
- **Tests never spawn a real engine or touch S3.** `NativeDuckDb` takes `connect` as an injected keyword, the way `NativeClipboard` injects `which` and `run`.
- **Waits in tests use `tests/helpers.wait_until`** against an observable condition. A count of `pilot.pause()` calls is not a bounded wait: a pause yields one scheduler cycle without advancing the clock.
- **Row limit steps are `100`, `1000`, `10000`.** The ceiling matches Athena's `_MAX_RESULT_ROWS` (`src/aws_tui/vm/athena/results_vm.py:55`).
- **DuckDB is an optional extra.** `duckdb>=1.3,<2` under `[project.optional-dependencies]`. Import lazily; never import `duckdb` at module scope in any file that loads on startup.
- Commit trailer, literally: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Commit as `1766308+thekaveh@users.noreply.github.com` or the push is rejected.
- Any ledgered dependency change updates `docs/contract-ledger.md` in the SAME PR or the `documentation contracts` job fails.

## 2. Resolved open questions

The spec's §9 left four open. They are settled here; do not reopen them.

**1. The row-limit control does not exist.** No new widget. The existing `#glue-iceberg-more` button serves Peek, and on Peek it means "re-run at the next limit step". This is genuinely honest load-more, which the six sibling panes are not: they widen a local window and issue no query (`src/aws_tui/vm/glue/iceberg_vm.py:498`).

**2. Exactly one new focus slot.** `FocusSlot.GLUE_ICEBERG_PREVIEW = "glue.iceberg.preview"` for the tab. The `more` button reuses the existing `GLUE_ICEBERG_MORE`. Slots live at `src/aws_tui/vm/chrome/focus_coordinator_vm.py:67-76` and the id map at `src/aws_tui/ui/widgets/glue/page.py:67-77`.

**3. No App cleanup-registry step.** The preview view model joins the Iceberg view model's existing drain machinery: `clear_table_and_drain` (`iceberg_vm.py:326`), `cancel_metadata_loads_and_drain_silently` (`:345`), `begin_shutdown` (`:360`), `shutdown` (`:369`). Cancellation calls `connection.interrupt()`.

**4. CI installs the extra in the unit job only.** `.github/workflows/ci.yml:42` gains `--extra duckdb`. The lowest-supported-dependencies job installs `.` without extras (`ci.yml:18` of that job) and runs a fixed file list, so the DuckDB floor test is guarded with `pytest.importorskip("duckdb")` and skips there. The engine-absent test forces `ImportError` and therefore runs everywhere regardless of installation.

## 3. File structure

| File | Responsibility |
|---|---|
| `src/aws_tui/infra/duckdb.py` (create) | `DuckDbPort`, `DuckDbResult`, `DuckDbOutcome`, `NativeDuckDb`, `InMemoryDuckDb` |
| `src/aws_tui/domain/iceberg_preview.py` (create) | `iceberg_preview_sql()` and `next_row_limit()` |
| `src/aws_tui/vm/glue/iceberg_preview_vm.py` (create) | `IcebergPreviewVM` |
| `src/aws_tui/vm/chrome/focus_coordinator_vm.py` (modify) | one new `FocusSlot` member |
| `src/aws_tui/vm/glue/iceberg_vm.py` (modify) | hold the location, own the preview VM, extend drains |
| `src/aws_tui/vm/glue/catalog_vm.py` (modify) | pass `storage.location` into `bind_table` |
| `src/aws_tui/ui/widgets/glue/iceberg_view.py` (modify) | the Peek tab and its rendering |
| `src/aws_tui/ui/widgets/glue/page.py` (modify) | focus slot id map |
| `src/aws_tui/composition.py` (modify) | construct and inject the port |
| `pyproject.toml`, `uv.lock`, `docs/contract-ledger.md`, `.github/workflows/ci.yml` | packaging |

---

## 4. Tasks

### 4.1. Task 1: Packaging — the optional extra and its ledger row

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (generated)
- Modify: `docs/contract-ledger.md`
- Modify: `tests/docs/test_contract_parity.py`
- Modify: `.github/workflows/ci.yml:42`
- Test: `tests/minimum_runtime/test_dependency_floors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the `duckdb` distribution available under the `duckdb` extra, so later tasks may `import duckdb` inside a function body.

- [ ] **Step 1: Add the optional-dependencies table**

No `[project.optional-dependencies]` table exists today. Add it immediately after the `dependencies` list in `pyproject.toml`:

```toml
[project.optional-dependencies]
# Local Iceberg preview. Optional because the wheel is ~14 MB per platform and
# the feature is one pane. Next-major cap matches the policy on `dependencies`:
# insulate `pip install aws-tui` from a transitive breaking change.
duckdb = ["duckdb>=1.3,<2"]
```

- [ ] **Step 2: Lock it**

Run: `uv lock`
Expected: `uv.lock` gains a `duckdb` package entry. Commit the lock.

- [ ] **Step 3: Record the exact locked version**

Run: `uv run python -c "import tomllib;d=tomllib.load(open('uv.lock','rb'));print(next(p['version'] for p in d['package'] if p['name']=='duckdb'))"`
Write the printed version down; Step 4 needs it verbatim.

- [ ] **Step 4: Add the contract-ledger row**

In `docs/contract-ledger.md`, inside the **current dated pass** (the most recent `## N. <date>` section), add a row to its table. Replace `<VERSION>` with Step 3's output:

```markdown
| Local DuckDB Iceberg preview | `duckdb==<VERSION>` from `uv.lock`, optional extra `duckdb` | Extensions `httpfs`, `aws`, `iceberg` loaded per connection. `iceberg_scan(path)` with the named parameter `snapshot_from_id`. `CREATE OR REPLACE SECRET (TYPE s3, PROVIDER credential_chain, PROFILE, REGION)`. The setting `unsafe_enable_version_guessing`. `DuckDBPyConnection.interrupt()` for cancellation. Errors consumed as `duckdb.HTTPException` with `.status_code`, and `duckdb.InterruptException`. | Port unit tests drive an injected fake connection; no test spawns a real engine. A floor test under `tests/minimum_runtime/` exercises the real package when the extra is installed. |
```

- [ ] **Step 5: Extend the parity test's package tuple**

In `tests/docs/test_contract_parity.py`, find the tuple of package names near line 278 that contains `"sqlglot"` and add `"duckdb"` to it, keeping alphabetical order if the existing entries are ordered.

- [ ] **Step 6: Run the parity test to verify it passes**

Run: `uv run pytest tests/docs/test_contract_parity.py::test_dependency_ledger_matches_locked_runtime_and_build_versions -v`
Expected: PASS. A failure here means the version in the ledger does not match `uv.lock`.

- [ ] **Step 7: Write the floor test**

Append to `tests/minimum_runtime/test_dependency_floors.py`:

```python
def test_duckdb_floor_loads_iceberg_extensions_and_supports_interrupt() -> None:
    """The declared duckdb floor must actually provide what the port uses.

    Skipped in the lowest-supported-dependencies CI job, which installs the
    project without extras. It runs in the unit matrix, where the extra is
    installed.
    """
    duckdb = pytest.importorskip("duckdb")

    connection = duckdb.connect()
    try:
        for extension in ("httpfs", "aws", "iceberg"):
            connection.execute(f"INSTALL {extension}")
            connection.execute(f"LOAD {extension}")
        connection.execute("SET unsafe_enable_version_guessing = true")
        assert hasattr(connection, "interrupt")
        assert issubclass(duckdb.HTTPException, duckdb.Error)
        assert issubclass(duckdb.InterruptException, duckdb.Error)
    finally:
        connection.close()
```

- [ ] **Step 8: Run the floor test**

Run: `uv run --extra duckdb pytest tests/minimum_runtime/test_dependency_floors.py::test_duckdb_floor_loads_iceberg_extensions_and_supports_interrupt -v`
Expected: PASS. Without `--extra duckdb` it must report SKIPPED, not fail.

- [ ] **Step 9: Teach the unit CI job to install the extra**

In `.github/workflows/ci.yml`, change line 42 from:

```yaml
        run: uv sync --locked --python ${{ matrix.python }}
```

to:

```yaml
        run: uv sync --locked --python ${{ matrix.python }} --extra duckdb
```

Leave every other `uv sync` line unchanged. In particular the lowest-supported-dependencies job must keep installing `.` without extras, so the `importorskip` path stays exercised.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock docs/contract-ledger.md tests/docs/test_contract_parity.py tests/minimum_runtime/test_dependency_floors.py .github/workflows/ci.yml
git commit -m "build: add duckdb as an optional extra with its ledger row

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 4.2. Task 2: The SQL generator

**Files:**
- Create: `src/aws_tui/domain/iceberg_preview.py`
- Test: `tests/unit/domain/test_iceberg_preview.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ROW_LIMIT_STEPS: Final[tuple[int, ...]] = (100, 1000, 10000)`
  - `iceberg_preview_sql(location: str, *, limit: int, snapshot_id: int | None = None) -> str`
  - `next_row_limit(limit: int) -> int | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/domain/test_iceberg_preview.py`:

```python
"""Unit tests for the Iceberg preview statement generator."""

from __future__ import annotations

import pytest

from aws_tui.domain.iceberg_preview import (
    ROW_LIMIT_STEPS,
    iceberg_preview_sql,
    next_row_limit,
)


def test_generates_a_scan_bounded_by_the_limit() -> None:
    sql = iceberg_preview_sql("s3://bucket/warehouse/db/tbl", limit=100)

    assert sql == (
        "SELECT * FROM iceberg_scan('s3://bucket/warehouse/db/tbl') LIMIT 100"
    )


def test_pins_the_snapshot_when_one_is_selected() -> None:
    sql = iceberg_preview_sql("s3://b/t", limit=100, snapshot_id=4201)

    assert sql == (
        "SELECT * FROM iceberg_scan('s3://b/t', snapshot_from_id := 4201) LIMIT 100"
    )


def test_escapes_a_single_quote_in_the_location() -> None:
    sql = iceberg_preview_sql("s3://b/it's", limit=100)

    assert "iceberg_scan('s3://b/it''s')" in sql


@pytest.mark.parametrize("location", ["", "   ", "https://example.com/t", "/tmp/t"])
def test_rejects_anything_that_is_not_an_s3_uri(location: str) -> None:
    with pytest.raises(ValueError, match="s3:// location"):
        iceberg_preview_sql(location, limit=100)


@pytest.mark.parametrize("limit", [0, -1, 7, 999])
def test_rejects_a_limit_that_is_not_a_declared_step(limit: int) -> None:
    with pytest.raises(ValueError, match="row limit"):
        iceberg_preview_sql("s3://b/t", limit=limit)


@pytest.mark.parametrize("snapshot_id", [-1, True])
def test_rejects_an_invalid_snapshot_id(snapshot_id: object) -> None:
    # `True` is an int subclass and must not pass as a snapshot id, matching
    # select_starter_sql at src/aws_tui/domain/sql_policy.py:561.
    with pytest.raises(ValueError, match="snapshot ID"):
        iceberg_preview_sql("s3://b/t", limit=100, snapshot_id=snapshot_id)  # type: ignore[arg-type]


def test_next_row_limit_walks_the_steps_then_stops() -> None:
    assert next_row_limit(100) == 1000
    assert next_row_limit(1000) == 10000
    assert next_row_limit(10000) is None


def test_row_limit_steps_are_ascending_and_capped_at_ten_thousand() -> None:
    assert ROW_LIMIT_STEPS == (100, 1000, 10000)
    assert list(ROW_LIMIT_STEPS) == sorted(ROW_LIMIT_STEPS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_iceberg_preview.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aws_tui.domain.iceberg_preview'`

- [ ] **Step 3: Write the implementation**

Create `src/aws_tui/domain/iceberg_preview.py`:

```python
"""Build the one statement the Iceberg preview pane runs.

This module is the only producer of SQL for the preview. No user-entered text
reaches the DuckDB port, so there is no SQL policy here and none is needed --
see the design spec's non-goals. Keep it that way: if an editor is ever added,
it gets a read-only policy of its own first.
"""

from __future__ import annotations

from typing import Final

ROW_LIMIT_STEPS: Final[tuple[int, ...]] = (100, 1000, 10000)
"""Selectable row limits, ascending.

The ceiling matches Athena's ``_MAX_RESULT_ROWS``
(``src/aws_tui/vm/athena/results_vm.py:55``) so one engine cannot quietly
return far more rows into the same kind of table than the other.
"""

__all__ = ["ROW_LIMIT_STEPS", "iceberg_preview_sql", "next_row_limit"]


def next_row_limit(limit: int) -> int | None:
    """The next larger step, or ``None`` when ``limit`` is already the largest."""
    for step in ROW_LIMIT_STEPS:
        if step > limit:
            return step
    return None


def _quote_literal(value: str) -> str:
    """Single-quote a SQL string literal, doubling embedded quotes."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def iceberg_preview_sql(
    location: str,
    *,
    limit: int,
    snapshot_id: int | None = None,
) -> str:
    """Build the preview scan for one Iceberg table root.

    ``location`` is the table's physical S3 root, not a metadata file: the
    exact ``metadata_location`` pointer is unavailable because
    ``TableDetail.__post_init__`` redacts every table parameter
    (``src/aws_tui/domain/data_catalog.py:145-151``). The caller therefore
    enables version guessing so DuckDB finds the newest metadata itself.
    """
    if not location.strip() or not location.startswith("s3://"):
        raise ValueError("preview requires an s3:// location")
    if limit not in ROW_LIMIT_STEPS:
        raise ValueError(f"row limit must be one of {ROW_LIMIT_STEPS}")
    scan_args = _quote_literal(location)
    if snapshot_id is not None:
        if isinstance(snapshot_id, bool) or not isinstance(snapshot_id, int):
            raise ValueError("snapshot ID must be a non-negative integer")
        if snapshot_id < 0:
            raise ValueError("snapshot ID must be a non-negative integer")
        scan_args = f"{scan_args}, snapshot_from_id := {snapshot_id}"
    return f"SELECT * FROM iceberg_scan({scan_args}) LIMIT {limit}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/test_iceberg_preview.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/domain/iceberg_preview.py tests/unit/domain/test_iceberg_preview.py
git commit -m "feat(domain): add the Iceberg preview statement generator

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 4.3. Task 3: The DuckDB port

**Files:**
- Create: `src/aws_tui/infra/duckdb.py`
- Test: `tests/unit/infra/test_duckdb.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `infra/` must not import `aws_tui.domain`.
- Produces:
  - `class DuckDbOutcome(StrEnum)` with `OK`, `FORBIDDEN`, `NOT_FOUND`, `AUTH_REQUIRED`, `NOT_ICEBERG`, `CANCELLED`, `ENGINE_MISSING`, `FAILED`
  - `@dataclass(frozen=True, slots=True) class DuckDbResult` with `outcome: DuckDbOutcome`, `columns: tuple[str, ...]`, `rows: tuple[tuple[str | None, ...], ...]`, `error_type: str | None`
  - `class DuckDbPort(Protocol)` with `query(sql, *, profile, region) -> DuckDbResult` and `interrupt() -> None`
  - `class NativeDuckDb(DuckDbPort)` with
    `__init__(self, *, connect: Callable[[], Any] | None = None, error_types: tuple[type[BaseException], ...] | None = None)`
    where `error_types` is ordered `(base, http, interrupt)`
  - `class InMemoryDuckDb(DuckDbPort)`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/infra/test_duckdb.py`:

```python
"""Unit tests for the DuckDB port.

Nothing here ever starts a real engine or touches S3. ``NativeDuckDb`` takes
its connection factory as an injected keyword, the same seam
``NativeClipboard`` uses for ``which`` and ``run``
(``tests/unit/infra/test_clipboard.py:3``).
"""

from __future__ import annotations

from typing import Any

import pytest

from aws_tui.infra.duckdb import (
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


class _FakeConnection:
    """Records statements and replays a scripted outcome."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.statements: list[str] = []
        self.interrupts = 0
        self.closed = False
        self._raises = raises

    def execute(self, sql: str) -> "_FakeConnection":
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
    port = NativeDuckDb(connect=lambda: connection)

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


def test_returns_columns_and_stringified_rows_preserving_null() -> None:
    connection = _connection_with([(1, None), (2, "x")], ["n", "label"])
    port = NativeDuckDb(connect=lambda: connection)

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
        error_types=(_FakeError, _FakeHttpError, _FakeInterrupt),
    )

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is expected
    assert result.error_type == "_FakeHttpError"


def test_maps_a_version_guess_failure_to_not_iceberg() -> None:
    failure = _FakeError("Could not guess Iceberg table version using 'none' compression")
    port = NativeDuckDb(
        connect=lambda: _FakeConnection(raises=failure),
        error_types=(_FakeError, _FakeHttpError, _FakeInterrupt),
    )

    result = port.query("SELECT 1", profile="p", region="r")

    assert result.outcome is DuckDbOutcome.NOT_ICEBERG


def test_maps_a_missing_profile_to_auth_required() -> None:
    failure = _FakeError("Secret Validation Failure: no profile 'x' found in config file")
    port = NativeDuckDb(
        connect=lambda: _FakeConnection(raises=failure),
        error_types=(_FakeError, _FakeHttpError, _FakeInterrupt),
    )

    result = port.query("SELECT 1", profile="x", region="r")

    assert result.outcome is DuckDbOutcome.AUTH_REQUIRED


def test_maps_an_interrupt_to_cancelled() -> None:
    port = NativeDuckDb(
        connect=lambda: _FakeConnection(raises=_FakeInterrupt("interrupted")),
        error_types=(_FakeError, _FakeHttpError, _FakeInterrupt),
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


def test_interrupt_reaches_the_live_connection() -> None:
    connection = _connection_with([(1,)], ["a"])
    port = NativeDuckDb(connect=lambda: connection)
    port.query("SELECT 1", profile="p", region="r")

    port.interrupt()

    assert connection.interrupts == 1


def test_in_memory_fake_records_queries_and_replays_a_result() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))

    result = port.query("SELECT 1", profile="p", region="r")

    assert port.queries == [("SELECT 1", "p", "r")]
    assert result.outcome is DuckDbOutcome.OK
    assert result.rows == (("1",),)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/infra/test_duckdb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aws_tui.infra.duckdb'`

- [ ] **Step 3: Write the implementation**

Create `src/aws_tui/infra/duckdb.py`:

```python
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
    import duckdb  # noqa: PLC0415 - optional extra, imported on use

    return duckdb.connect()


def _default_error_types() -> tuple[type[BaseException], ...]:
    import duckdb  # noqa: PLC0415 - optional extra, imported on use

    return (duckdb.Error, duckdb.HTTPException, duckdb.InterruptException)


class NativeDuckDb:
    """The real engine, with its connection factory injected for tests."""

    __slots__ = ("_connect", "_connection", "_error_types")

    def __init__(
        self,
        *,
        connect: Callable[[], Any] | None = None,
        error_types: tuple[type[BaseException], ...] | None = None,
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
        except Exception:  # noqa: BLE001 - interrupt is best effort
            return

    def query(self, sql: str, *, profile: str, region: str) -> DuckDbResult:
        try:
            connection = self._connect()
        except ImportError as exc:
            return DuckDbResult(
                outcome=DuckDbOutcome.ENGINE_MISSING, error_type=type(exc).__name__
            )
        self._connection = connection
        try:
            error_types = self._error_types or _default_error_types()
        except ImportError as exc:
            return DuckDbResult(
                outcome=DuckDbOutcome.ENGINE_MISSING, error_type=type(exc).__name__
            )
        try:
            self._prepare(connection, profile=profile, region=region)
            cursor = connection.execute(sql)
            columns = tuple(str(column[0]) for column in cursor.description)
            rows = tuple(
                tuple(None if value is None else str(value) for value in row)
                for row in cursor.fetchall()
            )
        except error_types as exc:
            return DuckDbResult(
                outcome=_classify(exc, error_types), error_type=type(exc).__name__
            )
        except Exception as exc:  # noqa: BLE001 - never escape into the pump
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
    except Exception:  # noqa: BLE001 - closing must never mask the outcome
        return


def _classify(
    exc: BaseException, error_types: tuple[type[BaseException], ...]
) -> DuckDbOutcome:
    """Map an engine failure, preferring structured fields over message text.

    The Athena path classifies by casefolded message and its own comments call
    that fragile. DuckDB exposes ``HTTPException.status_code``, so use it.
    """
    # ``error_types`` is ordered (base, http, interrupt) by both
    # ``_default_error_types`` and every test that injects it.
    _base, _http, interrupt_type = error_types
    if isinstance(exc, interrupt_type):
        return DuckDbOutcome.CANCELLED
    status = getattr(exc, "status_code", None)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/infra/test_duckdb.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Verify the layer rule still holds**

Run: `./scripts/check-layers.sh`
Expected: `layer rules clean`. A failure means `infra/duckdb.py` imported from `aws_tui.domain`.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/infra/duckdb.py tests/unit/infra/test_duckdb.py
git commit -m "feat(infra): add a DuckDB port with an injected connection factory

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 4.4. Task 4: The preview view model

**Files:**
- Create: `src/aws_tui/vm/glue/iceberg_preview_vm.py`
- Test: `tests/unit/vm/glue/test_iceberg_preview_vm.py`

**Interfaces:**
- Consumes: `DuckDbPort`, `DuckDbOutcome`, `DuckDbResult`, `InMemoryDuckDb` from Task 3. `ROW_LIMIT_STEPS`, `iceberg_preview_sql`, `next_row_limit` from Task 2.
- Produces:
  - `IcebergPreviewVM(port: DuckDbPort, *, hub, dispatcher)`
  - `bind(location: str | None, *, profile: str | None, region: str)` -> None
  - `async load(snapshot_id: int | None = None) -> None`
  - `async load_more(snapshot_id: int | None = None) -> bool`
  - `async cancel() -> None`
  - properties: `available: bool`, `state: PaneState`, `error_text: str | None`, `columns: tuple[str, ...]`, `rows: tuple[tuple[str | None, ...], ...]`, `limit: int`, `has_more: bool`, `snapshot_id: int | None`
  - `on_property_changed` — a per-VM `ObserverSafeSubject[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/vm/glue/test_iceberg_preview_vm.py`:

```python
"""Unit tests for the Iceberg preview view model."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.duckdb import DuckDbOutcome, InMemoryDuckDb
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.iceberg_preview_vm import IcebergPreviewVM


def _build(port: InMemoryDuckDb) -> IcebergPreviewVM:
    hub: MessageHub[Message] = MessageHub()
    vm = IcebergPreviewVM(port=port, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def test_is_unavailable_without_a_location() -> None:
    vm = _build(InMemoryDuckDb())
    vm.bind(None, profile="analytics", region="us-east-1")

    assert vm.available is False


def test_is_unavailable_without_an_aws_profile() -> None:
    # s3-compatible connections carry no profile and are a non-goal.
    vm = _build(InMemoryDuckDb())
    vm.bind("s3://b/t", profile=None, region="us-east-1")

    assert vm.available is False


@pytest.mark.asyncio
async def test_loads_rows_and_reaches_idle() -> None:
    port = InMemoryDuckDb(columns=("a", "b"), rows=(("1", None),))
    vm = _build(port)
    vm.bind("s3://b/t", profile="analytics", region="us-east-1")

    await vm.load()

    assert vm.state is PaneState.IDLE
    assert vm.columns == ("a", "b")
    assert vm.rows == (("1", None),)
    assert port.queries[0][1:] == ("analytics", "us-east-1")


@pytest.mark.asyncio
async def test_pins_the_selected_snapshot_in_the_statement() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://b/t", profile="p", region="r")

    await vm.load(snapshot_id=4201)

    assert "snapshot_from_id := 4201" in port.queries[0][0]
    assert vm.snapshot_id == 4201


@pytest.mark.asyncio
async def test_load_more_reruns_at_the_next_limit() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=tuple((str(n),) for n in range(100)))
    vm = _build(port)
    vm.bind("s3://b/t", profile="p", region="r")
    await vm.load()
    assert vm.limit == 100
    assert vm.has_more is True

    await vm.load_more()

    # A real second query, not a widened local window.
    assert len(port.queries) == 2
    assert "LIMIT 1000" in port.queries[1][0]
    assert vm.limit == 1000


@pytest.mark.asyncio
async def test_has_more_is_false_when_fewer_rows_than_the_limit_return() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://b/t", profile="p", region="r")

    await vm.load()

    assert vm.has_more is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_state", "expected_text"),
    [
        (DuckDbOutcome.FORBIDDEN, PaneState.FORBIDDEN, "S3 access is forbidden for this table"),
        (DuckDbOutcome.NOT_FOUND, PaneState.ERROR, "table location not found"),
        (DuckDbOutcome.AUTH_REQUIRED, PaneState.AUTH_REQUIRED, "AWS authentication is required"),
        (DuckDbOutcome.NOT_ICEBERG, PaneState.ERROR, "not a readable Iceberg table"),
        (DuckDbOutcome.ENGINE_MISSING, PaneState.ERROR, "pip install aws-tui[duckdb]"),
        (DuckDbOutcome.FAILED, PaneState.ERROR, "local preview failed"),
    ],
)
async def test_maps_each_outcome_to_a_pane_state(
    outcome: DuckDbOutcome, expected_state: PaneState, expected_text: str
) -> None:
    vm = _build(InMemoryDuckDb(outcome=outcome))
    vm.bind("s3://b/t", profile="p", region="r")

    await vm.load()

    assert vm.state is expected_state
    assert vm.error_text is not None
    assert expected_text in vm.error_text


@pytest.mark.asyncio
async def test_a_cancelled_query_returns_to_idle_without_an_error() -> None:
    vm = _build(InMemoryDuckDb(outcome=DuckDbOutcome.CANCELLED))
    vm.bind("s3://b/t", profile="p", region="r")

    await vm.load()

    assert vm.state is PaneState.IDLE
    assert vm.error_text is None


@pytest.mark.asyncio
async def test_cancel_interrupts_the_engine() -> None:
    port = InMemoryDuckDb(columns=("a",), rows=(("1",),))
    vm = _build(port)
    vm.bind("s3://b/t", profile="p", region="r")

    await vm.cancel()

    assert port.interrupts == 1


@pytest.mark.asyncio
async def test_publishes_property_changes_on_its_own_subject() -> None:
    # MVVM: the view binds to this, never to the shared hub.
    vm = _build(InMemoryDuckDb(columns=("a",), rows=(("1",),)))
    vm.bind("s3://b/t", profile="p", region="r")
    seen: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=seen.append)

    await vm.load()

    subscription.dispose()
    assert "state" in seen
    assert "rows" in seen
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/vm/glue/test_iceberg_preview_vm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aws_tui.vm.glue.iceberg_preview_vm'`

- [ ] **Step 3: Read the pattern before writing**

Read `src/aws_tui/vm/clipboard_vm.py` in full. It is the canonical port consumer: it offloads with `anyio.to_thread.run_sync`, classifies the port result into an enum on the view-model side, and publishes by assignment. Read `src/aws_tui/vm/file_manager/entry_vm.py` for the per-VM `ObserverSafeSubject` shape that `on_property_changed` must follow.

- [ ] **Step 4: Write the implementation**

Create `src/aws_tui/vm/glue/iceberg_preview_vm.py`. Key requirements, each load-bearing:

```python
"""Preview rows of one Iceberg table through the local DuckDB port.

Offloads with ``anyio.to_thread.run_sync`` -- never ``asyncio.to_thread``,
because this repo runs blocking work through anyio's limiter. The caller must
additionally reach ``load`` through ``_run_lifecycle_worker``: one offload
keeps the scan off the event loop, the other keeps it off the App's message
pump, and the pump is where the next keypress is dequeued
(``src/aws_tui/app.py:2386``). A local scan is CPU-bound and will freeze the
pump harder than a network wait.
"""

from __future__ import annotations

from functools import partial

import anyio
from vmx import ComponentVM

from aws_tui.domain.iceberg_preview import (
    ROW_LIMIT_STEPS,
    iceberg_preview_sql,
    next_row_limit,
)
from aws_tui.infra.duckdb import DuckDbOutcome, DuckDbPort
from aws_tui.vm.file_manager.pane_vm import PaneState
```

Implement with these rules:

1. `bind(location, *, profile, region)` stores the three values and resets state to `PaneState.EMPTY`. `available` is `True` only when `location` is a non-blank `s3://` string **and** `profile` is a non-blank string.
2. `load(snapshot_id=None)` sets `PaneState.LOADING`, notifies, builds SQL via `iceberg_preview_sql(location, limit=self._limit, snapshot_id=snapshot_id)`, then:
   ```python
   result = await anyio.to_thread.run_sync(
       partial(self._port.query, sql, profile=self._profile, region=self._region)
   )
   ```
3. Map `result.outcome` to state and text with this exact table:

   | Outcome | State | Text |
   |---|---|---|
   | `OK` | `IDLE` | `None` |
   | `CANCELLED` | `IDLE` | `None` |
   | `FORBIDDEN` | `FORBIDDEN` | `"S3 access is forbidden for this table"` |
   | `NOT_FOUND` | `ERROR` | `"table location not found"` |
   | `AUTH_REQUIRED` | `AUTH_REQUIRED` | `"AWS authentication is required"` |
   | `NOT_ICEBERG` | `ERROR` | `"not a readable Iceberg table"` |
   | `ENGINE_MISSING` | `ERROR` | `"local preview needs DuckDB: pip install aws-tui[duckdb]"` |
   | `FAILED` | `ERROR` | `"local preview failed"` |

4. `has_more` is `len(self._rows) >= self._limit and next_row_limit(self._limit) is not None`. This is the honest form: a full page means there may be more, and the ceiling is real.
5. `load_more(snapshot_id=None)` advances `self._limit = next_row_limit(self._limit)` and calls `load`. Returns `False` when already at the top step.
6. `cancel()` calls `self._port.interrupt()` through `anyio.to_thread.run_sync`.
7. `on_property_changed` is a per-VM `ObserverSafeSubject[str]`, disposed in `dispose()`. Every state, rows, columns, limit or error change calls `self._notify("<name>")`, which fires the subject. Do not publish these through the shared `MessageHub`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/vm/glue/test_iceberg_preview_vm.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 6: Verify layers and types**

Run: `./scripts/check-layers.sh && uv run mypy`
Expected: `layer rules clean` and `Success: no issues found`.

- [ ] **Step 7: Commit**

```bash
git add src/aws_tui/vm/glue/iceberg_preview_vm.py tests/unit/vm/glue/test_iceberg_preview_vm.py
git commit -m "feat(vm): add the Iceberg preview view model

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 4.5. Task 5: Thread the table location to the Iceberg view model

**Files:**
- Modify: `src/aws_tui/vm/glue/iceberg_vm.py:298` (`bind_table`) and its `__init__`
- Modify: `src/aws_tui/vm/glue/catalog_vm.py` (`__init__` and the `bind_table` call site)
- Modify: `src/aws_tui/vm/glue/page_vm.py:36` (pass the profile down)
- Test: `tests/unit/vm/glue/test_iceberg_vm.py`

**Interfaces:**
- Consumes: `IcebergPreviewVM` from Task 4.
- Produces: `GlueIcebergVM.bind_table(table_ref, table_format=..., location=None)` and a `preview` property returning the `IcebergPreviewVM`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/vm/glue/test_iceberg_vm.py`:

The module's real helpers are `make_vm()` (`tests/unit/vm/glue/test_iceberg_vm.py:144`,
returning `(vm, inspector, hub)`) and `ICEBERG_REF` (`:32`). Extend `make_vm` with an
`aws_profile: str | None = "analytics"` keyword that it forwards to `GlueIcebergVM`.

```python
@pytest.mark.asyncio
async def test_bind_table_passes_the_location_to_the_preview() -> None:
    vm, _inspector, _hub = make_vm()

    vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG, location="s3://b/t")

    assert vm.preview.available is True


@pytest.mark.asyncio
async def test_preview_is_unavailable_for_a_non_iceberg_table() -> None:
    vm, _inspector, _hub = make_vm()

    vm.bind_table(ICEBERG_REF, table_format=TableFormat.HIVE, location="s3://b/t")

    assert vm.preview.available is False


@pytest.mark.asyncio
async def test_preview_is_unavailable_without_an_aws_profile() -> None:
    # s3-compatible connections carry no profile; the pane must stay hidden.
    vm, _inspector, _hub = make_vm(aws_profile=None)

    vm.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG, location="s3://b/t")

    assert vm.preview.available is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/vm/glue/test_iceberg_vm.py -k preview -v`
Expected: FAIL with `TypeError: bind_table() got an unexpected keyword argument 'location'`

- [ ] **Step 3: Implement**

In `src/aws_tui/vm/glue/iceberg_vm.py`:
1. Add `location: str | None = None` as a keyword parameter to `bind_table`.
2. Construct an `IcebergPreviewVM` in `__init__` and expose it as a read-only `preview` property.
3. Add `aws_profile: str | None = None` as an `__init__` keyword on `GlueIcebergVM`. In `bind_table`, after the existing format check, call
   `self._preview.bind(location if bound else None, profile=self._aws_profile, region=table_ref.region)`.
   The region comes from the `TableRef`; **the profile does not** — `TableRef` has no such field (`src/aws_tui/domain/data_catalog.py`, fields are catalog/database/table/connection_name/region). Thread it down instead:
   - `GluePageVM` already holds the `Connection` (`src/aws_tui/vm/glue/page_vm.py:7`). At its `GlueCatalogVM(...)` call (`page_vm.py:36`) pass
     `aws_profile=connection.profile if connection.kind == "aws" else None`.
   - `GlueCatalogVM.__init__` accepts `aws_profile: str | None = None` and forwards it to `GlueIcebergVM` at `catalog_vm.py:101`.
   Passing `None` for non-AWS connections is how the s3-compatible non-goal is enforced: `preview.available` is then `False` and the tab never appears.
4. Extend the four drain methods at `:326`, `:345`, `:360` and `:369` to also cancel the preview: call `await self._preview.cancel()` in the async ones and set the preview to `EMPTY` in `begin_shutdown`.

In `src/aws_tui/vm/glue/catalog_vm.py`, find the existing `bind_table(...)` call and pass `location=detail.storage.location` where `detail` is the `TableDetail` already read at `catalog_vm.py:659`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/vm/glue/ -v`
Expected: PASS. The whole Glue VM suite must stay green, not just the three new tests.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/vm/glue/iceberg_vm.py src/aws_tui/vm/glue/catalog_vm.py src/aws_tui/vm/glue/page_vm.py tests/unit/vm/glue/test_iceberg_vm.py
git commit -m "feat(vm): give the Iceberg VM the table location and a preview child

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 4.6. Task 6: The Peek pane

**Files:**
- Modify: `src/aws_tui/vm/chrome/focus_coordinator_vm.py:76` (add one slot)
- Modify: `src/aws_tui/ui/widgets/glue/iceberg_view.py`
- Modify: `src/aws_tui/ui/widgets/glue/page.py:67-77` (slot id map)
- Modify: `src/aws_tui/composition.py`
- Test: `tests/unit/ui/glue/test_iceberg_view.py`

**Interfaces:**
- Consumes: `GlueIcebergVM.preview` from Task 5; `NativeDuckDb` from Task 3.
- Produces: the `#glue-iceberg-tab-preview` tab and `FocusSlot.GLUE_ICEBERG_PREVIEW`.

- [ ] **Step 1: Add the focus slot**

In `src/aws_tui/vm/chrome/focus_coordinator_vm.py`, after line 76, add:

```python
    GLUE_ICEBERG_PREVIEW = "glue.iceberg.preview"
```

In `src/aws_tui/ui/widgets/glue/page.py`, add to `_ICEBERG_FOCUS_SLOTS`:

```python
    "glue-iceberg-tab-preview": FocusSlot.GLUE_ICEBERG_PREVIEW,
```

and add `FocusSlot.GLUE_ICEBERG_PREVIEW` to the ordered tuple above it, immediately after `FocusSlot.GLUE_ICEBERG_REFS`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/ui/glue/test_iceberg_view.py`:

```python
@pytest.mark.asyncio
async def test_peek_tab_is_present_for_an_iceberg_table() -> None:
    vm, _ = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert pilot.app.query("#glue-iceberg-tab-preview")


@pytest.mark.asyncio
async def test_peek_renders_rows_and_keeps_null_distinct() -> None:
    vm, _ = _build_vm()
    await vm.setup()

    async with _GlueIcebergApp(vm).run_test(size=(100, 30)) as pilot:
        await pilot.click("#glue-iceberg-tab-preview")
        await wait_until(
            lambda: vm.catalog.iceberg.preview.state is PaneState.IDLE,
            what="the preview pane finished loading",
        )
        table = pilot.app.query_one("#glue-iceberg-table", DataTable)

        assert table.row_count > 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/ui/glue/test_iceberg_view.py -k peek -v`
Expected: FAIL — no matching nodes for `#glue-iceberg-tab-preview`.

- [ ] **Step 4: Implement the pane**

In `src/aws_tui/ui/widgets/glue/iceberg_view.py`:
1. Add `"preview"` to `_VIEW_ORDER` (last) and `_VIEW_LABELS` with the label `"Peek"`.
2. The tab is composed only when `self._vm.preview.available`; otherwise it is omitted entirely.
3. In `on_mount`, subscribe to `self._vm.preview.on_property_changed` and call `self._sync()` on each notification. Dispose the subscription in `on_unmount`. Do not filter the shared `MessageHub`, and do not have the parent push state into this widget.
4. When the active view is `"preview"`, render `preview.columns` and `preview.rows` into the existing `#glue-iceberg-table` `DataTable`. Render a `None` cell as the dimmed text `NULL` through the same text-plus-is-null pair used at `src/aws_tui/vm/athena/results_vm.py:154-164`, so a real null is never confused with the literal string.
5. The `#glue-iceberg-more` button, when the active view is `"preview"`, calls `preview.load_more()` through `self._run_lifecycle_worker(...)`, and its `disabled` state reads `not preview.has_more`.
6. The footer reads `f"{len(preview.rows)} rows · limit {preview.limit}"`, plus `f" · snapshot {preview.snapshot_id}"` when a snapshot is pinned. It must not say "more available" — that phrasing belongs to the six panes whose control does not fetch.

- [ ] **Step 5: Wire the port at composition**

In `src/aws_tui/composition.py`, beside the existing clipboard wiring, add:

```python
self.duckdb: DuckDbPort = duckdb_port or NativeDuckDb()
```

and pass it down to the Glue service so it reaches `GlueIcebergVM`. Follow the clipboard precedent: the port owns no resources, so it does not belong in `close_unstarted`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/ui/glue/ -v`
Expected: PASS, including the pre-existing Iceberg view tests.

- [ ] **Step 7: Commit**

```bash
git add src/aws_tui/vm/chrome/focus_coordinator_vm.py src/aws_tui/ui/widgets/glue/iceberg_view.py src/aws_tui/ui/widgets/glue/page.py src/aws_tui/composition.py tests/unit/ui/glue/test_iceberg_view.py
git commit -m "feat(ui): add the Peek pane to the Glue Iceberg view

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 4.7. Task 7: Integration, snapshots, and documentation

**Files:**
- Test: `tests/integration/test_glue_iceberg_preview.py` (create)
- Test: `tests/snapshot/test_glue.py` (modify)
- Modify: `docs/services/glue.md`, `docs/cookbook.md`, `docs/keybindings.md`
- Modify: `docs/superpowers/specs/2026-07-22-glue-athena-services-design.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_glue_iceberg_preview.py` covering three journeys, each driving the app with `InMemoryDuckDb` injected: the pane loads rows and reaches `IDLE`; the `more` button issues a second query at `LIMIT 1000`; and with `outcome=DuckDbOutcome.ENGINE_MISSING` the pane shows the install line. Use `wait_until` on an observable condition, never a count of `pilot.pause()`.

- [ ] **Step 2: Add the snapshot cases**

Add snapshot cases for the pane in its loaded, empty and engine-missing states. Re-record on Python 3.12 with `uv run --python 3.12 pytest tests/snapshot --snapshot-update`, then **read the diff** rather than trusting the pass.

- [ ] **Step 3: Run the full suite**

Run: `uv run --extra duckdb pytest tests/unit tests/integration tests/e2e -q`
Expected: PASS with no new failures.

- [ ] **Step 4: Update the canonical docs**

Add the Peek pane to the Iceberg section of `docs/services/glue.md`, a journey to `docs/cookbook.md` §7, and the tab to `docs/keybindings.md`. State plainly that the pane needs the `duckdb` extra and AWS profile connections, that it runs locally and bills nothing, and that its row limit is a real ceiling re-queried on demand.

- [ ] **Step 5: Record the reversal**

Append a dated note to `docs/superpowers/specs/2026-07-22-glue-athena-services-design.md` below its no-DuckDB line:

```markdown
> **2026-09-24:** The "no DuckDB" constraint above was scoped to the first
> release. It is superseded for the local Iceberg preview pane by
> `docs/superpowers/specs/2026-09-24-duckdb-iceberg-preview-design.md`,
> approved by the maintainer. The original text is left unedited as the
> historical record of this pass.
```

- [ ] **Step 6: Run the doc gates**

Run: `make docs-check && uv run pytest tests/docs -q`
Expected: documentation builds, and the contract parity test passes.

- [ ] **Step 7: Run the static gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && ./scripts/check-layers.sh`
Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add tests/integration/test_glue_iceberg_preview.py tests/snapshot docs
git commit -m "docs+test: cover the Iceberg preview pane and record the DuckDB reversal

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## 5. Landing

Three PRs, each merged with a merge commit and never squashed, matching the repository's ruleset: branch to `develop`, then `develop` to `main`, then `main` back to `develop`. The back-merge is mandatory; skipping it blocks the next promotion. After promoting, verify `git diff origin/main origin/develop` is empty.

Expect one or two reruns of flaky Windows or macOS jobs. Never add retries and never weaken an assertion to go green. Two known flakes have open tickets: [#274](https://github.com/thekaveh/aws-tui/issues/274) and [#276](https://github.com/thekaveh/aws-tui/issues/276).
