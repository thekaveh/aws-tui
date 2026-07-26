# Task 3 Report: Athena Query and Result ViewModels

## Status

Implemented Athena plan Task 3 only on `codex/aws-service-expansion-study`.

## Implementation

- Added `AthenaQueryVM` with VMx async execute/cancel commands, fail-closed SQL
  and context validation, deterministic context-plus-UUID request tokens, and
  double-submit suppression.
- Added app-owned query lifecycle tracking. Explicit cancel, context replacement,
  and shutdown stop only the active query started by this VM; terminal observation
  revokes ownership.
- Added bounded polling at `0.25, 0.5, 1, 2, 4, 5, 5...` seconds, structured
  state/statistics/error detail, and first-page result loading on success.
- Added generation checks after every query and result await. Shutdown, cancel,
  context replacement, query replacement, and disposal invalidate stale work.
  Late submit responses are stopped without publishing stale errors.
- Added `AthenaResultsVM` with VMx `TokenPagedComposition`. It fetches one Athena
  result page at a time, accumulates only pages explicitly requested, preserves
  domain-owned header handling, and rejects column metadata changes between pages.
- Added null-aware render cells. Raw rows preserve `None`, empty strings, and
  literal `"NULL"` independently; render-cell reprs never include result values.
- Added stable pane-local error mapping that never copies provider, SQL, result,
  request-token, or raw exception text into VM errors.
- Added idempotent synchronous disposal of VMx commands, pagers, subjects, and
  component VMs. Graceful remote stop remains in awaited `shutdown()`.

## TDD Evidence

### RED 1: Missing Athena VM modules

The complete query/results tests were added before production code:

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_results_vm.py -q
```

Result:

```text
ERROR tests/unit/vm/athena/test_query_vm.py
ModuleNotFoundError: No module named 'aws_tui.vm.athena'
ERROR tests/unit/vm/athena/test_results_vm.py
ModuleNotFoundError: No module named 'aws_tui.vm.athena'
2 errors
```

### RED 2: Lifecycle review regressions

Requirements review added stale-submit flags and mismatched-context cleanup tests
before changing production code:

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py -q
```

Result:

```text
FAILED test_context_replacement_drains_stale_submit_and_clears_submitting
FAILED test_mismatched_execution_identity_fails_closed_before_polling
FAILED test_mismatched_polled_context_never_replaces_active_state
FAILED test_shutdown_stops_query_returned_after_cancelled_submit
4 failed, 17 passed
```

The implementation now clears submission state and asks the authority-enforcing
domain client to stop every out-of-context app-started execution.

### RED 3: Stale cleanup error publication

```text
uv run pytest \
  tests/unit/vm/athena/test_query_vm.py::test_stale_submit_cleanup_failure_cannot_publish_into_new_context \
  -q
```

Result:

```text
FAILED test_stale_submit_cleanup_failure_cannot_publish_into_new_context
AssertionError: visible_errors contained 'Athena query request failed'
1 failed
```

Stale cleanup now remains silent while explicit cancel/shutdown failures retain
normal pane-local feedback.

### RED 4: Result completion after shutdown

```text
uv run pytest \
  tests/unit/vm/athena/test_query_vm.py::test_shutdown_invalidates_result_page_that_ignores_cancellation \
  -q
```

Result:

```text
FAILED test_shutdown_invalidates_result_page_that_ignores_cancellation
AssertionError: rows contained ('STALE_RESULT',)
1 failed
```

Query lifecycle transitions now invalidate the results pager generation before
draining the query task.

### GREEN: Complete Task 3 contract

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_results_vm.py -q
30 passed in 0.36s
```

## Verification

Focused Athena domain and policy:

```text
uv run pytest tests/unit/domain/test_athena.py \
  tests/unit/domain/test_query.py \
  tests/unit/domain/test_sql_policy.py -q
257 passed in 0.76s
```

Full VM regression after final lifecycle fixes:

```text
uv run pytest tests/unit/vm -q
529 passed in 29.61s
```

Privacy and crash-path regression:

```text
uv run pytest tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/test_notifications.py -q
38 passed in 0.36s
```

The non-overlapping domain, VM, and privacy runs total **824 passing tests**.

Static, formatting, and layer checks:

```text
uv run mypy src/aws_tui/domain/query.py src/aws_tui/domain/athena.py \
  src/aws_tui/vm/athena
Success: no issues found in 6 source files

uv run ruff check src/aws_tui/vm/athena tests/unit/vm/athena
All checks passed!

uv run ruff format --check src/aws_tui/vm/athena tests/unit/vm/athena
6 files already formatted

scripts/check-layers.sh
layer rules clean

git diff --cached --check
```

Every listed command exited 0. The unrelated shell startup warning for the
missing `/tmp/vmx-cargo-182/env` appeared on commands and is omitted above.

## Changed Files

- `src/aws_tui/vm/athena/__init__.py`
- `src/aws_tui/vm/athena/_errors.py`
- `src/aws_tui/vm/athena/query_vm.py`
- `src/aws_tui/vm/athena/results_vm.py`
- `tests/unit/vm/athena/test_query_vm.py`
- `tests/unit/vm/athena/test_results_vm.py`
- `.superpowers/sdd/task-3-report.md`

## Concerns

- If a real `StartQueryExecution` request reaches Athena but local cancellation
  prevents the client from ever returning its execution ID, the VM has no ID to
  stop. The context-scoped client request token prevents accidental duplicate
  execution, and tests cover clients that return after cancellation, but this
  transport-level ambiguity cannot be resolved at the VM boundary alone.
- Result pages intentionally accumulate after the user requests them because VMx
  token paging is forward-only. The VM never prefetches or materializes the full
  Athena result set.
- Structured Athena failure messages, state reasons, and result locations remain
  available as explicit UI properties. They are excluded from reprs and are never
  copied into VM exception/error text or logs.
