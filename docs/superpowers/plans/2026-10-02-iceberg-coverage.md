# Iceberg Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Complete every acceptance criterion in issue #236 across all six tabs.

**Architecture:** Carry immutable collection coverage from the bounded Athena
runner through the inspector to independent VM panes and the footer. Request one
sentinel beyond each existing display cap; preserve generation-based lifecycle.

**Tech Stack:** Python 3.12, dataclasses, pytest/asyncio, Textual, vmx.

**Spec:** `docs/superpowers/specs/2026-10-02-iceberg-coverage-design.md`

## 1. Global Constraints

- Caps stay snapshots/history/refs 100, manifests/partitions 500, files 1000.
- At most one sentinel beyond the display cap; never map or render the sentinel.
- Exactly-full batches need sentinel evidence to be labelled truncated.
- Coverage describes the selected table's named metadata collection at inspection time.
- Load more only reveals cached rows; fresh inspection is explicit.
- Failed refresh retains last known rows and coverage; stale completion never overwrites a new binding.
- Metadata row limits do not bound scanned bytes or Athena costs.
- Local gates only; no GitHub Actions dispatch or live AWS mutations.

## 2. Task 1: Deliver the coverage contract from query to rendered footer

**Files:**
- Modify: `src/aws_tui/domain/athena_runner.py`, `src/aws_tui/domain/iceberg.py`
- Modify: `src/aws_tui/vm/glue/iceberg_vm.py`, `src/aws_tui/services/glue/service.py`
- Modify: `src/aws_tui/ui/widgets/glue/iceberg_view.py`
- Modify built-in demo response producers where necessary.
- Tests: `tests/unit/domain/test_athena_runner.py`, `tests/unit/domain/test_iceberg.py`, `tests/unit/vm/glue/test_iceberg_vm.py`, `tests/unit/ui/glue/test_iceberg_view.py`, `tests/snapshot/test_glue.py`, affected service/demo tests.
- Modify: `docs/cookbook.md` §7.1; regenerate documentation only through project tools.

**Interfaces:**
- Consumes: existing `BoundedQueryResult`, six `list_<view>(TableRef)` methods, `_MetadataPane` lifecycle and footer refresh.
- Produces: `BoundedQueryResult.source_exhausted: bool | None = None`.
- Produces: frozen `IcebergCoverage(status, row_limit, collection, table_ref)` and generic frozen `IcebergInspection[T](rows: tuple[T, ...], coverage: IcebergCoverage)`.
- All six domain/contextual inspector methods return the corresponding typed `IcebergInspection`.
- VM exposes active `coverage` and `fetched_count`; per-view access allows independent coverage assertions.

- [ ] **Step 1: Add boundary regression tests before implementation.**

```python
@pytest.mark.parametrize("count", [0, 99, 100, 101])
async def test_snapshot_coverage_boundary(count):
    # Use valid snapshot rows and the existing RecordingRunner fixture.
    result = await inspector.list_snapshots(TABLE)
    assert result.coverage.status == ("truncated" if count > 100 else "complete")
    assert result.coverage.row_limit == 100
    assert result.coverage.collection == "snapshots"
    assert result.coverage.table_ref == TABLE
    assert len(result.rows) == min(count, 100)
    assert runner.calls[0][3] == 101
    assert runner.sql.endswith("LIMIT 101")
```

Provide valid fixtures for all six collections, parametrizing zero, cap-1,
cap, cap+1. Explicit exhausted evidence belongs in the runner fixture. Add
unknown exhaustion/no-sentinel tests and runner tests for terminal page,
continuation token, and page slicing. Record failing command/output.

Run `.venv/bin/python -m pytest tests/unit/domain/test_iceberg.py tests/unit/domain/test_athena_runner.py`.
Expected: new coverage assertions fail because no coverage contract exists.

- [ ] **Step 2: Implement bounded result evidence and inspection values.**

```python
# Inside bounded pagination, after obtaining remaining, page_rows, next_token:
source_exhausted = next_token is None and len(page_rows) <= remaining
# Carry this evidence with the collected rows into BoundedQueryResult.
# Inside inspection after requesting SQL LIMIT cap+1 and max_rows=cap+1:
status = "truncated" if len(result.rows) > cap else (
    "complete" if result.source_exhausted is True else "unknown"
)
# Slice to cap before mapper and pair mapped rows with collection coverage.
```

Partitions use the same cap+1 evidence; partition_spec retains its type.
Reject over-bound/malformed results instead of inventing coverage. Update
existing explicit tuple expectations to structured `.rows` expectations.
Run the Step 1 command. Expected: all domain/runner tests pass.

- [ ] **Step 3: Add all-six-view lifecycle and provider regressions, then implement VM propagation.**

```python
# For each view, inject structured truncated rows with matching collection/cap.
await vm.ensure_loaded(view)
coverage = vm.coverage_for(view)
calls = len(inspector.calls)
while vm.has_more:
    await vm.load_more()
assert vm.coverage == coverage
assert len(inspector.calls) == calls
# A refresh error retains coverage. Rebinding resets it; release an old blocked
# completion and prove it cannot replace new rows or coverage.
```

Add complete/unknown/truncated cases, invalid identity/cap/status/count payloads,
retry success, cancellation, failed first load, stale table/view completion and
demo/contextual provider propagation. Preserve existing stable-state and
generation safeguards. Bare tuples may remain accepted but with unknown
coverage. Run affected VM/service/demo tests before and after implementation;
record expected RED and GREEN output.

- [ ] **Step 4: Add running-pilot footer regressions and Glue snapshot, then implement footer and docs.**

```python
# A mounted running Textual pilot, truncated 100-row snapshots, page size 50:
assert "50 visible" in str(footer.render())
assert "100 fetched" in str(footer.render())
assert "truncated" in str(footer.render())
await vm.load_more()
# Wait for the actual footer repaint using existing bounded UI wait helpers.
assert "100 visible" in str(footer.render())
assert "truncated" in str(footer.render())
```

Cover complete and unknown coverage, failed-refresh warning persistence, empty
complete result and per-tab switching. Capture a deterministic running Glue
snapshot with explicit coverage state. Inspect the changed rendered snapshot;
update only snapshots reflecting the intended footer. Document caps as metadata
row limits, cached reveal, sentinel evidence and unbounded bytes scanned.
Run all affected unit tests and `tests/snapshot/test_glue.py`; expected GREEN.

- [ ] **Step 5: Self-review every #236 AC, commit, report evidence.**

Run applicable focused tests, lint/format/types/architecture for affected code.
Controller runs the complete suite/coverage and docs/build gates once before
delivery. Commit coherent Conventional Commit changes. Report exact commands,
RED/GREEN evidence, all AC-to-test mappings, changed snapshot inspection and any
limitations. Do not push, merge or close the issue: controller owns delivery.

## 3. Review Focus

Check malformed structured responses, source exhaustion when a page is sliced,
sentinel removal before mapping, independent coverage after view switching,
stable-state cancellation/error restoration, stale table/view identity, legacy
unknown coverage, and demo/service propagation. Confirm footer state remains
truthful when rows are hidden by error/loading placeholders. No full-scan or
cost-bound claims. Each AC must have direct evidence rather than a passing test
whose scope omits it.
